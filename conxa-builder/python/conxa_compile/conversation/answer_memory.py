"""What this person usually answers, per app, so the next workflow on the same app asks less.

Only the shape of a decision is kept: the app's host, the question kind, a scrubbed label and the choice id.
Never a value, a page text or a file name (``app_graph.scrub`` is the only door text goes through). It only
moves how sure scoring is that the compiler's default is right (``scoring.p_wrong``); it never answers a
question, and a safety-floor question (a decision popup, a step that cannot be undone) ignores it entirely.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from conxa_compile.app_graph.extract import walk_doc
from conxa_compile.app_graph.scrub import scrub_label
from conxa_core.config import settings

# Kinds whose answer says something general about the app. Not the safety kinds, not value-specific kinds.
REMEMBERED = frozenset({"popup", "skip_when_empty", "missing_item", "output", "done_when", "relative_date", "dropped_value"})
_lock = threading.Lock()


def _path():
    return settings.data_dir / "answer_memory.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def host_of(doc: dict[str, Any]) -> str:
    """The first app the workflow touched ("" when none is known)."""
    try:
        return next((w.host for w in walk_doc(doc) if w.host), "")
    except Exception:  # noqa: BLE001 -- memory is a convenience
        return ""


def key_of(host: str, q: dict[str, Any]) -> str | None:
    kind = str(q.get("kind") or "")
    if not host or kind not in REMEMBERED:
        return None
    return f"{host}|{kind}|{scrub_label(q.get('step_name') or '') or ''}"


def remember(host: str, q: dict[str, Any], answer: dict[str, Any]) -> None:
    """Count a single-choice answer. Never raises."""
    key, choice = key_of(host, q), answer.get("choice")
    if key is None or not choice:
        return
    try:
        with _lock:
            data = _load()
            counts = data.setdefault(key, {})
            counts[str(choice)] = int(counts.get(str(choice), 0)) + 1
            _path().parent.mkdir(parents=True, exist_ok=True)
            _path().write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def recall(host: str, q: dict[str, Any]) -> dict[str, int]:
    """``{choice id: times}`` for this kind of question on this app ({} when none)."""
    key = key_of(host, q)
    counts = _load().get(key) if key else None
    return {str(c): int(n) for c, n in counts.items()} if isinstance(counts, dict) else {}
