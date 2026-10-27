"""Locate ffmpeg and run it. Shared by the recorder, frame cutting and video preparation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


def _is_executable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt" and path.suffix.lower() in {".exe", ".cmd", ".bat"}:
        return True
    return bool(path.stat().st_mode & 0o111)


def find_ffmpeg() -> str | None:
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


def find_full_ffmpeg() -> str | None:
    """A full ffmpeg build (libx264, trim/concat/boxblur/drawbox filters).

    Playwright's bundled ffmpeg is a minimal VP8-only build and cannot prepare
    the upload video, so this skips it: imageio-ffmpeg first, then PATH.
    """
    try:
        import imageio_ffmpeg

        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if candidate.is_file():
            return str(candidate)
    except (ImportError, OSError, RuntimeError):
        pass
    return shutil.which("ffmpeg")


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_duration_ms(ffmpeg: str, path: Path) -> int | None:
    """Container duration in ms, or None when the file reports none (ffmpeg has no ffprobe here)."""
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    match = _DURATION_RE.search(result.stderr.decode("utf-8", errors="replace"))
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)
