"""clipboard_write / clipboard_read: the editor gate, saved-step conversion, produced names and capability."""
import pytest

from conxa_compile.editor.action_registry import INSERTABLE_ACTIONS, is_supported_action
from conxa_compile.editor.patch_gate import validate_editor_patch
from conxa_compile.editor.workflow_mutations import _new_manual_step
from conxa_compile.skill_package_builder_output import execution_capabilities
from conxa_compile.skill_package_builder_saved_skill import (
    _merge_saved_inputs_with_execution_placeholders,
    _produced_names,
    _saved_step_to_execution_step,
)


def _step(kind, value):
    return {"action": {"action": kind}, "intent": "x", "value": value}


def test_both_are_known_insertable_actions_with_scaffolds():
    for kind in ("clipboard_write", "clipboard_read"):
        assert is_supported_action(kind) and kind in INSERTABLE_ACTIONS
        scaffold = _new_manual_step(kind, "")
        validate_editor_patch(scaffold, {"intent": "copy_text"}, {})


def test_gate_accepts_valid_edits_and_run_if():
    validate_editor_patch(_step("clipboard_write", "Order {{id}}"), {"value": "Other"}, {})
    validate_editor_patch(_step("clipboard_read", "pasted"), {"value": "pasted_text"}, {})
    validate_editor_patch(_step("clipboard_read", "pasted"), {"run_if": {"input": "x", "op": "not_empty"}}, {})


@pytest.mark.parametrize("kind,patch,message", [
    ("clipboard_write", {"value": "  "}, "clipboard_write_value_empty"),
    ("clipboard_read", {"value": ""}, "clipboard_read_value_empty"),
    ("clipboard_read", {"value": "bad name"}, "clipboard_read_name_invalid"),
    ("clipboard_read", {"value": "1abc"}, "clipboard_read_name_invalid"),
    ("clipboard_write", {"recovery": {}}, "clipboard_write_step_cannot_patch_recovery"),
    ("clipboard_write", {"target": {}}, "clipboard_write_step_allows_only_value_and_description"),
])
def test_gate_rejects_bad_edits(kind, patch, message):
    with pytest.raises(ValueError, match=message):
        validate_editor_patch(_step(kind, "ok_value"), patch, {})


def test_saved_steps_become_runtime_steps():
    assert _saved_step_to_execution_step(_step("clipboard_write", "Hi {{name}}")) == {"type": "clipboard_write", "value": "Hi {{name}}"}
    assert _saved_step_to_execution_step(_step("clipboard_read", " pasted ")) == {"type": "clipboard_read", "output_name": "pasted"}


def test_unfinished_steps_are_dropped_not_exported():
    assert _saved_step_to_execution_step(_step("clipboard_write", "  ")) is None
    assert _saved_step_to_execution_step(_step("clipboard_read", "bad name")) is None


def test_produced_names_are_never_asked_of_the_caller():
    steps = [
        {"type": "extract", "output_name": "ticket"},
        {"type": "clipboard_read", "output_name": "pasted"},
        {"type": "ai_review", "output_name": "verdict", "output_schema": {"properties": {"choice": {}, "why": {}}}},
        {"type": "fill", "value": "{{ticket}} {{pasted}} {{verdict_choice}} {{customer}}"},
    ]
    assert _produced_names(steps) == {"ticket", "pasted", "verdict", "verdict_choice", "verdict_why"}
    names = [i["name"] for i in _merge_saved_inputs_with_execution_placeholders([], steps)]
    assert names == ["customer"]


def test_a_skill_with_clipboard_steps_declares_the_capability():
    assert execution_capabilities([{"type": "clipboard_read"}]) == ["clipboard"]
    assert execution_capabilities([{"type": "clipboard_write"}, {"type": "wait_until"}]) == ["clipboard", "wait_until"]
