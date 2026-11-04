"""Model-specific request fields live in one table, and every task's output ceiling in another."""
from __future__ import annotations

from app.llm.router import _model_request_extras
from conxa_core.llm.client import TASK_OUTPUT_BUDGET, _openai_body_dict


def test_kimi_on_openrouter_gets_its_pinned_provider() -> None:
    extras = _model_request_extras("https://openrouter.ai/api/v1", "moonshotai/Kimi-K3")
    assert extras["reasoning"] == {"enabled": True}
    assert extras["provider"]["allow_fallbacks"] is False


def test_other_models_and_hosts_get_nothing_extra() -> None:
    assert _model_request_extras("https://openrouter.ai/api/v1", "some/other-model") == {}
    assert _model_request_extras("https://api.groq.com/openai/v1", "kimi-k3") == {}


def test_budget_table_feeds_the_request_body() -> None:
    body = _openai_body_dict("timeline_understanding", {"model": "m", "messages": []}, json_mode=False)
    assert body["max_tokens"] == TASK_OUTPUT_BUDGET["timeline_understanding"] == 16384
    unlisted = _openai_body_dict("some_new_task", {"model": "m", "messages": []}, json_mode=False)
    assert "max_tokens" not in unlisted
