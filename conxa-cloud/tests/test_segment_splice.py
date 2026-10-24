"""Gap analysis G6/G10: re-record from a step onward — splice, keys, answers — and the failure image."""

from __future__ import annotations

import copy
import importlib.util
import os
import shutil
from contextlib import ExitStack

import pytest

from conxa_compile.compiler.build import compile_skill_package
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.conversation import clarify_store
from conxa_compile.editor.workflow_mutations import replace_step_range
from conxa_compile.pipeline.run import run_pipeline
from conxa_core.config import settings
from tests.test_second_opinion import _compile_fixture, _minimal_click_event


def _step(intent, **kw):
    s = {"action": {"action": "click"}, "intent": intent, "identity_bundle": {"stable_hash": intent}, "validation": {"assertions": []}}
    s.update(kw)
    return s


def _doc(*names):
    return {"meta": {"version": 3}, "inputs": [], "intent_graph": {"goal": "g", "steps": [{"index": i, "intent": n} for i, n in enumerate(names)]},
            "compile_report": {"status": "ok", "steps": [{"warnings": []}] * len(names)},
            "skills": [{"steps": [_step(n) for n in names]}]}


def _intents(doc):
    return [s["intent"] for s in doc["skills"][0]["steps"]]


# ── the splice ──────────────────────────────────────────────────────────────────────────────


def test_steps_before_the_splice_keep_their_keys_and_the_rest_are_replaced():
    doc = _doc("open", "search", "pick", "save")
    before = step_keys(doc["skills"][0]["steps"])
    out = replace_step_range(doc, 2, [_step("choose_other"), _step("confirm"), _step("finish")])
    assert _intents(out) == ["open", "search", "choose_other", "confirm", "finish"]
    assert step_keys(out["skills"][0]["steps"])[:2] == before[:2]                     # untouched, same identity
    assert out["meta"]["version"] == 4 and doc["meta"]["version"] == 3                # the original is not mutated
    assert out["compile_report"]["steps"] == []                                       # per-step confidence no longer lines up
    assert [g["intent"] for g in out["intent_graph"]["steps"]] == ["open", "search"]  # replaced steps' graph rows are gone, not misattributed


def test_a_middle_range_shifts_what_follows():
    out = replace_step_range(_doc("a", "b", "c", "d", "e"), 1, [_step("x")], end=2)
    assert _intents(out) == ["a", "x", "d", "e"]
    assert [(g["index"], g["intent"]) for g in out["intent_graph"]["steps"]] == [(0, "a"), (2, "d"), (3, "e")]


def test_a_bad_range_or_bad_steps_are_refused_without_touching_anything():
    doc = _doc("a", "b")
    for args in ((5, [_step("x")]), (-1, [_step("x")])):
        with pytest.raises(ValueError, match="step_range_out_of_bounds"):
            replace_step_range(doc, *args)
    with pytest.raises(ValueError, match="new_steps_must_be_steps"):
        replace_step_range(doc, 1, ["not a step"])  # type: ignore[list-item]
    assert _intents(doc) == ["a", "b"]


def test_new_placeholders_in_the_segment_are_declared_as_inputs():
    seg = _step("type_name", value="{{customer}}", input_binding="customer", action={"action": "type"})
    out = replace_step_range(_doc("a", "b"), 1, [seg])
    assert [i["id"] for i in out["inputs"]] == ["customer"]


# ── answers about replaced steps ────────────────────────────────────────────────────────────


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def test_answers_about_surviving_steps_are_kept_and_the_rest_flagged_stale(data_dir):
    clarify_store.save("w1", {"questions": [
        {"id": "keep", "status": "answered", "step_keys": ["a#1"]},
        {"id": "gone", "status": "answered", "step_keys": ["z#1"]},
        {"id": "open_gone", "status": "open", "step_keys": ["z#1"]},
        {"id": "global", "status": "answered", "step_keys": []},
    ]})
    assert clarify_store.mark_stale("w1", {"a#1"}) == 2
    qs = {q["id"]: q for q in clarify_store.load("w1")["questions"]}
    assert not qs["keep"].get("stale") and not qs["global"].get("stale")
    assert qs["gone"]["stale"] is True and qs["gone"]["status"] == "answered"
    assert qs["open_gone"]["status"] == "skipped" and "re-recorded" in qs["open_gone"]["receipt"]
    assert clarify_store.mark_stale("w1", {"a#1"}) == 0                              # idempotent


# ── compiling a segment ─────────────────────────────────────────────────────────────────────


def _first_action(pkg):
    s = pkg.skills[0].steps[0]
    return s.action if isinstance(s.action, str) else s.action.get("action")


def test_a_segment_has_no_synthetic_start_page_but_a_whole_recording_does():
    ev = _minimal_click_event()
    ev["extras"] = {"sequence": 1}
    results = {}
    for segment in (False, True):
        evs = run_pipeline([copy.deepcopy(ev)])
        d, patchers = _compile_fixture("sess", evs)
        try:
            with ExitStack() as stack:
                for p in patchers:
                    stack.enter_context(p)
                results[segment] = compile_skill_package(evs, skill_id="s", source_session_id="sess", title="t", version=1, segment=segment)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    assert _first_action(results[False]) == "navigate" and _first_action(results[True]) == "click"


