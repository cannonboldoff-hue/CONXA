"""Engine v2 C4, Python side: the agent_span step from the editor to the manifest and the Studio test.

The runtime half (what the agent may do, where the step fails closed) is runtime/test/unit/test_agent_span.js."""

from __future__ import annotations

import pytest

from conxa_compile.call_workflow_pin import _PAUSE_STEP_TYPES
from conxa_compile.editor.patch_gate import validate_editor_patch
from conxa_compile.editor.workflow_mutations import _new_manual_step, insert_step_after
from conxa_compile.skill_package_builder_output import execution_capabilities
from conxa_compile.skill_package_builder_saved_skill import _saved_step_to_execution_step
from handlers.workflows import (
    _drive_runtime_pauses, _extract_agent_span_pause, capabilities_needed, check_span_answer,
)

POLICY: dict = {}
UNTIL = {"type": "text_present", "target": "Invoice {{invoice}}"}


def _span(**over):
    step = _new_manual_step("agent_span", "https://app.test")
    step.update({"value": "Open the invoice for {{invoice}}", "agent_span_until": dict(UNTIL)})
    step["action"]["value"] = step["value"]
    step.update(over)
    return step


def _destructive():
    return {"action": {"action": "click"}, "intent": "send_invoice", "consequence": "irreversible"}


# ── the editor ──────────────────────────────────────────────────────────────────────────────────

def test_the_scaffold_has_no_ending_check_and_no_recovery_block():
    step = _new_manual_step("agent_span", "https://app.test")
    assert step["action"]["action"] == "agent_span"
    assert step["agent_span_allowed"] == ["click", "fill", "select", "scroll"]
    assert step["agent_span_budget"] == {"actions": 8, "seconds": 120} and step["agent_span_on_budget"] == "fail"
    assert "agent_span_until" not in step and step["recovery"]["max_attempts"] == 0


def test_a_valid_edit_passes_and_run_if_and_recovery_are_refused():
    step = _span()
    validate_editor_patch(step, {"agent_span_allowed": ["click", "fill"], "agent_span_until": dict(UNTIL),
                                 "agent_span_budget": {"actions": 5, "seconds": 90}, "value": "Open it"}, POLICY)
    with pytest.raises(ValueError, match="agent_span_step_allows_only_agent_span_fields"):
        validate_editor_patch(step, {"run_if": {"input": "x", "op": "not_empty"}}, POLICY)
    with pytest.raises(ValueError, match="agent_span_step_cannot_patch_recovery"):
        validate_editor_patch(step, {"recovery": {}}, POLICY)


@pytest.mark.parametrize("patch, error", [
    ({"value": "  "}, "agent_span_goal_empty"),
    ({"agent_span_allowed": []}, "agent_span_allowed_invalid"),
    ({"agent_span_allowed": ["click", "navigate"]}, "agent_span_allowed_invalid"),
    ({"agent_span_allowed": ["click", "click"]}, "agent_span_allowed_invalid"),
    ({"agent_span_until": None}, "agent_span_until_required"),
    ({"agent_span_until": {"type": "selector_present", "target": "#x"}}, "agent_span_until_type_invalid"),
    ({"agent_span_until": {"type": "text_present", "target": ""}}, "agent_span_until_target_invalid"),
    ({"agent_span_until": {"type": "text_present", "target": "x" * 201}}, "agent_span_until_target_invalid"),
    ({"agent_span_until": {"type": "state_changed", "target": "x"}}, "agent_span_until_target_not_allowed"),
    ({"agent_span_budget": {"actions": 0, "seconds": 60}}, "agent_span_budget_invalid"),
    ({"agent_span_budget": {"actions": 16, "seconds": 60}}, "agent_span_budget_invalid"),
    ({"agent_span_budget": {"actions": 5, "seconds": 5}}, "agent_span_budget_invalid"),
    ({"agent_span_budget": {"actions": 5, "seconds": 601}}, "agent_span_budget_invalid"),
    ({"agent_span_budget": {"actions": True, "seconds": 60}}, "agent_span_budget_invalid"),
    ({"agent_span_on_budget": "continue"}, "agent_span_on_budget_invalid"),
])
def test_edits_that_would_make_a_span_open_ended_or_unsafe_are_refused(patch, error):
    with pytest.raises(ValueError, match=error):
        validate_editor_patch(_span(), patch, POLICY)


