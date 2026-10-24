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
