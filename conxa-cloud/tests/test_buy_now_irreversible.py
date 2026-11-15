"""A purchase button counts as irreversible; look-alike words do not (PROD-11b)."""
from __future__ import annotations

import re

import pytest

from conxa_compile.compiler.destructive_semantics import classify_consequence
from conxa_compile.policy.bundle import get_default_policy_dict


def _click(label: str) -> dict:
    intent = "click_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") + "_button"
    return {
        "action": {"action": "click"},
        "target": {"tag": "button", "type": "button", "inner_text": label, "role": "button"},
        "semantic": {"normalized_text": label, "role": "button", "final_intent": intent},
    }


@pytest.mark.parametrize("label", ["Buy Now", "Buy it now", "Place order", "Pay now", "Complete purchase"])
def test_purchase_buttons_are_irreversible(label):
    assert classify_consequence(_click(label), get_default_policy_dict()) == "irreversible"


@pytest.mark.parametrize("label", ["Display options", "Proceed to checkout", "Purchase history", "Buyer guide"])
def test_look_alike_buttons_are_not_irreversible(label):
    assert classify_consequence(_click(label), get_default_policy_dict()) != "irreversible"


def test_ambiguous_failure_names_the_elements_it_could_not_choose_between():
    """PROD-11d: the person is told which ones tied and sent to Human Edit, not left with a vague line."""
    from conxa_compile.conversation.failure_summary import summarize

    text = summarize("Ambiguous element resolution (x)", "ambiguous", ["Apple Juice", "Pineapple Juice"])
    assert "“Apple Juice”" in text and "“Pineapple Juice”" in text and "Human Edit" in text
    assert "Human Edit" not in summarize("Ambiguous element resolution (x)", "ambiguous", [])
    assert "Human Edit" not in summarize("Element not found (resolve miss)", "not_found", ["A", "B"])
