"""Tests for compiler/date_picker.py's click-run collapse — previously untested (7969ad8 added
only runtime/test/unit/test_date_picker.js, the pure replay math; the compile-side collapse
pass itself had zero Python coverage).

Covers the two defects found chasing the demoqa.com/automation-practice-form Date of Birth
picker failure (the failing step there was actually a fabricated accessible-name bug in
identity_bundle.py — see test_nameless_element_identity.py — but this module has its own,
separate defects that were never exercised by any recording old enough to hit them):

- _looks_like_field_open must match a `focus`-action opener (clean_steps rewrites a plain
  click on an editable target to `action=focus` before this pass ever runs — a click-only
  check can never fold the common case).
- strategy is derived from whether an anchored field exists (handler_hints.date_picker.open),
  not from whether a preceding step happened to fold — those are different questions.
- no compiled date_pick step carries a value_equals assertion pinned to the recorded date
  (that made every run with a caller-supplied date fail VERIFY even after a correct pick).
"""
from __future__ import annotations

from conxa_compile.compiler.date_picker import collapse_date_picker_runs
from conxa_core.models.skill_spec import SkillStep


def _step(action: str, semantic_description: str) -> SkillStep:
    return SkillStep(action=action, semantic_description=semantic_description)


def _open_event(css: str = "#dateOfBirthInput") -> dict:
    return {
        "action": {"action": "focus"},
        "target": {"tag": "input", "label_text": "Date of Birth"},
        "selectors": {"css": css, "aria": "", "xpath": ""},
        "date_context": None,
    }


def _day_event(field: str = "#dateOfBirthInput", iso_date: str = "2026-09-15", cell_attr: str = "") -> dict:
    return {
        "action": {"action": "click"},
        "target": {"tag": "div"},
        "selectors": {"css": ".react-datepicker__day--15", "aria": "", "xpath": ""},
        "date_context": {
            "role": "day",
            "iso_date": iso_date,
            "cell_attr": cell_attr,
            "grid": ".react-datepicker",
            "header": ".react-datepicker__current-month",
            "header_text": "September 2026",
            "prev": ".react-datepicker__navigation--previous",
            "next": ".react-datepicker__navigation--next",
            "cell": ".react-datepicker__day--15",
            "field": field,
            "field_display_value": "",
        },
    }


def test_focus_action_opener_is_folded():
    """Regression: _looks_like_field_open used to require action == "click", but clean_steps
    always rewrites a plain click on an editable target to "focus" before this pass runs — so
    the fold could never fire on the common case. The observable effect: the date_pick step's
    identity should come from the OPENER step, not the day-cell step."""
    open_step = _step("focus", "opener-marker")
    day_step = _step("click", "day-cell-marker")
    out = collapse_date_picker_runs([open_step, day_step], [_open_event(), _day_event()], {})
    assert len(out) == 1, "no orphaned focus step should remain next to the collapsed date_pick"
    assert out[0].action == "date_pick"
    assert out[0].semantic_description == "opener-marker", (
        "base step must be the folded opener, not the day cell — the fold didn't happen"
    )


def test_strategy_is_typed_first_when_a_field_exists_even_without_a_fold():
    """Regression: strategy used to be "typed_first" only when a preceding step successfully
    folded into open_step. But the day event's own date_context.field independently names the
    anchored field (bridge.js's focus-tracked reference) — an anchored field existing and a
    preceding step folding are different questions. Here there is no preceding step at all
    (nothing to fold), yet the day event still carries a field, so strategy must be
    "typed_first", not "grid_only"."""
    day_step = _step("click", "day-cell-marker")
    out = collapse_date_picker_runs([day_step], [_day_event()], {})
    assert len(out) == 1
    assert out[0].handler_hints.control_kind == "date_picker"
    assert out[0].handler_hints.date_picker["strategy"] == "typed_first"
    assert out[0].handler_hints.date_picker["open"] == "#dateOfBirthInput"


def test_strategy_is_grid_only_when_no_anchored_field():
    """An inline always-visible calendar (no field the day event points back to)."""
    day_step = _step("click", "day-cell-marker")
    out = collapse_date_picker_runs([day_step], [_day_event(field="")], {})
    assert len(out) == 1
    assert out[0].handler_hints.date_picker["strategy"] == "grid_only"


def test_no_compiled_assertion_pins_the_recorded_date_for_an_anchored_field():
    """Regression: date_picker.py used to bake the RECORDED date into a required value_equals
    assertion while step.value was the {{binding}} token — so VERIFY failed for every
    caller-supplied date other than the one recorded, even after a correct pick. The runtime's
    own format-aware readback (handlers.js) is now the enforced post-condition instead."""
    open_step = _step("focus", "opener")
    day_step = _step("click", "day")
    out = collapse_date_picker_runs([open_step, day_step], [_open_event(), _day_event()], {})
    assert out[0].validation.assertions == []


def test_grid_only_assertion_uses_the_binding_token_not_the_recorded_date():
    """The grid_only compile-time assertion (when one is possible at all — only for a machine
    cell attribute) must check whatever date the CALLER picks, not the one recorded."""
    day_step = _step("click", "day")
    out = collapse_date_picker_runs([day_step], [_day_event(field="", cell_attr="data-date")], {})
    assertions = out[0].validation.assertions
    assert len(assertions) == 1
    assert assertions[0].type == "selector_present"
    assert "2026-09-15" not in assertions[0].target, "must not contain the literal recorded date"
    assert assertions[0].target == f'[data-date="{out[0].value}"]'


def test_grid_only_no_machine_attr_ships_no_assertion_rather_than_a_frozen_one():
    """No machine cell attribute means there's no way to build a selector the {{binding}} token
    can interpolate into — rather than freeze the recorded day's aria-label sentence (which is
    only ever valid for that one day), ship no compile-time assertion at all. The runtime's
    grid drive already throws if it can't land the click."""
    day_step = _step("click", "day")
    out = collapse_date_picker_runs([day_step], [_day_event(field="", cell_attr="")], {})
    assert out[0].validation.assertions == []


def test_typed_date_pass_skips_scroll_steps_with_dict_actions():
    """Regression: a scroll step's action is a dict payload, and `step.action in frozenset(...)`
    raised "unhashable type: 'dict'", failing every compile of a recording that scrolled."""
    from conxa_compile.compiler.date_picker import convert_typed_date_steps

    scroll = _step("click", "scroll")
    scroll.action = {"action": "scroll", "delta": 651}
    scroll_event = {"action": {"action": "scroll"}, "target": {}}
    convert_typed_date_steps([scroll], [scroll_event], {})
    assert scroll.action == {"action": "scroll", "delta": 651}
