"""Validate editor patches before persisting (selectors, intent, destructive pairing)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from conxa_compile.compiler.action_semantics import action_name, commit_intent_hit, is_editable_field_click
from conxa_compile.compiler.destructive_semantics import destructive_compiler_step
from conxa_compile.compiler.intent_access import get_effective_intent_from_skill_step
from conxa_compile.compiler.patch import deep_merge
from conxa_compile.compiler.selector_filters import selector_passes_filters
from conxa_compile.compiler.wait_for_shape import destructive_wait_for_is_non_none
from conxa_compile.editor.action_registry import action_spec, is_supported_action
from conxa_compile.editor.placeholder_grammar import (
    FULL_PLACEHOLDER_RE,
    LOOSE_BRACE_RE,
    is_valid_placeholder_id,
)
from conxa_compile.editor.step_view import skill_step_for_destructive_check
from conxa_compile.policy.intent_ontology import sanitize_intent_token

# Per-action-kind patch key allow-lists — the single source of truth both `validate_editor_patch`
# below and `editor/capability_manifest.py` read, so the copilot's capability manifest can never
# drift from what the gate actually enforces (they are the same object, not two lists kept in
# sync by hand).
NAVIGATE_ALLOWED_KEYS = frozenset({"intent", "semantic_description", "action", "url", "validation", "recovery", "frame"})
SCROLL_ALLOWED_KEYS = frozenset({"intent", "semantic_description", "action", "frame"})
WAIT_SCREENSHOT_ALLOWED_KEYS = frozenset({"intent", "semantic_description", "action", "validation", "recovery", "value", "frame"})
CHECK_ASSERT_ALLOWED_KEYS = frozenset({
    "intent", "semantic_description", "action", "check_kind", "check_pattern", "check_threshold",
    "check_selector", "check_text", "signals", "recovery", "frame",
})
AI_REVIEW_ALLOWED_KEYS = frozenset({
    "intent", "semantic_description", "action", "validation", "value", "frame",
    "ai_review_prompt", "ai_review_output_schema", "ai_review_on_failure",
    "ai_review_default_value", "ai_review_reference_screenshot_ref",
})
HANDOVER_ALLOWED_KEYS = frozenset({
    "intent", "semantic_description", "action", "validation", "value", "frame",
    "handover_on_failure", "handover_resume_when", "handover_resume_when_timeout_ms",
})
BRANCH_ALLOWED_KEYS = frozenset({"intent", "semantic_description", "action", "target", "frame", "branch", "validation", "recovery"})
FOR_EACH_ALLOWED_KEYS = frozenset({"intent", "semantic_description", "action", "frame", "for_each"})
# An outcome fork's states/options/policy change only through the set_fork_policy mutation (the
# sole writer of policy.author_approved). A generic patch may rename the step and nothing else.
FORK_ALLOWED_KEYS = frozenset({"intent", "semantic_description"})
DEFAULT_ALLOWED_KEYS = frozenset({
    "intent", "semantic_description", "action", "target", "frame", "validation", "recovery", "value", "input_binding",
})


def _validate_value_placeholders(value: str) -> None:
    """Reject a step-value edit containing a malformed {{...}} attempt (hyphens, digits-first,
    empty, etc.) rather than silently persisting text `interpolate()` will never substitute
    (see CONXA replace-variable audit finding M5/C3)."""
    for match in LOOSE_BRACE_RE.finditer(value):
        if not FULL_PLACEHOLDER_RE.match(match.group(0)):
            raise ValueError(f"invalid_placeholder_syntax:{match.group(0)}")


def _merge_step_shell(step: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(step)
    for key in (
        "target",
        "signals",
        "frame",
        "validation",
        "recovery",
        "confidence_protocol",
        "decision_policy",
        # PROD-3: without this, a patch confirming entity_binding.confirmed would validate
        # against the unpatched (pre-confirmation) step and always fail the new
        # irreversible_step_requires_confirmed_entity_binding invariant below.
        "entity_binding",
    ):
        if key in patch and isinstance(patch[key], dict):
            base = out.get(key) or {}
            out[key] = deep_merge(dict(base), dict(patch[key]))
    if "action" in patch and isinstance(patch["action"], dict):
        current = out.get("action")
        base = dict(current) if isinstance(current, dict) else {"action": str(current or "")}
        out["action"] = deep_merge(base, dict(patch["action"]))
    if "intent" in patch and isinstance(patch["intent"], str):
        out["intent"] = str(patch["intent"]).strip()
        signals = dict(out.get("signals") or {})
        sem = dict(signals.get("semantic") or {})
        resolved = out["intent"]
        if resolved:
            sem["final_intent"] = resolved
            sem["llm_intent"] = resolved
        signals["semantic"] = sem
        out["signals"] = signals
    if "semantic_description" in patch and isinstance(patch["semantic_description"], str):
        # Human-readable per-step description (the workflow-intent graph's prose).
        # Deliberately NOT funneled into intent/final_intent — the machine token
        # drives deterministic logic and stays validated separately.
        out["semantic_description"] = str(patch["semantic_description"]).strip()
    if "value" in patch:
        out["value"] = patch["value"]
        # Keep input_binding in sync with the edited value instead of leaving it stale: a
        # human edit that turns {{old_name}} into {{new_name}} (or into plain literal text)
        # must not leave input_binding still pointing at "old_name" (audit finding L0).
        new_value = patch["value"]
        if isinstance(new_value, str):
            full_match = FULL_PLACEHOLDER_RE.match(new_value.strip())
            out["input_binding"] = full_match.group(1) if full_match else None
        else:
            out["input_binding"] = None
    if "url" in patch and isinstance(patch["url"], str):
        out["url"] = str(patch["url"]).strip()
    return out


def _validate_frame_patch(raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError("frame_must_be_object")
    chain = raw.get("chain")
    if chain in (None, []):
        return
    if not isinstance(chain, list):
        raise ValueError("frame_chain_must_be_array")
    for index, item in enumerate(chain):
        if not isinstance(item, dict):
            raise ValueError(f"frame_chain_{index}_must_be_object")
        selector = str(item.get("selector") or "").strip()
        if not selector:
            raise ValueError(f"frame_chain_{index}_selector_required")
        lowered = selector.lower()
        if lowered.startswith(("/", "./", "//")) or "xpath" in lowered:
            raise ValueError(f"frame_chain_{index}_selector_must_not_be_xpath")
        fallbacks = item.get("fallback_selectors") or []
        if not isinstance(fallbacks, list):
            raise ValueError(f"frame_chain_{index}_fallback_selectors_must_be_array")
        for fb in fallbacks:
            fb_text = str(fb or "").strip()
            if fb_text.lower().startswith(("/", "./", "//")) or "xpath" in fb_text.lower():
                raise ValueError(f"frame_chain_{index}_fallback_selector_must_not_be_xpath")


def _coerce_scroll_delta(raw: Any) -> int:
    if raw is None:
        raise ValueError("scroll_amount_required")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("scroll_amount_must_be_integer") from exc


def _coerce_branch_timeout_ms(raw: Any) -> None:
    if raw is None:
        return
    try:
        timeout = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("branch_timeout_ms_must_be_integer") from exc
    if timeout < 0 or timeout > 60000:
        raise ValueError("branch_timeout_ms_out_of_range")


def _validate_branch_patch(kind: str, raw: Any) -> None:
    """Validate a patch to step["branch"] (if_present/try_dismiss/wait_for_one_of — EXEC-1).

    Nested `if_present` body steps are NOT edited through this key — they're structural
    (insert/delete/reorder RPCs mirroring the top-level ones) plus per-nested-step patches
    addressed by `path` (see cmd_patch_step), so any `steps` content here is rejected to avoid
    a blind deep-merge clobbering edits made through that path-addressed flow. `wait_for_one_of`
    option bodies are read-only this pass (see StepEditorDTO.branch_summary's `options` — full
    per-option step editing is an explicit follow-up), so a `steps` key on any option is rejected
    too, keeping that boundary honest rather than silently accepting content nothing renders.
    """
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError("branch_must_be_object")
    _coerce_branch_timeout_ms(raw.get("timeout_ms"))

    if kind == "if_present":
        if "steps" in raw:
            raise ValueError("branch_if_present_steps_not_patchable_here")
        return

    if kind == "try_dismiss":
        candidates = raw.get("candidates")
        if candidates is not None:
            if not isinstance(candidates, list):
                raise ValueError("branch_candidates_must_be_array")
            for i, c in enumerate(candidates):
                text = str(c or "").strip()
                if text and not selector_passes_filters(text):
                    raise ValueError(f"branch_candidate_{i}_failed_quality_gates")
        fallback_escape = raw.get("fallback_escape")
        if fallback_escape is not None and not isinstance(fallback_escape, bool):
            raise ValueError("branch_fallback_escape_must_be_boolean")
        return

    if kind == "wait_for_one_of":
        options = raw.get("options")
        if options is not None:
            if not isinstance(options, list):
                raise ValueError("branch_options_must_be_array")
            for i, opt in enumerate(options):
                if not isinstance(opt, dict):
                    raise ValueError(f"branch_option_{i}_must_be_object")
                if "steps" in opt:
                    raise ValueError(f"branch_option_{i}_steps_not_patchable_here")
                selector = str(opt.get("selector") or "").strip()
                if not selector:
                    raise ValueError(f"branch_option_{i}_selector_required")
                if not selector_passes_filters(selector):
                    raise ValueError(f"branch_option_{i}_selector_failed_quality_gates")
        required = raw.get("required")
        if required is not None and not isinstance(required, bool):
            raise ValueError("branch_required_must_be_boolean")


def _validate_for_each_patch(raw: Any) -> None:
    """Validate a patch to step["for_each"] (EXEC-38). Nested body steps are NOT edited through
    this key — same reasoning as `_validate_branch_patch`'s if_present case — so a `steps`
    key here is rejected to avoid a blind deep-merge clobbering edits made through the
    path-addressed flow (`for_each.steps[j]`)."""
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError("for_each_must_be_object")
    if "steps" in raw:
        raise ValueError("for_each_steps_not_patchable_here")
    rows = raw.get("rows")
    if rows is not None:
        if not isinstance(rows, dict):
            raise ValueError("for_each_rows_must_be_object")
        selector = str(rows.get("container_selector") or "").strip()
        if selector and not selector_passes_filters(selector):
            raise ValueError("for_each_rows_container_selector_failed_quality_gates")
    items = raw.get("items")
    if items is not None and not is_valid_placeholder_id(str(items).strip()):
        raise ValueError("for_each_items_must_be_valid_input_id")
    as_name = raw.get("as")
    if as_name is not None and not str(as_name).strip():
        raise ValueError("for_each_as_must_be_non_empty_string")
    if "max_iterations" in raw:
        try:
            max_iterations = int(raw.get("max_iterations"))
        except (TypeError, ValueError) as exc:
            raise ValueError("for_each_max_iterations_must_be_integer") from exc
        # A generous sanity ceiling, not the enforced cap — the runtime's own
        # CONXA_MAX_LOOP_ITERATIONS (default 100) is what actually bounds a run; this just
        # rejects an obviously-wrong value (0, negative, or absurdly large) at edit time.
        if max_iterations <= 0 or max_iterations > 1000:
            raise ValueError("for_each_max_iterations_out_of_range")
    on_row_error = raw.get("on_row_error")
    if on_row_error is not None and on_row_error not in ("stop", "continue"):
        raise ValueError("for_each_on_row_error_invalid")


def _validate_for_each_source(step: dict[str, Any], raw_patch: Any) -> None:
    """Exactly one row source — `rows.container_selector` (a DOM scan) or `items` (a named
    runtime input, comma-split at execution) — same rule `_saved_for_each_step` enforces at
    package time, checked here against the EFFECTIVE for_each (existing step merged with this
    patch) rather than the raw patch alone: a patch touching only `items` must still be
    rejected if the step's existing `rows.container_selector` would be left in place, and vice
    versa. Shallow merge is enough — `items`/`rows` are the only keys this check reads, and a
    patch that sets `rows` always means "replace the row spec", never "merge into it"."""
    if not isinstance(raw_patch, dict):
        return
    existing = step.get("for_each") if isinstance(step.get("for_each"), dict) else {}
    effective = {**existing, **raw_patch}
    eff_rows = effective.get("rows") if isinstance(effective.get("rows"), dict) else {}
    has_selector = bool(str(eff_rows.get("container_selector") or "").strip())
    has_items = bool(str(effective.get("items") or "").strip())
    if has_selector == has_items:
        raise ValueError("for_each_requires_exactly_one_row_source")


# `enum: [true]` is what makes "no" end the run: an ai_review answer that doesn't satisfy the
# schema is re-asked a bounded number of times and then `on_failure: abort` applies, whereas a
# valid `{confirmed: false}` would simply be bound and the run would carry on.
CONFIRM_ONLY_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["confirmed"],
    "properties": {"confirmed": {"type": "boolean", "enum": [True]}},
}


def is_confirm_only_review(step: dict[str, Any]) -> bool:
    """A review step whose only acceptable answer is "yes" and whose "no" ends the run.

    EXEC-13 refuses a destructive step directly after an ``ai_review`` because a model's free
    answer must never supply *what* a destructive step acts on. A confirm-only review (schema
    exactly ``{confirmed: boolean (only true allowed)}``, ``on_failure == "abort"``) can only gate *whether* the next
    step runs, never what it acts on, so Clarify's "a person confirms before sending" is allowed
    to sit directly before the step it guards (BUILD-39). TRD §11.3."""
    schema = step.get("ai_review_output_schema")
    if not isinstance(schema, dict):
        return False
    props = schema.get("properties")
    if not isinstance(props, dict) or set(props) != {"confirmed"}:
        return False
    confirmed = props.get("confirmed")
    if (
        not isinstance(confirmed, dict)
        or confirmed.get("type") != "boolean"
        or confirmed.get("enum") != [True]
        or set(confirmed) - {"type", "enum", "description"}
    ):
        return False
    if set(schema.get("required") or []) != {"confirmed"}:
        return False
    return str(step.get("ai_review_on_failure") or "abort").strip().lower() == "abort"


def validate_editor_patch(
    step: dict[str, Any],
    patch: dict[str, Any],
    policy: dict[str, Any],
    *,
    in_branch_body: bool = False,
    previous_step: dict[str, Any] | None = None,
) -> None:
    """Raise ValueError with a human-readable message if the patch is not allowed.

    `in_branch_body=True` marks a patch to a nested step inside another step's branch body
    (if_present's `branch.steps[j]`, addressed via cmd_patch_step's `path` parameter). Branch
    bodies run best-effort and never enter the Tier 1-4 recovery cascade (CLAUDE.md Key
    Invariants), so a nested step's `recovery`/`validation` blocks are meaningless there and any
    attempt to patch them is rejected rather than silently accepted.

    A for_each loop body step (`for_each.steps[j]`) is deliberately NOT covered by this flag —
    the opposite case from branch: EXEC-38 runs a loop body through `executeOneStep`, the exact
    same tab-resolution/GATE/VERIFY/recovery-cascade path every top-level step gets (see
    runtime/app/run.js), so `recovery`/`validation` are meaningful there and must stay patchable —
    the re-target wizard's Validation phase is exactly this. Callers patching a for_each nested
    step pass `in_branch_body=False` (the default), same as an ordinary top-level step.

    `previous_step`, when given, is the step immediately before this one in the same step list
    (top-level only — EXEC-13's destructive-after-ai_review lint below only makes sense there;
    branch bodies never enter recovery and don't carry ai_review either). Omitted by callers that
    can't cheaply resolve it (e.g. a first step has none); the lint below simply doesn't fire.
    """
    if in_branch_body and ("recovery" in patch or "validation" in patch):
        raise ValueError("branch_body_step_cannot_patch_recovery_or_validation")

    if "value" in patch and isinstance(patch.get("value"), str):
        _validate_value_placeholders(patch["value"])

    if "frame" in patch:
        _validate_frame_patch(patch.get("frame"))

    merged = _merge_step_shell(step, patch)

    if "intent" in patch:
        raw = str(patch.get("intent") or "").strip()
        if not raw:
            raise ValueError("intent_empty")
        if not sanitize_intent_token(raw, ""):
            raise ValueError("invalid_intent_slug")

    if "semantic_description" in patch:
        raw = str(patch.get("semantic_description") or "").strip()
        if not raw:
            raise ValueError("semantic_description_empty")

    act = action_name(merged).lower()
    spec = action_spec(act)
    if not is_supported_action(act):
        raise ValueError("unsupported_action_kind")
    if spec.marker:
        invalid_keys = sorted(set(patch))
        if invalid_keys:
            raise ValueError("recording_marker_steps_are_read_only")
        return
    if act == "navigate":
        invalid_keys = sorted(set(patch) - NAVIGATE_ALLOWED_KEYS)
        if invalid_keys:
            raise ValueError("navigate_step_allows_only_url_intent_validation_recovery")
        action_patch = patch.get("action")
        url = ""
        if isinstance(action_patch, dict):
            url = str(action_patch.get("url") or "").strip()
        url = url or str(patch.get("url") or merged.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("navigate_url_must_be_http_url")
        return
    if act == "scroll":
        invalid_keys = sorted(set(patch) - SCROLL_ALLOWED_KEYS)
        if invalid_keys:
            raise ValueError("scroll_step_allows_only_intent_and_action")
        action_patch = patch.get("action")
        if not isinstance(action_patch, dict):
            raise ValueError("scroll_action_patch_required")
        if str(action_patch.get("action") or "scroll").strip().lower() != "scroll":
            raise ValueError("scroll_action_kind_invalid")
        selector = str(action_patch.get("selector") or "").strip()
        if selector:
            if not selector_passes_filters(selector):
                raise ValueError("scroll_selector_failed_quality_gates")
        else:
            delta = _coerce_scroll_delta(action_patch.get("delta"))
            if abs(delta) > 20000:
                raise ValueError("scroll_amount_out_of_range")
        return
    if act in {"wait", "screenshot"}:
        invalid_keys = sorted(set(patch) - WAIT_SCREENSHOT_ALLOWED_KEYS)
        if invalid_keys:
            raise ValueError(f"{act}_step_allows_only_action_intent_validation_recovery")
    if act in {"check", "assert"}:
        invalid_keys = sorted(set(patch) - CHECK_ASSERT_ALLOWED_KEYS)
        if invalid_keys:
            raise ValueError("check_step_allows_only_check_fields")
    if act == "ai_review":
        # EXEC-13: an author-placed reasoning checkpoint, not a selector/identity step. Config
        # fields are top-level, sibling to `action` — the same shape check/assert's check_kind/
        # check_pattern/etc. use above, not the scroll/drag_drop pattern of nesting inside
        # `action` (the saved-skill export normalizes `action` down to a plain string before
        # _saved_step_to_execution_step ever runs, so kind-specific config can't live there).
        # No `recovery` block is meaningful here (nothing to re-locate) — excluded from the
        # allowlist entirely, checked ahead of the generic allowlist so this specific mistake
        # gets its own message rather than the generic one.
        if "recovery" in patch:
            raise ValueError("ai_review_step_cannot_patch_recovery")
        invalid_keys = sorted(set(patch) - AI_REVIEW_ALLOWED_KEYS)
        if invalid_keys:
            raise ValueError("ai_review_step_allows_only_ai_review_fields")
        if "ai_review_prompt" in patch:
            prompt = str(patch.get("ai_review_prompt") or "").strip()
            if not prompt:
                raise ValueError("ai_review_prompt_empty")
        if "ai_review_output_schema" in patch:
            output_schema = patch.get("ai_review_output_schema")
            if output_schema is not None and not isinstance(output_schema, dict):
                raise ValueError("ai_review_output_schema_must_be_object")
        if "ai_review_reference_screenshot_ref" in patch:
            reference_ref = patch.get("ai_review_reference_screenshot_ref")
            if reference_ref is not None and not str(reference_ref).strip():
                raise ValueError("ai_review_reference_screenshot_ref_empty")
        if "ai_review_on_failure" in patch:
            on_failure = str(patch.get("ai_review_on_failure") or "").strip().lower()
            if on_failure not in {"abort", "use_default", "continue"}:
                raise ValueError("ai_review_on_failure_invalid")
            if on_failure == "use_default" and "ai_review_default_value" not in patch \
                    and "ai_review_default_value" not in step:
                raise ValueError("ai_review_use_default_requires_default_value")
    if act == "handover":
        # EXEC-21 (hand-over shape): the human sibling of ai_review above — same "no recovery
        # block, config lives in top-level fields" shape, but the message shown to the person
        # rides the generic `value` field (VALUE_LABELS covers this in action_registry.py)
        # rather than a dedicated `handover_message` field, since that's the one thing every
        # action-kind editor already renders a labeled text box for.
        if "recovery" in patch:
            raise ValueError("handover_step_cannot_patch_recovery")
        invalid_keys = sorted(set(patch) - HANDOVER_ALLOWED_KEYS)
        if invalid_keys:
            raise ValueError("handover_step_allows_only_handover_fields")
        message = str(merged.get("value") or "").strip()
        if not message:
            raise ValueError("handover_message_empty")
        if "handover_on_failure" in patch:
            on_failure = str(patch.get("handover_on_failure") or "").strip().lower()
            # No use_default: a hand-over produces no answer value, so there's nothing to fall
            # back to — only abort (default) or continue past it are meaningful.
            if on_failure not in {"abort", "continue"}:
                raise ValueError("handover_on_failure_invalid")
        if "handover_resume_when" in patch:
            resume_when = patch.get("handover_resume_when")
            if resume_when is not None and not isinstance(resume_when, dict):
                raise ValueError("handover_resume_when_must_be_object")
        if "handover_resume_when_timeout_ms" in patch:
            timeout_ms = patch.get("handover_resume_when_timeout_ms")
            if timeout_ms is not None and (not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0):
                raise ValueError("handover_resume_when_timeout_ms_invalid")
    if act in {"if_present", "try_dismiss", "wait_for_one_of"}:
        invalid_keys = sorted(set(patch) - BRANCH_ALLOWED_KEYS)
        if invalid_keys:
            raise ValueError("branch_step_allows_only_target_branch_intent_validation_recovery_frame")
        if "branch" in patch:
            _validate_branch_patch(act, patch.get("branch"))
    if act == "outcome_fork":
        invalid_keys = sorted(set(patch) - FORK_ALLOWED_KEYS)
        if invalid_keys:
            raise ValueError("outcome_fork_step_allows_only_intent_and_description")
    if act == "for_each":
        invalid_keys = sorted(set(patch) - FOR_EACH_ALLOWED_KEYS)
        if invalid_keys:
            raise ValueError("for_each_step_allows_only_intent_for_each_frame")
        if "for_each" in patch:
            _validate_for_each_patch(patch.get("for_each"))
            _validate_for_each_source(step, patch.get("for_each"))
    if act != "scroll":
        eff = get_effective_intent_from_skill_step(merged) or str(merged.get("intent") or "").strip()
        if not eff.strip():
            raise ValueError("intent_required_for_non_scroll_step")

    tgt = merged.get("target") if isinstance(merged.get("target"), dict) else {}
    primary = str(tgt.get("primary_selector") or "").strip()
    if primary and not selector_passes_filters(primary):
        raise ValueError("primary_selector_failed_quality_gates")
    for fb in tgt.get("fallback_selectors") or []:
        s = str(fb).strip()
        if s and not selector_passes_filters(s):
            raise ValueError("fallback_selector_failed_quality_gates")

    view = skill_step_for_destructive_check(merged)
    if destructive_compiler_step(view, policy):
        wf = (merged.get("validation") or {}).get("wait_for") or {}
        wf_d = dict(wf) if isinstance(wf, dict) else {}
        if not destructive_wait_for_is_non_none(wf_d):
            raise ValueError("destructive_step_requires_non_none_wait_for")
        anchors = (merged.get("signals") or {}).get("anchors") or []
        if not anchors:
            raise ValueError("destructive_step_requires_signals_anchors")
        # EXEC-13 / PROD-3 guardrail: a destructive step reading its target straight off an
        # ai_review answer has no entity binding to prove it's acting on the right record.
        # PROD-3's entity binding is now real (see below), but a review answer still isn't a
        # declared run input or a recorded literal a binding can be built from — route through a
        # conditional (EXEC-1) instead: let the review gate WHETHER the destructive step runs,
        # not WHAT it acts on.
        if previous_step is not None and action_name(previous_step).lower() == "ai_review"                 and not is_confirm_only_review(previous_step):
            raise ValueError("destructive_step_cannot_directly_follow_ai_review")

        # PROD-3: when the compiler detected this step's target sits inside a repeating
        # container (a table row, a list item — see conxa_compile.compiler.entity_binding), a
        # machine guess is never enough authorization to delete/pay/submit against it. The
        # vendor must explicitly confirm the binding in the editor before this step can be
        # saved. A step with no detected container has nothing to confirm — there is no wrong
        # row to guard against, so it is unaffected.
        eb_raw = merged.get("entity_binding")
        eb = eb_raw if isinstance(eb_raw, dict) else {}
        if eb.get("container_selector") and not eb.get("confirmed"):
            raise ValueError("irreversible_step_requires_confirmed_entity_binding")

    # Any consequential action must retain at least one enforced (required=True) post-condition
    # assertion after the edit — mirrors the destructive wait_for invariant above. Prevents a
    # human edit (e.g. clearing validation.assertions) from silently dropping the check that
    # confirms the action actually produced its intended result.
    #
    # Only enforced when there is something to protect: either the patch explicitly touches
    # `validation` (the Human Editor's Validation phase), or the step already carried a required
    # assertion before this edit. This keeps packs compiled before enforced post-conditions
    # existed editable via unrelated patches (intent rename, retarget) without forcing a
    # recompile — see the plan's backward-compatibility decision.
    consequential_input = act in {"fill", "type", "select", "select_option"} and bool(merged.get("value"))
    consequential_click = (
        act == "click"
        and not is_editable_field_click(view)
        and (commit_intent_hit(view, policy) or destructive_compiler_step(view, policy))
    )
    if consequential_input or consequential_click:
        def _has_required(step_dict: dict[str, Any]) -> bool:
            assertions = (step_dict.get("validation") or {}).get("assertions") or []
            return any(isinstance(a, dict) and a.get("required", True) is not False for a in assertions)

        if ("validation" in patch or _has_required(step)) and not _has_required(merged):
            raise ValueError("consequential_step_requires_required_assertion")
