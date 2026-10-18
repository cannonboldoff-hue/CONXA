"""Append-only conversation threads (BUILD-37).

One thread per workflow — ``data/workflows/<workflow_id>/thread.jsonl`` — plus workspace-level
general chats under ``data/chats/<chat_id>.jsonl`` (and a small ``index.json`` for the sidebar's
Chats list). A workflow's thread lives beside the workflow, not under the skill, because the
workflow exists from the moment it is created while its ``skill_id`` is born at compile and
changes on every re-record.

Same fail-silent contract as ``editor/copilot_sessions.py``: a disk hiccup must never stop a
person from continuing a conversation, so writes swallow ``OSError`` and reads degrade to empty.

Item kinds: ``user_message``, ``assistant_message``, ``event`` (the system's one-line voice),
``card`` (stateful — see below), ``summary`` (a rolling summary of older turns) and
``import_marker``.

Cards are never edited in place. A card is *revised* by appending a new line with the same
``card_id`` and a higher ``revision``; :func:`fold` shows the latest revision at the position
of the first, so history is never rewritten and a card that is still waiting on the backend
simply has an older revision as its newest.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from conxa_core.config import settings

_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_write_lock = threading.Lock()


class ThreadIdError(ValueError):
    """A thread id that is malformed or would escape the data directory."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_thread_id(thread_id: str) -> tuple[str, str]:
    """``"wf:<workflow_id>"`` or ``"chat:<chat_id>"`` → ``(kind, id)``."""
    kind, _, ident = str(thread_id or "").partition(":")
    if kind not in ("wf", "chat") or not _ID_RE.match(ident) or ident in (".", ".."):
        raise ThreadIdError(f"Bad thread id {thread_id!r}")
    return kind, ident


def thread_path(thread_id: str) -> Path:
    kind, ident = parse_thread_id(thread_id)
    if kind == "wf":
        return settings.data_dir / "workflows" / ident / "thread.jsonl"
    return settings.data_dir / "chats" / f"{ident}.jsonl"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ── low-level read / write ──────────────────────────────────────────────────────────────────


def _append_line(thread_id: str, item: dict[str, Any]) -> None:
    path = thread_path(thread_id)
    try:
        with _write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def read_raw(thread_id: str) -> list[dict[str, Any]]:
    """Every line, oldest first. Unparseable lines are skipped, never fatal."""
    path = thread_path(thread_id)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def append(thread_id: str, kind: str, **fields: Any) -> dict[str, Any]:
    """Append one non-card item and return it."""
    item = {"id": new_id("it"), "ts": _now(), "kind": kind, **fields}
    _append_line(thread_id, item)
    return item


# ── cards ───────────────────────────────────────────────────────────────────────────────────


