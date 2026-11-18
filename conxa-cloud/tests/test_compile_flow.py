"""BUILD-39 / BUILD-32: compile and Clarify driven from a workflow's conversation, end to end through
the real editor commands (no model, no network)."""

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
    monkeypatch.setattr("conxa_compile.editor.source_event.find_source_event", lambda step, doc: SOURCES.get(step.get("intent")))
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


def test_the_compile_card_mirrors_the_compilers_own_events_then_questions_begin(env, monkeypatch):
    b, wf, _out = env
    monkeypatch.setattr("conxa_compile.llm.compile_recap.call_llm", lambda t, p, ms, **k: {
        "message": "Your workflow has 4 steps. I'm not sure about " + " and ".join(json.loads(p["user_text"])["unsure_steps"]) + ". I have a few quick questions."})
    _fake_compile(b, wf)
    res = b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    assert res["ok"] and res["step_count"] == 4
    card = _cards(wf.id, "compile")[0]
    assert card["state"] == "done" and card["credits_left"] == 37 and card["step_count"] == 4
    assert [(r["key"], r["status"]) for r in card["rows"]] == [("clean", "done"), ("find", "done"), ("meaning", "done"), ("save", "done")]
    assert card["rows"][0]["detail"] == "14 recorded actions" and card["rows"][1]["detail"] == "4 of 4"
    assert "Compiled" not in _texts(wf.id, "event")  # the card carries it
    assert "Your workflow has 4 steps." in _texts(wf.id, "assistant_message")[0]
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


def test_no_test_is_offered_between_the_compile_ending_and_the_questions_existing(env, monkeypatch):
    b, wf, _out = env
    _fake_compile(b, wf)
    seen: list[list[str]] = []
    from conxa_compile.conversation import understanding as understanding_mod

    real = understanding_mod.understand

    def spy(doc, task, **kw):  # the check after the compile: the compile card already says "done" by now
        from conxa_core.storage.workflow_store import get_workflow

        seen.append(lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"])
        return real(doc, task, **kw)

    monkeypatch.setattr(understanding_mod, "understand", spy)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    assert seen and "test" not in seen[0]
    assert runtime_state.get_flags(wf.id) == {}


def test_a_failing_question_generator_ends_checking_and_tells_the_thread(env, monkeypatch):
    b, wf, out = env
    _fake_compile(b, wf)
    from conxa_compile.conversation import clarify

    def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(clarify, "generate_questions", boom)
    res = b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    assert res["ok"]
    assert runtime_state.get_flags(wf.id) == {}
    assert "Couldn’t prepare the questions" in _texts(wf.id, "event")
    # the event is the last thing posted, so the renderer's refetch sees the settled lifecycle
    from conxa_core.storage.workflow_store import get_workflow

    assert "test" in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]


def test_a_test_cannot_be_started_while_questions_are_open_whatever_the_entry_point(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    assert clarify_store.summary(wf.id)["open"] > 0
    with pytest.raises(_CommandError) as exc:
        b.cmd_flow_test_prepare({"workflow_id": wf.id}, "r")
    assert exc.value.code == "not_allowed_now"
    res = b._run_flow_action(wf.id, "test", {}, "r", lambda e: None)
    assert res["ok"] is False and res["message"].startswith("Not yet")


def test_answering_applies_existing_mutations_and_the_counts_add_up(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    q1 = _cards(wf.id, "clarify_question")[0]
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")
    assert r["ok"] and r["receipt"] == "Someone approves it first"
    steps = read_skill(skill_id)["skills"][0]["steps"]
    assert [s["action"] if isinstance(s["action"], str) else s["action"]["action"] for s in steps][-2:] == ["agent_span", "click"]
    review = steps[-2]
    assert review["agent_span_answer_schema"]["required"] == ["confirmed"] and review["agent_span_on_failure"] == "abort"
    assert review["agent_span_allowed"] == []
    assert "send invoice" in review["value"].lower()
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


def test_a_question_cannot_be_skipped_and_typing_your_own_words_answers_it(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    before = read_skill(skill_id)
    q = [c for c in _cards(wf.id, "clarify_question") if c["state"] == "open"][0]
    with pytest.raises(_CommandError) as exc:
        b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "skip"}, "r")
    assert exc.value.code == "card_action_unsupported"
    assert clarify_store.get_question(wf.id, q["question_id"])["status"] == "open"   # nothing ended it
    assert read_skill(skill_id) == before
    # Words that name no single choice leave it open, and the card says why (WF-5 5.2: 5,000 "a"s answered it).
    for text in ("a" * 5000, "   ", "😀😀🎉", "yes, don't"):
        r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "own",
                               "payload": {"custom_text": text}}, "r")
        assert r["ok"] is False and r["code"] == "answer_unclear"
        assert clarify_store.get_question(wf.id, q["question_id"])["status"] == "open"
        card = next(c for c in _cards(wf.id, "clarify_question") if c["card_id"] == q["card_id"])
        assert card["state"] == "failed" and card["error"]
    assert read_skill(skill_id) == before
    # Words code can read as one choice answer it, and code applies that choice like a picked one.
    yes = next(c for c in clarify_store.get_question(wf.id, q["question_id"])["choices"] if c["label"].startswith("Yes"))
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "own",
                           "payload": {"custom_text": "हाँ"}}, "r")
    assert r["ok"] and clarify_store.get_question(wf.id, q["question_id"])["answer"]["choice"] == yes["id"]
    assert clarify_store.get_question(wf.id, q["question_id"])["status"] == "answered"


def _wish_env(b, wf, monkeypatch, proposals, mapped=None):
    from conxa_compile.conversation import harness
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import get_workflow

    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    skill_id = get_workflow(wf.id).skill_id
    monkeypatch.setattr(b, "_install_proxy_router", lambda **kw: None)
    monkeypatch.setattr(harness, "map_free_text_to_choice", lambda q, t: mapped)      # what the model maps it to
    monkeypatch.setattr(harness, "_propose", lambda **kw: proposals)
    monkeypatch.setattr(b, "_thread_context", lambda kind, ident, sink: {
        "skill_id": skill_id, "load_digest": lambda: None, "gate_proposals": lambda raw: raw})

    def apply(sid, props, rid):                                                        # stands in for the gated edit path
        doc = read_skill(sid)
        doc["skills"][0]["steps"][1]["semantic_description"] = "changed by the wish"
        write_skill(sid, doc)
    monkeypatch.setattr(b, "_apply_change_set", apply)
    q = next(c for c in _cards(wf.id, "clarify_question") if c["state"] == "open")
    assert clarify_store.get_question(wf.id, q["question_id"])["kind"] == "confirm"
    return skill_id, q


