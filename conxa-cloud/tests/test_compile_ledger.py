"""BUILD-23 / BUILD-24: where a recorded action went, and no input left behind for a step that was dropped."""

from __future__ import annotations

import copy
import shutil
from contextlib import ExitStack

from conxa_compile.compiler.build import _record_drops, compile_skill_package
from conxa_compile.pipeline.run import run_pipeline
from conxa_compile.skill_package_builder_saved_skill import _drop_unreferenced_inputs
from tests.test_second_opinion import _compile_fixture, _minimal_click_event


def test_record_drops_lists_exactly_the_sequences_that_disappeared():
    before = [{"extras": {"sequence": n}, "action": {"action": a}} for n, a in [(1, "click"), (2, "click"), (3, "manual_navigate")]]
    after = [before[0]]
    ledger: list = []
    _record_drops(ledger, before, after, "cleanup")
    assert ledger == [{"sequence": 2, "pass": "cleanup", "action": "click"}, {"sequence": 3, "pass": "cleanup", "action": "manual_navigate"}]
    _record_drops(ledger, [{"extras": {}, "action": {"action": "click"}}], [], "cleanup")  # no sequence: can't be tracked
    assert len(ledger) == 2


def test_a_repeated_click_is_accounted_for_in_the_compile_report():
    first = _minimal_click_event()
    first["extras"] = {"sequence": 1}
    second = copy.deepcopy(first)
    second["extras"] = {"sequence": 2}
    evs = run_pipeline([first, second])
    data_dir, patchers = _compile_fixture("sess", evs)
    try:
        with ExitStack() as stack:
            for p in patchers:
                stack.enter_context(p)
            pkg = compile_skill_package(evs, skill_id="skill_test", source_session_id="sess", title="t", version=1)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    report = pkg.compile_report
    assert report["events_total"] == len(evs)
    # two identical clicks become one step, and the one that went away is named, not lost
    assert [d["pass"] for d in report["dropped"]] == ["cleanup"] and report["dropped"][0]["action"] == "click"
    assert report["dropped"][0]["sequence"] in (1, 2)


def test_an_input_nothing_uses_is_removed_and_named_but_a_bare_name_reference_survives():
    steps = [{"type": "for_each", "items": "files", "as": "file", "steps": [{"type": "navigate", "url": "{{base}}/{{file_id}}"}]}]
    inputs = [{"name": "base"}, {"name": "files"}, {"name": "react_select_2_listbox"}]
    warnings: list[str] = []
    kept = _drop_unreferenced_inputs(inputs, steps, warnings.append)
    assert [i["name"] for i in kept] == ["base", "files"]            # "files" is a bare loop name, still used
    assert warnings == ["The input “react_select_2_listbox” isn’t used by any step that was kept, so it was removed from the skill."]
    assert _drop_unreferenced_inputs([], steps) == []


def test_a_second_opinion_is_pinned_beside_the_recording_and_replayed_without_the_model(tmp_path, monkeypatch):
    from conxa_compile.llm import workflow_review

    calls: list[int] = []
    raw = {"goal": "Do the thing", "steps": [], "decision_points": [], "expected_end_state": {}, "suggestions": []}
    monkeypatch.setattr(workflow_review, "infer_workflow_review", lambda **k: calls.append(1) or raw)
    monkeypatch.setattr(workflow_review, "read_cached", lambda *a, **k: None)   # the shared cache was cleared
    monkeypatch.setattr(workflow_review, "write_cached", lambda *a, **k: None)
    steps = [{"key": "k1", "action": "click"}]
    pin = tmp_path / "sess" / "second_opinion.json"

    graph, _ = workflow_review.build_workflow_review(steps, page_urls=[], sidecar=pin)
    assert graph.goal == "Do the thing" and calls == [1] and pin.exists()

    graph2, _ = workflow_review.build_workflow_review(steps, page_urls=[], sidecar=pin)    # recompile
    assert graph2.goal == "Do the thing" and calls == [1]                                   # replayed, no model call

    workflow_review.build_workflow_review([{"key": "k1", "action": "type"}], page_urls=[], sidecar=pin)  # a different recording
    assert calls == [1, 1]


def test_a_dropped_value_is_kept_on_the_ledger_and_merged_passes_are_named():
    ev = {"extras": {"sequence": 4}, "action": {"action": "type", "value": "hello"}}
    ledger: list = []
    _record_drops(ledger, [ev], [], "choice_collapsed")
    assert ledger == [{"sequence": 4, "pass": "choice_collapsed", "action": "type", "value": "hello"}]


def test_clarify_asks_about_a_lost_value_but_not_one_a_merged_step_kept():
    from conxa_compile.conversation.clarify import generate_questions, plan_answer

    doc = {"skills": [{"steps": [{"action": "click", "intent": "go"}]}]}
    merged = [{"sequence": 1, "pass": "date_collapsed", "action": "select", "value": "2026"}]
    assert not [q for q in generate_questions(doc, dropped=merged, limit=None) if q["kind"] == "dropped_value"]
    lost = [{"sequence": 2, "pass": "cleanup", "action": "type", "value": "Acme Ltd"}]
    qs = [q for q in generate_questions(doc, dropped=lost, limit=None) if q["kind"] == "dropped_value"]
    assert len(qs) == 1 and "Acme Ltd" in qs[0]["body"]
    ops, receipt = plan_answer(qs[0], {"choice": "ok"}, doc)
    assert ops == [] and receipt == "Left out"


def test_the_second_opinion_cache_key_changes_with_the_model(tmp_path, monkeypatch):
    from conxa_compile.llm import workflow_review

    keys: list[str] = []
    monkeypatch.setattr(workflow_review, "read_cached", lambda ns, key, v: keys.append(key) or None)
    monkeypatch.setattr(workflow_review, "write_cached", lambda *a, **k: None)
    monkeypatch.setattr(workflow_review, "infer_workflow_review", lambda **k: None)
    steps = [{"key": "k1", "action": "click"}]
    workflow_review.build_workflow_review(steps, page_urls=[], model="a")
    workflow_review.build_workflow_review(steps, page_urls=[], model="b")
    workflow_review.build_workflow_review(steps, page_urls=[], model="a")
    assert keys[0] != keys[1] and keys[0] == keys[2]


def test_capture_mode_rearms_a_frame_that_appears_after_it_was_switched_on(monkeypatch):
    from conxa_compile.recorder.session import RecordingSession

    sess = RecordingSession(session_id="late-frame")
    monkeypatch.setattr(sess, "_ensure_bridge_installed_in_frame_sync", lambda f: None)
    sess._on_frame_ready(object())
    assert sess._capture_pending is None                       # capture was never switched on
    sess.request_capture(True)
    sess._capture_pending = None                               # the pump already applied it
    sess._on_frame_ready(object())                             # ...then a new frame appears
    assert sess._capture_pending is True
    sess._consume_payload_safe_sync({"action": {"action": "extract"}})   # one-shot: the bridge turned itself off
    sess._capture_pending = None
    sess._on_frame_ready(object())
    assert sess._capture_pending is None
