"""Understanding-first flow: watch the recording, ask one round, store decisions, then compile."""

from __future__ import annotations

import json

import pytest

from conxa_compile.conversation import clarify_store, decisions, runtime_state
from conxa_compile.conversation import timeline_understanding as tu
from conxa_compile.conversation.lifecycle import lifecycle_for_workflow
from conxa_compile.video import track_a, track_b
from conxa_core.config import settings
from conxa_core.storage.workflow_store import get_workflow, save_workflow
from services.video_analysis_client import VideoUnavailable
from tests.test_compile_flow import _cards, _texts, env  # noqa: F401 -- env is a fixture


def _event(n, action, target="", value=None, url="https://billing.example.test/new"):
    return {"action": {"action": action, "value": value}, "target": {"inner_text": target}, "semantic": {"role": "button"},
            "page": {"url": url}, "tab": {"id": "tab_0"}, "visual": {"timestamp_ms": n * 1000}, "extras": {"sequence": n}}


TIMED = [{"ev": _event(1, "manual_navigate", value=json.dumps({"to_url": "https://billing.example.test/acme/invoices"})),
          "tab": "tab_0", "wall": 100_500},
         {"ev": _event(2, "click", "Send invoice"), "tab": "tab_0", "wall": 102_000}]
RAW = {
    "goal": "Create and send an invoice", "short_summary": "Types the PO number and sends the invoice.",
    "values": [{"ref": "url:1", "verdict": "varies", "name": "customer", "literal": "acme"}],
    "questions": [{"kind": "confirm_before", "row": "r2", "prompt": "Check before sending?", "best_guess": "confirm", "cost_rank": 1}],
}


@pytest.fixture()
def flow(env, monkeypatch, tmp_path):  # noqa: F811
    b, wf, _out = env
    (settings.data_dir / "sessions" / "sess1").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(b, "_blur_all_typed_values", lambda: False)
    monkeypatch.setattr(b, "_install_proxy_router", lambda *a, **k: None)
    monkeypatch.setattr(track_a, "run_track_a", lambda d, c, blur_all=False: {"rows": [], "splice_map": []})
    monkeypatch.setattr(track_b, "run_track_b", lambda d: TIMED)
    monkeypatch.setattr(tu, "_default_call", lambda task, payload, ms: RAW)
    compiled: list[str] = []
    monkeypatch.setattr(b, "_flow_start_compile", lambda wid: compiled.append(wid))
    return b, wf, compiled


def _answer(b, wf, card, action="choose", **payload):
    return b.cmd_card_action({"thread_id": f"wf:{wf.id}", "card_id": card["card_id"], "action": action, "payload": payload}, "r")


def _open_cards(wf):
    return [c for c in _cards(wf.id, "clarify_question") if c["state"] == "open"]


def test_watching_then_one_round_of_questions_and_no_compile_yet(flow):
    b, wf, compiled = flow
    b._flow_understand(wf.id, "rid")
    watching = _cards(wf.id, "watching_video")
    assert [c["state"] for c in watching] == ["done"]
    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert (lc["stage"], lc["substate"]) == ("understand", "open")
    assert lc["allowed_actions"] == ["answer_clarify", "re_record"]
    data = clarify_store.load(wf.id)
    assert data["stage"] == "understand" and all(q["phase"] == "pre_compile" for q in data["questions"])
    assert [c["question_kind"] for c in _open_cards(wf)] == ["confirm_before"]        # one at a time, most costly first
    assert {q["kind"] for q in data["questions"]} == {"confirm_before", "value"}      # no fixed summary/coverage card
    said = _texts(wf.id, "assistant_message")[-1]
    assert said.startswith("Types the PO number and sends the invoice.") and "A few quick questions" in said
    assert runtime_state.get_flags(wf.id) == {} and compiled == []
    session = settings.data_dir / "sessions" / "sess1"
    assert (session / "understanding.json").is_file() and (session / "merged_timeline.json").is_file()


def test_answers_are_stored_as_decisions_and_the_last_one_starts_the_compile_once(flow):
    b, wf, compiled = flow
    b._flow_understand(wf.id, "rid")
    answers = {"value": {"choice": "varies"}, "confirm_before": {"choice": "confirm"}}
    for _ in range(10):
        pending = _open_cards(wf)
        if not pending:
            break
        _answer(b, wf, pending[0], **answers[pending[0]["question_kind"]])
    session = settings.data_dir / "sessions" / "sess1"
    stored = {d["qid"]: d for d in decisions.load(session)}
    assert [v["name"] for v in stored["value:url:1"]["values"]] == ["customer"]
    assert stored["value:url:1"]["values"][0]["event_ids"] == [1]
    assert stored["confirm_before:r2"]["choice"] == "confirm" and stored["confirm_before:r2"]["event_ids"] == [2]
    assert compiled == [wf.id]                                               # exactly once
    assert clarify_store.load(wf.id)["stage"] == "building"
    assert runtime_state.get_flags(wf.id).get("compiling") is True