def test_a_typed_wish_becomes_a_change_and_the_question_closes_only_once_it_is_applied(env, monkeypatch):
    from conxa_core.storage.json_store import read_skill

    b, wf, _out = env
    skill_id, q = _wish_env(b, wf, monkeypatch, [{"id": "p1", "command": "patch_step", "step_key": "k", "field": "note",
                                                   "patch": {"semantic_description": "x"}}])
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "own",
                           "payload": {"custom_text": "ask my manager by email first"}}, "r")
    assert r["ok"] and r["pending_change"]
    assert clarify_store.get_question(wf.id, q["question_id"])["status"] == "open"     # nothing applied yet
    change = _cards(wf.id, "pending_change")[-1]
    assert change["refs"]["question_id"] == q["question_id"]
    assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": change["card_id"], "action": "apply"}, "r")["ok"]
    got = clarify_store.get_question(wf.id, q["question_id"])
    assert got["status"] == "answered" and got["receipt"].startswith("Changed:")
    steps = read_skill(skill_id)["skills"][0]["steps"]
    assert steps[1]["semantic_description"] == "changed by the wish"
    # A typed wish on a step that can't be undone never drops the approval before it.
    assert "still approves" in got["receipt"]
    assert any((s.get("action") or {}).get("action") == "agent_span" for s in steps)


def test_a_wish_no_safe_change_can_carry_leaves_the_question_open(env, monkeypatch):
    b, wf, _out = env
    _skill_id, q = _wish_env(b, wf, monkeypatch, [])                                   # the gate refused every change
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "own",
                           "payload": {"custom_text": "do it the other way"}}, "r")
    assert r["ok"] is False and r["code"] == "wish_not_applied"
    assert clarify_store.get_question(wf.id, q["question_id"])["status"] == "open"
    card = next(c for c in _cards(wf.id, "clarify_question") if c["card_id"] == q["card_id"])
    assert card["state"] == "failed" and "still open" in card["error"]


def test_a_dismissed_wish_change_leaves_the_question_open(env, monkeypatch):
    b, wf, _out = env
    _skill_id, q = _wish_env(b, wf, monkeypatch, [{"id": "p1", "command": "patch_step", "step_key": "k", "field": "note",
                                                    "patch": {"semantic_description": "x"}}])
    b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "own",
                       "payload": {"custom_text": "ask my manager by email first"}}, "r")
    change = _cards(wf.id, "pending_change")[-1]
    b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": change["card_id"], "action": "dismiss"}, "r")
    assert clarify_store.get_question(wf.id, q["question_id"])["status"] == "open"
    card = next(c for c in _cards(wf.id, "clarify_question") if c["card_id"] == q["card_id"])
    assert card["state"] == "failed" and "dismissed" in card["error"]


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
    assert card["state"] == "failed" and "changed in Human Edit" in card["error"] and card["actions"] == ["choose"]


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


def test_finishing_a_recording_starts_watching_it_and_says_so(env, monkeypatch):
    b, wf, _out = env
    started: list[str] = []
    monkeypatch.setattr(b, "_flow_start_compile", lambda wid: (_ for _ in ()).throw(AssertionError("compile waits for the questions")))
    monkeypatch.setattr(b, "_flow_start_understand", lambda wid: started.append(wid))
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


# ── understanding: a recording with nothing typed (GitHub file → Drive) ─────────────────────


def _github_doc(skill_id):
    steps = [
        _step("navigate", "open_repo", url="https://github.com/github/gitignore"),
        _step("click", "open_file", identity_bundle={"stable_hash": "open_file", "fingerprint": {"inner_text": "Android.gitignore"}}),
        _step("click", "download_file", identity_bundle={"stable_hash": "download_file", "fingerprint": {"inner_text": "Download raw file"}}),
        _step("navigate", "open_drive", url="https://drive.google.com/"),
    ]
    return {
        "meta": {"id": skill_id, "title": "Github to Drive Handoff", "version": 1},
        "skills": [{"id": skill_id, "steps": steps}], "inputs": [],
        "compile_report": {"status": "ok", "steps": [{"warnings": []}] * 4},
    }


def test_a_recording_with_nothing_typed_still_asks_what_changes_and_blocks_the_test_until_answered(env, monkeypatch):
    b, wf, _out = env
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow, save_workflow
    from conxa_compile.conversation import understanding

    task = "Download a gitignore file from GitHub and upload it to Google Drive"
    w = get_workflow(wf.id)
    w.task_description = task
    save_workflow(w)

    # The compile's own review call judged the candidates (BUILD-55): the repository changes, the download button doesn't.
    # No separate model call is made after the compile.
    def doc_with_review(skill_id):
        doc = _github_doc(skill_id)
        cands = understanding.extract_candidates(doc)
        doc["compile_report"]["understanding"] = {
            "goal_summary": "Copy a file from a GitHub repository to Google Drive.", "coverage": {"ok": True}, "for_task": task,
            "values": [{"ref": c["ref"], "verdict": "varies" if c["kind"] == "url" else "fixed", "name": "repository"} for c in cands]}
        return doc

    monkeypatch.setattr("conxa_compile.llm.client.call_llm",
                        lambda *a, **k: pytest.fail("the question flow must not make a model call to check the description"))
    _fake_compile(b, wf, doc_fn=doc_with_review)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")

    data = clarify_store.load(wf.id)
    assert [q["kind"] for q in data["questions"]] == ["chosen_values"]          # not "nothing to ask"
    assert data["understanding"]["goal_summary"].startswith("Copy a file")
    assert "nothing needs deciding" not in " ".join(_texts(wf.id, "assistant_message"))
    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert lc["stage"] == "clarify" and "test" not in lc["allowed_actions"]     # understood first, tested after

    q = _cards(wf.id, "clarify_question")[0]
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "choose",
                           "payload": {"selected": [data["questions"][0]["choices"][0]["id"]]}}, "r")
    assert r["ok"] and r["receipt"] == "Repository"
    doc = read_skill(get_workflow(wf.id).skill_id)
    assert doc["skills"][0]["steps"][0]["url"] == "https://github.com/{{repository}}"
    assert [(i["id"], i["default"]) for i in doc["inputs"]] == [("repository", "github/gitignore")]
    assert "test" in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]
    summary = _cards(wf.id, "workflow_summary")[0]["summary"]
    assert summary["goal"].startswith("Copy a file") and summary["input_names"] == ["repository"]

    # the test card offers the input, pre-filled with what was recorded
    prep = b.cmd_flow_test_prepare({"workflow_id": wf.id}, "r")
    card = next(c for c in _cards(wf.id, "test_inputs") if c["card_id"] == prep["card_id"])
    assert [(i["id"], i["value"]) for i in card["inputs"]] == [("repository", "github/gitignore")]


def test_a_compile_with_no_review_answer_still_asks_the_likely_values_unticked(env, monkeypatch):
    b, wf, _out = env
    from conxa_core.storage.workflow_store import get_workflow, save_workflow

    w = get_workflow(wf.id)
    w.task_description = "Copy a file"
    save_workflow(w)

    # The review call failed or was disabled: the compile carries no understanding, and none is invented.
    monkeypatch.setattr("conxa_compile.llm.client.call_llm",
                        lambda *a, **k: pytest.fail("the question flow must not make a model call to check the description"))
    _fake_compile(b, wf, doc_fn=_github_doc)
    assert b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")["ok"]
    q = clarify_store.load(wf.id)["questions"][0]
    assert q["kind"] == "chosen_values" and [c["selected"] for c in q["choices"]] == [False]  # asked, but nothing pre-ticked


