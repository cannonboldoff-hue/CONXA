"""LLMRouter's multimodal model slot (BUILD-26) — Copilot's screenshot-bearing turns must
cool/select independently of the compiler's plain vision tasks (anchor_vision, ...), since both can live on the same pool entry with distinct model IDs. Before this, Copilot
was unconditionally routed through vision_model/cooled_until_vision, so a rate-limited vision
model would falsely cool down Copilot's own multimodal model on the same entry (and vice versa).
"""

from __future__ import annotations

import io
import urllib.error
from email.message import Message
from threading import Lock as _Lock

from app.llm.router import LLMRouter, PoolEntry


def _entry(**overrides) -> PoolEntry:
    base = dict(
        provider="fake",
        endpoint="https://fake.example/v1",
        api_key="key",
        text_model="fake-text",
        vision_model="fake-vision",
        multimodal_model="fake-multimodal",
        pool="free",
    )
    base.update(overrides)
    return PoolEntry(**base)


def _fresh_router(entries: list[PoolEntry], *, max_retries: int = 1) -> LLMRouter:
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


def _http_429() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://fake.example/openai/v1/chat/completions",
        code=429,
        msg="rate limited",
        hdrs=Message(),
        fp=io.BytesIO(b'{"error": "rate_limited"}'),
    )


def test_copilot_screenshot_turn_cools_multimodal_not_vision(monkeypatch):
    router = _fresh_router([_entry()])

    def fake_urlopen(req, timeout=None):
        raise _http_429()

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.llm.router.settings.environment", "prod")

    router.route_vision("copilot_diagnose", {"image_base64": "abc"}, 5_000)

    entry = router.pool[0]
    assert entry.cooled_until_multimodal > 0.0
    assert entry.cooled_until_vision == 0.0


def test_plain_vision_task_cools_vision_not_multimodal(monkeypatch):
    router = _fresh_router([_entry()])

    def fake_urlopen(req, timeout=None):
        raise _http_429()

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    router.route_vision("anchor_vision", {}, 5_000)

    entry = router.pool[0]
    assert entry.cooled_until_vision > 0.0
    assert entry.cooled_until_multimodal == 0.0


def test_copilot_screenshot_turn_falls_back_to_vision_model_when_multimodal_unset(monkeypatch):
    router = _fresh_router([_entry(multimodal_model="")])
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        import json as json_mod

        captured["model"] = json_mod.loads(req.data)["model"]
        raise TimeoutError("stop after inspecting the request")

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    router.route_vision("copilot_reply", {"image_base64": "abc"}, 5_000)

    assert captured["model"] == "fake-vision"


def test_copilot_text_only_turn_uses_text_model(monkeypatch):
    router = _fresh_router([_entry()])
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        import json as json_mod

        captured["model"] = json_mod.loads(req.data)["model"]
        raise TimeoutError("stop after inspecting the request")

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    router.route_text("copilot_diagnose", {}, 5_000)

    assert captured["model"] == "fake-text"
    assert router.pool[0].cooled_until_text > 0.0
    assert router.pool[0].cooled_until_multimodal == 0.0
