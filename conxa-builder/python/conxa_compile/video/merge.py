"""M: line Track A (Pegasus rows) up with Track B (browser events) on one clock.

Plain code; it decides nothing about meaning. Both lists already run in time
order, so they are aligned the way a diff aligns two files: one pass that never
reorders either list. Pairing an event with a segment costs more the further the
event sits outside the segment (after 1 s of slack, because Pegasus rounds to whole
seconds) and more again when the action kinds differ. Leaving either side unpaired
has a fixed cost, and one segment may take several events (type, then Enter).
The browser always wins: an event Pegasus missed is kept and flagged.
"""

from __future__ import annotations

from typing import Any

from conxa_compile.compiler.outcome_fork import classify_dialog
from conxa_compile.video import splice as splice_mod
from conxa_compile.video.prepare import IDLE_KEEP_MS

SLACK_MS = 1000
KIND_MISMATCH_COST = 1.0
UNPAIRED_EVENT_COST = 2.0
UNPAIRED_SEGMENT_COST = 2.0
EXTRA_EVENT_COST = 0.2      # a second event on one segment: preferred only when it fits better elsewhere
_INF = float("inf")
_OFF_PAGE_KINDS = {"dialog", "download", "upload", "wait"}

# Browser action -> the Pegasus action_kind that describes it.
_KIND_OF_ACTION = {
    "click": "click", "dblclick": "click", "right_click": "click", "hover": "click", "set_checkbox": "click",
    "set_radio": "click", "drag_drop": "click", "type": "type", "fill": "type", "keyboard_shortcut": "type",
    "select": "select", "select_option": "select", "date_pick": "select", "scroll": "scroll",
    "navigate": "navigate", "manual_navigate": "navigate", "browser_back": "navigate", "browser_forward": "navigate",
    "upload": "upload", "upload_intent": "upload", "download_observed": "download", "dialog_appeared": "dialog",
    "dialog_accept": "dialog", "dialog_dismiss": "dialog", "wait": "wait",
}