# ── driving the flow from conversation (gap analysis G2 / G9) ───────────────────────────────


def test_a_confirmed_action_runs_the_same_command_the_button_runs(env, monkeypatch):
    b, wf, _out = env
    from conxa_compile.conversation import harness

    started: list[str] = []
    monkeypatch.setattr(b, "_flow_start_compile", lambda workflow_id: started.append(workflow_id))
    card = harness.post_card(f"wf:{wf.id}", "confirm_action", "open", action="compile", label="Compile", slots={}, actions=["confirm", "cancel"])
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": "confirm"}, "r")
    assert r["ok"] and started == [wf.id]
    done = next(c for c in _cards(wf.id, "confirm_action") if c["card_id"] == card["card_id"])
    assert done["state"] == "done" and done["receipt"] == "Compiling the recording."
    # a second press does nothing
    again = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": "confirm"}, "r")
    assert again["ok"] is False and started == [wf.id]


def test_cancelling_a_confirmation_runs_nothing(env, monkeypatch):
    b, wf, _out = env
    from conxa_compile.conversation import harness

    monkeypatch.setattr(b, "_flow_start_compile", lambda workflow_id: (_ for _ in ()).throw(AssertionError("must not run")))
    card = harness.post_card(f"wf:{wf.id}", "confirm_action", "open", action="compile", label="Compile", slots={}, actions=["confirm", "cancel"])
    assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": "cancel"}, "r")["ok"]
    assert _cards(wf.id, "confirm_action")[0]["state"] == "dismissed"


def test_an_action_that_cannot_run_becomes_a_sentence_not_an_exception(env):
    b, wf, _out = env
    res = b._run_flow_action(wf.id, "approve", {}, "r", lambda e: None)
    # not offered by the lifecycle right now, so refused with the lifecycle's own reason
    assert res["ok"] is False and res["message"].startswith("Not yet")
    assert b._run_flow_action(wf.id, "teleport", {}, "r", lambda e: None)["ok"] is False


def test_test_inputs_stated_in_chat_prefill_the_card_but_never_start_a_run(env, monkeypatch):
    b, wf, _out = env
    from conxa_core.storage.workflow_store import get_workflow, save_workflow

    w = get_workflow(wf.id)
    task = "Copy a file from a repository"
    w.task_description = task
    save_workflow(w)

    def doc_with_review(skill_id):  # the compile's review call said the repository changes each run
        from conxa_compile.conversation import understanding

        doc = _github_doc(skill_id)
        doc["compile_report"]["understanding"] = {"goal_summary": "g", "for_task": task, "values": [
            {"ref": c["ref"], "verdict": "varies", "name": "repository"} for c in understanding.extract_candidates(doc) if c["kind"] == "url"]}
        return doc

    _fake_compile(b, wf, doc_fn=doc_with_review)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    q = clarify_store.load(wf.id)["questions"][0]
    b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": _cards(wf.id, "clarify_question")[0]["card_id"], "action": "choose",
                       "payload": {"selected": [q["choices"][0]["id"]]}}, "r")
    ran: list = []
    monkeypatch.setattr(b, "cmd_flow_test_run", lambda *a, **k: ran.append(1))
    res = b._run_flow_action(wf.id, "test", {"inputs": {"repository": "templates"}}, "r", lambda e: None)
    assert res["ok"] and "nothing has run yet" in res["message"] and ran == []
    card = _cards(wf.id, "test_inputs")[0]
    assert [(i["id"], i["value"]) for i in card["inputs"]] == [("repository", "templates")]


def test_general_chat_can_see_the_persons_workflows_and_groups_read_only(env):
    b, wf, _out = env
    ctx = b._chat_context()
    assert ctx["group_names"] == ["Finance Ops"]
    assert [(w["id"], w["name"], w["group"]) for w in ctx["workflows"]] == [(wf.id, "Create invoice", "Finance Ops")]
    titles = [t for t, _ in ctx["context_sections"]]
    assert "The person's workflows (read-only)" in titles


def test_questions_past_the_cap_are_queued_then_asked_without_being_requested(env):
    b, wf, _out = env

    def many(skill_id):
        steps = [_step("navigate", "open")] + [_step("click", f"send_{i}", consequence="irreversible") for i in range(1, 9)]
        return {"meta": {"id": skill_id, "title": "Many", "version": 1}, "skills": [{"id": skill_id, "steps": steps}], "inputs": [],
                "compile_report": {"status": "ok", "steps": [{"warnings": []}] * 9}}

    _fake_compile(b, wf, doc_fn=many)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    data = clarify_store.load(wf.id)
    assert [q["status"] for q in data["questions"]] == ["open"] * 5 + ["queued"] * 3
    assert clarify_store.summary(wf.id)["unanswered"] == 8        # held back is not answered
    first = _cards(wf.id, "clarify_question")[0]
    assert first["total"] == 5
    from conxa_core.storage.workflow_store import get_workflow

    for _ in range(5):
        assert "test" not in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]
        card = [c for c in _cards(wf.id, "clarify_question") if c["state"] == "open"][0]
        b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": "choose", "payload": {"choice": "no"}}, "r")
    # nothing was asked for: the held-back three come forward on their own, and still block the test
    assert [q["status"] for q in clarify_store.load(wf.id)["questions"]][5:] == ["open"] * 3
    assert "test" not in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]
    for _ in range(3):
        card = [c for c in _cards(wf.id, "clarify_question") if c["state"] == "open"][0]
        b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": "choose", "payload": {"choice": "no"}}, "r")
    assert clarify_store.summary(wf.id)["unanswered"] == 0
    assert "test" in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]


# ── BUILD-55 B2: an answer is all-or-nothing and never applied twice ───────────────────────


def _step_actions(doc):
    return [s["action"] if isinstance(s["action"], str) else s["action"]["action"] for s in doc["skills"][0]["steps"]]


def test_an_answer_whose_second_edit_fails_restores_the_workflow_and_a_retry_applies_it_once(env, monkeypatch):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    before = read_skill(skill_id)
    q1 = _cards(wf.id, "clarify_question")[0]
    real_patch = b.cmd_patch_step

    def boom(payload, rid):
        raise _CommandError("patch_failed", "The second edit failed.")

    monkeypatch.setattr(b, "cmd_patch_step", boom)   # the review step is inserted, then its fill-in fails
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")
    assert r["ok"] is False and r["code"] == "patch_failed"
    assert read_skill(skill_id) == before                       # the half-applied insert was rolled back
    card = _cards(wf.id, "clarify_question")[0]
    assert card["state"] == "failed" and card["actions"] == ["choose"]
    assert clarify_store.get_question(wf.id, q1["question_id"])["status"] == "open"

    monkeypatch.setattr(b, "cmd_patch_step", real_patch)
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")
    assert r["ok"]
    assert _step_actions(read_skill(skill_id)).count("agent_span") == 1   # not two


