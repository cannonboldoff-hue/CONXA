"""Tests for the editor patch gate's consequential-action assertion invariant.

validate_editor_patch() is not yet wired into any RPC handler (cmd_patch_step doesn't call it
today) — these tests exercise the function directly, the same way the destructive wait_for
invariant it sits alongside is exercised.
"""

from __future__ import annotations

import pytest

from conxa_compile.editor.patch_gate import validate_editor_patch


def _fill_step(*, required_assertion: bool | None = True) -> dict:
    assertions = []
    if required_assertion is not None:
        assertions = [
            {"type": "value_equals", "target": "#email", "expected": "alice@example.com", "required": required_assertion},
        ]
    return {
        "action": {"action": "fill", "value": "alice@example.com"},
        "intent": "Enter email address",
        "value": "alice@example.com",
        "target": {"primary_selector": "#email", "fallback_selectors": []},
        "signals": {},
        "validation": {"wait_for": {"type": "none"}, "assertions": assertions},
    }


def _submit_click_step(*, required_assertion: bool | None = True) -> dict:
    assertions = []
    if required_assertion is not None:
        assertions = [{"type": "state_changed", "required": required_assertion}]
    return {
        "action": {"action": "click"},
        "intent": "Submit the form",
        "target": {"primary_selector": "#submit-btn", "fallback_selectors": [], "type": "submit"},
        "signals": {},
        "validation": {"wait_for": {"type": "none"}, "assertions": assertions},
    }


def test_clearing_assertions_on_a_fill_step_raises():
    step = _fill_step(required_assertion=True)
    with pytest.raises(ValueError, match="consequential_step_requires_required_assertion"):
        validate_editor_patch(step, {"validation": {"assertions": []}}, {})


def test_unrelated_patch_on_a_fill_step_with_existing_required_assertion_is_allowed():
    step = _fill_step(required_assertion=True)
    # Doesn't touch validation at all — the required assertion carries over unchanged.
    validate_editor_patch(step, {"intent": "Enter the customer email"}, {})


def test_unrelated_patch_on_a_legacy_fill_step_without_assertions_is_allowed():
    # Backward compatibility: a step compiled before enforced post-conditions existed has no
    # required assertion. An edit that doesn't touch validation must not be blocked by it.
    step = _fill_step(required_assertion=None)
    validate_editor_patch(step, {"intent": "Enter the customer email"}, {})


def test_editing_validation_on_a_legacy_step_without_a_required_check_raises():
    # The patch explicitly touches validation but still leaves nothing enforced — this is the
    # case the invariant exists to catch (a human edit silently dropping the enforced check).
    step = _fill_step(required_assertion=None)
    patch = {"validation": {"assertions": [{"type": "text_present", "target": "ok", "required": False}]}}
    with pytest.raises(ValueError, match="consequential_step_requires_required_assertion"):
        validate_editor_patch(step, patch, {})


# ── EXEC-13: ai_review step patches ──────────────────────────────────────────

def _ai_review_step() -> dict:
    return {
        "action": {"action": "ai_review"},
        "intent": "Check whether the save succeeded",
        "value": "save_succeeded",
        "ai_review_prompt": "Is the confirmation banner visible?",
    }


def test_ai_review_step_accepts_a_valid_prompt_patch():
    step = _ai_review_step()
    validate_editor_patch(step, {"ai_review_prompt": "Is X visible?"}, {})


def test_ai_review_step_accepts_full_config_with_schema_and_use_default():
    step = _ai_review_step()
    patch = {
        "ai_review_prompt": "Is X visible?",
        "ai_review_output_schema": {"type": "object", "required": ["visible"]},
        "ai_review_reference_screenshot_ref": "visuals/Image_3.jpg",
        "ai_review_on_failure": "use_default",
        "ai_review_default_value": {"visible": False},
    }
    validate_editor_patch(step, patch, {})


def test_ai_review_step_rejects_empty_prompt():
    step = _ai_review_step()
    with pytest.raises(ValueError, match="ai_review_prompt_empty"):
        validate_editor_patch(step, {"ai_review_prompt": "   "}, {})


