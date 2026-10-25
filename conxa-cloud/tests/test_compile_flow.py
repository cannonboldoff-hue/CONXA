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
    assert "Compiled" not in _texts(wf.id, "event")  # the card carries it
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


def test_questions_past_the_cap_are_queued_not_lost_and_come_back_when_asked(env):
    b, wf, _out = env

    def many(skill_id):
        steps = [_step("navigate", "open")] + [_step("click", f"send_{i}", consequence="irreversible") for i in range(1, 9)]
        return {"meta": {"id": skill_id, "title": "Many", "version": 1}, "skills": [{"id": skill_id, "steps": steps}], "inputs": [],
                "compile_report": {"status": "ok", "steps": [{"warnings": []}] * 9}}

    _fake_compile(b, wf, doc_fn=many)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid1")
    data = clarify_store.load(wf.id)
    assert [q["status"] for q in data["questions"]] == ["open"] * 5 + ["queued"] * 3
    assert clarify_store.summary(wf.id)["queued"] == 3
    first = _cards(wf.id, "clarify_question")[0]
    assert first["total"] == 5                                      # the queue is not part of "1 of N"
    for _ in range(5):
        card = [c for c in _cards(wf.id, "clarify_question") if c["state"] == "open"][0]
        b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": "skip"}, "r")
    assert "held back 3 smaller questions" in _texts(wf.id, "assistant_message")[-1]
    from conxa_core.storage.workflow_store import get_workflow

    assert "answer_clarify" in lifecycle_for_workflow(get_workflow(wf.id))["allowed_actions"]
    assert b._run_flow_action(wf.id, "answer_clarify", {}, "r", lambda e: None)["ok"]
    assert [q["status"] for q in clarify_store.load(wf.id)["questions"]][5:] == ["open"] * 3


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
    assert card["state"] == "failed" and card["actions"] == ["choose", "skip"]
    assert clarify_store.get_question(wf.id, q1["question_id"])["status"] == "open"

    monkeypatch.setattr(b, "cmd_patch_step", real_patch)
    r = b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "choose", "payload": {"choice": "yes"}}, "r")
    assert r["ok"]
    assert _step_actions(read_skill(skill_id)).count("ai_review") == 1   # not two


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
    assert card["state"] == "failed" and card["actions"] == ["skip"]


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
    assert _step_actions(read_skill(skill_id)).count("ai_review") == 1


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
    assert b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": q1["card_id"], "action": "skip"}, "r")["ok"]
    gate.set()
    time.sleep(0.5)
    done = _cards(wf.id, "clarify_question")[0]
    assert done["state"] == "skipped" and not done["title"].startswith("Friendly")


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
