"""A keyboard pick in a native list is one step (WF-3 Leg B, saucedemo sort, 2026-11-17).

Focus + ArrowDown on a <select> was recorded as select, type, a key press and type again, and compiled into four
steps per pass; replaying the arrow moved the sort choice again. Only the ``select`` event is the person's pick."""

from __future__ import annotations

import json

from conxa_compile.compiler.step_anchors import clean_steps


def _on_list(seq: int, action: str, value: str) -> dict:
    return {"action": {"action": action, "value": value},
            "target": {"tag": "select", "id": "sort", "aria_label": "Sort products"},
            "selectors": {"css": "#sort"}, "extras": {"sequence": seq, "element_ref": "e1"}}


def _key(seq: int, key: str, **mods: bool) -> dict:
    return _on_list(seq, "keyboard_shortcut", json.dumps({"key": key, "modifiers": {"ctrl": False, **mods}}))


def test_typing_and_arrow_echoes_on_a_list_are_dropped():
    out = clean_steps([_on_list(1, "select", "za"), _on_list(2, "type", "za"), _key(3, "ArrowDown"),
                       _on_list(4, "type", "za")])
    assert [(e["action"]["action"], e["action"]["value"]) for e in out] == [("select", "za")]


def test_two_picks_in_a_row_keep_the_last_one():
    out = clean_steps([_on_list(1, "select", "za"), _on_list(2, "select", "lohi")])
    assert [e["action"]["value"] for e in out] == ["lohi"]


def test_tab_and_shortcuts_on_a_list_stay_steps():
    for key in (_key(2, "Tab"), _key(2, "s", ctrl=True)):
        out = clean_steps([_on_list(1, "select", "za"), key])
        assert [e["action"]["action"] for e in out] == ["select", "keyboard_shortcut"]
