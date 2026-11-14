"""Validation planning: FINAL_INTENT + action + policy first; state_diff refines commits (no fixed login paths)."""

from __future__ import annotations

import re
from typing import Any

from conxa_compile.compiler.action_semantics import (
    action_name,
    commit_intent_hit,
    is_editable_field_click,
)
from conxa_compile.compiler.destructive_semantics import destructive_compiler_step
from conxa_compile.compiler.decision_layer import (
    intent_outcome_tokens,
    intent_primary_validation_enabled,
    intent_tokens_grounded_in_context,
)
from conxa_compile.compiler.intent_access import get_effective_intent
from conxa_compile.compiler.intent_validation_rules import (
    selector_wait_target_from_step,
    try_destructive_confirmation_wait,
    try_intent_validation_facets,
    try_open_select_wait_rule,
)
from conxa_compile.compiler.selector_filters import is_ephemeral_anchor

Step = dict[str, Any]


_VALID_NO_EVIDENCE_WAITS = frozenset({"url_change", "intent_outcome"})


def _no_evidence_wait(raw: str) -> str:
    """Validate policy.validation.commit_no_evidence_wait. "dom_change" was retired in favor
    of "intent_outcome" — a policy file still specifying it needs updating, not silent
    translation, since the compiler no longer knows what dom_change-based waiting meant."""
    r = str(raw or "intent_outcome").strip().lower()
    if r not in _VALID_NO_EVIDENCE_WAITS:
        raise ValueError(
            f"policy.validation.commit_no_evidence_wait={raw!r} is not a valid value "
            f"({sorted(_VALID_NO_EVIDENCE_WAITS)}). 'dom_change' was retired — use "
            "'intent_outcome'. Update the policy bundle."
        )
    return r


def dom_signal_to_intent_mapped_wait(step: Step, timeout: int) -> dict[str, Any]:
    """Prefer element_appear on a stable selector when available; else intent-level outcome wait."""
    tgt = selector_wait_target_from_step(step)
    if tgt:
        return {"type": "element_appear", "target": tgt, "timeout": timeout}
    return {"type": "intent_outcome", "target": "", "timeout": timeout}


def _state_diff_channel_scores(state_diff: dict[str, Any], val: dict[str, Any]) -> dict[str, float]:
    """Deterministic weighting of observable channels; used to pick wait type without a fixed 'always URL' rule."""
    w = val.get("channel_weights") if isinstance(val.get("channel_weights"), dict) else {}
    w_url = float(w.get("url", 1.0))
    w_dom = float(w.get("dom_fingerprint", 0.85))
    w_el = float(w.get("element_set", 0.35))
    w_txt = float(w.get("text", 0.2))

    url_changed = bool(state_diff.get("url_changed"))
    dom_changed = bool(state_diff.get("dom_changed"))
    new_els = state_diff.get("new_elements") or []
    rem = state_diff.get("removed_elements") or []
    txt = state_diff.get("text_change") or []
    n_new, n_rem, n_txt = len(new_els or []), len(rem or []), len(txt or [])

    el_delta = n_new + n_rem
    dom_signal = bool(dom_changed or el_delta > 0 or n_txt > 0)
    dom_score = 0.0
    if dom_signal:
        dom_score = w_dom + w_el * float(el_delta) + w_txt * float(n_txt)

    return {
        "url_change": w_url if url_changed else 0.0,
        "dom_signal": dom_score,
    }


def _commit_no_diff_intent_wait_override(
    fin_l: str,
    wf_out: dict[str, Any],
    state_diff: dict[str, Any],
    policy: dict[str, Any],
    step: Step,
) -> dict[str, Any] | None:
    """When compile-time diff has no URL/DOM channel scores, nudge commit wait from FINAL_INTENT."""
    if not fin_l:
        return None
    val = policy.get("validation") if isinstance(policy.get("validation"), dict) else {}
    ch = _state_diff_channel_scores(state_diff, val)
    u, d = float(ch.get("url_change", 0.0)), float(ch.get("dom_signal", 0.0))
    if u > 0.0 or d > 0.0:
        return None
    dl = policy.get("decision_layer") if isinstance(policy.get("decision_layer"), dict) else {}
    prefer_url = [str(x).lower() for x in (dl.get("commit_intent_prefer_url_substrings") or [])]
    prefer_dom = [str(x).lower() for x in (dl.get("commit_intent_prefer_dom_substrings") or [])]
    if prefer_url and any(s in fin_l for s in prefer_url if s):
        return {**wf_out, "type": "url_change"}
    if prefer_dom and any(s in fin_l for s in prefer_dom if s):
        t = int(wf_out.get("timeout") or 0)
        return dom_signal_to_intent_mapped_wait(step, t)
    return None


