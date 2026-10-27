"""A3: Pegasus segments -> video rows (plain code, no model).

Row: {start_s, end_s, action_kind, visible_target, intent, purpose, screen_before,
screen_after, outside_page}. Times are seconds on the prepared video's clock.
Malformed segments are dropped. An unrecognised action_kind becomes "wait" for an
off-page segment and "unknown" otherwise, so the merge's kind-mismatch penalty
applies instead of a wrong match.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ACTION_KINDS = {"click", "type", "select", "navigate", "scroll", "upload", "download", "dialog", "wait"}
_TEXT_FIELDS = ("visible_target", "intent", "purpose", "screen_before", "screen_after")
CACHE_FILE = "video_observations.json"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def rows_from_segments(segments: list[Any], duration_s: float | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        start, end = _num(seg.get("start_time")), _num(seg.get("end_time"))
        meta = seg.get("metadata") if isinstance(seg.get("metadata"), dict) else {}
        if start is None or end is None or end < start:
            continue
        if duration_s is not None:
            end = min(end, duration_s)
            if start >= end:
                continue
        kind = str(meta.get("action_kind") or "").strip().lower()
        off_page = meta.get("outside_page") is True
        row: dict[str, Any] = {
            "start_s": start,
            "end_s": end,
            "action_kind": kind if kind in ACTION_KINDS else ("wait" if off_page else "unknown"),
            "outside_page": off_page,
        }
        for name in _TEXT_FIELDS:
            row[name] = str(meta.get(name) or "").strip()
        rows.append(row)
    rows.sort(key=lambda r: (r["start_s"], r["end_s"]))
    return rows


# --- result cache, keyed by the prepared video's fingerprint ---------------------

def read_cache(session_dir: Path, fingerprint: str) -> dict[str, Any] | None:
    path = Path(session_dir) / CACHE_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("fingerprint") == fingerprint else None


def write_cache(session_dir: Path, fingerprint: str, result: dict[str, Any]) -> None:
    payload = {
        "fingerprint": fingerprint,
        "segments": result.get("segments") or [],
        "duration_s": result.get("duration_s"),
        "truncated": bool(result.get("truncated")),
    }
    (Path(session_dir) / CACHE_FILE).write_text(json.dumps(payload), encoding="utf-8")
