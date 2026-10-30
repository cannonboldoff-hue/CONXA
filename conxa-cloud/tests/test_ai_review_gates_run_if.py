"""A free-form ai_review answer may not gate a destructive step; attach names are validated."""
import pytest

from conxa_compile.editor.patch_gate import (
    CONFIRM_ONLY_REVIEW_SCHEMA,
    validate_editor_patch,
)

POLICY: dict = {}


def _free_review(output_name="verdict"):
    return {
        "action": {"action": "ai_review"}, "intent": "decide", "value": output_name,
        "ai_review_prompt": "Is it a duplicate?",
        "ai_review_output_schema": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}},
        "ai_review_on_failure": "abort",
    }


def _confirm_review():
    return {
        "action": {"action": "ai_review"}, "intent": "confirm", "value": "verdict", "ai_review_prompt": "Send?",
        "ai_review_output_schema": dict(CONFIRM_ONLY_REVIEW_SCHEMA), "ai_review_on_failure": "abort",
    }


def _destructive():
    return {"action": {"action": "click"}, "intent": "send_invoice", "consequence": "irreversible"}


def test_destructive_step_cannot_be_gated_by_a_free_form_review_answer():
    patch = {"run_if": {"input": "verdict_answer", "op": "equals", "value": "yes"}}
    with pytest.raises(ValueError, match="destructive_step_cannot_be_gated_by_ai_review_answer"):
        validate_editor_patch(_destructive(), patch, POLICY, earlier_steps=[_free_review()])


def test_a_safe_step_may_be_gated_by_a_review_answer():
    step = {"action": {"action": "navigate"}, "intent": "go", "url": "https://example.com"}
    patch = {"run_if": {"input": "verdict_answer", "op": "equals", "value": "yes"}}
    validate_editor_patch(step, patch, POLICY, earlier_steps=[_free_review()])


def test_a_confirm_only_review_may_gate_anything():
    step = {"action": {"action": "navigate"}, "intent": "go", "url": "https://example.com"}
    patch = {"run_if": {"input": "verdict_confirmed", "op": "equals", "value": "true"}}
    validate_editor_patch(step, patch, POLICY, earlier_steps=[_confirm_review()])


def test_run_if_on_an_unrelated_input_is_not_affected():
    patch = {"run_if": {"input": "coupon", "op": "not_empty"}}
    # coupon is not produced by the review, so the lint stays out of the way
    validate_editor_patch(_destructive(), patch, POLICY, earlier_steps=[_free_review()])


def test_ai_review_attach_accepts_input_names_only():
    review = _free_review()
    validate_editor_patch(review, {"ai_review_attach": ["downloaded_file"]}, POLICY)
    validate_editor_patch(review, {"ai_review_attach": []}, POLICY)
    for bad in ("downloaded_file", ["../etc/passwd"], ["a"] * 6, [1]):
        with pytest.raises(ValueError, match="ai_review_attach_invalid"):
            validate_editor_patch(review, {"ai_review_attach": bad}, POLICY)
