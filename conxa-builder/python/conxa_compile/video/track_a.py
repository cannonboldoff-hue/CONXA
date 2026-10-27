"""Track A end to end: prepare the video, ask Pegasus (or the cache), return video rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from conxa_compile.video.prepare import prepare_video
from conxa_compile.video.signals import read_cache, rows_from_segments, write_cache


class _Analyzer(Protocol):
    def analyze(self, video_path: Path, duration_s: float = 0.0) -> dict[str, Any]: ...


def run_track_a(session_dir: Path, client: _Analyzer, *, blur_all: bool = False) -> dict[str, Any]:
    """{rows, splice_map, fingerprint, from_cache, truncated}. Only a new recording or a changed
    blur setting reaches Pegasus; a recompile, edit or re-answered question hits the cache."""
    prepared = prepare_video(session_dir, blur_all=blur_all)
    result = read_cache(session_dir, prepared.fingerprint)
    from_cache = result is not None
    if result is None:
        result = client.analyze(prepared.path, prepared.duration_ms / 1000)
        write_cache(session_dir, prepared.fingerprint, result)
    return {
        "rows": rows_from_segments(result.get("segments") or [], prepared.duration_ms / 1000),
        "splice_map": prepared.splice_map,
        "fingerprint": prepared.fingerprint,
        "from_cache": from_cache,
        "truncated": bool(result.get("truncated")),
    }
