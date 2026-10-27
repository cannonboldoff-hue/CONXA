"""A1 video preparation: front-tab pieces, idle cuts, splice map, blur/ring windows, filter graph."""

from __future__ import annotations

import json
import subprocess

import pytest

from conxa_compile.video import prepare as P
from conxa_compile.video import splice
from conxa_compile.video.ffmpeg import find_full_ffmpeg, probe_duration_ms

VIDEOS = {
    "tab_0": {"file": "recording.webm", "start_wall_ms": 100_000},
    "tab_1": {"file": "recording-tab_1.webm", "start_wall_ms": 104_000},
}


def ev(tab: str, ts: int, action: str = "click", url: str = "https://a.test/", *, input_type: str | None = None,
       value: str | None = None, bbox=(100, 200, 80, 30), viewport="1280x720", scroll="0,0") -> dict:
    return {
        "action": {"action": action, "value": value},
        "semantic": {"input_type": input_type},
        "page": {"url": url},
        "tab": {"id": tab},
        "visual": {"timestamp_ms": ts, "bbox": dict(zip("xywh", bbox)), "viewport": viewport, "scroll_position": scroll},
    }


def timed(*events: dict, videos=VIDEOS) -> list[dict]:
    return P.timed_events(list(events), videos)


# --- pieces ---------------------------------------------------------------------

def test_new_tab_switch_happens_when_its_video_starts_and_returning_leads_the_event():
    evs = timed(ev("tab_0", 1000), ev("tab_0", 2000), ev("tab_1", 7000), ev("tab_0", 12_000))
    pieces = P.plan_pieces(evs, VIDEOS)
    assert pieces == [
        P.Piece("tab_0", 100_000, 104_000),    # new tab: max(prev event 102000, tab_1 start 104000)
        P.Piece("tab_1", 104_000, 111_500),    # returning to tab_0 at 112000 - 500ms lead
        P.Piece("tab_0", 111_500, 115_000),    # last event 112000 + 3000 tail
    ]


def test_tail_is_clamped_to_video_end():
    videos = {"tab_0": {**VIDEOS["tab_0"], "end_wall_ms": 102_500}}
    evs = timed(ev("tab_0", 1000), ev("tab_0", 2000), videos=videos)
    assert P.plan_pieces(evs, videos)[-1].wall_end == 102_500


# --- cuts and splice map ----------------------------------------------------------

def test_cuts_only_for_gaps_over_5s_and_keep_a_second_each_side():
    assert P.plan_cuts([0, 4_000, 4_000 + 5_001]) == [(5_000, 8_001)]
    assert P.plan_cuts([0, 5_000]) == []


def test_subtract_cuts_splits_a_piece_and_splice_map_is_contiguous():
    pieces = [P.Piece("tab_0", 100_000, 120_000)]
    cuts = [(103_000, 115_000)]
    kept = P.subtract_cuts(pieces, cuts)
    assert kept == [P.Piece("tab_0", 100_000, 103_000), P.Piece("tab_0", 115_000, 120_000)]
    smap = P.build_splice_map(kept, cuts, VIDEOS)
    assert [(e["kind"], e["out_start_ms"], e["out_end_ms"]) for e in smap] == [
        ("piece", 0, 3_000), ("cut", 3_000, 3_000), ("piece", 3_000, 8_000)]
    assert smap[1]["cut_ms"] == 12_000
    assert splice.wall_to_out_ms(116_000, smap) == 4_000
    assert splice.wall_to_out_ms(110_000, smap) is None          # inside the cut
    assert splice.out_ms_to_wall(4_000, smap) == ("tab_0", 116_000)


def test_splice_map_records_tab_offsets_across_a_switch():
    kept = [P.Piece("tab_0", 100_000, 104_000), P.Piece("tab_1", 104_000, 110_000)]
    smap = P.build_splice_map(kept, [], VIDEOS)
    assert smap[1]["src_offset_ms"] == 0 and smap[1]["out_start_ms"] == 4_000
    assert splice.out_ms_to_wall(5_000, smap) == ("tab_1", 105_000)


# --- blur windows -------------------------------------------------------------------

def _smap(wall_end=130_000):
    return P.build_splice_map([P.Piece("tab_0", 100_000, wall_end)], [], VIDEOS)


def test_password_is_blurred_until_the_page_changes_but_plain_text_is_not():
    evs = timed(
        ev("tab_0", 1000, "click", "https://a.test/login"),
        ev("tab_0", 3000, "type", "https://a.test/login", input_type="password", value="{{REDACTED}}"),
        ev("tab_0", 4000, "type", "https://a.test/login", value="alice"),
        ev("tab_0", 6000, "click", "https://a.test/home"),
    )
    smap = _smap()
    windows = P.blur_windows(evs, smap, blur_all=False, end_wall=130_000)
    assert len(windows) == 1
    # from the previous event (101000 -> 1000ms) to the page change at 106000 + 500ms tail
    assert (windows[0]["start_ms"], windows[0]["end_ms"]) == (1_000, 6_500)
    assert len(P.blur_windows(evs, smap, blur_all=True, end_wall=130_000)) == 2


