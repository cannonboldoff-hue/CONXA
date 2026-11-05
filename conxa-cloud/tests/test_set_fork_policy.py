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
    assert ids == ["option:replace", "option:keep_both", "option:cancel", "always", "by_input", "ask_human", "stop"]
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


# ── where the workflow goes after an option (EXEC-49 G4) ─────────────────────────────────────


def test_tails_alone_change_the_options_and_leave_the_policy_untouched():
    base = set_fork_policy(_doc(), 1, "dialog_upload_options", {"mode": "by_input"})
    out = set_fork_policy(base, 1, "dialog_upload_options", {"then": {"keep_both": "stop", "cancel": "end"}})
    state = _state(out)
    by_id = {o["id"]: o for o in state["options"]}
    assert by_id["keep_both"]["then"] == "stop" and by_id["cancel"]["then"] == "end"
    assert "then" not in by_id["replace"]
    assert state["policy"] == _state(base)["policy"], "a tails-only call must not rewrite the policy"
    assert state["then_reviewed"] is True


def test_rejoin_removes_a_tail_and_the_original_document_is_not_mutated():
    doc = _doc()
    once = set_fork_policy(doc, 1, "dialog_upload_options", {"then": {"keep_both": "stop"}})
    assert all("then" not in o for o in _state(doc)["options"]), "the caller's document was changed"
    twice = set_fork_policy(once, 1, "dialog_upload_options", {"then": {"keep_both": "rejoin"}})
    assert "then" not in next(o for o in _state(twice)["options"] if o["id"] == "keep_both")


def test_a_mode_and_tails_together_set_both():
    out = set_fork_policy(_doc(), 1, "dialog_upload_options", {"mode": "ask_human", "then": {"cancel": "stop"}})
    assert _state(out)["policy"]["mode"] == "ask_human" and _state(out)["policy"]["reviewed"] is True
    assert next(o for o in _state(out)["options"] if o["id"] == "cancel")["then"] == "stop"


@pytest.mark.parametrize("then,code", [
    ({"nope": "stop"}, "fork_option_not_found"),
    ({"cancel": "explode"}, "fork_then_invalid"),
    (["cancel"], "fork_then_invalid"),
])
def test_invalid_tails_are_rejected(then, code):
    with pytest.raises(ValueError, match=code):
        set_fork_policy(_doc(), 1, "dialog_upload_options", {"then": then})


def _human_doc():
    return set_fork_policy(_doc(), 1, "dialog_upload_options", {"mode": "by_input"})


def test_a_popup_left_to_the_person_asks_whether_the_other_choices_fit_the_next_steps():
    q = next(q for q in clarify.generate_questions(_human_doc()) if q["kind"] == "fork_then")
    assert [c["id"] for c in q["choices"]] == ["same", "stop", "end"]
    assert q["other_option_ids"] == ["keep_both", "cancel"] and q["fork_state_id"] == "dialog_upload_options"
    assert "Keep both" in q["title"] and "Replace" in q["title"]


def test_no_tail_question_for_an_automatic_popup_an_unanswered_one_or_an_answered_one():
    assert not [q for q in clarify.generate_questions(_doc()) if q["kind"] == "fork_then"]  # not reviewed yet
    auto = set_fork_policy(_doc(), 1, "dialog_upload_options", {"mode": "auto", "option_id": "replace"})
    assert not [q for q in clarify.generate_questions(auto) if q["kind"] == "fork_then"]  # only one path ever runs
    answered = set_fork_policy(_human_doc(), 1, "dialog_upload_options", {"then": {"keep_both": "rejoin", "cancel": "rejoin"}})
    assert not [q for q in clarify.generate_questions(answered) if q["kind"] == "fork_then"]


@pytest.mark.parametrize("choice,how", [("same", "rejoin"), ("stop", "stop"), ("end", "end")])
def test_each_tail_answer_becomes_a_tails_only_set_fork_policy_op(choice, how):
    q = next(q for q in clarify.generate_questions(_human_doc()) if q["kind"] == "fork_then")
    ops, _ = clarify.plan_answer(q, {"choice": choice}, _human_doc())
    assert ops == [{"op": "set_fork_policy", "step_key": q["step_keys"][0], "state_id": "dialog_upload_options",
                    "policy": {"then": {"keep_both": how, "cancel": how}}}]


