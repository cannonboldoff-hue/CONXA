"""BUILD-37: thread store, context pack, lifecycle, harness and the scoped LLM router.

No network and no model: ``stream_llm`` / ``call_llm`` are replaced with fakes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from conxa_core.config import settings
from conxa_compile.conversation import clarify_store, harness, runtime_state
from conxa_compile.conversation import thread_store as ts
from conxa_compile.conversation.context_pack import SUMMARIZE_AFTER_DROPPED, build_context_pack, estimate_tokens, shrink_digest
from conxa_compile.conversation.lifecycle import derive_lifecycle


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    runtime_state.clear_all()
    yield


# ── thread store ────────────────────────────────────────────────────────────────────────────


def test_thread_ids_cannot_escape_the_data_dir():
    for bad in ("wf:../x", "wf:", "chat:a/b", "other:1", "", "wf:.."):
        with pytest.raises(ts.ThreadIdError):
            ts.parse_thread_id(bad)


def test_append_and_reload_round_trip():
    ts.append("wf:w1", "user_message", text="hi")
    ts.append("wf:w1", "assistant_message", text="hello")
    page = ts.load("wf:w1")
    assert [i["text"] for i in page["items"]] == ["hi", "hello"]
    assert page["has_more"] is False


def test_card_revisions_fold_to_the_latest_at_the_first_position():
    ts.append("wf:w1", "event", text="before")
    card = ts.append_card("wf:w1", "question", "open", title="Q", choices=[{"id": "a"}])
    ts.append("wf:w1", "event", text="after")
    ts.revise_card("wf:w1", card["card_id"], state="answered", receipt="Kept")
    items = ts.load("wf:w1")["items"]
    assert [i.get("text") or i["card_type"] for i in items] == ["before", "question", "after"]
    folded = items[1]
    assert folded["state"] == "answered" and folded["receipt"] == "Kept"
    assert folded["revision"] == 2 and folded["title"] == "Q"  # carries everything the first had
    # History is never rewritten: both revisions are still on disk.
    assert sum(1 for r in ts.read_raw("wf:w1") if r.get("card_id") == card["card_id"]) == 2


def test_paging_is_newest_first_and_chronological_within_a_page():
    for n in range(10):
        ts.append("chat:c1", "user_message", text=str(n))
    newest = ts.load("chat:c1", limit=4)
    assert [i["text"] for i in newest["items"]] == ["6", "7", "8", "9"]
    assert newest["has_more"] and newest["cursor"] == 6
    older = ts.load("chat:c1", before=newest["cursor"], limit=4)
    assert [i["text"] for i in older["items"]] == ["2", "3", "4", "5"]


def test_unreadable_lines_are_skipped_and_missing_threads_are_empty():
    assert ts.load("wf:none")["items"] == []
    ts.append("wf:w2", "event", text="ok")
    with ts.thread_path("wf:w2").open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    assert [i["text"] for i in ts.load("wf:w2")["items"]] == ["ok"]


def test_chat_index_create_rename_delete():
    chat = ts.create_chat("Planning")
    ts.append(f"chat:{chat['id']}", "user_message", text="x")
    assert [c["title"] for c in ts.list_chats()] == ["Planning"]
    assert ts.rename_chat(chat["id"], "Refunds")["title"] == "Refunds"
    assert ts.delete_chat(chat["id"]) is True
    assert ts.list_chats() == []
    assert not ts.thread_path(f"chat:{chat['id']}").exists()


def test_copilot_history_is_imported_once(monkeypatch):
    monkeypatch.setattr(
        "conxa_compile.editor.copilot_sessions.load_last_session",
        lambda skill_id: [{"role": "user", "text": "why did it fail?"}, {"role": "assistant", "text": "the list was slow"}],
    )
    assert ts.import_copilot_session("w1", "skill_1") == 2
    assert ts.import_copilot_session("w1", "skill_1") == 0
    texts = [i.get("text") for i in ts.load("wf:w1")["items"] if i["kind"] != "import_marker"]
    assert texts == ["why did it fail?", "the list was slow"]


# ── context pack ────────────────────────────────────────────────────────────────────────────


def _turns(n: int, size: int = 400) -> list[dict[str, Any]]:
    return [{"id": f"t{i}", "role": "user" if i % 2 == 0 else "assistant", "text": f"turn {i} " + "x" * size} for i in range(n)]


def test_pinned_facts_and_the_message_survive_a_tiny_budget():
    pack = build_context_pack(
        message="what now?", budget_tokens=50, pinned_facts=["Customer: ask each run"], turns=_turns(5),
        digest={"steps": [{"description": "Open", "edit_history": "x" * 2000}]},
    )
    assert "Customer: ask each run" in pack.text
    assert "what now?" in pack.text
    assert "turn 4" not in pack.text  # conversation is what gives way first


def test_oldest_turns_are_trimmed_first_and_reported_as_dropped():
    pack = build_context_pack(message="m", budget_tokens=700, turns=_turns(12))
    assert "turn 11" in pack.text and "turn 0 " not in pack.text
    assert pack.dropped_turns and pack.dropped_turns[0]["id"] == "t0"
    # kept turns are still oldest-first
    kept = [int(l.split()[2]) for l in pack.text.splitlines() if l.startswith(("user: turn", "assistant: turn"))]
    assert kept == sorted(kept)


def test_summary_is_used_and_enough_dropped_turns_ask_for_a_new_one():
    pack = build_context_pack(message="m", budget_tokens=400, summary="We chose to ask each run.", turns=_turns(SUMMARIZE_AFTER_DROPPED + 6))
    assert "We chose to ask each run." in pack.text
    assert pack.needs_summary


def test_a_big_digest_is_shrunk_before_it_is_dropped():
    digest = {"steps": [{"description": f"step {i}", "edit_history": ["e"] * 50, "compile_report": {"k": "v" * 80}} for i in range(60)]}
    small, shrunk = shrink_digest(digest, 400)
    assert shrunk and estimate_tokens(str(small)) < estimate_tokens(str(digest))
    assert all("edit_history" not in s for s in small["steps"])
    pack = build_context_pack(message="m", budget_tokens=1500, digest=digest)
    assert pack.digest_shrunk and pack.tokens <= 1800


# ── lifecycle ───────────────────────────────────────────────────────────────────────────────

_BASE = dict(target_url="https://a.example.com", session_id=None, recording_status=None, skill_id=None,
             last_test_status="never", signed_off=False, compile_steps_with_warnings=0, compile_status=None)


@pytest.mark.parametrize(
    "wf, kw, stage, substate, glyph",
    [
        (dict(target_url=""), {}, "describe", "needs_details", "warn"),
        ({}, dict(auth_missing=True), "configure", "sign_in_needed", "warn"),
        ({}, {}, "record", "not_recorded", "none"),
        ({}, dict(runtime={"recording": True}), "record", "recording", "run"),
        ({}, dict(runtime={"recording": True, "recording_paused": True}), "record", "paused", "run"),
        (dict(session_id="s", recording_status="recorded"), {}, "compile", "ready", "warn"),
        (dict(session_id="s", recording_status="recorded"), dict(runtime={"queued": 2}), "compile", "queued", "run"),
        (dict(session_id="s", recording_status="recorded"), dict(runtime={"out_of_credits": True}), "compile", "out_of_credits", "warn"),
        (dict(session_id="s", recording_status="error"), {}, "compile", "failed", "fail"),
        (dict(session_id="s", recording_status="compiled", skill_id="k"), dict(clarify={"open": 2}), "clarify", "open", "warn"),
        (dict(session_id="s", recording_status="compiled", skill_id="k", compile_steps_with_warnings=2), {}, "edit", "recommended", "warn"),
        (dict(session_id="s", recording_status="compiled", skill_id="k"), {}, "test", "ready", "warn"),
        (dict(session_id="s", recording_status="compiled", skill_id="k"), dict(runtime={"testing": True, "autofix_attempt": 2}), "test", "autofix", "run"),
        (dict(session_id="s", recording_status="compiled", skill_id="k", last_test_status="failed"), {}, "test", "failed", "fail"),
        (dict(session_id="s", recording_status="compiled", skill_id="k", last_test_status="passed"), {}, "approve", "ready", "warn"),
        (dict(session_id="s", recording_status="compiled", skill_id="k", last_test_status="passed", signed_off=True), {}, "approve", "approved", "ok"),
    ],
)
def test_lifecycle_is_derived_from_existing_data(wf, kw, stage, substate, glyph):
    lc = derive_lifecycle({**_BASE, **wf}, **kw)
    assert (lc["stage"], lc["substate"], lc["glyph"]) == (stage, substate, glyph)
    assert isinstance(lc["allowed_actions"], list)
    if glyph != "run":
        assert lc["allowed_actions"]  # anything waiting on a person offers a way forward


def test_edits_clear_approval_so_the_lifecycle_falls_back_to_testing():
    wf = {**_BASE, "session_id": "s", "recording_status": "compiled", "skill_id": "k", "last_test_status": "never", "signed_off": False}
    assert derive_lifecycle(wf)["stage"] in ("test", "edit")


# ── harness ─────────────────────────────────────────────────────────────────────────────────


def _fake_stream(monkeypatch, chunks=("Hello ", "there."), hold: threading.Event | None = None, calls: list | None = None):
    def fake(task, payload, timeout_ms, *, on_delta, error_detail=None):
        if calls is not None:
            calls.append((task, payload))
        if hold is not None:
            hold.wait(5)
        for c in chunks:
            on_delta(c)
        return {"text": "".join(chunks)}

    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", fake)


def _no_model(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", boom)
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", boom)


def test_a_turn_streams_and_appends_user_then_assistant(monkeypatch):
    _fake_stream(monkeypatch)
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda *a, **k: {"draft_workflow": None})
    events: list[dict] = []
    out = harness.run_turn(thread_id="chat:c1", message="hi", sink=events.append, group_names=["Finance"])
    assert out["status"] == "done" and out["reply"] == "Hello there."
    assert [i["kind"] for i in ts.load("chat:c1")["items"]] == ["user_message", "assistant_message"]
    assert "".join(e["text"] for e in events if e.get("phase") == "thread_delta") == "Hello there."


def test_cancel_appends_nothing_half_finished(monkeypatch):
    hold = threading.Event()
    seen_cancel: list[bool] = []

    def fake(task, payload, timeout_ms, *, on_delta, error_detail=None):
        hold.wait(5)
        on_delta("partial")  # the cancel flag is checked here and raises TurnCancelled
        seen_cancel.append(False)
        return {}

    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", fake)
    result: dict = {}
    t = threading.Thread(target=lambda: result.update(harness.run_turn(thread_id="chat:c2", message="hi", sink=None, turn_id="T1", group_names=[])))
    t.start()
    time.sleep(0.1)
    assert harness.cancel_turn("T1") is True
    hold.set()
    t.join(5)
    assert result["status"] == "cancelled" and not seen_cancel
    assert [i["kind"] for i in ts.load("chat:c2")["items"]] == ["user_message"]  # no assistant message


def test_turns_on_one_thread_are_serialized_not_interleaved(monkeypatch):
    hold = threading.Event()
    order: list[str] = []

    def fake(task, payload, timeout_ms, *, on_delta, error_detail=None):
        text = payload["user_text"].rsplit("Person's message: ", 1)[1]
        order.append(f"start {text}")
        if text == "first":
            hold.wait(5)
        order.append(f"end {text}")
        on_delta(f"re: {text}")
        return {}

    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", fake)
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda *a, **k: {"draft_workflow": None})
    queued: list[dict] = []
    a = threading.Thread(target=lambda: harness.run_turn(thread_id="chat:c3", message="first", sink=None, group_names=[]))
    b = threading.Thread(target=lambda: harness.run_turn(thread_id="chat:c3", message="second", sink=queued.append, group_names=[]))
    a.start(); time.sleep(0.15); b.start(); time.sleep(0.15)
    assert order == ["start first"]  # the second is queued, not started
    hold.set(); a.join(5); b.join(5)
    assert order == ["start first", "end first", "start second", "end second"]
    assert any(e.get("status") == "queued" for e in queued)


def test_a_card_click_never_calls_the_model(monkeypatch):
    _no_model(monkeypatch)
    card = ts.append_card("wf:w1", "question", "open", title="Q", choices=[{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}])
    applied: list[tuple] = []

    def executor(c, action, body):
        applied.append((c["card_id"], action, body["choice"]))
        return {"ok": True}

    out = harness.run_turn(thread_id="wf:w1", message="", sink=None, card_id=card["card_id"], choice="yes", card_executor=executor)
    assert out["status"] == "done" and applied == [(card["card_id"], "choose", "yes")]


def test_free_text_that_matches_a_label_is_applied_without_the_model(monkeypatch):
    _no_model(monkeypatch)
    ts.append_card("wf:w1", "question", "open", title="Q", choices=[{"id": "stop", "label": "Stop and report it"}, {"id": "ask", "label": "Ask"}])
    applied: list[str] = []
    harness.run_turn(thread_id="wf:w1", message="stop and report it", sink=None,
                     card_executor=lambda c, a, b: applied.append(b["choice"]) or {})
    assert applied == ["stop"]


def test_free_text_goes_through_one_mapping_call_and_unclear_applies_nothing(monkeypatch):
    ts.append_card("wf:w1", "question", "open", title="Q", choices=[{"id": "stop", "label": "Stop"}, {"id": "ask", "label": "Ask"}])
    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", lambda *a, **k: pytest.fail("no chat reply for an answer"))
    applied: list[str] = []
    ex = lambda c, a, b: applied.append(b["choice"]) or {}  # noqa: E731

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda task, *a, **k: {"choice": "ask"} if task == "chat_map_answer" else None)
    harness.run_turn(thread_id="wf:w1", message="let them pick someone else", sink=None, card_executor=ex)
    assert applied == ["ask"]

    ts.append_card("wf:w2", "question", "open", title="Q", choices=[{"id": "stop", "label": "Stop"}])
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda task, *a, **k: {"choice": "invented"})
    out = harness.run_turn(thread_id="wf:w2", message="hmm?", sink=None, card_executor=ex)
    assert applied == ["ask"]  # an id that isn't on the card is "unclear" — nothing applied
    assert "wasn’t sure" in out["reply"]


def test_proposals_only_become_cards_after_the_gates(monkeypatch):
    _fake_stream(monkeypatch)
    monkeypatch.setattr("conxa_compile.llm.copilot.copilot_turn", lambda **k: {"reply": "", "proposals": [{"id": "p1"}, {"id": "bad"}]})
    gated: list[list] = []

    def gate(raw):
        gated.append(raw)
        return [{"id": "p1", "step_key": "s1", "field": "value", "why": "slow list", "command": "patch_step", "patch": {"value": "x"}}]

    harness.run_turn(thread_id="wf:w9", message="please fix the wait", sink=None, skill_id="skill_1",
                     gate_proposals=gate, load_digest=lambda: {"steps": []}, lifecycle={"stage": "test"})
    cards = [i for i in ts.load("wf:w9")["items"] if i["kind"] == "card"]
    assert len(gated[0]) == 2 and len(cards) == 1          # the ungated one never reaches the page
    assert cards[0]["card_type"] == "pending_change" and cards[0]["state"] == "open" and cards[0]["actions"] == ["apply", "dismiss"]


def test_a_plain_question_does_not_pay_for_the_proposal_call(monkeypatch):
    _fake_stream(monkeypatch)
    monkeypatch.setattr("conxa_compile.llm.copilot.copilot_turn", lambda **k: pytest.fail("no proposal call for a question"))
    harness.run_turn(thread_id="wf:w10", message="what does this do?", sink=None, skill_id="skill_1",
                     gate_proposals=lambda raw: raw, load_digest=lambda: {"steps": []})


def test_only_a_confident_draft_with_a_valid_url_and_known_group_survives(monkeypatch):
    _fake_stream(monkeypatch)
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda *a, **k: {"draft_workflow": {
        "name": "Issue a partial refund", "group": "Nonexistent", "starting_page": "not a url", "task": "refund part"}})
    harness.run_turn(thread_id="chat:c9", message="let's do refunds", sink=None, group_names=["Finance Ops"])
    draft = next(i for i in ts.load("chat:c9")["items"] if i.get("card_type") == "draft_workflow")["draft"]
    assert draft["group"] == "" and draft["starting_page"] == "" and draft["name"] == "Issue a partial refund"


def test_old_turns_are_folded_into_a_stored_summary(monkeypatch):
    _fake_stream(monkeypatch)
    for i in range(30):
        ts.append("chat:c5", "user_message" if i % 2 == 0 else "assistant_message", text=f"m{i} " + "y" * 1200)

    def call(task, payload, *a, **k):
        return {"summary": "Earlier we talked about refunds."} if task == "thread_summarize" else {"draft_workflow": None}

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", call)
    harness.run_turn(thread_id="chat:c5", message="and now?", sink=None, group_names=[])
    summary = ts.latest_summary(ts.load("chat:c5", limit=100)["items"])
    assert summary and "refunds" in summary["text"] and summary["covers_through"]
    # the next turn's context uses the summary and skips what it covers
    calls: list = []
    _fake_stream(monkeypatch, calls=calls)
    harness.run_turn(thread_id="chat:c5", message="ok", sink=None, group_names=[])
    text = calls[0][1]["user_text"]
    assert "Earlier we talked about refunds." in text and "m0 " not in text


# ── clarify store & scoped router ───────────────────────────────────────────────────────────


def test_clarify_summary_counts_by_status():
    assert clarify_store.summary("w1") is None
    clarify_store.save("w1", {"questions": [{"id": "a", "status": "open"}, {"id": "b", "status": "answered"}, {"id": "c", "status": "skipped"}]})
    assert clarify_store.summary("w1") == {"open": 1, "answered": 1, "skipped": 1, "total": 3}
    clarify_store.update_question("w1", "a", status="answered")
    assert clarify_store.next_open("w1") is None


def test_scoped_routers_do_not_leak_between_threads():
    from conxa_core import llm as core_llm

    class R:
        def __init__(self, name): self.name = name
        def route_text(self, *a, **k): return None
        def route_vision(self, *a, **k): return None

    previous = core_llm._router
    core_llm.set_router(R("global"))
    seen: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def worker(name):
        core_llm.set_scoped_router(R(name))
        barrier.wait(5)  # both have installed theirs before either reads
        seen[name] = core_llm.get_router().name

    ts_ = [threading.Thread(target=worker, args=(n,)) for n in ("compile", "chat")]
    [t.start() for t in ts_]; [t.join(5) for t in ts_]
    assert seen == {"compile": "compile", "chat": "chat"}
    try:
        assert core_llm.get_router().name == "global"  # a thread that installed nothing falls back to the global
    finally:
        core_llm.set_router(previous)  # other tests may rely on whatever router was installed


def test_free_text_never_answers_a_tick_list_question(monkeypatch):
    """Mapping "yes" onto one choice of a multi-select would silently untick everything else."""
    _no_model(monkeypatch)
    ts.append_card("wf:w5", "clarify_question", "open", title="Which values change?", multi=True,
                   choices=[{"id": "po", "label": "PO number", "selected": True}, {"id": "desc", "label": "Description", "selected": True}])
    applied: list = []
    out = harness.run_turn(thread_id="wf:w5", message="po number", sink=None, card_executor=lambda c, a, b: applied.append(b) or {})
    assert applied == [] and "tick the ones you want" in out["reply"]
