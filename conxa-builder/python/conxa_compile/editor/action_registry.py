"""Shared Human Edit action contract.

The recorder and skill-package compiler know more actions than the original
editor UI exposed. Keep the editor, saved export, and patch validation aligned
through this registry instead of scattering allowlists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from conxa_compile.compiler.action_policy import RECOVERY_ACTION_TYPES


ACTION_KIND_ORDER = (
    "navigate",
    # Browser Back/Forward buttons (incl. Alt+Left/Right) — recorded-only, not insertable.
    # Real executable steps at replay time: page.goBack()/page.goForward() on the step's tab.
    "browser_back",
    "browser_forward",
    "click",
    "dblclick",
    "right_click",
    "hover",
    "focus",
    "type",
    "fill",
    "set_checkbox",
    "set_radio",
    "select",
    "select_option",
    "date_pick",
    "drag_drop",
    "keyboard_shortcut",
    "scroll",
    "check",
    "assert",
    "wait",
    "screenshot",
    # Read one value off the page into a named output the run returns to its caller. Target comes
    # from the recording (full IdentityBundle) like any click; the step's `value` is the output name
    # and its `extract` block says what to read. See runtime/app/handlers.js::extract.
    "extract",
    # EXEC-13: author-placed reasoning checkpoint — pauses execution, asks Claude a stored
    # question about the live page, binds the structured answer into `inputs` under
    # `output_name`. No selector/identity_bundle (SELECTOR_ACTIONS excludes it); value-bearing
    # (the "value" carried is the output binding name, not a page-interaction value — see
    # VALUE_LABELS below) like "wait"/"screenshot" before it.
    "ai_review",
    # EXEC-21 (hand-over shape): pauses execution and yields the live page to a PERSON for the
    # part only they can do (2FA, a CAPTCHA, an e-signature) — the human sibling of ai_review
    # immediately above, same "no selector/identity_bundle, value-bearing like wait/screenshot"
    # shape. The generic value field (VALUE_LABELS below) carries the message shown to the
    # person, exactly as ai_review's carries its output binding name — everything else
    # (on_failure, an optional resume_when probe) is a top-level `handover_*` field validated by
    # patch_gate.py, same convention as ai_review's own `ai_review_*` fields.
    "handover",
    "upload_intent",
    "upload",
    "tab_open",
    "tab_switch",
    "popup",
    "frame_enter",
    "frame_exit",
    "download_observed",
    "dialog_appeared",
    "dialog_accept",
    "dialog_dismiss",
    "file_chooser_opened",
    "clipboard_copy",
    "clipboard_paste",
    # Conditional/branch primitives (EXEC-1). Not markers: they carry a real probe
    # target/candidates/options (in SkillStep.branch), unlike tab_open/frame_enter/etc.
    # Insertable since the 2026-07-10 Human Edit redesign — see BranchBodyEditor.tsx for the
    # nested-body authoring UI and workflow_mutations.py::_new_manual_step for the scaffold.
    "if_present",
    "try_dismiss",
    "wait_for_one_of",
    # EXEC-38: iteration primitive — "for each row matching X, do steps A-C." Not a marker
    # (carries a real rows/as/max_iterations/steps body, same "real probe, not a bookkeeping
    # event" reasoning as the branch primitives above). Authored the same way branch bodies are
    # — wrap N existing steps into a body — one level deep only.
    "for_each",
    # Outcome fork: "after the previous action the app is in one of several known states, each
    # with its own policy" (compiler/outcome_fork.py). Built only from a recorded decision dialog,
    # so deliberately NOT in INSERTABLE_ACTIONS: a vendor edits its policy through the
    # set_fork_policy mutation, never by patching or hand-inserting one.
    "outcome_fork",
)

MARKER_ACTIONS = frozenset(
    {
        "tab_open",
        "tab_switch",
        "popup",
        "frame_enter",
        "frame_exit",
        "download_observed",
        "dialog_appeared",
        "dialog_accept",
        "dialog_dismiss",
        "file_chooser_opened",
        "clipboard_copy",
        "clipboard_paste",
    }
)

INSERTABLE_ACTIONS = frozenset(
    {
        "navigate",
        "click",
        "dblclick",
        "right_click",
        "hover",
        "focus",
        "type",
        "fill",
        "set_checkbox",
        "set_radio",
        "select",
        "select_option",
        "date_pick",
        "drag_drop",
        "keyboard_shortcut",
        "scroll",
        "check",
        "assert",
        "wait",
        "screenshot",
        "extract",
        "ai_review",
        "handover",
        "upload",
        "if_present",
        "try_dismiss",
        "wait_for_one_of",
        "for_each",
    }
)

SELECTOR_ACTIONS = frozenset(
    {
        "click",
        "dblclick",
        "right_click",
        "hover",
        "focus",
        "type",
        "fill",
        "set_checkbox",
        "set_radio",
        "select",
        "select_option",
        "date_pick",
        "drag_drop",
        "upload",
        "extract",
        "if_present",
        "try_dismiss",
        "wait_for_one_of",
    }
)

VALUE_ACTIONS = frozenset(
    {
        "type",
        "fill",
        "set_checkbox",
        "set_radio",
        "select",
        "select_option",
        "date_pick",
        "drag_drop",
        "keyboard_shortcut",
        "wait",
        "upload",
        # The output name the run reports the read value under (see VALUE_LABELS below).
        "extract",
        # EXEC-13: not a page-interaction value — this is the output binding name later steps
        # read the reviewed answer back from (see VALUE_LABELS below).
        "ai_review",
        # EXEC-21: likewise not a page-interaction value — this is the message shown to the
        # person in the hand-over banner (see VALUE_LABELS below).
        "handover",
    }
)

ACTION_LABELS = {
    "navigate": "Navigate",
    "browser_back": "Browser back",
    "browser_forward": "Browser forward",
    "click": "Click",
    "dblclick": "Double click",
    "right_click": "Right click",
    "hover": "Hover",
    "focus": "Focus",
    "type": "Type",
    "fill": "Fill",
    "set_checkbox": "Set checkbox",
    "set_radio": "Set radio",
    "select": "Select",
    "select_option": "Select option",
    "date_pick": "Date pick",
    "drag_drop": "Drag and drop",
    "keyboard_shortcut": "Keyboard shortcut",
    "scroll": "Scroll",
    "check": "Check",
    "assert": "Assert",
    "wait": "Wait",
    "screenshot": "Screenshot",
    "ai_review": "AI Review",
    "handover": "Hand over to person",
    "upload_intent": "Upload intent",
    "upload": "Upload",
    "tab_open": "Tab open",
    "tab_switch": "Tab switch",
    "popup": "Popup",
    "frame_enter": "Frame enter",
    "frame_exit": "Frame exit",
    "download_observed": "Download observed",
    "dialog_appeared": "Dialog appeared",
    "dialog_accept": "Dialog accept",
    "dialog_dismiss": "Dialog dismiss",
    "file_chooser_opened": "File chooser opened",
    "clipboard_copy": "Clipboard copy",
    "clipboard_paste": "Clipboard paste",
    "if_present": "If present",
    "try_dismiss": "Try dismiss",
    "wait_for_one_of": "Wait for one of",
    "for_each": "For each row",
    "outcome_fork": "Popup decision",
}

CATEGORIES = {
    "navigate": "flow",
    "browser_back": "flow",
    "browser_forward": "flow",
    "scroll": "flow",
    "check": "validation",
    "assert": "validation",
    "wait": "validation",
    "screenshot": "validation",
    "extract": "validation",
    "ai_review": "validation",
    "handover": "validation",
    "click": "pointer",
    "dblclick": "pointer",
    "right_click": "pointer",
    "hover": "pointer",
    "focus": "pointer",
    "type": "input",
    "fill": "input",
    "set_checkbox": "input",
    "set_radio": "input",
    "select": "input",
    "select_option": "input",
    "date_pick": "input",
    "drag_drop": "advanced",
    "keyboard_shortcut": "advanced",
    "upload": "advanced",
    "upload_intent": "advanced",
    "if_present": "conditional",
    "try_dismiss": "conditional",
    "wait_for_one_of": "conditional",
    "for_each": "iteration",
    "outcome_fork": "conditional",
}

VALUE_LABELS = {
    "type": "Value",
    "fill": "Value",
    "set_checkbox": "Checked (true or false)",
    "set_radio": "Value",
    "select": "Option value",
    "select_option": "Option value",
    "date_pick": "Date value",
    "drag_drop": "Drag payload JSON",
    "keyboard_shortcut": "Shortcut JSON or key combo",
    "wait": "Milliseconds",
    "upload": "File path input",
    "ai_review": "Output binding name",
    "extract": "Output name",
    "handover": "Message shown to the person",
}


@dataclass(frozen=True)
class ActionSpec:
    kind: str
    label: str
    category: str
    insertable: bool
    marker: bool
    selectors: bool
    value: bool
    value_label: str
    recovery: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_action_kind(kind: Any) -> str:
    text = str(kind or "").strip().lower().replace("-", "_")
    if text == "input":
        return "type"
    return text


def action_spec(kind: Any) -> ActionSpec:
    normalized = normalize_action_kind(kind)
    if normalized not in ACTION_KIND_ORDER:
        return ActionSpec(
            kind=normalized,
            label=normalized.replace("_", " ").title() if normalized else "Unknown",
            category="unknown",
            insertable=False,
            marker=False,
            selectors=False,
            value=False,
            value_label="Value",
            recovery=False,
        )
    marker = normalized in MARKER_ACTIONS
    return ActionSpec(
        kind=normalized,
        label=ACTION_LABELS.get(normalized, normalized.replace("_", " ").title()),
        category="marker" if marker else CATEGORIES.get(normalized, "advanced"),
        insertable=normalized in INSERTABLE_ACTIONS,
        marker=marker,
        selectors=normalized in SELECTOR_ACTIONS,
        value=normalized in VALUE_ACTIONS,
        value_label=VALUE_LABELS.get(normalized, "Value"),
        recovery=normalized in RECOVERY_ACTION_TYPES,
    )


def action_spec_dict(kind: Any) -> dict[str, Any]:
    return action_spec(kind).to_dict()


def is_supported_action(kind: Any) -> bool:
    return normalize_action_kind(kind) in ACTION_KIND_ORDER


def is_marker_action(kind: Any) -> bool:
    return normalize_action_kind(kind) in MARKER_ACTIONS


def manual_action_kinds() -> tuple[str, ...]:
    return tuple(kind for kind in ACTION_KIND_ORDER if kind in INSERTABLE_ACTIONS)


def default_action_value(kind: str) -> Any:
    normalized = normalize_action_kind(kind)
    if normalized == "set_checkbox":
        return "true"
    if normalized == "keyboard_shortcut":
        return '{"key":"Enter","modifiers":{"ctrl":false,"shift":false,"alt":false,"meta":false}}'
    if normalized == "drag_drop":
        return '{"src_selector":"","dst_selector":""}'
    if normalized == "wait":
        return "1000"
    if normalized == "upload":
        return "{{file_path}}"
    if normalized == "extract":
        return "extracted_value"
    if normalized == "ai_review":
        # EXEC-13: not a page-interaction value — the output binding name later steps read the
        # reviewed answer back from (see VALUE_LABELS above).
        return "review_answer"
    if normalized == "handover":
        # EXEC-21: not a page-interaction value — the message shown to the person (see
        # VALUE_LABELS above).
        return "Please complete this step, then click Done."
    if normalized in {"type", "fill", "select", "select_option", "date_pick", "set_radio"}:
        return ""
    return None
