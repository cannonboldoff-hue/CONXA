"""Gap analysis G11: an interrupted test is said to be interrupted; a sign-in that expired mid-test is
fixed and the same test carries on, instead of dropping the person back at the start."""

from __future__ import annotations

import threading
import time

from conxa_compile.conversation import harness, runtime_state, thread_store as ts
from tests.test_compile_flow import _fake_compile, _github_doc, env  # noqa: F401  (the fixture)


def _compiled(env):  # noqa: F811
    b, wf, out = env
    _fake_compile(b, wf, doc_fn=_github_doc)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    return b, wf


def _card(wf, card_type):
    return [i for i in ts.load(f"wf:{wf.id}", limit=200)["items"] if i["kind"] == "card" and i["card_type"] == card_type]


def _age(wf, card_id, seconds):
    """Backdate a card so it looks like it was posted `seconds` ago."""
    import json
    from datetime import datetime, timedelta, timezone

    path = ts.thread_path(f"wf:{wf.id}")
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        d = json.loads(line)
        if d.get("card_id") == card_id:
            d["ts"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        out.append(json.dumps(d))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_a_test_that_claims_to_run_in_a_studio_that_is_not_testing_is_marked_interrupted(env):  # noqa: F811
    b, wf = _compiled(env)
    stale = harness.post_card(f"wf:{wf.id}", "test_run", "waiting_guard", actions=["stop"])
    fresh = harness.post_card(f"wf:{wf.id}", "test_run", "running", actions=["stop"])
    done = harness.post_card(f"wf:{wf.id}", "test_run", "passed", actions=["approve"])
    _age(wf, stale["card_id"], 120)
    _age(wf, done["card_id"], 120)
    b._reconcile_interrupted(wf.id)
    states = {c["card_id"]: c["state"] for c in _card(wf, "test_run")}
    assert states[stale["card_id"]] == "interrupted"
    assert states[fresh["card_id"]] == "running"        # a test that has only just begun is left alone
    assert states[done["card_id"]] == "passed"           # finished tests are never touched


def test_a_test_that_really_is_running_is_never_called_interrupted(env):  # noqa: F811
    b, wf = _compiled(env)
    card = harness.post_card(f"wf:{wf.id}", "test_run", "running", actions=["stop"])
    _age(wf, card["card_id"], 600)
    runtime_state.set_flags(wf.id, testing=True)
    b._reconcile_interrupted(wf.id)
    assert _card(wf, "test_run")[0]["state"] == "running"


def test_loading_a_thread_reconciles_before_it_returns(env):  # noqa: F811
    b, wf = _compiled(env)
    card = harness.post_card(f"wf:{wf.id}", "test_run", "running", actions=["stop"])
    _age(wf, card["card_id"], 600)
    page = b.cmd_thread_load({"thread_id": f"wf:{wf.id}"}, "r")
    shown = next(i for i in page["items"] if i.get("card_id") == card["card_id"])
    assert shown["state"] == "interrupted"


def test_sign_in_then_test_signs_in_first_and_never_starts_a_recording(env, monkeypatch):  # noqa: F811
    b, wf = _compiled(env)
    app = type("A", (), {"id": "a1", "name": "Billing"})()
    opened: list = []
    monkeypatch.setattr(b, "_flow_missing_apps", lambda w: [app])
    monkeypatch.setattr(b, "_flow_open_sign_in", lambda w, a, rid, sink: opened.append(a.id) or {"next": "sign_in"})
    monkeypatch.setattr(b, "_flow_open_recording", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not record")))
    assert b.cmd_flow_sign_in_then_test({"workflow_id": wf.id}, "r") == {"next": "sign_in"}
    assert opened == ["a1"] and runtime_state.get_flags(wf.id).get("retest_after_sign_in") is True


def test_once_signed_in_the_same_test_runs_again_with_the_same_inputs(env, monkeypatch):  # noqa: F811
    b, wf = _compiled(env)
    from conxa_core.storage.workflow_store import get_workflow, save_workflow

    w = get_workflow(wf.id)
    w.last_test_inputs = {"repository": "templates"}
    save_workflow(w)
    started: list = []
    ran = threading.Event()
    monkeypatch.setattr(b, "_tf_background", lambda *args: (started.append(args), ran.set()))
    monkeypatch.setattr(b, "_flow_missing_apps", lambda w: [])
    runtime_state.set_flags(wf.id, sign_in_expired=True)
    assert b.cmd_flow_sign_in_then_test({"workflow_id": wf.id}, "r") == {"next": "test"}
    assert ran.wait(3)
    assert started == [(wf.id, {"repository": "templates"}, True, True)]           # guard stays on
    flags = runtime_state.get_flags(wf.id)
    assert "retest_after_sign_in" not in flags and "sign_in_expired" not in flags


def test_cancelling_the_sign_in_forgets_that_a_retest_was_wanted(env, monkeypatch):  # noqa: F811
    b, wf = _compiled(env)
    runtime_state.set_flags(wf.id, retest_after_sign_in=True, signing_in=True)
    card = harness.post_card(f"wf:{wf.id}", "sign_in", "waiting", refs={"session_id": "s"}, actions=["done", "cancel"])
    monkeypatch.setattr(b, "cmd_cancel_group_app_auth", lambda *a, **k: {})
    b.cmd_flow_sign_in_cancel({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert "retest_after_sign_in" not in runtime_state.get_flags(wf.id)


def test_a_sign_in_said_in_chat_on_a_compiled_workflow_continues_the_test_not_a_recording(env, monkeypatch):  # noqa: F811
    b, wf = _compiled(env)
    runtime_state.set_flags(wf.id, sign_in_expired=True)  # the lifecycle only offers sign-in once a saved one has stopped working
    monkeypatch.setattr(b, "cmd_flow_sign_in_then_test", lambda payload, rid: {"next": "sign_in"})
    monkeypatch.setattr(b, "cmd_flow_begin", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not begin a recording")))
    res = b._run_flow_action(wf.id, "sign_in", {}, "r", lambda e: None)
    assert res["ok"] and "test again" in res["message"]
