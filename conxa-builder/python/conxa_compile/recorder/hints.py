"""Per-session recording hints (BUILD-38): what the person said while recording.

``data/sessions/<session_id>/hints.json``::

    {"varies": {"<sequence>": true | false}, "paused_spans": [...]}

A hint is the recorder's *author* telling us something only they know — "this typed value changes
each run" (``true``) or "this stays exactly as recorded" (``false``) — so Clarify doesn't have to
ask later. It is stored beside, never inside, ``events.jsonl``: the recording's shape is
untouched. At compile time :func:`apply_to_events` copies each hint onto its event's ``extras``
(``user_varies``); with no hints, no event changes and the compile is byte-identical.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from conxa_core.config import settings

_lock = threading.Lock()
_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


def hints_path(session_id: str) -> Path:
    if not _ID.match(session_id or ""):
        raise ValueError(f"Bad session id {session_id!r}")
    return settings.data_dir / "sessions" / session_id / "hints.json"


def load(session_id: str) -> dict[str, Any]:
    try:
        data = json.loads(hints_path(session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"varies": {}}
    if not isinstance(data, dict):
        return {"varies": {}}
    varies = data.get("varies")
    data["varies"] = varies if isinstance(varies, dict) else {}
    return data


def _save(session_id: str, data: dict[str, Any]) -> None:
    path = hints_path(session_id)
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
    except OSError:
        pass


def set_varies(session_id: str, sequence: int, varies: bool | None) -> dict[str, Any]:
    """Record the person's answer for one typed value; ``None`` clears it."""
    data = load(session_id)
    key = str(int(sequence))
    if varies is None:
        data["varies"].pop(key, None)
    else:
        data["varies"][key] = bool(varies)
    _save(session_id, data)
    return data


def add_paused_span(session_id: str, start_sequence: int) -> None:
    """Note where a pause began (the last sequence captured before it), for the record."""
    data = load(session_id)
    data.setdefault("paused_spans", []).append({"after_sequence": int(start_sequence)})
    _save(session_id, data)


def apply_to_events(session_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stamp ``extras["user_varies"]`` on each event the person answered for. Returns the same
    list; events with no hint are left exactly as they were."""
    varies = load(session_id).get("varies") or {}
    if not varies:
        return events
    for ev in events:
        extras = ev.get("extras")
        seq = (extras or {}).get("sequence") if isinstance(extras, dict) else None
        if seq is None:
            continue
        answer = varies.get(str(seq))
        if isinstance(answer, bool):
            ev["extras"] = {**extras, "user_varies": answer}
    return events