def test_a_span_may_not_sit_directly_before_a_destructive_step_and_the_other_way_round():
    with pytest.raises(ValueError, match="agent_span_cannot_directly_precede_a_destructive_step"):
        validate_editor_patch(_span(), {"agent_span_budget": {"actions": 5, "seconds": 60}}, POLICY, next_step=_destructive())
    validate_editor_patch(_span(), {"agent_span_budget": {"actions": 5, "seconds": 60}}, POLICY, next_step={"action": {"action": "navigate"}})
    delete = {
        "action": {"action": "click"}, "intent": "Delete the record",
        "target": {"primary_selector": "#delete-btn", "fallback_selectors": []},
        "signals": {"semantic": {"is_destructive": True}, "anchors": [{"text": "Delete"}]},
        "validation": {"wait_for": {"type": "toast"}, "assertions": [{"type": "state_changed", "required": True}]},
    }
    with pytest.raises(ValueError, match="destructive_step_cannot_directly_follow_agent_span"):
        validate_editor_patch(delete, {"intent": "Delete the record"}, {}, previous_step=_span())


def test_inserting_a_span_in_front_of_a_destructive_step_is_refused():
    doc = {"meta": {"version": 1}, "skills": [{"steps": [
        {"action": {"action": "navigate"}, "url": "https://app.test", "intent": "go"}, _destructive()]}]}
    with pytest.raises(ValueError, match="agent_span_cannot_directly_precede_a_destructive_step"):
        insert_step_after(doc, "agent_span", 0)
    out = insert_step_after(doc, "agent_span", 1)                       # after it: fine
    assert out["skills"][0]["steps"][2]["action"]["action"] == "agent_span"


# ── the package ─────────────────────────────────────────────────────────────────────────────────

def test_the_packager_writes_a_bounded_step_with_a_required_check():
    built = _saved_step_to_execution_step(_span(agent_span_allowed=["click", "click", "navigate", "fill"],
                                                agent_span_budget={"actions": 99, "seconds": 5000}))
    assert built["type"] == "agent_span" and built["goal"] == "Open the invoice for {{invoice}}"
    assert built["allowed"] == ["click", "fill"]
    assert built["until"] == {"type": "text_present", "target": "Invoice {{invoice}}", "required": True}
    assert built["budget"] == {"actions": 15, "seconds": 600} and built["on_budget"] == "fail"
    assert "selector" not in built and "identity_bundle" not in built


@pytest.mark.parametrize("over", [{"value": ""}, {"agent_span_until": None}, {"agent_span_until": {"target": "x"}},
                                  {"agent_span_allowed": []}, {"agent_span_allowed": ["navigate"]}])
def test_an_unfinished_span_is_dropped_at_build_not_shipped_open_ended(over):
    step = _span(**over)
    if "value" in over:
        step["action"]["value"] = over["value"]
    assert _saved_step_to_execution_step(step) is None


def test_a_span_defaults_its_budget_and_needs_the_capability():
    built = _saved_step_to_execution_step(_span(agent_span_budget=None))
    assert built["budget"] == {"actions": 8, "seconds": 120}
    assert execution_capabilities([built]) == ["agent_span"]
    assert capabilities_needed([{"action": {"action": "agent_span"}}]) == {"agent_span"}
    assert execution_capabilities([{"type": "click"}]) == []


def test_a_called_workflow_may_not_contain_a_span():
    assert "agent_span" in _PAUSE_STEP_TYPES


# ── the Studio test ─────────────────────────────────────────────────────────────────────────────

