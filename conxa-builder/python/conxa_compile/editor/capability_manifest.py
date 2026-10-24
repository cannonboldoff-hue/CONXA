"""The Human Review Copilot's ground-truth statement of what it may propose and what the runtime
actually does with a step (BUILD-26 stage g — "the copilot doesn't know what the runtime can do").

Generated from the SAME data structures the editor's own patch gate enforces
(`editor/patch_gate.py`'s `*_ALLOWED_KEYS` constants and `editor/action_registry.py`'s
kind/category/insertable/selector/value sets) rather than hand-duplicated prose — the failure
mode this exists to prevent is the manifest telling the model something is allowed that the gate
then silently drops, wasting the reviewer's turn. The only hand-maintained content is
`RUNTIME_NOTES`: one line per kind naming what `runtime/app/*` actually requires or ignores for
it (see docs/TRD.md's runtime dispatch table) — prose the gate has no way to derive, since the
gate enforces the editor's contract, not the runtime's.
"""

from __future__ import annotations

from typing import Any

from conxa_compile.editor.action_registry import (
    ACTION_KIND_ORDER,
    MARKER_ACTIONS,
    SELECTOR_ACTIONS,
    VALUE_ACTIONS,
    VALUE_LABELS,
    action_spec,
)
from conxa_compile.editor.patch_gate import (
    AI_REVIEW_ALLOWED_KEYS,
    BRANCH_ALLOWED_KEYS,
    CHECK_ASSERT_ALLOWED_KEYS,
    DEFAULT_ALLOWED_KEYS,
    EXTRACT_ALLOWED_KEYS,
    FOR_EACH_ALLOWED_KEYS,
    HANDOVER_ALLOWED_KEYS,
    NAVIGATE_ALLOWED_KEYS,
    SCROLL_ALLOWED_KEYS,
    WAIT_SCREENSHOT_ALLOWED_KEYS,
)

# One line per insertable kind, naming what the RUNTIME (not the editor) actually requires or
# ignores — see runtime/app/handlers.js + run.js. Hand-maintained because it describes behaviour
# outside this codebase's Python; kept short and factual, not a copy of the dispatch table.
RUNTIME_NOTES: dict[str, str] = {
    "navigate": "Navigates the step's own tab; the next step waits for domcontentloaded.",
    "browser_back": "page.goBack() only — the recorded url is informational, never navigated to.",
    "browser_forward": "page.goForward() only — same non-navigating url caveat as browser_back.",
    "click": "Runs handler_hints.hover_chain first if present; intercepted-pointer retries once.",
    "dblclick": "Same resolution path as click.",
    "extract": "Reads one value off the matched element into the output named by `value`; nothing is clicked. extract.source is text, value or attribute (extract.attribute); extract.pattern keeps its first capture group. No match is a clear failure, never an empty output.",
    "right_click": "Same resolution path as click.",
    "hover": "Same resolution path as click; no value, no side effect beyond hovering.",
    "focus": "Falls back to click-then-focus; no-op if the step has no target.",
    "type": "Uses .fill(), not real keystrokes — identical to fill at execution time.",
    "fill": "Uses .fill(); interpolates {{vars}} in value first.",
    "set_checkbox": "value 'false' unchecks; a multi-select choice reads input_binding as a list.",
    "set_radio": "Resolves via choice.options[].selector, falling back to a name+value CSS query.",
    "select": "Selects by visible LABEL text, not the option's value attribute.",
    "select_option": "For an aria listbox (handler_hints.choice.kind), clicks the option directly.",
    "date_pick": "Tries typed input first, falls back to a calendar-grid click strategy.",
    "drag_drop": "Needs src_selector/dst_selector or a JSON value with src_css/dst_css.",
    "keyboard_shortcut": "Marks the step as possibly-having-acted for the action guard (EXEC-24).",
    "scroll": "Scrolls a selector into view, or by raw delta_x/delta_y with no target.",
    "check": "Regex-matches page.url() against pattern/check_pattern — no recovery on a miss.",
    "assert": "assert_kind in {url, selector, visible, text}; fails the run on a miss.",
    "wait": "Milliseconds, clamped to a 1000ms ceiling regardless of the configured value.",
    "screenshot": "Capture-and-discard — produces no artifact a later step can read.",
    "ai_review": (
        "Pauses the run and asks the MCP caller (or the Studio sandbox) the stored prompt against "
        "the live page; binds the JSON answer into inputs under output_name. Never enters the "
        "recovery cascade. A destructive step may not directly follow one (patch gate enforces this)."
    ),
    "handover": (
        "Pauses and yields the live page to a person (2FA/CAPTCHA/e-signature); races an in-page "
        "banner, a file-drop, and a loopback signal against a timeout, then resumes in the same "
        "call if it arrives in time. No answer to validate — the person may change the page."
    ),
    "upload": "Resolves value as file path(s); can consume (delete) a prior download_observed file.",
    "upload_intent": (
        "Recorded provenance only — the packager collapses it to a real `upload` step at build "
        "time (same value and selector), so it DOES execute. Not insertable by hand."
    ),
    "if_present": "Best-effort probe + body; NEVER enters the Tier 1-4 recovery cascade.",
    "try_dismiss": "Best-effort candidates + Escape fallback; never enters the recovery cascade.",
    "wait_for_one_of": "Races each option's probe up to timeout_ms; never enters the recovery cascade.",
    "for_each": (
        "max_iterations is REQUIRED at runtime — a missing cap fails the whole run, not just the "
        "loop. Exactly one of rows.container_selector or items must be set. The loop body runs "
        "through the FULL cascade (unlike if_present/try_dismiss bodies)."
    ),
}

