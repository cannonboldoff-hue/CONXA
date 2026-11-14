"""Deterministic intent normalization and ontology checks (LLM output validated here)."""

from __future__ import annotations

import re
from typing import Any

_INTENT_RE = re.compile(r"^[a-z][a-z0-9_]{2,80}$")


def generic_intents(policy: dict[str, Any]) -> set[str]:
    intent_sec = policy.get("intent") if isinstance(policy, dict) else {}
    raw = intent_sec.get("generic_intents") if isinstance(intent_sec, dict) else None
    if isinstance(raw, list):
        return {str(x).strip().lower() for x in raw}
    return {
        "",
        "interact",
        "perform_action",
        "provide_input",
        "advance_ui_flow",
        "enter_form_value",
        "activate_control",
        "focus_input",
        "click_button",
        "click_input",
        "focus_button",
    }


_TAG_ONLY_HINTS = frozenset(
    {
        "button",
        "input",
        "textarea",
        "select",
        "a",
        "div",
        "span",
        "target",
        "form",
        "label",
        "link",
        "path",
        "svg",
        "g",
    }
)

_STRUCTURE_CLICK_INTENTS = frozenset({"click_path", "click_span", "click_div", "click_svg", "click_g"})


def sanitize_intent_token(value: str, fallback: str) -> str:
    intent = "_".join(value.strip().lower().split())
    if intent == "perform_action":
        return fallback
    if not _INTENT_RE.match(intent):
        return fallback
    return intent


# A shortcut hint printed beside a menu entry ("File upload  Alt+C then U"): not part of what the control is called.
_KEY_HINT_RE = re.compile(r"\s+(?:alt|ctrl|control|shift|cmd|⌘)\s*\+.*$", re.I)


def _visible_text_for_intent(ev: dict[str, Any]) -> str:
    target = ev.get("target") or {}
    semantic = ev.get("semantic") or {}
    text = str(target.get("inner_text") or semantic.get("normalized_text") or "").strip()
    if not text:
        # An icon button has no text of its own; the page still names it (aria-label, title, a label, or its test id).
        testid = str(target.get("data_testid") or "").replace("-", " ").replace("_", " ")
        text = next((str(target.get(k)).strip() for k in ("aria_label", "title", "label_text") if target.get(k)), "") or testid.strip()
    return _KEY_HINT_RE.sub("", text).strip()


def _upgrade_tag_only_action_intent(ev: dict[str, Any], intent: str, action: str, policy: dict[str, Any]) -> str:
    """Replace click_/focus_ + bare tag hints with text-derived slugs when visible copy exists."""
    intent_sec = policy.get("intent") if isinstance(policy, dict) else {}
    if not bool(intent_sec.get("collapse_tag_only_action_intents", True)):
        return intent
    if action not in {"click", "focus"}:
        return intent
    lowered = intent.lower()
    for prefix in ("click_", "focus_"):
        if not lowered.startswith(prefix):
            continue
        hint = lowered[len(prefix) :]
        if hint not in _TAG_ONLY_HINTS:
            return intent
        raw_text = _visible_text_for_intent(ev)
        if len(raw_text) < 2:
            return intent
        et = str((ev.get("target") or {}).get("tag") or (ev.get("semantic") or {}).get("role") or "element")
        slug, _conf = semantic_slug_from_text(et, raw_text, policy)
        if not slug or slug in generic_intents(policy):
            return intent
        if prefix == "focus_" and slug.startswith("activate_control_"):
            body = slug[len("activate_control_") :]
            candidate = sanitize_intent_token(f"focus_{body}", intent)
        elif prefix == "focus_" and slug.startswith("provide_input_"):
            body = slug[len("provide_input_") :]
            candidate = sanitize_intent_token(f"focus_{body}", intent)
        else:
            candidate = sanitize_intent_token(slug, intent)
        if candidate == intent or not candidate:
            return intent
        return candidate
    return intent


