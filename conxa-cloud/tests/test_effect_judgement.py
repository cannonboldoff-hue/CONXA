"""W10: a model judges whether a step can be undone, from what the step means; code enforces the label and fails safe.
No word list decides a step that was judged."""

from __future__ import annotations

from conxa_compile.compiler.action_semantics import commit_intent_hit, judged_irreversible
from conxa_compile.compiler.destructive_semantics import classify_consequence, destructive_compiler_step
from conxa_compile.compiler.second_opinion import apply_effect_judgements
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.llm.workflow_semantics import _validate_effects
from conxa_core.models.skill_spec import SkillStep

POLICY: dict = {"workflow": {"submit_text_tokens": ["submit", "place order", "pay"], "navigation_text_tokens": ["continue"]}}


def _step(action: str, text: str, **semantic) -> SkillStep:
    return SkillStep(action={"action": action}, intent=text.lower().replace(" ", "_"),
                     target={"inner_text": text, "tag": "button"}, signals={"semantic": dict(semantic)})


def _view(step: SkillStep) -> dict:
    d = step.model_dump(mode="json")
    d["semantic"] = d["signals"]["semantic"]
    return d


def _judge(step: SkillStep, **fields) -> dict:
    apply_effect_judgements([step], [{"step_key": step_keys([step])[0], **fields}])
    return _view(step)


def test_the_models_answer_beats_the_words_on_the_button():
    # "Finish" only closes a pop-up; "Submit" here only goes to the next page. Both judged reversible.
    for text in ("Finish", "Submit"):
        view = _judge(_step("click", text), reversible=True, kind="moves_forward", confidence=0.9, reason="")
        assert commit_intent_hit(view, POLICY) is False
        assert destructive_compiler_step(view, POLICY) is False
        assert classify_consequence(view, POLICY) == "reversible"


def test_a_harmless_looking_ok_is_a_delete_when_the_model_says_so():
    view = _judge(_step("dialog_accept", "OK"), reversible=False, kind="deletes", confidence=0.9, reason="confirms the delete")
    assert classify_consequence(view, POLICY) == "irreversible"


def test_an_unsure_answer_counts_as_cannot_be_undone():
    view = _judge(_step("click", "Maybe"), reversible=True, kind="other", confidence=0.3, reason="")
    assert judged_irreversible(view) is True


def test_the_persons_answer_beats_the_model_and_survives_a_rebuild():
    step = _step("click", "Save")
    _judge(step, reversible=False, kind="saves", confidence=0.95, reason="")
    step.signals["semantic"]["effect_judgement"]["person"] = "reversible"
    view = _judge(step, reversible=False, kind="saves", confidence=0.95, reason="")  # a rebuild judges again
    assert judged_irreversible(view) is False


def test_a_step_no_one_judged_is_left_to_the_older_rules():
    assert judged_irreversible(_view(_step("click", "Place order"))) is None
    assert commit_intent_hit(_view(_step("click", "Place order")), POLICY) is True


def test_effects_are_checked_before_they_are_kept():
    ctx = [{"key": "a#0", "action": "click"}, {"key": "b#0", "action": "scroll"}]
    out = _validate_effects([
        {"step_key": "a#0", "reversible": True, "kind": "opens", "confidence": 7, "reason": "x" * 500},
        {"step_key": "a#0", "reversible": False},                    # a second answer for one step
        {"step_key": "b#0", "reversible": True},                      # a scroll is not judged
        {"step_key": "zzz", "reversible": True},                      # not a step of this workflow
        {"step_key": "a#0", "reversible": "yes"},                     # not a yes/no
    ], steps_context=ctx)
    assert len(out) == 1 and out[0]["confidence"] == 1.0 and len(out[0]["reason"]) == 200 and out[0]["kind"] == "opens"


def test_a_step_the_model_left_out_counts_as_cannot_be_undone():
    """WF-9 F-4: no answer for "Send card" used to leave it to the word lists, which called it reversible."""
    ctx = [{"key": "a#0", "action": "click"}, {"key": "b#0", "action": "scroll"}]
    for raw in ("nonsense", None, []):
        out = _validate_effects(raw, steps_context=ctx)
        assert [e["step_key"] for e in out] == ["a#0"], raw  # the scroll is never judged
        assert out[0]["reversible"] is None and out[0]["confidence"] == 0.0
    step = _step("click", "Send card")
    view = _judge(step, **{k: v for k, v in _validate_effects([], steps_context=[{"key": step_keys([step])[0], "action": "click"}])[0].items() if k != "step_key"})
    assert judged_irreversible(view) is True


def test_a_judgement_never_touches_the_address():
    step = _step("click", "Save")
    before = step.model_dump(mode="json")
    _judge(step, reversible=False, kind="saves", confidence=0.9, reason="")
    after = step.model_dump(mode="json")
    for key in ("target", "identity_bundle", "compiled_selectors"):
        assert before[key] == after[key]


