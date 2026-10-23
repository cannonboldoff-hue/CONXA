"""BUILD-40: the test guard, the person-answered confirmation, the test card, auto-fix and approval
(fake runtime and no model — the runtime side is covered by runtime/test/unit/test_guard.js)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time

import pytest

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from conxa_core.config import settings  # noqa: E402
from conxa_core.storage.workflow_store import get_workflow, save_workflow, set_workflow_test_result  # noqa: E402
from conxa_compile.conversation import clarify_store, runtime_state, studio_settings, thread_store as ts  # noqa: E402
from conxa_compile.conversation.lifecycle import lifecycle_for_workflow  # noqa: E402
from handlers import test_flow  # noqa: E402
from handlers.protocol import _CommandError  # noqa: E402
from handlers.workflows import (  # noqa: E402
    _app_layer_supports,
    _drive_runtime_pauses,
    _extract_fork_pause,
    _extract_guard_pause,
    _extract_session_expired,
    _is_confirm_only_review_request,
)

CONFIRM_SCHEMA = {"type": "object", "required": ["confirmed"], "properties": {"confirmed": {"type": "boolean", "enum": [True]}}}


def _guard_result(step=3, run_id="run_1", remaining=2):
    return {
        "content": [{"type": "text", "text": f"Paused before a step. resume_from: {step} is what the review extractor would grab."}],
        "_meta": {"conxa/guard": {"step_index": step, "step_intent": "send_invoice", "remaining_irreversible": remaining, "run_id": run_id}},
    }


def _review_result(step=2, schema=CONFIRM_SCHEMA, prompt="Send the invoice?"):
    return {"content": [{"type": "text", "text": "Review"}],
            "_meta": {"conxa/ai_review": {"step_index": step, "prompt": prompt, "output_schema": schema}}}


DONE = {"content": [{"type": "text", "text": "Done. URL: https://x.test"}]}


class Calls:
    def __init__(self, results):
        self.results, self.args, self.cancelled, self.marks = list(results), [], [], []

    def call(self, args):
        self.args.append(args)
        return self.results.pop(0)

    def drive(self, first, **kw):
        defaults = dict(call=self.call, cancel_run=self.cancelled.append, answer_review=lambda r, c: {"answer": "yes"},
                        guard_hook=None, review_hook=None, guard_requested=True, previewed=False, mark=self.marks.append)
        defaults.update(kw)
        return _drive_runtime_pauses(first, {"skill": "s", "inputs": {}}, **defaults)


# ── helpers ─────────────────────────────────────────────────────────────────────────────────


def test_a_guard_pause_is_read_from_meta_only_never_from_prose():
    assert _extract_guard_pause(_guard_result())["step_index"] == 3
    assert _extract_guard_pause({"content": [{"type": "text", "text": "resume_from: 3"}]}) is None


def test_the_app_layer_must_say_it_can_guard(tmp_path):
    (tmp_path / "conxa-app" / "current").mkdir(parents=True)
    assert _app_layer_supports(tmp_path, "test_guard") is False  # no version.json at all
    (tmp_path / "conxa-app" / "current" / "version.json").write_text(json.dumps({"app_version": "app-v1"}))
    assert _app_layer_supports(tmp_path, "test_guard") is False  # an older engine: it would ignore the argument
    (tmp_path / "conxa-app" / "current" / "version.json").write_text(json.dumps({"capabilities": ["test_guard"]}))
    assert _app_layer_supports(tmp_path, "test_guard") is True


def test_only_a_yes_only_review_counts_as_confirm_only():
    assert _is_confirm_only_review_request({"output_schema": CONFIRM_SCHEMA})
    assert not _is_confirm_only_review_request({"output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}}})
    assert not _is_confirm_only_review_request({"output_schema": None})


# ── the pause loop ──────────────────────────────────────────────────────────────────────────


def test_no_pause_means_the_result_is_returned_untouched():
    c = Calls([])
    assert c.drive(DONE) == (DONE, None) and c.args == []


def test_run_rest_resumes_from_the_paused_step_with_the_guard_released():
    c = Calls([DONE])
    result, stopped = c.drive(_guard_result(step=3), guard_hook=lambda g: "run_rest")
    assert result is DONE and stopped is None
    assert c.args == [{"skill": "s", "inputs": {}, "resume_from": 3, "_guard_release": True}]
    assert c.cancelled == []


def test_ending_the_test_runs_nothing_and_frees_the_parked_browser():
    c = Calls([])
    result, stopped = c.drive(_guard_result(run_id="run_9"), guard_hook=lambda g: "end_here")
    assert stopped["step_index"] == 3 and c.args == [] and c.cancelled == ["run_9"]


def test_no_decision_maker_fails_closed():
    c = Calls([])
    _, stopped = c.drive(_guard_result(), guard_hook=None)
    assert stopped is not None and c.args == []  # an unattended test never crosses the line


def test_a_persons_yes_to_a_confirmation_also_releases_the_guard_in_one_call():
    c = Calls([DONE])
    asked: list = []
    result, stopped = c.drive(_review_result(step=2), review_hook=lambda r: asked.append(r) or True)
    assert result is DONE and stopped is None and len(asked) == 1
    assert c.args == [{"skill": "s", "inputs": {}, "resume_from": 2, "review_results": {"2": {"confirmed": True}}, "_guard_release": True}]


def test_a_persons_no_ends_the_test_and_nothing_is_sent():
    c = Calls([])
    _, stopped = c.drive(_review_result(), review_hook=lambda r: False)
    assert stopped["declined"] is True and c.args == []


def test_a_preview_run_never_asks_to_release_a_guard_it_does_not_have():
    c = Calls([DONE])
    c.drive(_review_result(), review_hook=lambda r: True, previewed=True)
    assert "_guard_release" not in c.args[0]


def test_an_ordinary_review_is_still_answered_by_the_sandbox_not_the_person():
    free = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
    c = Calls([DONE])
    person: list = []
    c.drive(_review_result(schema=free), review_hook=lambda r: person.append(r) or True, answer_review=lambda r, content: {"answer": "ok"})
    assert person == [] and c.args[0]["review_results"] == {"2": {"answer": "ok"}}
    assert "_guard_release" not in c.args[0]


def test_a_misbehaving_run_cannot_loop_forever():
    free = {"type": "object", "properties": {"answer": {"type": "string"}}}
    c = Calls([_review_result(schema=free)] * 20)
    with pytest.raises(RuntimeError, match="paused"):
        c.drive(_review_result(schema=free))


# ── the conversation's test card ────────────────────────────────────────────────────────────


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    runtime_state.clear_all()
    test_flow._waiters.clear()
    spec = importlib.util.spec_from_file_location("cbackend_tflow", os.path.join(_PY_DIR, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from handlers import protocol

    out: list[dict] = []
    mod._write = out.append
    protocol._write = out.append
    b = mod.Backend()
    monkeypatch.setattr(b, "_tf_ensure_built", lambda wf, rid: None)
    monkeypatch.setattr(b, "_install_proxy_router", lambda *a, **k: None)
    from conxa_core.storage.group_store import create_group
    from conxa_core.storage.json_store import write_skill
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow

    g = create_group("Finance Ops")
    wf = create_workflow("Create invoice", "https://billing.example.test/new", group_id=g.id)
    skill_id = "skill_t1"
    steps = [
        {"action": {"action": "navigate"}, "intent": "open_new_invoice", "validation": {"assertions": []}},
        {"action": {"action": "type"}, "intent": "enter_po_number", "value": "{{po_number}}", "input_binding": "po_number", "validation": {"assertions": []}},
        {"action": {"action": "click"}, "intent": "send_invoice", "consequence": "irreversible", "validation": {"assertions": []}},
    ]
    write_skill(skill_id, {"meta": {"version": 3}, "skills": [{"steps": steps}], "inputs": [{"id": "po_number", "type": "text"}, {"id": "date", "type": "text", "default": "2026-11-15", "required": False}]})
    w = get_workflow(wf.id)
    w.session_id, w.recording_status, w.skill_id = "s", "compiled", skill_id
    save_workflow(w)
    clarify_store.save(wf.id, {"questions": [{"id": "values", "kind": "values", "status": "answered", "recorded": {"po_number": "PO-44821"}}]})
    return b, wf, out


def _cards(wf_id, card_type):
    return [i for i in ts.load(f"wf:{wf_id}", limit=200)["items"] if i["kind"] == "card" and i["card_type"] == card_type]


def _fake_test(b, *, steps, outcome="passed", error=None, guard_info=None, confirm=None):
    """A stand-in for cmd_test_workflow that drives the hooks the way the real one does."""
    def fake(payload, rid):
        hook = payload["_phase_hook"]
        for i, (intent, destructive) in enumerate(steps):
            if guard_info and destructive and payload.get("guard"):
                if payload["_guard_hook"](guard_info) != "run_rest":
                    return {"status": "stopped_at_guard", "stopped_intent": intent, "stopped_at": i, "declined": False}
            hook({"phase": f"step_{i}_click_start", "step": i, "step_total": len(steps), "intent": intent, "destructive": destructive})
            if error and i == len(steps) - 1:
                raise _CommandError("workflow_test_failed", error)
        return {"status": outcome}
    b.cmd_test_workflow = fake


def test_prepare_prefills_recorded_values_and_names_what_cant_be_undone(env):
    b, wf, _out = env
    res = b.cmd_flow_test_prepare({"workflow_id": wf.id}, "r")
    card = _cards(wf.id, "test_inputs")[0]
    assert card["card_id"] == res["card_id"] and card["state"] == "open" and card["guard"] is True
    ins = {i["id"]: i for i in card["inputs"]}
    assert ins["po_number"]["value"] == "PO-44821" and ins["po_number"]["required"] is True
    assert ins["date"]["value"] == "2026-11-15" and ins["date"]["required"] is False
    assert card["irreversible"] == ["Send invoice"] and card["version"] == 3 and card["step_count"] == 3
    # asking again reuses the open card rather than stacking a second
    assert b.cmd_flow_test_prepare({"workflow_id": wf.id}, "r")["card_id"] == res["card_id"]
    assert len(_cards(wf.id, "test_inputs")) == 1


def test_a_required_input_must_be_filled_and_a_started_test_cannot_start_twice(env, monkeypatch):
    b, wf, _out = env
    card_id = b.cmd_flow_test_prepare({"workflow_id": wf.id}, "r")["card_id"]
    with pytest.raises(_CommandError) as e:
        b.cmd_flow_test_run({"workflow_id": wf.id, "card_id": card_id, "inputs": {"po_number": "  "}}, "r")
    assert e.value.code == "inputs_required" and "Po number" in e.value.message
    monkeypatch.setattr(b, "_tf_background", lambda *a: None)
    assert b.cmd_flow_test_run({"workflow_id": wf.id, "card_id": card_id, "inputs": {"po_number": "PO-1"}}, "r")["ok"]
    assert _cards(wf.id, "test_inputs")[0]["state"] == "submitted"
    with pytest.raises(_CommandError):
        b.cmd_flow_test_run({"workflow_id": wf.id, "card_id": card_id, "inputs": {"po_number": "PO-1"}}, "r")


def test_the_guard_pauses_the_card_and_run_rest_lets_it_finish(env):
    b, wf, _out = env
    _fake_test(b, steps=[("open_new_invoice", False), ("enter_po_number", False), ("send_invoice", True)],
               guard_info={"step_index": 2, "step_intent": "send_invoice", "remaining": 1, "run_id": "r1"})
    outcome: list[str] = []
    t = threading.Thread(target=lambda: outcome.append(b._tf_run_test(wf.id, {"po_number": "PO-1"}, guard=True, show_browser=False, attempt=1, auto=False)), daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not (_cards(wf.id, "test_run") and _cards(wf.id, "test_run")[0]["state"] == "waiting_guard"):
        time.sleep(0.02)
    paused = _cards(wf.id, "test_run")[0]
    assert paused["state"] == "waiting_guard" and paused["paused_before"] == "Send invoice" and paused["ran"] == 2
    assert paused["actions"] == ["run_rest", "end_here"]
    assert [r["status"] for r in paused["rows"]] == ["done", "done"]  # nothing past the line has started
    assert lifecycle_for_workflow(get_workflow(wf.id))["substate"] == "running"
    assert b.cmd_flow_test_decision({"workflow_id": wf.id, "decision": "run_rest"}, "r")["ok"]
    t.join(5)
    assert outcome == ["passed"]
    done = _cards(wf.id, "test_run")[0]
    assert done["state"] == "passed" and done["irreversible_ran"] is True and done["actions"] == ["approve", "test_again"]
    assert [r["name"] for r in done["rows"]] == ["Open new invoice", "Enter po number", "Send invoice"]
    assert runtime_state.get_flags(wf.id) == {}


def test_ending_at_the_guard_is_not_a_pass_and_says_nothing_was_sent(env):
    b, wf, _out = env
    _fake_test(b, steps=[("open_new_invoice", False), ("send_invoice", True)],
               guard_info={"step_index": 1, "step_intent": "send_invoice", "remaining": 1, "run_id": "r1"})
    t = threading.Thread(target=lambda: b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False), daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not test_flow._waiters:
        time.sleep(0.02)
    b.cmd_flow_test_decision({"workflow_id": wf.id, "decision": "end_here"}, "r")
    t.join(5)
    card = _cards(wf.id, "test_run")[0]
    assert card["state"] == "ended_before_irreversible" and card["stopped_before"] == "Send invoice"
    assert get_workflow(wf.id).last_test_status == "never"  # "passed up to X" is not a pass


def test_a_failure_says_what_ran_and_only_claims_nothing_was_sent_when_true(env):
    b, wf, _out = env
    _fake_test(b, steps=[("open_new_invoice", False), ("enter_po_number", False)], error="The customer list didn't appear within 5 seconds.")
    assert b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False) == "failed"
    card = _cards(wf.id, "test_run")[0]
    assert card["state"] == "failed" and card["failed_step"] == "Enter po number" and card["nothing_sent"] is True
    assert [r["status"] for r in card["rows"]] == ["done", "failed"]

    # a failure at (or after) a step that can't be undone must NOT claim nothing was sent
    _fake_test(b, steps=[("open_new_invoice", False), ("send_invoice", True)], error="The page closed.")
    assert b._tf_run_test(wf.id, {}, guard=False, show_browser=False, attempt=1, auto=False) == "failed"
    assert _cards(wf.id, "test_run")[1]["nothing_sent"] is False


def test_stopping_by_hand_is_its_own_state(env):
    b, wf, _out = env
    _fake_test(b, steps=[("open_new_invoice", False)], outcome="cancelled")
    assert b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False) == "stopped"
    assert _cards(wf.id, "test_run")[0]["state"] == "stopped"


def test_a_preview_on_an_old_engine_is_not_a_pass(env):
    b, wf, _out = env
    _fake_test(b, steps=[("open_new_invoice", False)], outcome="previewed")
    assert b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False) == "previewed"
    assert _cards(wf.id, "test_run")[0]["state"] == "previewed"


def test_approval_needs_a_passing_test_and_never_publishes(env, monkeypatch):
    b, wf, _out = env
    _fake_test(b, steps=[("open_new_invoice", False)])
    b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False)
    card = _cards(wf.id, "test_run")[0]
    # the real run records the pass on the workflow; the fake doesn't, so do what cmd_test_workflow does
    with pytest.raises(_CommandError) as e:
        b.cmd_flow_approve({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert e.value.code == "test_required"
    set_workflow_test_result(wf.id, status="passed", inputs={})
    published: list = []
    monkeypatch.setattr(b, "cmd_publish_skill_pack", lambda p, r: published.append(p))
    def sign_off(p, r):
        w = get_workflow(wf.id)
        w.signed_off = True
        save_workflow(w)
        return {"signed_off": True, "built": True}

    monkeypatch.setattr(b, "cmd_sign_off_workflow", sign_off)
    assert b.cmd_flow_approve({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")["approved"] is True
    assert published == []  # approving never publishes
    after = _cards(wf.id, "test_run")[0]
    assert after["approved"] is True and after["actions"] == ["test_again"]
    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert (lc["stage"], lc["substate"]) == ("approve", "approved")


def test_the_autofix_switch_defaults_on_and_is_stored_locally(env):
    b, _wf, _out = env
    assert b.cmd_get_studio_settings({}, "r")["settings"] == {"autofix_enabled": True}
    assert b.cmd_set_studio_settings({"autofix_enabled": False, "bogus": 1}, "r")["settings"] == {"autofix_enabled": False}
    assert studio_settings.load()["autofix_enabled"] is False
    assert b.cmd_set_studio_settings({"autofix_enabled": "no"}, "r")["settings"]["autofix_enabled"] is False  # wrong type ignored


# ── auto-fix ────────────────────────────────────────────────────────────────────────────────


def _proposal(i=1):
    return {"id": f"p{i}", "step_key": "k", "command": "patch_step", "field": "validation.wait_for", "why": "The list is slower.",
            "patch": {"validation": {"wait_for": {"type": "none"}}}, "preview": {"before": "5 s", "after": "15 s"}}


def test_autofix_makes_one_gated_change_at_a_time_retests_with_the_guard_and_stops_at_three(env, monkeypatch):
    b, wf, _out = env
    runs: list[dict] = []

    def fake_test(payload, rid):
        runs.append(payload)
        payload["_phase_hook"]({"phase": "step_0_click_start", "step": 0, "step_total": 1, "intent": "choose_customer", "destructive": False})
        raise _CommandError("workflow_test_failed", "Still failing.")

    b.cmd_test_workflow = fake_test
    applied: list[str] = []
    monkeypatch.setattr(b, "_tf_propose_fix", lambda wid: [_proposal(len(applied) + 1)])
    monkeypatch.setattr(b, "_card_pending_change", lambda thread, card, action, body, rid, sink:
                        applied.append(body.get("source")) or {"ok": True})
    b._tf_autofix(wf.id, {"po_number": "PO-1"}, False)
    assert applied == ["auto_fix", "auto_fix", "auto_fix"]  # one change per attempt, every one attributed to auto-fix
    assert all(r["guard"] is True for r in runs) and len(runs) == 3  # re-tests always run with the guard on
    af = _cards(wf.id, "autofix")[0]
    assert af["state"] == "stopped" and af["reason"] == "Three attempts didn’t fix it." and af["actions"] == ["resume", "human_edit"]
    assert len(af["changes"]) == 3
    w = get_workflow(wf.id)
    w.last_test_status = "failed"  # what the real cmd_test_workflow records on a failure
    save_workflow(w)
    lc = lifecycle_for_workflow(w)
    assert lc["substate"] == "autofix_stopped" and lc["allowed_actions"][0] == "resume_autofix"
    assert runtime_state.get_flags(wf.id) == {}


def test_autofix_stops_when_the_retest_passes(env, monkeypatch):
    b, wf, _out = env
    results = iter(["raise", "ok"])

    def fake_test(payload, rid):
        payload["_phase_hook"]({"phase": "step_0_click_start", "step": 0, "step_total": 1, "intent": "choose_customer", "destructive": False})
        if next(results) == "raise":
            raise _CommandError("workflow_test_failed", "x")
        return {"status": "passed"}

    b.cmd_test_workflow = fake_test
    monkeypatch.setattr(b, "_tf_propose_fix", lambda wid: [_proposal()])
    monkeypatch.setattr(b, "_card_pending_change", lambda *a: {"ok": True})
    # the first run is the failure that starts auto-fix
    assert b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False) == "failed"
    b._tf_autofix(wf.id, {}, False)
    af = _cards(wf.id, "autofix")[0]
    assert af["state"] == "passed" and af["attempts"] == 2
    assert any(c["state"] == "passed" for c in _cards(wf.id, "test_run"))


def test_autofix_stops_when_there_is_no_safe_fix_or_the_change_cannot_apply(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr(b, "_tf_propose_fix", lambda wid: [])
    b._tf_autofix(wf.id, {}, False)
    assert _cards(wf.id, "autofix")[0]["reason"].startswith("I couldn’t find a fix I’m sure about")
    monkeypatch.setattr(b, "_tf_propose_fix", lambda wid: [_proposal()])
    monkeypatch.setattr(b, "_card_pending_change", lambda *a: {"ok": False, "message": "The workflow changed."})
    b._tf_autofix(wf.id, {}, False)
    assert _cards(wf.id, "autofix")[1]["reason"] == "The workflow changed."


def test_the_stop_button_ends_autofix_between_steps(env, monkeypatch):
    b, wf, _out = env

    def propose(wid):
        test_flow._autofix_stop[wid].set()  # the person presses Stop while it is thinking
        return [_proposal()]

    monkeypatch.setattr(b, "_tf_propose_fix", propose)
    monkeypatch.setattr(b, "_card_pending_change", lambda *a: pytest.fail("must not apply after Stop"))
    b.cmd_test_workflow = lambda p, r: pytest.fail("must not re-test after Stop")
    b._tf_autofix(wf.id, {}, False)
    assert _cards(wf.id, "autofix")[0]["reason"] == "You pressed Stop."


# ── BUILD-43: a sign-in that expired during a test pauses it instead of failing it ───────────────

def test_a_session_expiry_is_read_from_meta_only():
    meta = {"content": [{"type": "text", "text": "x"}], "_meta": {"conxa/session_expired": {"run_id": "r1", "step_index": 4}}}
    assert _extract_session_expired(meta) == {"step_index": 4, "run_id": "r1"}
    assert _extract_session_expired({"content": [{"type": "text", "text": "Your session expired. Sign in, then run again."}]}) is None


def test_an_expired_sign_in_pauses_the_test_and_never_starts_auto_fix(env):
    b, wf, _out = env
    _fake_test(b, steps=[("open_new_invoice", False), ("enter_po_number", False)], outcome="needs_sign_in")
    assert b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False) == "needs_sign_in"
    card = _cards(wf.id, "test_run")[-1]
    assert card["state"] == "needs_sign_in" and card["actions"] == ["test_again"]
    assert lifecycle_for_workflow(get_workflow(wf.id))["substate"] == "sign_in_expired"
    # auto-fix is only ever started by a plain failure
    started = []
    b._tf_autofix = lambda *a, **k: started.append(1)
    monkeypatched = b._tf_run_test
    b._tf_run_test = lambda *a, **k: "needs_sign_in"
    b._tf_background(wf.id, {}, True, False)
    b._tf_run_test = monkeypatched
    assert started == []


def test_the_next_test_clears_the_expired_sign_in_flag(env):
    b, wf, _out = env
    _fake_test(b, steps=[("open_new_invoice", False)], outcome="needs_sign_in")
    b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False)
    _fake_test(b, steps=[("open_new_invoice", False)], outcome="passed")
    assert b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False) == "passed"
    assert lifecycle_for_workflow(get_workflow(wf.id))["substate"] != "sign_in_expired"


def test_cloud_ping_reports_offline_instead_of_raising(env, monkeypatch):
    b, _wf, _out = env
    import urllib.request

    def boom(*a, **k):
        raise OSError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert b.cmd_cloud_ping({}, "r") == {"online": False, "detail": "OSError"}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    out = b.cmd_cloud_ping({}, "r")
    assert out["online"] is True and isinstance(out["ms"], int)


# ── popup decisions (outcome forks): a person answers, never the model ───────────────────────


def _fork_result(step=4, unknown=False):
    return {
        "content": [{"type": "text", "text": f"ASK THE USER. resume_from: {step} is what the review extractor would grab."}],
        "_meta": {"conxa/fork": {
            "step_index": step, "title": "Upload options", "message": "report.pdf already exists.",
            "reason": "consequential_unapproved", "unknown": unknown, "suggested": "replace", "requires_human": True,
            "choices": [{"id": "replace", "label": "Replace", "role": "consequential"},
                        {"id": "keep_both", "label": "Keep both", "role": "neutral"},
                        {"id": "__stop__", "label": "Stop the run", "role": "stop"}],
        }},
    }


def test_a_fork_pause_is_read_from_meta_only():
    assert _extract_fork_pause(_fork_result())["step_index"] == 4
    assert _extract_fork_pause(_fork_result())["choices"][0]["id"] == "replace"
    assert _extract_fork_pause({"content": [{"type": "text", "text": "resume_from: 4"}]}) is None


def test_the_persons_choice_resumes_the_fork_step_with_that_choice():
    c = Calls([DONE])
    result, stopped = c.drive(_fork_result(step=4), fork_hook=lambda f: "keep_both")
    assert result is DONE and stopped is None
    assert c.args == [{"skill": "s", "inputs": {}, "resume_from": 4, "review_results": {"4": {"choice": "keep_both"}}}]


def test_a_fork_pause_never_reaches_the_model_answerer():
    asked = []
    c = Calls([DONE])
    c.drive(_fork_result(), fork_hook=lambda f: "keep_both", answer_review=lambda r, content: asked.append(r) or {"answer": "x"})
    assert asked == []


def test_with_no_one_to_ask_the_test_ends_at_the_popup_and_the_model_is_not_consulted():
    asked = []
    c = Calls([])
    result, stopped = c.drive(_fork_result(), fork_hook=None, answer_review=lambda r, content: asked.append(r))
    assert stopped["declined"] is True and c.args == [] and asked == []


def test_choosing_stop_or_an_unlisted_choice_ends_the_test_without_resuming():
    for answer in ("__stop__", "delete_everything"):
        c = Calls([])
        _, stopped = c.drive(_fork_result(), fork_hook=lambda f, a=answer: a)
        assert stopped["declined"] is True and c.args == []