def _split_rows(rows: list[dict[str, Any]], splice: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pegasus rows -> wall-clock sub-segments, one per piece they overlap (so a segment never
    straddles a tab switch or a cut)."""
    subs: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        start_ms, end_ms = row["start_s"] * 1000, row["end_s"] * 1000
        for p in splice_mod.pieces(splice):
            s, e = max(start_ms, p["out_start_ms"]), min(end_ms, p["out_end_ms"])
            if e > s:
                base = p["wall_start_ms"] - p["out_start_ms"]
                subs.append({"row": idx, "tab": p["tab_id"], "ws": s + base, "we": e + base})
    return subs


def _pair_cost(event: dict[str, Any], sub: dict[str, Any], rows: list[dict[str, Any]]) -> float:
    if event["tab"] != sub["tab"]:
        return _INF
    wall = event["wall"]
    outside = max(sub["ws"] - SLACK_MS - wall, wall - (sub["we"] + SLACK_MS), 0)
    cost = outside / 1000.0
    kind = _KIND_OF_ACTION.get(str((event["ev"].get("action") or {}).get("action") or ""))
    row = rows[sub["row"]]
    row_kind = row["action_kind"]
    # A wait or off-page moment (spinner, native dialog, download bar) never absorbs an on-page action.
    if (row_kind == "wait" or row.get("outside_page")) and kind not in _OFF_PAGE_KINDS:
        return _INF
    if kind is not None and row_kind not in (kind, "unknown"):
        cost += KIND_MISMATCH_COST
    return cost


def align(events: list[dict[str, Any]], subs: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Order-keeping min-cost alignment. Returns (event_index, sub_index) pairs."""
    n, m = len(events), len(subs)
    dp = [[_INF] * (m + 1) for _ in range(n + 1)]       # best cost with i events, j subs resolved
    mm = [[_INF] * (m + 1) for _ in range(n + 1)]       # ...and event i paired with sub j
    back: list[list[str | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    mback: list[list[str | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best, how = _INF, None
            if i and dp[i - 1][j] + UNPAIRED_EVENT_COST < best:
                best, how = dp[i - 1][j] + UNPAIRED_EVENT_COST, "e"
            if j and dp[i][j - 1] + UNPAIRED_SEGMENT_COST < best:
                best, how = dp[i][j - 1] + UNPAIRED_SEGMENT_COST, "s"
            if i and j:
                cost = _pair_cost(events[i - 1], subs[j - 1], rows)
                if cost < _INF:
                    new, ext = dp[i - 1][j - 1] + cost, mm[i - 1][j] + cost + EXTRA_EVENT_COST
                    mm[i][j], mback[i][j] = (new, "new") if new <= ext else (ext, "ext")
                    if mm[i][j] < best:
                        best, how = mm[i][j], "m"
            dp[i][j], back[i][j] = best, how
    pairs: list[tuple[int, int]] = []
    i, j = n, m
    while i or j:
        how = back[i][j]
        if how == "e":
            i -= 1
        elif how == "s":
            j -= 1
        else:
            while True:
                pairs.append((i - 1, j - 1))
                if mback[i][j] == "new":
                    i, j = i - 1, j - 1
                    break
                i -= 1
    pairs.reverse()
    return pairs


# --- building rows -----------------------------------------------------------------

def _event_text(ev: dict[str, Any]) -> str:
    t = ev.get("target") or {}
    for key in ("aria_label", "label_text", "inner_text", "placeholder", "name", "title", "alt"):
        v = str(t.get(key) or "").strip()
        if v:
            return v[:80]
    return ""


def _segment_text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(k) or "") for k in ("visible_target", "intent", "purpose", "screen_before", "screen_after")).lower()


def _value_seen_in_both(ev: dict[str, Any], row: dict[str, Any] | None) -> bool:
    if row is None:
        return False
    value = str((ev.get("action") or {}).get("value") or "").strip()
    if len(value) < 3 or value == "{{REDACTED}}":
        value = _event_text(ev)
    return len(value) >= 3 and value.lower() in _segment_text(row)


def merge_timeline(timed: list[dict[str, Any]], rows: list[dict[str, Any]], splice: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merged timeline rows in time order: events (matched or Pegasus-missed) and off-page observations."""
    timed = sorted(timed, key=lambda e: e["wall"])
    subs = _split_rows(rows, splice)
    pairs = align(timed, subs, rows)
    row_of_event = {ei: subs[sj]["row"] for ei, sj in pairs}
    matched_rows = set(row_of_event.values())

    items: list[tuple[int, dict[str, Any]]] = []
    for i, e in enumerate(timed):
        ev = e["ev"]
        action = str((ev.get("action") or {}).get("action") or "")
        row = rows[row_of_event[i]] if i in row_of_event else None
        flags = []
        if row is None:
            flags.append("pegasus_missed")
        if _value_seen_in_both(ev, row):
            flags.append("value_seen_in_both")
        items.append((e["wall"], {
            "kind": "event",
            "event_id": (ev.get("extras") or {}).get("sequence"),
            "tab": e["tab"],
            "action": action,
            "target": _event_text(ev),
            "role": str((ev.get("semantic") or {}).get("role") or (ev.get("target") or {}).get("role") or ""),
            "value": (ev.get("action") or {}).get("value"),
            "url": str((ev.get("page") or {}).get("url") or ""),
            "t_ms": e["wall"],
            "segment": row,
            "flags": flags,
            # decision | dismissable | consent | form: the recorder saw this action inside a popup.
            **({"dialog": classify_dialog(ev["dialog_context"])} if isinstance(ev.get("dialog_context"), dict) else {}),
        }))
    seen: set[int] = set()
    for sub in subs:
        if sub["row"] in matched_rows or sub["row"] in seen:
            continue
        seen.add(sub["row"])
        row = rows[sub["row"]]
        items.append((sub["ws"], {
            "kind": "observation", "type": "off_page" if row.get("outside_page") else "unmatched_segment",
            "tab": sub["tab"], "t_ms": sub["ws"], "segment": row,
        }))
    for cut in (c for c in splice if c.get("kind") == "cut"):
        items.append((cut["wall_start_ms"], {
            "kind": "observation", "type": "idle_wait", "t_ms": cut["wall_start_ms"],
            "seconds": round((cut["cut_ms"] + 2 * IDLE_KEEP_MS) / 1000, 1),
        }))
    items.sort(key=lambda it: it[0])
    out = []
    for n, (_, item) in enumerate(items, start=1):
        item["row_ref"] = f"r{n}"
        out.append(item)
    return out
