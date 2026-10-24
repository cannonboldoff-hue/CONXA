"""Tiny observer list for skill writes.

`json_store.write_skill` / `delete_skill` are the one place every compile, every editor mutation and
every undo passes through. Code that wants to react (the app map) lives above this package, so it
registers here instead of being imported from below. A hook that raises is reported as a warning
and never fails the write it observes.
"""

from __future__ import annotations

import warnings
from typing import Any, Callable

_on_written: list[Callable[[str, dict[str, Any]], None]] = []
_on_deleted: list[Callable[[str], None]] = []


def on_skill_written(fn: Callable[[str, dict[str, Any]], None]) -> None:
    if fn not in _on_written:
        _on_written.append(fn)


def on_skill_deleted(fn: Callable[[str], None]) -> None:
    if fn not in _on_deleted:
        _on_deleted.append(fn)


def clear_hooks() -> None:
    """Test isolation only."""
    _on_written.clear()
    _on_deleted.clear()


def notify_skill_written(skill_id: str, document: dict[str, Any]) -> None:
    for fn in list(_on_written):
        try:
            fn(skill_id, document)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"skill-written hook {getattr(fn, '__name__', fn)!r} failed for {skill_id!r}: {type(exc).__name__}: {exc}", stacklevel=2)


def notify_skill_deleted(skill_id: str) -> None:
    for fn in list(_on_deleted):
        try:
            fn(skill_id)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"skill-deleted hook {getattr(fn, '__name__', fn)!r} failed for {skill_id!r}: {type(exc).__name__}: {exc}", stacklevel=2)