def fold(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse card revisions: each card appears once, where it first appeared, as its latest
    revision. ``created_ts`` is when it first appeared, ``ts`` stays that moment so the thread
    keeps its order, ``updated_ts`` is when the newest revision landed."""
    first_pos: dict[str, int] = {}
    latest: dict[str, dict[str, Any]] = {}
    first_ts: dict[str, str] = {}
    order: list[Any] = []
    for item in raw:
        cid = item.get("card_id") if item.get("kind") == "card" else None
        if not cid:
            order.append(item)
            continue
        if cid not in first_pos:
            first_pos[cid] = len(order)
            first_ts[cid] = str(item.get("ts") or "")
            order.append(cid)
        latest[cid] = item
    out: list[dict[str, Any]] = []
    for entry in order:
        if isinstance(entry, str):
            card = dict(latest[entry])
            card["created_ts"] = first_ts[entry]
            card["updated_ts"] = card.get("ts")
            card["ts"] = first_ts[entry]
            out.append(card)
        else:
            out.append(entry)
    return out


def append_card(
    thread_id: str,
    card_type: str,
    state: str,
    *,
    refs: dict[str, Any] | None = None,
    card_id: str | None = None,
    **body: Any,
) -> dict[str, Any]:
    item = {
        "id": new_id("it"),
        "ts": _now(),
        "kind": "card",
        "card_id": card_id or new_id("cd"),
        "revision": 1,
        "card_type": card_type,
        "state": state,
        "refs": dict(refs or {}),
        **body,
    }
    _append_line(thread_id, item)
    return item


def get_card(thread_id: str, card_id: str) -> dict[str, Any] | None:
    for item in reversed(fold(read_raw(thread_id))):
        if item.get("kind") == "card" and item.get("card_id") == card_id:
            return item
    return None


def revise_card(
    thread_id: str,
    card_id: str,
    *,
    state: str | None = None,
    refs: dict[str, Any] | None = None,
    **changes: Any,
) -> dict[str, Any] | None:
    """Append a new revision of ``card_id`` carrying everything the previous one had, plus the
    changes. Returns the new revision, or None when there is no such card."""
    current = get_card(thread_id, card_id)
    if current is None:
        return None
    item = {k: v for k, v in current.items() if k not in ("created_ts", "updated_ts")}
    item["id"] = new_id("it")
    item["ts"] = _now()
    item["revision"] = int(current.get("revision") or 1) + 1
    if state is not None:
        item["state"] = state
    if refs:
        item["refs"] = {**(current.get("refs") or {}), **refs}
    item.update(changes)
    _append_line(thread_id, item)
    folded = dict(item)
    folded["created_ts"] = current.get("created_ts")
    folded["updated_ts"] = item["ts"]
    folded["ts"] = current.get("created_ts") or item["ts"]
    return folded


# ── reading a page ──────────────────────────────────────────────────────────────────────────


def load(thread_id: str, *, before: int | None = None, limit: int = 40) -> dict[str, Any]:
    """Newest-first paging over the folded thread. ``before`` is an index into the folded list
    (exclusive); the page itself comes back oldest-first so it renders top to bottom.

    The whole file is parsed — card revisions can land far from the card's first line, and a
    workflow thread is hundreds of lines, not millions — but only a page is sent to the renderer.
    """
    items = fold(read_raw(thread_id))
    total = len(items)
    end = total if before is None else max(0, min(int(before), total))
    start = max(0, end - max(1, int(limit)))
    return {
        "items": items[start:end],
        "start": start,
        "total": total,
        "has_more": start > 0,
        "cursor": start if start > 0 else None,
    }


def latest_summary(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(items):
        if item.get("kind") == "summary":
            return item
    return None


def open_cards(items: list[dict[str, Any]], card_type: str | None = None) -> list[dict[str, Any]]:
    """Cards still waiting on a person (state ``open``), optionally of one type."""
    return [
        i for i in items
        if i.get("kind") == "card" and i.get("state") == "open" and (card_type is None or i.get("card_type") == card_type)
    ]


# ── general chats index ─────────────────────────────────────────────────────────────────────


def _chats_dir() -> Path:
    return settings.data_dir / "chats"


def _index_path() -> Path:
    return _chats_dir() / "index.json"


def list_chats() -> list[dict[str, Any]]:
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    chats = data.get("chats") if isinstance(data, dict) else None
    rows = [c for c in chats if isinstance(c, dict) and c.get("id")] if isinstance(chats, list) else []
    return sorted(rows, key=lambda c: str(c.get("updated_at") or ""), reverse=True)


def _write_index(chats: list[dict[str, Any]]) -> None:
    path = _index_path()
    try:
        with _write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"chats": chats}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
    except OSError:
        pass


def create_chat(title: str = "New chat") -> dict[str, Any]:
    now = _now()
    chat = {"id": new_id("chat"), "title": (title or "New chat").strip()[:80] or "New chat", "created_at": now, "updated_at": now}
    _write_index([chat] + [c for c in list_chats()])
    return chat


def touch_chat(chat_id: str, *, title: str | None = None) -> None:
    chats = list_chats()
    for c in chats:
        if c.get("id") == chat_id:
            c["updated_at"] = _now()
            if title:
                c["title"] = title.strip()[:80]
    _write_index(chats)


def rename_chat(chat_id: str, title: str) -> dict[str, Any] | None:
    title = title.strip()[:80]
    if not title:
        return None
    chats = list_chats()
    hit = None
    for c in chats:
        if c.get("id") == chat_id:
            c["title"] = title
            hit = c
    if hit:
        _write_index(chats)
    return hit


def delete_chat(chat_id: str) -> bool:
    parse_thread_id(f"chat:{chat_id}")
    chats = list_chats()
    kept = [c for c in chats if c.get("id") != chat_id]
    if len(kept) == len(chats):
        return False
    _write_index(kept)
    try:
        thread_path(f"chat:{chat_id}").unlink(missing_ok=True)
    except OSError:
        pass
    return True


# ── one-time import of the Human Review copilot's archived conversations ─────────────────────


def import_copilot_session(workflow_id: str, skill_id: str | None) -> int:
    """Read-only import of ``copilot_sessions.jsonl``'s last transcript into the workflow's
    thread, once per skill, so a reviewer's earlier copilot history is not lost when the copilot
    becomes this conversation. Returns how many messages were imported."""
    if not skill_id:
        return 0
    thread_id = f"wf:{workflow_id}"
    for item in read_raw(thread_id):
        if item.get("kind") == "import_marker" and item.get("source_skill_id") == skill_id:
            return 0
    from conxa_compile.editor.copilot_sessions import load_last_session

    messages = [m for m in load_last_session(skill_id) if isinstance(m, dict)]
    rows: list[tuple[str, str]] = []
    for m in messages:
        text = str(m.get("text") or m.get("content") or "").strip()
        if not text:
            continue
        role = "user_message" if str(m.get("role") or "user") == "user" else "assistant_message"
        rows.append((role, text))
    append(thread_id, "import_marker", source="copilot_sessions", source_skill_id=skill_id, count=len(rows))
    for role, text in rows:
        append(thread_id, role, text=text, imported=True)
    return len(rows)
