"""LLMRouter's 429 handling: honour Retry-After, and wait briefly for a cooled
pool to clear instead of instantly failing a compile step over a transient
rate limit.

Before this behaviour existed, every 429 cooled its key for a flat
llm_router_cooldown_secs (60s) with no regard for what the provider actually
asked for, and route_text/route_vision gave up the instant no entry was
immediately available — even when every entry was seconds from clearing.
"""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from threading import Lock as _Lock

from app.llm.router import LLMRouter, PoolEntry, _parse_retry_after_secs


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


def _http_429(retry_after: str | None) -> urllib.error.HTTPError:
    hdrs = Message()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        url="https://fake.example/openai/v1/chat/completions",
        code=429,
        msg="rate limited",
        hdrs=hdrs,
        fp=io.BytesIO(b'{"error": "rate_limited"}'),
    )


def _http_error(code: int, body: bytes = b'{"error": "bad"}') -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://fake.example/openai/v1/chat/completions",
        code=code,
        msg="error",
        hdrs=Message(),
        fp=io.BytesIO(body),
    )


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


def _http_200(body: dict) -> bytes:
    return json.dumps(body).encode("utf-8")


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_retry_after_header_sets_cooldown_not_flat_default(monkeypatch):
    router = LLMRouter.__new__(LLMRouter)
    router.pool = [_entry()]
    router.cooldown_secs = 60
    router.max_retries = 1
    router.wait_ceiling_secs = 8.0
    router._request_counter = 0
    router._last_lru_index = -1
    router._lru_lock = _Lock()
    router.total_budget_secs = 75.0

    def fake_urlopen(req, timeout=None):
        raise _http_429("2")

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    result = router.route_text("intent", {}, 5_000)

    assert result is None
    entry = router.pool[0]
    import time as time_mod
    remaining = entry.cooled_until_text - time_mod.monotonic()
    assert 0 < remaining <= 3, f"expected ~2s cooldown from Retry-After, got {remaining:.2f}s"


def test_missing_retry_after_falls_back_to_flat_cooldown(monkeypatch):
    router = LLMRouter.__new__(LLMRouter)
    router.pool = [_entry()]
    router.cooldown_secs = 60
    router.max_retries = 1
    router.wait_ceiling_secs = 8.0
    router._request_counter = 0
    router._last_lru_index = -1
    router._lru_lock = _Lock()
    router.total_budget_secs = 75.0

    def fake_urlopen(req, timeout=None):
        raise _http_429(None)

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    router.route_text("intent", {}, 5_000)

    import time as time_mod
    remaining = router.pool[0].cooled_until_text - time_mod.monotonic()
    assert 25 < remaining <= 30


def test_waits_for_soonest_cooldown_then_succeeds(monkeypatch):
    import time as time_mod

    router = LLMRouter.__new__(LLMRouter)
    router.pool = [_entry(provider="a"), _entry(provider="b")]
    router.cooldown_secs = 60
    router.max_retries = 2
    router.wait_ceiling_secs = 8.0
    router._request_counter = 0
    router._last_lru_index = -1
    router._lru_lock = _Lock()
    router.total_budget_secs = 75.0

    now = time_mod.monotonic()
    for e in router.pool:
        e.cooled_until_text = now + 0.3  # both cooled, but clear well within wait_ceiling_secs

    slept: list[float] = []
    # app.llm.router imports the same `time` module object as this test, so patching
    # its `.sleep` attribute patches the module globally — grab the real function first
    # or the wrapper below recurses into itself.
    real_sleep = time_mod.sleep

    def fake_sleep(s: float) -> None:
        # Record the wait but actually pause — the router re-checks cooled_until
        # against real monotonic time after waking, so a no-op mock would leave
        # every entry still cooled and defeat the point of this test.
        slept.append(s)
        real_sleep(s)

    monkeypatch.setattr("app.llm.router.time.sleep", fake_sleep)

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(_http_200({"choices": [{"message": {"content": "{}"}}]}))

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    result = router.route_text("intent", {}, 5_000)

    assert result is not None
    # One wait to the cooldown, then (on a coarse Windows clock) a few tiny remainders until a tick passes.
    assert slept and all(x > 0 for x in slept)
    assert slept[0] <= 0.31 and sum(slept) <= 0.4


def test_cooldown_beyond_ceiling_fails_fast_without_sleeping(monkeypatch):
    import time as time_mod

    router = LLMRouter.__new__(LLMRouter)
    router.pool = [_entry()]
    router.cooldown_secs = 60
    router.max_retries = 2
    router.wait_ceiling_secs = 8.0
    router._request_counter = 0
    router._last_lru_index = -1
    router._lru_lock = _Lock()
    router.total_budget_secs = 75.0
    router.pool[0].cooled_until_text = time_mod.monotonic() + 60  # far beyond the ceiling

    slept: list[float] = []
    monkeypatch.setattr("app.llm.router.time.sleep", lambda s: slept.append(s))

    def fake_urlopen(req, timeout=None):
        raise AssertionError("should never call the provider while cooled beyond the ceiling")

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    error_detail: list[str] = []
    result = router.route_text("intent", {}, 5_000, error_detail=error_detail)

    assert result is None
    assert slept == []
    assert "all providers cooled or exhausted" in error_detail[0]