def _mark_applying(wf, q, *, since, base_version):
    from conxa_compile.conversation import harness

    clarify_store.update_question(wf.id, q["question_id"], status="applying", base_version=base_version, applying_since=since)
    harness.update_card(f"wf:{wf.id}", q["card_id"], state="applying", actions=[])


def test_an_attempt_that_died_after_changing_the_workflow_is_not_run_again(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    before = read_skill(skill_id)
    q1 = _cards(wf.id, "clarify_question")[0]
    _mark_applying(wf, q1, since=1.0, base_version=0)           # old, and the workflow has moved on from version 0
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")
    assert r["ok"] is False and r["code"] == "interrupted"
    assert read_skill(skill_id) == before
    card = _cards(wf.id, "clarify_question")[0]
    assert card["state"] == "failed" and card["actions"] == ["checked"]


def test_a_dead_attempt_that_changed_nothing_can_be_tried_again(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    version = (read_skill(skill_id).get("meta") or {}).get("version")
    q1 = _cards(wf.id, "clarify_question")[0]
    _mark_applying(wf, q1, since=1.0, base_version=version)
    assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")["ok"]
    assert _step_actions(read_skill(skill_id)).count("agent_span") == 1


def test_a_live_attempt_is_refused_so_a_double_click_cannot_apply_twice(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    q1 = _cards(wf.id, "clarify_question")[0]
    _mark_applying(wf, q1, since=time.time(), base_version=1)
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")
    assert r["ok"] is False and r["code"] == "not_open"


def test_two_writers_to_the_question_file_do_not_lose_each_others_change(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    qs = clarify_store.load(wf.id)["questions"]
    a, c = qs[0]["id"], qs[1]["id"]

    def hammer(qid, tag):
        for n in range(40):
            clarify_store.update_question(wf.id, qid, receipt=f"{tag}{n}")

    threads = [threading.Thread(target=hammer, args=(a, "a")), threading.Thread(target=hammer, args=(c, "c"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    got = {q["id"]: q["receipt"] for q in clarify_store.load(wf.id)["questions"]}
    assert got[a] == "a39" and got[c] == "c39"


# ── BUILD-55 A4: no question waits on wording ───────────────────────────────────────────────


def test_the_first_question_appears_in_template_wording_and_is_reworded_in_the_background(env, monkeypatch):
    b, wf, _out = env
    gate = threading.Event()

    def fake(task, payload, timeout_ms, **k):
        assert gate.wait(5)                      # the card must already be on screen while this waits
        return {"questions": [{"id": q["id"], "title": "Friendly: " + q["title"]} for q in payload["input"]["questions"]]}

    monkeypatch.setattr("conxa_compile.llm.clarify_question_copy.call_llm", fake)
    _fake_compile(b, wf)
    assert b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")["ok"]
    first = _cards(wf.id, "clarify_question")[0]
    assert first["state"] == "open" and not first["title"].startswith("Friendly")

    gate.set()
    deadline = time.time() + 5
    while time.time() < deadline and not _cards(wf.id, "clarify_question")[0]["title"].startswith("Friendly"):
        time.sleep(0.05)
    card = _cards(wf.id, "clarify_question")[0]
    assert card["title"].startswith("Friendly:") and card["state"] == "open"
    assert clarify_store.load(wf.id)["questions"][0]["title"].startswith("Friendly:")      # later questions are posted reworded
    assert [c["id"] for c in card["choices"]] == [c["id"] for c in first["choices"]]       # wording never changes the choices


def test_a_question_answered_while_the_wording_call_runs_is_left_alone(env, monkeypatch):
    b, wf, _out = env
    gate = threading.Event()

    def fake(task, payload, timeout_ms, **k):
        gate.wait(5)
        return {"questions": [{"id": q["id"], "title": "Friendly: " + q["title"]} for q in payload["input"]["questions"]]}

    monkeypatch.setattr("conxa_compile.llm.clarify_question_copy.call_llm", fake)
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    q1 = _cards(wf.id, "clarify_question")[0]
    assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "no"}}, "r")["ok"]
    gate.set()
    time.sleep(0.5)
    done = _cards(wf.id, "clarify_question")[0]
    assert done["state"] == "answered" and not done["title"].startswith("Friendly")


# ── BUILD-55 B5: a check cut short by a restart is rebuilt when the workflow is opened ─────


def _wait_for(cond, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline and not cond():
        time.sleep(0.05)
    return cond()


def test_questions_lost_to_a_restart_are_rebuilt_when_the_workflow_opens_and_without_a_model_call(env, monkeypatch):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    # what a restart mid-check leaves behind: the durable marker only; every in-memory flag is gone
    clarify_store.save(wf.id, {"skill_id": skill_id, "version": 1, "building": True, "questions": []})
    runtime_state.clear_all()
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda *a, **k: pytest.fail("a rebuild must not call a model"))

    page = b.cmd_thread_load({"thread_id": f"wf:{wf.id}"}, "r")
    assert page["lifecycle"]["stage"] == "clarify"              # checking, or already asking: never "ready to test"
    assert _wait_for(lambda: (clarify_store.load(wf.id) or {}).get("questions"))
    data = clarify_store.load(wf.id)
    assert not data.get("building") and data["skill_id"] == skill_id
    assert _wait_for(lambda: runtime_state.get_flags(wf.id) == {})
    assert lifecycle_for_workflow(get_workflow(wf.id))["stage"] == "clarify"


def test_a_workflow_compiled_before_questions_existed_is_left_alone(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    clarify_store.clear(wf.id)                                  # no file at all: not an interrupted check
    b.cmd_thread_load({"thread_id": f"wf:{wf.id}"}, "r")
    time.sleep(0.3)
    assert clarify_store.load(wf.id) is None and runtime_state.get_flags(wf.id) == {}


def test_a_rebuild_that_fails_clears_the_marker_so_opening_it_again_does_not_repeat_the_failure(env, monkeypatch):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    clarify_store.save(wf.id, {"skill_id": skill_id, "version": 1, "building": True, "questions": []})
    runtime_state.clear_all()
    monkeypatch.setattr(b, "_flow_after_compile", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    b.cmd_thread_load({"thread_id": f"wf:{wf.id}"}, "r")
    assert _wait_for(lambda: clarify_store.load(wf.id) is None and runtime_state.get_flags(wf.id) == {})
    assert "Couldn’t prepare the questions" in _texts(wf.id, "event")
    assert "test" in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]


# ── Phase 0: the pencil after the build ──


def test_the_pencil_after_the_build_restores_the_workflow_and_asks_the_question_again(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    skill_id = get_workflow(wf.id).skill_id
    before = read_skill(skill_id)
    q1 = _cards(wf.id, "clarify_question")[0]
    assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")["ok"]
    assert len(read_skill(skill_id)["skills"][0]["steps"]) == len(before["skills"][0]["steps"]) + 1       # the confirm step was added
    assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "reopen"}, "r")["ok"]
    assert read_skill(skill_id)["skills"][0]["steps"] == before["skills"][0]["steps"]                      # the answer is undone
    first = next(c for c in _cards(wf.id, "clarify_question") if c["card_id"] == q1["card_id"])
    assert first["state"] == "open"


def test_a_rebuild_does_not_ask_again_what_was_already_settled_unless_it_is_a_safety_question(env):
    from conxa_compile.conversation import clarify_store

    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    for _ in range(6):                                           # answer everything the first build asked
        open_cards = [c for c in _cards(wf.id, "clarify_question") if c["state"] == "open"]
        if not open_cards:
            break
        c = open_cards[0]
        payload = {"selected": [x["id"] for x in c["choices"] if x.get("selected")]} if c.get("multi") else {"choice": c["choices"][0]["id"]}
        assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": c["card_id"], "action": "choose", "payload": payload}, "r")["ok"]
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid2")           # a rebuild, as after the pencil on an earlier question
    by_kind = {q["kind"]: q for q in clarify_store.load(wf.id)["questions"]}
    assert by_kind["values"]["status"] == "answered" and by_kind["values"]["carried"] is True
    assert by_kind["confirm"]["status"] == "open"                # a safety question is asked every time


# ── Phase 2: scoring on ──


def test_scoring_on_keeps_asking_what_it_must_and_lists_what_it_was_sure_about(env, monkeypatch):
    from conxa_compile.conversation import clarify_store
    from conxa_core.config import settings

    monkeypatch.setattr(settings, "clarify_scoring", "on")
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    data = clarify_store.load(wf.id)
    by_kind = {q["kind"]: q for q in data["questions"]}
    assert by_kind["confirm"]["status"] == "open" and by_kind["confirm"]["decision"]["action"] == "ask"       # the safety floor still asks
    assert by_kind["values"]["status"] == "open" and by_kind["values"]["decision"]["action"] == "confirm"
    assert "assumptions" not in data                                                # nothing here was cheap enough to assume
    # an assumed question is listed in the summary and comes back on "ask me"
    clarify_store.mutate(wf.id, lambda d: (d["questions"].append({"id": "q_assumed", "kind": "done_when", "title": "How can we tell it worked?",
                                                                 "status": "assumed", "assumed": True, "body": "", "choices": [], "step_keys": []}),
                                           d.__setitem__("assumptions", [{"qid": "q_assumed", "kind": "done_when", "title": "How can we tell it worked?",
                                                                          "default": "No checks needed"}])))
    b._flow_post_summary(wf.id, lambda e: None)
    summary = _cards(wf.id, "workflow_summary")[-1]["summary"]
    assert summary["assumptions"] == [{"qid": "q_assumed", "kind": "done_when", "title": "How can we tell it worked?", "default": "No checks needed"}]
    # what the harness was sure about is not a question: it blocks nothing, and "ask me" brings it forward
    from conxa_core.storage.workflow_store import get_workflow

    assert clarify_store.summary(wf.id)["assumed"] == 1
    assert clarify_store.summary(wf.id)["unanswered"] == len([q for q in clarify_store.load(wf.id)["questions"] if q["status"] == "open"])
    for q in clarify_store.load(wf.id)["questions"]:
        if q["status"] == "open":
            clarify_store.update_question(wf.id, q["id"], status="answered")
    assert "test" in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]
    b._flow_post_next_question(wf.id, lambda e: None, promote_assumed=True)
    assert clarify_store.get_question(wf.id, "q_assumed")["status"] == "open"


# ── Phase 3: re-evaluation after an answer ──


def test_an_answer_that_changes_the_workflow_raises_the_question_it_newly_makes_possible_once(env):
    from conxa_compile.conversation import clarify_store
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import get_workflow

    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    skill_id = get_workflow(wf.id).skill_id
    before = {q["id"] for q in clarify_store.load(wf.id)["questions"]}
    assert not any(k.startswith("missing_item") for k in before)
    doc = read_skill(skill_id)                       # as if an earlier answer turned a clicked item into a run input
    doc["skills"][0]["steps"].append({"action": {"action": "click"}, "intent": "Delete row",
                                      "entity_binding": {"container_selector": "tr", "identifier": "{{row}}"}})
    write_skill(skill_id, doc)
    b._flow_reevaluate(wf.id)
    added = [q for q in clarify_store.load(wf.id)["questions"] if q["id"] not in before]
    assert [q["kind"] for q in added] == ["missing_item"] and added[0]["status"] in ("open", "queued")
    b._flow_reevaluate(wf.id)                        # asked now: never asked twice
    assert len([q for q in clarify_store.load(wf.id)["questions"] if q["kind"] == "missing_item"]) == 1


# ── Every question is a hard blocker, and an unanswered one keeps coming back ───────────────


def test_an_unanswered_question_blocks_testing_approval_publishing_and_an_early_build(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    from conxa_core.storage.workflow_store import get_workflow

    allowed = lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]
    assert "test" not in allowed and "approve" not in allowed and "publish" not in allowed
    with pytest.raises(_CommandError) as e:
        b._require_no_unanswered_questions([get_workflow(wf.id)])      # the publish / installer gate
    assert e.value.code == "unanswered_questions"
    # a question still open while the recording is being understood also holds the build, whichever door is used
    clarify_store.mutate(wf.id, lambda d: d.__setitem__("stage", "understand"))
    with pytest.raises(_CommandError) as e:
        b.cmd_flow_compile({"workflow_id": wf.id}, "rid2")
    assert e.value.code == "unanswered_questions"


def test_every_direct_door_refuses_a_workflow_with_an_unanswered_question(env):
    """WF-5 5.1: the Publish page's Run test went straight to cmd_test_workflow and ran. Every door that
    tests, approves or builds a release must refuse on its own, not rely on the lifecycle strip."""
    from conxa_core.storage.workflow_store import get_workflow

    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    wf = get_workflow(wf.id)
    doors = [
        lambda: b.cmd_test_workflow({"workflow_id": wf.id}, "t"),                          # Publish / group page Run test
        lambda: b.cmd_sign_off_workflow({"skill_id": wf.skill_id}, "s"),                   # direct approval
        lambda: b.cmd_build_skill_package({"skill_slug": wf.slug}, "b"),                    # release build
    ]
    for door in doors:
        with pytest.raises(_CommandError) as e:
            door()
        assert e.value.code == "unanswered_questions"
    assert not get_workflow(wf.id).signed_off
    # A sign-in that expired mid-test offers "test" again, but never past an open question.
    runtime_state.set_flags(wf.id, sign_in_expired=True)
    assert "test" not in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]


def test_a_test_of_a_workflow_missing_from_the_built_copy_says_so_plainly(env, monkeypatch):
    """Not the runtime's "Skill not found: <slug>. Call list_skills." (WF-5 5.1)."""
    from conxa_core.models.workflow import SkillPack, SkillPackBuild
    from conxa_core.storage.workflow_store import get_workflow, save_workflow

    b, wf, _out = env
    wf = get_workflow(wf.id)
    wf.skill_id, wf.edited_at = "skill_x", 1.0
    save_workflow(wf)
    pack = SkillPack(workspace_id="ws", company_slug="c", company_name="C", created_at=0.0, updated_at=0.0,
                     build=SkillPackBuild(last_built_at=2.0, output_path=str(settings.data_dir / "skill-packs" / "c"), version="0.1.0"))
    monkeypatch.setattr("conxa_core.storage.skill_pack_store.get_skill_pack", lambda _ws: pack)
    with pytest.raises(_CommandError) as e:
        b.cmd_test_workflow({"workflow_id": wf.id}, "t")
    assert e.value.code == "workflow_not_built"
    assert "list_skills" not in e.value.message and wf.name in e.value.message


def test_opening_the_conversation_asks_an_unanswered_question_again_but_never_stacks_a_second_card(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    before = len(_cards(wf.id, "clarify_question"))
    b._reask_unanswered(wf.id, lambda e: None)
    assert len(_cards(wf.id, "clarify_question")) == before          # its card is still on screen: nothing added
    first = [q for q in clarify_store.load(wf.id)["questions"] if q["status"] == "open"][0]
    clarify_store.update_question(wf.id, first["id"], card_id=None)  # as if the card had been lost
    b._reask_unanswered(wf.id, lambda e: None)
    assert len(_cards(wf.id, "clarify_question")) == before + 1


def test_a_defer_decision_leaves_the_question_open(env, monkeypatch):
    from conxa_compile.conversation import question_plan
    from conxa_core.config import settings as cfg

    monkeypatch.setattr(cfg, "clarify_scoring", "on")
    qs = [{"id": "d", "kind": "relative_date", "status": "open", "decision": {"action": "defer"}, "step_keys": [], "choices": []}]
    monkeypatch.setattr("conxa_compile.conversation.scoring.decide", lambda q, doc: {"action": "defer"})
    out, _ = question_plan.apply_scoring(qs, {"skills": [{"steps": []}]}, "post_compile")
    assert out[0]["status"] == "open"


# ── the "workflow ready" message is written by a model over code-built facts ──────────────────


def _first_message(env, monkeypatch, fake):
    b, wf, _out = env
    monkeypatch.setattr("conxa_compile.llm.compile_recap.call_llm", fake)
    _fake_compile(b, wf)
    assert b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")["ok"]
    return _texts(wf.id, "assistant_message")[0]


def test_the_ready_message_uses_the_models_wording_when_it_checks_out(env, monkeypatch):
    seen = {}

    def fake(task, payload, timeout_ms, **k):
        seen.update(json.loads(payload["user_text"]))
        names = " and ".join(seen["unsure_steps"])
        return {"message": f"Your workflow has {seen['step_count']} steps. I'm not sure about {names}, so please check it. I have a few quick questions first."}

    text = _first_message(env, monkeypatch, fake)
    assert text.startswith("Your workflow has 4 steps.")
    assert seen["step_count"] == 4 and seen["unsure_steps"]
    assert "target" not in json.dumps(seen["left_out"])  # kinds and counts only, never page text


def test_a_recap_that_drops_a_fact_is_retried_with_the_reason(env, monkeypatch):
    calls = []

    def fake(task, payload, timeout_ms, **k):
        facts = json.loads(payload["user_text"])
        calls.append(facts.get("previous_answer_rejected_because"))
        if len(calls) == 1:
            return {"message": "All done, everything looks great and is ready for you to use now."}
        return {"message": f"Your workflow has {facts['step_count']} steps. " + " ".join(facts["unsure_steps"]) + " needs a check."}

    assert _first_message(env, monkeypatch, fake).startswith("Your workflow has 4 steps.")
    assert calls[0] is None and "number of steps" in calls[1]


def test_when_the_recap_never_works_no_stand_in_is_posted_and_a_warning_shows(env, monkeypatch):
    b, wf, _out = env

    def boom(*a, **k):
        raise RuntimeError("cloud down")

    monkeypatch.setattr("conxa_compile.llm.compile_recap.call_llm", boom)
    _fake_compile(b, wf)
    assert b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")["ok"]
    assert not any("ready" in t for t in _texts(wf.id, "assistant_message"))
    assert "Couldn’t write the summary of what was built" in _texts(wf.id, "event")
    assert _cards(wf.id, "clarify_question")  # the questions still follow


def test_saving_a_step_with_nothing_edited_writes_nothing(env):
    """WF-5: Human Edit's "Save step" on an untouched step turned a bare action kind into its object form, bumped
    the version and reset the test and approval."""
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import get_workflow

    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    skill_id = get_workflow(wf.id).skill_id
    doc = read_skill(skill_id)
    doc["skills"][0]["steps"][0].update(action="navigate", url="https://billing.example.test/new")
    write_skill(skill_id, doc)
    before = read_skill(skill_id)
    b.cmd_patch_step({"skill_id": skill_id, "step_index": 0,
                      "patch": {"action": {"action": "navigate"}, "url": "https://billing.example.test/new"}}, "r")
    assert read_skill(skill_id) == before


def test_a_human_edit_link_keeps_element_addresses_behind_details(env):
    from conxa_compile.conversation import harness

    b, wf, _out = env
    card = harness.post_editor_hint_card(f"wf:{wf.id}", None, {"reason": 'Use #userEmail, not role=textbox[name="x"]'}, None)
    assert "role=" not in card["title"] and "#userEmail" in card["detail"]
    plain = harness.post_editor_hint_card(f"wf:{wf.id}", None, {"reason": "The email box moved"}, None)
    assert plain["title"].endswith("The email box moved") and not plain["detail"]



@pytest.mark.parametrize("text,mapped", [
    ("wait ten seconds, then continue on its own", "no"),   # WF-5 E: closed as "no", the approval gone
    ("only ask on the first run", "yes"),                   # WF-5 D: closed as "yes", "first run" dropped
])
def test_a_wish_on_a_step_that_cant_be_undone_is_never_read_as_an_option(env, monkeypatch, text, mapped):
    b, wf, _out = env
    _skill_id, q = _wish_env(b, wf, monkeypatch, [{"id": "p1", "command": "patch_step", "step_key": "k", "field": "note",
                                                    "patch": {"semantic_description": "x"}}], mapped=mapped)
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "own",
                           "payload": {"custom_text": text}}, "r")
    assert r.get("pending_change") is True
    got = clarify_store.get_question(wf.id, q["question_id"])
    assert got["status"] == "open" and got.get("answer") is None


def test_how_can_we_tell_it_worked_refuses_words_with_no_letters(env):
    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    q = {"id": "done_when", "kind": "done_when", "multi": True, "title": "How can we tell it worked?", "choices": [],
         "step_keys": [], "status": "open", "answer": None, "receipt": ""}
    clarify_store.mutate(wf.id, lambda d: d["questions"].append(dict(q)))
    from conxa_compile.conversation import harness

    card = harness.post_card(f"wf:{wf.id}", "clarify_question", "open", sink=None, refs={"question_id": q["id"]},
                             question_id=q["id"], choices=q["choices"], actions=["choose"])
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": "choose",
                           "payload": {"selected": [], "custom_text": "😀😀🎉"}}, "r")
    assert r["ok"] is False and r["code"] == "answer_unclear"
    assert clarify_store.get_question(wf.id, q["id"])["status"] == "open"



