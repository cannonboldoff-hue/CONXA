"""Deterministic human-readable step descriptions for the editor UI."""

from __future__ import annotations

import json
import re
from typing import Any

from conxa_compile.compiler.action_semantics import action_name
from conxa_compile.compiler.intent_access import get_effective_intent_from_skill_step


def _visible_label(step: dict[str, Any]) -> str:
    signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
    dom = signals.get("dom") if isinstance(signals.get("dom"), dict) else {}
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    semantic = signals.get("semantic") if isinstance(signals.get("semantic"), dict) else {}
    text = str(dom.get("inner_text") or target.get("inner_text") or semantic.get("normalized_text") or "").strip()
    if len(text) > 72:
        return text[:69] + "…"
    return text


def _selector_has_template(sel: str) -> bool:
    return "{{" in sel and "}}" in sel


# Playwright-style text locator embedded in stored primary_selector, e.g. text="{{db_name}}"
_PLAYWRIGHT_TEXT_EQ_DOUBLE = re.compile(r'^\s*text\s*=\s*"([^"]*)"\s*$', re.IGNORECASE)
_PLAYWRIGHT_TEXT_EQ_SINGLE = re.compile(r"^\s*text\s*=\s*'([^']*)'\s*$", re.IGNORECASE)


def _strip_playwright_text_equals(sel: str) -> str | None:
    """If selector is exclusively `text="..."` or `text='...'`, return the inner literal."""
    m = _PLAYWRIGHT_TEXT_EQ_DOUBLE.match(sel.strip())
    if m:
        return m.group(1).strip()
    m = _PLAYWRIGHT_TEXT_EQ_SINGLE.match(sel.strip())
    if m:
        return m.group(1).strip()
    return None


def _click_display_from_selector(primary_selector: str) -> str | None:
    """Stable label for parameterized clicks; unwraps text= wrappers to avoid ugly nested quotes."""
    ps = primary_selector.strip()
    if not ps:
        return None
    inner = _strip_playwright_text_equals(ps)
    candidate = inner if inner is not None else ps
    if not _selector_has_template(candidate):
        return None
    if len(candidate) > 72:
        return candidate[:69] + "…"
    return candidate


def _enquote_click_label(label: str) -> str:
    """Prefer double quotes around the label; fall back when that would nest badly."""
    if "'" in label and '"' in label:
        return f"[{label}]"
    if '"' in label:
        return f"'{label}'"
    return f'"{label}"'


def _click_list_label(recorded_label: str, primary_selector: str) -> str:
    """Prefer parameterized primary_selector for UI copy over frozen recording text."""
    from_sel = _click_display_from_selector(primary_selector)
    if from_sel is not None:
        return from_sel
    return recorded_label


def _humanize_slug(text: str) -> str:
    """Turn a snake_case/kebab-case identifier into space-separated words."""
    text = str(text or "").strip()
    if not text:
        return ""
    text = re.sub(r"[_\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_ACTION_VERBS = {
    "select": "Select",
    "select_option": "Select option",
    "set_checkbox": "Toggle checkbox",
    "set_radio": "Select radio option",
    "date_pick": "Pick date",
    "drag_drop": "Drag and drop",
    "keyboard_shortcut": "Press keyboard shortcut",
    "upload": "Upload file",
}

_STATIC_ACTION_LABELS = {
    "wait": "Wait",
    "wait_until": "Wait until",
    "call_workflow": "Call another workflow",
    "clipboard_write": "Copy to clipboard",
    "clipboard_read": "Read clipboard",
    "assert": "Check condition",
    "screenshot": "Take screenshot",
    "check": "Check page state",
    "upload_intent": "Recorded upload intent",
    "if_present": "Conditionally run if element present",
    "try_dismiss": "Try to dismiss element",
    "wait_for_one_of": "Wait for one of several outcomes",
}


def _choice_description(step: dict[str, Any], n: int) -> str | None:
    """`Select "Gender" (Male, Female, Other)` for a compiled multiple-choice step — the group's
    own question and its full recorded option set, never the bare
    `role=radio[name="gender"]`-shaped selector _ACTION_VERBS would otherwise fall back to
    (see CLAUDE.md's multiple-choice plan). None for a step compiled before this feature existed,
    or an ordinary standalone checkbox (no group -> no handler_hints.choice)."""
    hints = step.get("handler_hints") if isinstance(step.get("handler_hints"), dict) else {}
    if hints.get("control_kind") != "choice":
        return None
    choice = hints.get("choice") if isinstance(hints.get("choice"), dict) else {}
    options = [
        str(o.get("label") or o.get("value") or "")
        for o in (choice.get("options") or [])
        if isinstance(o, dict) and (o.get("label") or o.get("value"))
    ]
    label = str(choice.get("group_label") or "").strip() or _humanize_slug(str(choice.get("group_key") or "")) or "choice"
    if not options:
        return f'Step {n}: Select "{label}"'
    shown = ", ".join(options[:6])
    if len(options) > 6:
        shown += ", …"
    return f'Step {n}: Select "{label}" ({shown})'