# ── the command ─────────────────────────────────────────────────────────────────────────────


def test_splice_segment_replaces_from_a_step_is_undoable_and_flags_stale_answers(data_dir):
    py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
    spec = importlib.util.spec_from_file_location("cbackend_splice", os.path.join(py, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    b = mod.Backend()
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow
    from handlers.protocol import _CommandError

    wf = create_workflow("W", "https://x.test")
    w = get_workflow(wf.id)
    w.skill_id = "skill_w"
    save_workflow(w)
    doc = _doc("open", "search", "pick", "save")
    write_skill("skill_w", doc)
    write_skill("skill_seg", {"meta": {"version": 1}, "inputs": [], "skills": [{"steps": [_step("choose_other"), _step("finish")]}]})
    keys = step_keys(doc["skills"][0]["steps"])
    clarify_store.save(wf.id, {"questions": [{"id": "q1", "status": "answered", "step_keys": [keys[3]]},
                                             {"id": "q2", "status": "answered", "step_keys": [keys[0]]}]})

    out = b.cmd_splice_segment({"skill_id": "skill_w", "segment_skill_id": "skill_seg", "from_step_key": keys[2]}, "r")
    assert out["replaced_from"] == 2 and out["added"] == 2 and out["stale_answers"] == 1
    assert _intents(read_skill("skill_w")) == ["open", "search", "choose_other", "finish"]
    qs = {q["id"]: q for q in clarify_store.load(wf.id)["questions"]}
    assert qs["q1"].get("stale") and not qs["q2"].get("stale")

    b.cmd_undo_workflow({"skill_id": "skill_w"}, "r")
    assert _intents(read_skill("skill_w")) == ["open", "search", "pick", "save"]

    with pytest.raises(_CommandError):
        b.cmd_splice_segment({"skill_id": "skill_w", "segment_skill_id": "skill_seg", "from_step_key": "nope#1"}, "r")


# ── stopping at a step, and the failure picture ─────────────────────────────────────────────


def test_a_runtime_that_parked_before_the_asked_step_is_recognised_from_meta_only():
    from handlers.workflows import _extract_stopped_at

    parked = {"content": [{"type": "text", "text": "Stopped before step 7"}],
              "_meta": {"conxa/stopped_at": {"step_index": 6, "step_intent": "click_download", "run_id": "r1"}}}
    assert _extract_stopped_at(parked) == {"step_index": 6, "step_intent": "click_download", "run_id": "r1"}
    assert _extract_stopped_at({"content": [{"type": "text", "text": "Done."}]}) is None
    assert _extract_stopped_at({"_meta": {"conxa/stopped_at": {"step_index": "6"}}}) is None


def test_the_failure_picture_address_exposes_only_a_run_id_and_a_file_name(data_dir, monkeypatch):
    py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
    spec = importlib.util.spec_from_file_location("cbackend_img", os.path.join(py, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    b = mod.Backend()
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow

    wf = create_workflow("W", "https://x.test")
    w = get_workflow(wf.id)
    w.skill_id, w.last_test_run_id = "skill_w", "r_abc123"
    save_workflow(w)
    shot = r"C:\Users\someone\AppData\Local\Conxa\sandbox\data\runs\r_abc123\_evidence\failure.jpg"
    monkeypatch.setattr("conxa_compile.editor.evidence.build_evidence_bundle",
                        lambda *a, **k: {"runtime_evidence": {"failure_screenshot_path": shot}})
    assert b._tf_failure_image(wf.id) == "conxa-asset://local/sandbox-evidence/r_abc123/_evidence/failure.jpg"
    monkeypatch.setattr("conxa_compile.editor.evidence.build_evidence_bundle",
                        lambda *a, **k: {"runtime_evidence": {"failure_screenshot_path": None}})
    assert b._tf_failure_image(wf.id) is None
    monkeypatch.setattr("conxa_compile.editor.evidence.build_evidence_bundle",
                        lambda *a, **k: {"runtime_evidence": {"failure_screenshot_path": r"C:\secrets\auth.json"}})
    assert b._tf_failure_image(wf.id) is None                       # not evidence: never turned into an address


def test_the_fix_proposal_and_the_chat_diagnosis_get_the_failure_screenshot(monkeypatch):
    from conxa_compile.conversation import harness

    seen: list = []
    monkeypatch.setattr("conxa_compile.llm.copilot.copilot_turn", lambda **k: seen.append(k.get("screenshot_path")) or {"proposals": []})
    pack = type("P", (), {"text": "ctx"})()
    digest = {"runtime_evidence": {"failure_screenshot_path": "/x/failure.jpg"}}
    harness._propose(message="fix", base_pack=pack, digest=digest, skill_id="s", gate_proposals=lambda r: r,
                     cancel=__import__("threading").Event(), with_failure_image=True)
    harness._propose(message="fix", base_pack=pack, digest=digest, skill_id="s", gate_proposals=lambda r: r,
                     cancel=__import__("threading").Event(), with_failure_image=False)
    assert seen == ["/x/failure.jpg", None]                         # only when the last test failed
