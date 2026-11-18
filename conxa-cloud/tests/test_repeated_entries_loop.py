"""The same entry made several times with different details can become one repeated step over a list (WF-3 Leg A,
demoqa Web Tables: 5 records added one after another, 2026-11-16). Code finds the repeat, the person decides, code
rewrites; nothing here is decided by a model."""

from __future__ import annotations

import json

import pytest

from conxa_compile.compiler.loop_suggestion import detect_repeated_entries
from conxa_compile.conversation import clarify
from conxa_compile.editor.workflow_mutations import wrap_repeated_entries
from conxa_compile.skill_package_builder_saved_skill import _saved_for_each_step

LAST = ["E1", "E2", "E3"]


def _step(action, element, *, binding=None, seq=None, irreversible=False):
    s = {"action": {"action": action}, "identity_bundle": {"stable_hash": element}, "intent": f"{action}_{element}",
         "source_event": seq, "target": {"primary_selector": f"#{element}"}}
    if binding:
        s["value"], s["input_binding"] = "{{" + binding + "}}", binding
        s["validation"] = {"assertions": [{"type": "value_equals", "target": element, "expected": "{{" + binding + "}}",
                                           "required": True}]}
    if irreversible:
        s["consequence"] = "irreversible"
    return s


def _doc():
    steps, typed = [{"action": {"action": "navigate"}, "url": "https://x.test/", "intent": "open"}], {}
    for r, last in enumerate(LAST):
        sfx = "" if r == 0 else f"_{r + 1}"
        base = 100 * (r + 1)
        steps += [_step("click", "add"), _step("type", "first", binding=f"first_name{sfx}", seq=base + 1),
                  _step("type", "last", binding=f"last_name{sfx}", seq=base + 2), _step("click", "submit", irreversible=True)]
        typed[base + 1], typed[base + 2] = "Conxa", last
    steps.append(_step("click", "logout"))
    inputs = [{"id": s["input_binding"], "type": "text"} for s in steps if s.get("input_binding")]
    return {"skills": [{"steps": steps}], "inputs": inputs, "meta": {"version": 1}}, typed


def _typed_of(typed):
    return lambda s: typed.get(s.get("source_event"))


def test_finds_the_repeated_block_with_the_differing_value_as_a_column():
    doc, typed = _doc()
    found = detect_repeated_entries(doc["skills"][0]["steps"], _typed_of(typed))
    assert (found["block_len"], found["count"]) == (4, 3)
    assert found["columns"] == [{"name": "last_name", "offset": 2}]
    assert found["fixed"] == [{"offset": 1, "value": "Conxa"}]
    assert found["rows"] == [{"last_name": v} for v in LAST]


def test_an_identical_repeat_is_not_a_list_of_entries():
    doc, typed = _doc()
    same = {k: "same" for k in typed}
    assert detect_repeated_entries(doc["skills"][0]["steps"], _typed_of(same)) is None


def test_wrap_makes_one_table_loop_with_the_recorded_rows():
    doc, typed = _doc()
    found = detect_repeated_entries(doc["skills"][0]["steps"], _typed_of(typed))
    out = wrap_repeated_entries(doc, found)
    steps = out["skills"][0]["steps"]
    assert [s["action"]["action"] if isinstance(s["action"], dict) else s["action"] for s in steps] == ["navigate", "for_each", "click"]
    loop = steps[1]["for_each"]
    assert (loop["items_rows"], loop["columns"], loop["as"]) == ("entries", ["last_name"], "row")
    assert [b.get("value") for b in loop["steps"]] == [None, "Conxa", "{{row.last_name}}", None]
    assert loop["steps"][2]["validation"]["assertions"][0]["expected"] == "{{row.last_name}}"
    assert [i["id"] for i in out["inputs"]] == ["entries"]
    assert json.loads(out["inputs"][0]["default"]) == [{"last_name": v} for v in LAST]
    packaged = _saved_for_each_step(steps[1])
    assert packaged["type"] == "for_each_rows" and packaged["columns"] == ["last_name"]