def test_ai_review_step_rejects_non_object_output_schema():
    step = _ai_review_step()
    with pytest.raises(ValueError, match="ai_review_output_schema_must_be_object"):
        validate_editor_patch(step, {"ai_review_output_schema": "not an object"}, {})


def test_ai_review_step_rejects_invalid_on_failure():
    step = _ai_review_step()
    with pytest.raises(ValueError, match="ai_review_on_failure_invalid"):
        validate_editor_patch(step, {"ai_review_on_failure": "retry_forever"}, {})


def test_ai_review_step_use_default_requires_default_value():
    step = _ai_review_step()
    with pytest.raises(ValueError, match="ai_review_use_default_requires_default_value"):
        validate_editor_patch(step, {"ai_review_on_failure": "use_default"}, {})


def test_ai_review_step_use_default_allowed_when_step_already_carries_a_default():
    # The patch only changes on_failure; the step itself already has a default_value from an
    # earlier edit — must not be forced to resubmit it every time.
    step = _ai_review_step()
    step["ai_review_default_value"] = {"visible": False}
    validate_editor_patch(step, {"ai_review_on_failure": "use_default"}, {})


def test_ai_review_step_rejects_recovery_patch():
    # No selector/identity to recover — recovery is excluded from the allowlist entirely,
    # not merely left unused (CLAUDE.md: ai_review is a checkpoint, not a recovery fallback).
    step = _ai_review_step()
    with pytest.raises(ValueError, match="ai_review_step_cannot_patch_recovery"):
        validate_editor_patch(step, {"recovery": {"max_attempts": 2}}, {})


def test_ai_review_step_rejects_unknown_keys():
    step = _ai_review_step()
    with pytest.raises(ValueError, match="ai_review_step_allows_only_ai_review_fields"):
        validate_editor_patch(step, {"target": {"primary_selector": "#x"}}, {})


# ── EXEC-21 (hand-over shape): handover step patches ────────────────────────
# The human sibling of ai_review above — same shape, but the message shown to the person rides
# the generic `value` field (see action_registry.py's VALUE_LABELS) rather than a dedicated
# `handover_message` field.

def _handover_step() -> dict:
    return {
        "action": {"action": "handover"},
        "intent": "Wait for the person to complete 2FA",
        "value": "Please complete the 2FA challenge, then click Done.",
    }


def test_handover_step_accepts_a_valid_message_patch():
    step = _handover_step()
    validate_editor_patch(step, {"value": "Please sign the document, then click Done."}, {})


def test_handover_step_accepts_on_failure_and_resume_when():
    step = _handover_step()
    validate_editor_patch(step, {
        "handover_on_failure": "continue",
        "handover_resume_when": {"selector": "#dashboard"},
        "handover_resume_when_timeout_ms": 15000,
    }, {})


def test_handover_step_rejects_empty_message():
    step = _handover_step()
    with pytest.raises(ValueError, match="handover_message_empty"):
        validate_editor_patch(step, {"value": "   "}, {})


def test_handover_step_rejects_invalid_on_failure():
    step = _handover_step()
    with pytest.raises(ValueError, match="handover_on_failure_invalid"):
        validate_editor_patch(step, {"handover_on_failure": "use_default"}, {})


def test_handover_step_rejects_non_object_resume_when():
    step = _handover_step()
    with pytest.raises(ValueError, match="handover_resume_when_must_be_object"):
        validate_editor_patch(step, {"handover_resume_when": "#dashboard"}, {})


def test_handover_step_rejects_non_positive_resume_when_timeout():
    step = _handover_step()
    with pytest.raises(ValueError, match="handover_resume_when_timeout_ms_invalid"):
        validate_editor_patch(step, {"handover_resume_when_timeout_ms": 0}, {})


def test_handover_step_rejects_recovery_patch():
    step = _handover_step()
    with pytest.raises(ValueError, match="handover_step_cannot_patch_recovery"):
        validate_editor_patch(step, {"recovery": {"tier": 2}}, {})


def test_handover_step_rejects_unknown_keys():
    step = _handover_step()
    with pytest.raises(ValueError, match="handover_step_allows_only_handover_fields"):
        validate_editor_patch(step, {"target": {"primary_selector": "#x"}}, {})


# ── EXEC-13 / PROD-3: destructive step cannot directly follow ai_review ─────