def test_a_new_typed_attempt_or_a_failed_one_leaves_no_stale_change_card(env, monkeypatch):
    """WF-5 F: a failed attempt left the earlier change card open; chat called it waiting and Test it was refused."""
    from conxa_compile.conversation import harness

    b, wf, _out = env
    props = [{"id": "p1", "command": "patch_step", "step_key": "k", "field": "note", "patch": {"semantic_description": "x"}}]
    _skill_id, q = _wish_env(b, wf, monkeypatch, props)
    send = lambda t: b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "own",  # noqa: E731
                                        "payload": {"custom_text": t}}, "r")
    send("ask my manager by email first")
    send("ask my manager by text first")
    assert len([c for c in _cards(wf.id, "pending_change") if c["state"] == "open"]) == 1   # one wish, one live card
    monkeypatch.setattr(harness, "_propose", lambda **kw: [])
    assert send("do it some other way")["code"] == "wish_not_applied"
    assert not [c for c in _cards(wf.id, "pending_change") if c["state"] == "open"]          # a failed attempt leaves none
    send_choice = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "choose",
                                     "payload": {"choice": "yes"}}, "r")
    assert send_choice["ok"]


def test_a_failed_proposal_is_tried_once_more(env, monkeypatch):
    from conxa_compile.conversation import harness

    b, wf, _out = env
    _skill_id, q = _wish_env(b, wf, monkeypatch, [])
    calls = []
    props = [{"id": "p1", "command": "patch_step", "step_key": "k", "field": "note", "patch": {"semantic_description": "x"}}]
    monkeypatch.setattr(harness, "_propose", lambda **kw: (calls.append(1), props if len(calls) == 2 else [])[1])
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "own",
                           "payload": {"custom_text": "ask my manager by email first"}}, "r")
    assert r.get("pending_change") and len(calls) == 2


