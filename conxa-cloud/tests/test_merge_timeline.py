"""M: order-keeping alignment of Pegasus rows with browser events."""

from __future__ import annotations

from conxa_compile.video import merge as M
from conxa_compile.video import prepare as P

VIDEOS = {
    "tab_0": {"file": "recording.webm", "start_wall_ms": 100_000},
    "tab_1": {"file": "recording-tab_1.webm", "start_wall_ms": 120_000},
}


def ev(tab, ts, action="click", seq=0, value=None, text=""):
    return {
        "action": {"action": action, "value": value},
        "target": {"inner_text": text},
        "semantic": {"role": "button"},
        "page": {"url": "https://a.test/"},
        "tab": {"id": tab},
        "visual": {"timestamp_ms": ts},
        "extras": {"sequence": seq},
    }


def row(start, end, kind="click", outside=False, **text):
    base = {"start_s": start, "end_s": end, "action_kind": kind, "outside_page": outside,
            "visible_target": "", "intent": "", "purpose": "", "screen_before": "", "screen_after": ""}
    return {**base, **text}


def one_piece(wall_end=130_000):
    return P.build_splice_map([P.Piece("tab_0", 100_000, wall_end)], [], VIDEOS)


def merge(events, rows, splice=None):
    return M.merge_timeline(P.timed_events(events, VIDEOS), rows, splice or one_piece())


def events_of(merged):
    return [r for r in merged if r["kind"] == "event"]


def test_artifact_example_rows():
    events = [
        ev("tab_0", 4_901, "scroll", seq=4),
        ev("tab_0", 7_412, "type", seq=6, value="INV-20931"),
        ev("tab_0", 10_120, "click", seq=7, text="row"),
        ev("tab_0", 10_640, "click", seq=8, text="Open"),
        ev("tab_0", 12_288, "click", seq=9, text="Download PDF"),
    ]
    rows = [
        row(5, 6, "wait", outside=True, intent="wait while the list loads"),
        row(7, 9, "type", intent="find the invoice", visible_target="search box INV-20931"),
        row(10, 11, "click", intent="open the invoice"),
        row(12, 15, "click", intent="download the invoice PDF", screen_after="download bar shows the file"),
    ]
    merged = merge(events, rows)
    by_id = {r["event_id"]: r for r in events_of(merged)}
    assert by_id[4]["segment"] is None and by_id[4]["flags"] == ["pegasus_missed"]
    assert by_id[6]["segment"]["intent"] == "find the invoice" and "value_seen_in_both" in by_id[6]["flags"]
    # two clicks in one segment, kept in recorded order
    assert by_id[7]["segment"] is by_id[8]["segment"] and by_id[7]["t_ms"] < by_id[8]["t_ms"]
    assert by_id[9]["segment"]["intent"] == "download the invoice PDF"
    obs = [r for r in merged if r["kind"] == "observation"]
    assert [(o["type"], o["segment"]["intent"]) for o in obs] == [("off_page", "wait while the list loads")]
    assert [r["row_ref"] for r in merged] == [f"r{i}" for i in range(1, len(merged) + 1)]


def test_two_clicks_in_the_same_second_never_swap():
    events = [ev("tab_0", 5_100, seq=1, text="A"), ev("tab_0", 5_400, seq=2, text="B")]
    rows = [row(5, 6, "click", intent="first"), row(5, 6, "click", intent="second")]
    ev_rows = events_of(merge(events, rows))
    assert [r["segment"]["intent"] for r in ev_rows] == ["first", "second"]


def test_a_popup_click_carries_its_class_in_the_shared_vocabulary():
    popup = {"kind": "dom", "container_signal": '[role="dialog"]', "title": "Upload options",
             "message": "x already exists.", "has_fields": False,
             "buttons": [{"name": "Replace", "chosen": True}, {"name": "Keep both"}, {"name": "Cancel"}]}
    plain = ev("tab_0", 9_100, seq=2, text="Save")
    with_popup = {**ev("tab_0", 5_100, seq=1, text="Replace"), "dialog_context": popup}
    ev_rows = events_of(merge([with_popup, plain], [row(5, 6, "click"), row(9, 10, "click")]))
    assert ev_rows[0]["dialog"] == "decision"
    assert ev_rows[0]["surface"] == "decision_consequential"
    assert ev_rows[0]["dialog_buttons"] == ["Replace", "Keep both", "Cancel"]
    assert "surface" not in ev_rows[1] and "dialog" not in ev_rows[1] and "dialog_buttons" not in ev_rows[1]


