"""Multiple-choice control compilation: radio/checkbox groups, native <select>, ARIA
radiogroup/listbox widgets.

Mirrors compiler/date_picker.py's structure and role in the pipeline — a recorded control that
needs its own replay strategy gets a bridge.js observation (`choice_context` here,
`date_context` there), a compile-time derivation module (this file), and a runtime-side pure
module (runtime/app/choice.js) plus a handlers.js adapter branch dispatched via
handler_hints.control_kind. See CLAUDE.md's multiple-choice plan for the full design.

Two entry points:
  - `derive_choice(ev)`   — single-option events (radio/select/aria_radio/aria_listbox): one
                            `change`/click already carries the whole choice, no run to collapse.
  - `collapse_choice_group_runs(steps, events, policy)` — checkbox groups only: bridge.js emits
    one set_checkbox event per box toggled, so a "pick all that apply" answer compiles into N
    separate steps unless collapsed into one multi-valued set_checkbox step (mirrors
    collapse_date_picker_runs's run-collapse, minus the day-cell run structure this control
    doesn't have).
"""

from __future__ import annotations

from typing import Any

from conxa_compile.compiler.input_binding import _snake_case

# Kinds that carry their whole answer in a single event — no run to collapse.
_SINGLE_SHOT_KINDS = frozenset({"radio", "select", "aria_radio", "aria_listbox"})


def _choice_context(ev: dict[str, Any]) -> dict[str, Any]:
    cc = ev.get("choice_context")
    return cc if isinstance(cc, dict) else {}


def _group_name(cc: dict[str, Any]) -> str:
    """Input name comes from the QUESTION (the group), never the ANSWER (the option clicked).
    "Gender" -> "gender", never "male" -- input_binding.derive_input_binding's label_text
    priority would otherwise name this input after whichever option happened to be recorded."""
    label = str(cc.get("group_label") or "").strip()
    if label:
        binding = _snake_case(label)
        if binding:
            return binding
    key = str(cc.get("group_key") or "").strip()
    if key:
        binding = _snake_case(key)
        if binding:
            return binding
    return "choice"


def _options_payload(cc: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for opt in cc.get("options") or []:
        if not isinstance(opt, dict):
            continue
        out.append({
            "value": str(opt.get("value") or ""),
            "label": str(opt.get("label") or opt.get("value") or ""),
            "selector": str(opt.get("selector") or ""),
        })
    return out


def derive_choice(ev: dict[str, Any]) -> dict[str, Any] | None:
    """For a single-shot choice event (radio/select/aria_radio/aria_listbox), returns
    {"value", "input_binding", "choice": <handler_hints.choice payload>} or None when the event
    carries no choice_context, or the context is a checkbox group (handled by
    collapse_choice_group_runs instead, since a checkbox group's answer spans multiple events)."""
    cc = _choice_context(ev)
    kind = str(cc.get("kind") or "")
    if kind not in _SINGLE_SHOT_KINDS:
        return None
    options = _options_payload(cc)
    if not options:
        return None
    binding = _group_name(cc)
    recorded_value = str((ev.get("action") or {}).get("value") or "")
    picked = next((o for o in options if o["value"] == recorded_value), None)
    return {
        "value": f"{{{{{binding}}}}}",
        "input_binding": binding,
        "choice": {
            "kind": kind,
            "multi": False,
            "group_key": str(cc.get("group_key") or ""),
            "group_label": str(cc.get("group_label") or ""),
            # Control that reopens a popup listbox at replay. "" for always-visible groups
            # (native <select>, a radio fieldset), which need no opening gesture.
            "opener_selector": str(cc.get("opener_selector") or ""),
            "options": options,
            # The recorded answer's label, not its value -- reconcile_inputs_with_step_values
            # seeds the auto-declared input's `default` from this, and a default an agent reads
            # back must be the same human-facing label the enum itself is built from.
            "recorded_label": picked["label"] if picked else "",
        },
    }


def collapse_choice_group_runs(
    steps: list[Any],
    events: list[dict[str, Any]],
    _policy: dict[str, Any],
    absorbed: list[dict[str, Any]] | None = None,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Collapse consecutive set_checkbox steps sharing a checkbox-group `group_key` into one
    set_checkbox step carrying every group member and which ones ended up checked. Absolute, not
    relative: the runtime sets each member's checked state to match this snapshot rather than
    replaying individual toggles against an unknown starting state (a stray earlier click, a
    site default) -- see runtime/app/handlers.js's set_checkbox choice branch.

    bridge.js::buildChoiceContext queries every group member's LIVE `.checked` property at the
    moment each event fires, so the last event in a run already carries the group's final state
    across all members -- no need to reconstruct it toggle-by-toggle from individual events.

    Must run while steps[i] still maps 1:1 onto events[i] -- same alignment requirement as
    _populate_hover_chains in build.py. Returns (collapsed_steps, synced_events): a same-length
    "representative event" per output step (the run's first event for a collapsed group, the
    original event for a pass-through step) so a LATER length-changing pass over the SAME event
    stream -- collapse_date_picker_runs, called right after this one -- can still assume 1:1
    alignment against synced_events instead of the original cleaned_events, whose length this
    function may have already shrunk. Safe specifically because date_context and choice_context
    are mutually exclusive per event (see bridge.js::serializeTarget), so a checkbox event picked
    as a run's representative never hides a date-picker run underneath it.

    `_policy` kept for call-site symmetry with collapse_date_picker_runs; unused here since the
    binding name comes from the group's own label/key, not a target-anchored
    derive_input_binding lookup."""
    if len(steps) != len(events):
        raise ValueError(
            f"collapse_choice_group_runs: steps ({len(steps)}) and events ({len(events)}) "
            "must be the same length — an earlier pass desynced them"
        )
    n = len(steps)
    out: list[Any] = []
    synced_events: list[dict[str, Any]] = []
    i = 0
    while i < n:
        cc = _choice_context(events[i])
        if cc.get("kind") != "checkbox" or not cc.get("group_key"):
            out.append(steps[i])
            synced_events.append(events[i])
            i += 1
            continue

        group_key = cc.get("group_key")
        run_end = i
        j = i + 1
        while j < n:
            ccj = _choice_context(events[j])
            if ccj.get("kind") != "checkbox" or ccj.get("group_key") != group_key:
                break
            run_end = j
            j += 1

        final_cc = _choice_context(events[run_end])
        options = _options_payload(final_cc) or _options_payload(cc)
        checked_values = sorted(
            str(o.get("value") or "")
            for o in (final_cc.get("options") or [])
            if isinstance(o, dict) and o.get("checked")
        )
        base = steps[i]
        binding = _group_name(cc)
        base.value = f"{{{{{binding}}}}}"
        base.input_binding = binding
        base.handler_hints.control_kind = "choice"
        base.handler_hints.choice = {
            "kind": "checkbox",
            "multi": True,
            "group_key": str(group_key or ""),
            "group_label": str(cc.get("group_label") or ""),
            "options": options,
            "recorded_values": checked_values,
        }
        out.append(base)
        synced_events.append(events[i])
        if absorbed is not None:
            absorbed.extend(events[i + 1:run_end + 1])
        i = run_end + 1

    return out, synced_events
