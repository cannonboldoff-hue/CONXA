"""A1: prepare the recording video for Pegasus.

Stitches the per-tab videos (front tab over time), cuts long idle stretches,
shrinks to 720p/10fps, blurs typed values, and draws a ring at each click. All
of it is driven by the recorder's own event times and positions, never by a
model. Writes prepared.mp4 + splice_map.json + prepared.meta.json beside the
session; the SHA-256 of prepared.mp4 is the fingerprint that keys the Pegasus
result cache.

The plan (pieces, cuts, windows, filter graph) is pure and unit-tested; only
``prepare_video`` runs ffmpeg.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from conxa_compile.video import splice as splice_mod
from conxa_compile.video.ffmpeg import find_full_ffmpeg, probe_duration_ms

OUT_W, OUT_H, OUT_FPS = 1280, 720, 10
IDLE_GAP_MS = 5000      # a stretch with no event longer than this is cut
IDLE_KEEP_MS = 1000     # ...keeping this much at each end so a spinner is still seen
TAIL_MS = 3000          # video kept after the last event (its screen_after)
SWITCH_LEAD_MS = 500    # returning to a known tab: show it this long before its first event
MIN_TOTAL_MS = 4000     # shorter videos are padded by holding the last frame
BLUR_PAD_PX = 8
BLUR_TAIL_MS = 500      # keep blurring this long after the page changes
RING_SIZE_PX = 56
RING_BEFORE_MS, RING_AFTER_MS = 100, 500

TYPED_ACTIONS = {"type", "fill"}
CLICK_ACTIONS = {"click", "dblclick", "right_click", "set_checkbox", "set_radio"}


class NoVideoError(RuntimeError):
    """The session has no usable tab video (auth sessions record none)."""


@dataclass(frozen=True)
class Piece:
    tab_id: str
    wall_start: int
    wall_end: int


@dataclass(frozen=True)
class PreparedVideo:
    path: Path
    fingerprint: str
    splice_map: list[dict[str, Any]]
    duration_ms: int
    blur_all: bool
    reused: bool


# --- reading the session ------------------------------------------------------

def _tab_id(ev: dict[str, Any]) -> str:
    tab = ev.get("tab") if isinstance(ev.get("tab"), dict) else {}
    return str(tab.get("id") or "tab_0")


def load_timed_events(session_dir: Path, videos: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    events_path = session_dir / "events.jsonl"
    if not events_path.is_file():
        return []
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return timed_events(events, videos)


def timed_events(events: list[dict[str, Any]], videos: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Events that have a tab video and a timestamp, as {ev, tab, wall}, in wall-clock order."""
    out: list[dict[str, Any]] = []
    for ev in events:
        ts = (ev.get("visual") or {}).get("timestamp_ms")
        tab = _tab_id(ev)
        if ts is None or tab not in videos:
            continue
        out.append({"ev": ev, "tab": tab, "wall": int(videos[tab]["start_wall_ms"]) + int(ts)})
    out.sort(key=lambda e: e["wall"])
    return out


# --- plan: pieces, cuts, splice map ---------------------------------------------

def plan_pieces(evs: list[dict[str, Any]], videos: dict[str, dict[str, Any]], tail_ms: int = TAIL_MS) -> list[Piece]:
    """Front tab over time. The switch point for a tab seen before is just ahead of its first
    event; for a newly opened tab it is when its video starts (never before the previous event)."""
    cur_tab = evs[0]["tab"]
    cur_start = int(videos[cur_tab]["start_wall_ms"])
    seen = {cur_tab}
    prev_wall = evs[0]["wall"]
    out: list[Piece] = []
    for e in evs[1:]:
        if e["tab"] != cur_tab:
            if e["tab"] in seen:
                switch = max(prev_wall, e["wall"] - SWITCH_LEAD_MS)
            else:
                switch = min(e["wall"], max(prev_wall, int(videos[e["tab"]]["start_wall_ms"])))
            if switch > cur_start:
                out.append(Piece(cur_tab, cur_start, switch))
                cur_start = switch
            cur_tab = e["tab"]
            seen.add(cur_tab)
        prev_wall = e["wall"]
    end = evs[-1]["wall"] + tail_ms
    video_end = videos[cur_tab].get("end_wall_ms")
    if video_end:
        end = min(end, int(video_end))
    out.append(Piece(cur_tab, cur_start, max(end, cur_start + 1)))
    return out


