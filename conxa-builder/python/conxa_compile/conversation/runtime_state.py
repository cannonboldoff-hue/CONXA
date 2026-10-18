"""In-memory, per-workflow flags for things that are happening *right now* (BUILD-37).

The lifecycle (``lifecycle.py``) is derived from data that already persists — the workflow,
its skill, the clarify answers. What it cannot read from disk is "a compile is running" or "a
test is in flight": those die with the process on purpose (a half-finished compile after a
crash is not a state to resume into). Command handlers set and clear these flags; nothing else
does, so the lifecycle only ever changes because a backend command ran, never because a model
said something.

Flags: ``recording``, ``recording_paused``, ``signing_in``, ``compiling``, ``queued`` (an int
position, 0 = none), ``testing``, ``autofix_attempt`` (0 = not running), ``out_of_credits``,
``compile_error`` (a short reason), ``sign_in_expired`` (a saved sign-in stopped working — before
recording, or a test paused on it).
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {}


def set_flags(workflow_id: str, **flags: Any) -> dict[str, Any]:
    with _lock:
        cur = _state.setdefault(workflow_id, {})
        for k, v in flags.items():
            if v in (None, False, 0, ""):
                cur.pop(k, None)
            else:
                cur[k] = v
        if not cur:
            _state.pop(workflow_id, None)
        return dict(cur)


def get_flags(workflow_id: str) -> dict[str, Any]:
    with _lock:
        return dict(_state.get(workflow_id, {}))


def clear(workflow_id: str) -> None:
    with _lock:
        _state.pop(workflow_id, None)


def clear_all() -> None:
    """Tests only."""
    with _lock:
        _state.clear()