def test_nothing_to_ask_builds_at_once_and_a_free_description_comes_from_the_summary(flow, monkeypatch):
    b, wf, compiled = flow
    monkeypatch.setattr(tu, "_default_call", lambda task, payload, ms: {**RAW, "values": [], "questions": []})
    b._flow_understand(wf.id, "rid")
    assert not _open_cards(wf) and compiled == [wf.id]
    assert "building it now" in " ".join(_texts(wf.id, "assistant_message"))


def test_video_service_down_blocks_everything_past_the_merge_and_retry_resumes(flow, monkeypatch):
    b, wf, compiled = flow

    def down(d, c, blur_all=False):
        raise VideoUnavailable("cloud answered 503")

    monkeypatch.setattr(track_a, "run_track_a", down)
    b._flow_understand(wf.id, "rid")
    card = _cards(wf.id, "watching_video")[0]
    assert card["state"] == "failed" and card["actions"] == ["retry_video", "re_record"]
    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert (lc["stage"], lc["substate"]) == ("understand", "video_failed")
    assert lc["allowed_actions"] == ["retry_video", "re_record"]       # no compile on offer
    assert clarify_store.load(wf.id) is None and compiled == []

    restarted: list[str] = []
    monkeypatch.setattr(b, "_flow_start_understand", lambda wid: restarted.append(wid))
    assert _answer(b, wf, card, action="retry_video")["ok"] and restarted == [wf.id]

    monkeypatch.setattr(track_a, "run_track_a", lambda d, c, blur_all=False: {"rows": [], "splice_map": []})
    b._flow_understand(wf.id, "rid2")                                     # what the retry thread runs
    assert lifecycle_for_workflow(get_workflow(wf.id))["substate"] == "open"
    assert runtime_state.get_flags(wf.id) == {}


def test_a_failed_model_call_still_asks_about_the_value_it_could_not_judge(flow, monkeypatch):
    b, wf, _ = flow

    def boom(task, payload, ms):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(tu, "_default_call", boom)
    stored = get_workflow(wf.id)
    stored.task_description = "Create and send an invoice"
    save_workflow(stored)
    b._flow_understand(wf.id, "rid")
    assert [c["question_kind"] for c in _open_cards(wf)] == ["value"]
    assert _open_cards(wf)[0]["choices"] and not any(c.get("selected") for c in _open_cards(wf)[0]["choices"])
    assert clarify_store.load(wf.id)["understanding"]["source"] == "fallback"
    # plain questions after a failed read: "watch the video again" is on offer, and typing it works
    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert lc["substate"] == "open" and "retry_video" in lc["allowed_actions"]
    from conxa_compile.conversation.actions import ACTIONS
    assert "watch the video again" in ACTIONS["retry_video"].phrases
    # a re-watch closes the old round of question cards
    old_ids = [c["card_id"] for c in _open_cards(wf)]
    monkeypatch.setattr(tu, "_default_call", lambda t, p, ms: None)
    b._flow_understand(wf.id, "rid2")
    assert not set(old_ids) & {c["card_id"] for c in _open_cards(wf)}


def test_a_watch_cut_short_by_a_restart_is_marked_failed_with_retry(flow, monkeypatch):
    import time

    from conxa_compile.conversation import harness

    b, wf, _ = flow
    harness.post_card(f"wf:{wf.id}", "watching_video", "watching", elapsed_s=0, reason="", actions=[])
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 120)      # long enough to be a dead attempt, not a new one
    b._reconcile_interrupted(wf.id)
    assert _cards(wf.id, "watching_video")[0]["state"] == "failed"
    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert lc["substate"] == "video_failed" and lc["allowed_actions"] == ["retry_video", "re_record"]


# --- replay after the compile -------------------------------------------------------------

def _write_session(decs):
    session = settings.data_dir / "sessions" / "sess1"
    session.mkdir(parents=True, exist_ok=True)
    (session / "understanding.json").write_text(json.dumps({"goal": "Create invoice", "source": "model", "gaps": []}))
    (session / "decisions.json").write_text(json.dumps(decs))


def _compile(b, wf, monkeypatch):
    from tests.test_compile_flow import SOURCES, _fake_compile

    sources = {**SOURCES, "send_invoice": {"action": {"value": None}, "extras": {"sequence": 7}}}
    monkeypatch.setattr("conxa_compile.editor.retarget.find_source_event", lambda step, doc: sources.get(step.get("intent")))
    _fake_compile(b, wf)
    b.cmd_flow_compile({"workflow_id": wf.id}, "rid")
    return clarify_store.load(wf.id)


def test_decisions_are_replayed_onto_the_compiled_steps_and_not_asked_again(env, monkeypatch):
    b, wf, _ = env
    _write_session([
        {"qid": "confirm_before:r7", "kind": "confirm_before", "event_ids": [7], "skipped": False, "choice": "confirm"},
        {"qid": "value:url:1", "kind": "value", "event_ids": [1], "skipped": False, "choice": "varies",
         "values": [{"ref": "url:1", "name": "customer", "literal": "no-such-text", "kind": "url", "event_ids": [1]}]},
    ])
    data = _compile(b, wf, monkeypatch)
    by_kind = {q["kind"]: q for q in data["questions"]}
    assert by_kind["confirm"]["status"] == "answered" and by_kind["confirm"]["receipt"] == "Someone approves it first"
    assert data["added_steps"] == 1                                               # the confirm step the answer inserted
    # a value that could not be applied is said once in the chat, never asked a second time
    assert "chosen_values" not in by_kind and "values" not in by_kind
    assert any("couldn’t make “no-such-text” change each run" in t for t in _texts(wf.id, "assistant_message"))
    assert {q["kind"] for q in data["questions"]} <= {"fork", "known_popup", "popup", "confirm"}


