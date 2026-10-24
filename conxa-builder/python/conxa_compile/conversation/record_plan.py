"""Pre-record plan (BUILD-49): a short, advisory checklist of what the person will need to do,
written from their own task description before they start recording.

Advisory only: nothing here reaches the recorder or the compiler, and a missing or failed plan just
means no card. The model writes sentences for a person to read; code keeps them short and plain.
"""

from __future__ import annotations

import json
from typing import Any, Callable

MAX_STEPS = 8
MAX_STEP_CHARS = 100
Caller = Callable[[str, dict[str, Any], int], "dict[str, Any] | None"]


def clean(raw: Any) -> list[str]:
    """The model's answer held to what a card can show: 1-8 non-empty one-line strings."""
    items = raw.get("steps") if isinstance(raw, dict) else None
    out: list[str] = []
    for item in items if isinstance(items, list) else []:
        text = " ".join(str(item or "").split())[:MAX_STEP_CHARS]
        if text and text.lower() not in (o.lower() for o in out):
            out.append(text)
    return out[:MAX_STEPS]


def generate(task_description: str, start_url: str = "", *, call: Caller | None = None, timeout_ms: int = 20_000) -> list[str]:
    """Never raises. Empty list without a description or when the model gave nothing usable."""
    task = (task_description or "").strip()
    if not task:
        return []
    try:
        if call is None:
            from conxa_compile.llm.client import call_llm as call  # noqa: PLC0415
        raw = call("record_plan", {"user_text": json.dumps({"task": task[:300], "start_url": (start_url or "")[:200]})}, timeout_ms)
    except Exception:  # noqa: BLE001 -- advisory: a failed model call must never get in the way of recording
        return []
    steps = clean(raw)
    return steps if len(steps) >= 2 else []
