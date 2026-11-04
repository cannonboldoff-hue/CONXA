"""Tests for multiple-choice control compilation (radio/checkbox groups, native <select>,
ARIA radiogroup/listbox widgets) — see CLAUDE.md's multiple-choice plan.

Regression coverage for the reported bug: recording a gender radio group compiled to a step
labelled `role=radio[name="gender"]` (the group's shared HTML `name`, not the picked option's
accessible name — Playwright's role selector matches accessible name, not `name`) plus a
duplicate phantom `Focus` step, and the input the runtime asked for was named after the
recorded ANSWER ({{male}}) instead of the QUESTION ({{gender}}), so passing a different value
at execution time had no effect on which radio got clicked.
"""

from __future__ import annotations

from conxa_compile.compiler.action_semantics import is_editable_target
from conxa_compile.compiler.choice import collapse_choice_group_runs, derive_choice
from conxa_compile.compiler.identity_bundle import generate_deterministic_signals
from conxa_compile.compiler.step_anchors import clean_steps
from conxa_compile.editor.describe import describe_step
from conxa_core.models.skill_spec import HandlerHints, SkillStep


def _radio_event(value: str, *, group_key: str = "gender") -> dict:
    options = [
        {"value": "male", "label": "Male", "selector": '#gender-male', "checked": value == "male"},
        {"value": "female", "label": "Female", "selector": "#gender-female", "checked": value == "female"},
        {"value": "other", "label": "Other", "selector": "#gender-other", "checked": value == "other"},
    ]
    return {
        "action": {"action": "set_radio", "value": value},
        "target": {
            "tag": "input", "role": "radio", "inner_text": "", "id": f"gender-{value}",
            "name": group_key, "label_text": options[["male", "female", "other"].index(value)]["label"],
        },
        "semantic": {"role": "radio", "input_type": "radio"},
        "selectors": {"css": f"#gender-{value}"},
        "choice_context": {
            "kind": "radio",
            "multi": False,
            "group_key": group_key,
            "group_label": "Gender",
            "group_selector": "fieldset#gender-group",
            "options": options,
        },
    }


def _click_event_on_radio(value: str, *, group_key: str = "gender") -> dict:
    """The prep click bridge.js also records immediately before the change-driven set_radio
    (see bridge.js's click listener) -- same target, no choice_context of its own."""
    return {
        "action": {"action": "click"},
        "target": {"tag": "input", "role": "radio", "inner_text": "", "name": group_key, "id": f"gender-{value}"},
        "semantic": {"role": "radio", "input_type": "radio"},
        "selectors": {"css": f"#gender-{value}"},
    }


# ---------------------------------------------------------------------------
# derive_choice
# ---------------------------------------------------------------------------

def test_derive_choice_names_input_after_the_question_not_the_answer():
    result = derive_choice(_radio_event("male"))
    assert result is not None
    assert result["input_binding"] == "gender"
    assert result["value"] == "{{gender}}"


def test_derive_choice_carries_the_full_option_set():
    result = derive_choice(_radio_event("male"))
    choice = result["choice"]
    assert choice["kind"] == "radio"
    assert [o["value"] for o in choice["options"]] == ["male", "female", "other"]
    assert choice["recorded_label"] == "Male"


def test_derive_choice_falls_back_to_group_key_without_a_label():
    ev = _radio_event("male")
    ev["choice_context"]["group_label"] = ""
    result = derive_choice(ev)
    assert result["input_binding"] == "gender"


def test_derive_choice_none_without_choice_context():
    assert derive_choice({"action": {"action": "click"}, "target": {}}) is None


def test_derive_choice_none_for_checkbox_kind():
    """Checkbox groups are handled by collapse_choice_group_runs instead, since one group's
    answer spans multiple events -- derive_choice only covers single-shot kinds."""
    ev = _radio_event("male")
    ev["choice_context"]["kind"] = "checkbox"
    assert derive_choice(ev) is None


# ---------------------------------------------------------------------------
# identity_bundle: no name-derived role signal for a radio group
# ---------------------------------------------------------------------------

def test_no_role_signal_built_from_the_group_name_attribute():
    """The root cause: role=radio[name="gender"] names the GROUP, not the option Playwright's
    role selector actually matches against (the accessible name, "Male"). Every signal the
    compiler emits must come from the option's own accessible name (its label_text), never the
    shared group `name`."""
    ev = _radio_event("male")
    signals = generate_deterministic_signals(ev)
    for sig in signals:
        assert 'name="gender"' not in sig.selector, sig.selector


# ---------------------------------------------------------------------------
# collapse_choice_group_runs (checkbox groups)
# ---------------------------------------------------------------------------

def _checkbox_event(value: str, checked_map: dict[str, bool], *, group_key: str = "sports") -> dict:
    options = [
        {"value": v, "label": v.capitalize(), "selector": f"#sport-{v}", "checked": checked_map.get(v, False)}
        for v in ("soccer", "tennis", "chess")
    ]
    return {
        "action": {"action": "set_checkbox", "value": "true" if checked_map.get(value) else "false"},
        "target": {"tag": "input", "name": group_key, "id": f"sport-{value}"},
        "semantic": {"input_type": "checkbox"},
        "selectors": {"css": f"#sport-{value}"},
        "choice_context": {
            "kind": "checkbox", "multi": True, "group_key": group_key,
            "group_label": "Sports", "group_selector": "", "options": options,
        },
    }


