"""Track B and the merge, end to end: browser events (B1 normalize, B2 clean) -> merged timeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conxa_compile.video.merge import merge_timeline
from conxa_compile.video.prepare import timed_events

MERGED_FILE = "merged_timeline.json"


def run_track_b(session_dir: Path) -> list[dict[str, Any]]:
    """B1 + B2: the same normalised, cleaned event list the compiler will compile, as timed events
    ({ev, tab, wall}). Tab markers and other events with no video time drop out."""
    from conxa_compile.compiler.build import prepare_compile_events
    from conxa_compile.pipeline.run import run_pipeline
    from conxa_compile.policy.bundle import get_policy_bundle

    session_dir = Path(session_dir)
    raw = [json.loads(line) for line in (session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    videos = json.loads((session_dir / "videos.json").read_text(encoding="utf-8"))
    cleaned = prepare_compile_events(run_pipeline(raw), get_policy_bundle().data)
    return timed_events(cleaned, videos)


def build_merged_timeline(session_dir: Path, track_a: dict[str, Any], timed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """M: line the Pegasus rows up with the browser events and save merged_timeline.json."""
    merged = merge_timeline(timed, track_a["rows"], track_a["splice_map"])
    (Path(session_dir) / MERGED_FILE).write_text(json.dumps(merged), encoding="utf-8")
    return merged
