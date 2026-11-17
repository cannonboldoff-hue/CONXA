"""Shared step action / commit / editable-target semantics (single source for step_anchors,
state_validation + validation_planner)."""

from __future__ import annotations

import re
from typing import Any

from conxa_compile.compiler.intent_access import get_effective_intent

Step = dict[str, Any]


def action_name(step: Step) -> str:
    action = step.get("action") or {}
    if isinstance(action, dict):
        return str(action.get("action") or "")
    return str(action)


def is_editable_target(step: Step) -> bool:
    target = step.get("target") or {}
    tag = str(target.get("tag") or "").lower()
    if tag != "input":
        return tag in {"textarea", "select"}
    # A file input isn't a text-entry field: clicking it invokes a native OS picker, not caret
    # placement. Treating it as "editable" makes the click->focus rewrite below turn a recorded
    # click into a `focus` step, and runtime's focus handler clicks before it focuses -- reopening
    # a dialog nothing can drive during an unattended run.
    #
    # A radio/checkbox isn't a text-entry field either, for the same reason: clicking one commits
    # a choice in one gesture (fires its own "change" -> set_radio/set_checkbox), it doesn't
    # acquire a caret to type into. Treating it as "editable" produces the exact bug this comment
    # sits next to -- a click on a radio option gets rewritten into a phantom `focus` step that
    # duplicates the real set_radio step compiler/choice.py already derives from the click.
    semantic = step.get("semantic") or {}
    input_type = str(semantic.get("input_type") or "").lower()
    if input_type in {"file", "radio", "checkbox"}:
        return False
    # A button drawn as an <input> holds no text to type either: a click on it presses it (saucedemo's checkout
    # "Continue" was planned as a box to type into, so its move to the next page was never checked).
    if input_type in {"submit", "button", "reset", "image"}:
        return False
    return True


def is_editable_field_click(step: Step) -> bool:
    if action_name(step).lower() != "click":
        return False
    return is_editable_target(step)


def looks_like_submit(step: Step, policy: dict[str, Any]) -> bool:
    """Policy-driven primary-action / submit control detection."""
    wf = policy.get("workflow") if isinstance(policy.get("workflow"), dict) else {}
    text_tokens = [str(t).lower() for t in (wf.get("submit_text_tokens") or [])]
    intent_sub = [str(t).lower() for t in (wf.get("submit_intent_substrings") or ["submit"])]
    btn_types = {str(x).lower() for x in (wf.get("submit_button_types") or ["submit", "button"])}
    semantic = step.get("semantic") or {}
    target = step.get("target") or {}
    llm_intent = get_effective_intent(semantic).lower()
    if any(sub in llm_intent for sub in intent_sub):
        return True
    if action_name(step).lower() != "click":
        return False
    text = " ".join(
        [
            str(semantic.get("normalized_text") or ""),
            str(target.get("inner_text") or ""),
            str(target.get("name") or ""),
            str(target.get("aria_label") or ""),
        ]
    ).lower()
    if text_tokens and any(token in text for token in text_tokens):
        return True
    tag = str(target.get("tag") or "").lower()
    typ = str(target.get("type") or "").lower()
    role = str(target.get("role") or semantic.get("role") or "").lower()
    if typ == "submit":
        return True
    if tag == "button" and (typ in btn_types or role == "button") and text.strip():
        return bool(text_tokens) and any(token in text for token in text_tokens)
    return False


def _has_word(text: str, token: str) -> bool:
    return bool(token) and re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", text) is not None


def only_moves_forward(step: Step, policy: dict[str, Any]) -> bool:
    """A control whose own words only move a person through the app ("Continue", "Continue Shopping", "Next", "Back",
    "Sign in") and name no commit ("Place order", "Pay now", "Confirm", "Submit order"). Whole words: "Continue" is
    navigation, "Discontinue" is not."""
    wf = policy.get("workflow") if isinstance(policy.get("workflow"), dict) else {}
    nav = [str(t).lower() for t in (wf.get("navigation_text_tokens") or [])]
    commit = [str(t).lower() for t in (wf.get("submit_text_tokens") or []) if str(t).lower() not in nav]
    semantic = step.get("semantic") or {}
    target = step.get("target") or {}
    text = " ".join(str(v or "") for v in (semantic.get("normalized_text"), target.get("inner_text"),
                                           target.get("name"), target.get("aria_label"))).lower()
    return any(_has_word(text, t) for t in nav) and not any(_has_word(text, t) for t in commit)


def commit_intent_hit(step: Step, policy: dict[str, Any]) -> bool:
    """Whether effective intent + submit heuristics indicate a commit / primary action. A button that only moves
    forward is not one, even as a form's submit button: it can be undone by going back, so it is never asked about as
    a step that can't be undone and carries no "the app accepted the request" proof (WF-3 Leg B, owner decision)."""
    wf = policy.get("workflow") if isinstance(policy.get("workflow"), dict) else {}
    subs = [str(s).lower() for s in (wf.get("commit_intent_substrings") or ["submit", "confirm"])]
    intent = get_effective_intent(step.get("semantic") or {}).lower()
    if any(sub in intent for sub in subs):
        return True
    return looks_like_submit(step, policy) and not only_moves_forward(step, policy)


def detect_hover_precondition(ev: Step, prev_ev: Step | None) -> bool:
    """Whether the immediately preceding recorded event was a hover over a different element.

    A hover-then-click sequence (e.g. menu reveal) means the target only becomes
    interactable after re-hovering the parent at runtime.
    """
    if prev_ev is None:
        return False
    if action_name(prev_ev).lower() != "hover":
        return False
    # Different element → the hover revealed the current target
    prev_target = (prev_ev.get("target") or {})
    cur_target = (ev.get("target") or {})
    prev_key = (prev_target.get("inner_text") or "", prev_target.get("aria_label") or "")
    cur_key = (cur_target.get("inner_text") or "", cur_target.get("aria_label") or "")
    return prev_key != cur_key
