"""A form filled once per record keeps every record's typing, in order (WF-3 Leg A, demoqa Web Tables, 2026-11-16).

Three causes, each general: (1) sorting every event by timestamp put a field's typing (stamped when the field loses
focus) after the next field's click; (2) typing into a new copy of a form merged into the first copy's typing;
(3) typing the search box again at the end merged back into its first use at the start."""

from __future__ import annotations

from conxa_compile.compiler.step_anchors import clean_steps
from conxa_compile.pipeline.run import _reorder_by_timestamp


def _ev(seq, action, ts, label, ref, value=None):
    return {"action": {"action": action, "timestamp": ts, "value": value},
            "target": {"tag": "input", "label_text": label, "inner_text": ""}, "selectors": {"css": f"#{label}"},
            "page": {"url": "https://x.test/"}, "extras": {"sequence": seq, "element_ref": ref}}


def test_only_a_dialog_answer_moves_by_time():
    click_last = _ev(2, "click", "2026-01-01T00:00:01.000Z", "last", "b:2")
    type_first = _ev(3, "type", "2026-01-01T00:00:01.004Z", "first", "b:1", "Conxa")  # stamped on blur, after the click
    click_first = _ev(1, "click", "2026-01-01T00:00:00.000Z", "first", "b:1")
    out = _reorder_by_timestamp([click_first, type_first, click_last])
    assert [e["extras"]["sequence"] for e in out] == [1, 3, 2]


def test_a_new_copy_of_a_form_keeps_its_own_typing():
    first = _ev(1, "type", "t", "First Name", "b:3", "E1")
    second = _ev(2, "type", "t", "First Name", "b:10", "E2")  # same label, a new box
    out = clean_steps([first, second])
    assert [e["action"]["value"] for e in out] == ["E1", "E2"]


def test_typing_again_later_is_its_own_moment_not_merged_back():
    search = _ev(1, "type", "t", "Search", "b:1", "Cierra")
    other = {**_ev(2, "click", "t", "Add", "b:2"), "target": {"tag": "button", "inner_text": "Add"}}
    again = _ev(3, "type", "t", "Search", "b:1", "Conxa")
    out = clean_steps([search, other, again])
    assert [(e["action"]["action"], e["action"].get("value")) for e in out][-1] == ("type", "Conxa")
    assert len([e for e in out if e["action"]["action"] == "type"]) == 2


def test_a_correction_in_the_same_box_still_merges():
    out = clean_steps([_ev(1, "type", "t", "Email", "b:1", "e1@acme"), _ev(2, "type", "t", "Email", "b:1", "e1@acme.io")])
    assert [e["action"]["value"] for e in out] == ["e1@acme.io"]


def test_two_same_looking_clicks_on_different_elements_both_run():
    a = {**_ev(1, "click", "t", "Delete", "b:4"), "target": {"tag": "span", "inner_text": "", "label_text": "Delete"}}
    b = {**_ev(2, "click", "t", "Delete", "b:6"), "target": {"tag": "span", "inner_text": "", "label_text": "Delete"}}
    out = clean_steps([a, b])
    assert len(out) == 2 and not (out[0].get("extras") or {}).get("repeat_count")


def test_emptying_a_box_after_select_all_is_kept():
    out = clean_steps([_ev(1, "type", "t", "Search", "b:1", "Cierra"),
                       {**_ev(2, "keyboard_shortcut", "t", "Search", "b:1", '{"key":"a"}')},
                       _ev(3, "type", "t", "Search", "b:1", "")])
    assert [(e["action"]["action"], e["action"].get("value")) for e in out][-1] == ("type", "")