def test_a_step_judged_cannot_be_undone_ships_as_destructive_to_the_runtime():
    """WF-9 F-4: the runtime refuses to click such a step twice only if the package says it cannot be undone."""
    from conxa_compile.skill_package_builder_saved_skill import _copy_saved_common

    unsure = _step("click", "Send card")
    sure = _step("click", "Next")
    _judge(unsure, reversible=None, kind="other", confidence=0.0, reason="not judged")
    _judge(sure, reversible=True, kind="moves_forward", confidence=0.9, reason="")
    assert _copy_saved_common(unsure.model_dump(mode="json"), {}).get("destructive") is True
    assert "destructive" not in _copy_saved_common(sure.model_dump(mode="json"), {})


def _compile_with_effects(effects_for):
    """Compile a one-click recording with a canned review answer whose `effects` come from effects_for(click_key)."""
    import shutil
    from contextlib import ExitStack
    from unittest.mock import patch

    from conxa_compile.compiler.build import compile_skill_package
    from conxa_compile.pipeline.run import run_pipeline
    from conxa_core.models.skill_spec import WorkflowIntentGraph
    from tests.test_second_opinion import _compile_fixture, _minimal_click_event

    evs = run_pipeline([_minimal_click_event()])
    data_dir, patchers = _compile_fixture("sess", evs)

    def fake_review(inputs, **kw):
        key = [s["key"] for s in inputs if s.get("action") == "click"][-1]
        kw["extras_out"]["effects"] = effects_for(key)
        return WorkflowIntentGraph(), []

    try:
        with ExitStack() as stack:
            for p in patchers:
                stack.enter_context(p)
            stack.enter_context(patch("conxa_compile.llm.workflow_review.build_workflow_review", side_effect=fake_review))
            pkg = compile_skill_package(evs, skill_id="skill_test", source_session_id="sess", title="t", version=1)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    return pkg.skills[0].steps[-1]


def test_the_compiled_step_reads_the_models_judgement_into_its_consequence():
    """WF-9 round 2: `consequence` was set before the model judged, so a "Send card" judged irreversible still said reversible."""
    unjudged_word = lambda k: [{"step_key": k, "reversible": False, "kind": "sends", "confidence": 0.9, "reason": "sends"}]  # noqa: E731
    reversible = lambda k: [{"step_key": k, "reversible": True, "kind": "moves_forward", "confidence": 0.9, "reason": "next"}]  # noqa: E731
    assert _compile_with_effects(unjudged_word).consequence == "irreversible"
    assert _compile_with_effects(reversible).consequence == "reversible"


def test_an_unanswered_field_fill_is_not_called_irreversible_but_a_click_still_is():
    ctx = [{"key": f"{a}#0", "action": a} for a in ("set_radio", "set_checkbox", "select", "select_option", "click")]
    assert [e["step_key"] for e in _validate_effects([], steps_context=ctx)] == ["click#0"]


def test_an_unanswered_date_pick_is_not_called_irreversible():
    ctx = [{"key": "a#0", "action": "click"}, {"key": "d#0", "action": "date_pick"}]
    assert [e["step_key"] for e in _validate_effects([], steps_context=ctx)] == ["a#0"]
    kept = _validate_effects([{"step_key": "d#0", "reversible": False, "kind": "other", "confidence": 0.9}], steps_context=ctx)
    assert any(e["step_key"] == "d#0" and e["reversible"] is False for e in kept)  # a real answer still counts


def _destructive_click() -> dict:
    return {
        "action": {"action": "click"}, "intent": "send_card",
        "target": {"primary_selector": "#send", "fallback_selectors": []},
        "signals": {"anchors": [{"element": "h1", "relation": "above"}],
                    "semantic": {"effect_judgement": {"reversible": False, "kind": "sends", "confidence": 0.9, "reason": ""}}},
        "validation": {"wait_for": {"type": "none"}, "assertions": []},
    }


def test_a_patch_that_adds_the_outcome_check_to_an_irreversible_step_is_accepted():
    """WF-9 F-8: the gate refused any patch to a step with no wait, even the patch that gives it a result check."""
    import pytest
    from conxa_compile.editor.patch_gate import validate_editor_patch

    step = _destructive_click()
    adds = {"validation": {"wait_for": {"type": "none"}, "assertions": [
        {"type": "text_present", "target": "Card ready for", "required": True, "timeout_ms": 5000}]}}
    validate_editor_patch(step, adds, POLICY)  # no longer raises
    with pytest.raises(ValueError, match="destructive_step_requires_non_none_wait_for"):
        validate_editor_patch(step, {"intent": "send_the_card"}, POLICY)  # no proof added: still refused
    proofs = {"validation": {"wait_for": {"type": "none"}, "assertions": [
        {"type": "text_present", "target": f"Proof {i}", "required": True, "timeout_ms": 5000} for i in range(6)],
        "effect": {"kind": "send", "checks": [{"id": "c1", "role": "support", "probe": {"type": "new_text"}, "plain": "it was sent"}]}}}
    validate_editor_patch(step, proofs, POLICY)
