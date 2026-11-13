"""The decision-model client and its proxy route (POST /api/v1/llm/proxy/decide)."""

import pytest
from fastapi.testclient import TestClient

from app.llm import decisions
from app.main import app
from app.services import llm_metering
from conxa_core.config import settings

client = TestClient(app)
STUDIO_HEADER = {"X-Conxa-Client": settings.llm_proxy_client_header}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    for flag in ("compile", "human_edit", "distribution", "machines"):
        monkeypatch.setattr(settings, f"entitlements_enforce_{flag}", False)
    monkeypatch.setattr(settings, "llm_proxy_monthly_token_quota", 1_000_000)


def test_questions_are_cleaned_and_always_predicates():
    raw = [{"name": "a", "instructions": "q1", "type": "choice"}, {"name": "a", "instructions": "dup"},
           {"name": "", "instructions": "no name"}, {"name": "b"}, "junk", {"name": "c", "instructions": "x" * 900}]
    out = decisions.clean_questions(raw)
    assert [q["name"] for q in out] == ["a", "c"]
    assert all(q["type"] == "predicate" for q in out) and len(out[1]["instructions"]) == decisions.MAX_INSTRUCTION_CHARS


def test_only_valid_predicate_probabilities_for_asked_names_are_kept():
    data = {"answers": [
        {"type": "predicate", "name": "a", "probability": 0.9},
        {"type": "predicate", "name": "b", "probability": 1.4},      # out of range
        {"type": "predicate", "name": "zzz", "probability": 0.5},    # never asked
        {"type": "refusal", "name": "c"},
        {"type": "choice", "name": "d", "probability": 0.7},
        {"type": "predicate", "name": "e", "probability": True},
    ]}
    assert decisions.parse_probabilities(data, {"a", "b", "c", "d", "e"}) == {"a": 0.9}
    assert decisions.parse_probabilities({"nope": 1}, {"a"}) == {}


def test_decide_sends_the_documented_shape(monkeypatch):
    monkeypatch.setattr(settings, "llm_decisions_api_key", "k")
    seen = {}

    class _Resp:
        def raise_for_status(self): ...
        def json(self): return {"answers": [{"type": "predicate", "name": "a", "probability": 0.8}]}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, body=json, auth=headers["Authorization"])
        return _Resp()

    monkeypatch.setattr(decisions.httpx, "post", fake_post)
    out = decisions.decide({"input": "facts", "questions": [{"name": "a", "instructions": "q"}]})
    assert out == {"probabilities": {"a": 0.8}}
    assert seen["url"] == "https://openrouter.ai/api/v1/decisions" and seen["auth"] == "Bearer k"
    assert seen["body"] == {"model": "openai/gpt-6-luna-decisions", "input": "facts",
                            "questions": [{"type": "predicate", "name": "a", "instructions": "q"}]}


def test_route_without_a_key_answers_501_and_a_provider_error_answers_502(monkeypatch):
    monkeypatch.setattr(settings, "llm_decisions_api_key", "")
    body = {"task": "proof_pick", "payload": {"input": "x", "questions": [{"name": "a", "instructions": "q"}]}}
    r = client.post("/api/v1/llm/proxy/decide", json=body, headers=STUDIO_HEADER)
    assert (r.status_code, r.json()["detail"]) == (501, "decide_unavailable")

    monkeypatch.setattr(settings, "llm_decisions_api_key", "k")
    monkeypatch.setattr(decisions, "decide", lambda payload, timeout_s=30.0: None)
    assert client.post("/api/v1/llm/proxy/decide", json=body, headers=STUDIO_HEADER).status_code == 502


def test_route_requires_the_studio_header_and_meters_a_good_call(monkeypatch):
    body = {"task": "proof_pick", "payload": {"input": "x", "questions": [{"name": "a", "instructions": "q"}]}}
    assert client.post("/api/v1/llm/proxy/decide", json=body).status_code == 403

    monkeypatch.setattr(decisions, "decide", lambda payload, timeout_s=30.0: {"probabilities": {"a": 0.7}})
    before = llm_metering.get_usage("wrk_local")["requests"]
    r = client.post("/api/v1/llm/proxy/decide", json=body, headers=STUDIO_HEADER)
    assert r.status_code == 200 and r.json() == {"probabilities": {"a": 0.7}}
    assert llm_metering.get_usage("wrk_local")["requests"] == before + 1