def test_a_recording_without_decisions_compiles_as_before(env, monkeypatch):
    b, wf, _ = env
    data = _compile(b, wf, monkeypatch)
    assert data["added_steps"] == 0 and {q["kind"] for q in data["questions"]} >= {"confirm"}
    assert all(q["status"] != "answered" for q in data["questions"])


def test_a_skipped_or_unmatched_decision_changes_nothing(env, monkeypatch):
    b, wf, _ = env
    _write_session([
        {"qid": "confirm_before:r9", "kind": "confirm_before", "event_ids": [7], "skipped": True},
        {"qid": "upload:r1", "kind": "upload", "event_ids": [99], "skipped": False, "choice": "fixed_path"},
    ])
    data = _compile(b, wf, monkeypatch)
    assert data["added_steps"] == 0 and next(q for q in data["questions"] if q["kind"] == "confirm")["status"] == "open"


# --- replay.plan on its own --------------------------------------------------------------

def _doc_with_click():
    step = {"intent": "open_file", "action": {"action": "click"}, "identity_bundle": {
        "fingerprint": {"inner_text": "Report.gitignore", "aria_label": "Report.gitignore, (File)"},
        "signals": [{"engine": "role", "selector": "internal:role=link[name=\"Report.gitignore\"i]"}]}}
    return {"skills": [{"steps": [step]}], "inputs": []}


def test_a_clicked_value_replays_with_the_label_the_compiled_step_is_known_by():
    from conxa_compile.compiler.step_key import step_keys
    from conxa_compile.conversation import replay

    doc = _doc_with_click()
    key = step_keys(doc["skills"][0]["steps"])[0]
    stored = [{"qid": "value:click:1", "kind": "value", "event_ids": [1], "skipped": False, "choice": "varies",
               "values": [{"ref": "click:1", "name": "target_file", "kind": "click", "event_ids": [1],
                           "literal": "Report.gitignore, (File)"}]}]    # what the timeline called it
    groups = replay.plan(stored, doc, [], lambda k: 1 if k == key else None)["groups"]
    assert groups[0]["ops"][0]["literal"] == "Report.gitignore"


def test_the_same_text_chosen_as_a_click_and_in_an_address_becomes_one_input():
    from conxa_compile.compiler.step_key import step_keys
    from conxa_compile.conversation import replay

    doc = _doc_with_click()
    key = step_keys(doc["skills"][0]["steps"])[0]
    stored = [
        {"qid": "value:click:1", "kind": "value", "event_ids": [1], "skipped": False, "choice": "varies",
         "values": [{"ref": "click:1", "name": "target_file", "kind": "click", "event_ids": [1],
                     "literal": "Report.gitignore, (File)"}]},
        {"qid": "value:url:2", "kind": "value", "event_ids": [2], "skipped": False, "choice": "varies",
         "values": [{"ref": "url:2", "name": "target_file_url", "kind": "url", "event_ids": [2],
                     "literal": "Report.gitignore"}]},
    ]
    groups = replay.plan(stored, doc, [], lambda k: 1 if k == key else None)["groups"]
    assert [g["ops"][0]["name"] for g in groups] == ["target_file", "target_file"]


def test_a_step_finds_its_own_event_when_a_snapshot_is_shared(monkeypatch):
    from conxa_compile.editor.retarget import find_source_event

    events = [{"extras": {"sequence": 1}, "action": {"action": "scroll"}, "snapshot": {"ref": "snap"}},
              {"extras": {"sequence": 2}, "action": {"action": "click"}, "snapshot": {"ref": "snap"}}]
    monkeypatch.setattr("conxa_core.storage.session_events.read_session_events", lambda _sid: events)
    doc = {"meta": {"source_session_id": "s"}}
    assert find_source_event({"snapshot_ref": "snap", "source_event": 2}, doc) is events[1]
    assert find_source_event({"source_event": 1}, doc) is events[0]       # no snapshot recorded
    assert find_source_event({"snapshot_ref": "snap"}, doc) is events[0]  # older steps: by ref


def test_a_fork_answer_is_never_applied_to_a_popup_it_does_not_trace_back_to():
    from conxa_compile.conversation import replay

    forks = [{"id": "fork:s1", "kind": "fork", "status": "open", "title": "Upload options", "step_keys": ["s1"], "choices": []}]
    stored = [{"qid": "fork:r2", "kind": "fork", "event_ids": [1], "skipped": False, "choice": "ask_human"}]
    assert replay.plan(stored, {"skills": [{"steps": []}]}, forks, lambda k: 10)["groups"] == []
