"""BUILD-55 B1: an optional extra model call can never cost the person a reply they already read,
and a failed answer-mapping call is "unclear" rather than a crashed turn."""

from __future__ import annotations

import pytest

from conxa_core.config import settings
from conxa_compile.conversation import harness, runtime_state
from conxa_compile.conversation import thread_store as ts


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    runtime_state.clear_all()


def _fake_stream(monkeypatch, chunks=("Hello ", "there.")):
    def fake(task, payload, timeout_ms, *, on_delta, error_detail=None):
        for c in chunks:
            on_delta(c)
        return {"text": "".join(chunks)}

    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", fake)


def test_a_failing_draft_check_keeps_the_reply(monkeypatch):
    _fake_stream(monkeypatch)

    def boom(task, *a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", boom)
    out = harness.run_turn(thread_id="chat:g1", message="hi", sink=None, group_names=[])
    assert out["status"] == "done" and out["reply"] == "Hello there."
    assert [i["kind"] for i in ts.load("chat:g1")["items"]] == ["user_message", "assistant_message"]


def test_a_failing_mapping_call_is_unclear_and_the_card_stays_open(monkeypatch):
    ts.append_card("wf:g2", "question", "open", title="Q", choices=[{"id": "stop", "label": "Stop"}])
    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", lambda *a, **k: pytest.fail("no chat reply for an answer"))

    def boom(task, *a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", boom)
    applied: list[str] = []
    out = harness.run_turn(thread_id="wf:g2", message="hmm?", sink=None,
                           card_executor=lambda c, a, b: applied.append(b["choice"]) or {})
    assert applied == [] and "wasn’t sure" in out["reply"]


def test_out_of_credits_still_reaches_the_command_handler(monkeypatch):
    class QuotaExceeded(RuntimeError):
        pass

    ts.append_card("wf:g3", "question", "open", title="Q", choices=[{"id": "stop", "label": "Stop"}])

    def out_of_credits(task, *a, **k):
        raise QuotaExceeded("no credits")

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", out_of_credits)
    with pytest.raises(QuotaExceeded):
        harness.run_turn(thread_id="wf:g3", message="hmm?", sink=None, card_executor=lambda c, a, b: {})
