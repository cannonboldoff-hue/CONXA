"""Understanding-first flow: watch the recording, ask one round, store decisions, then compile."""

from __future__ import annotations

import json

import pytest

from conxa_compile.conversation import clarify_store, decisions, runtime_state
from conxa_compile.conversation import timeline_understanding as tu
from conxa_compile.conversation.lifecycle import lifecycle_for_workflow
from conxa_compile.video import track_a, track_b
from conxa_core.config import settings
from conxa_core.storage.workflow_store import get_workflow
from services.video_analysis_client import VideoUnavailable
from tests.test_compile_flow import _cards, env  # noqa: F401 -- env is a fixture


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
    assert [c["question_kind"] for c in _open_cards(wf)] == ["confirm_summary"]       # one at a time, summary first
    assert runtime_state.get_flags(wf.id) == {} and compiled == []
    session = settings.data_dir / "sessions" / "sess1"
    assert (session / "understanding.json").is_file() and (session / "merged_timeline.json").is_file()


def test_answers_are_stored_as_decisions_and_the_last_one_starts_the_compile_once(flow):
    b, wf, compiled = flow
    b._flow_understand(wf.id, "rid")
    answers = {"confirm_summary": {"choice": "yes"}, "chosen_values": {"selected": ["url:1"]},
               "confirm_before": {"choice": "confirm"}}
    for _ in range(10):
        pending = _open_cards(wf)
        if not pending:
            break
        _answer(b, wf, pending[0], **answers[pending[0]["question_kind"]])
    session = settings.data_dir / "sessions" / "sess1"
    stored = {d["qid"]: d for d in decisions.load(session)}
    assert stored["confirm_summary"]["description"] == "Types the PO number and sends the invoice."
    assert [v["name"] for v in stored["chosen_values"]["values"]] == ["customer"]
    assert stored["chosen_values"]["values"][0]["event_ids"] == [1]
    assert stored["confirm_before:r2"]["choice"] == "confirm" and stored["confirm_before:r2"]["event_ids"] == [2]
    assert compiled == [wf.id]                                               # exactly once
    assert clarify_store.load(wf.id)["stage"] == "building"
    assert runtime_state.get_flags(wf.id).get("compiling") is True


def test_saying_the_summary_is_wrong_stops_before_building(flow):
    b, wf, compiled = flow
    b._flow_understand(wf.id, "rid")
    _answer(b, wf, _open_cards(wf)[0], choice="no")
    assert compiled == [] and clarify_store.load(wf.id)["stage"] == "rejected"
    lc = lifecycle_for_workflow(get_workflow(wf.id))
    assert (lc["stage"], lc["substate"]) == ("compile", "ready") and "re_record" in lc["allowed_actions"]
    assert not _open_cards(wf)


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


def test_a_failed_model_call_still_asks_the_summary_question(flow, monkeypatch):
    b, wf, _ = flow

    def boom(task, payload, ms):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(tu, "_default_call", boom)
    b._flow_understand(wf.id, "rid")
    assert [c["question_kind"] for c in _open_cards(wf)] == ["confirm_summary"]
    assert clarify_store.load(wf.id)["understanding"]["source"] == "fallback"


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
        {"qid": "confirm_summary", "kind": "confirm_summary", "event_ids": [], "skipped": False, "choice": "yes",
         "description": "Create and send an invoice"},
        {"qid": "confirm_before:r7", "kind": "confirm_before", "event_ids": [7], "skipped": False, "choice": "confirm"},
        {"qid": "chosen_values", "kind": "chosen_values", "event_ids": [], "skipped": False,
         "values": [{"ref": "url:1", "name": "customer", "literal": "no-such-text", "kind": "url", "event_ids": [1]}]},
    ])
    data = _compile(b, wf, monkeypatch)
    by_kind = {q["kind"]: q for q in data["questions"]}
    assert by_kind["confirm"]["status"] == "answered" and by_kind["confirm"]["receipt"] == "Someone approves it first"
    assert data["added_steps"] == 1                                               # the confirm step the answer inserted
    # the value that could not be applied is asked again instead of being lost
    assert by_kind["chosen_values"]["status"] == "open" and "couldn’t find" in by_kind["chosen_values"]["body"]
    from conxa_core.storage.workflow_store import get_workflow

    assert get_workflow(wf.id).task_description == "Create and send an invoice"


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