def test_blur_window_is_clipped_to_the_events_own_tab():
    smap = P.build_splice_map([P.Piece("tab_0", 100_000, 104_000), P.Piece("tab_1", 104_000, 110_000)], [], VIDEOS)
    evs = timed(ev("tab_0", 1000), ev("tab_0", 3000, "type", input_type="password", value="{{REDACTED}}"),
                ev("tab_1", 7000, "click", "https://b.test/"))
    windows = P.blur_windows(evs, smap, blur_all=False, end_wall=110_000)
    assert all(w["end_ms"] <= 4_000 for w in windows)


def test_output_rect_subtracts_scroll_pads_and_letterboxes():
    assert P.output_rect(ev("tab_0", 0, bbox=(100, 500, 80, 30), scroll="0,400"), pad=8) == (92, 92, 96, 46)
    # 1920x1080 viewport scales by 2/3
    assert P.output_rect(ev("tab_0", 0, bbox=(300, 150, 150, 60), viewport="1920x1080")) == (200, 100, 100, 40)
    assert P.output_rect(ev("tab_0", 0, bbox=(0, 0, 0, 0))) is None


def test_ring_windows_for_clicks_only():
    evs = timed(ev("tab_0", 5000), ev("tab_0", 7000, "type", value="x"))
    rings = P.ring_windows(evs, _smap())
    assert len(rings) == 1
    assert (rings[0]["start_ms"], rings[0]["end_ms"]) == (4_900, 5_500)
    assert (rings[0]["x"], rings[0]["y"]) == (140 - 28, 215 - 28)


# --- filter graph ---------------------------------------------------------------------

def test_filter_graph_single_piece_has_no_concat_and_pads_short_video():
    graph = P.build_filter_graph([P.Piece("tab_0", 100_000, 101_000)], {"tab_0": 0}, VIDEOS, [], [], pad_ms=3_000)
    assert "concat" not in graph
    assert "trim=start=0.000:end=1.000" in graph
    assert "tpad=stop_mode=clone:stop_duration=3.000" in graph
    assert graph.rstrip().endswith("[outv]")


def test_filter_graph_two_pieces_blur_and_ring():
    kept = [P.Piece("tab_0", 100_000, 103_000), P.Piece("tab_1", 104_000, 108_000)]
    graph = P.build_filter_graph(
        kept, {"tab_0": 0, "tab_1": 1}, VIDEOS,
        [{"rect": (10, 20, 100, 40), "start_ms": 500, "end_ms": 2_500}],
        [{"x": 1, "y": 2, "start_ms": 100, "end_ms": 700}], pad_ms=0)
    assert "[1:v]trim=start=0.000:end=4.000" in graph
    assert "concat=n=2:v=1:a=0" in graph
    assert "crop=100:40:10:20,boxblur=luma_radius=9:luma_power=3" in graph
    assert "overlay=10:20:enable='between(t,0.500,2.500)'" in graph
    assert "drawbox=x=1:y=2:w=56:h=56" in graph and "between(t,0.100,0.700)" in graph


# --- real ffmpeg ----------------------------------------------------------------------

FFMPEG = find_full_ffmpeg()


@pytest.mark.skipif(not FFMPEG, reason="needs a full ffmpeg build")
def test_prepare_video_end_to_end_cuts_idle_and_reuses_cache(tmp_path):
    src = tmp_path / "recording.webm"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=5:duration=12",
                    "-c:v", "libvpx", str(src)], check=True, stdin=subprocess.DEVNULL)
    (tmp_path / "videos.json").write_text(json.dumps({"tab_0": {"file": "recording.webm", "start_wall_ms": 0}}))
    events = [ev("tab_0", 1000), ev("tab_0", 2000, "type", input_type="password", value="{{REDACTED}}"), ev("tab_0", 9000)]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")

    first = P.prepare_video(tmp_path, ffmpeg=FFMPEG)
    assert not first.reused and first.path.stat().st_size > 0
    assert any(e["kind"] == "cut" and e["cut_ms"] == 5_000 for e in first.splice_map)   # 2000->9000 gap, 1s kept each side
    assert abs(probe_duration_ms(FFMPEG, first.path) - 7_000) < 500

    again = P.prepare_video(tmp_path, ffmpeg=FFMPEG)
    assert again.reused and again.fingerprint == first.fingerprint

    other = P.prepare_video(tmp_path, blur_all=True, ffmpeg=FFMPEG)
    assert not other.reused