def intent_specificity_score(intent: str, policy: dict[str, Any]) -> float:
    """Heuristic 0..1: longer multi-token non-generic intents score higher (for gates / audit)."""
    fin = str(intent or "").strip().lower()
    if not fin or fin in generic_intents(policy):
        return 0.0
    parts = [p for p in fin.replace("-", "_").split("_") if len(p) >= 2]
    score = 0.35 + 0.11 * min(len(parts), 6)
    if any(len(p) >= 6 for p in parts):
        score += 0.12
    if fin.startswith(("click_", "focus_")) and len(parts) >= 2 and parts[-1] not in _TAG_ONLY_HINTS:
        score += 0.1
    return round(min(1.0, score), 3)


def normalize_compiler_intent(ev: dict[str, Any], llm_intent: str, policy: dict[str, Any]) -> str:
    """Refine a real intent; never fabricate one when none exists.

    Preference order, each step only running if the previous produced nothing usable:
    1. The LLM's own intent, if specific (non-generic, non-empty, not a bare structural click).
    2. A content-derived slug for icon-only / unlabeled-container elements (path/svg/g,
       unlabeled div/span) — built from the element's intent_hint + visible text, not a guess.
    3. For a plain form-control tag (button/input/...), a bare "click_<tag>"/"focus_<tag>" seed —
       used ONLY to give _upgrade_tag_only_action_intent's visible-text lookup a shape to work
       from, not as a naming scheme in its own right.
    If none of that yields anything non-generic, returns "" — the step is flagged via the
    existing flags.generic_intent / "intent" badge / generic_or_empty_intent suggestion rather
    than silently shipping a plausible-looking template.
    """
    action = str((ev.get("action") or {}).get("action") or "").lower()
    normalized = str(llm_intent or "").strip().lower().replace(" ", "_")
    if normalized in _STRUCTURE_CLICK_INTENTS:
        normalized = ""
    generics = generic_intents(policy)

    base = normalized if (normalized and normalized not in generics and "_" in normalized) else ""

    if not base:
        base = _content_derived_intent_slug(ev, policy)

    if not base:
        tag = str((ev.get("target") or {}).get("tag") or "").lower()
        if action in {"click", "focus"} and tag in _TAG_ONLY_HINTS:
            base = f"{action}_{tag}"

    if not base:
        target = ev.get("target") or {}
        role = str((ev.get("semantic") or {}).get("role") or target.get("role") or "").lower()
        vis = _visible_text_for_intent(ev)
        # An action with no element text to name it (choosing a file) is named by what it is.
        if action in {"upload", "upload_intent"}:
            return sanitize_intent_token(action, "")
        # Anything with an interactive role (a li or div that acts as a menu entry, option, tab…) is named by what it
        # says, the same way a button is.
        if action == "click" and role in _ACTIONABLE_ROLES and len(vis) >= 2:
            slug_t, _c = semantic_slug_from_text("button", vis, policy)
            return sanitize_intent_token(slug_t, "")

    if not base:
        return ""

    refined = _refine_intent_for_action_semantics(ev, base, policy)
    upgraded = _upgrade_tag_only_action_intent(ev, refined, action, policy)
    final = str(upgraded or "").strip().lower()
    return "" if (not final or final in generics) else final


def _target_is_editable_control(ev: dict[str, Any]) -> bool:
    target = ev.get("target") or {}
    tag = str(target.get("tag") or "").lower()
    return tag in {"input", "textarea", "select"}


def _refine_intent_for_action_semantics(ev: dict[str, Any], intent: str, policy: dict[str, Any]) -> str:
    """Map action-centric intents to purpose when action implies focus, not activation (policy-gated)."""
    intent_sec = policy.get("intent") if isinstance(policy.get("intent"), dict) else {}
    if not bool(intent_sec.get("rewrite_click_on_editable_to_focus", True)):
        return intent
    action = str((ev.get("action") or {}).get("action") or "").lower()
    if action not in {"click", "focus"}:
        return intent
    if not _target_is_editable_control(ev):
        return intent
    it = str((ev.get("semantic") or {}).get("input_type") or "").lower()
    if it == "submit":
        return intent
    lowered = intent.lower()
    if lowered.startswith("click_"):
        return sanitize_intent_token("focus_" + lowered[len("click_") :], intent)
    return intent