def test_an_answer_that_is_not_one_of_the_choices_changes_nothing():
    q = next(q for q in clarify.generate_questions(_doc()) if q["kind"] == "fork")
    ops, receipt = clarify.plan_answer(q, {"choice": "burn_it_down"}, _doc())
    assert ops == [] and "not one of the choices" in receipt
    t = next(q for q in clarify.generate_questions(_human_doc()) if q["kind"] == "fork_then")
    assert clarify.plan_answer(t, {"choice": "maybe"}, _human_doc())[0] == []


# ── "it shows up every time": the fork becomes the recorded steps (EXEC-49 G3) ───────────────

from conxa_compile.editor.workflow_mutations import flatten_fork_to_recorded_option  # noqa: E402


def _doc_with_opener_assertion():
    doc = _doc()
    steps = doc["skills"][0]["steps"]
    steps[0]["validation"] = {"assertions": [
        {"type": "selector_present", "target": '[role="dialog"]', "required": False},
        {"type": "url_changed", "target": "", "required": True},
    ]}
    return doc


def test_flattening_replaces_the_fork_with_the_recorded_steps_and_restores_the_opener_check():
    doc = _doc_with_opener_assertion()
    out = flatten_fork_to_recorded_option(doc, 1, "dialog_upload_options")
    steps = out["skills"][0]["steps"]
    assert [s.get("intent") for s in steps] == ["up", "click_replace"], "the fork is gone, the recorded click took its place"
    assert not any((s.get("action") if isinstance(s.get("action"), str) else s["action"].get("action")) == "outcome_fork" for s in steps)
    by_type = {a["type"]: a["required"] for a in steps[0]["validation"]["assertions"]}
    assert by_type == {"selector_present": True, "url_changed": True}
    assert out["meta"]["version"] == 2
    assert _state(doc)["id"] == "dialog_upload_options", "the caller's document was changed"


def test_a_consequential_recorded_click_stays_a_destructive_step_after_flattening():
    out = flatten_fork_to_recorded_option(_doc_with_opener_assertion(), 1, "dialog_upload_options")
    assert out["skills"][0]["steps"][1].get("consequence") == "irreversible"


def test_only_a_single_state_fork_with_a_recorded_option_can_be_flattened():
    doc = _doc()
    with pytest.raises(ValueError, match="fork_state_not_found"):
        flatten_fork_to_recorded_option(doc, 1, "nope")
    with pytest.raises(ValueError, match="step_is_not_an_outcome_fork"):
        flatten_fork_to_recorded_option(doc, 0, "dialog_upload_options")
    two = _doc()
    fork = two["skills"][0]["steps"][1]["outcome_fork"]
    fork["states"].append({**fork["states"][0], "id": "other"})
    with pytest.raises(ValueError, match="fork_has_other_states"):
        flatten_fork_to_recorded_option(two, 1, "dialog_upload_options")
    none = _doc()
    for o in _state(none)["options"]:
        o["recorded"] = False
    with pytest.raises(ValueError, match="fork_option_not_found"):
        flatten_fork_to_recorded_option(none, 1, "dialog_upload_options")


def test_a_by_input_popup_loses_its_declared_input_when_flattened():
    doc = set_fork_policy(_doc(), 1, "dialog_upload_options", {"mode": "by_input", "input": "on_popup"})
    assert any(i["id"] == "on_popup" for i in doc["inputs"])
    out = flatten_fork_to_recorded_option(doc, 1, "dialog_upload_options")
    assert not any(i["id"] == "on_popup" for i in out["inputs"])


def test_the_always_answer_becomes_a_flatten_op_and_is_offered_only_for_a_single_state_fork_with_a_recorded_click():
    q = next(q for q in clarify.generate_questions(_doc()) if q["kind"] == "fork")
    always = next(c for c in q["choices"] if c["id"] == "always")
    assert "Replace" in always["label"] and "Careful" in always["detail"] and "stop" in always["detail"]
    ops, receipt = clarify.plan_answer(q, {"choice": "always"}, _doc())
    assert ops == [{"op": "flatten_fork", "step_key": q["step_keys"][0], "state_id": "dialog_upload_options"}]
    assert receipt.startswith("A normal step")
    two = _doc()
    fork = two["skills"][0]["steps"][1]["outcome_fork"]
    fork["states"].append({**fork["states"][0], "id": "other"})
    assert all(c["id"] != "always" for q in clarify.generate_questions(two) if q["kind"] == "fork" for c in q["choices"])
