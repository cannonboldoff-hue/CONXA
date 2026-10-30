"""call_workflow: the editor gate, saved-step conversion, and the build-time check + version pin."""
import json

import pytest

from conxa_compile.call_workflow_pin import call_steps, pin_called_workflows
from conxa_compile.editor.action_registry import INSERTABLE_ACTIONS, is_supported_action
from conxa_compile.editor.patch_gate import validate_editor_patch
from conxa_compile.editor.workflow_mutations import _new_manual_step
from conxa_compile.skill_package_builder_output import execution_capabilities
from conxa_compile.skill_package_builder_saved_skill import _produced_names, _saved_step_to_execution_step


def _step(value="invoice_lookup", **extra):
    return {"action": {"action": "call_workflow"}, "intent": "call", "value": value, **extra}


# ── editor ───────────────────────────────────────────────────────────────────

def test_known_insertable_action_with_scaffold():
    assert is_supported_action("call_workflow") and "call_workflow" in INSERTABLE_ACTIONS
    scaffold = _new_manual_step("call_workflow", "")
    assert scaffold["call_workflow_inputs"] == {} and scaffold["call_workflow_outputs"] == {}
    validate_editor_patch(scaffold, {"intent": "call_other"}, {})  # unfinished is savable; the build drops it


def test_gate_accepts_valid_edits():
    step = _step()
    validate_editor_patch(step, {"value": "other-workflow_2"}, {})
    validate_editor_patch(step, {"call_workflow_inputs": {"order_id": "{{id}}"}, "call_workflow_outputs": {"total": "order_total"}}, {})
    validate_editor_patch(step, {"run_if": {"input": "x", "op": "not_empty"}}, {})


@pytest.mark.parametrize("patch,message", [
    ({"value": "  "}, "call_workflow_value_invalid"),
    ({"value": "../etc"}, "call_workflow_value_invalid"),
    ({"call_workflow_inputs": ["a"]}, "call_workflow_inputs_invalid"),
    ({"call_workflow_inputs": {"bad key": "x"}}, "call_workflow_inputs_invalid"),
    ({"call_workflow_inputs": {"a": 1}}, "call_workflow_inputs_invalid"),
    ({"call_workflow_outputs": {"a": "not valid"}}, "call_workflow_outputs_invalid"),
    ({"recovery": {}}, "call_workflow_step_cannot_patch_recovery"),
    ({"target": {}}, "call_workflow_step_allows_only_call_fields"),
])
def test_gate_rejects_bad_edits(patch, message):
    with pytest.raises(ValueError, match=message):
        validate_editor_patch(_step(), patch, {})


def test_saved_step_becomes_a_runtime_step_without_a_version():
    out = _saved_step_to_execution_step(_step(call_workflow_inputs={"order_id": "{{id}}"}, call_workflow_outputs={"total": "t"}))
    assert out == {"type": "call_workflow", "call": {"skill": "invoice_lookup", "inputs": {"order_id": "{{id}}"}, "outputs": {"total": "t"}}}
    assert _saved_step_to_execution_step(_step())["call"] == {"skill": "invoice_lookup"}
    assert _saved_step_to_execution_step(_step(value="  ")) is None
    assert _saved_step_to_execution_step(_step(value="bad name")) is None


def test_results_a_call_brings_back_are_produced_not_asked_of_the_caller():
    steps = [{"type": "call_workflow", "call": {"skill": "b", "outputs": {"total": "order_total"}}}]
    assert _produced_names(steps) == {"order_total"}
    assert execution_capabilities(steps) == ["call_workflow"]


# ── build: check and pin ─────────────────────────────────────────────────────

def _write(path, steps, inputs=None):
    path.mkdir(parents=True, exist_ok=True)
    (path / "execution.json").write_text(json.dumps(steps), encoding="utf-8")
    if inputs is not None:
        (path / ("input.json" if "bundle" in str(path) else "inputs.json")).write_text(json.dumps({"inputs": inputs}), encoding="utf-8")


