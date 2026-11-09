"""Proof of Done, Build Studio half: the "How can we tell it worked?" question agrees to the compiler's completion
checks, a clean test run calibrates them, and a step that cannot be undone cannot be published without proof."""

import copy

import pytest

from conxa_compile.compiler.effects import build_effect, has_real_proof, person_sentence
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.conversation import clarify, scoring
from conxa_compile.editor.patch_gate import validate_editor_patch
from conxa_compile.editor.workflow_mutations import calibrate_effects, confirm_effect
from conxa_compile.policy.bundle import get_policy_bundle
from handlers.protocol import _CommandError
from handlers.test_flow import TestFlowMixin  # noqa: F401  (the calibrate helper lives on the mixin)


def _watch(*words):
    return {"progress_seen": True, "waited_ms": 3000, "final_status": [{"kind": "status", "words": list(words)}]}


def _step(action: str, text: str, effect, *, irreversible: bool = False):
    return {
        "action": {"action": action},
        "intent": text,
        "target": {"inner_text": text},
        "identity_bundle": {"stable_hash": f"h-{text}"},
        "validation": {
            "assertions": [],
            **({"wait_for": {"type": "element_appear", "target": "#done"}} if irreversible else {}),
            **({"effect": effect.model_dump()} if effect is not None else {}),
        },
        **({"consequence": "irreversible", "destructive": True, "signals": {"anchors": [{"kind": "text"}], "semantic": {"is_destructive": True}}} if irreversible else {}),
    }


def _doc(*steps):
    return {"skills": [{"steps": list(steps)}], "inputs": []}


UPLOAD = lambda: build_effect({"action": {"action": "upload_intent"}, "post_watch": _watch("upload", "complete")})  # noqa: E731
DELETE = lambda: build_effect({"action": {"action": "click"}, "target": {"inner_text": "Delete project"}}, destructive=True, consequence="irreversible")  # noqa: E731


# ── the question ────────────────────────────────────────────────────────────────────────────────


def test_a_step_that_starts_work_is_offered_as_a_tickable_way_to_tell_it_worked():
    doc = _doc(_step("upload", "Upload the file", UPLOAD()))
    choices, keys = clarify._done_when_choices(doc)
    assert [c["id"].split(":")[0] for c in choices] == ["effect"]
    assert choices[0]["label"].startswith("After ") and "upload complete" in choices[0]["label"]
    assert choices[0]["selected"] is True and keys == [step_keys(clarify._steps(doc))[0]]
    # plain words: no probe names, no selectors
    for tech in ("request_chain", "probe", "selector", "witness"):
        assert tech not in choices[0]["label"]


def test_a_step_whose_checks_were_all_agreed_to_is_not_asked_again():
    doc = confirm_effect(_doc(_step("upload", "Upload the file", UPLOAD())), 0)
    assert clarify._done_when_choices(doc)[0] == []


def test_ticking_it_agrees_to_the_checks_and_leaving_it_unticked_changes_nothing():
    doc = _doc(_step("upload", "Upload the file", UPLOAD()))
    key = step_keys(clarify._steps(doc))[0]
    q = [q for q in clarify.generate_questions(doc) if q["kind"] == "done_when"][0]
    ticked, _ = clarify.plan_answer(q, {"selected": [f"effect:{key}"]}, doc)
    assert ticked == [{"op": "confirm_effect", "step_key": key}]
    unticked, _ = clarify.plan_answer(q, {"selected": []}, doc)
    assert unticked == []


def test_a_step_that_cannot_be_undone_without_proof_makes_the_question_unskippable():
    risky = _doc(_step("click", "Delete project", DELETE(), irreversible=True))
    q = [q for q in clarify.generate_questions(risky) if q["kind"] == "done_when"][0]
    assert q["needs_proof"] is True
    assert scoring.decide(q, risky)["action"] == "ask" and scoring.decide(q, risky)["reason"] == "safety floor"
    plain = _doc(_step("upload", "Upload the file", UPLOAD()))
    q2 = [q for q in clarify.generate_questions(plain) if q["kind"] == "done_when"][0]
    assert q2["needs_proof"] is False, "an upload can be done again: it may be assumed"


def test_an_older_compile_with_no_checklists_is_not_held_to_the_rule():
    old = _doc(_step("click", "Delete project", None, irreversible=True))
    assert clarify.irreversible_without_proof(old) == []


# ── confirming and calibrating ──────────────────────────────────────────────────────────────────


def test_confirm_effect_marks_only_the_checks_that_prove_the_work_finished():
    doc = confirm_effect(_doc(_step("upload", "Upload the file", UPLOAD())), 0)
    checks = {c["probe"]["type"]: c for c in doc["skills"][0]["steps"][0]["validation"]["effect"]["checks"]}
    assert checks["request_chain"]["confirmed"] is True and checks["new_text"]["confirmed"] is True
    assert checks["input_has_files"]["confirmed"] is False, "'the file was chosen' proves nothing finished"
    assert checks["failure_signal"]["confirmed"] is False


def test_confirm_effect_refuses_a_step_with_no_checklist():
    with pytest.raises(ValueError, match="step_has_no_effect"):
        confirm_effect(_doc(_step("click", "Open", None)), 0)


