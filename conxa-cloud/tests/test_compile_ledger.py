"""BUILD-23 / BUILD-24: where a recorded action went, and no input left behind for a step that was dropped."""

from __future__ import annotations

import copy
import shutil
from contextlib import ExitStack
from unittest.mock import patch

from conxa_core.config import settings

from conxa_compile.compiler.build import _record_drops, compile_skill_package
from conxa_compile.pipeline.run import run_pipeline
from conxa_compile.skill_package_builder_saved_skill import _drop_unreferenced_inputs
from tests.test_second_opinion import _compile_fixture, _minimal_click_event


def test_record_drops_lists_exactly_the_sequences_that_disappeared():
    before = [{"extras": {"sequence": n}, "action": {"action": a}} for n, a in [(1, "click"), (2, "click"), (3, "manual_navigate")]]
    after = [before[0]]
    ledger: list = []
    _record_drops(ledger, before, after, "cleanup")
    # each dropped action also says which kept action it sat right after
    assert ledger == [{"sequence": 2, "pass": "cleanup", "action": "click", "after_sequence": 1},
                      {"sequence": 3, "pass": "cleanup", "action": "manual_navigate", "after_sequence": 1}]
    _record_drops(ledger, [{"extras": {}, "action": {"action": "click"}}], [], "cleanup")  # no sequence: can't be tracked
    assert len(ledger) == 2


@patch.object(settings, "compile_keep_every_action", True)
def test_keep_every_action_turns_two_identical_clicks_into_two_steps():
    first = _minimal_click_event()
    first["extras"] = {"sequence": 1}
    second = copy.deepcopy(first)
    second["extras"] = {"sequence": 2}
    evs = run_pipeline([first, second])
    assert len(evs) == 2
    data_dir, patchers = _compile_fixture("sess", evs)
    try:
        with ExitStack() as stack:
            for p in patchers:
                stack.enter_context(p)
            pkg = compile_skill_package(evs, skill_id="skill_test", source_session_id="sess", title="t", version=1)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    assert not pkg.compile_report.get("dropped")
    assert [s.action for s in pkg.skills[0].steps].count("click") == 2


@patch.object(settings, "compile_keep_every_action", False)
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


def test_clarify_does_not_ask_about_words_typed_twice_that_the_workflow_still_types():
    """Wikipedia re-reports the search box on Enter, so "Ada Lovelace" is recorded twice; clean-up folds the second into
    the first step. Its words are still the input's recorded value, so nothing was left out."""
    from conxa_compile.conversation.clarify import generate_questions

    doc = {"inputs": [{"id": "search_wikipedia", "default": "Ada Lovelace"}],
           "skills": [{"steps": [{"action": {"action": "type"}, "value": "{{search_wikipedia}}", "input_binding": "search_wikipedia"}]}]}
    twice = [{"sequence": 4, "pass": "cleanup", "action": "type", "value": "Ada Lovelace"}]
    assert not [q for q in generate_questions(doc, dropped=twice, limit=None) if q["kind"] == "dropped_value"]
    # As saved by Studio: the input has no default; the kept step's recorded event holds the words.
    doc["inputs"] = [{"id": "search_wikipedia", "default": None}]
    recorded = {"action": {"action": "type", "value": "Ada Lovelace"}}
    assert not [q for q in generate_questions(doc, dropped=twice, limit=None, source_lookup=lambda _s: recorded)
                if q["kind"] == "dropped_value"]
    assert [q for q in generate_questions(doc, dropped=twice, limit=None) if q["kind"] == "dropped_value"]


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


def test_pipeline_drops_names_what_the_pipeline_removed_and_never_a_value():
    from conxa_compile.pipeline.run import pipeline_drops

    def ev(seq, action, value=None):
        e = _minimal_click_event()
        e["extras"] = {"sequence": seq}
        e["action"] = {"action": action, **({"value": value} if value else {})}
        return e

    raw = [ev(1, "scroll"), ev(2, "scroll"), ev(3, "click"), ev(4, "type", "hunter2")]
    normalized = [raw[0], raw[2], raw[3]]
    rows = pipeline_drops(raw, normalized)
    assert rows == [{"sequence": 2, "pass": "upstream_dedupe", "action": "scroll"}]
    # an event with no sequence cannot be tracked, so it is not listed
    raw.append({"extras": {}, "action": {"action": "hover"}})
    assert len(pipeline_drops(raw, normalized)) == 1


