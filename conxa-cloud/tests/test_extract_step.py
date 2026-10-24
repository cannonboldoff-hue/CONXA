"""Gap analysis G4 / G5.2: a value read off the page (extract), reported as an output, and run_if."""

from __future__ import annotations

import copy
import shutil
from contextlib import ExitStack

import pytest

from conxa_compile.compiler.build import compile_skill_package
from conxa_compile.editor.patch_gate import validate_editor_patch
from conxa_compile.editor.action_registry import action_spec, manual_action_kinds
from conxa_compile.editor.workflow_mutations import _new_manual_step
from conxa_compile.pipeline.run import run_pipeline
from conxa_compile.skill_package_builder_saved_skill import _saved_step_to_execution_step
from handlers.workflows import capabilities_needed
from tests.test_second_opinion import _compile_fixture, _minimal_click_event

POLICY: dict = {}


def _extract_event(seq=1, value="", **extract):
    ev = _minimal_click_event()
    ev["action"]["action"] = "extract"
    ev["action"]["value"] = value
    ev["target"]["inner_text"] = "Order total"
    ev["extras"] = {"sequence": seq, "extract": extract}
    return ev


def _compile(events):
    evs = run_pipeline(events)
    data_dir, patchers = _compile_fixture("sess", evs)
    try:
        with ExitStack() as stack:
            for p in patchers:
                stack.enter_context(p)
            return compile_skill_package(evs, skill_id="skill_test", source_session_id="sess", title="t", version=1)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


# ── compile ─────────────────────────────────────────────────────────────────────────────────


def test_a_recorded_capture_becomes_an_extract_step_with_a_unique_output_name():
    a = _extract_event(1, source="attribute", attribute="href")
    b = copy.deepcopy(_extract_event(2, source="text", pattern="([0-9]+)"))
    b["target"]["id"] = "other"
    b["selectors"]["css"] = "#other"
    pkg = _compile([a, b])
    steps = [s for s in pkg.skills[0].steps if (s.action if isinstance(s.action, str) else s.action.get("action")) == "extract"]
    assert [s.value for s in steps] == ["order_total", "order_total_2"]
    assert steps[0].extract == {"source": "attribute", "attribute": "href"} and steps[1].extract["pattern"] == "([0-9]+)"
    assert all(s.input_binding is None and not s.validation.assertions for s in steps)
    assert steps[0].identity_bundle.signals                         # found the way any click is found


def test_an_extract_output_is_never_offered_as_a_run_input():
    from conxa_compile.editor.workflow_mutations import reconcile_inputs_with_step_values

    doc = {"skills": [{"steps": [{"action": "extract", "value": "order_total", "extract": {"source": "text"}}]}], "inputs": []}
    assert reconcile_inputs_with_step_values(doc)[0]["inputs"] == []      # a bare name is not a {{placeholder}}


# ── package ─────────────────────────────────────────────────────────────────────────────────


def _saved(**kw):
    s = {"action": "extract", "intent": "read_total", "value": "order_total", "extract": {"source": "text", "pattern": "([0-9]+)"},
         "target": {"primary_selector": "#total"}}
    s.update(kw)
    return s


def test_the_packager_emits_what_the_runtime_reads():
    out = _saved_step_to_execution_step(_saved())
    assert out["type"] == "extract" and out["output_name"] == "order_total"
    assert out["extract"] == {"source": "text", "pattern": "([0-9]+)"} and out["selector"] == "#total"


def test_an_extract_with_no_name_or_no_target_is_not_exported():
    assert _saved_step_to_execution_step(_saved(value="")) is None
    assert _saved_step_to_execution_step(_saved(value="1 bad name")) is None
    assert _saved_step_to_execution_step(_saved(target={})) is None