def test_summary_cards_never_count_as_waiting():
    from conxa_compile.conversation import thread_store as ts

    items = [{"kind": "card", "card_type": "workflow_summary", "state": "open"},
             {"kind": "card", "card_type": "test_inputs", "state": "open"}]
    assert [c["card_type"] for c in ts.open_cards(items)] == ["test_inputs"]


def test_a_check_change_reads_in_plain_words():
    from conxa_compile.editor.copilot_proposals import proposal_summary

    p = {"command": "patch_step", "field": "validation.assertions", "step_label": "“Submit”",
         "preview": {"before": [{"type": "state_changed"}],
                     "after": [{"type": "state_changed"}, {"type": "text_present", "target": "{{user_email}}"}]}}
    text = proposal_summary(p)
    assert text == "After “Submit”, also check that the page shows the user email you typed"
    assert "validation" not in text


def test_a_described_check_is_a_wish_and_long_screen_words_are_refused(env, monkeypatch):
    """WF-5 F: "check the output shows the email I typed" was saved as words to look for, and 5,000 characters were
    cut to 120."""
    from conxa_compile.conversation import harness

    b, wf, _out = env
    props = [{"id": "p1", "command": "patch_step", "step_key": "k", "field": "note", "patch": {"semantic_description": "x"}}]
    _wish_env(b, wf, monkeypatch, props)
    q = {"id": "done_when", "kind": "done_when", "multi": True, "title": "How can we tell it worked?", "choices": [],
         "step_keys": [], "status": "open", "answer": None, "receipt": ""}
    clarify_store.mutate(wf.id, lambda d: d["questions"].append(dict(q)))
    card = harness.post_card(f"wf:{wf.id}", "clarify_question", "open", sink=None, refs={"question_id": "done_when"},
                             question_id="done_when", choices=[], actions=["choose"])
    pick = lambda t: b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": "choose",  # noqa: E731
                                        "payload": {"selected": [], "custom_text": t}}, "r")
    assert pick("x" * 300)["code"] == "answer_too_long"
    r = pick("check the output shows the email I typed")
    assert r.get("pending_change") and clarify_store.get_question(wf.id, "done_when")["status"] == "open"


