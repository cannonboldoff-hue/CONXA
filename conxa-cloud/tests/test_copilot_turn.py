"""conxa_compile.llm.copilot::copilot_turn (BUILD-26 stage b2) — the Studio-side client for one
Human Review Copilot conversation turn. Graceful-empty on failure except the three infra
exceptions, which must keep bubbling up (same contract as region_selector_vision.py)."""

from __future__ import annotations

from unittest.mock import patch

from services.llm_proxy_client import (
    CloudUnreachable,
    EntitlementBlocked,
    ProxyUnavailable,
    QuotaExceeded,
)

from conxa_compile.llm.copilot import copilot_turn


def test_returns_reply_and_proposals_from_a_normal_response():
    with patch(
        "conxa_compile.llm.copilot.call_llm",
        return_value={"reply": "Step 3 failed because the button moved.", "proposals": [
            {"step_key": "h1#1", "field": "value", "patch": "x", "why": "y"},
        ]},
    ):
        result = copilot_turn(evidence={"steps": []}, transcript=[], message="why did step 3 fail?")
    assert result["reply"] == "Step 3 failed because the button moved."
    assert len(result["proposals"]) == 1


def test_drops_non_dict_proposals_and_missing_reply():
    with patch("conxa_compile.llm.copilot.call_llm", return_value={"proposals": ["not a dict", {"a": 1}]}):
        result = copilot_turn(evidence={}, transcript=[], message="hi")
    assert result["reply"] == ""
    assert result["proposals"] == [{"a": 1}]


def test_returns_empty_turn_when_call_llm_returns_none():
    with patch("conxa_compile.llm.copilot.call_llm", return_value=None):
        result = copilot_turn(evidence={}, transcript=[], message="hi")
    assert result == {"reply": "", "proposals": [], "needs_editor": []}


def test_returns_empty_turn_on_an_ordinary_exception():
    with patch("conxa_compile.llm.copilot.call_llm", side_effect=RuntimeError("router unset")):
        result = copilot_turn(evidence={}, transcript=[], message="hi")
    assert result == {"reply": "", "proposals": [], "needs_editor": []}


def test_infra_exceptions_propagate_instead_of_degrading():
    for exc in (QuotaExceeded("quota"), EntitlementBlocked("human_edit_pool_exceeded"), CloudUnreachable("down")):
        with patch("conxa_compile.llm.copilot.call_llm", side_effect=exc):
            try:
                copilot_turn(evidence={}, transcript=[], message="hi")
                assert False, f"expected {type(exc).__name__} to propagate"
            except type(exc):
                pass


def test_transcript_is_flattened_into_one_stateless_payload():
    captured = {}

    def _fake_call_llm(task, payload, timeout_ms, *, error_detail=None):
        captured["payload"] = payload
        return {"reply": "ok", "proposals": []}

    with patch("conxa_compile.llm.copilot.call_llm", side_effect=_fake_call_llm):
        copilot_turn(
            evidence={"x": 1},
            transcript=[{"role": "user", "text": "step 3 failed"}, {"role": "assistant", "text": "looking..."}],
            message="fix it",
        )
    user_text = captured["payload"]["user_text"]
    assert "step 3 failed" in user_text
    assert "looking..." in user_text
    assert "fix it" in user_text
    assert "image_base64" not in captured["payload"]


def test_missing_screenshot_path_degrades_to_text_only_with_a_note():
    captured = {}

    def _fake_call_llm(task, payload, timeout_ms, *, error_detail=None):
        captured["payload"] = payload
        return {"reply": "ok", "proposals": []}

    with patch("conxa_compile.llm.copilot.call_llm", side_effect=_fake_call_llm):
        copilot_turn(evidence={}, transcript=[], message="hi", screenshot_path="/no/such/file.jpg")
    assert "image_base64" not in captured["payload"]
    assert "No failure screenshot is available" in captured["payload"]["user_text"]


def test_on_delta_streams_via_stream_llm_and_the_streamed_reply_wins():
    """BUILD-26 stage c: when on_delta is given, copilot_turn calls stream_llm("copilot_reply", ...)
    for the prose the reviewer watches live, and the streamed text — not copilot_diagnose's own
    (identical) reply field — is what comes back, so the panel's streamed bubble and the final
    stored message are the exact same text (no post-stream swap to a re-generated answer)."""
    deltas: list[str] = []

    def _fake_stream_llm(task, payload, timeout_ms, *, on_delta, error_detail=None):
        assert task == "copilot_reply"
        for chunk in ("Step 3 ", "failed because the button moved."):
            on_delta(chunk)
        full = "Step 3 failed because the button moved."
        return {"text": full, "output": full}

    with (
        patch("conxa_compile.llm.copilot.stream_llm", side_effect=_fake_stream_llm),
        patch(
            "conxa_compile.llm.copilot.call_llm",
            return_value={"reply": "a different, re-generated answer", "proposals": []},
        ),
    ):
        result = copilot_turn(evidence={}, transcript=[], message="why did step 3 fail?", on_delta=deltas.append)

    assert deltas == ["Step 3 ", "failed because the button moved."]
    assert result["reply"] == "Step 3 failed because the button moved."