def test_parse_retry_after_secs_rejects_garbage_and_out_of_range():
    assert _parse_retry_after_secs(Message()) is None
    hdrs = Message()
    hdrs["Retry-After"] = "not-a-number"
    assert _parse_retry_after_secs(hdrs) is None
    hdrs = Message()
    hdrs["Retry-After"] = "5000"
    assert _parse_retry_after_secs(hdrs) is None
    hdrs = Message()
    hdrs["Retry-After"] = "3"
    assert _parse_retry_after_secs(hdrs) == 3.0


def test_deterministic_4xx_does_not_cool_entry_or_retry_other_providers(monkeypatch):
    """A 400/413/422 means every provider will reject this exact payload the same
    way — cooling the (healthy) key and burning the other two entries on a request
    that can't succeed anywhere is pure waste. It should fail once, fast, clean."""
    router = _fresh_router([_entry(provider="a"), _entry(provider="b")], max_retries=3)
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append("called")
        raise _http_error(400, b'{"error": "context_length_exceeded"}')

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    error_detail: list[str] = []
    result = router.route_text("intent", {}, 5_000, error_detail=error_detail)

    assert result is None
    assert len(calls) == 1, "should not retry a deterministic rejection against another provider"
    assert router.pool[0].cooled_until_text == 0.0
    assert router.pool[1].cooled_until_text == 0.0
    assert "deterministic_rejection" in error_detail[0]


def test_text_timeout_does_not_cool_vision_capacity(monkeypatch):
    """Before per-modality cooldowns, a text-timeout storm benched the same key's
    vision_model too — so the one final vision call in a compile would find an
    empty pool right after a burst of unrelated text failures."""
    router = _fresh_router([_entry()], max_retries=1)

    def fake_urlopen(req, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    router.route_text("intent", {}, 5_000)

    entry = router.pool[0]
    assert entry.cooled_until_text > 0.0
    assert entry.cooled_until_vision == 0.0
    # so vision selection still finds this entry
    assert router._next_available_entry(for_vision=True) is entry


def test_consecutive_timeouts_back_off_before_capping(monkeypatch):
    entry = _entry()
    router = _fresh_router([entry], max_retries=1)

    def fake_urlopen(req, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    import time as time_mod

    for expected in (5.0, 15.0, 45.0, 60.0):
        router.route_text("intent", {}, 5_000)
        remaining = entry.cooled_until_text - time_mod.monotonic()
        assert expected - 1 < remaining <= expected, (
            f"expected ~{expected}s cooldown, got {remaining:.2f}s "
            f"(consecutive_transient_failures={entry.consecutive_transient_failures})"
        )
        entry.cooled_until_text = 0.0  # clear so the next call is selectable


def test_401_quarantines_instead_of_removing_key(monkeypatch):
    """401/403 used to delete the pool entry permanently with no re-admission
    path — a transient auth glitch shrank the pool toward empty until process
    restart. It should instead quarantine (temporarily unselectable) and self-heal."""
    router = _fresh_router([_entry()], max_retries=1)

    def fake_urlopen(req, timeout=None):
        raise _http_error(401, b'{"error": "invalid_api_key"}')

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    router.route_text("intent", {}, 5_000)

    assert len(router.pool) == 1, "401 must not remove the entry from the pool"
    entry = router.pool[0]
    assert entry.quarantined_until > 0.0
    assert router._next_available_entry() is None  # quarantined, not selectable
    entry.quarantined_until = 0.0
    assert router._next_available_entry() is entry  # selectable again once cleared


def test_route_stops_when_total_budget_exhausted(monkeypatch):
    """Every attempt is bounded by total_budget_secs wall-clock so a degraded pool
    answers with a real 502 well inside Render's own proxy timeout, instead of
    3 attempts x timeout_ms blowing past it and getting cut off at the edge."""
    router = _fresh_router([_entry(provider="a"), _entry(provider="b")], max_retries=3)
    router.total_budget_secs = 0.05

    import time as time_mod

    # A fake clock the slow attempt advances itself: no real waiting, no dependence on the OS timer tick.
    class _FakeTime:
        now = 1000.0

        def monotonic(self):
            return self.now

        def sleep(self, secs):
            self.now += secs

        def __getattr__(self, name):
            return getattr(time_mod, name)

    fake_time = _FakeTime()
    monkeypatch.setattr("app.llm.router.time", fake_time)

    def fake_urlopen(req, timeout=None):
        fake_time.now += 0.06  # first attempt alone exceeds the whole budget
        raise TimeoutError("timed out")

    monkeypatch.setattr("app.llm.router.request.urlopen", fake_urlopen)

    calls: list[str] = []
    real_call_provider = router._call_provider

    def counting_call_provider(*args, **kwargs):
        calls.append("call")
        return real_call_provider(*args, **kwargs)

    monkeypatch.setattr(router, "_call_provider", counting_call_provider)

    error_detail: list[str] = []
    result = router.route_text("intent", {}, 5_000, error_detail=error_detail)

    assert result is None
    assert len(calls) == 1, "budget should be exhausted after the first slow attempt"
    assert "total request budget exhausted" in error_detail[-1]