def test_a_clean_test_run_calibrates_by_the_documents_own_step_key():
    doc = _doc(_step("upload", "Upload the file", UPLOAD()))
    key = step_keys(doc["skills"][0]["steps"])[0]
    new, changed = calibrate_effects(doc, {key: {"c1": "yes", "c2": "yes", "c3": "no", "c4": "no"}})
    assert changed == 1
    eff = new["skills"][0]["steps"][0]["validation"]["effect"]
    assert {c["id"]: c["calibrated"] for c in eff["checks"]} == {"c1": True, "c2": True, "c3": False, "c4": None}
    assert eff["quorum"]["witnesses"] == 2
    # nothing known about a step, nothing changes
    same, none = calibrate_effects(doc, {"nope#1": {"c1": "yes"}})
    assert none == 0 and same == doc


def test_the_mixin_calibrates_a_stored_skill_and_never_raises(monkeypatch, tmp_path):
    doc = _doc(_step("upload", "Upload the file", UPLOAD()))
    key = step_keys(doc["skills"][0]["steps"])[0]
    store = {"doc": copy.deepcopy(doc)}
    import conxa_core.storage.json_store as js

    monkeypatch.setattr(js, "read_skill", lambda sid: copy.deepcopy(store["doc"]))
    monkeypatch.setattr(js, "write_skill", lambda sid, d: store.__setitem__("doc", d))
    n = TestFlowMixin._tf_calibrate("sk1", [{"source_step_key": key, "checks": [{"id": "c1", "answer": "yes"}, {"id": "c2", "answer": "no"}]}])
    assert n == 1
    assert store["doc"]["skills"][0]["steps"][0]["validation"]["effect"]["checks"][1]["calibrated"] is False
    assert TestFlowMixin._tf_calibrate("sk1", []) == 0
    monkeypatch.setattr(js, "read_skill", lambda sid: (_ for _ in ()).throw(RuntimeError("disk")))
    assert TestFlowMixin._tf_calibrate("sk1", [{"source_step_key": key, "checks": [{"id": "c1", "answer": "yes"}]}]) == 0


# ── the publish rule ────────────────────────────────────────────────────────────────────────────


class _Handler:
    """Just enough of the workflow handler to call the publish rule on a stored document."""

    from handlers.workflows import WorkflowsMixin as _Mixin  # noqa: N813

    _require_proof_for_irreversible = _Mixin._require_proof_for_irreversible


def _publish_check(monkeypatch, doc):
    import conxa_core.storage.json_store as js

    monkeypatch.setattr(js, "read_skill", lambda sid: copy.deepcopy(doc))
    _Handler()._require_proof_for_irreversible("sk1", "invoice-flow")


def test_publish_is_blocked_for_a_step_that_cannot_be_undone_until_someone_agrees_to_its_check(monkeypatch):
    doc = _doc(_step("click", "Delete project", DELETE(), irreversible=True))
    with pytest.raises(_CommandError) as err:
        _publish_check(monkeypatch, doc)
    assert err.value.code == "irreversible_step_requires_proof"
    assert "Delete project" in err.value.message and "How can we tell it worked?" in err.value.message
    for tech in ("effect", "probe", "request_chain", "step_key"):
        assert tech not in err.value.message
    # agreeing to it in chat opens the gate
    _publish_check(monkeypatch, confirm_effect(doc, 0))


def test_a_check_a_test_run_proved_also_opens_the_gate_but_a_dead_one_does_not(monkeypatch):
    doc = _doc(_step("click", "Delete project", DELETE(), irreversible=True))
    key = step_keys(doc["skills"][0]["steps"])[0]
    alive, _ = calibrate_effects(doc, {key: {"c1": "yes"}})
    _publish_check(monkeypatch, alive)
    dead, _ = calibrate_effects(doc, {key: {"c1": "no"}})
    with pytest.raises(_CommandError):
        _publish_check(monkeypatch, dead)


def test_an_upload_can_be_published_without_agreement_because_it_can_be_done_again(monkeypatch):
    _publish_check(monkeypatch, _doc(_step("upload", "Upload the file", UPLOAD())))


def test_publish_still_works_for_a_pack_with_no_checklists_at_all(monkeypatch):
    _publish_check(monkeypatch, _doc(_step("click", "Delete project", None, irreversible=True)))


def test_strict_proof_needs_a_test_or_the_authors_word():
    eff = UPLOAD().model_dump()
    assert has_real_proof(eff) and not has_real_proof(eff, strict=True)
    assert has_real_proof(confirm_effect(_doc(_step("upload", "x", UPLOAD())), 0)["skills"][0]["steps"][0]["validation"]["effect"], strict=True)
    assert person_sentence(eff) == "the app confirms it received it and you see “upload complete” on the screen"


# ── the patch gate ──────────────────────────────────────────────────────────────────────────────


def _gate(step, patch):
    validate_editor_patch(step, patch, get_policy_bundle().data)


def test_a_hand_edit_cannot_leave_a_broken_checklist():
    step = _step("click", "Delete project", DELETE(), irreversible=True)
    with pytest.raises(ValueError, match="effect_is_not_a_valid_checklist"):
        _gate(step, {"validation": {"effect": {"kind": "teleport", "checks": []}}})


def test_a_hand_edit_cannot_strip_the_check_that_proves_an_irreversible_step_finished():
    step = _step("click", "Delete project", DELETE(), irreversible=True)
    only_veto = DELETE().model_dump()
    only_veto["checks"] = [c for c in only_veto["checks"] if c["role"] == "veto"]
    with pytest.raises(ValueError, match="irreversible_step_requires_completion_check"):
        _gate(step, {"validation": {"effect": only_veto}})
