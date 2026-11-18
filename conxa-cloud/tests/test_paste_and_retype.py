"""Paste, select-all and retype record the box's final value, once (WF-8 8.2/8.3, 2026-11-18).

Ctrl+V compiled into a key step that replayed a literal "v"; Ctrl+A replayed as a typed "A" after the old text; and the
retyped "Final Corp" was folded into the first typing's step while that step still pointed at the "Wrong Name" event,
so the test filled in "Wrong Name" and asked whether "Final Corp" was left out by mistake."""

from __future__ import annotations

import json

from conxa_compile.compiler.build import prepare_compile_events
from conxa_compile.compiler.input_binding import parse_keyboard_event_value
from conxa_compile.conversation.clarify import generate_questions
from conxa_compile.policy.bundle import get_policy_bundle

_PAGE = {"url": "http://localhost:8128/f/p1.html"}


def _box(seq: int, action: str, value: str | None = None, label: str = "Company name") -> dict:
    return {"action": {"action": action, **({"value": value} if value is not None else {})},
            "target": {"tag": "input", "label_text": label, "name": label.lower().replace(" ", "_")},
            "selectors": {"css": "#" + label.lower().replace(" ", "_")},
            "semantic": {"input_type": "text"}, "page": _PAGE, "tab": _PAGE,
            "extras": {"sequence": seq, "element_ref": label}}


def _key(seq: int, key: str, label: str = "Company name", **mods: bool) -> dict:
    return _box(seq, "keyboard_shortcut", json.dumps({"key": key, "code": f"Key{key.upper()}",
                                                      "modifiers": {"ctrl": False, "shift": False, "alt": False,
                                                                    "meta": False, **mods}}), label)


def _prepare(events: list[dict]) -> tuple[list[dict], list[dict]]:
    ledger: list[dict] = []
    out = prepare_compile_events(events, get_policy_bundle().data, ledger)
    return [e for e in out if e["action"]["action"] not in ("focus", "click")], ledger


def test_a_paste_is_the_pasted_text_typed_once_and_no_key_press():
    kept, _ = _prepare([_box(1, "click", label="Reference code"), _key(2, "v", "Reference code", ctrl=True),
                        _box(3, "type", "REF-12345-pasted", "Reference code")])
    assert [(e["action"]["action"], e["action"]["value"]) for e in kept] == [("type", "REF-12345-pasted")]


def test_select_all_and_retype_keeps_the_final_value_from_its_own_event():
    kept, ledger = _prepare([_box(4, "click"), _box(5, "type", "Wrong Name"), _key(6, "A", ctrl=True),
                             _box(8, "type", "Final Corp")])
    assert [(e["action"]["action"], e["action"]["value"]) for e in kept] == [("type", "Final Corp")]
    assert kept[0]["extras"]["sequence"] == 8          # the step's source event holds the value it types
    typed_over = [d for d in ledger if d.get("value")]
    assert [(d["pass"], d["value"]) for d in typed_over] == [("typed_over", "Wrong Name")]
    doc = {"skills": [{"steps": [{"action": "type", "value": "{{company_name}}"}]}]}
    assert not [q for q in generate_questions(doc, dropped=ledger, limit=None) if q["kind"] == "dropped_value"]


def test_a_shortcut_outside_text_editing_stays_and_keeps_its_modifiers():
    kept, _ = _prepare([_box(1, "type", "x"), _key(2, "s", ctrl=True)])
    assert [e["action"]["action"] for e in kept] == ["type", "keyboard_shortcut"]
    raw = lambda key, **m: json.dumps({"key": key, "modifiers": {"ctrl": False, "shift": False, **m}})  # noqa: E731
    assert parse_keyboard_event_value(raw("s", ctrl=True)) == "Control+S"
    assert parse_keyboard_event_value(raw("Tab", shift=True)) == "Shift+Tab"
    assert parse_keyboard_event_value(raw("Enter")) == "Enter"
    assert parse_keyboard_event_value({"key": "Enter", "modifiers": {"meta": True}}) == "Meta+Enter"


def test_a_value_really_left_out_is_said_with_one_choice_that_promises_nothing():
    doc = {"skills": [{"steps": [{"action": "click", "intent": "go"}]}]}
    lost = [{"sequence": 2, "pass": "cleanup", "action": "type", "value": "Acme Ltd"}]
    q = next(q for q in generate_questions(doc, dropped=lost, limit=None) if q["kind"] == "dropped_value")
    assert [c["id"] for c in q["choices"]] == ["ok"]


def test_a_double_click_compiles_to_the_dblclick_alone_in_both_real_recordings():
    """WF-8 8.6: the same click, click, dblclick compiled to one or two steps, as the second opinion happened to judge."""
    from pathlib import Path

    takes = json.loads((Path(__file__).parent / "fixtures" / "wf8_double_click_events.json").read_text(encoding="utf-8"))
    for events in takes.values():
        ledger: list[dict] = []
        out = prepare_compile_events(events, get_policy_bundle().data, ledger)
        assert [(e["extras"]["sequence"], e["action"]["action"]) for e in out] == [
            (3, "dblclick"), (4, "right_click"), (5, "keyboard_shortcut"), (6, "click")]
        assert sorted(d["sequence"] for d in ledger) == [1, 2]
