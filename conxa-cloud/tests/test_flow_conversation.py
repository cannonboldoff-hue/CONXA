"""BUILD-38: sign-in and recording driven from a workflow's conversation (no browser, no model)."""

from __future__ import annotations

import importlib.util
import os
import sys
import time

import pytest

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from conxa_core.config import settings  # noqa: E402
from conxa_compile.conversation import runtime_state, thread_store as ts  # noqa: E402
from conxa_compile.conversation.lifecycle import lifecycle_for_workflow  # noqa: E402
from handlers.protocol import _CommandError  # noqa: E402


class FakeSession:
    def __init__(self):
        self.browser_open = True
        self.paused = False
        self.events: list[dict] = []
        self.event_hook = None

    def pause(self): self.paused = True
    def resume(self): self.paused = False
    def snapshot_events(self): return self.events
    def status(self): return {"paused": self.paused}


class FakeRegistry:
    def __init__(self): self.sessions: dict[str, FakeSession] = {}
    def get(self, sid): return self.sessions.get(sid)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    runtime_state.clear_all()
    spec = importlib.util.spec_from_file_location("cbackend_flow", os.path.join(_PY_DIR, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from handlers import protocol, workflow_flow

    out: list[dict] = []
    mod._write = out.append
    protocol._write = out.append
    b = mod.Backend()
    reg = FakeRegistry()
    monkeypatch.setattr(workflow_flow, "_recorder_registry", reg)

    from conxa_core.storage.group_store import add_app, create_group
    from conxa_core.storage.workflow_store import create_workflow

    group = create_group("Finance Ops")
    group = add_app(group.id, "Billing Portal", "https://billing.example.test/login", "https://billing.example.test/home")
    wf = create_workflow("Create invoice", "https://billing.example.test/invoices/new", group_id=group.id)
    calls: list[tuple] = []

    def start_auth(payload, rid):
        calls.append(("start_auth", payload))
        reg.sessions["auth1"] = FakeSession()
        return {"session_id": "auth1", "login_url": "https://billing.example.test/login"}

    def finish_auth(payload, rid):
        calls.append(("finish_auth", payload))
        if env_state["confirmed"]:
            from conxa_core.storage.group_store import set_group_app_auth
            set_group_app_auth(group.id, group.apps[0].id, "state.json") if False else None
            captured["done"] = True
            return {"confirmed": True}
        return {"confirmed": False, "reason": "Couldn’t confirm sign-in yet."}

    def start_rec(payload, rid):
        calls.append(("start_rec", payload))
        reg.sessions["rec1"] = FakeSession()
        return {"session_id": "rec1", "start_url": wf.target_url}

    def stop_rec(payload, rid):
        calls.append(("stop_rec", payload))
        from conxa_core.storage.workflow_store import get_workflow, save_workflow
        w = get_workflow(wf.id)
        w.session_id, w.recording_status, w.recording_duration_seconds = "rec1", "recorded", 151.0
        save_workflow(w)
        reg.sessions["rec1"].browser_open = False
        return {"session_id": "rec1", "event_count": 14, "status": "recorded"}

    def cancel_rec(payload, rid):
        calls.append(("cancel_rec", payload))
        return {"ok": True}

    env_state = {"confirmed": False}
    captured: dict = {}
    monkeypatch.setattr(b, "cmd_start_group_app_auth", start_auth)
    monkeypatch.setattr(b, "cmd_finish_group_app_auth", finish_auth)
    monkeypatch.setattr(b, "cmd_start_recording", start_rec)
    monkeypatch.setattr(b, "cmd_stop_recording", stop_rec)
    monkeypatch.setattr(b, "cmd_cancel_recording", cancel_rec)
    monkeypatch.setattr(b, "cmd_cancel_group_app_auth", lambda p, r: calls.append(("cancel_auth", p)) or {"ok": True})
    monkeypatch.setattr("handlers.workflow_flow._WATCH_INTERVAL_S", 0.01)
    monkeypatch.setattr(b, "_flow_start_compile", lambda wid: calls.append(("compile", wid)))
    return b, reg, group, wf, calls, env_state, out


def _cards(wf_id, card_type=None):
    return [i for i in ts.load(f"wf:{wf_id}", limit=100)["items"] if i["kind"] == "card" and (card_type is None or i["card_type"] == card_type)]


def _events(wf_id):
    return [i["text"] for i in ts.load(f"wf:{wf_id}", limit=100)["items"] if i["kind"] == "event"]


def _mark_signed_in(group):
    from conxa_core.storage.group_store import get_group, save_group

    g = get_group(group.id)
    state = settings.data_dir / f"{g.apps[0].id}.json"
    state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    g.apps[0].captured_at = g.apps[0].checked_at = time.time()
    g.apps[0].storage_state_path = str(state)
    save_group(g)


def test_begin_with_an_expired_sign_in_asks_for_it_again_before_recording(env, monkeypatch):
    b, _reg, group, wf, calls, _st, _out = env
    from conxa_core.storage.group_store import get_group, save_group

    _mark_signed_in(group)
    g = get_group(group.id)
    g.apps[0].checked_at = time.time() - 3600  # past the gate's trust window, so it re-checks
    save_group(g)
    monkeypatch.setattr("conxa_compile.recorder.session.check_app_session_sync", lambda _app: "expired")

    res = b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    assert res["next"] == "sign_in"
    assert [c[0] for c in calls] == ["start_auth"]  # no recording started
    assert "Billing Portal sign-in expired — opened its sign-in page" in _events(wf.id)


def test_begin_asks_for_an_app_the_start_url_does_not_match(env):
    """The reported case: start on one app, use another later. Every group app gates."""
    b, _reg, group, wf, calls, _st, _out = env
    from conxa_core.storage.group_store import add_app

    _mark_signed_in(group)
    add_app(group.id, "Drive", "https://accounts.drive.test/login", "https://drive.test/")
    res = b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    assert res["next"] == "sign_in"
    assert calls[0][0] == "start_auth" and _cards(wf.id, "sign_in")[0]["app_name"] == "Drive"


def test_begin_with_no_saved_sign_in_opens_the_sign_in_page_and_records_nothing(env):
    b, reg, group, wf, calls, _st, _out = env
    res = b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    assert res["next"] == "sign_in"
    assert [c[0] for c in calls] == ["start_auth"]  # no recording started
    card = _cards(wf.id, "sign_in")[0]
    assert card["state"] == "waiting" and card["app_name"] == "Billing Portal" and card["actions"] == ["done", "cancel"]
    assert "Billing Portal needs sign-in — opened its sign-in page" in _events(wf.id)
    lc = lifecycle_for_workflow(__import__("conxa_core.storage.workflow_store", fromlist=["x"]).get_workflow(wf.id))
    assert (lc["stage"], lc["substate"]) == ("configure", "signing_in")
    # a second call reports instead of opening a second window
    assert b.cmd_flow_begin({"workflow_id": wf.id}, "r") == {"next": "sign_in", "already": True}


def test_an_unconfirmed_sign_in_keeps_the_window_open_and_says_why(env):
    b, _reg, _g, wf, calls, st, _out = env
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    card = _cards(wf.id, "sign_in")[0]
    res = b.cmd_flow_sign_in_done({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert res["confirmed"] is False
    after = _cards(wf.id, "sign_in")[0]
    assert after["state"] == "waiting" and after["note"] == "Couldn’t confirm sign-in yet." and after["can_force"] is True
    assert not _cards(wf.id, "recording")  # nothing recorded until it is confirmed


def test_confirming_sign_in_saves_it_then_goes_straight_on_to_recording(env):
    b, reg, group, wf, calls, st, _out = env
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    card = _cards(wf.id, "sign_in")[0]
    st["confirmed"] = True
    _mark_signed_in(group)  # what the real finish command persists
    res = b.cmd_flow_sign_in_done({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert res["next"] == "record"
    assert _cards(wf.id, "sign_in")[0]["state"] == "saved"
    assert _cards(wf.id, "recording")[0]["state"] == "recording"
    assert _events(wf.id)[-2:] == ["Signed in to Billing Portal", "Recording started"]
    assert [c[0] for c in calls][-1] == "start_rec"


def test_cancelling_sign_in_clears_the_waiting_state(env):
    b, _reg, _g, wf, calls, _st, _out = env
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    card = _cards(wf.id, "sign_in")[0]
    b.cmd_flow_sign_in_cancel({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert _cards(wf.id, "sign_in")[0]["state"] == "cancelled"
    assert runtime_state.get_flags(wf.id) == {}


def test_pause_resume_finish_and_the_lifecycle_follows(env):
    b, reg, group, wf, calls, _st, out = env
    _mark_signed_in(group)
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    card = _cards(wf.id, "recording")[0]
    from conxa_core.storage.workflow_store import get_workflow

    assert lifecycle_for_workflow(get_workflow(wf.id))["substate"] == "recording"
    b.cmd_flow_pause_recording({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert reg.sessions["rec1"].paused and _cards(wf.id, "recording")[0]["state"] == "paused"
    assert lifecycle_for_workflow(get_workflow(wf.id))["substate"] == "paused"
    b.cmd_flow_resume_recording({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert not reg.sessions["rec1"].paused

    res = b.cmd_flow_finish_recording({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert res["event_count"] == 14
    done = _cards(wf.id, "recording")[0]
    assert done["state"] == "saved" and done["count"] == 14 and done["duration_seconds"] == 151
    assert "Recording saved" not in _events(wf.id)  # the card carries it
    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert (lc["stage"], lc["substate"]) == ("compile", "ready")
    # finishing twice is refused rather than saving twice
    with pytest.raises(_CommandError):
        b.cmd_flow_finish_recording({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")


def test_discard_saves_nothing_and_the_lifecycle_says_so(env):
    b, _reg, group, wf, calls, _st, _out = env
    _mark_signed_in(group)
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    card = _cards(wf.id, "recording")[0]
    b.cmd_flow_discard_recording({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert _cards(wf.id, "recording")[0]["state"] == "discarded"
    from conxa_core.storage.workflow_store import get_workflow

    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert (lc["stage"], lc["substate"]) == ("record", "discarded") and lc["allowed_actions"] == ["record"]
    assert "Recording discarded" not in _events(wf.id)


def test_a_browser_closed_by_hand_offers_to_save_what_was_captured(env):
    b, reg, group, wf, calls, _st, _out = env
    _mark_signed_in(group)
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    reg.sessions["rec1"].events = [{"a": 1}] * 9
    reg.sessions["rec1"].browser_open = False
    deadline = time.time() + 3
    while time.time() < deadline and _cards(wf.id, "recording")[0]["state"] == "recording":
        time.sleep(0.02)
    card = _cards(wf.id, "recording")[0]
    assert card["state"] == "browser_closed" and card["count"] == 9 and card["actions"] == ["finish", "discard"]
    assert "The browser closed before you finished" not in _events(wf.id)
    # "Save 9 actions" is the same finish command
    assert b.cmd_flow_finish_recording({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")["ok"]


def test_live_rows_are_emitted_while_recording_and_varies_hints_are_kept(env, monkeypatch):
    b, reg, group, wf, calls, _st, out = env
    _mark_signed_in(group)
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    hook = reg.sessions["rec1"].event_hook
    assert hook is not None

    class Ev:
        def model_dump(self, mode="json"):
            return {"action": {"action": "type", "value": "PO-44821"}, "target": {"tag": "input", "label_text": "PO number"},
                    "page": {}, "extras": {"sequence": 4}, "tab": {"id": "tab_1"}}

    hook(Ev())
    row_events = [e for e in out if e.get("phase") == "recording_event"]
    assert row_events and row_events[-1]["row"]["text"] == "Typed “PO-44821” into PO number"
    assert b.cmd_flow_set_varies({"session_id": "rec1", "sequence": 4, "varies": True}, "r")["varies"] == {"4": True}
    assert b.cmd_recording_rows({"session_id": "rec1"}, "r")["varies"] == {"4": True}
    assert b.cmd_flow_set_varies({"session_id": "rec1", "sequence": 4, "varies": None}, "r")["varies"] == {}


def _wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_signing_in_is_detected_without_pressing_the_button(env, monkeypatch):
    b, reg, group, wf, calls, st, _out = env
    monkeypatch.setattr("handlers.workflow_flow._SIGN_IN_WATCH_INTERVAL_S", 0.01)
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    sess = reg.sessions["auth1"]
    sess.current_url = "https://billing.example.test/login?next=/home"
    time.sleep(0.2)
    assert "finish_auth" not in [c[0] for c in calls]  # still on the login page: nothing to check

    st["confirmed"] = True
    _mark_signed_in(group)  # what the real finish command persists
    sess.current_url = "https://billing.example.test/home"
    assert _wait_for(lambda: _cards(wf.id, "sign_in")[0]["state"] == "saved")
    assert _wait_for(lambda: bool(_cards(wf.id, "recording")))
    assert "Signed in to Billing Portal — detected automatically" in _events(wf.id)
    assert [c[0] for c in calls].count("finish_auth") == 1


def test_an_early_automatic_check_is_silent_and_not_repeated_on_the_same_page(env, monkeypatch):
    b, reg, _group, wf, calls, _st, _out = env
    monkeypatch.setattr("handlers.workflow_flow._SIGN_IN_WATCH_INTERVAL_S", 0.01)
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    sess = reg.sessions["auth1"]
    sess.current_url = "https://billing.example.test/login"
    time.sleep(0.1)
    sess.current_url = "https://billing.example.test/consent?step=1"  # not signed in yet (confirmed=False)
    assert _wait_for(lambda: "finish_auth" in [c[0] for c in calls])
    time.sleep(0.2)
    assert [c[0] for c in calls].count("finish_auth") == 1  # once per page, not every tick
    card = _cards(wf.id, "sign_in")[0]
    assert card["state"] == "waiting" and not card.get("note")  # no "couldn't confirm" for an automatic try


def test_no_automatic_check_before_the_login_page_was_seen(env, monkeypatch):
    b, reg, _group, wf, calls, _st, _out = env
    monkeypatch.setattr("handlers.workflow_flow._SIGN_IN_WATCH_INTERVAL_S", 0.01)
    b.cmd_flow_begin({"workflow_id": wf.id}, "r")
    reg.sessions["auth1"].current_url = "https://billing.example.test/home"
    time.sleep(0.2)
    assert "finish_auth" not in [c[0] for c in calls]