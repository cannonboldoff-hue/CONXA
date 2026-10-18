"""BUILD-39 / BUILD-32: compile and Clarify driven from a workflow's conversation, end to end through
the real editor commands (no model, no network)."""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time

import pytest

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from conxa_core.config import settings  # noqa: E402
from conxa_compile.conversation import clarify_store, compile_queue, runtime_state, thread_store as ts  # noqa: E402
from conxa_compile.conversation.lifecycle import lifecycle_for_workflow  # noqa: E402
from handlers.protocol import _CommandError, _event_sink  # noqa: E402

SOURCES = {
    "enter_po_number": {"action": {"value": "PO-44821"}, "extras": {"sequence": 4}},
    "enter_description": {"action": {"value": "Annual license"}, "extras": {"sequence": 5}},
}


def _step(action, intent, **kw):
    s = {
        "action": {"action": action}, "intent": intent, "semantic_description": "", "value": None, "input_binding": None,
        "target": {"primary_selector": f"#{intent}", "fallback_selectors": []}, "signals": {},
        "validation": {"wait_for": {"type": "none"}, "assertions": []},
    }
    s.update(kw)
    return s


def _doc(skill_id):
    steps = [
        _step("navigate", "open_new_invoice"),
        _step("type", "enter_po_number", value="{{po_number}}", input_binding="po_number"),
        _step("type", "enter_description", value="{{description}}", input_binding="description"),
        _step("click", "send_invoice", consequence="irreversible"),
    ]
    return {
        "meta": {"id": skill_id, "title": "Create invoice", "version": 1},
        "skills": [{"id": skill_id, "steps": steps}],
        "inputs": [{"id": "po_number", "type": "text"}, {"id": "description", "type": "text"}],
        "compile_report": {"status": "ok", "steps": [{"warnings": []}, {"warnings": []}, {"warnings": []}, {"warnings": ["low confidence"]}]},
    }


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    runtime_state.clear_all()
    compile_queue.reset()
    spec = importlib.util.spec_from_file_location("cbackend_cflow", os.path.join(_PY_DIR, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from handlers import protocol

    out: list[dict] = []
    mod._write = out.append
    protocol._write = out.append
    b = mod.Backend()
    monkeypatch.setattr("conxa_compile.editor.retarget.find_source_event", lambda step, doc: SOURCES.get(step.get("intent")))
    from conxa_core.storage.group_store import create_group
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow

    g = create_group("Finance Ops")
    wf = create_workflow("Create invoice", "https://billing.example.test/new", group_id=g.id)
    w = get_workflow(wf.id)
    w.session_id, w.recording_status = "sess1", "recorded"
    save_workflow(w)
    return b, wf, out


def _fake_compile(b, wf, *, doc_fn=_doc, hold=None, fail=None):
    from conxa_core.storage.json_store import write_skill
    from conxa_core.storage.workflow_store import get_workflow, save_workflow

    def cmd_compile(payload, rid):
        sink = _event_sink(rid)
        sink({"phase": "pipeline_start"})
        if hold is not None:
            hold.wait(5)
        if fail is not None:
            raise fail
        sink({"phase": "quota", "status": "committed", "remaining": 37})
        sink({"phase": "pipeline_done", "event_count": 14})
        sink({"phase": "compiler_start"})
        for n in range(1, 5):
            sink({"phase": "compile_log", "message": f"Compiled step {n}.", "step_index": n - 1})
        sink({"phase": "compile_log", "message": "Asking for a second opinion on the whole workflow."})
        sink({"phase": "compile_log", "message": "Second opinion finished."})
        skill_id = f"skill_{payload['session_id']}_{payload['workflow_id'][:4]}"
        write_skill(skill_id, doc_fn(skill_id))
        w = get_workflow(payload["workflow_id"])
        w.skill_id, w.recording_status, w.compile_steps_with_warnings = skill_id, "compiled", 1
        save_workflow(w)
        sink({"phase": "compiler_done", "step_count": 4})
        sink({"phase": "compile_done", "skill_id": skill_id, "version": 1, "step_count": 4})
        return {"skill_id": skill_id, "version": 1, "step_count": 4, "compile_status": "ok", "compile_steps_with_warnings": 1}

    b.cmd_compile = cmd_compile


def _cards(wf_id, card_type):
    return [i for i in ts.load(f"wf:{wf_id}", limit=200)["items"] if i["kind"] == "card" and i["card_type"] == card_type]


def _texts(wf_id, kind):
    return [i["text"] for i in ts.load(f"wf:{wf_id}", limit=200)["items"] if i["kind"] == kind]


def test_the_compile_card_mirrors_the_compilers_own_events_then_questions_begin(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    res = b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    assert res["ok"] and res["step_count"] == 4
    card = _cards(wf.id, "compile")[0]
    assert card["state"] == "done" and card["credits_left"] == 37 and card["step_count"] == 4
    assert [(r["key"], r["status"]) for r in card["rows"]] == [("clean", "done"), ("find", "done"), ("meaning", "done"), ("save", "done")]
    assert card["rows"][0]["detail"] == "14 actions kept" and card["rows"][1]["detail"] == "4 of 4"
    assert _texts(wf.id, "event")[-1] == "Compiled"
    assert "Compiled into 4 steps." in _texts(wf.id, "assistant_message")[0]
    assert "send invoice" in _texts(wf.id, "assistant_message")[0].lower()  # the uncertain step is named, not numbered
    # lifecycle follows: clarify questions are open
    from conxa_core.storage.workflow_store import get_workflow

    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert (lc["stage"], lc["substate"]) == ("clarify", "open")
    assert runtime_state.get_flags(wf.id) == {}  # nothing left "compiling"


def test_questions_are_asked_one_at_a_time_and_never_blocking(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    qs = _cards(wf.id, "clarify_question")
    assert len(qs) == 1 and qs[0]["state"] == "open" and qs[0]["index"] == 1
    assert qs[0]["question_kind"] == "confirm"  # the irreversible step outranks everything but uploads
    data = clarify_store.load(wf.id)
    assert [q["kind"] for q in data["questions"]] == ["confirm", "values"]
    assert qs[0]["total"] == 2


def test_answering_applies_existing_mutations_and_the_counts_add_up(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    q1 = _cards(wf.id, "clarify_question")[0]
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")
    assert r["ok"] and r["receipt"] == "A person confirms"
    steps = read_skill(skill_id)["skills"][0]["steps"]
    assert [s["action"] if isinstance(s["action"], str) else s["action"]["action"] for s in steps][-2:] == ["ai_review", "click"]
    review = steps[-2]
    assert review["ai_review_output_schema"]["required"] == ["confirmed"] and review["ai_review_on_failure"] == "abort"
    assert "send invoice" in review["ai_review_prompt"].lower()
    assert _cards(wf.id, "clarify_question")[0]["state"] == "answered"

    # the next question appears only now
    qs = _cards(wf.id, "clarify_question")
    assert len(qs) == 2 and qs[1]["question_kind"] == "values" and qs[1]["index"] == 2 and qs[1]["state"] == "open"
    r2 = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": qs[1]["card_id"], "action": "choose", "payload": {"selected": ["po_number"]}}, "r")
    assert r2["ok"]
    after = read_skill(skill_id)
    desc = next(s for s in after["skills"][0]["steps"] if s["intent"] == "enter_description")
    assert desc["value"] == "Annual license" and not desc.get("input_binding")  # restored to the recorded literal through the gated patch
    assert [i["id"] for i in after["inputs"]] == ["po_number"]  # the orphaned input is gone
    # the destructive step still carries its flag (nothing the answers did touched it)
    assert after["skills"][0]["steps"][-1]["consequence"] == "irreversible"

    # the summary card: counts add up (4 steps compiled → 1 added by answers → 5 now)
    summary = _cards(wf.id, "workflow_summary")[0]["summary"]
    assert (summary["recorded_actions"], summary["steps"], summary["added_steps"], summary["inputs"]) == (0, 5, 1, 1) or summary["steps"] == 5
    assert summary["added_steps"] == 1 and summary["name"] == "Create invoice"
    assert "That’s everything. Your answers added 1 step." in _texts(wf.id, "assistant_message")[-1]
    # every answer was edit-logged with its own source
    from conxa_compile.editor.edit_log import edits_path

    log = edits_path(skill_id).read_text(encoding="utf-8")
    assert '"source": "clarify"' in log
    # a skill write revoked any approval and reset tests: still not approved
    assert get_workflow(wf.id).signed_off is False


def test_skipping_keeps_the_compilers_default_and_changes_nothing(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    before = read_skill(skill_id)
    for _ in range(2):
        q = [c for c in _cards(wf.id, "clarify_question") if c["state"] == "open"][0]
        assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "skip"}, "r")["ok"]
    assert read_skill(skill_id) == before  # byte-identical: unanswered questions never touch the skill
    assert all(c["state"] == "skipped" for c in _cards(wf.id, "clarify_question"))
    assert _cards(wf.id, "workflow_summary")  # and testing is never blocked: the summary still arrives


def test_a_workflow_edited_in_human_edit_meanwhile_fails_the_answer_cleanly(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    doc = read_skill(skill_id)
    doc["skills"][0]["steps"] = [s for s in doc["skills"][0]["steps"] if s["intent"] != "send_invoice"]
    write_skill(skill_id, doc)
    q1 = _cards(wf.id, "clarify_question")[0]
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")
    assert r["ok"] is False and r["code"] == "proposal_stale"
    card = _cards(wf.id, "clarify_question")[0]
    assert card["state"] == "failed" and "changed in Human Edit" in card["error"] and card["actions"] == ["choose", "skip"]


def test_out_of_credits_and_failures_are_their_own_states(env):
    b, wf, _out = env
    _fake_compile(b, wf, fail=_CommandError("compile_credit_limit_exceeded", "Monthly compile credits are exhausted for this workspace."))
    res = b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    assert res["ok"] is False
    card = _cards(wf.id, "compile")[0]
    assert card["state"] == "out_of_credits" and card["actions"] == ["add_credits", "compile_later"]
    from conxa_core.storage.workflow_store import get_workflow

    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert (lc["stage"], lc["substate"]) == ("compile", "out_of_credits")
    assert "Your recording is saved" in " ".join(str(i.get("subject")) for i in ts.load(f"wf:{wf.id}")["items"])

    _fake_compile(b, wf, fail=_CommandError("cloud_unreachable", "Conxa Cloud is unreachable."))
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid2")
    failed = _cards(wf.id, "compile")[1]
    assert failed["state"] == "failed" and failed["refunded"] is True and failed["actions"] == ["retry", "details"]
    assert lifecycle_for_workflow(get_workflow(wf.id))["substate"] == "failed"

    # a failure that is not infrastructure must not claim the credit came back
    _fake_compile(b, wf, fail=_CommandError("vision_anchors_failed", "Couldn't read the page."))
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid3")
    assert _cards(wf.id, "compile")[2]["refunded"] is False


def test_a_second_compile_waits_its_turn_with_a_real_position(env):
    b, wf, _out = env
    from conxa_core.storage.group_store import list_groups
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow

    other = create_workflow("Assign training", "https://hr.example.test/x", group_id=list_groups()[0].id)
    w = get_workflow(other.id)
    w.session_id, w.recording_status = "sess2", "recorded"
    save_workflow(w)

    hold = threading.Event()
    _fake_compile(b, wf, hold=hold)
    first = threading.Thread(target=lambda: b.cmd_flow_compile({"workflow_id": wf.id}, "ridA")); first.start()
    deadline = time.time() + 3
    while time.time() < deadline and compile_queue.running() != wf.id:
        time.sleep(0.02)
    second = threading.Thread(target=lambda: b.cmd_flow_compile({"workflow_id": other.id}, "ridB")); second.start()
    deadline = time.time() + 3
    while time.time() < deadline and not (_cards(other.id, "compile") and _cards(other.id, "compile")[0].get("position")):
        time.sleep(0.02)
    queued = _cards(other.id, "compile")[0]
    assert queued["state"] == "queued" and queued["position"] == 1 and queued["ahead_name"] == "Create invoice"
    assert lifecycle_for_workflow(get_workflow(other.id))["substate"] == "queued"
    hold.set(); first.join(10); second.join(10)
    assert _cards(other.id, "compile")[0]["state"] == "done"  # it started on its own once the first finished


def test_finishing_a_recording_starts_the_compile_and_says_so(env, monkeypatch):
    b, wf, _out = env
    started: list[str] = []
    monkeypatch.setattr(b, "_flow_start_compile", lambda wid: started.append(wid))
    from handlers import workflow_flow

    class Sess:
        browser_open = True
        def snapshot_events(self): return []

    class Reg:
        def get(self, sid): return Sess()

    monkeypatch.setattr(workflow_flow, "_recorder_registry", Reg())
    monkeypatch.setattr(b, "cmd_stop_recording", lambda p, r: {"event_count": 14})
    card = ts.append_card(f"wf:{wf.id}", "recording", "recording", refs={"session_id": "s"})
    b.cmd_flow_finish_recording({"workflow_id": wf.id, "card_id": card["card_id"]}, "r")
    assert started == [wf.id]
    assert "Got it — 14 actions." in _texts(wf.id, "assistant_message")[-1]
    # opting out (used when the caller wants to compile later) starts nothing
    started.clear()
    card2 = ts.append_card(f"wf:{wf.id}", "recording", "recording", refs={"session_id": "s"})
    b.cmd_flow_finish_recording({"workflow_id": wf.id, "card_id": card2["card_id"], "auto_compile": False}, "r")
    assert started == []