def _pin(tmp_path, caller_steps, callee_steps, callee_inputs=None, callee_built_earlier=False, version="3.0.0"):
    bundle = tmp_path / "bundle" / "skills"
    packs = tmp_path / "packs"
    dest = packs / "g" / "caller"
    _write(dest, caller_steps)
    building = {"caller": bundle / "caller"}
    if callee_built_earlier:
        _write(packs / "g" / "callee", callee_steps, callee_inputs or [])
        (packs / "g" / "callee" / "manifest.json").write_text(json.dumps({"version": "1.2.0"}), encoding="utf-8")
    else:
        _write(bundle / "callee", callee_steps, callee_inputs or [])
        building["callee"] = bundle / "callee"
    pin_called_workflows(dest_dir=dest, slug="caller", version=version, building=building, skill_packs=packs)
    return json.loads((dest / "execution.json").read_text(encoding="utf-8"))


def _call(**kw):
    return [{"type": "click"}, {"type": "call_workflow", "call": {"skill": "callee", **kw}}]


def test_pin_stamps_the_version_built_in_this_call(tmp_path):
    steps = _pin(tmp_path, _call(inputs={"id": "{{x}}"}, outputs={"total": "t"}),
                 [{"type": "extract", "output_name": "total"}], [{"name": "id"}])
    assert steps[1]["call"]["version"] == "3.0.0"


def test_pin_reads_the_version_of_a_workflow_built_earlier(tmp_path):
    steps = _pin(tmp_path, _call(), [{"type": "click"}], callee_built_earlier=True)
    assert steps[1]["call"]["version"] == "1.2.0"


def test_a_skill_without_calls_is_left_alone(tmp_path):
    steps = _pin(tmp_path, [{"type": "click"}], [{"type": "click"}])
    assert steps == [{"type": "click"}]


@pytest.mark.parametrize("call,callee_steps,inputs,message", [
    ({}, [{"type": "handover"}], [], "contains a hand-over to a person"),
    ({}, [{"type": "for_each", "steps": [{"type": "ai_review"}]}], [], "contains an AI review"),
    ({"inputs": {"nope": "1"}}, [{"type": "click"}], [{"name": "id", "optional": True}], "input\\(s\\) it does not take: nope"),
    ({}, [{"type": "click"}], [{"name": "id"}], "does not give 'callee' the input\\(s\\) it needs: id"),
    ({"outputs": {"total": "t"}}, [{"type": "click"}], [], "result\\(s\\) it does not produce: total"),
])
def test_build_refuses_a_bad_call(tmp_path, call, callee_steps, inputs, message):
    with pytest.raises(RuntimeError, match=message):
        _pin(tmp_path, _call(**call), callee_steps, inputs)


def test_a_default_or_optional_input_need_not_be_given(tmp_path):
    steps = _pin(tmp_path, _call(), [{"type": "click"}], [{"name": "a", "default": "1"}, {"name": "b", "optional": True}])
    assert steps[1]["call"]["version"] == "3.0.0"


def test_build_refuses_a_call_to_a_workflow_that_does_not_exist(tmp_path):
    dest = tmp_path / "packs" / "g" / "caller"
    _write(dest, _call())
    with pytest.raises(RuntimeError, match="calls 'callee', which has not been built yet"):
        pin_called_workflows(dest_dir=dest, slug="caller", version="1", building={"caller": tmp_path}, skill_packs=tmp_path / "packs")


def test_build_refuses_a_circle(tmp_path):
    bundle = tmp_path / "bundle" / "skills"
    packs = tmp_path / "packs"
    dest = packs / "g" / "caller"
    _write(dest, _call())
    _write(bundle / "callee", [{"type": "call_workflow", "call": {"skill": "caller", "version": "x"}}], [])
    _write(bundle / "caller", _call(), [])
    with pytest.raises(RuntimeError, match="in a circle \\(caller -> callee -> caller\\)"):
        pin_called_workflows(dest_dir=dest, slug="caller", version="1",
                             building={"caller": bundle / "caller", "callee": bundle / "callee"}, skill_packs=packs)


def test_call_steps_finds_nested_calls():
    nested = [{"type": "for_each", "for_each": {"steps": [{"type": "call_workflow", "call": {"skill": "b"}}]}}]
    assert len(call_steps(nested)) == 1
