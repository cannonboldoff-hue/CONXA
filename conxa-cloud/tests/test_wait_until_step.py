"""wait_until: the editor gate, the saved-step -> runtime-step conversion and the capability flag."""
import pytest

from conxa_compile.editor.action_registry import INSERTABLE_ACTIONS, is_supported_action
from conxa_compile.editor.capability_manifest import build_capability_manifest
from conxa_compile.editor.patch_gate import validate_editor_patch
from conxa_compile.editor.workflow_mutations import _new_manual_step
from conxa_compile.skill_package_builder_output import execution_capabilities
from conxa_compile.skill_package_builder_saved_skill import _saved_step_to_execution_step


def _step(**over):
    step = {
        "action": {"action": "wait_until"}, "intent": "wait", "value": "#done",
        "wait_until_kind": "element_visible", "wait_until_timeout_ms": 30000, "wait_until_on_timeout": "fail",
    }
    step.update(over)
    return step


def test_wait_until_is_a_known_insertable_action_with_a_scaffold():
    assert is_supported_action("wait_until") and "wait_until" in INSERTABLE_ACTIONS
    scaffold = _new_manual_step("wait_until", "")
    assert scaffold["wait_until_kind"] == "load_state" and scaffold["action"]["value"] == "networkidle"
    validate_editor_patch({**scaffold, "value": "networkidle"}, {"intent": "wait for the page"}, {})


def test_the_capability_manifest_lists_it():
    kinds = {row["kind"]: row for row in build_capability_manifest()["kinds"]}
    assert kinds["wait_until"]["insertable"] and kinds["wait_until"]["runtime_note"]


def test_gate_accepts_valid_edits():
    step = _step()
    validate_editor_patch(step, {"value": "#other"}, {})
    validate_editor_patch(step, {"wait_until_kind": "url_contains", "value": "/thanks"}, {})
    validate_editor_patch(step, {"wait_until_timeout_ms": 120000, "wait_until_on_timeout": "continue"}, {})
    validate_editor_patch(step, {"run_if": {"input": "coupon", "op": "not_empty"}}, {})


@pytest.mark.parametrize("patch,message", [
    ({"value": "  "}, "wait_until_value_empty"),
    ({"wait_until_kind": "teleport"}, "wait_until_kind_invalid"),
    ({"wait_until_kind": "load_state"}, "wait_until_load_state_invalid"),  # "#done" is not a load state
    ({"wait_until_timeout_ms": 500}, "wait_until_timeout_ms_invalid"),
    ({"wait_until_timeout_ms": 121000}, "wait_until_timeout_ms_invalid"),
    ({"wait_until_timeout_ms": True}, "wait_until_timeout_ms_invalid"),
    ({"wait_until_on_timeout": "retry"}, "wait_until_on_timeout_invalid"),
    ({"recovery": {}}, "wait_until_step_cannot_patch_recovery"),
    ({"target": {}}, "wait_until_step_allows_only_wait_until_fields"),
])
def test_gate_rejects_bad_edits(patch, message):
    with pytest.raises(ValueError, match=message):
        validate_editor_patch(_step(), patch, {})


def test_saved_step_becomes_a_runtime_step_for_each_kind():
    cases = {
        "element_visible": ("#done", {"kind": "element_visible", "selector": "#done"}),
        "element_hidden": (".spinner", {"kind": "element_hidden", "selector": ".spinner"}),
        "text_visible": ("Shipped", {"kind": "text_visible", "text": "Shipped"}),
        "url_contains": ("/thanks", {"kind": "url_contains", "url": "/thanks"}),
        "load_state": ("networkidle", {"kind": "load_state", "state": "networkidle"}),
    }
    for kind, (value, until) in cases.items():
        out = _saved_step_to_execution_step(_step(value=value, wait_until_kind=kind, wait_until_on_timeout="continue"))
        assert out["type"] == "wait_until" and out["until"] == until
        assert out["timeout_ms"] == 30000 and out["on_timeout"] == "continue"


def test_saved_step_defaults_clamps_and_drops():
    out = _saved_step_to_execution_step({"action": "wait_until", "value": "load"})
    assert out["until"] == {"kind": "load_state", "state": "load"} and out["timeout_ms"] == 30000 and out["on_timeout"] == "fail"
    assert _saved_step_to_execution_step(_step(wait_until_timeout_ms=10**9))["timeout_ms"] == 120000
    assert _saved_step_to_execution_step(_step(wait_until_timeout_ms=5))["timeout_ms"] == 1000
    assert _saved_step_to_execution_step(_step(value="  ")) is None
    assert _saved_step_to_execution_step(_step(wait_until_kind="teleport")) is None


def test_a_skill_with_wait_until_declares_the_capability():
    assert execution_capabilities([{"type": "click"}]) == []
    assert execution_capabilities([{"type": "wait_until"}]) == ["wait_until"]
