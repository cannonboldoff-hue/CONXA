"""E2E16-6a: question cards and answers. A first-read note names what it settles from a closed list; twin rows are
said before a person agrees to them; an unticked completion check stops counting; an old package still loads."""

from __future__ import annotations

from conxa_core.models.skill_spec import EntityBinding, SkillStep

from conxa_compile.compiler.effects import has_real_proof
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.conversation import clarify
from conxa_compile.conversation import timeline_understanding as TU
from conxa_compile.editor.workflow_mutations import retire_effect

from tests.test_timeline_understanding import MERGED, raw_answer


def _note(**over):
    return {"effect": "note", "row": "r3", "prompt": "How many entries should it add each time?", "cost_rank": 1,
            "choices": [{"label": "Five, like this recording", "means": "same"},
                        {"label": "A number given each time", "means": "list"}], **over}


def _validated(*notes):
    return TU.validate(raw_answer(questions=list(notes)), MERGED, TU.candidates_from_timeline(MERGED))


# ── BUILD-93 (19), BUILD-108 (2): a first-read note's facet ────────────────────────────────


def test_a_repeat_note_keeps_its_facet_and_each_options_meaning():
    q = _validated(_note(facet="repeat"))["questions"][0]
    assert q["facet"] == "repeat"
    assert [(c["id"], c["means"]) for c in q["choices"]] == [("c1", "same"), ("c2", "list")]
    # carried to the pre-build card, so the answer can be mapped by code after the build
    pre = TU.questions_from_understanding({"questions": [q], "candidates": []}, MERGED)[0]
    assert pre["facet"] == "repeat" and [c["means"] for c in pre["choices"]] == ["same", "list"]


def test_a_repeat_note_whose_options_are_not_one_same_and_one_list_stays_a_plain_note():
    for choices in ([{"label": "Five", "means": "same"}, {"label": "Ten", "means": "same"}],
                    [{"label": "Five", "means": "same"}, {"label": "A number", "means": "maybe"}]):
        out = _validated(_note(facet="repeat", choices=choices))
        q = out["questions"][0]
        assert "facet" not in q and not any("means" in c for c in q["choices"])
    assert out["validation_report"]["shape"]["repeat_without_means"]["count"] == 1


def test_a_note_takes_only_a_facet_from_the_closed_list_and_never_a_meaning_tag_without_repeat():
    out = _validated(_note(facet="alternatives"), _note(row="r5", facet="whatever"))
    by_row = {q["row"]: q for q in out["questions"]}
    assert by_row["r3"]["facet"] == "alternatives" and not any("means" in c for c in by_row["r3"]["choices"])
    assert "facet" not in by_row["r5"]
    assert out["validation_report"]["shape"]["unknown_facet"]["example"] == "whatever"


# ── BUILD-109 (1): twin rows ───────────────────────────────────────────────────────────────


def _bound_doc(matches):
    step = {"action": {"action": "click"}, "intent": "delete_row", "consequence": "irreversible",
            "entity_binding": {"container_selector": "tbody > tr", "identifier": "{{person}}", "source": "input",
                               "recorded_matches": matches}}
    return {"skills": [{"steps": [step]}], "inputs": [{"id": "person", "type": "text", "default": "Carol"}]}


def test_twin_rows_on_the_recorded_page_are_said_in_the_row_questions():
    qs = {q["kind"]: q for q in clarify.generate_questions(_bound_doc(2), limit=None)}
    warning = "On the page you recorded, more than one row shows “Carol”, so a run looking for “Carol” stops every time."
    assert warning in qs["confirm_row"]["body"] and warning in qs["missing_item"]["body"]
    qs = {q["kind"]: q for q in clarify.generate_questions(_bound_doc(1), limit=None)}
    assert "more than one row shows" not in qs["confirm_row"]["body"]


def test_an_old_package_without_recorded_matches_still_loads():
    eb = EntityBinding.model_validate({"container_selector": "tbody > tr", "identifier": "Carol", "confirmed": True})
    assert eb.recorded_matches == 0
    step = SkillStep.model_validate({"action": "click", "intent": "edit", "target": {"primary_selector": "#e"},
                                     "entity_binding": {"container_selector": "tbody > tr", "identifier": "Carol"}})
    assert step.entity_binding.recorded_matches == 0


# ── BUILD-103 (2): an unticked completion check is retired ─────────────────────────────────


def _effect_doc():
    step = {"action": {"action": "click"}, "intent": "upload_files", "validation": {"assertions": [], "effect": {
        "kind": "upload", "checks": [
            {"id": "c1", "rung": "completed", "witness": "renderer", "probe": {"type": "new_text", "words": ["Uploaded"]},
             "plain": "you see “Uploaded”", "calibrated": True, "calibration_basis": ["c1"]},
            {"id": "v1", "rung": "completed", "witness": "renderer", "role": "veto", "probe": {"type": "failure_signal"}}]}}}
    return {"skills": [{"steps": [step]}], "inputs": []}


def test_only_the_persons_untick_plans_retiring_a_completion_check():
    doc = _effect_doc()
    q = next(q for q in clarify.generate_questions(doc, limit=None) if q["kind"] == "done_when")
    key = step_keys(doc["skills"][0]["steps"])[0]
    assert f"effect:{key}" in [c["id"] for c in q["choices"]]
    ops, _ = clarify.plan_answer(q, {"selected": []}, doc)
    assert {"op": "retire_effect", "step_key": key} in ops
    ops, _ = clarify.plan_answer(q, {"selected": [f"effect:{key}"]}, doc)
    assert not [o for o in ops if o["op"] == "retire_effect"]
    # a default the harness assumed (nobody unticked anything) retires nothing
    ops, _ = clarify.plan_answer(q, {"selected": [], "assume": True}, doc)
    assert not [o for o in ops if o["op"] == "retire_effect"]


