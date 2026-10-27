"""One frame cut on demand from a tab video."""

from __future__ import annotations

import json
import subprocess

import pytest

from conxa_compile.video import frame_at
from conxa_compile.video.ffmpeg import find_full_ffmpeg

FFMPEG = find_full_ffmpeg()


def event(ts=3000, seq=7, tab="tab_0"):
    return {"visual": {"timestamp_ms": ts}, "extras": {"sequence": seq}, "tab": {"id": tab}}


def test_no_timestamp_sequence_or_video_means_no_frame(tmp_path):
    assert frame_at.event_frame(tmp_path, {"visual": {}, "extras": {"sequence": 1}}) is None
    assert frame_at.event_frame(tmp_path, {"visual": {"timestamp_ms": 5}, "extras": {}}) is None
    assert frame_at.event_frame(tmp_path, event()) is None                       # no video on disk


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_frame_is_cut_once_from_the_events_own_tab_video_and_reused(tmp_path):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=5:duration=6",
                    "-c:v", "libvpx", str(tmp_path / "recording-tab_1.webm")], check=True, stdin=subprocess.DEVNULL)
    (tmp_path / "videos.json").write_text(json.dumps({"tab_1": {"file": "recording-tab_1.webm", "start_wall_ms": 0}}))
    rel = frame_at.event_frame(tmp_path, event(ts=3000, seq=7, tab="tab_1"), ffmpeg=FFMPEG)
    assert rel == "frames/evt_0007_before_near.png" and (tmp_path / rel).stat().st_size > 0
    stamp = (tmp_path / rel).stat().st_mtime_ns
    assert frame_at.event_frame(tmp_path, event(ts=3000, seq=7, tab="tab_1"), ffmpeg=FFMPEG) == rel
    assert (tmp_path / rel).stat().st_mtime_ns == stamp                              # not cut again