def test_pipeline_drops_reach_the_compile_report():
    first = _minimal_click_event()
    first["extras"] = {"sequence": 1}
    evs = run_pipeline([first])
    drop = {"sequence": 9, "pass": "upstream_dedupe", "action": "scroll"}
    data_dir, patchers = _compile_fixture("sess", evs)
    try:
        with ExitStack() as stack:
            for p in patchers:
                stack.enter_context(p)
            pkg = compile_skill_package(evs, skill_id="skill_test", source_session_id="sess",
                                        title="t", version=1, pipeline_drops=[drop])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    assert drop in pkg.compile_report["dropped"]


# --- BUILD-23 / BUILD-24: every recorded action and every input is accounted for ---------------------------------

_SELECT_MENU = "https://demoqa.com/select-menu"


def _react_select_events() -> list[dict]:
    """demoqa /select-menu: open the "Select Value" react-select, pick "Group 1, option 1" (session 3651c833's shape:
    `select_option` on `#react-select-2-option-0-0` in listbox `react-select-2-listbox`)."""
    opener = _minimal_click_event()
    opener["target"] = {"tag": "input", "id": "react-select-2-input", "classes": [], "inner_text": "", "role": "combobox",
                        "aria_label": None, "name": None, "type": "text"}
    opener["selectors"] = {"css": "#react-select-2-input", "xpath": "//input[@id='react-select-2-input']",
                           "text_based": "", "aria": '[role="combobox"]'}
    opener["semantic"] = {"normalized_text": "", "role": "combobox", "input_type": "text", "intent_hint": ""}
    opener["page"] = {"url": _SELECT_MENU, "title": "DEMOQA"}
    opener["extras"] = {"sequence": 1}
    options = [{"value": f"Group {g}, option {o}", "label": f"Group {g}, option {o}",
                "selector": f"#react-select-2-option-{g - 1}-{o - 1}", "checked": False} for g in (1, 2) for o in (1, 2)]
    pick = _minimal_click_event()
    pick["action"] = {"action": "select_option", "timestamp": "2026-01-01T00:00:02Z", "value": "Group 1, option 1"}
    pick["target"] = {"tag": "div", "id": "react-select-2-option-0-0", "classes": ["css-yt9ioa-option"],
                      "inner_text": "Group 1, option 1", "role": "option", "aria_label": None, "name": None}
    pick["selectors"] = {"css": "#react-select-2-option-0-0", "xpath": "//div[@id='react-select-2-option-0-0']",
                         "text_based": 'text="Group 1, option 1"', "aria": '[role="option"]'}
    pick["semantic"] = {"normalized_text": "group 1, option 1", "role": "option", "input_type": None, "intent_hint": ""}
    pick["page"] = {"url": _SELECT_MENU, "title": "DEMOQA"}
    pick["choice_context"] = {"kind": "aria_listbox", "multi": False, "group_key": "react-select-2-listbox",
                              "group_label": "", "group_selector": "#react-select-2-listbox",
                              "opener_selector": "#withOptGroup", "options": options}
    pick["extras"] = {"sequence": 2}
    pick["visual"] = {**pick["visual"], "full_screenshot": "images/evt_0002_full.jpg"}
    return [opener, pick]


def _compile_doc(raw: list[dict], *extra_patches) -> dict:
    """The compile handler's path on a recording: pipeline, compiler, inputs from the steps, the build check."""
    from conxa_compile.compiler.build import record_unbuildable
    from conxa_compile.editor.workflow_mutations import reconcile_inputs_with_step_values
    from conxa_compile.pipeline.run import pipeline_drops

    evs = run_pipeline(copy.deepcopy(raw))
    data_dir, patchers = _compile_fixture("sess", evs)
    try:
        with ExitStack() as stack:
            for p in (*patchers, *extra_patches):
                stack.enter_context(p)
            pkg = compile_skill_package(evs, skill_id="skill_test", source_session_id="sess", title="t", version=1,
                                        pipeline_drops=pipeline_drops(raw, evs))
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    doc, _ = reconcile_inputs_with_step_values(pkg.model_dump(mode="json"))
    record_unbuildable(doc)
    return doc