def test_undoing_a_wishs_change_opens_its_question_again(env, monkeypatch):
    """BUILD-104 (4): a question is closed only while the change that answered it is applied."""
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    b, wf, _out = env
    props = [{"id": "p1", "command": "patch_step", "step_key": "k", "field": "note", "patch": {"semantic_description": "x"}}]
    skill_id, q = _wish_env(b, wf, monkeypatch, props)
    before = read_skill(skill_id)

    def apply(sid, _props, rid):        # through a real command, so it leaves an undo entry like the real path
        b.cmd_patch_step({"skill_id": sid, "step_index": 1, "patch": {"semantic_description": "changed by the wish"}}, rid)
    monkeypatch.setattr(b, "_apply_change_set", apply)
    b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q["card_id"], "action": "own",
                       "payload": {"custom_text": "ask my manager by email first"}}, "r")
    change = _cards(wf.id, "pending_change")[-1]
    b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": change["card_id"], "action": "apply"}, "r")
    assert clarify_store.get_question(wf.id, q["question_id"])["status"] == "answered"
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": change["card_id"], "action": "undo"}, "r")
    assert r["ok"], r
    assert clarify_store.get_question(wf.id, q["question_id"])["status"] == "open"
    assert read_skill(skill_id)["skills"][0]["steps"] == before["skills"][0]["steps"]     # change and approval both gone
    assert "test" not in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]
    card = next(c for c in _cards(wf.id, "clarify_question") if c["card_id"] == q["card_id"])
    assert card["state"] == "open" and "undone" in card["note"]


def test_a_check_wish_lands_on_the_questions_step_even_with_an_approval_step_before_it(env, monkeypatch):
    """WF-5 H/G: with an approval (agent) step inserted before Submit, the wish never became a change, or changed the
    approval step's goal instead. The check goes on the question's own step, and no wish touches an agent step."""
    from conxa_compile.compiler.step_key import step_keys
    from conxa_compile.conversation import harness
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    skill_id = get_workflow(wf.id).skill_id
    b.cmd_insert_step({"skill_id": skill_id, "action_kind": "agent_span", "insert_after": 2, "confirm_only": True}, "r")
    steps = read_skill(skill_id)["skills"][0]["steps"]
    keys = step_keys(steps)
    send_key = keys[-1]
    assert steps[-2]["action"]["action"] == "agent_span" and steps[-1]["intent"] == "send_invoice"
    monkeypatch.setattr(b, "_install_proxy_router", lambda **kw: None)
    monkeypatch.setattr(harness, "_propose", lambda **kw: pytest.fail("the template needs no model"))
    q = {"id": "done_when", "kind": "done_when", "multi": True, "title": "How can we tell it worked?", "choices": [],
         "step_keys": [send_key], "status": "open", "answer": None, "receipt": ""}
    clarify_store.mutate(wf.id, lambda d: d["questions"].append(dict(q)))
    card = harness.post_card(f"wf:{wf.id}", "clarify_question", "open", sink=None, refs={"question_id": "done_when"},
                             question_id="done_when", choices=[], actions=["choose"])
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": "choose",
                           "payload": {"selected": [], "custom_text": "after Submit, also check the page shows the description I typed"}}, "r")
    assert r.get("pending_change"), r
    change = _cards(wf.id, "pending_change")[-1]
    assert [p["step_key"] for p in change["proposals"]] == [send_key]
    assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": change["card_id"], "action": "apply"}, "r")["ok"]
    after = read_skill(skill_id)["skills"][0]["steps"]
    assert {"type": "text_present", "target": "{{description}}", "required": True} in after[-1]["validation"]["assertions"]
    assert after[-2] == steps[-2]                                                     # the approval step is untouched
    assert clarify_store.get_question(wf.id, "done_when")["status"] == "answered"


