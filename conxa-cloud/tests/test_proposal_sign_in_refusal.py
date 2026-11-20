"""Chat and the diagnosis never type into a password box or send a run to a sign-in page (BUILD-102 (3))."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conxa-builder" / "python"))

from conxa_compile.editor.copilot_proposals import refuses_sign_in  # noqa: E402

PASSWORD_STEP = {"identity_bundle": {"fingerprint": {"input_type": "password"}}}
SEMANTIC_PASSWORD = {"signals": {"semantic": {"input_type": "password"}}}
TEXT_STEP = {"identity_bundle": {"fingerprint": {"input_type": "text"}}}


def test_typing_into_a_password_box_is_refused():
    assert refuses_sign_in("type", PASSWORD_STEP, {"value": "secret123"})
    assert refuses_sign_in("fill", SEMANTIC_PASSWORD, {"value": "{{password}}"})
    assert refuses_sign_in("type", PASSWORD_STEP, {"input_binding": "password"})


def test_typing_into_an_ordinary_box_is_not():
    assert not refuses_sign_in("type", TEXT_STEP, {"value": "Amelia"})
    assert not refuses_sign_in("type", PASSWORD_STEP, {"intent": "Enter the code"})  # words only, no value


def test_a_navigate_to_a_sign_in_page_is_refused():
    assert refuses_sign_in("navigate", {}, {"url": "https://demo.app/web/index.php/auth/login"})
    assert refuses_sign_in("navigate", {}, {"action": {"url": "https://x.com/signin"}})
    assert not refuses_sign_in("navigate", {}, {"url": "https://demo.app/pim/viewEmployeeList"})


def test_other_kinds_are_left_alone():
    assert not refuses_sign_in("click", PASSWORD_STEP, {"value": "x"})
