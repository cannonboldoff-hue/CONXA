"""EXEC-13: the Build Studio sandbox's stand-in answerer for an ai_review checkpoint
(handlers/workflows.py's cmd_test_workflow park/resume loop) — no MCP agent is present in a
Studio test run, so Studio answers the review itself through the metered cloud proxy.

Covers the pure helpers the loop is built from (pause detection, context extraction, answer
validation — the same structural check as runtime/app/review_pause.js::validateReviewAnswer)
and _answer_ai_review's text-route-first-with-vision-fallback behavior. The surrounding
cmd_test_workflow loop itself is thin control flow over these already-tested pieces plus the
sandbox-staging machinery this change doesn't touch, so it isn't re-mocked here.
"""

from __future__ import annotations

import pytest

from handlers.workflows import (
    WorkflowsMixin,
    _extract_ai_review_pause,
    _review_context_from_content,
    _validate_ai_review_answer,
)
from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded


# ── _extract_ai_review_pause ─────────────────────────────────────────────────

def test_extract_review_pause_from_meta():
    result = {
        "content": [{"type": "text", "text": "irrelevant"}],
        "_meta": {"conxa/ai_review": {
            "step_index": 2, "prompt": "Is X visible?", "output_schema": {"type": "object"},
        }},
    }
    review = _extract_ai_review_pause(result)
    assert review == {"step_index": 2, "prompt": "Is X visible?", "output_schema": {"type": "object"}}


def test_extract_review_pause_falls_back_to_prose_header_without_meta():
    result = {"content": [{"type": "text", "text": "paused...\n  resume_from: 3\n  review_results: ..."}]}
    review = _extract_ai_review_pause(result)
    assert review == {"step_index": 3, "prompt": "", "output_schema": None}


def test_extract_review_pause_returns_none_for_an_ordinary_result():
    result = {"content": [{"type": "text", "text": "Done. URL: https://x.test"}]}
    assert _extract_ai_review_pause(result) is None


# ── _review_context_from_content ─────────────────────────────────────────────

def test_review_context_joins_text_and_picks_the_last_image():
    content = [
        {"type": "text", "text": "header"},
        {"type": "text", "text": "Reference image:"},
        {"type": "image", "data": "ref-b64", "mimeType": "image/jpeg"},
        {"type": "text", "text": "Current page:"},
        {"type": "image", "data": "current-b64", "mimeType": "image/jpeg"},
    ]
    text, image = _review_context_from_content(content)
    assert "header" in text and "Current page" in text
    assert image == ("current-b64", "image/jpeg")


def test_review_context_handles_no_image():
    text, image = _review_context_from_content([{"type": "text", "text": "just text"}])
    assert text == "just text"
    assert image is None


# ── _validate_ai_review_answer ───────────────────────────────────────────────

def test_validate_answer_no_schema_always_passes():
    assert _validate_ai_review_answer({"anything": "goes"}, None) == []


def test_validate_answer_non_object_fails_when_schema_present():
    errors = _validate_ai_review_answer("yes", {"type": "object", "properties": {}})
    assert errors == ["answer must be a JSON object"]


def test_validate_answer_missing_required_field_fails():
    schema = {"required": ["visible"], "properties": {"visible": {"type": "boolean"}}}
    errors = _validate_ai_review_answer({"why": "no banner"}, schema)
    assert errors == ['missing required field "visible"']


def test_validate_answer_wrong_type_fails():
    schema = {"properties": {"visible": {"type": "boolean"}}}
    errors = _validate_ai_review_answer({"visible": "yes"}, schema)
    assert errors == ['field "visible" must be of type boolean']


def test_validate_answer_enum_mismatch_fails():
    schema = {"properties": {"status": {"type": "string", "enum": ["ok", "error"]}}}
    errors = _validate_ai_review_answer({"status": "maybe"}, schema)
    assert errors == ['field "status" must be one of ["ok", "error"]']


def test_validate_answer_matching_canonical_shape_passes():
    schema = {
        "required": ["answer", "why"],
        "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}, "why": {"type": "string"}},
    }
    assert _validate_ai_review_answer({"answer": "yes", "why": "banner is showing"}, schema) == []


# ── _answer_ai_review ─────────────────────────────────────────────────────────

class _FakeBackend(WorkflowsMixin):
    """Just enough of the Backend class for _answer_ai_review's self._install_proxy_router()."""

    def _install_proxy_router(self, sink=None, *, usage_class="compile"):
        self.installed_usage_class = usage_class


def _review(schema=None):
    return {"step_index": 0, "prompt": "Is there an error banner?", "output_schema": schema}


def test_answer_ai_review_uses_text_route_and_meters_as_human_edit(monkeypatch):
    backend = _FakeBackend()
    calls = []

    def fake_call_llm(task, payload, timeout_ms, **kwargs):
        calls.append((task, payload))
        return {"answer": "no", "why": "page looks fine"}

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", fake_call_llm)

    content = [
        {"type": "text", "text": "Page elements (live DOM): []"},
        {"type": "image", "data": "shot-b64", "mimeType": "image/jpeg"},
    ]
    schema = {"required": ["answer"], "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}}}
    answer = backend._answer_ai_review(_review(schema), content)

    assert answer == {"answer": "no", "why": "page looks fine"}
    assert backend.installed_usage_class == "ai_credits"
    assert calls[0][0] == "ai_review"
    assert calls[0][1]["image_base64"] == "shot-b64"


def test_answer_ai_review_falls_back_to_vision_route_on_cloud_unreachable(monkeypatch):
    backend = _FakeBackend()

    def fake_call_llm(task, payload, timeout_ms, **kwargs):
        raise CloudUnreachable("provider rejected the request")

    class _FakeRouter:
        def route_vision(self, task, payload, timeout_ms, **kwargs):
            assert task == "ai_review"
            return {"answer": "yes"}

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", fake_call_llm)
    monkeypatch.setattr("conxa_core.llm.get_router", lambda: _FakeRouter())

    answer = backend._answer_ai_review(_review(), [{"type": "text", "text": "ctx"}])
    assert answer == {"answer": "yes"}


@pytest.mark.parametrize("make_exc", [
    lambda: QuotaExceeded("quota"),
    lambda: EntitlementBlocked("human_edit_pool_exceeded"),
])
def test_answer_ai_review_propagates_quota_and_entitlement_errors_without_a_fallback_call(monkeypatch, make_exc):
    backend = _FakeBackend()
    vision_called = []

    def fake_call_llm(task, payload, timeout_ms, **kwargs):
        raise make_exc()

    class _FakeRouter:
        def route_vision(self, *a, **k):
            vision_called.append(True)
            return {"answer": "yes"}

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", fake_call_llm)
    monkeypatch.setattr("conxa_core.llm.get_router", lambda: _FakeRouter())

    with pytest.raises((QuotaExceeded, EntitlementBlocked)):
        backend._answer_ai_review(_review(), [])
    assert vision_called == [], "quota/entitlement errors should not trigger the vision fallback"


def test_answer_ai_review_raises_on_a_schema_violating_answer(monkeypatch):
    backend = _FakeBackend()
    monkeypatch.setattr(
        "conxa_compile.llm.client.call_llm",
        lambda *a, **k: {"wrong_field": "oops"},
    )
    schema = {"required": ["answer"], "properties": {"answer": {"type": "string"}}}
    with pytest.raises(RuntimeError, match="did not match the required output"):
        backend._answer_ai_review(_review(schema), [])