def describe_step(step: dict[str, Any], step_index: int) -> str:
    n = step_index + 1
    act = action_name(step).lower()
    if act in {"select", "select_option", "set_checkbox", "set_radio"}:
        choice_desc = _choice_description(step, n)
        if choice_desc is not None:
            return choice_desc
    intent = get_effective_intent_from_skill_step(step) or str(step.get("intent") or "").strip()
    label = _visible_label(step)
    sel = str((step.get("target") or {}).get("primary_selector") or "").strip()

    if act == "scroll":
        return f"Step {n}: Scroll the page"
    if act == "navigate" or act == "goto":
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        direct_url = str((action or {}).get("url") or step.get("url") or "").strip() if isinstance(action, dict) else str(step.get("url") or "").strip()
        if direct_url:
            return f"Step {n}: Go to {direct_url[:80]}{'…' if len(direct_url) > 80 else ''}"
        ctx = (step.get("signals") or {}).get("context") or {}
        url = str(ctx.get("page_url") or "").strip()
        if url:
            return f"Step {n}: Go to {url[:80]}{'…' if len(url) > 80 else ''}"
        return f"Step {n}: Navigate"
    if act in {"browser_back", "browser_forward"}:
        verb = "Go back" if act == "browser_back" else "Go forward"
        url = str(step.get("url") or "").strip()
        if url:
            return f"Step {n}: {verb} to {url[:80]}{'…' if len(url) > 80 else ''}"
        return f"Step {n}: {verb} (browser history)"
    if act in {"fill", "type"}:
        verb = "Fill" if act == "fill" else "Type"
        v = step.get("value")
        tail = f' "{label}"' if label else ""
        if v is not None and str(v):
            return f"Step {n}: {verb}{tail} with value"
        return f"Step {n}: {verb}{tail}".strip()
    if act in {"click", "dblclick", "right_click"}:
        list_label = _click_list_label(label, sel)
        quoted = _enquote_click_label(list_label) if list_label else ""
        intent_part = f" ({_humanize_slug(intent)})" if intent else ""
        verb = {"click": "Click on", "dblclick": "Double click", "right_click": "Right click"}.get(act, "Click on")
        if quoted:
            return f"Step {n}: {verb} {quoted}{intent_part}".strip()
        if sel:
            return f"Step {n}: {verb} target {sel}{intent_part}".strip()
        return f"Step {n}: {verb}{intent_part}".strip()
    if act == "hover":
        list_label = _click_list_label(label, sel)
        quoted = _enquote_click_label(list_label) if list_label else ""
        intent_part = f" ({_humanize_slug(intent)})" if intent else ""
        if quoted:
            return f"Step {n}: Hover over {quoted}{intent_part}".strip()
        if sel:
            return f"Step {n}: Hover over target {sel}{intent_part}".strip()
        return f"Step {n}: Hover{intent_part}".strip()
    if act == "focus":
        list_label = _click_list_label(label, sel)
        quoted = _enquote_click_label(list_label) if list_label else ""
        if quoted:
            return f"Step {n}: Focus {quoted}".strip()
        if sel:
            return f"Step {n}: Focus target {sel}".strip()
        return f"Step {n}: Focus".strip()
    if act in _ACTION_VERBS:
        verb = _ACTION_VERBS[act]
        list_label = _click_list_label(label, sel)
        quoted = _enquote_click_label(list_label) if list_label else ""
        if quoted:
            return f"Step {n}: {verb} {quoted}".strip()
        if sel:
            return f"Step {n}: {verb} target {sel}".strip()
        return f"Step {n}: {verb}".strip()
    if act in _STATIC_ACTION_LABELS:
        return f"Step {n}: {_STATIC_ACTION_LABELS[act]}"
    if act in {"dialog_accept", "dialog_dismiss"}:
        verb = "Accepted" if act == "dialog_accept" else "Dismissed"
        dialog_payload: dict[str, Any] | None = None
        raw_value = step.get("value")
        if isinstance(raw_value, str):
            try:
                parsed = json.loads(raw_value)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                dialog_payload = parsed
        message = str((dialog_payload or {}).get("message") or "").strip()
        typed = str((dialog_payload or {}).get("value") or "").strip()
        quoted_message = f' "{message[:80]}{"…" if len(message) > 80 else ""}"' if message else ""
        if typed and act == "dialog_accept":
            return f'Step {n}: {verb} dialog{quoted_message}, answered "{typed}"'
        return f"Step {n}: {verb} dialog{quoted_message}"
    marker_labels = {
        "tab_open": "Recorded tab open",
        "tab_switch": "Recorded tab switch",
        "popup": "Recorded popup",
        "frame_enter": "Recorded frame enter",
        "frame_exit": "Recorded frame exit",
        "download_observed": "Recorded download",
        "dialog_appeared": "Recorded dialog",
        "file_chooser_opened": "Recorded file chooser",
        "clipboard_copy": "Recorded clipboard copy",
        "clipboard_paste": "Recorded clipboard paste",
    }
    if act in marker_labels:
        return f"Step {n}: {marker_labels[act]}"
    if act:
        extra = f" — {_humanize_slug(intent)}" if intent else ""
        return f"Step {n}: {_humanize_slug(act).capitalize()}{extra}".strip()
    humanized_intent = _humanize_slug(intent)
    return f"Step {n}: {humanized_intent or 'Recorded action'}".strip()
