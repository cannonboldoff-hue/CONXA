"""Where Clarify's questions and the reviewer's answers live (BUILD-32 / BUILD-37).

``data/workflows/<workflow_id>/clarify.json`` — deliberately NOT inside the skill document. A
compile with no answers must stay byte-identical to a compile that never asked anything (the
BUILD-25 rule), so the questions, their status and the answers sit beside the skill, and an
answer only ever changes the skill through the existing gated mutations.

Shape::

    {"skill_id": "...", "version": 1,
     "questions": [{"id", "kind", "step_key", "title", "body", "choices": [{"id", "label", "detail"}],
                    "multi": bool, "status": "open" | "answered" | "skipped",
                    "answer": {...} | null, "receipt": "...", "applied": {...} | null}]}

Fail-silent on disk errors, like the thread store.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from conxa_core.config import settings

_lock = threading.Lock()


def _path(workflow_id: str) -> Path:
    return settings.data_dir / "workflows" / workflow_id / "clarify.json"


def load(workflow_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_path(workflow_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save(workflow_id: str, data: dict[str, Any]) -> None:
    path = _path(workflow_id)
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, path)
    except OSError:
        pass


def clear(workflow_id: str) -> None:
    try:
        _path(workflow_id).unlink(missing_ok=True)
    except OSError:
        pass


def summary(workflow_id: str) -> dict[str, Any] | None:
    """``{"open": n, "answered": m, "skipped": k, "total": t}``, or None before any questions exist."""
    data = load(workflow_id)
    if data is None:
        return None
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict)]
    return {
        "open": sum(1 for q in qs if q.get("status") == "open"),
        "answered": sum(1 for q in qs if q.get("status") == "answered"),
        "skipped": sum(1 for q in qs if q.get("status") == "skipped"),
        "total": len(qs),
    }


def get_question(workflow_id: str, question_id: str) -> dict[str, Any] | None:
    for q in (load(workflow_id) or {}).get("questions") or []:
        if isinstance(q, dict) and q.get("id") == question_id:
            return q
    return None


def update_question(workflow_id: str, question_id: str, **changes: Any) -> dict[str, Any] | None:
    data = load(workflow_id)
    if data is None:
        return None
    hit = None
    for q in data.get("questions") or []:
        if isinstance(q, dict) and q.get("id") == question_id:
            q.update(changes)
            hit = q
    if hit is not None:
        save(workflow_id, data)
    return hit


def next_open(workflow_id: str) -> dict[str, Any] | None:
    for q in (load(workflow_id) or {}).get("questions") or []:
        if isinstance(q, dict) and q.get("status") == "open":
            return q
    return None
