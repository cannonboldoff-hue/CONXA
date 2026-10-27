"""Cut ONE frame from a recording's tab video, on demand.

Studio no longer cuts five frames per event at compile time. The only thing that still needs a
picture is Human Edit's "draw a new region" fix, so the frame is cut here, from the local video,
when a person actually uses it. Idempotent: a frame already on disk is returned as it is.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from conxa_compile.video.ffmpeg import find_ffmpeg

BEFORE_MS = 250     # just before the action, so the page is shown as it was when the person acted


def cut_frame(video_path: Path, out_path: Path, timestamp_ms: int, *, ffmpeg: str | None = None) -> None:
    """Write one PNG. Raises RuntimeError when ffmpeg is missing, fails or writes nothing."""
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-ss", f"{max(0, timestamp_ms) / 1000.0:.3f}", "-i", str(video_path),
           "-frames:v", "1", "-q:v", "2", "-loglevel", "error", str(out_path)]
    try:
        # stdin=DEVNULL: ffmpeg would otherwise inherit the backend's JSON-RPC stdin pipe.
        result = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"ffmpeg invocation failed: {exc!s}") from exc
    if result.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg could not cut a frame: {result.stderr.decode('utf-8', errors='replace')[:300]}")


def event_frame(session_dir: Path, event: dict[str, Any], *, ffmpeg: str | None = None) -> str | None:
    """The frame just before ``event`` happened, as a path relative to the session folder
    (``frames/evt_<sequence>_before_near.png``); cut once, reused after. None when the event has
    no timestamp or its tab has no video (an old recording, or an auth session)."""
    session_dir = Path(session_dir)
    visual = event.get("visual") or {}
    ts = visual.get("timestamp_ms")
    sequence = (event.get("extras") or {}).get("sequence")
    tab = str((event.get("tab") or {}).get("id") or "tab_0")
    if ts is None or sequence is None:
        return None
    try:
        videos = json.loads((session_dir / "videos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        videos = {}
    entry = videos.get(tab)
    video = session_dir / str(entry["file"]) if isinstance(entry, dict) and entry.get("file") else None
    if video is None and tab == "tab_0":
        video = session_dir / "recording.webm"      # a session from before videos.json
    if video is None or not video.is_file():
        return None
    rel = f"frames/evt_{int(sequence):04d}_before_near.png"
    out = session_dir / rel
    if not out.is_file():
        cut_frame(video, out, int(ts) - BEFORE_MS, ffmpeg=ffmpeg)
    return rel
