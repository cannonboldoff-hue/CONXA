"""Every body a step can hold has one address, one walker and a place for checks (BUILD-102 (1), plan W-B)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conxa-builder" / "python"))

from conxa_compile.conversation import clarify  # noqa: E402
from conxa_compile.conversation.clarify import plan_answer  # noqa: E402
from conxa_compile.editor.step_path import (  # noqa: E402
    StepPathError,
    body_address,
    get_nested_step,
    is_best_effort_body,
    iter_steps,
    parse_nested_step_path,
    split_body_address,
    walk_steps,
    with_nested_step,
)
from conxa_compile.editor.workflow_mutations import _template_assertion_targets  # noqa: E402
from conxa_core.models.step_identity import step_keys  # noqa: E402


def _s(action, intent="", **kw):
    return {"action": {"action": action}, "intent": intent, "validation": {"assertions": []}, **kw}


def _fork(option_steps):
    return _s("outcome_fork", "popup", outcome_fork={"states": [
        {"id": "a", "options": [{"id": "no", "steps": [_s("click", "cancel")]}, {"id": "yes", "steps": option_steps}]}]})


def _parent():
    return {
        **_s("if_present", "maybe"),
        "branch": {"steps": [_s("click", "b0")], "options": [{"steps": [_s("click", "o0")]}, {"steps": [_s("click", "o1")]}],
                   "flow": False},
        "for_each": {"steps": [_s("click", "l0"), _s("click", "l1")]},
        "outcome_fork": {"states": [{"options": [{"steps": [_s("click", "f00")]}, {"steps": [_s("click", "f01")]}]}]},
    }


@pytest.mark.parametrize("path, route, index, want", [
    ("branch.steps[0]", "branch", 0, "b0"),
    ("branch.options[1].steps[0]", "branch.options[1]", 0, "o1"),
    ("for_each.steps[1]", "for_each", 1, "l1"),
    ("outcome_fork.states[0].options[1].steps[0]", "outcome_fork.states[0].options[1]", 0, "f01"),
])
def test_every_body_has_one_path_form_that_reads_and_writes_back(path, route, index, want):
    parent = _parent()
    nested = parse_nested_step_path(path)
    assert nested == (route, index)
    got = get_nested_step(parent, nested)
    assert got["intent"] == want
    written = with_nested_step(parent, nested, {**got, "intent": "changed"})
    assert get_nested_step(written, nested)["intent"] == "changed"
    assert get_nested_step(parent, nested)["intent"] == want  # a new parent, the old one untouched


@pytest.mark.parametrize("bad", ["branch", "branch.steps", "steps[0]", "outcome_fork.steps[0]", "branch.options.steps[0]"])
def test_a_path_that_is_none_of_the_four_forms_is_refused(bad):
    with pytest.raises(StepPathError):
        parse_nested_step_path(bad)


def test_an_index_out_of_range_is_refused():
    with pytest.raises(StepPathError):
        get_nested_step(_parent(), parse_nested_step_path("outcome_fork.states[0].options[1].steps[5]"))


def test_only_if_present_style_bodies_are_best_effort():
    parent = _parent()
    assert is_best_effort_body(parent, parse_nested_step_path("branch.steps[0]"))
    assert is_best_effort_body(parent, parse_nested_step_path("branch.options[0].steps[0]"))
    assert not is_best_effort_body(parent, parse_nested_step_path("for_each.steps[0]"))
    assert not is_best_effort_body(parent, parse_nested_step_path("outcome_fork.states[0].options[0].steps[0]"))
    parent["branch"]["flow"] = True  # a whole way through the workflow runs through the real step loop
    assert not is_best_effort_body(parent, parse_nested_step_path("branch.options[0].steps[0]"))


def test_one_walk_reaches_every_step_once_at_any_depth():
    inner = _s("for_each", "inner", for_each={"steps": [_s("click", "deep")]})
    doc = {"skills": [{"steps": [_parent(), _fork([inner])]}]}
    names = [s["intent"] for s in walk_steps(doc["skills"][0]["steps"])]
    assert names.count("deep") == 1
    assert {"b0", "o0", "o1", "l0", "l1", "f00", "f01", "cancel", "inner", "deep"} <= set(names)
    addressed = [(i, p) for i, p, _ in iter_steps(doc)]
    assert (0, "for_each.steps[1]") in addressed and (1, "outcome_fork.states[0].options[1].steps[0]") in addressed
    assert addressed.count((0, None)) == 1


def test_a_body_address_splits_back_and_a_plain_key_does_not():
    addr = body_address("rec12", "outcome_fork.states[0].options[1].steps[0]")
    assert split_body_address(addr) == ("rec12", "outcome_fork.states[0].options[1].steps[0]")
    assert split_body_address("rec12") == ("rec12", None)
    assert split_body_address("abc123#2") == ("abc123#2", None)


def test_naming_a_value_reaches_the_checks_inside_every_body():
    """A check kept inside a popup option or loop body used to keep the recorded value after the value became an input."""
    in_fork = _s("click", "yes")
    in_fork["validation"]["assertions"] = [{"type": "text_present", "target": "Order 847291 deleted", "required": True}]
    doc = {"skills": [{"steps": [_fork([in_fork])]}]}
    _template_assertion_targets(doc["skills"][0]["steps"], "847291", "{{order}}")
    assert in_fork["validation"]["assertions"][0]["target"] == "Order {{order}} deleted"


def test_a_check_after_a_popups_answer_lands_on_the_step_inside_the_recorded_option(monkeypatch):
    """BUILD-102 (1): "Successfully Deleted" after "Yes, Delete" had nowhere to go."""
    import conxa_core.storage.session_events as se
    import conxa_core.storage.snapshots as sn

    events = [
        {"action": {"action": "click"}, "page": {"url": "https://hr.test/list"}, "extras": {"sequence": 1},
         "snapshot": {"dom_hash": "before"}},
        {"action": {"action": "click"}, "page": {"url": "https://hr.test/list"}, "extras": {"sequence": 2},
         "snapshot": {"dom_hash": "after"}},
    ]
    pages = {"before": "<p>Users</p>", "after": "<p>Users</p><p>Successfully Deleted</p>"}
    monkeypatch.setattr(se, "read_session_events", lambda _s: events)
    monkeypatch.setattr(sn, "read_dom_snapshot", lambda _s, h: pages.get(h))
    yes = _s("click", "yes_delete", source_event=2)
    fork = _fork([yes])
    fork["source_event"] = 2
    doc = {"meta": {"version": 1, "source_session_id": "s1"}, "inputs": [],
           "skills": [{"steps": [_s("click", "open", source_event=1), fork]}]}
    keys = step_keys(doc["skills"][0]["steps"])
    where = clarify.step_where_words_appear(doc, "Successfully Deleted")
    assert where == body_address(keys[1], "outcome_fork.states[0].options[1].steps[0]")

    q = {"kind": "done_when", "choices": [], "step_keys": []}
    ops, receipt = plan_answer(q, {"selected": [], "custom_text": "Successfully Deleted"}, doc)
    (op,) = ops
    assert op["step_key"] == where
    assert op["patch"]["validation"]["assertions"] == [{"type": "text_present", "target": "Successfully Deleted", "required": True}]
    assert "can’t check" not in receipt


def test_a_popup_option_that_was_never_recorded_holds_no_check():
    """An option nobody pressed has no event behind it, so words shown 'after' it are not placed on it."""
    fork = _fork([_s("click", "unrecorded")])
    assert clarify._body_step_for_event("rec2", fork, 2) is None


def test_the_patch_command_writes_a_check_into_a_popup_options_step(tmp_path, monkeypatch):
    """The edit command takes the new path form, so the answer above can be applied."""
    import importlib.util
    import os

    from conxa_core.config import settings
    from conxa_core.storage.json_store import read_skill, write_skill

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    py_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
    spec = importlib.util.spec_from_file_location("cbackend_bodies", os.path.join(py_dir, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    b = mod.Backend()

    yes = _s("click", "yes_delete")
    write_skill("skill_bodies", {"meta": {"id": "skill_bodies", "version": 1}, "inputs": [],
                                 "skills": [{"steps": [_s("click", "open"), _fork([yes])]}]})
    check = [{"type": "text_present", "target": "Successfully Deleted", "required": True}]
    b.cmd_patch_step({"skill_id": "skill_bodies", "step_index": 1, "path": "outcome_fork.states[0].options[1].steps[0]",
                      "patch": {"validation": {"assertions": check}}}, "rid")
    fork = read_skill("skill_bodies")["skills"][0]["steps"][1]
    assert fork["outcome_fork"]["states"][0]["options"][1]["steps"][0]["validation"]["assertions"] == check
    assert fork["outcome_fork"]["states"][0]["options"][0]["steps"][0]["validation"]["assertions"] == []


def test_a_chat_change_cannot_repoint_a_step_inside_a_body():
    import handlers.copilot as hc

    def doc(css):
        step = _s("click", "yes_delete", step_id="rec2/x", target={"css": css})
        fork = _fork([step])
        fork["step_id"] = "rec3"
        return {"skills": [{"steps": [fork]}]}

    before, after = doc("#yes"), doc("#somewhere-else")
    fixed = hc._keep_address_keys(before, after)
    assert fixed is not None
    assert fixed["skills"][0]["steps"][0]["outcome_fork"]["states"][0]["options"][1]["steps"][0]["target"] == {"css": "#yes"}
    assert hc._keep_address_keys(before, copy.deepcopy(before)) is None
