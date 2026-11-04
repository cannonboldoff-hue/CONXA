"""A step finds its recorded event by source_event, so the compile passes do not depend on their order
or on the step count still matching the event count."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # reuse the event builders of the sibling test files

from test_choice_compile import _checkbox_event  # noqa: E402
from test_date_picker_collapse import _day_event, _open_event  # noqa: E402

from conxa_compile.compiler.choice import collapse_choice_group_runs
from conxa_compile.compiler.date_picker import collapse_date_picker_runs
from conxa_compile.compiler.step_events import events_for_steps
from conxa_core.models.skill_spec import HandlerHints, SkillStep


def _scene():
    events = [
        {**_checkbox_event("soccer", {"soccer": True}), "extras": {"sequence": 1}},
        {**_checkbox_event("tennis", {"soccer": True, "tennis": True}), "extras": {"sequence": 2}},
        {**_open_event(), "extras": {"sequence": 3}},
        {**_day_event(), "extras": {"sequence": 4}},
    ]
    steps = [
        SkillStep(action="set_checkbox", handler_hints=HandlerHints(), source_event=1),
        SkillStep(action="set_checkbox", handler_hints=HandlerHints(), source_event=2),
        SkillStep(action="focus", source_event=3),
        SkillStep(action="click", source_event=4),
    ]
    return events, steps


def _actions(steps):
    return [s.action for s in steps]


def test_a_step_with_no_event_gets_an_empty_one() -> None:
    events, steps = _scene()
    added = SkillStep(action="navigate")                       # a step a pass added itself: no source_event
    assert events_for_steps([added, *steps], events)[0] == {}
    assert events_for_steps([added, *steps], events)[3] is events[2]


def test_the_two_collapses_give_the_same_result_in_either_order() -> None:
    events, steps = _scene()
    a = collapse_date_picker_runs(collapse_choice_group_runs(list(steps), events, {}), events, {})
    events, steps = _scene()
    b = collapse_choice_group_runs(collapse_date_picker_runs(list(steps), events, {}), events, {})
    assert _actions(a) == _actions(b) == ["set_checkbox", "date_pick"]


def test_a_step_added_in_front_does_not_shift_the_others() -> None:
    events, steps = _scene()
    added = SkillStep(action="navigate")
    out = collapse_date_picker_runs(collapse_choice_group_runs([added, *steps], events, {}), events, {})
    assert _actions(out) == ["navigate", "set_checkbox", "date_pick"]