def _destructive_click_step() -> dict:
    return {
        "action": {"action": "click"},
        "intent": "Delete the record",
        "target": {"primary_selector": "#delete-btn", "fallback_selectors": []},
        "signals": {"semantic": {"is_destructive": True}, "anchors": [{"text": "Delete"}]},
        "validation": {"wait_for": {"type": "toast"}, "assertions": [{"type": "state_changed", "required": True}]},
    }


def test_destructive_step_cannot_directly_follow_ai_review():
    step = _destructive_click_step()
    with pytest.raises(ValueError, match="destructive_step_cannot_directly_follow_ai_review"):
        validate_editor_patch(step, {"intent": "Delete the record"}, {}, previous_step=_ai_review_step())


def _confirm_review(**over) -> dict:
    step = {
        "action": "ai_review",
        "ai_review_prompt": "Send the invoice?",
        "ai_review_output_schema": {"type": "object", "required": ["confirmed"], "properties": {"confirmed": {"type": "boolean", "enum": [True]}}},
        "ai_review_on_failure": "abort",
    }
    step.update(over)
    return step


def test_a_confirm_only_review_may_directly_precede_a_destructive_step():
    """BUILD-39: a yes/no that ends the run on "no" can only gate WHETHER the step runs."""
    validate_editor_patch(_destructive_click_step(), {"intent": "Send invoice"}, {}, previous_step=_confirm_review())


@pytest.mark.parametrize(
    "over",
    [
        # a free-form answer could carry WHAT to act on
        {"ai_review_output_schema": {"type": "object", "required": ["customer"], "properties": {"customer": {"type": "string"}}}},
        # extra fields beside the boolean
        {"ai_review_output_schema": {"type": "object", "required": ["confirmed"], "properties": {"confirmed": {"type": "boolean"}, "who": {"type": "string"}}}},
        # a string, not a boolean
        {"ai_review_output_schema": {"type": "object", "required": ["confirmed"], "properties": {"confirmed": {"type": "string"}}}},
        # a bare boolean lets "no" through as a valid answer, so the run would carry on
        {"ai_review_output_schema": {"type": "object", "required": ["confirmed"], "properties": {"confirmed": {"type": "boolean"}}}},
        # "no" must end the run, not be swallowed
        {"ai_review_on_failure": "continue"},
        {"ai_review_on_failure": "use_default", "ai_review_default_value": {"confirmed": True}},
        # no schema at all
        {"ai_review_output_schema": None},
    ],
)
def test_anything_wider_than_confirm_only_is_still_refused(over):
    with pytest.raises(ValueError, match="destructive_step_cannot_directly_follow_ai_review"):
        validate_editor_patch(_destructive_click_step(), {"intent": "Send invoice"}, {}, previous_step=_confirm_review(**over))


def test_destructive_step_after_a_non_ai_review_step_is_allowed():
    step = _destructive_click_step()
    previous = {"action": {"action": "click"}, "intent": "Open the record"}
    validate_editor_patch(step, {"intent": "Delete the record"}, {}, previous_step=previous)


def test_destructive_step_with_no_previous_step_is_allowed():
    # First step in the workflow — nothing to check the lint against.
    step = _destructive_click_step()
    validate_editor_patch(step, {"intent": "Delete the record"}, {})


# ─────────────────────────────────────────────────
# PROD-3 — irreversible_step_requires_confirmed_entity_binding
# ─────────────────────────────────────────────────

def _bound_destructive_click_step(*, confirmed: bool) -> dict:
    step = _destructive_click_step()
    step["entity_binding"] = {
        "container_selector": "table#invoices tr",
        "identifier": "Invoice #12345",
        "source": "literal",
        "confirmed": confirmed,
    }
    return step


def test_unconfirmed_entity_binding_on_destructive_step_raises():
    step = _bound_destructive_click_step(confirmed=False)
    with pytest.raises(ValueError, match="irreversible_step_requires_confirmed_entity_binding"):
        validate_editor_patch(step, {"intent": "Delete the record"}, {})


def test_confirmed_entity_binding_on_destructive_step_is_allowed():
    step = _bound_destructive_click_step(confirmed=True)
    validate_editor_patch(step, {"intent": "Delete the record"}, {})


