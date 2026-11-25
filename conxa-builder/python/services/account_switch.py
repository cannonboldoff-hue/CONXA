"""Per-account local data folders (AUTH-26).

Build Studio keeps each signed-in account's workflows, groups, chats, saved sign-ins,
test sandbox and built bundles under ``<studio home>/accounts/<account key>/``. The backend
cannot move itself: it writes ``active_account.json`` and the Electron main process restarts
it with ``SKILL_DATA_DIR`` / ``CONXA_ACCOUNT_HOME`` pointing at that folder.

Only active when Electron set ``CONXA_ACCOUNT_HOME`` (tests and scripts keep the old layout).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from conxa_core.config import state_base_dir
from conxa_core.workspace import SIGNED_OUT_KEY, account_key

POINTER = "active_account.json"
LEGACY_MARKER = ".legacy_migrated.json"
# Folders that lived directly under the studio home before per-account folders.
_LEGACY_DIRS = ("data", "sandbox", "output")


def enabled() -> bool:
    return bool(os.environ.get("CONXA_ACCOUNT_HOME", "").strip())


def _has_files(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def migrate_legacy(key: str) -> bool:
    """Give the pre-update shared folders to the first account that signs in (moved, never deleted).

    Skipped when already done, when there is nothing to move, or when the account already has data.
    Returns True when something was moved.
    """
    base = state_base_dir()
    marker = base / LEGACY_MARKER
    if key == SIGNED_OUT_KEY or marker.exists():
        return False
    legacy = {name: base / name for name in _LEGACY_DIRS if _has_files(base / name)}
    if not legacy:
        return False
    target = base / "accounts" / key
    if _has_files(target / "data"):
        return False  # that account already has its own work; leave the old folder for a person to look at
    try:
        for name, src in legacy.items():
            dest = target / name
            if dest.is_dir() and not any(dest.iterdir()):
                dest.rmdir()
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
        marker.write_text(json.dumps({"account_key": key}), encoding="utf-8")
    except OSError as exc:
        print(f"[account] could not move the earlier local work: {exc}", file=sys.stderr)
        return False
    return True


def sync_account(identity: dict[str, Any] | None) -> str | None:
    """Record which account the next backend start should use. Returns the key, or None when disabled."""
    if not enabled():
        return None
    key = account_key(identity)
    migrate_legacy(key)
    pointer = state_base_dir() / POINTER
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"account_key": key}), encoding="utf-8")
    return key
