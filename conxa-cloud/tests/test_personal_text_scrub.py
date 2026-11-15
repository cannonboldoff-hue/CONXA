"""W5-h: a signed-in address in a page TITLE (Gmail) no longer blocks every build; one in how a step finds
its element still does."""

from __future__ import annotations

import pytest

from conxa_compile.compiler.selector_filters import drop_personal_target_text
from conxa_compile.skill_package_builder import _assert_no_account_text

TITLE = "Inbox (274) - someone@example.com - Gmail"


def test_the_address_is_cut_out_of_the_page_title_at_capture():
    out = drop_personal_target_text([{"page": {"title": TITLE, "url": "https://mail.google.com/"}, "target": {}}])
    assert "@" not in out[0]["page"]["title"] and "Gmail" in out[0]["page"]["title"]


def test_a_title_with_no_address_is_untouched():
    ev = {"page": {"title": "Inbox - Gmail"}, "target": {}}
    assert drop_personal_target_text([ev])[0] is ev


def _doc(**step):
    return {"skills": [{"steps": [step]}]}


def test_the_build_ignores_an_address_in_the_title_that_is_not_shipped():
    _assert_no_account_text(_doc(signals={"context": {"page_title": TITLE}}), "Send mail")


def test_the_build_still_refuses_an_address_in_how_a_step_finds_its_element():
    with pytest.raises(ValueError):
        _assert_no_account_text(_doc(target={"primary_selector": 'text="someone@acme.io"'}), "Send mail")
    with pytest.raises(ValueError):
        _assert_no_account_text(_doc(signals={"semantic": {"text": "someone@acme.io"}}), "Send mail")