def test_a_retired_check_no_longer_proves_the_step_nor_is_listed_and_a_veto_is_kept():
    doc = retire_effect(_effect_doc(), 0)
    step = doc["skills"][0]["steps"][0]
    checks = {c["id"]: c for c in step["validation"]["effect"]["checks"]}
    assert (checks["c1"]["calibrated"], checks["c1"]["calibration_basis"], checks["c1"]["confirmed"]) == (False, ["author_unticked"], False)
    assert "calibrated" not in checks["v1"]                         # a failure signal still counts
    assert not has_real_proof(step["validation"]["effect"])
    assert clarify.step_checks(step) == []
    # the question asked again offers nothing to agree to for that step
    assert not [q for q in clarify.generate_questions(doc, limit=None) if q["kind"] == "done_when"]


# ── BUILD-101 (7): "Always the first result?" ──────────────────────────────────────────────


def _place_doc(position=1, confirmed=False):
    step = {"action": "extract", "intent": "read_price", "value": "price", "extract": {"source": "text"},
            "entity_binding": {"container_selector": "ul.results > li.result", "identifier": "", "position": position,
                               "confirmed": confirmed}}
    return {"skills": [{"steps": [step]}], "inputs": []}


def test_a_value_read_from_a_place_in_a_list_asks_always_that_place_and_only_yes_turns_it_on():
    q = next(q for q in clarify.generate_questions(_place_doc(), limit=None) if q["kind"] == "pick_match")
    assert q["title"] == "Always the first result?" and q["position"] == 1
    assert [c["id"] for c in q["choices"]] == ["yes", "no"]
    key = step_keys(_place_doc()["skills"][0]["steps"])[0]
    ops, receipt = clarify.plan_answer(q, {"choice": "yes"}, _place_doc())
    assert ops == [{"op": "patch", "step_key": key, "patch": {"entity_binding": {"confirmed": True}}}]
    assert receipt == "Always reads the first result"
    assert clarify.plan_answer(q, {"choice": "no"}, _place_doc()) == ([], "Finds it the way it was recorded")
    # the wording follows the recorded place; a confirmed one is not asked again
    third = next(q for q in clarify.generate_questions(_place_doc(3), limit=None) if q["kind"] == "pick_match")
    assert third["title"] == "Always the third result?"
    assert not any(q["kind"] == "pick_match" for q in clarify.generate_questions(_place_doc(confirmed=True), limit=None))


def test_build_117_the_yes_answer_passes_the_patch_gate_and_chat_may_send_only_the_flag():
    import pytest

    from conxa_compile.editor.capability_manifest import ADDRESS_KEYS, chat_may_write, editable_fields
    from conxa_compile.editor.copilot_proposals import gate_proposals
    from conxa_compile.editor.patch_gate import validate_editor_patch

    step = _place_doc()["skills"][0]["steps"][0]
    q = next(q for q in clarify.generate_questions(_place_doc(), limit=None) if q["kind"] == "pick_match")
    (op,), _ = clarify.plan_answer(q, {"choice": "yes"}, _place_doc())
    validate_editor_patch(step, op["patch"], {})          # was refused: "Only the target, the value's name and ..."
    # Chat can say "yes, always the first one" too (BUILD-117 (2) residue): the same flag, and only the flag.
    assert chat_may_write("extract", op["patch"])
    assert not editable_fields("extract") & ADDRESS_KEYS
    for patch in ({"entity_binding": {"confirmed": True, "container_selector": "table tr"}},
                  {"entity_binding": {"position": 2}}, {"target": {"primary_selector": "li"}}):
        assert not chat_may_write("extract", patch), patch
    key = step_keys(_place_doc()["skills"][0]["steps"])[0]
    (prop,) = gate_proposals(_place_doc(), [{"step_key": key, "field": "entity_binding.confirmed", "patch": True}])
    assert prop["patch"] == {"entity_binding": {"confirmed": True}}
    for patch in ({"entity_binding": {"confirmed": True, "container_selector": "table tr"}},
                  {"entity_binding": {"position": 2}}):
        with pytest.raises(ValueError, match="extract_step_allows_only"):
            validate_editor_patch(step, patch, {})        # only the flag, never where the binding points
    with pytest.raises(ValueError, match="extract_step_allows_only"):
        validate_editor_patch({**step, "entity_binding": None}, op["patch"], {})  # nothing code wrote to confirm


def test_a_place_binding_is_shipped_to_the_runtime():
    from conxa_compile.skill_package_builder_saved_skill import _copy_saved_common

    out = _copy_saved_common(_place_doc()["skills"][0]["steps"][0], {})
    assert out["entity_binding"]["position"] == 1


def test_build_117_a_message_s_close_button_is_not_part_of_its_words():
    from conxa_compile.compiler.state_validation import merge_dom_diff_evidence

    ev = {"state_change": {"result_diff": {"added": ["You logged out of the secure area! ×", "Saved ✕", "×"]}}}
    merged = merge_dom_diff_evidence(ev, {"new_elements": [], "removed_elements": [], "text_change": []})
    assert merged["text_change"] == ["You logged out of the secure area!", "Saved"]
