"""BUILD-40: the test guard, the person-answered confirmation, the test card, find-the-cause and approval
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
    _is_confirm_only_span_request,
)

CONFIRM_SCHEMA = {"type": "object", "required": ["confirmed"], "properties": {"confirmed": {"type": "boolean", "enum": [True]}}}


def _guard_result(step=3, run_id="run_1", remaining=2):
    return {
        "content": [{"type": "text", "text": f"Paused before a step. resume_from: {step} is what the review extractor would grab."}],
        "_meta": {"conxa/guard": {"step_index": step, "step_intent": "send_invoice", "remaining_irreversible": remaining, "run_id": run_id}},
    }


QUESTION_ONLY = ["done", "give_up", "wait", "read_text"]


def _span_result(step=2, schema=CONFIRM_SCHEMA, prompt="Send the invoice?", allowed=None, needs_confirm=None):
    meta = {"step_index": step, "goal": prompt, "allowed": QUESTION_ONLY if allowed is None else allowed,
            "answer_schema": schema, "candidates": [{"i": 0, "tag": "button", "name": "Send"}]}
    if needs_confirm:
        meta["needs_user_confirmation"] = needs_confirm
    return {"content": [{"type": "text", "text": "Span"}], "_meta": {"conxa/agent_span": meta}}


_review_result = _span_result  # the confirm-only question is the old "review"


DONE = {"content": [{"type": "text", "text": "Done. URL: https://x.test"}]}


class Calls:
    def __init__(self, results):
        self.results, self.args, self.cancelled, self.marks = list(results), [], [], []

    def call(self, args):
        self.args.append(args)
        return self.results.pop(0)

    def drive(self, first, **kw):
        defaults = dict(call=self.call, cancel_run=self.cancelled.append,
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


def test_only_a_yes_only_question_span_counts_as_confirm_only():
    assert _is_confirm_only_span_request({"allowed": QUESTION_ONLY, "answer_schema": CONFIRM_SCHEMA})
    assert not _is_confirm_only_span_request({"allowed": QUESTION_ONLY, "answer_schema": {"type": "object", "properties": {"answer": {"type": "string"}}}})
    assert not _is_confirm_only_span_request({"allowed": QUESTION_ONLY, "answer_schema": None})
    assert not _is_confirm_only_span_request({"allowed": ["click", *QUESTION_ONLY], "answer_schema": CONFIRM_SCHEMA}), "a span that can act is not a plain question"


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
    assert c.args == [{"skill": "s", "inputs": {}, "resume_from": 2,
                       "span_actions": {"2": {"kind": "done", "answer": {"confirmed": True}}}, "_guard_release": True}]


def test_a_persons_no_ends_the_test_and_nothing_is_sent():
    c = Calls([])
    _, stopped = c.drive(_review_result(), review_hook=lambda r: False)
    assert stopped["declined"] is True and c.args == []


def test_a_preview_run_never_asks_to_release_a_guard_it_does_not_have():
    c = Calls([DONE])
    c.drive(_review_result(), review_hook=lambda r: True, previewed=True)
    assert "_guard_release" not in c.args[0]


def test_an_ordinary_span_is_answered_by_the_sandbox_not_the_person():
    free = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
    c = Calls([DONE])
    person: list = []
    c.drive(_span_result(schema=free), review_hook=lambda r: person.append(r) or True,
            answer_span=lambda span, content: {"kind": "done", "answer": {"answer": "ok"}})
    assert person == [] and c.args[0]["span_actions"] == {"2": {"kind": "done", "answer": {"answer": "ok"}}}
    assert "_guard_release" not in c.args[0]


def test_with_no_model_to_ask_the_test_ends_at_the_span():
    c = Calls([])
    _, stopped = c.drive(_span_result(schema=None, allowed=["click", *QUESTION_ONLY]), answer_span=None)
    assert stopped["declined"] is True and c.args == []


def test_a_button_the_runtime_wants_a_yes_for_goes_to_the_person_and_is_resent_confirmed():
    asked: list = []
    c = Calls([_span_result(schema=None, allowed=["click", *QUESTION_ONLY], needs_confirm="Send invoice"), DONE])
    result, stopped = c.drive(
        _span_result(schema=None, allowed=["click", *QUESTION_ONLY]),
        answer_span=lambda span, content: {"kind": "click", "candidate_index": 0},
        review_hook=lambda r: asked.append(r["prompt"]) or True,
    )
    assert result is DONE and stopped is None
    assert "Send invoice" in asked[0]
    assert c.args[0]["span_actions"] == {"2": {"kind": "click", "candidate_index": 0}}
    assert c.args[1]["span_actions"] == {"2": {"kind": "click", "candidate_index": 0, "user_confirmed": True}}


def test_the_model_can_never_confirm_for_the_person():
    asked: list = []
    c = Calls([_span_result(schema=None, allowed=["click", *QUESTION_ONLY], needs_confirm="Send invoice")])
    _, stopped = c.drive(
        _span_result(schema=None, allowed=["click", *QUESTION_ONLY]),
        answer_span=lambda span, content: {"kind": "click", "candidate_index": 0, "user_confirmed": True},
        review_hook=lambda r: asked.append(r) or False,   # the person says no
    )
    assert stopped["declined"] is True and len(c.args) == 1, "nothing was resent after the no"
    # and with nobody to ask at all the test also ends rather than letting the model decide
    c2 = Calls([_span_result(schema=None, allowed=["click", *QUESTION_ONLY], needs_confirm="Send invoice")])
    _, stopped2 = c2.drive(_span_result(schema=None, allowed=["click", *QUESTION_ONLY]),
                           answer_span=lambda span, content: {"kind": "click", "candidate_index": 0}, review_hook=None)
    assert stopped2["declined"] is True


def test_a_misbehaving_run_cannot_loop_forever():
    c = Calls([_span_result(schema=None, allowed=["scroll", *QUESTION_ONLY])] * 200)
    with pytest.raises(RuntimeError, match="rounds"):
        c.drive(_span_result(schema=None, allowed=["scroll", *QUESTION_ONLY]),
                answer_span=lambda span, content: {"kind": "scroll"})


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
        {"action": {"action": "navigate"}, "intent": "open_new_invoice", "url": "https://billing.example.test/new?on={{date}}", "validation": {"assertions": []}},
        {"action": {"action": "type"}, "intent": "enter_po_number", "value": "{{po_number}}", "input_binding": "po_number", "validation": {"assertions": []}},
        {"action": {"action": "click"}, "intent": "send_invoice", "consequence": "irreversible", "validation": {"assertions": []}},
    ]
    write_skill(skill_id, {"meta": {"version": 3}, "skills": [{"steps": steps}], "inputs": [{"id": "po_number", "type": "text"}, {"id": "date", "type": "text", "default": "2026-11-15", "optional": True}]})
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
    # asking again folds the old card away (state "replaced") and puts a fresh one at the bottom, so pressing
    # "Test it" again visibly does something; only one stays open (fd600d9).
    again = b.cmd_flow_test_prepare({"workflow_id": wf.id}, "r")["card_id"]
    assert again != res["card_id"]
    open_cards = [c for c in _cards(wf.id, "test_inputs") if c["state"] == "open"]
    assert [c["card_id"] for c in open_cards] == [again]


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


def test_ending_at_a_popup_is_recorded_as_a_popup_stop_with_its_step_and_reason(env):
    b, wf, _out = env

    def fake(payload, rid):
        payload["_phase_hook"]({"phase": "step_0_click_start", "step": 0, "step_total": 2, "intent": "open_new_invoice", "destructive": False})
        choice = payload["_fork_hook"]({"title": "Upload options", "message": "", "choices": [], "suggested": None,
                                        "unknown": False, "step_index": 1, "reason": "ask_human"})
        assert choice == "__stop__"
        return {"status": "stopped_at_guard", "stopped_intent": "Upload options", "stopped_at": 1, "declined": True}

    b.cmd_test_workflow = fake
    t = threading.Thread(target=lambda: b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False), daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not test_flow._waiters:
        time.sleep(0.02)
    b.cmd_flow_test_decision({"workflow_id": wf.id, "decision": "end_here"}, "r")
    t.join(5)
    card = _cards(wf.id, "test_run")[0]
    assert card["state"] == "ended_at_popup" and card["popup"] == "Upload options"
    assert card["step_index"] == 1 and card["reason"] == "ask_human"          # the ending is data, not just a chat line
    assert "stopped_before" not in card                                       # never reported as an irreversible-step stop
    assert get_workflow(wf.id).last_test_status == "never"


def test_a_step_inside_a_popup_answer_never_becomes_a_row_or_the_failed_step(env):
    b, wf, _out = env

    def fake(payload, rid):
        hook = payload["_phase_hook"]
        hook({"phase": "step_0_click_start", "step": 0, "path": [0], "step_total": 2, "intent": "open_new_invoice", "destructive": False})
        hook({"phase": "step_1_outcome_fork_start", "step": 1, "path": [1], "step_total": 2, "intent": "handle_popup_decision", "destructive": False})
        # the chosen option's own click reports itself against plan step 1, not as a new row (or as step 0 again)
        hook({"phase": "inner_1_click_start", "step": 1, "path": [1, 0], "step_total": 2, "intent": "click_upload", "destructive": False})
        raise _CommandError("workflow_test_failed", "The popup is still open.")

    b.cmd_test_workflow = fake
    assert b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False) == "failed"
    card = _cards(wf.id, "test_run")[0]
    assert [r["index"] for r in card["rows"]] == [0, 1]
    assert card["failed_step"] == "Handle popup decision"


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


# ── find the cause (formerly auto-fix) ───────────────────────────────────────────────────────────


def _proposal(i=1):
    return {"id": f"p{i}", "step_key": "k", "command": "patch_step", "field": "validation.wait_for", "why": "The list is slower.",
            "patch": {"validation": {"wait_for": {"type": "none"}}}, "preview": {"before": "5 s", "after": "15 s"}}


def _said(wf_id):
    return [i["text"] for i in ts.load(f"wf:{wf_id}", limit=200)["items"] if i["kind"] == "assistant_message"]


def test_a_failure_explains_the_cause_and_proposes_one_fix_but_applies_nothing(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr(b, "_tf_propose_fix", lambda wid, sink=None: ("The Save button moved into a menu.", [_proposal()], False))
    monkeypatch.setattr(b, "_card_pending_change", lambda *a: pytest.fail("a fix must wait for the person's Apply"))
    b.cmd_test_workflow = lambda p, r: pytest.fail("nothing re-tests until a fix is applied")
    b._tf_investigate(wf.id)
    assert "The Save button moved into a menu." in _said(wf.id)
    change = _cards(wf.id, "pending_change")[0]
    assert change["state"] == "open" and change["actions"] == ["apply", "dismiss"] and "auto" not in change
    assert _cards(wf.id, "autofix")[0]["state"] == "done"
    assert runtime_state.get_flags(wf.id) == {}
    # while the proposal waits, the person may still press Test (BUILD-101 (3)), but nothing may be approved or published
    waiting = lifecycle_for_workflow(get_workflow(wf.id))
    assert waiting["substate"] == "change_pending"
    assert "approve" not in waiting["allowed_actions"] and "publish" not in waiting["allowed_actions"]


def test_with_no_confident_fix_it_says_so_and_asks_how_the_person_would_fix_it(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr(b, "_tf_propose_fix", lambda wid, sink=None: ("It never found the Save button.", [], False))
    b._tf_investigate(wf.id)
    af = _cards(wf.id, "autofix")[0]
    assert af["state"] == "no_fix" and "human_edit" in af["actions"]
    assert _said(wf.id) == ["It never found the Save button."]  # the model's reading, once; no second hardcoded line
    assert not _cards(wf.id, "pending_change")


def test_an_error_while_investigating_ends_the_round_cleanly(env, monkeypatch):
    b, wf, _out = env

    def boom(wid, sink=None):
        raise RuntimeError("quota used up")

    monkeypatch.setattr(b, "_tf_propose_fix", boom)
    b._tf_investigate(wf.id)
    af = _cards(wf.id, "autofix")[0]
    assert af["state"] == "failed" and af["reason"] == "quota used up"
    assert runtime_state.get_flags(wf.id) == {}


def test_a_reply_and_the_fallback_line_are_never_both_said(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr(b, "_tf_propose_fix", lambda wid, sink=None: ("It never found the Save button.", [], False))
    b._tf_investigate(wf.id)
    assert _said(wf.id) == ["It never found the Save button."]
    monkeypatch.setattr(b, "_tf_propose_fix", lambda wid, sink=None: ("", [], False))
    b._tf_investigate(wf.id)
    assert sum("not sure how to fix" in t for t in _said(wf.id)) == 1


def test_a_deliberate_stop_is_one_card_built_from_the_record_and_never_sent_to_the_model(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr(b, "_tf_propose_fix", lambda *a, **k: pytest.fail("a known cause is not guessed at by a model"))
    monkeypatch.setattr(b, "_tf_run_facts", lambda wid: {
        "kind": "decision_needed", "took_effect": True, "message": "",
        "surface": {"title": "Replace file?", "buttons": ["Replace", "Keep both"], "blocks": True}})
    b._tf_investigate(wf.id)
    cards = _cards(wf.id, "autofix")
    assert len(cards) == 1 and cards[0]["state"] == "needs_answer" and not _said(wf.id)
    reason = cards[0]["reason"]
    assert "Replace file?" in reason and "Keep both" in reason and "nothing was repeated" in reason
    assert "re-target" not in reason and "Tell me here what should happen" in reason
    assert runtime_state.get_flags(wf.id) == {}
    # a refusal the app showed does not ask "what should happen when this shows up": the runtime's sentence stands
    monkeypatch.setattr(b, "_tf_run_facts", lambda wid: {
        "kind": "fault:action_failed", "took_effect": True, "surface": None,
        "message": "Step 3 (click) failed: This step didn't finish: the app said \"Upload failed\". (x)"})
    b._tf_investigate(wf.id)
    reason = _cards(wf.id, "autofix")[1]["reason"]
    assert "Upload failed" in reason and "Tell me here" not in reason


def test_nothing_sent_is_false_when_the_runtime_says_the_action_may_have_happened(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr(b, "_tf_run_facts", lambda wid: {"kind": "decision_needed", "took_effect": True, "surface": None, "message": ""})
    _fake_test(b, steps=[("open_new_invoice", False), ("upload_file", False)], error="Needs a decision: 1 upload complete.")
    assert b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False) == "failed"
    card = _cards(wf.id, "test_run")[0]
    assert card["nothing_sent"] is False
    assert "popup" in card["summary"] and "Something went wrong" not in card["summary"]


def test_stop_pressed_while_thinking_posts_no_editor_link_either(env, monkeypatch):
    b, wf, _out = env
    posted = []

    def propose(wid, sink=None):
        test_flow._autofix_stop[wid].set()
        return "A diagnosis.", [], lambda: posted.append(1)

    monkeypatch.setattr(b, "_tf_propose_fix", propose)
    b._tf_investigate(wf.id)
    assert posted == [] and "A diagnosis." not in _said(wf.id)


def test_the_stop_button_ends_investigating_and_posts_nothing(env, monkeypatch):
    b, wf, _out = env

    def propose(wid, sink=None):
        test_flow._autofix_stop[wid].set()  # the person presses Stop while it is thinking
        return "A diagnosis.", [_proposal()], False

    monkeypatch.setattr(b, "_tf_propose_fix", propose)
    b._tf_investigate(wf.id)
    assert _cards(wf.id, "autofix")[0]["state"] == "stopped"
    assert not _cards(wf.id, "pending_change") and "A diagnosis." not in _said(wf.id)


def test_the_lifecycle_says_when_it_is_investigating_and_offers_find_the_cause_after_a_failure(env):
    b, wf, _out = env
    runtime_state.set_flags(wf.id, investigating=True)
    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert (lc["stage"], lc["substate"], lc["allowed_actions"]) == ("test", "investigating", ["stop_test"])
    runtime_state.clear_all()
    w = get_workflow(wf.id)
    w.last_test_status = "failed"
    save_workflow(w)
    lc = lifecycle_for_workflow(w)
    assert lc["substate"] == "failed" and lc["allowed_actions"][:2] == ["investigate", "edit"]


def _failed_then_inputs(b, wf):
    """A failed test card in the thread, and the inputs it ran with (an edit resets the status, not the inputs)."""
    _fake_test(b, steps=[("open_new_invoice", False)], error="Could not find it")
    assert b._tf_run_test(wf.id, {"po_number": "PO-1"}, guard=True, show_browser=False, attempt=1, auto=False) == "failed"
    w = get_workflow(wf.id)
    w.last_test_inputs = {"po_number": "PO-1"}
    w.last_test_status = "never"  # what any applied edit does
    save_workflow(w)


def _wait_for(calls, n=1):
    for _ in range(100):
        if len(calls) >= n:
            return
        time.sleep(0.02)


def test_applying_a_fix_after_a_failed_test_retests_at_once_with_the_guard_on(env, monkeypatch):
    b, wf, _out = env
    _failed_then_inputs(b, wf)
    calls: list[tuple] = []
    monkeypatch.setattr(b, "_tf_background", lambda *a: calls.append(a))
    assert b._tf_retest_after_fix(wf.id, lambda e: None) is True
    _wait_for(calls)
    assert calls == [(wf.id, {"po_number": "PO-1"}, True, True)]  # (workflow, inputs, guard, show_browser)


def test_no_retest_when_the_last_test_did_not_fail_or_a_change_is_still_waiting(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr(b, "_tf_background", lambda *a: pytest.fail("must not re-test"))
    assert b._tf_retest_after_fix(wf.id, lambda e: None) is False  # never tested
    _fake_test(b, steps=[("open_new_invoice", False)], outcome="passed")
    b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False)
    w = get_workflow(wf.id)
    w.last_test_inputs = {"po_number": "PO-1"}
    save_workflow(w)
    assert b._tf_retest_after_fix(wf.id, lambda e: None) is False  # the newest test passed
    _failed_then_inputs(b, wf)
    from conxa_compile.conversation import harness

    harness.post_card(f"wf:{wf.id}", "pending_change", "open", sink=lambda e: None, title="Another change", actions=["apply", "dismiss"])
    assert b._tf_retest_after_fix(wf.id, lambda e: None) is False  # another unapplied change holds the test back


def test_pressing_apply_on_a_change_card_retests_and_otherwise_posts_the_usual_summary(env, monkeypatch):
    b, wf, _out = env
    from conxa_compile.conversation import harness

    monkeypatch.setattr(b, "_apply_change_set", lambda *a, **k: None)
    monkeypatch.setattr(b, "_log_edit", lambda *a, **k: None)
    summaries: list = []
    monkeypatch.setattr(b, "_flow_post_summary", lambda *a, **k: summaries.append(a))
    started: list = []
    thread = f"wf:{wf.id}"

    def press_apply():
        card = harness.post_card(thread, "pending_change", "open", sink=lambda e: None, refs={"skill_id": "skill_t1"},
                                 title="Change “x”", proposal=_proposal(), actions=["apply", "dismiss"])
        return b._card_pending_change(thread, card, "apply", {}, "r", lambda e: None)

    monkeypatch.setattr(b, "_tf_retest_after_fix", lambda wid, sink: started.append(wid) or True)
    assert press_apply()["ok"] and started == [wf.id] and summaries == []
    monkeypatch.setattr(b, "_tf_retest_after_fix", lambda wid, sink: False)
    assert press_apply()["ok"] and len(summaries) == 1


def test_a_second_failure_investigates_again_with_no_attempt_cap(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr(studio_settings, "load", lambda: {"autofix_enabled": True})
    monkeypatch.setattr(b, "_tf_run_test", lambda *a, **k: "failed")
    monkeypatch.setattr(b, "_tf_input_check", lambda *a: False)
    rounds: list = []
    monkeypatch.setattr(b, "_tf_investigate", lambda wid: rounds.append(wid))
    for _ in range(6):
        b._tf_background(wf.id, {}, True, False)
    assert len(rounds) == 6


def test_the_find_the_cause_switch_off_means_a_failure_is_only_reported(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr(studio_settings, "load", lambda: {"autofix_enabled": False})
    monkeypatch.setattr(b, "_tf_run_test", lambda *a, **k: "failed")
    monkeypatch.setattr(b, "_tf_input_check", lambda *a: False)
    monkeypatch.setattr(b, "_tf_investigate", lambda wid: pytest.fail("the switch is off"))
    b._tf_background(wf.id, {}, True, False)


# ── a value the person typed is blamed before the workflow is edited ─────────────────────────────

def _failed_find(env, monkeypatch, tested):
    """A test that failed to FIND its element, with the typed value `tested` for po_number."""
    b, wf, _out = env
    w = get_workflow(wf.id)
    w.last_test_run_id, w.last_test_status = "r_1", "failed"
    save_workflow(w)
    evidence = {"runtime_evidence": {"failure_kind": "ambiguous", "input_refs": [{"id": "po_number", "empty": False}]}}
    monkeypatch.setattr("conxa_compile.editor.evidence.build_evidence_bundle", lambda *a, **k: evidence)
    monkeypatch.setattr(studio_settings, "load", lambda: {"autofix_enabled": True})
    monkeypatch.setattr(b, "_tf_run_test", lambda *a, **k: "failed")
    return b, wf


def test_a_changed_input_is_asked_about_before_autofix_edits_any_step(env, monkeypatch):
    b, wf = _failed_find(env, monkeypatch, "PO-1")
    monkeypatch.setattr(b, "_tf_investigate", lambda *a: pytest.fail("Auto-fix must wait for the answer"))
    b._tf_background(wf.id, {"po_number": "PO-1"}, True, False)
    q = next(x for x in clarify_store.load(wf.id)["questions"] if x["kind"] == "input_check")
    assert q["status"] == "open" and q["input_ids"] == ["po_number"] and "PO-1" in q["body"] and "PO-44821" in q["body"]
    assert len(_cards(wf.id, "clarify_question")) == 1
    assert lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"] == ["answer_clarify", "edit", "rebuild"]


def test_the_recorded_value_is_not_blamed_so_autofix_runs_as_before(env, monkeypatch):
    b, wf = _failed_find(env, monkeypatch, "PO-44821")
    ran = []
    monkeypatch.setattr(b, "_tf_investigate", lambda *a: ran.append(a))
    b._tf_background(wf.id, {"po_number": "PO-44821"}, True, False)
    assert ran and not any(x["kind"] == "input_check" for x in clarify_store.load(wf.id)["questions"])


def test_answering_wrong_reopens_the_test_card_with_the_input_flagged(env, monkeypatch):
    b, wf = _failed_find(env, monkeypatch, "PO-1")
    monkeypatch.setattr(b, "_tf_investigate", lambda *a: pytest.fail("Auto-fix must wait for the answer"))
    b._tf_background(wf.id, {"po_number": "PO-1"}, True, False)
    card = _cards(wf.id, "clarify_question")[0]
    res = b._card_clarify_question(f"wf:{wf.id}", card, "choose", {"choice": "wrong"}, "r", lambda e: None)
    assert res["ok"]
    tc = _cards(wf.id, "test_inputs")[0]
    assert {i["id"]: i.get("suspect") for i in tc["inputs"]}["po_number"] is True


def test_answering_right_hands_the_failure_to_autofix(env, monkeypatch):
    b, wf = _failed_find(env, monkeypatch, "PO-1")
    monkeypatch.setattr(b, "_tf_investigate", lambda *a: pytest.fail("Auto-fix must wait for the answer"))
    b._tf_background(wf.id, {"po_number": "PO-1"}, True, False)
    resumed = []
    monkeypatch.setattr(b, "cmd_flow_investigate", lambda payload, rid: resumed.append(payload))
    b._card_clarify_question(f"wf:{wf.id}", _cards(wf.id, "clarify_question")[0], "choose", {"choice": "right"}, "r", lambda e: None)
    assert resumed == [{"workflow_id": wf.id}]


def test_answering_on_purpose_closes_the_question_and_starts_nothing(env, monkeypatch):
    """BUILD-109 (2): a test with deliberately bad input. The answer closes the question (Test is allowed again) and
    neither the re-test card nor Auto-fix starts."""
    b, wf = _failed_find(env, monkeypatch, "PO-1")
    monkeypatch.setattr(b, "_tf_investigate", lambda *a: pytest.fail("Auto-fix must wait for the answer"))
    b._tf_background(wf.id, {"po_number": "PO-1"}, True, False)
    monkeypatch.setattr(b, "cmd_flow_investigate", lambda *a: pytest.fail("nothing starts"))
    monkeypatch.setattr(b, "cmd_flow_test_prepare", lambda *a: pytest.fail("nothing starts"))
    res = b._card_clarify_question(f"wf:{wf.id}", _cards(wf.id, "clarify_question")[0], "choose",
                                   {"choice": "on_purpose"}, "r", lambda e: None)
    assert res["ok"]
    q = next(x for x in clarify_store.load(wf.id)["questions"] if x["kind"] == "input_check")
    assert q["status"] == "answered"
    assert "test" in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]


def test_evidence_without_the_new_keys_leaves_autofix_untouched(env, monkeypatch):
    b, wf, _out = env
    w = get_workflow(wf.id)
    w.last_test_run_id = "r_1"
    save_workflow(w)
    monkeypatch.setattr("conxa_compile.editor.evidence.build_evidence_bundle", lambda *a, **k: {"runtime_evidence": {"message": "old runtime"}})
    monkeypatch.setattr(studio_settings, "load", lambda: {"autofix_enabled": True})
    monkeypatch.setattr(b, "_tf_run_test", lambda *a, **k: "failed")
    ran = []
    monkeypatch.setattr(b, "_tf_investigate", lambda *a: ran.append(a))
    b._tf_background(wf.id, {"po_number": "PO-1"}, True, False)
    assert ran


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
    b._tf_investigate = lambda *a, **k: started.append(1)
    monkeypatched = b._tf_run_test
    b._tf_run_test = lambda *a, **k: "needs_sign_in"
    b._tf_background(wf.id, {}, True, False)
    b._tf_run_test = monkeypatched
    assert started == []


def test_a_test_paused_on_sign_in_keeps_its_inputs_for_sign_in_and_test_again(env):
    # WF-3 Leg B: the first test paused before any result was saved, so the re-test ran with no inputs at all.
    b, wf, _out = env
    _fake_test(b, steps=[("open_new_invoice", False)], outcome="needs_sign_in")
    b._tf_run_test(wf.id, {"first_name": "Ada"}, guard=True, show_browser=False, attempt=1, auto=False)
    assert get_workflow(wf.id).last_test_inputs == {"first_name": "Ada"}


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
    c.drive(_fork_result(), fork_hook=lambda f: "keep_both", answer_span=lambda r, content: asked.append(r) or {"kind": "done"})
    assert asked == []


def test_with_no_one_to_ask_the_test_ends_at_the_popup_and_the_model_is_not_consulted():
    asked = []
    c = Calls([])
    result, stopped = c.drive(_fork_result(), fork_hook=None, answer_span=lambda r, content: asked.append(r))
    assert stopped["declined"] is True and c.args == [] and asked == []


def test_choosing_stop_or_an_unlisted_choice_ends_the_test_without_resuming():
    for answer in ("__stop__", "delete_everything"):
        c = Calls([])
        _, stopped = c.drive(_fork_result(), fork_hook=lambda f, a=answer: a)
        assert stopped["declined"] is True and c.args == []


def _agent_fork_result(step=2):
    r = _fork_result(step=step, unknown=True)
    meta = r["_meta"]["conxa/fork"]
    meta.update({"requires_human": False, "agent": True, "reason": "agent_decides"})
    meta["choices"] = [{"id": "Open app", "label": "Open app", "role": "unknown"},
                       {"id": "Stay in browser", "label": "Stay in browser", "role": "unknown"},
                       {"id": "__stop__", "label": "Stop the run", "role": "stop"}]
    return r


def test_only_a_pause_the_runtime_marked_for_an_agent_is_read_as_one():
    assert _extract_fork_pause(_agent_fork_result())["agent"] is True
    assert _extract_fork_pause(_fork_result())["agent"] is False  # human-only (requires_human true)
    legacy = _fork_result()
    legacy["_meta"]["conxa/fork"].pop("requires_human")
    assert _extract_fork_pause(legacy)["agent"] is False  # an older engine: never handed to the model


def test_an_agent_popup_is_answered_by_the_standin_and_resumes_that_step():
    c = Calls([DONE])
    asked = []
    result, stopped = c.drive(_agent_fork_result(step=2), fork_hook=lambda f: 1 / 0,
                              answer_popup=lambda f, content: asked.append(f["title"]) or "Stay in browser")
    assert result is DONE and stopped is None and asked == ["Upload options"]
    assert c.args == [{"skill": "s", "inputs": {}, "resume_from": 2, "review_results": {"2": {"choice": "Stay in browser"}}}]


def test_an_agent_popup_never_goes_to_the_person_hook_and_a_human_popup_never_to_the_standin():
    c = Calls([DONE, DONE])
    c.drive(_agent_fork_result(), fork_hook=lambda f: pytest.fail("person asked"), answer_popup=lambda f, content: "Open app")
    c.drive(_fork_result(), fork_hook=lambda f: "keep_both", answer_popup=lambda f, content: pytest.fail("model asked"))


def test_with_let_ai_answer_off_the_test_ends_at_an_agent_popup():
    c = Calls([])
    result, stopped = c.drive(_agent_fork_result(), fork_hook=lambda f: "Open app", answer_popup=None)
    assert stopped["declined"] is True and c.args == []


def test_the_standin_choosing_stop_or_an_unlisted_button_ends_the_test():
    for answer in ("__stop__", "Delete everything"):
        c = Calls([])
        _, stopped = c.drive(_agent_fork_result(), answer_popup=lambda f, content, a=answer: a)
        assert stopped["declined"] is True and c.args == []


def test_a_declined_ok_cancel_box_ends_the_parked_run_with_stop_so_it_is_cancelled_now():
    # BUILD-117 (4): the page's own OK/Cancel box waits on the parked run; OK pressed on it later would still act.
    for answer in ("__stop__", "Delete everything"):
        r = _agent_fork_result()
        r["_meta"]["conxa/fork"]["native"] = True
        c = Calls([DONE])
        _, stopped = c.drive(r, answer_popup=lambda f, content, a=answer: a)
        assert stopped["declined"] is True
        assert c.args == [{"skill": "s", "inputs": {}, "resume_from": 2, "review_results": {"2": {"choice": "__stop__"}}}]


def test_a_test_ended_at_a_popup_is_kept_on_the_workflow_and_shows_as_partial_in_the_lifecycle(env):
    b, wf, _out = env

    def fake(payload, rid):
        payload["_fork_hook"]({"title": "Upload options", "choices": [], "step_index": 1, "reason": "ask_human"})
        return {"status": "stopped_at_guard", "stopped_intent": "Upload options", "stopped_at": 1, "declined": True}

    b.cmd_test_workflow = fake
    t = threading.Thread(target=lambda: b._tf_run_test(wf.id, {}, guard=True, show_browser=False, attempt=1, auto=False), daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not test_flow._waiters:
        time.sleep(0.02)
    b.cmd_flow_test_decision({"workflow_id": wf.id, "decision": "end_here"}, "r")
    t.join(5)
    saved = get_workflow(wf.id)
    assert saved.last_test_outcome["outcome"] == "ended_at_popup" and saved.last_test_outcome["step_index"] == 1
    assert saved.last_test_status == "never"                                   # still not a result
    life = lifecycle_for_workflow(saved)
    assert life["substate"] == "partial" and "Upload options" in life["note"]
    from conxa_core.storage.workflow_store import set_workflow_test_result
    assert set_workflow_test_result(wf.id, status="passed", inputs={}).last_test_outcome is None   # a real result supersedes it


def test_a_secret_test_input_is_hidden_on_the_card_and_never_saved(env, monkeypatch):
    """WF-8 secrets check: a typed password showed as plain text on the card and was saved in the thread."""
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import get_workflow

    b, wf, _out = env
    doc = read_skill("skill_t1")
    doc["inputs"].append({"id": "user_password", "type": "text"})
    doc["skills"][0]["steps"][1]["value"] = "{{po_number}} {{user_password}}"   # a step uses it, so a run needs it
    write_skill("skill_t1", doc)
    card_id = b.cmd_flow_test_prepare({"workflow_id": wf.id}, "r")["card_id"]
    spec = {i["id"]: i for i in _cards(wf.id, "test_inputs")[0]["inputs"]}["user_password"]
    assert spec["type"] == "password" and spec["secret"] is True and spec["value"] == ""
    ran: list = []
    monkeypatch.setattr(b, "_tf_background", lambda *a: ran.append(a[1]))
    b.cmd_flow_test_run({"workflow_id": wf.id, "card_id": card_id,
                         "inputs": {"po_number": "PO-1", "user_password": "ZzDummyPw!8812"}}, "r")
    assert ran[0]["user_password"] == "ZzDummyPw!8812"                       # the run gets the real value
    thread = str(ts.load(f"wf:{wf.id}", limit=200)["items"])
    assert "ZzDummyPw" not in thread                                         # the thread keeps only a mask
    assert test_flow.last_test_inputs(get_workflow(wf.id))["user_password"] == "ZzDummyPw!8812"   # this session only


def test_test_again_after_a_restart_asks_for_the_secret_instead_of_running_without_it(env, monkeypatch):
    """BUILD-110 (9): a secret test input lives only in memory; after a restart "sign in and test again" (and the
    automatic re-test) ran without it and failed on the missing value."""
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import get_workflow, set_workflow_test_result

    b, wf, _out = env
    doc = read_skill("skill_t1")
    doc["inputs"].append({"id": "user_password", "type": "text"})
    doc["skills"][0]["steps"][1]["value"] = "{{po_number}} {{user_password}}"
    write_skill("skill_t1", doc)
    set_workflow_test_result(wf.id, status="failed", inputs={"po_number": "PO-1", "user_password": "ZzDummyPw!8812"})
    monkeypatch.setattr(test_flow, "_secret_test_inputs", {})                      # Studio restarted
    ran: list = []
    monkeypatch.setattr(b, "_tf_run_test", lambda *a, **k: ran.append(a) or "passed")
    inputs = test_flow.last_test_inputs(get_workflow(wf.id))
    assert "user_password" not in inputs
    b._tf_background(wf.id, inputs, True, True)
    assert ran == []                                                              # nothing ran without the password
    card = [c for c in _cards(wf.id, "test_inputs") if c.get("state") == "open"][-1]
    spec = {i["id"]: i for i in card["inputs"]}
    assert spec["user_password"]["secret"] is True and spec["user_password"]["value"] == ""
    assert spec["po_number"]["value"] == "PO-1"                                   # the rest is pre-filled as before
    b._tf_background(wf.id, {**inputs, "user_password": "ZzDummyPw!8812"}, True, True)
    assert len(ran) == 1                                                          # with it, the run goes ahead


def test_no_just_do_it_lets_the_test_run_past_that_step_and_the_card_says_so(env):
    from conxa_compile.conversation import clarify
    from conxa_core.storage.json_store import read_skill

    b, wf, _out = env
    keys = clarify.step_keys(clarify._steps(read_skill("skill_t1")))
    clarify_store.save(wf.id, {"questions": [
        {"id": f"confirm:{keys[2]}", "kind": "confirm", "status": "answered", "step_keys": [keys[2]], "answer": {"choice": "no"}},
        {"id": f"confirm:{keys[1]}", "kind": "confirm", "status": "answered", "step_keys": [keys[1]], "answer": {"choice": "yes"}},
    ]})
    assert b._flow_go_ahead_keys(wf.id) == {keys[2]}      # only the "No" on a step that is still irreversible
    b.cmd_flow_test_prepare({"workflow_id": wf.id}, "r")
    assert _cards(wf.id, "test_inputs")[0]["go_ahead"] == ["Send invoice"]
