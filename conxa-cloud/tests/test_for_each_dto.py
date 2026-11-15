"""EXEC-38: step_to_dto's read-only for_each projection (visibility only — no dedicated
editor control exists yet, see TODO.md)."""

from __future__ import annotations

from conxa_compile.editor.workflow_dto import step_to_dto


def _click_step(intent: str = "click_it") -> dict:
    return {"action": {"action": "click"}, "intent": intent, "target": {"primary_selector": "#x"}}


def _for_each_step(*, max_iterations=50, on_row_error=None, body=None) -> dict:
    for_each: dict = {
        "rows": {"container_selector": "table#invoices tr"},
        "as": "row",
        "max_iterations": max_iterations,
        "steps": body if body is not None else [_click_step("delete_row")],
    }
    if on_row_error is not None:
        for_each["on_row_error"] = on_row_error
    return {"action": {"action": "for_each"}, "intent": "process_each_invoice", "for_each": for_each}


def test_for_each_summary_none_for_ordinary_step():
    dto = step_to_dto("skill_1", _click_step(), 0, {}, "")
    assert dto.for_each_summary is None
    assert dto.for_each_steps == []


def test_for_each_projects_a_summary_and_nested_body_with_path_addressed_ids():
    dto = step_to_dto("skill_1", _for_each_step(), 3, {}, "")
    assert dto.for_each_summary == {
        "container_selector": "table#invoices tr",
        "items": "",
        "items_rows": "",
        "columns": [],
        "as": "row",
        "max_iterations": 50,
        "on_row_error": "stop",
        "step_count": 1,
    }
    assert len(dto.for_each_steps) == 1
    assert dto.for_each_steps[0].id == "skill_1:3.for_each.steps[0]"
    assert dto.for_each_steps[0].intent == "delete_row"


def test_for_each_on_row_error_continue_is_surfaced():
    dto = step_to_dto("skill_1", _for_each_step(on_row_error="continue"), 0, {}, "")
    assert dto.for_each_summary["on_row_error"] == "continue"


def test_for_each_with_multiple_body_steps_projects_all_of_them():
    body = [_click_step("open_row"), _click_step("delete_row")]
    dto = step_to_dto("skill_1", _for_each_step(body=body), 0, {}, "")
    assert dto.for_each_summary["step_count"] == 2
    assert [s.intent for s in dto.for_each_steps] == ["open_row", "delete_row"]


def test_for_each_with_empty_body_projects_zero_steps():
    dto = step_to_dto("skill_1", _for_each_step(body=[]), 0, {}, "")
    assert dto.for_each_summary["step_count"] == 0
    assert dto.for_each_steps == []


def test_for_each_items_source_surfaced_in_summary():
    for_each = {
        "items": "files",
        "as": "file",
        "max_iterations": 50,
        "steps": [_click_step("download_file")],
    }
    step = {"action": {"action": "for_each"}, "intent": "download_each_file", "for_each": for_each}
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.for_each_summary["items"] == "files"
    assert dto.for_each_summary["container_selector"] == ""