def test_a_react_select_pick_ships_a_step_that_uses_its_input(tmp_path):
    """BUILD-24: the input a react-select pick declares has a step in execution.json that fills it, and the compile
    report has nothing unaccounted for."""
    import json

    from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill

    doc = _compile_doc(_react_select_events())
    assert [i["id"] for i in doc["inputs"]] == ["react_select_2_listbox"]
    report = doc["compile_report"]
    assert report["events_total"] == 2
    assert not [d for d in report.get("dropped") or [] if d["pass"] in ("unaccounted", "not_buildable")]
    assert "dropped_inputs" not in report
    warnings: list[str] = []
    _build_workflow_from_saved_skill(bundle_root=tmp_path, workflow_slug="w", saved_skill=doc, on_warning=warnings.append)
    execution = json.loads((tmp_path / "skills" / "w" / "execution.json").read_text(encoding="utf-8"))
    inputs = json.loads((tmp_path / "skills" / "w" / "input.json").read_text(encoding="utf-8"))["inputs"]
    pick = next(s for s in execution if s["type"] == "select_option")
    assert pick["value"] == "{{react_select_2_listbox}}"
    for item in inputs:   # every input the package asks for is used by a step it ships
        assert any("{{" + item["name"] + "}}" in json.dumps(s) for s in execution), item["name"]
    assert not warnings


def test_an_action_a_pass_loses_without_a_record_is_named_and_must_be_acknowledged():
    """BUILD-23: the old silent tail-truncation, simulated. The lost pick is on the report as `unaccounted`, and the
    person is told in plain words, by a question that blocks test and approval until answered."""
    from conxa_compile.conversation import question_plan
    from conxa_compile.conversation.clarify import generate_questions, plan_answer

    truncate = patch("conxa_compile.compiler.build.collapse_choice_group_runs",
                     lambda steps, events, pol, absorbed=None: steps[:-1])
    doc = _compile_doc(_react_select_events(), truncate)
    lost = [d for d in doc["compile_report"]["dropped"] if d["pass"] == "unaccounted"]
    assert [(d["sequence"], d["action"], d["target"]) for d in lost] == [(2, "select_option", "Group 1, option 1")]

    qs = [q for q in generate_questions(doc, dropped=doc["compile_report"]["dropped"], limit=None) if q["kind"] == "unsupported"]
    assert len(qs) == 1
    assert "choose in “Group 1, option 1”" in qs[0]["body"] and "drawing" not in qs[0]["body"].lower()
    assert plan_answer(qs[0], {"choice": "ok"}, doc) == ([], "Kept without it")
    planned, _ = question_plan.apply_scoring([dict(qs[0])], doc, "post_compile")
    assert planned[0]["status"] == "open"


def test_a_step_the_build_cannot_carry_and_the_input_only_it_used_are_named_in_the_compile_report():
    """BUILD-24: the build's own rules, run at compile time. The step goes on the ledger, its input in `dropped_inputs`."""
    from conxa_compile.compiler.build import record_unbuildable

    doc = {
        "inputs": [{"id": "city"}, {"id": "name"}],
        "compile_report": {"dropped": []},
        "skills": [{"steps": [
            {"action": "type", "value": "{{name}}", "target": {"primary_selector": "#name"}, "source_event": 1},
            {"action": "type", "value": "{{city}}", "target": {}, "source_event": 2,
             "identity_bundle": {"fingerprint": {"label_text": "City"}}},
        ]}],
    }
    record_unbuildable(doc)
    assert doc["compile_report"]["dropped"] == [{"sequence": 2, "pass": "not_buildable", "action": "type", "target": "City"}]
    assert doc["compile_report"]["dropped_inputs"] == ["city"]
    clean = {"inputs": [{"id": "name"}], "skills": [{"steps": doc["skills"][0]["steps"][:1]}]}
    record_unbuildable(clean)
    assert "compile_report" not in clean


def test_a_date_picker_run_names_the_step_each_merged_action_went_into():
    """A day cell folded into the field's own click is not that date_pick step's event: it is listed as merged into it."""
    from conxa_compile.compiler.date_picker import collapse_date_picker_runs
    from conxa_core.models.skill_spec import SkillStep
    from tests.test_date_picker_collapse import _day_event, _open_event

    opener, day = _open_event(), _day_event()
    opener["extras"], day["extras"] = {"sequence": 7}, {"sequence": 8}
    steps = [SkillStep(action="focus", source_event=7), SkillStep(action="click", source_event=8)]
    absorbed: list = []
    out = collapse_date_picker_runs(steps, [opener, day], {}, absorbed=absorbed)
    assert [s.source_event for s in out] == [7]
    ledger: list = []
    _record_drops(ledger, absorbed, [], "date_collapsed")
    assert [(r["sequence"], r["merged_into"]) for r in ledger] == [(8, 7)]


def test_a_review_step_without_an_index_is_skipped_not_put_on_step_zero():
    from conxa_compile.llm.workflow_intent import _graph_from_raw

    graph = _graph_from_raw({"steps": [{"intent": "no index"}, {"index": 0, "intent": "real"}, {"index": "x", "intent": "bad"}]})
    assert [(s.index, s.intent) for s in graph.steps] == [(0, "real")]
