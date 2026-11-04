"""Find the recorded event each compiled step came from.

Several compile passes (hover chains, checkbox-group and date-picker collapse, typed-date conversion) need
a step's own recorded event. They used to pair ``steps[i]`` with ``events[i]``, which is only true while
no earlier pass has added or removed a step, so every pass had to run in one exact order and a desync was
a crash. Each step already carries ``source_event`` (the recorder's sequence number), so the pairing is
looked up by that instead and the passes can run in any order.
"""

from __future__ import annotations

from typing import Any


def events_for_steps(steps: list[Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One event per step, in step order. A step with no recorded event (one a pass added itself) gets
    ``{}``, which every pass reads as "nothing special about this step".

    A step with no ``source_event`` falls back to its own position, but only when there are exactly as
    many steps as events (steps built straight from the events, which is how hand-made steps in tests
    arrive); a mismatch there means the pairing cannot be trusted, so it yields ``{}`` rather than a
    wrong event."""
    by_sequence: dict[Any, dict[str, Any]] = {}
    for ev in events:
        seq = (ev.get("extras") or {}).get("sequence")
        if seq is not None:
            by_sequence.setdefault(seq, ev)
    one_to_one = len(steps) == len(events)
    out: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        seq = getattr(step, "source_event", None)
        if seq is not None:
            out.append(by_sequence.get(seq, {}))
        elif one_to_one:
            out.append(events[i])
        else:
            out.append({})
    return out