def test_destructive_step_with_no_detected_container_needs_no_binding():
    # No repeating ancestor was ever detected — nothing to confirm, existing invariants
    # (anchors/wait_for) are the only gate.
    step = _destructive_click_step()
    validate_editor_patch(step, {"intent": "Delete the record"}, {})


def test_a_patch_can_confirm_an_existing_unconfirmed_binding():
    step = _bound_destructive_click_step(confirmed=False)
    validate_editor_patch(step, {"entity_binding": {"confirmed": True}}, {})


def test_editing_validation_on_a_legacy_step_to_add_a_required_check_is_allowed():
    step = _fill_step(required_assertion=None)
    patch = {"validation": {"assertions": [{"type": "value_equals", "target": "#email", "expected": "x", "required": True}]}}
    validate_editor_patch(step, patch, {})


def test_clearing_assertions_on_a_consequential_click_raises():
    step = _submit_click_step(required_assertion=True)
    with pytest.raises(ValueError, match="consequential_step_requires_required_assertion"):
        validate_editor_patch(step, {"validation": {"assertions": []}}, {})


def test_non_consequential_click_is_never_gated():
    step = {
        "action": {"action": "click"},
        "intent": "Expand the row",
        "target": {"primary_selector": ".row-toggle", "fallback_selectors": []},
        "signals": {},
        "validation": {"wait_for": {"type": "none"}, "assertions": []},
    }
    # No commit/destructive signal at all — clearing (already-empty) assertions is a no-op.
    validate_editor_patch(step, {"validation": {"assertions": []}}, {})


# ─────────────────────────────────────────────────
# Branch-step authoring (EXEC-1 editor gap, closed 2026-07-10) — if_present/try_dismiss/
# wait_for_one_of patches, and the recovery/validation restriction on nested branch-body steps.
# ─────────────────────────────────────────────────

def _if_present_step(*, branch=None) -> dict:
    return {
        "action": {"action": "if_present"},
        "intent": "dismiss_if_present",
        "target": {"primary_selector": ".cookie-banner", "fallback_selectors": []},
        "signals": {},
        "validation": {"wait_for": {"type": "none"}, "assertions": []},
        "recovery": {"strategies": [], "max_attempts": 0, "confidence_threshold": 0.85, "require_diverse_attempts": False, "anchors": []},
        "branch": branch if branch is not None else {"steps": [], "timeout_ms": 3000},
    }


def _try_dismiss_step(*, branch=None) -> dict:
    return {
        "action": {"action": "try_dismiss"},
        "intent": "try_dismiss_interstitial",
        "target": {"primary_selector": "", "fallback_selectors": []},
        "signals": {},
        "validation": {"wait_for": {"type": "none"}, "assertions": []},
        "recovery": {"strategies": [], "max_attempts": 0, "confidence_threshold": 0.85, "require_diverse_attempts": False, "anchors": []},
        "branch": branch if branch is not None else {"candidates": [], "timeout_ms": 3000, "fallback_escape": True},
    }


def _wait_for_one_of_step(*, branch=None) -> dict:
    return {
        "action": {"action": "wait_for_one_of"},
        "intent": "wait_for_one_of_states",
        "target": {"primary_selector": "", "fallback_selectors": []},
        "signals": {},
        "validation": {"wait_for": {"type": "none"}, "assertions": []},
        "recovery": {"strategies": [], "max_attempts": 0, "confidence_threshold": 0.85, "require_diverse_attempts": False, "anchors": []},
        "branch": branch if branch is not None else {"options": [], "timeout_ms": 5000, "required": True},
    }