def test_wrap_refuses_when_the_steps_changed_since_the_question():
    doc, typed = _doc()
    found = detect_repeated_entries(doc["skills"][0]["steps"], _typed_of(typed))
    doc["skills"][0]["steps"].pop(6)  # a step of the second copy removed in Human Edit
    with pytest.raises(ValueError, match="suggestion_stale"):
        wrap_repeated_entries(doc, found)


def _questions(doc, typed):
    by_seq = {k: {"action": {"value": v}, "extras": {"sequence": k}} for k, v in typed.items()}
    return clarify.generate_questions(doc, source_lookup=lambda s: by_seq.get(s.get("source_event")), limit=None)


def test_the_repeat_is_asked_first_and_its_values_are_not_asked_again():
    doc, typed = _doc()
    qs = _questions(doc, typed)
    assert qs[0]["kind"] == "repeat_rows"
    assert "different last name" in qs[0]["title"]
    assert not [q for q in qs if q["kind"] == "values"]


def test_answers_list_wraps_and_same_keeps_every_recorded_value():
    doc, typed = _doc()
    q = _questions(doc, typed)[0]
    ops, _ = clarify.plan_answer(q, {"choice": "list"}, doc)
    assert [o["op"] for o in ops] == ["wrap_rows_loop"]
    ops, receipt = clarify.plan_answer(q, {"choice": "same"}, doc)
    patched = [o["patch"]["value"] for o in ops if o["op"] == "patch"]
    assert patched == ["Conxa", "E1", "Conxa", "E2", "Conxa", "E3"]
    assert ops[-1]["op"] == "drop_inputs" and "these same 3" in receipt


def test_a_repeated_step_that_cannot_be_undone_is_approved_once_before_it_starts():
    doc, typed = _doc()
    found = detect_repeated_entries(doc["skills"][0]["steps"], _typed_of(typed))
    out = wrap_repeated_entries(doc, found)
    confirms = [q for q in clarify.generate_questions(out, limit=None) if q["kind"] == "confirm"]
    assert len(confirms) == 1 and "before it starts" in confirms[0]["title"]
    ops, _ = clarify.plan_answer(confirms[0], {"choice": "yes"}, out)
    assert ops[0]["op"] == "insert_review" and "every row of the list" in ops[0]["prompt"]


def test_same_entries_also_fixes_a_check_elsewhere_that_names_a_value():
    doc, typed = _doc()
    doc["skills"][0]["steps"][-1]["validation"] = {"assertions": [
        {"type": "text_present", "target": "{{last_name_3}}", "expected": "", "required": True}]}
    q = _questions(doc, typed)[0]
    ops, _ = clarify.plan_answer(q, {"choice": "same"}, doc)
    last_key = [o for o in ops if o["op"] == "patch"][-1]
    assert last_key["patch"]["validation"]["assertions"][0]["target"] == "E3"
    assert "last_name_3" in ops[-1]["ids"]


def test_two_different_boxes_that_share_an_element_hash_are_not_a_repeat():
    """WF-5: a name box and an email box with no distinguishing attributes got the same element hash, and Studio
    asked "You entered 2 items the same way, each with a different full name"."""
    def box(action, css, seq=None):
        return {"action": {"action": action}, "identity_bundle": {"stable_hash": "same"}, "source_event": seq,
                "target": {"css": css}}
    steps = [box("focus", "#userName"), box("type", "#userName", 1), box("focus", "#userEmail"), box("type", "#userEmail", 2)]
    assert detect_repeated_entries(steps, _typed_of({1: "Ada", 2: "ada@x.test"})) is None
    # The same box filled twice is still a repeat.
    same = [box("focus", "#userName"), box("type", "#userName", 1), box("focus", "#userName"), box("type", "#userName", 2)]
    assert detect_repeated_entries(same, _typed_of({1: "Ada", 2: "Bob"}))["count"] == 2
