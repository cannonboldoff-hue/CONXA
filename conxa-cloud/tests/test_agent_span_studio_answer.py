"""The Build Studio sandbox's stand-in answerers (handlers/workflows.py's cmd_test_workflow park/resume loop).

No MCP agent is present in a Studio test run, so Studio answers an agent span (and a popup nobody taught the
workflow) itself through the metered cloud proxy. Covers the pure helpers the loop is built from (context
extraction, answer validation -- the same structural check as runtime/app/agent_span.js::validateAnswer)
and the text-route-first-with-vision-fallback behavior of both answerers. The surrounding cmd_test_workflow
loop is thin control flow over these already-tested pieces, so it isn't re-mocked here."""

from __future__ import annotations

import pytest

from handlers.workflows import WorkflowsMixin, _review_context_from_content, _validate_answer_schema
from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded


# ── _review_context_from_content ─────────────────────────────────────────────

def test_context_joins_text_and_picks_the_last_image():
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


def test_context_handles_no_image():
    text, image = _review_context_from_content([{"type": "text", "text": "just text"}])
    assert text == "just text" and image is None


# ── _validate_answer_schema ──────────────────────────────────────────────────

def test_validate_answer_no_schema_always_passes():
    assert _validate_answer_schema({"anything": "goes"}, None) == []


def test_validate_answer_non_object_fails_when_schema_present():
    assert _validate_answer_schema("yes", {"type": "object", "properties": {}}) == ["answer must be a JSON object"]


def test_validate_answer_missing_required_field_fails():
    schema = {"required": ["visible"], "properties": {"visible": {"type": "boolean"}}}
    assert _validate_answer_schema({"why": "no banner"}, schema) == ['missing required field "visible"']


def test_validate_answer_wrong_type_and_enum_fail():
    assert _validate_answer_schema({"visible": "yes"}, {"properties": {"visible": {"type": "boolean"}}}) == ['field "visible" must be of type boolean']
    schema = {"properties": {"status": {"type": "string", "enum": ["ok", "error"]}}}
    assert _validate_answer_schema({"status": "maybe"}, schema) == ['field "status" must be one of ["ok", "error"]']


def test_validate_answer_matching_canonical_shape_passes():
    schema = {
        "required": ["answer", "why"],
        "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}, "why": {"type": "string"}},
    }
    assert _validate_answer_schema({"answer": "yes", "why": "banner is showing"}, schema) == []


# ── the answerers ─────────────────────────────────────────────────────────────

class _FakeBackend(WorkflowsMixin):
    """Just enough of the Backend class for the answerers' self._install_proxy_router()."""

    def _install_proxy_router(self, sink=None, *, usage_class="compile"):
        self.installed_usage_class = usage_class


SPAN = {
    "step_index": 0, "goal": "Is there an error banner?", "allowed": ["click", "done", "give_up", "wait", "read_text"],
    "candidates": [{"i": 0, "name": "Open"}], "answer_schema": None,
}
FORK = {"step_index": 1, "title": "File exists", "choices": [{"id": "replace", "label": "Replace"}, {"id": "keep_both", "label": "Keep both"}]}


def test_agent_span_answerer_uses_text_route_meters_as_ai_credits_and_returns_a_checked_action(monkeypatch):
    backend = _FakeBackend()
    calls = []

    def fake_call_llm(task, payload, timeout_ms, **kwargs):
        calls.append((task, payload))
        return {"kind": "click", "candidate_index": 0}

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", fake_call_llm)
    content = [{"type": "text", "text": "Page elements: []"}, {"type": "image", "data": "shot-b64", "mimeType": "image/jpeg"}]
    action = backend._answer_agent_span(SPAN, content)
    assert action == {"kind": "click", "candidate_index": 0}
    assert backend.installed_usage_class == "ai_credits"
    assert calls[0][0] == "agent_span" and calls[0][1]["image_base64"] == "shot-b64"


def test_agent_span_answerer_tells_the_model_the_answer_schema(monkeypatch):
    backend = _FakeBackend()
    seen = {}

    def fake_call_llm(task, payload, timeout_ms, **kwargs):
        seen["text"] = payload["user_text"]
        return {"kind": "done", "answer": {"visible": True}}

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", fake_call_llm)
    schema = {"type": "object", "required": ["visible"]}
    action = backend._answer_agent_span({**SPAN, "answer_schema": schema}, [{"type": "text", "text": "ctx"}])
    assert action == {"kind": "done", "answer": {"visible": True}}
    assert '"required": ["visible"]' in seen["text"]


def test_agent_span_answerer_falls_back_to_the_vision_route_on_cloud_unreachable(monkeypatch):
    backend = _FakeBackend()

    def fake_call_llm(task, payload, timeout_ms, **kwargs):
        raise CloudUnreachable("provider rejected the request")

    class _FakeRouter:
        def route_vision(self, task, payload, timeout_ms, **kwargs):
            assert task == "agent_span"
            return {"kind": "done"}

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", fake_call_llm)
    monkeypatch.setattr("conxa_core.llm.get_router", lambda: _FakeRouter())
    assert backend._answer_agent_span(SPAN, [{"type": "text", "text": "ctx"}]) == {"kind": "done"}


@pytest.mark.parametrize("make_exc", [lambda: QuotaExceeded("quota"), lambda: EntitlementBlocked("human_edit_pool_exceeded")])
def test_answerers_propagate_quota_and_entitlement_errors_without_a_fallback_call(monkeypatch, make_exc):
    backend = _FakeBackend()
    vision_called = []

    def fake_call_llm(task, payload, timeout_ms, **kwargs):
        raise make_exc()

    class _FakeRouter:
        def route_vision(self, *a, **k):
            vision_called.append(True)
            return {}

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", fake_call_llm)
    monkeypatch.setattr("conxa_core.llm.get_router", lambda: _FakeRouter())
    with pytest.raises((QuotaExceeded, EntitlementBlocked)):
        backend._answer_agent_span(SPAN, [])
    with pytest.raises((QuotaExceeded, EntitlementBlocked)):
        backend._answer_popup_choice(FORK, [])
    assert vision_called == [], "quota/entitlement errors should not trigger the vision fallback"


def test_a_model_that_returns_garbage_gives_the_span_up_instead_of_crashing_the_test(monkeypatch):
    backend = _FakeBackend()
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda *a, **k: "not json")
    assert backend._answer_agent_span(SPAN, [])["status"] == "give_up"


def test_popup_answerer_names_a_listed_button_under_the_popup_task(monkeypatch):
    backend = _FakeBackend()
    tasks = []

    def fake_call_llm(task, payload, timeout_ms, **kwargs):
        tasks.append(task)
        return {"choice": "keep_both"}

    monkeypatch.setattr("conxa_compile.llm.client.call_llm", fake_call_llm)
    assert backend._answer_popup_choice(FORK, [{"type": "text", "text": "ctx"}]) == "keep_both"
    assert tasks == ["popup_choice"]


def test_popup_answerer_raises_on_a_button_that_was_not_offered(monkeypatch):
    backend = _FakeBackend()
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda *a, **k: {"choice": "delete_everything"})
    with pytest.raises(RuntimeError, match="did not match the offered buttons"):
        backend._answer_popup_choice(FORK, [])
