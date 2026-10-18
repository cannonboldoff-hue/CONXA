"""Local Studio settings the conversation reads (BUILD-40).

``data/studio_settings.json`` — per-computer, never synced. Today: ``autofix_enabled`` (default
on), the switch for the auto-fix loop that applies one gated change after a failed test and
re-tests without a click per change. Fail-silent like the other small local stores.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from conxa_core.config import settings

_lock = threading.Lock()
DEFAULTS: dict[str, Any] = {"autofix_enabled": True}


def _path():
    return settings.data_dir / "studio_settings.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {**DEFAULTS, **{k: v for k, v in (data if isinstance(data, dict) else {}).items() if k in DEFAULTS}}


def update(**changes: Any) -> dict[str, Any]:
    cur = load()
    for k, v in changes.items():
        if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
            cur[k] = v
    path = _path()
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
            os.replace(tmp, path)
    except OSError:
        pass
    return cur
