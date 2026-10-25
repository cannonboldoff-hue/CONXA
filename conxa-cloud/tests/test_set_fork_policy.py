"""set_fork_policy: the author's decision for a popup state of an outcome fork, and the clarify
question that asks for it (conversation/clarify.py). It is the ONLY place a state's policy can
gain ``author_approved``."""

from __future__ import annotations

import json

import pytest
from conxa_core.models.skill_spec import IdentityBundle, SkillStep

from conxa_compile.compiler.outcome_fork import build_outcome_forks
from conxa_compile.conversation import clarify
from conxa_compile.editor.workflow_mutations import set_fork_policy


def _click(name, ctx=None):
    return SkillStep(
        action="click", intent=f"click_{name.lower()}",
        identity_bundle=IdentityBundle(stable_hash=f"h-{name}"),
        target={"primary_selector": f'internal:role=button[name="{name}"i]'},
        signals={"dialog_context": ctx} if ctx else {},
    )


def _ctx(chosen):
    names = ("Replace", "Keep both", "Cancel")
    return {
        "kind": "dom", "container_signal": '[role="dialog"]', "role": "dialog", "title": "Upload options",
        "message": "report.pdf already exists.", "has_fields": False,
        "buttons": [{"tag": "button", "role": "button", "name": n, "text": n, "chosen": n == chosen} for n in names],
    }


def _doc(chosen="Replace"):
    steps = build_outcome_forks([SkillStep(action="click", intent="up", target={"primary_selector": "#u"}), _click(chosen, _ctx(chosen))], "s1")
    return {"meta": {"id": "skill_x", "version": 1}, "skills": [{"steps": [json.loads(s.model_dump_json()) for s in steps]}], "inputs": []}


def _state(doc):
    return doc["skills"][0]["steps"][1]["outcome_fork"]["states"][0]


def test_picking_an_option_is_the_approval_to_run_it_automatically():
    out = set_fork_policy(_doc(), 1, "dialog_upload_options", {"mode": "auto", "option_id": "replace"})
    policy = _state(out)["policy"]
    assert policy["mode"] == "auto" and policy["option_id"] == "replace"
    assert policy["author_approved"] is True and policy["reviewed"] is True
    assert out["meta"]["version"] == 2


def test_compiling_never_pre_approves_anything():
    assert _state(_doc("Keep both"))["policy"]["author_approved"] is False


def test_by_input_declares_a_select_input_defaulting_to_ask_and_names_it_on_the_step():
    out = set_fork_policy(_doc(), 1, "dialog_upload_options", {"mode": "by_input", "input": "on_upload_options"})
    assert _state(out)["policy"]["input"] == "on_upload_options"
    spec = next(i for i in out["inputs"] if i["id"] == "on_upload_options")
    assert spec["type"] == "select" and spec["default"] == "ask"
    assert spec["options"] == ["Replace", "Keep both", "Cancel", "ask"]
    step = out["skills"][0]["steps"][1]
    assert step["input_binding"] == "on_upload_options" and step["value"] == "{{on_upload_options}}"


def test_changing_away_from_by_input_unbinds_the_step():
    by_input = set_fork_policy(_doc(), 1, "dialog_upload_options", {"mode": "by_input", "input": "on_x"})
    out = set_fork_policy(by_input, 1, "dialog_upload_options", {"mode": "stop"})
    step = out["skills"][0]["steps"][1]
    assert step["input_binding"] is None and step["value"] is None
    assert _state(out)["policy"]["mode"] == "stop"


def test_ask_human_keeps_the_recorded_option_as_the_suggestion():
    out = set_fork_policy(_doc("Keep both"), 1, "dialog_upload_options", {"mode": "ask_human"})
    assert _state(out)["policy"] == {"mode": "ask_human", "suggested": "keep_both", "reviewed": True}


@pytest.mark.parametrize("bad, code", [
    ({"mode": "auto", "option_id": "nuke"}, "fork_option_not_found"),
    ({"mode": "yolo"}, "fork_policy_mode_invalid"),
])
def test_invalid_policies_are_rejected(bad, code):
    with pytest.raises(ValueError, match=code):
        set_fork_policy(_doc(), 1, "dialog_upload_options", bad)


def test_unknown_state_and_non_fork_steps_are_rejected():
    with pytest.raises(ValueError, match="fork_state_not_found"):
        set_fork_policy(_doc(), 1, "nope", {"mode": "stop"})
    with pytest.raises(ValueError, match="step_is_not_an_outcome_fork"):
        set_fork_policy(_doc(), 0, "dialog_upload_options", {"mode": "stop"})


# ── the question ───────────────────────────────────────────────────────────────────────────


def test_a_fork_is_asked_about_with_every_option_plus_the_three_policies():
    qs = clarify.generate_questions(_doc())
    q = next(q for q in qs if q["kind"] == "fork")
    ids = [c["id"] for c in q["choices"]]
    assert ids == ["option:replace", "option:keep_both", "option:cancel", "by_input", "ask_human", "stop"]
    assert "recording" in q["choices"][0]["detail"]
    assert "change or delete things" in q["choices"][0]["detail"]
    assert q["fork_state_id"] == "dialog_upload_options"


def test_the_fork_question_survives_the_five_question_cap():
    doc = _doc()
    steps = doc["skills"][0]["steps"]
    for i in range(4):  # four upload-style questions would otherwise fill the cap
        steps.append({"action": {"action": "upload"}, "input_binding": "file_path", "intent": f"u{i}", "value": f"/tmp/f{i}.txt"})
    qs = clarify.generate_questions(doc)
    assert len(qs) <= clarify.MAX_QUESTIONS and any(q["kind"] == "fork" for q in qs)


def test_a_state_the_author_already_answered_is_not_asked_again():
    out = set_fork_policy(_doc(), 1, "dialog_upload_options", {"mode": "ask_human"})
    assert not [q for q in clarify.generate_questions(out) if q["kind"] == "fork"]


def test_the_model_may_only_reword_the_title_never_the_choices():
    seen = {}

    def copy(payload):
        seen.update(payload)
        return "Drive says a file with that name is already there. What should happen?"

    q = next(q for q in clarify.generate_questions(_doc(), fork_copy=copy) if q["kind"] == "fork")
    assert q["title"].startswith("Drive says")
    assert [c["id"] for c in q["choices"]][:3] == ["option:replace", "option:keep_both", "option:cancel"]
    assert seen["buttons"] == ["Replace", "Keep both", "Cancel"] and "report" not in " ".join(seen["message_hint"])


def test_a_failing_or_empty_rewrite_falls_back_to_the_fixed_title():
    for bad in (lambda p: None, lambda p: "  ", lambda p: (_ for _ in ()).throw(RuntimeError("provider down"))):
        q = next(q for q in clarify.generate_questions(_doc(), fork_copy=bad) if q["kind"] == "fork")
        assert q["title"] == "A message box called “Upload options” can pop up here. What should happen when it does?"


@pytest.mark.parametrize("choice, mode", [
    ("option:keep_both", "auto"), ("by_input", "by_input"), ("ask_human", "ask_human"), ("stop", "stop"),
])
def test_each_answer_becomes_a_set_fork_policy_op(choice, mode):
    doc = _doc()
    q = next(q for q in clarify.generate_questions(doc) if q["kind"] == "fork")
    ops, receipt = clarify.plan_answer(q, {"choice": choice}, doc)
    assert len(ops) == 1 and ops[0]["op"] == "set_fork_policy"
    assert ops[0]["policy"]["mode"] == mode and ops[0]["state_id"] == "dialog_upload_options"
    assert receipt