def test_what_the_page_said_after_an_action_rides_on_its_row_as_words():
    said = [{"kind": "alert", "words": ["name", "already", "exists"]}]
    noisy = {**ev("tab_0", 5_100, seq=1, text="Save"), "surface_context": {"items": said}}
    quiet = ev("tab_0", 9_100, seq=2, text="Next")
    ev_rows = events_of(merge([noisy, quiet], [row(5, 6, "click"), row(9, 10, "click")]))
    assert ev_rows[0]["said"] == said
    assert "said" not in ev_rows[1]


def test_kind_mismatch_loses_to_a_better_fitting_segment():
    events = [ev("tab_0", 5_000, "click", seq=1)]
    rows = [row(4, 6, "type", intent="typing"), row(4, 6, "click", intent="clicking")]
    assert events_of(merge(events, rows))[0]["segment"]["intent"] == "clicking"


def test_segment_far_from_event_is_not_paired():
    merged = merge([ev("tab_0", 2_000, seq=1)], [row(20, 22, "click", intent="much later")])
    assert events_of(merged)[0]["flags"] == ["pegasus_missed"]
    assert [r["type"] for r in merged if r["kind"] == "observation"] == ["unmatched_segment"]


def test_segment_across_a_tab_switch_pairs_each_half_with_its_own_tab():
    splice = P.build_splice_map([P.Piece("tab_0", 100_000, 104_000), P.Piece("tab_1", 120_000, 126_000)], [], VIDEOS)
    events = [ev("tab_0", 3_500, seq=1), ev("tab_1", 1_500, seq=2)]      # wall 103500 and 121500
    rows = [row(3, 6, "click", intent="spans the switch")]                # out 3000-6000: both pieces
    ev_rows = events_of(merge(events, rows, splice))
    assert all(r["segment"] is not None for r in ev_rows)
    other_tab = M.align(P.timed_events(events, VIDEOS), M._split_rows(rows, splice), rows)
    assert sorted(other_tab) == [(0, 0), (1, 1)]          # one sub-segment per tab


def test_cut_becomes_an_idle_wait_with_its_real_length():
    splice = P.build_splice_map([P.Piece("tab_0", 100_000, 103_000), P.Piece("tab_0", 115_000, 120_000)],
                                [(103_000, 115_000)], VIDEOS)
    merged = merge([ev("tab_0", 2_000, seq=1)], [], splice)
    waits = [r for r in merged if r["kind"] == "observation"]
    assert [(w["type"], w["seconds"]) for w in waits] == [("idle_wait", 14.0)]    # 12s cut + 1s kept each side


def test_empty_inputs():
    assert merge([], []) == []
    assert [r["flags"] for r in events_of(merge([ev("tab_0", 1_000, seq=1)], []))] == [["pegasus_missed"]]


# --- Track B end to end --------------------------------------------------------------

def test_track_b_runs_the_real_pipeline_and_merges(tmp_path):
    import json

    from conxa_compile.video import track_b
    from tests.test_second_opinion import _minimal_click_event

    events = []
    for seq, (ts, stamp, text) in enumerate([(1_000, "2026-01-01T00:00:01Z", "Save"), (2_500, "2026-01-01T00:00:02Z", "Done")], start=1):
        e = _minimal_click_event()
        e["action"] = {"action": "click", "timestamp": stamp, "value": None}
        e["target"].update({"id": f"b{seq}", "inner_text": text})
        e["selectors"].update({"css": f"#b{seq}", "text_based": f'text="{text}"'})
        e["semantic"]["normalized_text"] = text.lower()
        e["visual"]["timestamp_ms"] = ts
        e["extras"] = {"sequence": seq}
        events.append(e)
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (tmp_path / "videos.json").write_text(json.dumps({"tab_0": {"file": "recording.webm", "start_wall_ms": 100_000}}))

    timed = track_b.run_track_b(tmp_path)
    assert [t["wall"] for t in timed] == [101_000, 102_500]

    track_a = {"rows": [row(1, 3, "click", intent="save the form")], "splice_map": one_piece()}
    merged = track_b.build_merged_timeline(tmp_path, track_a, timed)
    assert (tmp_path / track_b.MERGED_FILE).is_file()
    assert [r["event_id"] for r in merged if r["kind"] == "event"] == [1, 2]
