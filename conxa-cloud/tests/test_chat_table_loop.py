"""EXEC-63: chat can make and change a table loop, one card, one code-owned write. The model names steps and
column names only; code declares the table input and switches the loop's source together, so the
one-row-source rule never sees a half state, and nothing a step acts on changes."""

from __future__ import annotations

import json

from conxa_compile.compiler.step_key import step_keys
from conxa_compile.editor.copilot_proposals import apply_table_loop_op, gate_structural_proposals, proposal_summary
from conxa_compile.editor.patch_gate import _validate_for_each_source
from conxa_compile.editor.workflow_mutations import _new_manual_step
from conxa_compile.skill_package_builder_saved_skill import _saved_for_each_step


def _typed(element, value):
    return {"action": {"action": "type"}, "identity_bundle": {"stable_hash": element}, "intent": f"type_{element}",
            "target": {"primary_selector": f"#{element}"}, "value": value}


def _click(element):
    return {"action": {"action": "click"}, "identity_bundle": {"stable_hash": element}, "intent": f"click_{element}",
            "target": {"primary_selector": f"#{element}"}, "value": ""}


def _steps(doc):
    return doc["skills"][0]["steps"]


def _ev(raw):
    return {**raw, "why": "asked", "evidence_refs": ["x"]}


def _mail_doc():
    steps = [{"action": {"action": "navigate"}, "url": "https://mail.test/", "intent": "open"},
             _click("compose"), _typed("to", "{{to}}"), _typed("subject", "Hello"), _click("send"), _click("logout")]
    return {"meta": {"version": 1}, "skills": [{"steps": steps}],
            "inputs": [{"id": "to", "type": "text", "default": "a@x.test"}]}


def _items_loop_doc(source=None):
    loop = _new_manual_step("for_each", "https://x")
    loop["for_each"] = {**(source or {"items": "files"}), "as": "row", "max_iterations": 5, "on_row_error": "stop",
                        "steps": [_typed("name", "{{row_id}}")]}
    return {"meta": {"version": 1}, "skills": [{"steps": [loop]}],
            "inputs": [{"id": "files", "type": "text", "label": "Files", "default": "a.pdf, b.pdf"}]}


def test_make_table_loop_wraps_recorded_steps_and_starts_the_list_with_the_recording():
    doc = _mail_doc()
    k = step_keys(_steps(doc))
    raw = {"op": "make_table_loop", "from_step_key": k[1], "to_step_key": k[4],
           "columns": [{"name": "to", "step_key": k[2]}, {"name": "subject", "step_key": k[3]}]}
    [card] = gate_structural_proposals(doc, [_ev(raw)])
    assert "for every row of a list with the columns to, subject" in proposal_summary(card)
    out = apply_table_loop_op(doc, card)
    steps = _steps(out)
    assert [s["action"]["action"] if isinstance(s["action"], dict) else s["action"] for s in steps] == ["navigate", "for_each", "click"]
    loop = steps[1]["for_each"]
    assert (loop["items_rows"], loop["columns"]) == ("entries", ["to", "subject"])
    assert [s["value"] for s in loop["steps"]] == ["", "{{row.to}}", "{{row.subject}}", ""]
    assert [s["target"] for s in loop["steps"]] == [s["target"] for s in _steps(doc)[1:5]]  # nothing re-pointed
    assert [i["id"] for i in out["inputs"]] == ["entries"]  # the old per-step input went with its step
    assert json.loads(out["inputs"][0]["default"]) == [{"to": "a@x.test", "subject": "Hello"}]
    assert _saved_for_each_step(steps[1])["type"] == "for_each_rows"


def test_make_table_loop_refuses_a_column_on_a_step_that_types_nothing_or_outside_the_range():
    doc = _mail_doc()
    k = step_keys(_steps(doc))
    for cols in ([{"name": "to", "step_key": k[1]}], [{"name": "to", "step_key": k[5]}], [{"name": "1bad", "step_key": k[2]}]):
        raw = {"op": "make_table_loop", "from_step_key": k[1], "to_step_key": k[4], "columns": cols}
        assert gate_structural_proposals(doc, [_ev(raw)]) == []


