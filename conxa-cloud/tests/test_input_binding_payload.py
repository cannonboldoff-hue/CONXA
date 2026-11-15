"""W5-c: a drag's payload is never turned into an invented input."""

from __future__ import annotations

import json

from conxa_compile.compiler.input_binding import derive_input_binding


def test_a_drag_keeps_its_payload_and_gets_no_input():
    payload = json.dumps({"src_css": "#a", "dst_css": "#drop"})
    ev = {"action": {"action": "drag_drop", "value": payload}, "target": {"label_text": "Products", "aria_label": "Drop here"}}
    assert derive_input_binding(ev, {}) == (payload, None)


def test_typing_still_becomes_an_input():
    ev = {"action": {"action": "type", "value": "Ada"}, "target": {"label_text": "First name"}}
    assert derive_input_binding(ev, {}) == ("{{first_name}}", "first_name")