class TestBranchStepPatches:
    def test_if_present_timeout_ms_patch_is_allowed(self):
        step = _if_present_step()
        validate_editor_patch(step, {"branch": {"timeout_ms": 4000}}, {})

    def test_if_present_out_of_range_timeout_ms_raises(self):
        step = _if_present_step()
        with pytest.raises(ValueError, match="branch_timeout_ms_out_of_range"):
            validate_editor_patch(step, {"branch": {"timeout_ms": 999999}}, {})

    def test_if_present_steps_key_rejected_on_parent_patch(self):
        """Nested body steps are structural (insert_branch_step/delete_branch_step/
        reorder_branch_steps) or path-addressed patches, never a blind deep-merge into the
        parent's own patch — otherwise a stale client could clobber concurrent nested edits."""
        step = _if_present_step()
        patch = {"branch": {"steps": [{"action": {"action": "click"}, "target": {"primary_selector": "#x"}}]}}
        with pytest.raises(ValueError, match="branch_if_present_steps_not_patchable_here"):
            validate_editor_patch(step, patch, {})

    def test_try_dismiss_valid_candidate_is_allowed(self):
        step = _try_dismiss_step()
        validate_editor_patch(step, {"branch": {"candidates": ["text=Dismiss"]}}, {})

    def test_try_dismiss_low_quality_candidate_raises(self):
        step = _try_dismiss_step()
        with pytest.raises(ValueError, match="branch_candidate_0_failed_quality_gates"):
            validate_editor_patch(step, {"branch": {"candidates": ["div"]}}, {})

    def test_try_dismiss_fallback_escape_must_be_boolean(self):
        step = _try_dismiss_step()
        with pytest.raises(ValueError, match="branch_fallback_escape_must_be_boolean"):
            validate_editor_patch(step, {"branch": {"fallback_escape": "yes"}}, {})

    def test_wait_for_one_of_option_requires_selector(self):
        step = _wait_for_one_of_step()
        with pytest.raises(ValueError, match="branch_option_0_selector_required"):
            validate_editor_patch(step, {"branch": {"options": [{"selector": ""}]}}, {})

    def test_wait_for_one_of_option_low_quality_selector_raises(self):
        step = _wait_for_one_of_step()
        with pytest.raises(ValueError, match="branch_option_0_selector_failed_quality_gates"):
            validate_editor_patch(step, {"branch": {"options": [{"selector": "div"}]}}, {})

    def test_wait_for_one_of_option_with_nested_steps_rejected(self):
        """Per-option nested bodies are read-only this pass (see StepEditorDTO.branch_summary's
        `options` in workflow_dto.py) — a patch smuggling `steps` content in is rejected rather
        than silently accepted and never rendered."""
        step = _wait_for_one_of_step()
        patch = {"branch": {"options": [{"selector": "#dashboard", "steps": [{"action": {"action": "click"}}]}]}}
        with pytest.raises(ValueError, match="branch_option_0_steps_not_patchable_here"):
            validate_editor_patch(step, patch, {})

    def test_wait_for_one_of_valid_options_patch_is_allowed(self):
        step = _wait_for_one_of_step()
        validate_editor_patch(step, {"branch": {"options": [{"selector": "#dashboard"}], "required": False}}, {})

    def test_branch_step_disallows_unrelated_keys(self):
        step = _if_present_step()
        with pytest.raises(ValueError, match="branch_step_allows_only_target_branch_intent_validation_recovery_frame"):
            validate_editor_patch(step, {"check_kind": "url"}, {})


class TestBranchBodyStepValidation:
    """Nested body steps (if_present's branch.steps[j]) are patched via cmd_patch_step's `path`
    parameter, which passes in_branch_body=True — see handlers/workflow_editor.py."""

    def _nested_click_step(self) -> dict:
        return {
            "action": {"action": "click"},
            "intent": "click_accept",
            "target": {"primary_selector": "text=Accept", "fallback_selectors": []},
            "signals": {},
        }

    def test_recovery_patch_on_branch_body_step_raises(self):
        step = self._nested_click_step()
        with pytest.raises(ValueError, match="branch_body_step_cannot_patch_recovery_or_validation"):
            validate_editor_patch(step, {"recovery": {"max_attempts": 3}}, {}, in_branch_body=True)

    def test_validation_patch_on_branch_body_step_raises(self):
        step = self._nested_click_step()
        with pytest.raises(ValueError, match="branch_body_step_cannot_patch_recovery_or_validation"):
            validate_editor_patch(step, {"validation": {"assertions": []}}, {}, in_branch_body=True)

    def test_non_recovery_patch_on_branch_body_step_is_allowed(self):
        step = self._nested_click_step()
        validate_editor_patch(step, {"intent": "click_accept_button"}, {}, in_branch_body=True)

    def test_same_patch_allowed_outside_branch_body(self):
        """The restriction is specific to in_branch_body=True — an ordinary top-level step keeps
        full recovery/validation editability."""
        step = self._nested_click_step()
        validate_editor_patch(step, {"recovery": {"max_attempts": 3}}, {}, in_branch_body=False)


