"""An optional-popup hint survives only where "close it if it shows up" can mean something (WF-3 Leg A, 2026-11-16):
on a click, inside a window the person did not open themselves."""

from __future__ import annotations

from conxa_compile.compiler.build import _drop_hints_no_popup_explains
from conxa_core.models.skill_spec import Assertion, SkillStep, ValidationBlock

DIALOG = '[role="dialog"]'


def _step(action, *, hint=False, opens=False):
    v = ValidationBlock(assertions=[Assertion(type="selector_present", target=DIALOG, required=True)] if opens else [])
    return SkillStep(action={"action": action}, intent="x", validation=v,
                     optional_hint={"kind": "try_dismiss", "container_signal": DIALOG} if hint else None)


def test_typing_or_focusing_in_a_window_is_never_a_dismissal():
    steps = [_step("type", hint=True), _step("focus", hint=True)]
    _drop_hints_no_popup_explains(steps)
    assert [s.optional_hint for s in steps] == [None, None]


def test_a_window_the_person_opened_is_part_of_the_task():
    steps = [_step("click", opens=True), _step("click", hint=True)]
    _drop_hints_no_popup_explains(steps)
    assert steps[1].optional_hint is None


def test_an_unexpected_window_keeps_its_hint():
    steps = [_step("click"), _step("click", hint=True)]
    _drop_hints_no_popup_explains(steps)
    assert steps[1].optional_hint
