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


def test_a_known_credential_field_has_one_name_for_its_value_and_its_binding():
    """An email/password field takes the policy's input name; the value and the binding must agree on it, on every
    path that finds the field (label, placeholder, aria label, the value's own shape, the input type)."""
    from conxa_compile.policy.bundle import get_policy_bundle

    policy = get_policy_bundle().data
    for ev in (
        {"action": {"action": "type", "value": "a@b.io"}, "target": {"label_text": "Email"}},
        {"action": {"action": "type", "value": "a@b.io"}, "target": {"placeholder": "Email"}},
        {"action": {"action": "type", "value": "a@b.io"}, "target": {}},
        {"action": {"action": "type", "value": "hunter2"}, "target": {"label_text": "Password"}},
    ):
        value, binding = derive_input_binding(ev, policy)
        assert value == "{{" + str(binding) + "}}", (ev, value, binding)
        assert binding in ("user_email", "user_password")
