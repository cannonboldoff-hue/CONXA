"""WF-19 F2: a model reply that is only a tool call (no words before it) is a normal reply.

The router used to read "no text" as "empty stream", so it retried other models: one chat turn was three
billed upstream requests, and because each attempt had already pushed its tool-call chunks to the client,
the client joined them (`{}{}{}`) into arguments that are not valid JSON. These tests pin: one request for a
tool-only turn, no second attempt once anything reached the caller, a genuinely empty stream still failing
over, and the proxy metering the tool call's text (it was ~0 output tokens).

Same fakes as test_llm_reasoning_only_stream.py."""

from __future__ import annotations

import json
from threading import Lock as _Lock

from fastapi.testclient import TestClient

from app.llm.router import LLMRouter, PoolEntry


def _entry(**overrides) -> PoolEntry:
    base = dict(
        provider="fake",
        endpoint="https://fake.example/v1",
        api_key="key",
        text_model="fake-text",
        vision_model="fake-vision",
        pool="free",
    )
    base.update(overrides)
    return PoolEntry(**base)


def _fresh_router(entries: list[PoolEntry], *, max_retries: int = 3) -> LLMRouter:
    router = LLMRouter.__new__(LLMRouter)
    router.pool = entries
    router.cooldown_secs = 60
    router.max_retries = max_retries
    router.wait_ceiling_secs = 8.0
    router.total_budget_secs = 75.0
    router._request_counter = 0
    router._last_lru_index = -1
    router._lru_lock = _Lock()
    return router


class _FakeStreamResponse:
    def __init__(self, lines: list[bytes], *, then_raise: Exception | None = None):
        self._lines = lines
        self._then_raise = then_raise
        self.status = 200

    def __iter__(self):
        yield from self._lines
        if self._then_raise is not None:
            raise self._then_raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _sse_lines(*raw: str) -> list[bytes]:
    return [line.encode("utf-8") for line in raw]


# The standard OpenAI streaming shape for a turn that is only a tool call: no content delta at all.
_TOOL_ONLY_BODY = _sse_lines(
    'data: {"choices":[{"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"list_skills","arguments":""}}]}}]}',
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
    "data: [DONE]",
)

_GENUINELY_EMPTY_BODY = _sse_lines(
    'data: {"choices":[{"delta":{"role":"assistant"}}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
    "data: [DONE]",
)


def test_tool_only_stream_is_one_upstream_request_and_each_chunk_reaches_the_caller_once(monkeypatch):
    router = _fresh_router([_entry(), _entry(provider="fake2")], max_retries=3)
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append("called")
        return _FakeStreamResponse(_TOOL_ONLY_BODY)

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    items: list[dict] = []
    error_detail: list[str] = []
    result = router.route_text("execute_chat", {}, 5_000, error_detail=error_detail, on_delta=items.append)

    assert result is not None, "a tool call with no prose is a real reply, not an empty stream"
    assert len(calls) == 1, "it must not be retried (and billed) against another model"
    assert [i["type"] for i in items] == ["tool_call", "tool_call"]
    assert "".join(i.get("arguments_delta") or "" for i in items) == "{}", "the arguments are not doubled"
    assert not any("no content" in line for line in error_detail)


def test_a_stream_that_failed_after_chunks_reached_the_caller_is_not_retried(monkeypatch):
    router = _fresh_router([_entry(), _entry(provider="fake2")], max_retries=3)
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append("called")
        return _FakeStreamResponse(
            _sse_lines('data: {"choices":[{"delta":{"content":"Hel"}}]}'), then_raise=OSError("connection reset")
        )

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    deltas: list[str] = []
    error_detail: list[str] = []
    result = router.route_text("copilot_reply", {}, 5_000, error_detail=error_detail, on_delta=deltas.append)

    assert result is None
    assert deltas == ["Hel"], "the caller must not get the same words twice"
    assert len(calls) == 1, "a retry would repeat what the caller already has and bill a second generation"
    assert any("not retried" in line for line in error_detail)


def test_a_genuinely_empty_stream_still_fails_over(monkeypatch):
    router = _fresh_router([_entry(), _entry(provider="fake2")], max_retries=3)
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append("called")
        return _FakeStreamResponse(_GENUINELY_EMPTY_BODY)

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    error_detail: list[str] = []
    result = router.route_text("execute_chat", {}, 5_000, error_detail=error_detail, on_delta=lambda c: None)

    assert result is None
    assert len(calls) > 1, "nothing reached the caller, so failing over is safe"
    assert any(line == "stream produced no content" for line in error_detail)


def test_proxy_meters_a_tool_only_turn_once_with_output_tokens(monkeypatch):
    from app.api import llm_proxy_routes
    from app.main import app
    from app.services import llm_metering

    args = json.dumps({"skill": "wrk_local", "inputs": {"note": "x" * 400}})

    class FakeRouter:
        def route_text(self, task, payload, timeout_ms, *, error_detail=None, pool=None, on_delta=None):
            on_delta({"type": "tool_call", "index": 0, "id": "call_1", "name": "execute_skill", "arguments_delta": ""})
            on_delta({"type": "tool_call", "index": 0, "id": None, "name": None, "arguments_delta": args})
            return {"text": "", "output": ""}

    recorded: list[dict] = []
    monkeypatch.setattr(llm_proxy_routes, "get_router", lambda: FakeRouter())
    monkeypatch.setattr(
        llm_proxy_routes, "record_execute_pool_usage",
        lambda org_id, *, input_tokens, output_tokens: recorded.append({"in": input_tokens, "out": output_tokens}),
    )

    r = TestClient(app).post(
        "/api/v1/llm/proxy/text/stream",
        json={
            "task": "execute_chat",
            "payload": {"messages": [{"role": "user", "content": "hi"}]},
            "usage_class": "execute_chat",
        },
        headers={"X-Conxa-Client": "conxa-execute"},
    )
    assert r.status_code == 200, r.text
    events = [json.loads(line[len("data:"):]) for line in r.text.splitlines() if line.startswith("data:")]
    assert events[-1].get("done") is True, f"a tool-only turn ends in done, not an error: {events[-1]}"
    assert not any("error" in e for e in events)
    assert len(recorded) == 1, "metered once per turn"
    assert recorded[0]["out"] > llm_metering.estimate_response_tokens({"text": ""}) + 50