def test_collapse_choice_group_runs_collapses_a_pick_all_that_apply_run():
    events = [
        _checkbox_event("soccer", {"soccer": True}),
        _checkbox_event("tennis", {"soccer": True, "tennis": True}),
    ]
    steps = [SkillStep(action="set_checkbox", handler_hints=HandlerHints()) for _ in events]
    out_steps = collapse_choice_group_runs(steps, events, {})
    assert len(out_steps) == 1
    step = out_steps[0]
    assert step.input_binding == "sports"
    assert step.value == "{{sports}}"
    assert step.handler_hints.control_kind == "choice"
    assert set(step.handler_hints.choice["recorded_values"]) == {"soccer", "tennis"}
    assert "chess" not in step.handler_hints.choice["recorded_values"]


def test_collapse_choice_group_runs_leaves_unrelated_steps_untouched():
    events = [{"action": {"action": "click"}, "target": {}}]
    steps = [SkillStep(action="click")]
    out_steps = collapse_choice_group_runs(steps, events, {})
    assert out_steps == steps


def test_collapse_choice_group_runs_leaves_a_later_date_pass_able_to_find_every_event():
    """After a checkbox run collapses, the steps no longer line up with the events by position. Each step
    still finds its own event through source_event, so a later pass (collapse_date_picker_runs) reads the
    right one."""
    from conxa_compile.compiler.step_events import events_for_steps
    events = [
        {**_checkbox_event("soccer", {"soccer": True}), "extras": {"sequence": 1}},
        {**_checkbox_event("tennis", {"soccer": True, "tennis": True}), "extras": {"sequence": 2}},
        {"action": {"action": "click"}, "target": {}, "marker": "unrelated", "extras": {"sequence": 3}},
    ]
    steps = [
        SkillStep(action="set_checkbox", handler_hints=HandlerHints(), source_event=1),
        SkillStep(action="set_checkbox", handler_hints=HandlerHints(), source_event=2),
        SkillStep(action="click", source_event=3),
    ]
    out_steps = collapse_choice_group_runs(steps, events, {})
    assert len(out_steps) == 2
    assert events_for_steps(out_steps, events)[1] is events[2]


# ---------------------------------------------------------------------------
# action_semantics / step_anchors: the phantom-Focus regression
# ---------------------------------------------------------------------------

def test_is_editable_target_excludes_radio_and_checkbox():
    assert is_editable_target({"target": {"tag": "input"}, "semantic": {"input_type": "radio"}}) is False
    assert is_editable_target({"target": {"tag": "input"}, "semantic": {"input_type": "checkbox"}}) is False
    assert is_editable_target({"target": {"tag": "input"}, "semantic": {"input_type": "text"}}) is True


def test_clean_steps_collapses_prep_click_and_set_radio_into_one_step():
    """The exact screenshot bug: a click on a radio option followed by its committing set_radio
    must compile to ONE step, never two (a phantom Focus/click plus the real set_radio)."""
    seq = [_click_event_on_radio("male"), _radio_event("male")]
    out = clean_steps(seq, {})
    actions = [(s.get("action") or {}).get("action") for s in out]
    assert actions == ["set_radio"]


def test_clean_steps_collapses_prep_click_and_set_checkbox_into_one_step():
    seq = [
        {"action": {"action": "click"}, "target": {"tag": "input", "id": "agree"}, "semantic": {"input_type": "checkbox"}, "selectors": {"css": "#agree"}},
        {"action": {"action": "set_checkbox", "value": "true"}, "target": {"tag": "input", "id": "agree"}, "semantic": {"input_type": "checkbox"}, "selectors": {"css": "#agree"}},
    ]
    out = clean_steps(seq, {})
    actions = [(s.get("action") or {}).get("action") for s in out]
    assert actions == ["set_checkbox"]


# ---------------------------------------------------------------------------
# editor/describe.py: the editor-visible half of the screenshot bug
# ---------------------------------------------------------------------------

def test_describe_step_shows_the_question_and_every_option():
    """Was: `Select radio option target role=radio[name="gender"]` (the screenshot bug). Now:
    the group's own question and its full recorded option set."""
    step = {
        "action": {"action": "set_radio"},
        "target": {"primary_selector": ""},
        "handler_hints": {
            "control_kind": "choice",
            "choice": {
                "group_label": "Gender",
                "options": [{"label": "Male"}, {"label": "Female"}, {"label": "Other"}],
            },
        },
    }
    assert describe_step(step, 0) == 'Step 1: Select "Gender" (Male, Female, Other)'


def test_describe_step_falls_back_to_default_verb_without_a_choice_hint():
    """A step compiled before this feature existed carries no handler_hints.choice at all --
    describe_step must fall through to today's unchanged behavior, not crash."""
    step = {"action": {"action": "set_radio"}, "target": {"primary_selector": "#g1"}}
    assert describe_step(step, 0) == "Step 1: Select radio option target #g1"
