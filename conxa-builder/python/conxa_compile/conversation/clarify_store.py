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
from typing import Any, Callable

from conxa_core.config import settings

# Re-entrant: ``mutate`` holds it across load → edit → save, and ``save`` takes it again.
_lock = threading.RLock()


def _path(workflow_id: str) -> Path:
    return settings.data_dir / "workflows" / workflow_id / "clarify.json"


def load(workflow_id: str) -> dict[str, Any] | None:
    # Under the lock too: on Windows a file that another thread has open can't be replaced or
    # removed, and ``save``/``clear`` swallow that error -- a write would silently be lost.
    with _lock:
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


def mutate(workflow_id: str, fn: Callable[[dict[str, Any]], Any]) -> dict[str, Any] | None:
    """Load, let ``fn`` edit the data in place, save — all under one lock, so two writers (an answer
    landing while a card id is recorded, or a stale-mark) can't overwrite each other's change.
    None when there is no question file."""
    with _lock:
        data = load(workflow_id)
        if data is None:
            return None
        fn(data)
        save(workflow_id, data)
        return data


def clear(workflow_id: str) -> None:
    # Under the same lock as ``mutate``: otherwise a writer that already loaded the file saves it
    # straight back after this removes it.
    with _lock:
        try:
            _path(workflow_id).unlink(missing_ok=True)
        except OSError:
            pass


def summary(workflow_id: str) -> dict[str, Any] | None:
    """``{"open": n, "queued": q, "answered": m, "skipped": k, "total": t}``, or None before any
    questions exist. Testing waits for every open question, whatever its kind."""
    data = load(workflow_id)
    if data is None:
        return None
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict)]
    return {
        "queued": sum(1 for q in qs if q.get("status") == "queued"),
        # An answer being applied is still open as far as the lifecycle goes: Test must not slip
        # through the gap between "applying" and "answered".
        "open": sum(1 for q in qs if q.get("status") in ("open", "applying")),
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
    hit: list[dict[str, Any]] = []

    def edit(data: dict[str, Any]) -> None:
        for q in data.get("questions") or []:
            if isinstance(q, dict) and q.get("id") == question_id:
                q.update(changes)
                hit.append(q)

    mutate(workflow_id, edit)
    return hit[-1] if hit else None


def answer_question(workflow_id: str, question_id: str, *, answer: Any, receipt: str, added_steps: int) -> None:
    """Mark a question answered and add the steps its answer added, in one locked write."""
    def edit(data: dict[str, Any]) -> None:
        for q in data.get("questions") or []:
            if isinstance(q, dict) and q.get("id") == question_id:
                q.update(status="answered", answer=answer, receipt=receipt, base_version=None, applying_since=None)
        data["added_steps"] = int(data.get("added_steps") or 0) + added_steps

    mutate(workflow_id, edit)


def mark_stale(workflow_id: str, live_keys: set[str]) -> int:
    """Flag answered/skipped questions whose steps no longer exist (a segment was re-recorded over
    them) as ``stale``; answers about steps that survive are kept as they are. Returns how many."""
    count = [0]

    def edit(data: dict[str, Any]) -> None:
        for q in data.get("questions") or []:
            if isinstance(q, dict) and q.get("step_keys") and not set(q["step_keys"]) <= live_keys and not q.get("stale"):
                q["stale"] = True
                if q.get("status") == "open":
                    q["status"] = "skipped"      # nothing left to ask about
                    q["receipt"] = "The steps this was about were re-recorded"
                count[0] += 1

    mutate(workflow_id, edit)
    return count[0]


def next_open(workflow_id: str) -> dict[str, Any] | None:
    for q in (load(workflow_id) or {}).get("questions") or []:
        if isinstance(q, dict) and q.get("status") == "open":
            return q
    return None
