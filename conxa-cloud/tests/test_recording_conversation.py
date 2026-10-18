"""BUILD-38: recorder pause/resume, the live-rows hook, plain-language rows and recording hints."""

from __future__ import annotations

import json

from conxa_core.config import settings
from conxa_compile.compiler.build import _derive_input_binding
from conxa_compile.recorder import hints
from conxa_compile.recorder.plain import describe_event
from conxa_compile.recorder.session import RecordingSession

from tests.test_recorder_session import _payload


class _FakePage:
    def is_closed(self) -> bool:
        return False


def _session(tmp_path, sid="s1") -> RecordingSession:
    sess = RecordingSession(session_id=sid, data_root=tmp_path)
    sess._video_session_start_wall_ms = 1
    sess._page = _FakePage()
    return sess


# ── pause / resume ──────────────────────────────────────────────────────────────────────────


def test_nothing_is_recorded_while_paused_and_nothing_is_replayed_on_resume(tmp_path):
    sess = _session(tmp_path)
    sess._consume_payload_sync(_payload())
    assert len(sess.snapshot_events()) == 1

    sess.pause()
    def stamped():
        p = _payload()
        p["_paused"] = True  # what the enqueue points do while paused
        return p

    sess._consume_payload_sync(stamped())
    sess.resume()
    sess._consume_payload_sync(stamped())  # the queue lagged: still dropped after resume
    assert len(sess.snapshot_events()) == 1

    sess._consume_payload_sync(_payload())
    assert len(sess.snapshot_events()) == 2
    assert sess.status()["paused"] is False


def test_enqueue_points_stamp_payloads_only_while_paused(tmp_path):
    sess = _session(tmp_path)
    sess._binding_sink_sync({"page": None, "frame": None}, _payload())
    sess.pause()
    sess._binding_sink_sync({"page": None, "frame": None}, _payload())
    sess.resume()
    first, _, _ = sess._pending_payloads.get_nowait()
    second, _, _ = sess._pending_payloads.get_nowait()
    assert "_paused" not in first and second["_paused"] is True


def test_the_event_hook_sees_each_captured_event_and_cannot_break_recording(tmp_path):
    sess = _session(tmp_path)
    seen: list[str] = []
    sess.event_hook = lambda ev: seen.append(ev.action.action)
    sess._consume_payload_sync(_payload("click"))
    assert seen == ["click"]

    def boom(ev):
        raise RuntimeError("ui callback failed")

    sess.event_hook = boom
    sess._consume_payload_sync(_payload("click"))  # must not raise
    assert len(sess.snapshot_events()) == 2


# ── plain-language rows ─────────────────────────────────────────────────────────────────────


def _ev(action, *, value=None, **target):
    return {
        "action": {"action": action, "value": value},
        "target": {"tag": "input", **target},
        "page": {"url": "https://billing.example.com/invoices/new"},
        "extras": {"sequence": 4},
        "tab": {"id": "tab_1"},
    }


def test_rows_read_like_a_person_describing_what_they_did():
    typed = describe_event(_ev("type", value="PO-44821", label_text="PO number"))
    assert typed == {"sequence": 4, "kind": "type", "text": "Typed “PO-44821” into PO number", "value": "PO-44821", "can_vary": True, "tab": "tab_1"}
    assert describe_event(_ev("click", inner_text="Save", tag="button"))["text"] == "Clicked “Save”"
    nav = describe_event(_ev("navigate", value="https://billing.example.com/invoices/new"))
    assert nav["kind"] == "navigate" and nav["text"] == "Opened the new invoices page" or nav["text"].startswith("Opened the")
    assert describe_event(_ev("select", value="Acme Retail", aria_label="Customer"))["text"] == "Chose “Acme Retail” in Customer"


def test_a_hidden_field_never_offers_the_changes_each_run_toggle_or_leaks_its_value():
    row = describe_event(_ev("type", value="{{REDACTED}}", aria_label="Password"))
    assert row["can_vary"] is False and row["value"] is None and "REDACTED" not in row["text"]


def test_unknown_actions_still_get_a_readable_row():
    assert describe_event(_ev("wait_for_one_of"))["text"] == "Wait for one of"


# ── hints ───────────────────────────────────────────────────────────────────────────────────


def test_hints_round_trip_and_are_stamped_onto_events(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    hints.set_varies("sess1", 4, False)
    hints.set_varies("sess1", 7, True)
    assert hints.load("sess1")["varies"] == {"4": False, "7": True}
    events = [{"extras": {"sequence": 4}}, {"extras": {"sequence": 7}}, {"extras": {"sequence": 9}}, {"action": {}}]
    out = hints.apply_to_events("sess1", events)
    assert [e.get("extras", {}).get("user_varies") for e in out] == [False, True, None, None]
    hints.set_varies("sess1", 4, None)
    assert "4" not in hints.load("sess1")["varies"]


def test_hints_cannot_escape_the_sessions_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    import pytest

    with pytest.raises(ValueError):
        hints.hints_path("../../etc")


def test_no_hints_means_events_are_returned_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    events = [{"extras": {"sequence": 1}}]
    before = json.dumps(events, sort_keys=True)
    assert json.dumps(hints.apply_to_events("never-hinted", events), sort_keys=True) == before


# ── compile ─────────────────────────────────────────────────────────────────────────────────

_POLICY: dict = {"signals": {}}


def _typed(extras=None):
    return {
        "action": {"action": "type", "value": "PO-44821"},
        "target": {"tag": "input", "label_text": "PO number"},
        "semantic": {},
        "extras": extras or {},
    }


def test_a_value_the_person_said_stays_fixed_compiles_as_a_literal_not_an_input():
    value, binding = _derive_input_binding(_typed({"user_varies": False}), _POLICY)
    assert (value, binding) == ("PO-44821", None)


def test_without_a_hint_or_with_changes_each_run_the_binding_is_unchanged():
    default = _derive_input_binding(_typed(), _POLICY)
    assert default[1] == "po_number"
    assert _derive_input_binding(_typed({"user_varies": True}), _POLICY) == default  # byte-identical to no hint
    assert _derive_input_binding(_typed({"sequence": 3, "session_id": "x"}), _POLICY) == default


def test_run_pipeline_with_no_hints_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    called: list[str] = []
    monkeypatch.setattr("conxa_compile.recorder.hints.apply_to_events", lambda sid, evs: called.append(sid) or evs)
    from conxa_compile.pipeline.run import run_pipeline

    assert run_pipeline([]) == []
    assert called == []  # no events, no session id, nothing consulted
