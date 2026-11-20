"""A free-form agent-span answer may not gate a destructive step; attach names are validated; the editor
scaffold, the DTO and the build carry a question span end to end. (This replaces the old ai_review tests.)"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from conxa_compile.editor.action_registry import default_action_value
from conxa_compile.editor.patch_gate import CONFIRM_ONLY_SPAN_SCHEMA, validate_editor_patch
from conxa_compile.editor.workflow_dto import step_to_dto
from conxa_compile.editor.workflow_mutations import _new_manual_step, insert_step_after
from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill

POLICY: dict = {}


def _free_question(output_name="verdict"):
    return {
        "action": {"action": "agent_span"}, "intent": "decide", "value": "Is it a duplicate?",
        "agent_span_allowed": [], "agent_span_output_name": output_name,
        "agent_span_answer_schema": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}},
        "agent_span_on_failure": "abort",
    }


def _confirm_question():
    return {
        "action": {"action": "agent_span"}, "intent": "confirm", "value": "Send?", "agent_span_allowed": [],
        "agent_span_output_name": "verdict", "agent_span_answer_schema": dict(CONFIRM_ONLY_SPAN_SCHEMA),
        "agent_span_on_failure": "abort",
    }


def _destructive():
    return {"action": {"action": "click"}, "intent": "send_invoice", "consequence": "irreversible"}


# ── what may gate what ──────────────────────────────────────────────────────────────────────────

def test_destructive_step_cannot_be_gated_by_a_free_form_span_answer():
    patch = {"run_if": {"input": "verdict_answer", "op": "equals", "value": "yes"}}
    with pytest.raises(ValueError, match="destructive_step_cannot_be_gated_by_span_answer"):
        validate_editor_patch(_destructive(), patch, POLICY, earlier_steps=[_free_question()])


def test_a_safe_step_may_be_gated_by_a_span_answer():
    step = {"action": {"action": "navigate"}, "intent": "go", "url": "https://example.com"}
    patch = {"run_if": {"input": "verdict_answer", "op": "equals", "value": "yes"}}
    validate_editor_patch(step, patch, POLICY, earlier_steps=[_free_question()])


def test_a_confirm_only_span_may_gate_anything():
    step = {"action": {"action": "navigate"}, "intent": "go", "url": "https://example.com"}
    patch = {"run_if": {"input": "verdict_confirmed", "op": "equals", "value": "true"}}
    validate_editor_patch(step, patch, POLICY, earlier_steps=[_confirm_question()])
    validate_editor_patch(_destructive(), patch, POLICY, earlier_steps=[_confirm_question()])


def test_run_if_on_an_unrelated_input_is_not_affected():
    patch = {"run_if": {"input": "coupon", "op": "not_empty"}}
    # coupon is not produced by the span, so the lint stays out of the way
    validate_editor_patch(_destructive(), patch, POLICY, earlier_steps=[_free_question()])


def test_an_acting_span_with_no_answer_never_gates_a_step_by_name():
    acting = {**_free_question(), "agent_span_allowed": ["click"], "agent_span_answer_schema": None}
    patch = {"run_if": {"input": "verdict_answer", "op": "equals", "value": "yes"}}
    validate_editor_patch(_destructive(), patch, POLICY, earlier_steps=[acting])


def test_span_attach_accepts_input_names_only():
    span = _free_question()
    validate_editor_patch(span, {"agent_span_attach": ["downloaded_file"]}, POLICY)
    validate_editor_patch(span, {"agent_span_attach": []}, POLICY)
    for bad in ("downloaded_file", ["../etc/passwd"], ["a"] * 6, [1]):
        with pytest.raises(ValueError, match="agent_span_attach_invalid"):
            validate_editor_patch(span, {"agent_span_attach": bad}, POLICY)


# ── the editor scaffold ─────────────────────────────────────────────────────────────────────────

def test_new_manual_step_scaffolds_a_loadable_span():
    step = _new_manual_step("agent_span", "https://x.test")
    assert step["action"]["action"] == "agent_span"
    assert step["intent"] == "agent_span_goal"
    # The goal is blank on purpose: the packager drops a span with no goal, with a warning.
    assert step["value"] == default_action_value("agent_span") == ""   # WF-13: no placeholder shipped as the goal
    assert "agent_span_answer_schema" not in step


def test_agent_span_is_insertable_into_a_document():
    doc = {"skills": [{"steps": [{"action": {"action": "navigate"}, "intent": "go", "url": "https://x.test"}]}]}
    steps = insert_step_after(doc, "agent_span", 0)["skills"][0]["steps"]
    assert len(steps) == 2 and steps[1]["action"]["action"] == "agent_span"


def test_step_to_dto_surfaces_the_span_answer_fields():
    step = {
        "action": {"action": "agent_span", "value": "Is there an error banner on this page?"},
        "intent": "agent_span_goal",
        "value": "Is there an error banner on this page?",
        "target": {"primary_selector": "", "fallback_selectors": []},
        "signals": {"selectors": {}, "semantic": {}},
        "validation": {"wait_for": {"type": "none"}, "success_conditions": {}},
        "agent_span_allowed": [],
        "agent_span_answer_schema": {"type": "object", "required": ["answer"]},
        "agent_span_output_name": "error_present",
        "agent_span_on_failure": "use_default",
        "agent_span_default_value": {"answer": "no"},
        "agent_span_attach": ["downloaded_file"],
    }
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.agent_span_answer_schema == {"type": "object", "required": ["answer"]}
    assert dto.agent_span_output_name == "error_present"
    assert dto.agent_span_on_failure == "use_default"
    assert dto.agent_span_default_value == {"answer": "no"}
    assert dto.agent_span_attach == ["downloaded_file"]


def test_step_to_dto_span_fields_are_none_for_other_step_kinds():
    step = {
        "action": {"action": "fill", "value": "alice@example.com"}, "intent": "Enter email", "value": "alice@example.com",
        "target": {"primary_selector": "#email", "fallback_selectors": []},
        "signals": {"selectors": {}, "semantic": {}},
        "validation": {"wait_for": {"type": "none"}, "success_conditions": {}},
    }
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.agent_span_answer_schema is None and dto.agent_span_output_name is None
    assert dto.agent_span_on_failure is None and dto.agent_span_default_value is None and dto.agent_span_attach is None


# ── scaffold through to the package ─────────────────────────────────────────────────────────────

def test_scaffold_through_to_compiled_execution_json():
    step = _new_manual_step("agent_span", "https://x.test")
    step["value"] = "Open the newest invoice and tell me its number."
    step["action"]["value"] = step["value"]
    step["agent_span_allowed"] = ["click", "navigate"]
    step["agent_span_answer_schema"] = {"type": "object", "required": ["number"], "properties": {"number": {"type": "string"}}}
    step["agent_span_output_name"] = "invoice_number"
    step["agent_span_on_failure"] = "use_default"
    step["agent_span_default_value"] = {"number": ""}

    saved_skill = {"meta": {"id": "skill_x", "title": "Span E2E"}, "inputs": [], "skills": [{"steps": [step]}]}
    with tempfile.TemporaryDirectory() as td:
        _build_workflow_from_saved_skill(bundle_root=Path(td), workflow_slug="span_e2e", saved_skill=saved_skill)
        execution = json.loads((Path(td) / "skills" / "span_e2e" / "execution.json").read_text())

    assert execution[0].pop("source_step_key")        # the document step this came from (maps a test result back)
    assert execution == [{
        "type": "agent_span",
        "goal": "Open the newest invoice and tell me its number.",
        "allowed": ["click", "navigate"],
        "budget": {"actions": 25, "seconds": 600},
        "on_budget": "fail",
        "answer": {
            "schema": {"type": "object", "required": ["number"], "properties": {"number": {"type": "string"}}},
            "on_failure": "use_default",
            "output_name": "invoice_number",
            "default_value": {"number": ""},
        },
    }]