def test_run_if_rides_into_the_execution_step_but_only_with_an_input():
    step = {"action": "click", "intent": "tick", "target": {"primary_selector": "#x"}, "run_if": {"input": "note", "op": "not_empty", "junk": 1}}
    assert _saved_step_to_execution_step(step)["run_if"] == {"input": "note", "op": "not_empty"}
    assert "run_if" not in _saved_step_to_execution_step({**step, "run_if": {"op": "not_empty"}})


# ── the editor ──────────────────────────────────────────────────────────────────────────────


def test_extract_is_a_registered_insertable_kind_with_a_scaffold():
    spec = action_spec("extract")
    assert spec.insertable and spec.selectors and spec.value and spec.recovery and "extract" in manual_action_kinds()
    step = _new_manual_step("extract", "https://x.test")
    assert step["action"] == {"action": "extract", "value": "extracted_value"}


@pytest.mark.parametrize("patch,code", [
    ({"value": "1 bad"}, "extract_output_name_invalid"),
    ({"extract": {"source": "magic"}}, "extract_source_invalid"),
    ({"extract": {"source": "attribute"}}, "extract_attribute_required"),
    ({"extract": {"source": "text", "pattern": "("}}, "extract_pattern_invalid"),
    ({"extract": "text"}, "extract_must_be_object"),
    ({"input_binding": "x"}, "extract_step_allows_only_target_output_name_and_extract_fields"),
])
def test_the_gate_refuses_a_malformed_extract_edit(patch, code):
    step = _saved()
    step["action"] = {"action": "extract"}
    with pytest.raises(ValueError, match=code):
        validate_editor_patch(step, patch, POLICY)


def test_the_gate_accepts_a_valid_extract_edit_and_a_run_if():
    step = _saved()
    step["action"] = {"action": "extract"}
    validate_editor_patch(step, {"value": "invoice_total", "extract": {"source": "attribute", "attribute": "data-total", "pattern": "([0-9]+)"}}, POLICY)
    validate_editor_patch(step, {"run_if": {"input": "note", "op": "not_empty"}}, POLICY)
    validate_editor_patch(step, {"run_if": {}}, POLICY)                      # clears it


@pytest.mark.parametrize("run_if,code", [
    ({"input": "1x", "op": "not_empty"}, "run_if_input_invalid"),
    ({"input": "note", "op": "teleport"}, "run_if_op_invalid"),
    ({"input": "note", "op": "equals"}, "run_if_value_required"),
    ("yes", "run_if_must_be_object"),
])
def test_the_gate_refuses_a_malformed_run_if(run_if, code):
    with pytest.raises(ValueError, match=code):
        validate_editor_patch({"action": {"action": "click"}, "intent": "x", "target": {"primary_selector": "#x"}}, {"run_if": run_if}, POLICY)


def test_a_confirmation_or_hand_over_can_never_be_switched_off_by_an_input():
    """If an input could skip the ai_review that guards a destructive step, the guard would be optional."""
    for kind in ("ai_review", "handover"):
        with pytest.raises(ValueError):
            validate_editor_patch({"action": {"action": kind}, "intent": "x", "value": "answer"}, {"run_if": {"input": "note"}}, POLICY)


# ── the engine check ────────────────────────────────────────────────────────────────────────


def test_a_skill_that_reads_values_or_skips_by_input_needs_the_matching_engine_capability():
    assert capabilities_needed([{"action": "click"}]) == set()
    assert capabilities_needed([{"action": {"action": "extract"}}]) == {"outputs"}
    assert capabilities_needed([{"action": "click", "run_if": {"input": "n"}}]) == {"run_if"}
    nested = [{"action": "for_each", "for_each": {"steps": [{"action": "extract"}]}}, {"action": "if_present", "branch": {"steps": [{"action": "click", "run_if": {"input": "n"}}]}}]
    assert capabilities_needed(nested) == {"outputs", "run_if"}


# ── the recorder side ───────────────────────────────────────────────────────────────────────


