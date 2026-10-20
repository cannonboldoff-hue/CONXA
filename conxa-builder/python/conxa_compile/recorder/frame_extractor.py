"""Extract 5 video frames per event for LLM-native selector verification.

For each event at timestamp T (in ms since video start), extract:
- T-500ms → frames/evt_NNNN_before_far.png
- T-250ms → frames/evt_NNNN_before_near.png  (default representative)
- T+0ms   → frames/evt_NNNN_at.png
- T+250ms → frames/evt_NNNN_after_near.png
- T+500ms → frames/evt_NNNN_after_far.png

After extraction, sets visual["full_screenshot"] to the before_near frame path
and visual["element_snapshot"] to a JPEG element crop from that frame (or None
when bbox w/h < 2). This replaces the old synchronous page.screenshot() capture.

Updates events.jsonl in place: each event's visual.frames dict gets the 5 paths.
Uses ffmpeg (from Playwright's bundled binary, imageio-ffmpeg, or PATH).

Runs at compile time (not at recording stop), so it is idempotent — frames
already on disk are not re-cut — and a recompile repairs whatever frames
failed on a prior attempt. A single event's frame failure does not lose
frames for any other event: it is recorded in the returned failures list and
that event falls back to DOM-only anchors at compile.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from conxa_core.config import settings
from conxa_compile.recorder.visual import crop_element_from_frame


def _is_executable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt" and path.suffix.lower() in {".exe", ".cmd", ".bat"}:
        return True
    return bool(path.stat().st_mode & 0o111)


def _find_ffmpeg() -> str | None:
    """Locate ffmpeg binary. Prefers Playwright, then imageio-ffmpeg, then PATH."""
    # Search the Playwright browsers path (where ffmpeg is actually installed in
    # production deployments — driven by PLAYWRIGHT_BROWSERS_PATH env var, often /opt/pw-browsers).
    candidate_roots: list[Path] = []
    pw_env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if pw_env and pw_env != "0":
        candidate_roots.append(Path(pw_env))
    # Common default locations
    # (Windows installs browsers under %LOCALAPPDATA%\ms-playwright.)
    defaults = ["/opt/pw-browsers", str(Path.home() / ".cache" / "ms-playwright")]
    if os.environ.get("LOCALAPPDATA"):
        defaults.append(str(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"))
    for default in defaults:
        p = Path(default)
        if p.is_dir() and p not in candidate_roots:
            candidate_roots.append(p)
    # Playwright package dir (for in-package bundled ffmpeg in older versions)
    try:
        import playwright
        candidate_roots.append(Path(playwright.__file__).parent)
    except ImportError:
        pass
    for root in candidate_roots:
        try:
            for candidate in root.rglob("ffmpeg*"):
                if _is_executable_file(candidate):
                    return str(candidate)
        except OSError:
            continue

    try:
        import imageio_ffmpeg

        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if candidate.is_file():
            return str(candidate)
    except (ImportError, OSError, RuntimeError):
        pass

    return shutil.which("ffmpeg")


def _extract_frame(
    ffmpeg: str,
    video_path: Path,
    out_path: Path,
    timestamp_ms: int,
) -> None:
    """Extract a single frame at the given timestamp. Raises on failure."""
    if timestamp_ms < 0:
        timestamp_ms = 0
    seconds = timestamp_ms / 1000.0
    cmd = [
        ffmpeg,
        "-y",  # overwrite
        "-ss", f"{seconds:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        "-loglevel", "error",
        str(out_path),
    ]
    try:
        # stdin=DEVNULL: without it ffmpeg inherits the backend process's stdin
        # (the Electron<->Python JSON-RPC pipe) and can hang past exit waiting
        # on it instead of closing. timeout=30 is generous headroom over the
        # ~0.2s/frame measured cost — it exists to bound a genuine hang, not
        # normal decode time.
        result = subprocess.run(
            cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"ffmpeg invocation failed: {exc!s} (cmd={' '.join(cmd)})") from exc

    if result.returncode != 0:
        stderr_snippet = result.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"ffmpeg exited {result.returncode} extracting {out_path.name}: {stderr_snippet}"
        )
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced empty/missing frame: {out_path}")


def extract_frames_for_session(
    session_dir: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[int, dict[str, str]], list[tuple[int, str]]]:
    """Extract 5 frames per event from recording.webm into session_dir/frames/.

    Idempotent: a frame already present on disk (non-empty file) is not
    re-cut, so a recompile after a partial failure only redoes the missing
    frames. Per-event isolated: a frame failure for one event is recorded in
    the returned failures list and that event is left without visual.frames /
    full_screenshot — it does not affect any other event.

    Returns (frames_by_event_index, failures) where failures is
    [(event_index, message), ...] for events that could not get frames.
    Raises only on session-wide problems: missing video, missing ffmpeg,
    missing events.jsonl, or an event without a timestamp.
    """
    video_path = session_dir / "recording.webm"
    events_path = session_dir / "events.jsonl"
    videos_json_path = session_dir / "videos.json"

    # Multi-tab: videos.json (written by session.py::_finalize_video_file_sync) maps each tab
    # id to its own video file — every tab records independently, at its own start time. Absent
    # for sessions recorded before multi-tab support (or single-tab ones that never wrote it),
    # in which case every event falls back to recording.webm, identical to pre-multi-tab behavior.
    tab_videos: dict[str, Path] = {}
    if videos_json_path.is_file():
        try:
            raw = json.loads(videos_json_path.read_text(encoding="utf-8"))
            for tab_id, meta in (raw or {}).items():
                fname = str((meta or {}).get("file") or "")
                if fname:
                    tab_videos[tab_id] = session_dir / fname
        except (json.JSONDecodeError, OSError):
            pass

    if not video_path.is_file() and not any(p.is_file() for p in tab_videos.values()):
        raise FileNotFoundError(f"recording.webm not found in {session_dir}")
    if not events_path.is_file():
        raise FileNotFoundError(f"events.jsonl not found in {session_dir}")

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not available; install Playwright browsers (which bundles ffmpeg) "
            "or install imageio-ffmpeg, or add ffmpeg to PATH. Searched "
            "PLAYWRIGHT_BROWSERS_PATH, /opt/pw-browsers, ~/.cache/ms-playwright, "
            "the playwright package directory, imageio-ffmpeg, and PATH."
        )

    frames_dir = session_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    offsets = [
        ("before_far", -500),
        ("before_near", -250),
        ("at", 0),
        ("after_near", 250),
        ("after_far", 500),
    ]

    events: list[dict[str, Any]] = []
    with open(events_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))  # raises on invalid JSON — by design

    result: dict[int, dict[str, str]] = {}
    failures: list[tuple[int, str]] = []
    images_dir = session_dir / "images"

    for i, ev in enumerate(events):
        visual = ev.setdefault("visual", {})
        ts_ms = visual.get("timestamp_ms")
        if ts_ms is None:
            raise ValueError(
                f"event index {i} has no visual.timestamp_ms; non-auth events must have one"
            )

        # upload_intent fires on a hidden <input type=file>'s change event, right after the OS
        # file picker (which nothing on the page's own timeline shows) closes — the click that
        # opened that picker, immediately prior in the same recording, already has real frames
        # of the moment. Extracting a second set here would just show the same UI a beat later
        # with no picker visible either way, so this event stays frame-less by design, not failure.
        action_name = str((ev.get("action") or {}).get("action") or "")
        if action_name == "upload_intent":
            if on_progress is not None:
                on_progress(i + 1, len(events))
            continue

        # Cut this event's frames from its own tab's video — a cross-tab timestamp cut from
        # the wrong tab's video is worse than no frames at all, so a missing/unreadable tab
        # video is this event's own failure, not a session-wide one (other tabs' events are
        # unaffected).
        tab_id = str((ev.get("tab") or {}).get("id") or "tab_0")
        event_video_path = tab_videos.get(tab_id, video_path)
        if not event_video_path.is_file():
            failures.append((i, f"video not found for tab {tab_id}: {event_video_path}"))
            continue

        try:
            frames: dict[str, str] = {}
            for label, offset_ms in offsets:
                frame_path = frames_dir / f"evt_{i + 1:04d}_{label}.png"
                if not (frame_path.is_file() and frame_path.stat().st_size > 0):
                    target_ms = int(ts_ms) + offset_ms
                    _extract_frame(ffmpeg, event_video_path, frame_path, target_ms)
                frames[label] = f"frames/evt_{i + 1:04d}_{label}.png"

            # Set the default representative: before_near frame (T-250ms).
            # This is after the user initiated the action but before any page reaction/navigation,
            # so the target element is still present and the bbox is valid.
            representative_rel = frames["before_near"]
            representative_abs = session_dir / representative_rel
            bbox = visual.get("bbox") if isinstance(visual.get("bbox"), dict) else {}
            images_dir.mkdir(parents=True, exist_ok=True)
            el_out = images_dir / f"evt_{i + 1:04d}_element.jpg"
            el_rel = crop_element_from_frame(
                representative_abs,
                bbox,
                el_out,
                jpeg_quality=settings.screenshot_jpeg_quality,
            )
        except (RuntimeError, OSError) as exc:
            # RuntimeError: ffmpeg failure. OSError (incl. PIL's UnidentifiedImageError,
            # a subclass): frame file unreadable/corrupt during the crop step.
            failures.append((i, str(exc)))
            continue

        visual["frames"] = frames
        visual["full_screenshot"] = representative_rel
        visual["element_snapshot"] = el_rel
        result[i] = frames

        if on_progress is not None:
            on_progress(i + 1, len(events))

    with open(events_path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    return result, failures
