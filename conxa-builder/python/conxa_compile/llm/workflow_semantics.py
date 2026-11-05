"""Validation for the second-opinion section of the merged workflow-review
call (BUILD-25): decides the four things no per-step rule can decide
correctly, because the information they need is workflow-global.

`_validate_findings` here used to gate a standalone workflow_semantics LLM
call. It's now reused by workflow_review.py, which merges that call with the
workflow-intent call into one whole-workflow multimodal call — see
workflow_review.py's module docstring.

This module only DECIDES. compiler/second_opinion.py writes the decisions onto
the compiled steps, so a reviewer opening Human Review sees finished work
rather than a list of chips to approve — Human Review is the gate, and a wrong
call is edited there like any other compiler output.

That means the output of this call is applied, not annotated, and the six
validation gates in _validate_findings below are the trust boundary between an
LLM and a shipped artifact. They matter more now, not less: keep every one of
them. Precision over recall — a pass that always finds something is worse than
one that usually finds nothing, because "no findings" is a first-class and
expected outcome.

CLAUDE.md's "LLM does not write selector strings on the primary compile path"
invariant holds: nothing here or in the applier touches a selector, an identity
signal, or an element address. It writes meaning only — binding names, value
placeholders, phase, optionality.

workflow_review.py mirrors the old cache-lookup -> LLM call -> validate ->
cache-on-success-only shape (never on empty, so a drained provider pool
retries next compile). Never raises. A failed or disabled call falls back to
the rules-only compile, which is a different (rules-only) package, not the
same one — see TODO.md BUILD-25 for why that trade was taken.

Six kinds (BUILD-25's six problem families):
  - rename_binding / parameterize_literal — naming is workflow-global; two
    same-typed fields can only be told apart with the whole workflow in view.
  - suggest_optional — the recorder already flags a step optional_hint
    (stochastic across recordings); this only judges hints that already exist,
    it never invents one.
  - label_phase — labels a run of steps login/navigate/act/verify/cleanup.
    Read by runtime/app/failure_response.js's Tier B recovery prompt
    (BUILD-25 stage e); deliberately distinct from the riskier group_steps
    kind, which is out of scope here.
  - suggest_assertion (stage d) — a step's real post-condition is only
    visible from a later step. Advisory only (required=False, always) and
    restricted to text/URL/state assertion types — never a selector type,
    since that target is a raw Playwright selector and this pass may never
    write one (CLAUDE.md's selector-string invariant, not just the
    "never touches validation" one it also narrows).
  - flag_noise (stage d) — a step the recorder itself observed to have no
    effect (post_condition.classified_effect == "none"), never the model's
    own opinion alone. Applied as an archive, not a silent delete: see
    compiler/second_opinion.py::archive_flagged_steps. One exception to the
    "recorder observed no effect" gate: a `navigate` step may be flagged
    duplicate_action when the step immediately before it is also a navigate
    to the identical url on the identical tab_id — two navigates land on the
    exact same URL, so there is no post_condition_effect signal to read, but
    the immediate-predecessor match is itself the rule-checked evidence.

Findings are keyed on step_key (compiler/step_key.py), never step_index — an
inserted step renumbers everything after it (BUILD-22/BUILD-23).
"""

from __future__ import annotations

import json
from typing import Any

from conxa_compile.editor.placeholder_grammar import is_valid_placeholder_id

_VALID_KINDS = frozenset({
    "rename_binding", "parameterize_literal", "suggest_optional", "label_phase",
    "suggest_assertion", "flag_noise",
})
_VALID_PHASES = frozenset({"login", "navigate", "act", "verify", "cleanup"})
_NAME_KINDS = frozenset({"rename_binding", "parameterize_literal"})
# Text/URL/state only — never a selector-bearing Assertion type (target would
# be a raw Playwright selector). See module docstring.
_VALID_ASSERTION_TYPES = frozenset({
    "text_present", "text_absent", "url_changed", "url_pattern", "state_changed",
})
_MAX_ASSERTION_TARGET_LEN = 200
_VALID_NOISE_CATEGORIES = frozenset({"duplicate_action", "no_op_action", "orphaned_hover"})
_NOISE_SAFE_ACTIONS = frozenset({"click", "hover", "scroll", "focus"})
# Precision over recall (see module docstring / TODO.md BUILD-25): a pass that
# always finds something trains reviewers to accept reflexively, which
# destroys the label quality this whole item exists to produce.
_MAX_FINDINGS_PER_STEP_RATIO = 0.5


def _valid_bindings(steps_context: list[dict[str, Any]]) -> set[str]:
    return {
        str(s.get("input_binding") or "").strip()
        for s in steps_context
        if s.get("input_binding")
    }


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _grounded_in_workflow(text: str, steps_context: list[dict[str, Any]]) -> bool:
    """suggest_assertion may only point at text that genuinely exists
    somewhere in this recording — never an invented claim. Cheap
    case-insensitive substring check against every step's own target_text/
    intent/url; not semantic verification, but the finding it gates is always
    advisory (required=False), so a false negative here costs nothing beyond
    a dropped suggestion.
    """
    needle = text.strip().lower()
    if not needle:
        return False
    for s in steps_context:
        haystack = " ".join(str(s.get(f) or "") for f in ("target_text", "intent", "url")).lower()
        if needle in haystack:
            return True
    return False