# Roles that mean "a person can act on this", whatever tag carries them.
_ACTIONABLE_ROLES = frozenset({
    "button", "link", "checkbox", "menuitem", "menuitemradio", "menuitemcheckbox", "option", "tab", "treeitem",
    "switch", "combobox", "textbox",
})


def _target_has_actionable_semantics(target: dict[str, Any], semantic: dict[str, Any]) -> bool:
    role = str(semantic.get("role") or target.get("role") or "").lower()
    if role in _ACTIONABLE_ROLES:
        return True
    if str(target.get("aria_label") or "").strip():
        return True
    if str(target.get("name") or "").strip():
        return True
    if len(str(target.get("inner_text") or "").strip()) >= 2:
        return True
    return False


def _content_derived_intent_slug(ev: dict[str, Any], policy: dict[str, Any]) -> str:
    """Content-aware slug for icon-only / unlabeled-container elements (path/svg/g, or a div/span
    with no actionable semantics of its own) — built from the element's detected intent_hint plus
    its visible text via semantic_slug_from_text. Returns "" when the element doesn't match this
    shape, or when there's no real signal to derive from — this is refinement of real page
    content, not a naming template for elements this doesn't apply to (see normalize_compiler_intent).
    """
    target = ev.get("target") or {}
    semantic = ev.get("semantic") or {}
    tag_l = str(target.get("tag") or "").lower()
    if tag_l not in {"path", "svg", "g"} and not (
        tag_l in {"div", "span"} and not _target_has_actionable_semantics(target, semantic)
    ):
        return ""
    ih = str(semantic.get("intent_hint") or "").strip().lower()
    vis = _visible_text_for_intent(ev)
    if ih == "commit_form":
        return "submit_form_action"
    if ih == "navigate" or tag_l == "a":
        if len(vis) >= 2:
            slug_t, _c = semantic_slug_from_text("link", vis, policy)
            return sanitize_intent_token(slug_t, "navigate_link_target")
        return "navigate_link_target"
    if ih == "activate_control" and len(vis) >= 2:
        slug_t, _c = semantic_slug_from_text("button", vis, policy)
        return sanitize_intent_token(slug_t, "activate_control")
    if ih == "provide_input" and len(vis) >= 2:
        slug_t, _c = semantic_slug_from_text("input", vis, policy)
        return sanitize_intent_token(slug_t, "provide_input")
    if ih == "choose_option":
        return "choose_option"
    if len(vis) >= 2:
        slug_t, _c = semantic_slug_from_text(str(target.get("tag") or "element"), vis, policy)
        return sanitize_intent_token(slug_t, "advance_ui_flow")
    return ""


def semantic_slug_from_text(element_type: str, raw_text: str, policy: dict[str, Any]) -> tuple[str, float]:
    """Generic intent label from visible text + element type (no domain-specific login mapping)."""
    text = " ".join(raw_text.lower().split())
    conf_sec = policy.get("intent", {}) if isinstance(policy, dict) else {}
    conf_map = conf_sec.get("semantic_fallback_confidence") if isinstance(conf_sec, dict) else {}
    default_c = float(conf_map.get("default", 0.6)) if isinstance(conf_map, dict) else 0.6
    btn_c = float(conf_map.get("button_submit_like", 0.82)) if isinstance(conf_map, dict) else 0.82
    field_c = float(conf_map.get("field_token", 0.78)) if isinstance(conf_map, dict) else 0.78
    input_c = float(conf_map.get("input_generic", 0.7)) if isinstance(conf_map, dict) else 0.7

    et = (element_type or "").lower()
    slug_base = "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")[:48] or "element"

    if et == "button" and text:
        return f"activate_control_{slug_base}", btn_c
    if et == "input" and text:
        return f"provide_input_{slug_base}", field_c
    if et == "input":
        return "provide_input", input_c
    if text:
        return f"interact_{slug_base}", default_c
    return "interact", default_c