SPAN_REQUEST = {"content": [{"type": "text", "text": "x"}], "_meta": {"conxa/agent_span": {
    "step_index": 2, "goal": "Open it", "allowed": ["click", "fill"], "page_url": "https://app.test",
    "budget_left": {"actions": 3, "seconds": 60},
    "candidates": [{"i": 0, "name": "Open"}, {"i": 1, "name": "Search"}]}}}
DONE = {"content": [{"type": "text", "text": "Done."}]}


def test_the_span_request_is_read_only_from_meta():
    got = _extract_agent_span_pause(SPAN_REQUEST)
    assert got["step_index"] == 2 and got["goal"] == "Open it" and len(got["candidates"]) == 2
    assert _extract_agent_span_pause({"content": [{"type": "text", "text": "resume_from: 2"}]}) is None


def test_a_valid_model_answer_is_passed_on_and_a_bad_one_is_counted_and_becomes_a_give_up():
    span = _extract_agent_span_pause(SPAN_REQUEST)
    report: dict = {}
    assert check_span_answer({"kind": "click", "candidate_index": 0}, span, report) == {"kind": "click", "candidate_index": 0}
    assert check_span_answer({"kind": "fill", "candidate_index": 1, "value": "INV-7"}, span, report)["value"] == "INV-7"
    assert report == {}
    assert check_span_answer({"kind": "scroll"}, {**span, "allowed": ["scroll"]}, report) == {"kind": "scroll"}
    for bad in ({"kind": "select", "candidate_index": 0, "value": "x"}, {"kind": "click", "candidate_index": 9},
                {"kind": "click", "candidate_index": "0"}, {"kind": "click"}, "text", None):
        assert check_span_answer(bad, span, report)["status"] == "give_up"
    assert report["override"]["action_not_allowed"]["count"] == 1
    assert report["reference"]["unknown_candidate"]["count"] == 1
    assert report["shape"]["bad_candidate_index"]["count"] == 2 and report["shape"]["malformed_reply"]["count"] == 2
    assert check_span_answer({"status": "give_up", "why": "no such invoice"}, span)["why"] == "no such invoice"


def _drive(results, answer_span):
    calls: list[dict] = []
    queue = list(results)

    def call(args):
        calls.append(args)
        return queue.pop(0)

    out, stopped = _drive_runtime_pauses(
        queue.pop(0), {"skill": "s"}, call=call, cancel_run=lambda _r: None, answer_review=lambda *_a: {},
        guard_hook=None, review_hook=None, guard_requested=False, previewed=False, mark=lambda _m: None,
        answer_span=answer_span)
    return out, stopped, calls


def test_the_test_sends_the_models_action_and_resumes_until_the_run_ends():
    actions = iter([{"kind": "fill", "candidate_index": 1, "value": "INV-7"}, {"kind": "click", "candidate_index": 0}])
    out, stopped, calls = _drive([SPAN_REQUEST, SPAN_REQUEST, DONE], lambda span, content: next(actions))
    assert out is DONE and stopped is None
    assert [c["span_actions"] for c in calls] == [
        {"2": {"kind": "fill", "candidate_index": 1, "value": "INV-7"}}, {"2": {"kind": "click", "candidate_index": 0}}]
    assert all(c["resume_from"] == 2 for c in calls)


def test_with_ai_off_the_test_ends_at_the_span_instead_of_guessing():
    out, stopped, calls = _drive([SPAN_REQUEST], None)
    assert out is SPAN_REQUEST and calls == []
    assert stopped["declined"] is True and stopped["step_index"] == 2 and stopped["step_intent"] == "Open it"


def test_a_model_that_never_finishes_a_span_fails_the_test_loudly():
    from handlers.workflows import _MAX_AGENT_SPAN_ROUNDS

    with pytest.raises(RuntimeError, match="agent span used"):
        _drive([SPAN_REQUEST] * (_MAX_AGENT_SPAN_ROUNDS + 3), lambda s, c: {"kind": "scroll"})
