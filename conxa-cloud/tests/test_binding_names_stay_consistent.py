"""WF-3 Leg A (2026-11-16): a value's name, its checks and the model's step numbers must agree.

- Renaming {{add}} to {{add_2}} changed the typed value but not its "the box holds {{add}}" check, which then failed.
- Emptying a box became a required input nobody could leave blank.
- The review model counted steps from 1, so every description sat on the next step."""

from __future__ import annotations

from conxa_compile.compiler.build import _deduplicate_input_bindings, _derive_input_binding
from conxa_compile.llm.workflow_review import _zero_based
from conxa_core.models.skill_spec import Assertion, SkillStep, ValidationBlock


def _typed(binding):
    return SkillStep(action={"action": "type"}, intent="x", value="{{" + binding + "}}", input_binding=binding,
                     validation=ValidationBlock(assertions=[Assertion(type="value_equals", target="#q", expected="{{" + binding + "}}")]))


def test_a_renamed_value_renames_its_check_too():
    steps = [_typed("add"), _typed("add")]
    _deduplicate_input_bindings(steps)
    assert steps[1].value == "{{add_2}}"
    assert steps[1].validation.assertions[0].expected == "{{add_2}}"


def test_emptying_a_box_is_not_an_input():
    ev = {"action": {"action": "type", "value": ""}, "target": {"tag": "input", "placeholder": "Type to search"},
          "semantic": {"input_type": "text"}}
    assert _derive_input_binding(ev, {})[1] is None


def test_a_reply_counted_from_one_is_moved_back():
    raw = {"steps": [{"index": 1, "intent": "Open"}, {"index": 2, "intent": "Type"}], "decision_points": [{"step_index": 2}]}
    out = _zero_based(raw, 2)
    assert [s["index"] for s in out["steps"]] == [0, 1] and out["decision_points"][0]["step_index"] == 1


def test_a_reply_counted_from_zero_is_left_alone():
    raw = {"steps": [{"index": 0}, {"index": 1}]}
    assert _zero_based(raw, 2) is raw