def test_capture_mode_is_applied_on_the_recorders_own_thread_to_every_frame():
    from types import SimpleNamespace

    from conxa_compile.recorder.session import RecordingSession

    calls: list[tuple[str, bool]] = []
    frame = lambda name: SimpleNamespace(evaluate=lambda js, on: calls.append((name, on)))  # noqa: E731
    page = SimpleNamespace(frames=[frame("main"), frame("iframe")])
    fake = SimpleNamespace(_capture_pending=None, _context=SimpleNamespace(pages=[page]))
    RecordingSession.request_capture(fake, True)
    assert fake._capture_pending is True and calls == []          # queued, not applied from the caller's thread
    RecordingSession._apply_capture_mode_sync(fake)
    assert calls == [("main", True), ("iframe", True)] and fake._capture_pending is None
    RecordingSession._apply_capture_mode_sync(fake)                # nothing pending: no second round
    assert len(calls) == 2


def test_the_capture_command_only_works_during_a_recording(monkeypatch):
    import importlib.util
    import os
    import sys

    from handlers.protocol import _CommandError

    py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
    spec = importlib.util.spec_from_file_location("cbackend_capture", os.path.join(py, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    b = mod.Backend()
    seen: list[bool] = []
    session = SimpleNamespaceCapture(seen)
    monkeypatch.setattr(b, "_flow_workflow", lambda payload: SimpleNamespaceId("w1"))
    monkeypatch.setattr(b, "_flow_card", lambda wid, cid: {"state": "recording", "refs": {"session_id": "s"}})
    monkeypatch.setattr(b, "_flow_session", lambda card: ("s", session))
    assert b.cmd_flow_capture_value({"workflow_id": "w1", "card_id": "c"}, "r") == {"ok": True, "capturing": True}
    assert b.cmd_flow_capture_value({"workflow_id": "w1", "card_id": "c", "on": False}, "r") == {"ok": True, "capturing": False}
    assert seen == [True, False]
    monkeypatch.setattr(b, "_flow_card", lambda wid, cid: {"state": "paused", "refs": {}})
    with pytest.raises(_CommandError):
        b.cmd_flow_capture_value({"workflow_id": "w1", "card_id": "c"}, "r")


class SimpleNamespaceId:
    def __init__(self, id_):
        self.id = id_


class SimpleNamespaceCapture:
    def __init__(self, seen):
        self.seen = seen

    def request_capture(self, on):
        self.seen.append(on)


# ── the question and the recording row ──────────────────────────────────────────────────────


def test_a_captured_value_is_confirmed_once_and_removal_deletes_just_that_step():
    from conxa_compile.conversation.clarify import generate_questions, plan_answer
    from conxa_compile.recorder.plain import describe_event

    doc = {"meta": {"version": 1}, "inputs": [], "skills": [{"steps": [
        {"action": {"action": "navigate"}, "intent": "open", "url": "https://x.test", "validation": {"assertions": []}, "identity_bundle": {"stable_hash": "a"}},
        {"action": {"action": "extract"}, "intent": "read_total", "value": "order_total", "extract": {"source": "text"},
         "semantic_description": "Order total", "validation": {"assertions": []}, "identity_bundle": {"stable_hash": "b"}},
    ]}]}
    qs = [q for q in generate_questions(doc) if q["kind"] == "output"]
    assert len(qs) == 1 and qs[0]["title"] == "Report \u201corder_total\u201d back when the workflow finishes?"
    assert plan_answer(qs[0], {"choice": "keep"}, doc) == ([], "Reported as \u201corder_total\u201d")
    ops, receipt = plan_answer(qs[0], {"choice": "remove"}, doc)
    assert ops == [{"op": "delete_step", "step_key": qs[0]["step_keys"][0]}] and receipt == "Not reported"
    row = describe_event({"action": {"action": "extract"}, "target": {"inner_text": "Order total"}, "extras": {"sequence": 3}, "page": {}})
    assert row["text"] == "Will read \u201cOrder total\u201d back to whoever runs this"


def test_a_tests_outputs_are_read_back_from_the_runtimes_success_text():
    from handlers.workflows import outputs_from_message

    text = "Done. URL: https://x.test/\n\nOutputs:\n  ticket_id: T-9\n  total: $42.10: incl. tax\n\nWarning:\n  something\n(run_id: r1)"
    assert outputs_from_message(text) == {"ticket_id": "T-9", "total": "$42.10: incl. tax"}
    assert outputs_from_message("Done. URL: https://x.test/\n(run_id: r1)") == {}


# ── on_missing ──────────────────────────────────────────────────────────────────────────────

_BOUND = {"container_selector": "tr.row", "identifier": "{{invoice}}", "source": "input", "confirmed": True}


def test_on_missing_skip_ships_only_on_a_step_bound_to_a_record():
    base = {"action": "click", "intent": "open_invoice", "target": {"primary_selector": "#open"}, "on_missing": "skip"}
    assert _saved_step_to_execution_step({**base, "entity_binding": _BOUND})["on_missing"] == "skip"
    assert _saved_step_to_execution_step({**base, "entity_binding": _BOUND, "on_missing": "ask"})["on_missing"] == "ask"
    assert "on_missing" not in _saved_step_to_execution_step(base)                    # nothing to be missing from
    assert "on_missing" not in _saved_step_to_execution_step({**base, "entity_binding": _BOUND, "on_missing": "teleport"})


def test_the_gate_only_lets_a_bound_step_choose_to_skip_when_its_record_is_missing():
    bound = {"action": {"action": "click"}, "intent": "x", "target": {"primary_selector": "#x"}, "entity_binding": _BOUND}
    validate_editor_patch(bound, {"on_missing": "skip"}, POLICY)
    validate_editor_patch(bound, {"on_missing": "fail"}, POLICY)
    validate_editor_patch(bound, {"on_missing": "ask"}, POLICY)
    with pytest.raises(ValueError, match="on_missing_invalid"):
        validate_editor_patch(bound, {"on_missing": "teleport"}, POLICY)
    with pytest.raises(ValueError, match="on_missing_needs_a_bound_record"):
        validate_editor_patch({"action": {"action": "click"}, "intent": "x", "target": {"primary_selector": "#x"}}, {"on_missing": "skip"}, POLICY)


# ── a confirmation guarding a destructive step cannot be edited into a free-form review ────


def _confirm_review():
    from conxa_compile.editor.patch_gate import CONFIRM_ONLY_REVIEW_SCHEMA

    return {"action": {"action": "ai_review"}, "intent": "confirm_send", "value": "ok", "ai_review_prompt": "Send?",
            "ai_review_output_schema": dict(CONFIRM_ONLY_REVIEW_SCHEMA), "ai_review_on_failure": "abort"}


def test_a_confirm_only_review_in_front_of_an_irreversible_step_cannot_be_loosened():
    nxt = {"action": {"action": "click"}, "intent": "send_invoice", "consequence": "irreversible"}
    loosened = {"ai_review_output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}}
    with pytest.raises(ValueError, match="confirmation_guards_a_destructive_step"):
        validate_editor_patch(_confirm_review(), loosened, POLICY, next_step=nxt)
    with pytest.raises(ValueError, match="confirmation_guards_a_destructive_step"):
        validate_editor_patch(_confirm_review(), {"ai_review_on_failure": "use_default"}, POLICY, next_step=nxt)
    # the prompt is free text for the person; wording changes stay allowed
    validate_editor_patch(_confirm_review(), {"ai_review_prompt": "Really send it?"}, POLICY, next_step=nxt)
    # nothing destructive behind it, or no neighbour known: loosening is fine (a plain review)
    validate_editor_patch(_confirm_review(), loosened, POLICY, next_step={"action": {"action": "click"}, "intent": "next"})
    validate_editor_patch(_confirm_review(), loosened, POLICY)
