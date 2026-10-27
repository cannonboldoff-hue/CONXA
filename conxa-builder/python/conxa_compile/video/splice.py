"""Clock conversion through a splice map (written by prepare.py, read by the merge).

A splice map is an ordered list of entries on the prepared video's own clock:
  piece: {kind, tab_id, out_start_ms, out_end_ms, src_offset_ms, start_wall_ms, wall_start_ms, wall_end_ms}
  cut:   {kind, tab_id: None, out_start_ms, out_end_ms (== out_start_ms), cut_ms, wall_start_ms, wall_end_ms}
Pieces are sequential and disjoint in wall time, so a wall time needs no tab to find its piece.
"""

from __future__ import annotations

from typing import Any

SpliceMap = list[dict[str, Any]]


def pieces(splice: SpliceMap) -> list[dict[str, Any]]:
    return [e for e in splice if e.get("kind") == "piece"]


def wall_to_out_ms(wall_ms: float, splice: SpliceMap) -> int | None:
    """Prepared-video time of a wall-clock instant, or None when it falls in a cut or outside the video."""
    for p in pieces(splice):
        if p["wall_start_ms"] <= wall_ms <= p["wall_end_ms"]:
            return int(p["out_start_ms"] + (wall_ms - p["wall_start_ms"]))
    return None


def out_ms_to_wall(out_ms: float, splice: SpliceMap) -> tuple[str, int] | None:
    """(tab_id, wall-clock ms) of a prepared-video time, or None when it is past the end."""
    for p in pieces(splice):
        if p["out_start_ms"] <= out_ms <= p["out_end_ms"]:
            return p["tab_id"], int(p["wall_start_ms"] + (out_ms - p["out_start_ms"]))
    return None


def wall_span_to_out(wall_start: float, wall_end: float, splice: SpliceMap, tab_id: str | None = None) -> list[tuple[int, int]]:
    """Prepared-video intervals covered by a wall-clock interval (cuts drop out), optionally one tab only."""
    out: list[tuple[int, int]] = []
    for p in pieces(splice):
        if tab_id is not None and p["tab_id"] != tab_id:
            continue
        s, e = max(wall_start, p["wall_start_ms"]), min(wall_end, p["wall_end_ms"])
        if e > s:
            base = p["out_start_ms"] - p["wall_start_ms"]
            out.append((int(base + s), int(base + e)))
    return out
