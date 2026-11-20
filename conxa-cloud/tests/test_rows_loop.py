"""EXEC-60: a loop that carries a table (several values per row, {{row.column}})."""
from __future__ import annotations

import pytest

from conxa_compile.compiler.upload_binding import for_each_loop_variable_names
from conxa_compile.editor.dto import SkillInputVariable
from conxa_compile.editor.patch_gate import _validate_for_each_patch, _validate_for_each_source
from conxa_compile.editor.workflow_mutations import reconcile_inputs_with_step_values
from conxa_compile.skill_package_builder_output import execution_capabilities
from conxa_compile.skill_package_builder_saved_skill import _normalize_saved_skill_inputs, _saved_for_each_step

BODY = [{"action": {"action": "navigate", "url": "https://x.test/?to={{row.to}}"}, "intent": "open"}]


def _loop(**over):
    fe = {"items_rows": "mails", "columns": ["to", "subject"], "as": "row", "max_iterations": 20, "steps": BODY}
    fe.update(over)
    return {"action": {"action": "for_each"}, "for_each": fe}


def test_loop_variable_names_include_each_column_in_both_step_shapes():
    assert {"row_id", "row_index", "row.to", "row.subject"} <= for_each_loop_variable_names([_loop()])
    flat = {"type": "for_each_rows", "as": "m", "columns": ["to"], "steps": []}
    assert "m.to" in for_each_loop_variable_names([flat])


def test_a_row_column_reference_never_becomes_an_input():
    doc = {"skills": [{"steps": [_loop()]}], "inputs": [{"id": "mails", "type": "rows", "columns": ["to", "subject"]}]}
    out, added = reconcile_inputs_with_step_values(doc)
    assert [i["id"] for i in out["inputs"]] == ["mails"] and not added


def test_the_table_loop_is_packaged_as_its_own_step_type_with_its_columns():
    step = _saved_for_each_step(_loop())
    assert step["type"] == "for_each_rows" and step["items_rows"] == "mails" and step["columns"] == ["to", "subject"]
    assert "items" not in step
    assert execution_capabilities([step]) == ["loop_rows"]


@pytest.mark.parametrize("over", [{"columns": []}, {"columns": None}, {"items": "also"}, {"rows": {"container_selector": "tr"}}])
def test_a_table_loop_with_no_columns_or_a_second_source_is_not_runnable(over):
    assert _saved_for_each_step(_loop(**over)) is None


def test_a_comma_loop_still_packages_as_the_old_step_type():
    step = _saved_for_each_step(_loop(items_rows="", columns=None, items="files"))
    assert step["type"] == "for_each" and step["items"] == "files" and "columns" not in step
    assert execution_capabilities([step]) == []


def test_a_rows_input_is_packaged_as_an_array_of_objects():
    (row,) = _normalize_saved_skill_inputs([{"id": "mails", "type": "rows", "columns": ["to", "subject"]}])
    assert row["type"] == "array" and row["columns"] == ["to", "subject"]
    assert row["items"]["required"] == ["to", "subject"] and row["items"]["properties"]["to"] == {"type": "string"}


def test_rows_input_validation():
    SkillInputVariable.model_validate({"id": "mails", "type": "rows", "columns": ["to", "subject"]})
    for bad in ({"columns": []}, {"columns": ["to", "to"]}, {"columns": ["1x"]}, {"columns": ["a.b"]}):
        with pytest.raises(ValueError):
            SkillInputVariable.model_validate({"id": "mails", "type": "rows", **bad})
    with pytest.raises(ValueError):
        SkillInputVariable.model_validate({"id": "name", "type": "text", "columns": ["to"]})


def test_the_edit_gate_wants_exactly_one_source_and_columns_for_a_table():
    _validate_for_each_patch({"items_rows": "mails", "columns": ["to"]})
    with pytest.raises(ValueError):
        _validate_for_each_patch({"columns": ["to", "to"]})
    step = {"for_each": {"items": "files", "steps": BODY}}
    with pytest.raises(ValueError, match="exactly_one_row_source"):
        _validate_for_each_source(step, {"items_rows": "mails", "columns": ["to"]})
    _validate_for_each_source(step, {"items": "", "items_rows": "mails", "columns": ["to"]})
    with pytest.raises(ValueError, match="needs_columns"):
        _validate_for_each_source({"for_each": {"steps": BODY}}, {"items_rows": "mails"})


def test_the_test_card_turns_lines_into_rows_and_back():
    import os, sys

    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")))
    from handlers.protocol import _CommandError
    from handlers.test_flow import rows_from_text, rows_to_text

    cols = ["to", "subject"]
    rows = rows_from_text("a@x.com | Hello there\n\n b@x.com|Hi", cols, "Mails")
    assert rows == [{"to": "a@x.com", "subject": "Hello there"}, {"to": "b@x.com", "subject": "Hi"}]
    assert rows_to_text(rows, cols) == "a@x.com | Hello there\nb@x.com | Hi"
    # WF-11: the saved default is JSON text; the card shows it as the lines its help text asks for.
    assert rows_to_text('[{"to": "a@x.com", "subject": "Hello there"}]', cols) == "a@x.com | Hello there"
    assert rows_to_text("a@x.com | Hi", cols) == "a@x.com | Hi"
    for bad in ("a@x.com", "a@x.com | x | y", "a@x.com | "):
        with pytest.raises(_CommandError, match="line 1"):
            rows_from_text(bad, cols, "Mails")