def test_a_wish_never_changes_an_agent_or_approval_step():
    from handlers.compile_flow import _without_agent_steps

    doc = {"skills": [{"steps": [{"action": {"action": "agent_span"}, "identity_bundle": {"stable_hash": "a"}},
                                 {"action": {"action": "click"}, "identity_bundle": {"stable_hash": "c"}}]}]}
    props = [{"step_key": "a#1", "field": "value", "is_agent_goal": True}, {"step_key": "a#1", "field": "agent_span_allowed"},
             {"op": "delete_step", "step_key": "a#1"}, {"step_key": "c#1", "field": "validation.assertions"}]
    assert _without_agent_steps(props, doc) == [props[-1]]


def test_an_unedited_save_of_a_navigate_step_with_no_action_object_writes_nothing(env):
    """WF-5 I: the step stored `action: "navigate"` (so `action.action` was null); Human Edit's Save sent the shown
    defaults (action with its url, the same checks, an empty check list for an absent one), which bumped the version, revoked the approval
    and changed an address key."""
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import get_workflow

    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    skill_id = get_workflow(wf.id).skill_id
    u = "https://billing.example.test/new"
    doc = read_skill(skill_id)
    doc["skills"][0]["steps"][0].update(
        action="navigate", url=u, semantic_description="Open the invoice page",
        validation={"wait_for": {"type": "url_change", "target": u, "timeout": 60000}, "success_conditions": {"url": u}})
    write_skill(skill_id, doc)
    before = read_skill(skill_id)
    shown = {"semantic_description": "Open the invoice page", "url": u, "action": {"action": "navigate", "url": u},
             "validation": {"wait_for": {"type": "url_change", "target": u, "timeout": 60000},
                            "success_conditions": {"url": u}, "assertions": []}}
    b.cmd_patch_step({"skill_id": skill_id, "step_index": 0, "patch": shown}, "r")
    assert read_skill(skill_id) == before
    b.cmd_patch_step({"skill_id": skill_id, "step_index": 0, "patch": {**shown, "semantic_description": "Open it"}}, "r")
    assert read_skill(skill_id)["meta"]["version"] == before["meta"]["version"] + 1      # a real edit still saves


def test_a_chat_change_leaves_every_address_key_as_it_was(env):
    """WF-5 5.8: renaming an input through chat re-derived an empty `recovery.intent` on the step (an address key)."""
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import get_workflow
    from conxa_compile.editor.capability_manifest import ADDRESS_KEYS

    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    skill_id = get_workflow(wf.id).skill_id
    doc = read_skill(skill_id)
    doc["skills"][0]["steps"][1]["recovery"] = {"intent": "", "final_intent": "", "anchors": [], "strategies": []}
    write_skill(skill_id, doc)
    before = read_skill(skill_id)["skills"][0]["steps"][1]

    def rename(payload, rid):           # an edit command that re-derives shadows, as cmd_patch_step does
        d = read_skill(skill_id)
        s = d["skills"][0]["steps"][1]
        s.update(value="{{contact}}", input_binding="contact")
        s["recovery"] = {**s["recovery"], "intent": "enter_po_number", "final_intent": "enter_po_number"}
        write_skill(skill_id, d)
        return {}
    b.cmd_accept_copilot_proposal = rename
    b._apply_change_set(skill_id, [{"id": "p1"}], "r")
    after = read_skill(skill_id)["skills"][0]["steps"][1]
    assert after["value"] == "{{contact}}"                                        # the change itself stands
    assert {k: after.get(k) for k in ADDRESS_KEYS} == {k: before.get(k) for k in ADDRESS_KEYS}


def test_an_address_check_names_the_address():
    from conxa_compile.editor.copilot_proposals import proposal_summary

    p = {"command": "patch_step", "field": "validation.assertions", "step_label": "“Open”",
         "preview": {"before": [], "after": [{"type": "url_pattern", "expected": "demoqa.com/text-box"}]}}
    assert "text-box" in proposal_summary(p)


def test_dropping_an_input_names_its_row_by_the_recorded_value_and_removes_the_input(env):
    """WF-7: a row bound to {{search_by_name}} kept the unticked input alive, so the run still asked for it."""
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import get_workflow

    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    skill_id = get_workflow(wf.id).skill_id
    doc = read_skill(skill_id)
    doc["inputs"] = list(doc.get("inputs") or []) + [{"id": "search_by_name", "type": "text"}]
    doc["skills"][0]["steps"].append({"action": {"action": "click"}, "intent": "Edit row",
                                      "entity_binding": {"container_selector": "tbody > tr",
                                                         "identifier": "{{search_by_name}}", "source": "input"}})
    write_skill(skill_id, doc)
    b._apply_clarify_ops(skill_id, [{"op": "drop_inputs", "ids": ["search_by_name"],
                                     "literals": {"search_by_name": "Carol"}}], "rid2")
    after = read_skill(skill_id)
    assert after["skills"][0]["steps"][-1]["entity_binding"]["identifier"] == "Carol"
    assert after["skills"][0]["steps"][-1]["entity_binding"]["source"] == "literal"
    assert not any(i.get("id") == "search_by_name" for i in after.get("inputs") or [])


def test_the_robot_check_answer_inserts_a_hand_over_step_by_code(env):
    """WF-8 8.13: "Add a hand-over step" puts one where the robot check was, worded for the person who does it."""
    from conxa_compile.compiler.step_key import step_keys
    from conxa_core.storage.json_store import read_skill
    from conxa_core.storage.workflow_store import get_workflow

    b, wf, _out = env
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    skill_id = get_workflow(wf.id).skill_id
    steps = read_skill(skill_id)["skills"][0]["steps"]
    added = b._apply_clarify_ops(skill_id, [{"op": "insert_handover", "after_step_key": step_keys(steps)[0],
                                             "message": "Please do the robot check, then continue."}], "rid2")
    after = read_skill(skill_id)["skills"][0]["steps"]
    assert added == 1 and len(after) == len(steps) + 1
    hand = after[1]
    assert (hand["action"] if isinstance(hand["action"], str) else hand["action"]["action"]) == "handover"
    assert hand["value"] == "Please do the robot check, then continue."
