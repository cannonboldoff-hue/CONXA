"""Which steps count as destructive is policy data, pinned by one fixture.

The words live in policy/default_policy.json. A word that is missing from it is missed by the compiler,
so every change to the list goes through runtime/test/fixtures/destructive_cases.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conxa_compile.compiler.destructive_semantics import label_has_destructive_word, step_has_destructive_intent
from conxa_compile.confidence.uncertainty import default_destructive_tokens
from conxa_compile.conversation.timeline_understanding import must_ask
from conxa_compile.policy.bundle import get_default_policy_dict

CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "runtime" / "test" / "fixtures" / "destructive_cases.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[c["intent"] for c in CASES])
def test_intent_cases(case: dict) -> None:
    step = {"semantic": {"llm_intent": case["intent"]}}
    assert step_has_destructive_intent(step, get_default_policy_dict()) is case["destructive"]


def test_words_come_from_policy_and_multiword_matches() -> None:
    assert "cancel subscription" in default_destructive_tokens()
    assert label_has_destructive_word("Cancel subscription")


def _row(ref: str, action: str, target: str = "", dialog: str = "") -> dict:
    return {"kind": "event", "row_ref": ref, "event_id": ref, "action": action, "target": target,
            **({"dialog": dialog} if dialog else {})}


def test_click_before_a_decision_popup_is_asked_about_even_with_no_destructive_word() -> None:
    rows = [_row("r1", "click", "Eliminar cuenta"), _row("r2", "click", "Sí, eliminar", dialog="decision")]
    assert {"row": "r1", "effect": "confirm_before"} in must_ask(rows)


def test_click_with_a_destructive_word_is_left_to_the_compile_time_question() -> None:
    rows = [_row("r1", "click", "Delete account"), _row("r2", "click", "Yes", dialog="decision")]
    assert all(m["effect"] != "confirm_before" for m in must_ask(rows))


def test_no_confirm_question_without_a_decision_popup() -> None:
    assert all(m["effect"] != "confirm_before" for m in must_ask([_row("r1", "click", "Save")]))
