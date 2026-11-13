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
