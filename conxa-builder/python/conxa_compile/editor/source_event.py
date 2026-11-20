"""Find the recorded event a compiled step came from."""

from __future__ import annotations

from typing import Any


def find_source_event(step: dict[str, Any], document: dict[str, Any]) -> dict[str, Any] | None:
    """Locate the original recorded event this step was compiled from.

    Shared by the 1-click-fix path (``compiler/patch.py::_regenerate_compiled_selectors``),
    the editor evidence digest and the copilot retrieval — all need the same recorded-event lookup by
    the step's ``snapshot_ref`` within the session named by ``meta.source_session_id``.
    A step's ``source_event`` (the event's sequence) wins when set: one snapshot can be shared by
    several events (a scroll and the click after it), so the ref alone can name the wrong one.
    """
    snapshot_ref = step.get("snapshot_ref")
    sequence = step.get("source_event")
    if not snapshot_ref and sequence is None:
        return None
    source_session_id = (document.get("meta") or {}).get("source_session_id")
    if not source_session_id:
        return None

    from conxa_core.storage.session_events import read_session_events

    events = read_session_events(source_session_id)
    for ev in events:
        if sequence is not None:
            if (ev.get("extras") or {}).get("sequence") == sequence:
                return ev
        elif (ev.get("snapshot") or {}).get("ref") == snapshot_ref:
            return ev
    return None


def recorded_input_values(document: dict[str, Any]) -> dict[str, str]:
    """What the person typed (or answered in a browser prompt) while recording, per input name.

    Read from the recording itself through each step's ``source_event``, so it covers every way a literal became an
    input (a typed value, a prompt's answer) without each of them having to store a copy. Prefill for the test card
    only: never written into the workflow or its package. Empty when the recording is gone."""
    import json

    sid = (document.get("meta") or {}).get("source_session_id")
    if not sid:
        return {}
    from conxa_core.storage.session_events import read_session_events

    try:
        events = {(e.get("extras") or {}).get("sequence"): e for e in read_session_events(sid)}
    except Exception:  # noqa: BLE001 -- a prefill is a bonus; the card still opens
        return {}
    out: dict[str, str] = {}

    def walk(steps: Any) -> None:
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            name, ev = str(step.get("input_binding") or ""), events.get(step.get("source_event"))
            raw = (ev.get("action") or {}).get("value") if ev else None
            if name and name not in out and raw not in (None, ""):
                text = str(raw)
                act = step.get("action")
                if str(act.get("action") if isinstance(act, dict) else act or "") in ("dialog_accept", "dialog_dismiss"):
                    try:
                        parsed = json.loads(text)
                        text = str(parsed.get("value") or "") if isinstance(parsed, dict) else text
                    except (TypeError, ValueError):
                        pass
                if text:
                    out[name] = text
            walk(step.get("steps"))

    walk(((document.get("skills") or [{}])[0] or {}).get("steps"))
    return out