def test_streamed_deltas_become_the_reply_even_when_stream_llm_returns_none():
    """The panel commits `result.reply` after the turn — if we only forwarded chunks live and
    then returned an empty reply, the bubble vanished the moment the call resolved."""

    def _fake_stream_llm(task, payload, timeout_ms, *, on_delta, error_detail=None):
        on_delta("Step 3 failed because the button moved.")
        return None

    with (
        patch("conxa_compile.llm.copilot.stream_llm", side_effect=_fake_stream_llm),
        patch("conxa_compile.llm.copilot.call_llm", return_value={"proposals": []}),
    ):
        result = copilot_turn(evidence={}, transcript=[], message="why?", on_delta=lambda _: None)
    assert result["reply"] == "Step 3 failed because the button moved."


def test_diagnose_prose_text_field_is_used_as_reply_when_json_reply_is_missing():
    """Providers that ignore json_object mode come back as {text, output} from
    _normalize_openai_response — that is still the answer the reviewer should see."""
    with (
        patch("conxa_compile.llm.copilot.stream_llm", return_value=None),
        patch(
            "conxa_compile.llm.copilot.call_llm",
            return_value={"text": "Step 3 failed because the button moved.", "output": "Step 3 failed because the button moved."},
        ),
    ):
        result = copilot_turn(evidence={}, transcript=[], message="why?", on_delta=lambda _: None)
    assert result["reply"] == "Step 3 failed because the button moved."


def test_on_delta_falls_back_to_copilot_diagnose_reply_when_streaming_yields_nothing():
    with (
        patch("conxa_compile.llm.copilot.stream_llm", return_value=None),
        patch(
            "conxa_compile.llm.copilot.call_llm",
            return_value={"reply": "fallback answer", "proposals": []},
        ),
    ):
        result = copilot_turn(evidence={}, transcript=[], message="hi", on_delta=lambda _: None)
    assert result["reply"] == "fallback answer"


def test_on_delta_infra_exception_from_streaming_call_propagates():
    for exc in (QuotaExceeded("quota"), EntitlementBlocked("human_edit_pool_exceeded"), CloudUnreachable("down")):
        with patch("conxa_compile.llm.copilot.stream_llm", side_effect=exc):
            try:
                copilot_turn(evidence={}, transcript=[], message="hi", on_delta=lambda _: None)
                assert False, f"expected {type(exc).__name__} to propagate"
            except type(exc):
                pass


def test_proxy_unavailable_from_streaming_falls_back_to_diagnose():
    """ProxyUnavailable subclasses CloudUnreachable but means something else: the proxy answered,
    the STREAM just produced no text (a reasoning model that burns its whole completion budget on
    hidden chain-of-thought before writing content lands here). copilot_diagnose is a separate,
    non-streamed request that routinely succeeds when that happens — so the turn must degrade to
    it, not die. Before this, the reviewer's question sat in the panel with no answer at all."""
    with (
        patch(
            "conxa_compile.llm.copilot.stream_llm",
            side_effect=ProxyUnavailable("stream ended without a result"),
        ),
        patch(
            "conxa_compile.llm.copilot.call_llm",
            return_value={"reply": "Step 3 failed because the button moved.", "proposals": []},
        ),
    ):
        result = copilot_turn(evidence={}, transcript=[], message="why?", on_delta=lambda _: None)
    assert result["reply"] == "Step 3 failed because the button moved."


def test_proxy_unavailable_from_the_diagnose_call_still_propagates():
    """The fallback above only applies to the STREAM. When the non-streamed call is the one that
    comes back empty there is nothing left to degrade to, so it must surface as a real error
    rather than a silently empty turn."""
    with (
        patch("conxa_compile.llm.copilot.stream_llm", return_value=None),
        patch(
            "conxa_compile.llm.copilot.call_llm",
            side_effect=ProxyUnavailable("stream ended without a result"),
        ),
    ):
        try:
            copilot_turn(evidence={}, transcript=[], message="hi", on_delta=lambda _: None)
            assert False, "expected ProxyUnavailable to propagate"
        except ProxyUnavailable:
            pass
