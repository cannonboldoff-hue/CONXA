"""BUILD-103 (3): a "choose files, then press Upload" page. The pick sends nothing; the press sends the files. The pick is
built as ``choose_files`` (proven by the file box holding the files, no "the page changed" check) and the press carries
the upload's checklist. Decided from the recorder's request log; a recording without one is left as it was."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from conxa_compile.compiler.build import _build_assertions, _mark_choose_file_presses
from conxa_compile.compiler.effects import build_effect, choose_file_presses
from conxa_core.models.skill_spec import ValidationBlock

PAGE = "http://app.test/up.html"


def _ev(seq, action, ms, url=PAGE, **over):
    sec, milli = divmod(ms, 1000)
    ev = {"action": {"action": action, "timestamp": f"2026-11-17T09:00:{sec:02d}.{milli:03d}Z"},
          "page": {"url": url}, "tab": {"id": "tab_0"}, "extras": {"sequence": seq},
          "post_condition": {"classified_effect": "none"}}
    ev.update(over)
    return ev


def _events():
    return [_ev(1, "click", 1000, url="http://app.test/start"), _ev(2, "upload_intent", 5000), _ev(3, "click", 9000)]


# The event timestamps above are 2026-11-17T09:00:SS; the request log is epoch ms of the same clock.
_BASE = int(datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _log_at(*offsets):
    return {"mutating": [{"t": _BASE + o, "method": "POST"} for o in offsets]}


def test_a_pick_that_sent_nothing_pairs_with_the_press_that_sent_the_files():
    assert choose_file_presses(_events(), _log_at(9300)) == {2: 3}


def test_a_pick_that_sent_the_files_itself_is_the_upload():
    assert choose_file_presses(_events(), _log_at(5200, 9300)) == {}


def test_without_a_request_log_nothing_changes():
    assert choose_file_presses(_events(), None) == {}


def test_a_press_that_sent_nothing_or_one_on_another_page_is_not_the_upload():
    assert choose_file_presses(_events(), _log_at()) == {}
    elsewhere = _events()[:2] + [_ev(3, "click", 9000, url="http://app.test/other")]
    assert choose_file_presses(elsewhere, _log_at(9300)) == {}


def test_a_pick_after_which_the_app_reacted_is_the_upload():
    reacted = _events()
    reacted[1]["post_watch"] = {"progress_seen": True, "final_status": []}
    assert choose_file_presses(reacted, _log_at(9300)) == {}


def test_the_pick_is_built_as_choose_files_and_the_press_as_the_upload(tmp_path):
    events = _events()
    (tmp_path / "requests.json").write_text(json.dumps(_log_at(9300)), encoding="utf-8")
    _mark_choose_file_presses(events, tmp_path)
    pick, press = events[1], events[2]

    eff = build_effect(pick)
    assert eff.kind == "choose_files" and eff.required_rung == "accepted"
    assert [(c.probe["type"], c.rung, c.witness) for c in eff.checks] == [("input_has_files", "accepted", "target")]
    assert not [a for a in _build_assertions(pick, ValidationBlock(), {}, {"primary_selector": "#f"}) if a.type == "state_changed"]

    up = build_effect(press)
    assert up.kind == "upload" and up.required_rung == "completed"
    kinds = [c.probe["type"] for c in up.checks]
    assert "request_chain" in kinds and "input_has_files" not in kinds   # the press holds no file box


def test_an_old_recording_keeps_the_upload_as_the_step_that_does_the_work(tmp_path):
    events = _events()
    _mark_choose_file_presses(events, tmp_path)          # no requests.json
    assert build_effect(events[1]).kind == "upload"
    assert any(a.type == "state_changed" and a.required
               for a in _build_assertions(events[1], ValidationBlock(), {}, {"primary_selector": "#f"}))


def test_the_recorder_logs_only_data_changing_requests_and_no_address():
    from types import SimpleNamespace

    from conxa_compile.recorder.session import RecordingSession

    sess = RecordingSession(session_id="s_req", start_url="about:blank")
    for m in ("GET", "POST", "put", "OPTIONS", "DELETE"):
        sess._on_request(SimpleNamespace(method=m, url="https://app.test/secret?q=1"))
    assert [r["method"] for r in sess._mutating_requests] == ["POST", "PUT", "DELETE"]
    assert all(set(r) == {"t", "method"} for r in sess._mutating_requests)