def _pick_commit_wait(
    step: Step,
    state_diff: dict[str, Any],
    policy: dict[str, Any],
    base_timeout: int,
    submit_min: int,
) -> dict[str, Any]:
    val = policy.get("validation") if isinstance(policy.get("validation"), dict) else {}
    scores = _state_diff_channel_scores(state_diff, val)
    t_commit = max(base_timeout, submit_min)
    u, d = scores["url_change"], scores["dom_signal"]
    if u > d and u > 0:
        return {"type": "url_change", "target": "", "timeout": t_commit}
    if d > u and d > 0:
        return dom_signal_to_intent_mapped_wait(step, t_commit)
    if u > 0:
        return {"type": "url_change", "target": "", "timeout": t_commit}
    if d > 0:
        return dom_signal_to_intent_mapped_wait(step, t_commit)
    pref = _no_evidence_wait(str(val.get("commit_no_evidence_wait", "intent_outcome")))
    if pref == "url_change":
        return {"type": "url_change", "target": "", "timeout": t_commit}
    return {"type": "intent_outcome", "target": "", "timeout": t_commit}


def infer_wait_for_shape(
    step: Step,
    state_diff: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Return wait_for dict: type, target, timeout — intent + action + policy first; state_diff refines commits."""
    action = action_name(step).lower()
    timing = step.get("timing") or {}
    timeout = int(
        timing.get("timeout")
        or policy.get("validation", {}).get("default_timeout_ms", 5000)  # type: ignore[union-attr]
    )
    wf = policy.get("workflow") if isinstance(policy.get("workflow"), dict) else {}
    nav_actions = {str(x).lower() for x in (wf.get("navigation_actions") or ["navigate", "go_to", "open"])}
    val = policy.get("validation") if isinstance(policy.get("validation"), dict) else {}
    submit_min = int(val.get("submit_min_timeout_ms", 8000))
    nav_min = int(val.get("navigation_min_timeout_ms", submit_min))

    if not isinstance(state_diff, dict):
        state_diff = {}
    url_changed = bool(state_diff.get("url_changed"))
    dom_changed = bool(state_diff.get("dom_changed"))

    if action in {"type", "focus", "fill"}:
        return {"type": "none", "target": "", "timeout": timeout}

    if action in nav_actions:
        return {"type": "url_change", "target": "", "timeout": max(timeout, nav_min)}

    if action == "click":
        is_commit = commit_intent_hit(step, policy)

        if is_editable_field_click(step) and not is_commit:
            if dom_changed:
                return dom_signal_to_intent_mapped_wait(step, timeout)
            return {"type": "none", "target": "", "timeout": timeout}

        if is_commit:
            facet_wait = try_intent_validation_facets(
                step, policy, is_commit=True, timeout=timeout, submit_min=submit_min, nav_min=nav_min
            )
            if facet_wait is not None:
                return facet_wait
            t_commit = max(timeout, submit_min)
            ch = _state_diff_channel_scores(state_diff, val)
            u, d = float(ch.get("url_change", 0.0)), float(ch.get("dom_signal", 0.0))
            fin_l = get_effective_intent(step.get("semantic") or {}).lower()
            if (
                bool(val.get("commit_no_evidence_intent_first", False))
                and intent_primary_validation_enabled(policy)
                and u == 0.0
                and d == 0.0
            ):
                wf_seed = {
                    "type": _no_evidence_wait(str(val.get("commit_no_evidence_wait", "intent_outcome"))),
                    "target": "",
                    "timeout": t_commit,
                }
                nudge = _commit_no_diff_intent_wait_override(fin_l, wf_seed, state_diff, policy, step)
                wf_out = nudge if nudge is not None else wf_seed
            else:
                wf_out = _pick_commit_wait(step, state_diff, policy, timeout, submit_min)
                if intent_primary_validation_enabled(policy) and fin_l:
                    nudge2 = _commit_no_diff_intent_wait_override(fin_l, wf_out, state_diff, policy, step)
                    if nudge2 is not None:
                        wf_out = nudge2
            if intent_primary_validation_enabled(policy):
                dl = policy.get("decision_layer") if isinstance(policy.get("decision_layer"), dict) else {}
                prefer_url = [
                    str(x).lower()
                    for x in (
                        dl.get("commit_intent_prefer_url_substrings")
                        or ["navigate", "redirect", "checkout", "payment"]
                    )
                ]
                prefer_dom = [
                    str(x).lower()
                    for x in (
                        dl.get("commit_intent_prefer_dom_substrings")
                        or ["modal", "dialog", "drawer", "toast", "inline"]
                    )
                ]
                ch2 = _state_diff_channel_scores(state_diff, val)
                u2, d2 = ch2.get("url_change", 0.0), ch2.get("dom_signal", 0.0)
                if u2 > 0 and d2 > 0 and abs(u2 - d2) < 0.05:
                    if fin_l and any(s in fin_l for s in prefer_url if s):
                        return {**wf_out, "type": "url_change"}
                    if fin_l and any(s in fin_l for s in prefer_dom if s):
                        t_wf = int(wf_out.get("timeout") or t_commit)
                        return dom_signal_to_intent_mapped_wait(step, t_wf)
            return wf_out

        dest = try_destructive_confirmation_wait(
            step, policy, is_commit=False, timeout=timeout, submit_min=submit_min, nav_min=nav_min
        )
        if dest is not None:
            return dest

        facet_nc = try_intent_validation_facets(
            step, policy, is_commit=False, timeout=timeout, submit_min=submit_min, nav_min=nav_min
        )
        if facet_nc is not None:
            return facet_nc

        open_sel = try_open_select_wait_rule(
            step, policy, is_commit=False, timeout=timeout, nav_min=nav_min
        )
        if open_sel is not None:
            return open_sel

        non_commit_dom = str(val.get("non_commit_dom_wait_on_diff", "intent_outcome")).strip().lower()
        if non_commit_dom == "dom_change":
            non_commit_dom = "intent_outcome"
        if url_changed:
            return {"type": "url_change", "target": "", "timeout": max(timeout, nav_min)}
        if dom_changed and non_commit_dom != "none":
            return dom_signal_to_intent_mapped_wait(step, timeout)
        return {"type": "none", "target": "", "timeout": timeout}

    if url_changed:
        return {"type": "url_change", "target": "", "timeout": max(timeout, nav_min)}
    if dom_changed:
        return dom_signal_to_intent_mapped_wait(step, timeout)
    return {"type": "none", "target": "", "timeout": timeout}


def _diff_strength(state_diff: dict[str, Any]) -> float:
    new_els = list(state_diff.get("new_elements") or [])
    removed = list(state_diff.get("removed_elements") or [])
    text_change = list(state_diff.get("text_change") or [])
    raw = (len(new_els) + len(removed) + len(text_change)) / 20.0
    return min(1.0, max(0.0, raw))


def _own_words(source_step: Any) -> set[str]:
    """Words of what the person chose in this step: the clicked item's own text and any value they typed or picked."""
    if not isinstance(source_step, dict):
        return set()
    target = source_step.get("target") if isinstance(source_step.get("target"), dict) else {}
    action = source_step.get("action") if isinstance(source_step.get("action"), dict) else {}
    parts = [str(target.get(k) or "") for k in ("inner_text", "aria_label", "label_text", "title", "name", "alt", "placeholder")]
    parts.append(str(action.get("value") or ""))
    return {w for w in re.findall(r"\w+", " ".join(parts).lower()) if len(w) >= 3}


def _drop_echoes(text_change: list[str], source_step: Any) -> list[str]:
    """New page text that only repeats what the person just chose says nothing about how the app responded (the
    opened item's own name, the value they typed): a check built from it is true of this recording and no other."""
    own = _own_words(source_step)
    if not own:
        return text_change
    kept = []
    for t in text_change:
        words = {w for w in re.findall(r"\w+", str(t).lower()) if len(w) >= 3}
        if words and words <= own:
            continue
        kept.append(t)
    return kept


def infer_success_conditions(
    wait_for: dict[str, Any],
    state_diff: dict[str, Any],
    page_url: str,
    policy: dict[str, Any] | None = None,
    *,
    final_intent: str = "",
    source_step: Step | None = None,
) -> dict[str, Any]:
    """Success predicates: state_diff is evidence; when intent-primary, FINAL_INTENT augments expectations."""
    wt = str(wait_for.get("type") or "none")
    new_els = list(state_diff.get("new_elements") or [])
    removed = list(state_diff.get("removed_elements") or [])
    text_change = _drop_echoes(list(state_diff.get("text_change") or []), source_step)
    strength = _diff_strength(state_diff)
    dl =policy.get("decision_layer") if isinstance(policy, dict) and isinstance(policy.get("decision_layer"), dict) else {}
    add_intent_tokens = bool(dl.get("success_add_intent_tokens", True))
    intent_primary = intent_primary_validation_enabled(policy) if isinstance(policy, dict) else False

    def _merge_tokens(base: list[str]) -> list[str]:
        out = list(base)
        if intent_primary and final_intent and add_intent_tokens and isinstance(policy, dict):
            candidates = intent_outcome_tokens(final_intent, policy)
            # Only require grounding when we actually have real page/target context to check
            # against (a live compile always passes source_step — see
            # decision_layer.py::infer_compiled_validation). Without it there's nothing to verify
            # against, so keep today's behavior rather than silently dropping every token.
            if source_step:
                candidates = intent_tokens_grounded_in_context(candidates, source_step)
                # "Grounded in the context" means the word was on the page BEFORE the action (usually the clicked
                # control's own label), so it cannot say whether the action worked: same rule as for new page text.
                candidates = _drop_echoes(candidates, source_step)
            for t in candidates:
                if t and t not in out:
                    out.append(t)
        if isinstance(policy, dict) and source_step and destructive_compiler_step(source_step, policy):
            val = policy.get("validation") if isinstance(policy.get("validation"), dict) else {}
            lex = val.get("destructive_confirm_expected_tokens")
            if isinstance(lex, list):
                for t in lex:
                    s = str(t).strip().lower()
                    if s and s not in out:
                        out.append(s)
        return out[:16]

    if wt == "none":
        out = {
            "url_not_contains": "",
            "required_elements": [],
            "forbidden_elements": [],
            "expected_text_tokens": _merge_tokens(text_change[:8]),
            "state_diff_strength": strength,
            "state_diff_as_hint": True,
        }
        if intent_primary and final_intent:
            out["final_intent"] = final_intent[:160]
            out["intent_validation_primary"] = True
        return out

    val = policy.get("validation") if isinstance(policy, dict) and isinstance(policy.get("validation"), dict) else {}
    min_strength_for_diff_elements = float(val.get("intent_required_elements_min_diff_strength", 0.08))
    required_from_diff = new_els[:24] if new_els else (text_change[:8] if text_change else [])
    if intent_primary and final_intent and strength < min_strength_for_diff_elements:
        required_from_diff = []

    # Ephemeral elements (cookie/consent banners, toasts, notifications) are typically gone,
    # dismissed, or repositioned by the time the runtime verifies — never let one become the
    # REQUIRED promotion candidate in build.py's primary-signal picker. Demote it to an advisory
    # text token instead of dropping the signal entirely.
    ephemeral = [el for el in required_from_diff if is_ephemeral_anchor(el)]
    required_from_diff = [el for el in required_from_diff if not is_ephemeral_anchor(el)]

    out = {
        "url_not_contains": "",
        "required_elements": required_from_diff,
        "forbidden_elements": removed[:24],
        "expected_text_tokens": _merge_tokens((text_change[:8] + ephemeral)[:8]),
        "page_url_hint": page_url[:500],
        "state_diff_strength": strength,
        "state_diff_as_hint": True,
    }
    if intent_primary and final_intent:
        out["final_intent"] = final_intent[:160]
        out["intent_validation_primary"] = True
    return out