def test_semantic_description_patch_is_accepted_for_every_action_shape():
    """BUILD-21: the Human Edit step form edits the workflow-intent graph's prose via a
    `semantic_description` patch key. It must pass the per-action allowlists (navigate,
    scroll, wait/screenshot, check/assert, branch) and persist via the real save path
    (compiler/patch._apply_top_level_step_fields) without touching the machine token."""
    from conxa_compile.compiler.patch import _apply_top_level_step_fields

    cases = [
        ({"action": {"action": "navigate", "url": "https://x.test/a"}, "intent": "go_to_dashboard", "url": "https://x.test/a"}, {"semantic_description": "Open the dashboard page."}),
        ({"action": {"action": "scroll"}, "intent": "scroll_viewport"}, {"semantic_description": "Scroll down to load more results.", "action": {"action": "scroll", "delta": 300}}),
        ({"action": {"action": "wait"}, "intent": "wait_short", "value": 1000}, {"semantic_description": "Wait for the page to settle."}),
        ({"action": {"action": "check"}, "intent": "check_state"}, {"semantic_description": "Confirm the banner is gone."}),
        ({"action": {"action": "click"}, "intent": "click_accept", "target": {"primary_selector": "text=Accept", "fallback_selectors": []}, "signals": {}}, {"semantic_description": "Accept the cookie consent banner."}),
        ({"action": {"action": "try_dismiss"}, "intent": "try_dismiss_interstitial", "target": {"primary_selector": "#ad", "fallback_selectors": []}, "signals": {}, "recovery": {}}, {"semantic_description": "Close the ad if it appears."}),
    ]
    for step, patch in cases:
        # Gate: prose must pass every per-action allowlist.
        validate_editor_patch(step, dict(patch), {})
        # Persistence: the real save path stores it and leaves the token untouched.
        merged = dict(step)
        _apply_top_level_step_fields(merged, dict(patch), sanitize_intent=False)
        assert merged["semantic_description"] == patch["semantic_description"]
        assert merged["intent"] == step["intent"]


def test_semantic_description_patch_rejects_empty():
    step = {
        "action": {"action": "click"},
        "intent": "click_accept",
        "target": {"primary_selector": "text=Accept", "fallback_selectors": []},
        "signals": {},
    }
    with pytest.raises(ValueError, match="semantic_description_empty"):
        validate_editor_patch(step, {"semantic_description": "   "}, {})


# BUILD-55: a "wait for this element" insert of a branch kind must not be refused for carrying a root
# identity_bundle; the element rides in the branch body instead.
@pytest.mark.parametrize("kind", ["if_present", "try_dismiss", "wait_for_one_of"])
def test_an_inserted_branch_step_is_pointed_at_its_source_element_without_being_refused(kind):
    from conxa_compile.editor.copilot_proposals import identity_patch_from_source
    from conxa_compile.editor.workflow_mutations import _new_manual_step
    from conxa_compile.policy.bundle import get_policy_bundle

    source = {"target": {"primary_selector": "internal:role=link[name=\"report.pdf\"i]", "fallback_selectors": []},
              "identity_bundle": {"signals": [{"selector": "internal:role=link[name=\"report.pdf\"i]"}]}}
    patch = identity_patch_from_source(kind, source)
    assert "identity_bundle" not in patch
    step = _new_manual_step(kind, "")
    validate_editor_patch(step, patch, get_policy_bundle().data)
    if kind == "wait_for_one_of":
        assert patch["branch"]["options"][0]["identity_bundle"] == source["identity_bundle"]


def test_a_click_insert_still_copies_the_full_identity():
    from conxa_compile.editor.copilot_proposals import identity_patch_from_source

    src = {"target": {"primary_selector": "x"}, "identity_bundle": {"signals": []}}
    assert identity_patch_from_source("click", src) == {"identity_bundle": src["identity_bundle"], "target": src["target"]}