def test_set_loop_table_switches_the_source_and_declares_the_table_in_one_write():
    doc = _items_loop_doc()
    k = step_keys(_steps(doc))
    [card] = gate_structural_proposals(doc, [_ev({"op": "set_loop_table", "step_key": k[0], "columns": ["file"]})])
    assert "go through a list with the columns file" in proposal_summary(card)
    out = apply_table_loop_op(doc, card)
    fe = _steps(out)[0]["for_each"]
    assert "items" not in fe and (fe["items_rows"], fe["columns"]) == ("files", ["file"])
    _validate_for_each_source(_steps(out)[0], {})  # exactly one row source
    [table] = out["inputs"]
    assert (table["type"], table["columns"], table["label"]) == ("rows", ["file"], "Files")
    assert json.loads(table["default"]) == [{"file": "a.pdf"}, {"file": "b.pdf"}]
    assert fe["steps"][0]["value"] == "{{row_id}}"  # the body is untouched: row_id is the first column


def test_set_loop_table_adds_a_column_and_keeps_rows_only_when_every_cell_has_a_value():
    doc = _items_loop_doc({"items_rows": "mails", "columns": ["to"]})
    doc["inputs"] = [{"id": "mails", "type": "rows", "columns": ["to"], "default": json.dumps([{"to": "a@x"}])}]
    k = step_keys(_steps(doc))
    out = apply_table_loop_op(doc, {"op": "set_loop_table", "step_key": k[0], "columns": ["to", "subject"]})
    [table] = out["inputs"]
    assert table["columns"] == ["to", "subject"] and "default" not in table  # a blank subject would end the run
    narrowed = apply_table_loop_op(doc, {"op": "set_loop_table", "step_key": k[0], "columns": ["to"]})
    assert json.loads(narrowed["inputs"][0]["default"]) == [{"to": "a@x"}]


def test_set_loop_table_refuses_a_page_loop_a_used_column_and_a_plain_step():
    page = _items_loop_doc({"rows": {"container_selector": "tr"}})
    assert gate_structural_proposals(page, [_ev({"op": "set_loop_table", "step_key": step_keys(_steps(page))[0], "columns": ["a"]})]) == []
    used = _items_loop_doc({"items_rows": "mails", "columns": ["to", "subject"]})
    used["inputs"] = [{"id": "mails", "type": "rows", "columns": ["to", "subject"]}]
    _steps(used)[0]["for_each"]["steps"][0]["value"] = "{{row.subject}}"
    assert gate_structural_proposals(used, [_ev({"op": "set_loop_table", "step_key": step_keys(_steps(used))[0], "columns": ["to"]})]) == []
    plain = _mail_doc()
    assert gate_structural_proposals(plain, [_ev({"op": "set_loop_table", "step_key": step_keys(_steps(plain))[1], "columns": ["a"]})]) == []


def test_apply_writes_the_loop_and_one_undo_takes_it_all_back(monkeypatch, tmp_path):
    import importlib.util
    import os
    import sys

    from conxa_core.config import settings
    from conxa_core.storage.json_store import read_skill, write_skill

    py_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
    sys.path.insert(0, py_dir)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    spec = importlib.util.spec_from_file_location("cbackend_table_loop", os.path.join(py_dir, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from handlers import protocol

    mod._write = protocol._write = lambda *_a, **_k: None
    b = mod.Backend()
    doc = _items_loop_doc()
    write_skill("sk_table", doc)
    [card] = gate_structural_proposals(doc, [_ev({"op": "set_loop_table", "step_key": step_keys(_steps(doc))[0], "columns": ["file", "note"]})])
    b._apply_change_set("sk_table", [card], "r1")
    after = read_skill("sk_table")
    assert _steps(after)[0]["for_each"]["items_rows"] == "files" and after["inputs"][0]["type"] == "rows"
    b.cmd_undo_workflow({"skill_id": "sk_table"}, "r2")
    assert _steps(read_skill("sk_table"))[0]["for_each"]["items"] == "files"