def plan_cuts(anchors: list[int], gap_ms: int = IDLE_GAP_MS, keep_ms: int = IDLE_KEEP_MS) -> list[tuple[int, int]]:
    """Wall intervals to drop: inside any gap between consecutive anchors longer than gap_ms."""
    pts = sorted(anchors)
    return [(a + keep_ms, b - keep_ms) for a, b in zip(pts, pts[1:]) if b - a > gap_ms]


def subtract_cuts(pieces: list[Piece], cuts: list[tuple[int, int]]) -> list[Piece]:
    out: list[Piece] = []
    for p in pieces:
        cursor = p.wall_start
        for cs, ce in cuts:
            if ce <= cursor or cs >= p.wall_end:
                continue
            if cs > cursor:
                out.append(Piece(p.tab_id, cursor, cs))
            cursor = max(cursor, ce)
        if cursor < p.wall_end:
            out.append(Piece(p.tab_id, cursor, p.wall_end))
    return out


def build_splice_map(kept: list[Piece], cuts: list[tuple[int, int]], videos: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[tuple[int, str, Any]] = [(p.wall_start, "piece", p) for p in kept]
    items += [(cs, "cut", (cs, ce)) for cs, ce in cuts if kept and kept[0].wall_start < cs < kept[-1].wall_end]
    items.sort(key=lambda i: (i[0], i[1] == "piece"))
    out: list[dict[str, Any]] = []
    cursor = 0
    for _, kind, obj in items:
        if kind == "piece":
            start_wall = int(videos[obj.tab_id]["start_wall_ms"])
            length = obj.wall_end - obj.wall_start
            out.append({
                "kind": "piece", "tab_id": obj.tab_id,
                "out_start_ms": cursor, "out_end_ms": cursor + length,
                "src_offset_ms": obj.wall_start - start_wall, "start_wall_ms": start_wall,
                "wall_start_ms": obj.wall_start, "wall_end_ms": obj.wall_end,
            })
            cursor += length
        else:
            out.append({
                "kind": "cut", "tab_id": None, "out_start_ms": cursor, "out_end_ms": cursor,
                "cut_ms": obj[1] - obj[0], "wall_start_ms": obj[0], "wall_end_ms": obj[1],
            })
    return out


# --- plan: blur and click-ring windows -------------------------------------------

def _viewport(ev: dict[str, Any]) -> tuple[float, float]:
    raw = str((ev.get("visual") or {}).get("viewport") or "")
    try:
        w, h = raw.lower().split("x")[:2]
        return float(w), float(h)
    except ValueError:
        return float(OUT_W), float(OUT_H)


def _scroll(ev: dict[str, Any]) -> tuple[float, float]:
    raw = str((ev.get("visual") or {}).get("scroll_position") or "0,0")
    try:
        x, y = raw.split(",")[:2]
        return float(x), float(y)
    except ValueError:
        return 0.0, 0.0


def output_rect(ev: dict[str, Any], pad: int = 0) -> tuple[int, int, int, int] | None:
    """The event's bbox in the prepared video's pixels (page -> viewport -> letterboxed frame), clamped."""
    bbox = (ev.get("visual") or {}).get("bbox") or {}
    w, h = float(bbox.get("w") or 0), float(bbox.get("h") or 0)
    if w <= 0 or h <= 0:
        return None
    vw, vh = _viewport(ev)
    sx, sy = _scroll(ev)
    scale = min(OUT_W / vw, OUT_H / vh)
    off_x, off_y = (OUT_W - vw * scale) / 2, (OUT_H - vh * scale) / 2
    x0 = (float(bbox.get("x") or 0) - sx) * scale + off_x - pad
    y0 = (float(bbox.get("y") or 0) - sy) * scale + off_y - pad
    x1 = x0 + w * scale + 2 * pad
    y1 = y0 + h * scale + 2 * pad
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(OUT_W, x1), min(OUT_H, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def _is_sensitive(ev: dict[str, Any]) -> bool:
    if str((ev.get("semantic") or {}).get("input_type") or "").lower() == "password":
        return True
    return (ev.get("action") or {}).get("value") == "{{REDACTED}}"


def blur_windows(evs: list[dict[str, Any]], splice: list[dict[str, Any]], *, blur_all: bool, end_wall: int) -> list[dict[str, Any]]:
    """Blur boxes in prepared-video ms. A typed value is covered from the previous event (typing
    starts after it) until the page changes, plus a short tail. Passwords always; every typed
    field when blur_all."""
    out: list[dict[str, Any]] = []
    for i, e in enumerate(evs):
        ev = e["ev"]
        if str((ev.get("action") or {}).get("action") or "") not in TYPED_ACTIONS:
            continue
        if not (blur_all or _is_sensitive(ev)):
            continue
        rect = output_rect(ev, BLUR_PAD_PX)
        if rect is None:
            continue
        url = str((ev.get("page") or {}).get("url") or "")
        stop = end_wall
        for later in evs[i + 1:]:
            if str((later["ev"].get("page") or {}).get("url") or "") != url:
                stop = later["wall"] + BLUR_TAIL_MS
                break
        start = evs[i - 1]["wall"] if i else e["wall"] - 3000
        for s, t in splice_mod.wall_span_to_out(start, stop, splice, tab_id=e["tab"]):
            out.append({"rect": rect, "start_ms": s, "end_ms": t})
    return out


def ring_windows(evs: list[dict[str, Any]], splice: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in evs:
        ev = e["ev"]
        if str((ev.get("action") or {}).get("action") or "") not in CLICK_ACTIONS:
            continue
        rect = output_rect(ev)
        if rect is None:
            continue
        x, y, w, h = rect
        cx, cy = x + w // 2, y + h // 2
        # Shortcut: an outlined square, not a circle. Upgrade only if the trial shows Pegasus misses clicks.
        half = RING_SIZE_PX // 2
        box = (max(0, min(OUT_W - RING_SIZE_PX, cx - half)), max(0, min(OUT_H - RING_SIZE_PX, cy - half)))
        for s, t in splice_mod.wall_span_to_out(e["wall"] - RING_BEFORE_MS, e["wall"] + RING_AFTER_MS, splice, tab_id=e["tab"]):
            out.append({"x": box[0], "y": box[1], "start_ms": s, "end_ms": t})
    return out


# --- ffmpeg filter graph -------------------------------------------------------------

def _sec(ms: int | float) -> str:
    return f"{ms / 1000:.3f}"


def build_filter_graph(
    kept: list[Piece],
    input_index: dict[str, int],
    videos: dict[str, dict[str, Any]],
    blurs: list[dict[str, Any]],
    rings: list[dict[str, Any]],
    pad_ms: int,
) -> str:
    lines: list[str] = []
    for i, p in enumerate(kept):
        off = p.wall_start - int(videos[p.tab_id]["start_wall_ms"])
        lines.append(
            f"[{input_index[p.tab_id]}:v]trim=start={_sec(off)}:end={_sec(off + p.wall_end - p.wall_start)},"
            f"setpts=PTS-STARTPTS,fps={OUT_FPS},scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
            f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[p{i}]"
        )
    if len(kept) == 1:
        lines.append("[p0]null[s0]")
    else:
        lines.append("".join(f"[p{i}]" for i in range(len(kept))) + f"concat=n={len(kept)}:v=1:a=0[s0]")
    n = 0
    if pad_ms > 0:
        lines.append(f"[s0]tpad=stop_mode=clone:stop_duration={_sec(pad_ms)}[s1]")
        n = 1
    for k, b in enumerate(blurs):
        x, y, w, h = b["rect"]
        radius = max(1, min(12, min(w, h) // 4 - 1))  # chroma planes are half size: radius <= min(w,h)/4
        lines.append(f"[s{n}]split[ba{k}][bb{k}]")
        lines.append(f"[bb{k}]crop={w}:{h}:{x}:{y},boxblur=luma_radius={radius}:luma_power=3[bl{k}]")
        lines.append(
            f"[ba{k}][bl{k}]overlay={x}:{y}:enable='between(t,{_sec(b['start_ms'])},{_sec(b['end_ms'])})'[s{n + 1}]"
        )
        n += 1
    for r in rings:
        lines.append(
            f"[s{n}]drawbox=x={r['x']}:y={r['y']}:w={RING_SIZE_PX}:h={RING_SIZE_PX}:color=red@0.9:t=4:"
            f"enable='between(t,{_sec(r['start_ms'])},{_sec(r['end_ms'])})'[s{n + 1}]"
        )
        n += 1
    lines.append(f"[s{n}]null[outv]")
    return ";\n".join(lines)


# --- run -----------------------------------------------------------------------------

def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _events_signature(session_dir: Path) -> str:
    return _file_sha256(session_dir / "events.jsonl")


def prepare_video(
    session_dir: Path,
    *,
    blur_all: bool = False,
    ffmpeg: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> PreparedVideo:
    """Build (or reuse) prepared.mp4. Reuse requires the same events and the same blur setting."""
    session_dir = Path(session_dir)
    prepared = session_dir / "prepared.mp4"
    map_path = session_dir / "splice_map.json"
    meta_path = session_dir / "prepared.meta.json"
    signature = _events_signature(session_dir) if (session_dir / "events.jsonl").is_file() else ""

    if prepared.is_file() and map_path.is_file() and meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("blur_all") == blur_all and meta.get("events_signature") == signature:
            return PreparedVideo(prepared, meta["fingerprint"], json.loads(map_path.read_text(encoding="utf-8")),
                                 int(meta["duration_ms"]), blur_all, True)

    videos_path = session_dir / "videos.json"
    if not videos_path.is_file():
        raise NoVideoError("session has no videos.json")
    videos = json.loads(videos_path.read_text(encoding="utf-8"))
    evs = load_timed_events(session_dir, videos)
    if not evs:
        raise NoVideoError("session has no timed events with a tab video")

    ffmpeg = ffmpeg or find_full_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (install imageio-ffmpeg)")

    used = sorted({e["tab"] for e in evs})
    for tab in used:
        path = session_dir / str(videos[tab]["file"])
        if not path.is_file():
            raise NoVideoError(f"missing tab video {path.name}")
        dur = probe_duration_ms(ffmpeg, path)
        if dur:
            videos[tab]["end_wall_ms"] = int(videos[tab]["start_wall_ms"]) + dur

    pieces = plan_pieces(evs, videos)
    anchors = [e["wall"] for e in evs] + [pieces[0].wall_start, pieces[-1].wall_end]
    cuts = plan_cuts(anchors)
    kept = subtract_cuts(pieces, cuts)
    splice = build_splice_map(kept, cuts, videos)
    total_ms = splice[-1]["out_end_ms"] if splice else 0
    pad_ms = max(0, MIN_TOTAL_MS - total_ms)
    blurs = blur_windows(evs, splice, blur_all=blur_all, end_wall=pieces[-1].wall_end)
    rings = ring_windows(evs, splice)

    graph = build_filter_graph(kept, {t: i for i, t in enumerate(used)}, videos, blurs, rings, pad_ms)
    script = session_dir / "prepared.filter.txt"
    script.write_text(graph, encoding="utf-8")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for tab in used:
        cmd += ["-i", str(session_dir / str(videos[tab]["file"]))]
    cmd += ["-filter_complex_script", str(script), "-map", "[outv]", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(prepared)]
    # stdin=DEVNULL: ffmpeg would otherwise inherit the backend's JSON-RPC stdin pipe.
    result = run(cmd, capture_output=True, timeout=900, stdin=subprocess.DEVNULL)
    if result.returncode != 0 or not prepared.is_file() or prepared.stat().st_size == 0:
        detail = (result.stderr or b"").decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"ffmpeg failed preparing the recording video: {detail}")

    duration_ms = total_ms + pad_ms
    fingerprint = _file_sha256(prepared)
    map_path.write_text(json.dumps(splice), encoding="utf-8")
    meta_path.write_text(json.dumps({
        "fingerprint": fingerprint, "blur_all": blur_all, "events_signature": signature, "duration_ms": duration_ms,
    }), encoding="utf-8")
    return PreparedVideo(prepared, fingerprint, splice, duration_ms, blur_all, False)
