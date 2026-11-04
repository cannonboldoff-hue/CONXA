"""Uncertainty / failure-first helpers (no execution — advisory for agents + API)."""

from __future__ import annotations

from typing import Any

from conxa_compile.compiler.intent_access import get_effective_intent
from conxa_compile.policy.bundle import get_policy_bundle

def default_destructive_tokens() -> tuple[str, ...]:
    """The words that mark a step destructive. Data, not code: policy/default_policy.json
    (validation.destructive_intent_substrings), pinned by runtime/test/fixtures/destructive_cases.json."""
    raw = get_policy_bundle().section("validation").get("destructive_intent_substrings")
    if not isinstance(raw, list):
        return ()
    return tuple(dict.fromkeys(str(x).strip().lower() for x in raw if str(x).strip()))


def is_score_below(value: float, threshold: float) -> bool:
    return value < threshold


def is_top_two_ambiguous(scores: list[float], *, margin: float = 0.03) -> bool:
    if len(scores) < 2:
        return False
    a, b = sorted(scores, reverse=True)[:2]
    return (a - b) < margin


def anchors_missing(reference: dict[str, Any], *, min_count: int = 1) -> bool:
    anchors = reference.get("anchors")
    if min_count <= 0:
        return False
    return not anchors or len(anchors) < min_count


def state_mismatch(expected_after: str | None, observed_after: str | None) -> bool:
    if expected_after is None or observed_after is None:
        return False
    return expected_after != observed_after


def _selector_strength(reference: dict[str, Any]) -> float:
    sel = reference.get("selectors") or {}
    css = str(sel.get("css") or "").strip()
    aria = str(sel.get("aria") or "").strip()
    text_based = str(sel.get("text_based") or "").strip()
    xpath = str(sel.get("xpath") or "").strip()
    score = 0.0
    if css:
        score += 0.4
    if aria:
        score += 0.35
    if text_based:
        score += 0.2
    if xpath:
        score += 0.05
    return min(1.0, score)


def _intent_is_clear(reference: dict[str, Any]) -> bool:
    semantic = reference.get("semantic") or {}
    intent = get_effective_intent(semantic).strip().lower()
    if not intent:
        return False
    generic = {"interact", "perform_action", "provide_input", "advance_ui_flow", "activate_control"}
    return intent not in generic


def _confidence_value(reference: dict[str, Any]) -> float | None:
    semantic = reference.get("semantic") or {}
    raw = reference.get("confidence")
    if raw is None:
        raw = semantic.get("llm_confidence")
    try:
        value = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    if value is None:
        return None
    return max(0.0, min(1.0, value))


def _normalize_for_match(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _token_matches(value: str, token: str) -> bool:
    norm = _normalize_for_match(value)
    if not norm:
        return False
    padded = f"_{norm}_"
    tok = _normalize_for_match(token)
    return f"_{tok}_" in padded


def _intent_has_destructive_token(reference: dict[str, Any]) -> bool:
    semantic = reference.get("semantic") or {}
    intent = get_effective_intent(semantic)
    if not intent:
        return False
    return any(_token_matches(intent, token) for token in default_destructive_tokens())


def _ui_supports_destructive(reference: dict[str, Any]) -> bool:
    target = reference.get("target") or {}
    semantic = reference.get("semantic") or {}
    role = str(target.get("role") or semantic.get("role") or "").strip().lower()
    typ = str(target.get("type") or semantic.get("input_type") or "").strip().lower()
    text_fields = [
        str(target.get("inner_text") or ""),
        str(target.get("aria_label") or ""),
        str(target.get("name") or ""),
    ]
    text_signal = any(any(_token_matches(txt, token) for token in default_destructive_tokens()) for txt in text_fields if txt)
    action = str(reference.get("action_kind") or "").strip().lower()
    is_actionable = action in {"click", "select", "submit", "delete", "remove", "revoke"}
    role_supports = role in {"button", "menuitem", "option", "link"}
    type_supports = typ in {"button", "submit"}
    return text_signal and is_actionable and (role_supports or type_supports or action in {"delete", "remove", "revoke"})


def _destructive_strength(reference: dict[str, Any]) -> str:
    semantic = reference.get("semantic") or {}
    explicit = semantic.get("is_destructive")
    if isinstance(explicit, bool):
        return "strong" if explicit else "none"
    if _intent_has_destructive_token(reference) and _ui_supports_destructive(reference):
        return "strong"
    if _intent_has_destructive_token(reference):
        return "weak"
    return "none"


def _anchors_required(reference: dict[str, Any]) -> bool:
    return _destructive_strength(reference) == "strong"


def _destructive_signal_is_weak(reference: dict[str, Any]) -> bool:
    return _destructive_strength(reference) == "weak"


def _anchors_warning_eligible(reference: dict[str, Any]) -> bool:
    if _destructive_signal_is_weak(reference):
        return True
    action = str(reference.get("action_kind") or "").lower()
    selector_strong = _selector_strength(reference) >= 0.6
    intent_clear = _intent_is_clear(reference)
    conf = _confidence_value(reference)
    confidence_high = conf is not None and conf >= 0.8
    optional_action = action in {"focus", "type", "click", "select"}
    return selector_strong and (confidence_high or (intent_clear and optional_action))


def audit_reference(reference: dict[str, Any]) -> list[str]:
    """Deterministic structural checks on a frozen signal_reference."""
    issues: list[str] = []
    action = str(reference.get("action_kind") or "").strip().lower()
    if action in {"navigate", "goto", "go_to", "open"}:
        return issues
    if action != "scroll":
        sel = reference.get("selectors") or {}
        if not sel:
            issues.append("missing_selectors")
        else:
            keys = ("css", "aria", "text_based", "xpath")
            any_nonempty = any(str(sel.get(k) or "").strip() for k in keys)
            if not any_nonempty:
                issues.append("empty_primary_css")
    # Scroll is a viewport movement action; it may not have stable relational anchors.
    if action != "scroll" and anchors_missing(reference, min_count=1):
        if _anchors_required(reference):
            issues.append("anchors_empty_required")
        elif _anchors_warning_eligible(reference):
            issues.append("anchors_empty_warn")
        else:
            issues.append("anchors_empty")
    vb = (reference.get("visual") or {}).get("bbox") or {}
    if action != "scroll" and (int(vb.get("w", 0) or 0) < 2 or int(vb.get("h", 0) or 0) < 2):
        issues.append("weak_visual_bbox")
    return issues
