"""One compile at a time (BUILD-39).

A compile reserves a credit, runs a long model-assisted call and writes the skill; two at once
would race on usage accounting and on the process-wide event channel. The Studio therefore
compiles one workflow at a time and the rest wait their turn, in order — the "Waiting for another
compile" state, with a real position and no fake progress bar.

``acquire(workflow_id, on_position)`` blocks until it is this workflow's turn, calling
``on_position(ahead, running_workflow_id)`` whenever the number of compiles ahead changes
(0 = nothing ahead; it is about to run). ``release()`` hands the slot to the next in line.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable

class WaitCancelled(Exception):
    """The person left the line before it was their turn."""


_cond = threading.Condition()
_running: str | None = None
_waiting: deque[str] = deque()


def running() -> str | None:
    with _cond:
        return _running


def queued() -> list[str]:
    with _cond:
        return list(_waiting)


def acquire(workflow_id: str, on_position: Callable[[int, str | None], None] | None = None) -> None:
    global _running
    with _cond:
        _waiting.append(workflow_id)
        last = None
        while True:
            if workflow_id not in _waiting:
                raise WaitCancelled()
            ahead = list(_waiting).index(workflow_id) + (1 if _running is not None else 0)
            if on_position is not None and ahead != last:
                last = ahead
                on_position(ahead, _running)
            if _running is None and _waiting[0] == workflow_id:
                _waiting.popleft()
                _running = workflow_id
                return
            _cond.wait(timeout=1.0)


def release(workflow_id: str) -> None:
    global _running
    with _cond:
        if _running == workflow_id:
            _running = None
        _cond.notify_all()


def cancel_wait(workflow_id: str) -> None:
    """Leave the line without running (the person gave up on a queued compile)."""
    with _cond:
        try:
            _waiting.remove(workflow_id)
        except ValueError:
            pass
        _cond.notify_all()


def reset() -> None:
    """Tests only."""
    global _running
    with _cond:
        _running = None
        _waiting.clear()
        _cond.notify_all()
