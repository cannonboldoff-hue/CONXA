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
    assert _validate_effects("nonsense", steps_context=ctx) == []


def test_a_judgement_never_touches_the_address():
    step = _step("click", "Save")
    before = step.model_dump(mode="json")
    _judge(step, reversible=False, kind="saves", confidence=0.9, reason="")
    after = step.model_dump(mode="json")
    for key in ("target", "identity_bundle", "compiled_selectors"):
        assert before[key] == after[key]
