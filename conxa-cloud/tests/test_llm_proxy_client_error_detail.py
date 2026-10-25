"""Studio-side LLM proxy client: the 502 error body's real failure reason must survive.

llm_proxy_routes.py answers `llm_all_providers_failed` with a dict `detail`
({"message": ..., "error_detail": [...]}) carrying each provider's actual failure
string (e.g. a 429 rate-limit reason). Before this test existed, LLMProxyClient._post
coerced that dict with str(...), so it never matched any of the flat entitlement
codes and the caller only ever saw "proxy HTTP 502" — indistinguishable from any
other cloud-side failure.

A 502/503/504 is now retried twice with backoff (see _RETRY_BACKOFFS_S) before
raising ProxyUnavailable — a real infra failure, not a silent None indistinguishable
from an empty LLM answer. Tests patch time.sleep so they don't actually wait ~5.5s.
"""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest

from services.llm_proxy_client import LLMProxyClient, ProxyUnavailable


def _http_error(code: int, body: dict) -> urllib.error.HTTPError:
    payload = json.dumps(body).encode("utf-8")
    return urllib.error.HTTPError(
        url="http://cloud.local/api/v1/llm/proxy/vision",
        code=code,
        msg="err",
        hdrs=Message(),
        fp=io.BytesIO(payload),
    )


def test_502_dict_detail_surfaces_provider_reason(monkeypatch):
    body = {
        "detail": {
            "message": "llm_all_providers_failed",
            "error_detail": ["HTTPError 429 rate_limited (cooled 2s): quota exceeded"],
        }
    }

    def fake_urlopen(req, timeout=None):
        raise _http_error(502, body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("services.llm_proxy_client.time.sleep", lambda s: None)

    proxy_client = LLMProxyClient("http://cloud.local", token_provider=lambda: "tok")
    detail: list[str] = []
    with pytest.raises(ProxyUnavailable) as exc_info:
        proxy_client.route_vision("anchor_vision", {"user_text": "x"}, 30_000, error_detail=detail)

    assert any("llm_all_providers_failed" in line for line in detail)
    assert any("HTTPError 429 rate_limited" in line for line in detail)
    assert any("llm_all_providers_failed" in line for line in exc_info.value.error_detail)


def test_502_flat_string_detail_still_appends_status(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise _http_error(502, {"detail": "some_unrecognized_string"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("services.llm_proxy_client.time.sleep", lambda s: None)

    proxy_client = LLMProxyClient("http://cloud.local", token_provider=lambda: "tok")
    detail: list[str] = []
    with pytest.raises(ProxyUnavailable):
        proxy_client.route_text("intent", {}, 30_000, error_detail=detail)

    # One "proxy HTTP 502" appended per attempt (first attempt + 2 retries).
    assert detail == ["proxy HTTP 502"] * 3


def test_502_retries_twice_with_backoff_before_raising(monkeypatch):
    calls: list[str] = []
    slept: list[float] = []

    def fake_urlopen(req, timeout=None):
        calls.append("call")
        raise _http_error(502, {"detail": "some_unrecognized_string"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("services.llm_proxy_client.time.sleep", lambda s: slept.append(s))

    proxy_client = LLMProxyClient("http://cloud.local", token_provider=lambda: "tok")
    with pytest.raises(ProxyUnavailable):
        proxy_client.route_text("intent", {}, 30_000)

    assert len(calls) == 3  # first attempt + 2 retries
    assert len(slept) == 2


def test_401_still_retries_and_entitlement_codes_still_raise(monkeypatch):
    from services.llm_proxy_client import EntitlementBlocked

    def fake_urlopen(req, timeout=None):
        raise _http_error(402, {"detail": "trial_expired"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    proxy_client = LLMProxyClient("http://cloud.local", token_provider=lambda: "tok")
    try:
        proxy_client.route_text("intent", {}, 30_000, error_detail=[])
        raised = None
    except EntitlementBlocked as exc:
        raised = exc
    assert raised is not None
    assert raised.code == "trial_expired"


def test_ai_credits_falls_back_to_legacy_class_on_an_older_cloud(monkeypatch):
    """CLOUD-25: a cloud that predates the rename answers 400 invalid_usage_class for
    "ai_credits". The client retries once as "human_edit" and keeps using it."""
    sent: list[str] = []

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"text": "ok"}).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        usage_class = json.loads(req.data.decode("utf-8"))["usage_class"]
        sent.append(usage_class)
        if usage_class == "ai_credits":
            raise _http_error(400, {"detail": "invalid_usage_class"})
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = LLMProxyClient("http://cloud.local", token_provider=lambda: "tok", usage_class="ai_credits")
    client.route_text("copilot_reply", {"user_text": "x"}, 30_000, error_detail=[])
    client.route_text("copilot_reply", {"user_text": "x"}, 30_000, error_detail=[])

    assert sent == ["ai_credits", "human_edit", "human_edit"]


def test_a_short_interactive_text_task_gets_its_own_deadline_not_the_90s_floor(monkeypatch):
    """BUILD-55 F04: workflow_understanding asks for 30 s; it must not run for the 90 s floor."""
    seen: list[float] = []

    def fake_urlopen(req, timeout=None):
        seen.append(timeout)
        raise _http_error(502, {"detail": "x"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    slept: list[float] = []
    monkeypatch.setattr("services.llm_proxy_client.time.sleep", lambda s: slept.append(s))

    client = LLMProxyClient("http://cloud.local", token_provider=lambda: "tok")
    with pytest.raises(ProxyUnavailable):
        client.route_text("workflow_understanding", {}, 30_000)

    assert seen[0] <= 35.0 + 0.5            # 30 s + 5 s slack, not 95 s
    assert len(seen) == 3 and len(slept) == 2  # backoffs (1 s, 4 s) still fit inside the deadline


def test_retries_stop_when_the_deadline_would_pass(monkeypatch):
    calls: list[str] = []
    clock = {"t": 1000.0}

    def fake_urlopen(req, timeout=None):
        calls.append("call")
        clock["t"] += 14.0                   # each attempt "takes" 14 s
        raise _http_error(502, {"detail": "x"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("services.llm_proxy_client.time.sleep", lambda s: None)
    monkeypatch.setattr("services.llm_proxy_client.time.monotonic", lambda: clock["t"])

    client = LLMProxyClient("http://cloud.local", token_provider=lambda: "tok")
    with pytest.raises(ProxyUnavailable):
        client.route_text("chat_map_answer", {}, 10_000)   # deadline = 15 s after the first call starts

    assert len(calls) == 1                   # a backoff would pass the deadline, so no retry is made


def test_other_text_tasks_keep_the_long_budget(monkeypatch):
    seen: list[float] = []

    def fake_urlopen(req, timeout=None):
        seen.append(timeout)
        raise _http_error(402, {"detail": "trial_expired"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    from services.llm_proxy_client import EntitlementBlocked

    client = LLMProxyClient("http://cloud.local", token_provider=lambda: "tok")
    with pytest.raises(EntitlementBlocked):
        client.route_text("semantic_enrichment", {}, 20_000)
    assert seen[0] == 95.0
