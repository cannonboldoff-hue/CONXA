"""Buttons that only move a person through an app are not steps that can't be undone (WF-3 Leg B, owner decision
2026-11-17). "Continue Shopping" and a checkout form's "Continue" were asked about as irreversible and carried only
"the app accepted the request" as proof, which a page-only shop never sends. Commits still are."""

from __future__ import annotations

import pytest

from conxa_compile.compiler.action_semantics import commit_intent_hit
from conxa_compile.compiler.destructive_semantics import classify_consequence
from conxa_compile.compiler.effects import classify_kind
from conxa_compile.policy.bundle import get_policy_bundle

POL = get_policy_bundle().data


def _click(text: str = "", *, tag: str = "button", input_type: str = "", name: str = "", intent: str = "") -> dict:
    return {"action": {"action": "click"},
            "target": {"tag": tag, "inner_text": text, "name": name or None, "role": "button", "type": input_type or None},
            "semantic": {"normalized_text": text.lower(), "role": "button", "final_intent": intent}}


@pytest.mark.parametrize("ev", [
    _click("Continue Shopping"),
    _click("Continue"),
    _click("Next"),
    _click("Back"),
    _click("Sign in"),
    _click(tag="input", input_type="submit", name="continue"),  # saucedemo's checkout Continue
])
def test_a_button_that_only_moves_forward_is_reversible_and_starts_no_work(ev):
    assert not commit_intent_hit(ev, POL)
    assert classify_consequence(ev, POL) == "reversible"
    assert classify_kind(ev, consequence="reversible", is_commit=False) is None


@pytest.mark.parametrize("ev", [
    _click("Place order"),
    _click("Pay now"),
    _click("Submit order"),
    _click("Confirm and continue"),  # a commit word wins over a navigation word
    _click(tag="input", input_type="submit", name="go"),  # a submit button that names nothing stays careful
])
def test_a_commit_is_still_a_step_that_cant_be_undone(ev):
    assert commit_intent_hit(ev, POL)
    assert classify_consequence(ev, POL) == "irreversible"


def test_delete_still_asks_whatever_the_navigation_words():
    ev = _click("Remove", intent="activate_control_remove")
    assert classify_consequence(ev, POL) == "irreversible"


def test_navigation_words_match_whole_words_only():
    assert commit_intent_hit(_click("Discontinue plan", tag="input", input_type="submit"), POL)


def test_a_button_drawn_as_an_input_is_not_a_box_to_type_into():
    from conxa_compile.compiler.action_semantics import is_editable_target
    for kind in ("submit", "button", "reset", "image"):
        assert not is_editable_target({"target": {"tag": "input"}, "semantic": {"input_type": kind}}), kind
    assert is_editable_target({"target": {"tag": "input"}, "semantic": {"input_type": "text"}})
