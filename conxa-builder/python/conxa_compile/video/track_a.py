"""Track A end to end: prepare the video, ask Pegasus (or the cache), return video rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from conxa_compile.video.prepare import prepare_video
from conxa_compile.video.signals import read_cache, rows_from_segments, write_cache


class _Analyzer(Protocol):
    def analyze(self, video_path: Path, duration_s: float = 0.0) -> dict[str, Any]: ...


def run_track_a(session_dir: Path, client: _Analyzer, *, blur_all: bool = False, job: Any = None) -> dict[str, Any]:
    """{rows, splice_map, fingerprint, from_cache, truncated}. Only a new recording or a changed
    blur setting reaches Pegasus; a recompile, edit or re-answered question hits the cache.

    ``job`` (video/job.py ``VideoJob``) saves the cloud job id as it appears and resumes a saved one for this
    exact prepared video, so a restart polls the same job instead of uploading again."""
    prepared = prepare_video(session_dir, blur_all=blur_all)
    result = read_cache(session_dir, prepared.fingerprint)
    from_cache = result is not None
    if result is None:
        if job is None:
            result = client.analyze(prepared.path, prepared.duration_ms / 1000)
        else:
            result = client.analyze(
                prepared.path, prepared.duration_ms / 1000,
                resume_job_id=job.resume_id(prepared.fingerprint),
                on_job_id=lambda job_id: job.submitted(prepared.fingerprint, job_id))
        write_cache(session_dir, prepared.fingerprint, result)
    if job is not None and (not from_cache or job.rec):
        job.done()   # on a cache hit only when an earlier try left a record to finish
    return {
        "rows": rows_from_segments(result.get("segments") or [], prepared.duration_ms / 1000),
        "splice_map": prepared.splice_map,
        "fingerprint": prepared.fingerprint,
        "from_cache": from_cache,
        "truncated": bool(result.get("truncated")),
    }
