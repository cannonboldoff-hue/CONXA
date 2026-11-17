"""W11-b: a click the person repeated on purpose is one step with a count, packaged as a runtime-only step type."""

from __future__ import annotations

import copy

from conxa_compile.compiler.step_anchors import clean_steps
from conxa_compile.policy.bundle import get_policy_bundle
from conxa_compile.skill_package_builder_saved_skill import _mark_repeat_click
from tests.test_phases import _minimal_click_event


def _clicks(n):
    out = []
    for i in range(n):
        e = copy.deepcopy(_minimal_click_event())
        e["extras"] = {"sequence": i + 1}
        out.append(e)
    return out


def _clean(events):
    return clean_steps(events, get_policy_bundle().data)


def test_fifty_identical_clicks_become_one_step_that_remembers_the_count():
    cleaned = _clean(_clicks(50))
    assert len(cleaned) == 1
    assert cleaned[0]["extras"]["repeat_count"] == 50
    assert len(cleaned[0]["extras"]["repeat_sequences"]) == 49


def test_the_input_events_are_not_modified():
    events = _clicks(5)
    snapshot = copy.deepcopy(events)
    _clean(events)
    assert events == snapshot


def test_two_identical_clicks_still_fold_to_one_plain_click():
    cleaned = _clean(_clicks(2))
    assert len(cleaned) == 1 and int(cleaned[0]["extras"].get("repeat_count") or 1) == 2   # build ignores counts below 3


def test_a_counted_click_is_packaged_as_repeat_click_and_a_plain_one_is_not():
    converted = {"type": "click", "selector": "#a"}
    _mark_repeat_click({"handler_hints": {"repeat_count": 50}}, converted)
    assert converted["type"] == "repeat_click" and converted["count"] == 50
    plain = {"type": "click", "selector": "#a"}
    _mark_repeat_click({"handler_hints": {"repeat_count": 1}}, plain)
    assert plain["type"] == "click" and "count" not in plain
    two = {"type": "click", "selector": "#a"}
    _mark_repeat_click({"handler_hints": {"repeat_count": 2}}, two)
    assert two["type"] == "click"
    huge = {"type": "click", "selector": "#a"}
    _mark_repeat_click({"handler_hints": {"repeat_count": 9999}}, huge)
    assert huge["count"] == 200


def test_the_step_name_says_how_many_times():
    from conxa_compile.conversation.clarify import step_name

    assert step_name({"semantic_description": "Click Add Element", "handler_hints": {"repeat_count": 50}}).endswith("(50 times)")
    assert "times" not in step_name({"semantic_description": "Click Add Element", "handler_hints": {"repeat_count": 1}})


def _bridged(bridge, seq, ts, ref):
    e = copy.deepcopy(_minimal_click_event())
    e["action"]["timestamp"] = ts
    e["page"] = {"url": "http://app.test/dl.html"}
    e["extras"] = {"sequence": seq, "bridge": bridge, "element_ref": f"{bridge}:{ref}", "uid": f"{bridge}-{ref}"}
    return e


def test_one_click_reported_by_two_bridges_in_one_page_is_one_step():
    """WF-5 Part B: after a download a second bridge sat beside the first; one click came in twice, 7 ms apart."""
    first = _bridged("bgsruyvw", 3, "2026-11-17T08:55:20.455Z", 2)
    echo = _bridged("0an0offe", 4, "2026-11-17T08:55:20.462Z", 1)
    echo["post_condition"] = {"classified_effect": "navigation"}
    cleaned = _clean([first, echo])
    assert len(cleaned) == 1
    assert int(cleaned[0]["extras"].get("repeat_count") or 1) == 1
    assert cleaned[0]["extras"]["sequence"] == 3
    assert cleaned[0]["post_condition"] == {"classified_effect": "navigation"}   # the effect only the echo carried


def test_the_same_button_clicked_again_after_the_page_reloaded_stays_two_steps():
    a = _bridged("bgsruyvw", 1, "2026-11-17T08:55:20.000Z", 1)
    b = _bridged("0an0offe", 2, "2026-11-17T08:55:21.500Z", 1)
    assert len(_clean([a, b])) == 2