_PER_KIND_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "navigate": NAVIGATE_ALLOWED_KEYS,
    "scroll": SCROLL_ALLOWED_KEYS,
    "wait": WAIT_SCREENSHOT_ALLOWED_KEYS,
    "screenshot": WAIT_SCREENSHOT_ALLOWED_KEYS,
    "check": CHECK_ASSERT_ALLOWED_KEYS,
    "assert": CHECK_ASSERT_ALLOWED_KEYS,
    "ai_review": AI_REVIEW_ALLOWED_KEYS,
    "handover": HANDOVER_ALLOWED_KEYS,
    "if_present": BRANCH_ALLOWED_KEYS,
    "try_dismiss": BRANCH_ALLOWED_KEYS,
    "wait_for_one_of": BRANCH_ALLOWED_KEYS,
    "for_each": FOR_EACH_ALLOWED_KEYS,
    "extract": EXTRACT_ALLOWED_KEYS,
}

# The eight assertion `type` values the runtime's verifyStep actually evaluates
# (runtime/app/assertions.js) — anything else silently passes, which is worth the model knowing.
ASSERTION_TYPES = (
    "url_changed", "url_pattern", "selector_present", "selector_absent",
    "text_present", "text_absent", "value_equals", "state_changed",
)


def _kind_row(kind: str) -> dict[str, Any]:
    spec = action_spec(kind)
    allowed_keys = _PER_KIND_ALLOWED_KEYS.get(kind, DEFAULT_ALLOWED_KEYS)
    return {
        "kind": kind,
        "category": spec.category,
        "insertable": spec.insertable,
        "needs_target": spec.selectors,
        "takes_value": spec.value,
        "value_label": VALUE_LABELS.get(kind) if spec.value else None,
        "patchable_fields": sorted(allowed_keys),
        "runtime_note": RUNTIME_NOTES.get(kind, ""),
    }


def build_capability_manifest() -> dict[str, Any]:
    """The compact table sent once per Copilot session (L0 in the BUILD-26-g plan). Cached at
    module scope by the caller (`llm/copilot.py`) — this only changes when the Studio build does."""
    kinds = [_kind_row(k) for k in ACTION_KIND_ORDER if k not in MARKER_ACTIONS]
    marker_kinds = sorted(MARKER_ACTIONS)
    return {
        "insertable_kinds": [row["kind"] for row in kinds if row["insertable"]],
        "selector_kinds": sorted(SELECTOR_ACTIONS),
        "value_kinds": sorted(VALUE_ACTIONS),
        "marker_kinds": marker_kinds,
        "kinds": kinds,
        "assertion_types": list(ASSERTION_TYPES),
        "notes": [
            "A step in selector_kinds can only be inserted by cloning an existing step's "
            "identity_bundle (identity_from_step_key) or from a runtime-observed overlay — "
            "never by writing a selector yourself.",
            "recovery.max_attempts and no_recovery_block are never read by the runtime; do not "
            "propose them as a fix for anything.",
            "marker_kinds are read-only recording bookkeeping (tab/frame/dialog/clipboard "
            "events) — never insertable, never patchable.",
        ],
    }


if __name__ == "__main__":
    # ponytail: the anti-drift self-check named in the plan — every insertable kind has a
    # runtime_note, and the manifest's claimed allowed keys are never a superset of what the gate
    # actually accepts (a superset would mean the model is told a lie; a subset is merely
    # conservative and harmless).
    import copy

    from conxa_compile.editor.patch_gate import validate_editor_patch
    from conxa_compile.editor.workflow_mutations import _new_manual_step
    from conxa_compile.policy.bundle import get_policy_bundle

    manifest = build_capability_manifest()
    for row in manifest["kinds"]:
        if row["insertable"]:
            assert row["runtime_note"], f"{row['kind']} is insertable but has no runtime_note"

    policy = get_policy_bundle().data
    for kind in ("navigate", "scroll", "wait", "check", "ai_review", "handover", "if_present", "for_each"):
        scaffold = _new_manual_step(kind, "")
        allowed = _PER_KIND_ALLOWED_KEYS[kind if kind != "screenshot" else "wait"]
        for key in allowed:
            if key in ("action", "target", "frame", "branch", "for_each", "signals"):
                continue  # structural keys need a real shaped value, not exercised by this smoke test
            probe = copy.deepcopy(scaffold)
            try:
                validate_editor_patch(probe, {key: probe.get(key, "")}, policy)
            except ValueError:
                pass  # a value-shape rejection is fine; an "unsupported key" rejection is not

    print("ok")