def _validate_findings(
    raw_findings: Any,
    *,
    step_keys_in_workflow: set[str],
    steps_context: list[dict[str, Any]],
    settled_literals: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Drop everything that isn't trustworthy, silently — a rejected finding is
    a normal outcome, not an error worth surfacing. Eight gates, and every one
    of them stands between an LLM and a compiled artifact a customer will run:
    an unknown step_key, an unknown kind, a name that isn't a valid placeholder
    id, a name that collides with a binding the compiler already assigned, an
    "optional" verdict the recorder never observed, an invalid phase label, an
    assertion proposal that isn't a grounded text/URL/state claim, and a noise
    flag on a step the recorder didn't itself observe as having no effect.
    The trailing cap keeps a talkative model from rewriting half the workflow."""
    if not isinstance(raw_findings, list):
        return []

    optional_hint_keys = {
        str(s.get("key") or "") for s in steps_context if s.get("has_optional_hint")
    }
    existing_bindings = _valid_bindings(steps_context)
    steps_by_key = {str(s.get("key") or ""): s for s in steps_context}
    # steps_context is in step order (build.py::_review_inputs) — this is the flag_noise
    # navigate path's only source of "what came immediately before", never step_index.
    prev_by_key = {
        str(s.get("key") or ""): steps_context[i - 1] if i > 0 else None
        for i, s in enumerate(steps_context)
    }
    seen_new_names: set[str] = set()
    out: list[dict[str, Any]] = []

    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        step_key = str(item.get("step_key") or "").strip()
        kind = str(item.get("kind") or "").strip()
        proposed = str(item.get("proposed") or "").strip()
        if step_key not in step_keys_in_workflow:
            continue
        if kind not in _VALID_KINDS:
            continue
        if kind == "parameterize_literal" and str(item.get("current") or "").strip().lower() in settled_literals:
            continue        # a value somebody already decided is not reopened here (lower-cased by the caller)
        if kind in _NAME_KINDS:
            if not is_valid_placeholder_id(proposed):
                continue
            if proposed in existing_bindings or proposed in seen_new_names:
                continue
            seen_new_names.add(proposed)
        elif kind == "suggest_optional":
            if step_key not in optional_hint_keys:
                continue
        elif kind == "label_phase":
            if proposed not in _VALID_PHASES:
                continue
        elif kind == "suggest_assertion":
            parsed = _parse_json_object(proposed)
            if parsed is None:
                continue
            a_type = str(parsed.get("type") or "").strip()
            a_target = str(parsed.get("target") or "").strip()
            if a_type not in _VALID_ASSERTION_TYPES:
                continue
            if a_type in ("state_changed", "url_changed"):  # both compare to the pre-action page
                if a_target:
                    continue
            else:
                if not a_target or len(a_target) > _MAX_ASSERTION_TARGET_LEN:
                    continue
                if not _grounded_in_workflow(a_target, steps_context):
                    continue
            proposed = json.dumps({"type": a_type, "target": a_target}, sort_keys=True)
        elif kind == "flag_noise":
            if proposed not in _VALID_NOISE_CATEGORIES:
                continue
            step_ctx = steps_by_key.get(step_key)
            if not step_ctx:
                continue
            if step_ctx.get("has_required_assertion") or step_ctx.get("input_binding"):
                continue
            if step_ctx.get("action") == "navigate":
                # No post_condition_effect signal exists for a navigate (the recorder's
                # "no effect" evidence is click/hover/scroll/focus-only), so this path is
                # gated on a different, equally rule-checked fact instead: the step
                # immediately before it is a navigate to the identical url on the
                # identical tab. That match — never the model's own say-so — is the
                # evidence. See docstring above.
                if proposed != "duplicate_action":
                    continue
                prev = prev_by_key.get(step_key)
                url = str(step_ctx.get("url") or "").strip()
                if not prev or not url:
                    continue
                if prev.get("action") != "navigate":
                    continue
                if str(prev.get("url") or "").strip() != url:
                    continue
                if str(prev.get("tab_id") or "") != str(step_ctx.get("tab_id") or ""):
                    continue
            else:
                if step_ctx.get("action") not in _NOISE_SAFE_ACTIONS:
                    continue
                if step_ctx.get("post_condition_effect") != "none":
                    continue
                # A click whose only job is to start a download or open a file picker is
                # SUPPOSED to leave post_condition_effect at "none" — the page itself doesn't
                # visibly react. build.py::_review_inputs sets "causes" for exactly these
                # steps; never let a noise flag land on one no matter what the model said.
                if step_ctx.get("causes"):
                    continue
        out.append({
            "step_key": step_key,
            "kind": kind,
            "current": str(item.get("current") or ""),
            "proposed": proposed,
            "why": str(item.get("why") or "")[:280],
        })

    cap = max(1, round(len(steps_context) * _MAX_FINDINGS_PER_STEP_RATIO))
    return out[:cap]
