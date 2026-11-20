"""WF-13 13.1: a part the AI agent does on its own may never sit directly before a step that can't be undone.

One rule (patch_gate.span_before_irreversible / check_step_order), one plain refusal, on every way two steps can end
up side by side: insert, move (Human Edit drag and chat), delete of the step between them, a re-recorded splice,
the build, and the runtime (runtime/test/unit/test_agent_span.js). Irreversible means the real rule
(action_semantics.judged_irreversible: the model's judgement, unsure counts as cannot-be-undone), not a word list."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from conxa_core.config import settings  # noqa: E402
from conxa_compile.editor.patch_gate import CONFIRM_ONLY_SPAN_SCHEMA, span_before_irreversible  # noqa: E402
from conxa_compile.editor.patch_wording import SPAN_BEFORE_IRREVERSIBLE, plain_patch_error  # noqa: E402
from conxa_compile.editor.workflow_mutations import (  # noqa: E402
    _new_manual_step, delete_step_at, insert_step_after, reconcile_inputs_with_step_values, reorder_steps,
    replace_step_range, run_produced_names,
)
from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill  # noqa: E402
from handlers.protocol import _CommandError  # noqa: E402

CODE = "agent_span_cannot_directly_precede_a_destructive_step"


def _judged(reversible: bool, confidence: float = 0.9, label: str = "Archive record") -> dict:
    """A recorded click whose meaning a model judged. The label words never decide (no word list)."""
    return {
        "action": {"action": "click"}, "intent": label.lower().replace(" ", "_"), "semantic_description": label,
        "target": {"primary_selector": "#b", "fallback_selectors": []},
        "signals": {"semantic": {"effect_judgement": {"reversible": reversible, "confidence": confidence}},
                    "anchors": [{"text": label}]},
        "validation": {"wait_for": {"type": "toast"}, "assertions": [{"type": "text_present", "required": True, "target": "Done"}]},
    }


def _span(confirm_only: bool = False) -> dict:
    step = _new_manual_step("agent_span", "https://app.test")
    step.update({"value": "Pick the record", "intent": "pick_record"})
    step["action"]["value"] = step["value"]
    if confirm_only:
        step.update({"agent_span_allowed": [], "agent_span_answer_schema": dict(CONFIRM_ONLY_SPAN_SCHEMA),
                     "agent_span_on_failure": "abort"})
    return step


def _nav() -> dict:
    return {"action": {"action": "navigate"}, "url": "https://app.test", "intent": "open"}


def _doc(*steps) -> dict:
    return {"meta": {"id": "skill_x", "title": "Delete gate", "version": 1}, "inputs": [], "skills": [{"steps": list(steps)}]}


IRREVERSIBLE = _judged(False)


# ── the rule itself ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("after, refused", [
    (_judged(False), True),                                   # judged: can't be undone
    (_judged(True, confidence=0.3), True),                    # unsure counts as can't be undone
    (_judged(True, 0.95, label="Delete record"), False),      # judged undoable: the word "Delete" does not decide
    ({**_judged(True), "signals": {"semantic": {"effect_judgement": {"reversible": True, "confidence": 0.9,
                                                                     "person": "irreversible"}}}}, True),  # the person's answer wins
    ({"action": {"action": "click"}, "intent": "x", "consequence": "irreversible"}, True),  # the compiler's label
    (_nav(), False),
])
def test_the_rule_uses_the_judgement_not_words(after, refused):
    assert (span_before_irreversible([_span(), after], {}) == 0) is refused


def test_only_a_confirm_only_question_may_sit_right_before_it():
    assert span_before_irreversible([_span(confirm_only=True), IRREVERSIBLE], {}) is None
    assert span_before_irreversible([_span(), _nav(), IRREVERSIBLE], {}) is None
    assert span_before_irreversible([IRREVERSIBLE, _span()], {}) is None             # after it is fine


def test_every_code_for_it_reads_the_same_plain_sentence():
    assert plain_patch_error(CODE) == plain_patch_error("destructive_step_cannot_directly_follow_agent_span") \
        == SPAN_BEFORE_IRREVERSIBLE
    assert "span" not in SPAN_BEFORE_IRREVERSIBLE.lower() and "destructive" not in SPAN_BEFORE_IRREVERSIBLE.lower()


# ── every structural path ─────────────────────────────────────────────────────────────────────────

def test_moving_a_span_in_front_of_it_is_refused_and_moving_it_away_is_not():
    doc = _doc(_span(), _nav(), IRREVERSIBLE)
    with pytest.raises(ValueError, match=CODE):
        reorder_steps(doc, [1, 0, 2])                         # drag the span down next to the delete
    with pytest.raises(ValueError, match=CODE):
        reorder_steps(_doc(IRREVERSIBLE, _nav(), _span()), [1, 2, 0])  # drag the delete up under the span
    assert reorder_steps(doc, [1, 2, 0])["skills"][0]["steps"][2]["action"]["action"] == "agent_span"


def test_deleting_the_step_between_them_is_refused():
    doc = _doc(_span(), _nav(), IRREVERSIBLE)
    with pytest.raises(ValueError, match=CODE):
        delete_step_at(doc, 1)
    assert len(delete_step_at(doc, 0)["skills"][0]["steps"]) == 2   # removing the span itself is fine


def test_inserting_a_span_right_before_it_is_refused_unless_it_only_asks_a_person():
    doc = _doc(_nav(), IRREVERSIBLE)
    with pytest.raises(ValueError, match=CODE):
        insert_step_after(doc, "agent_span", 0)
    assert insert_step_after(doc, "agent_span", 0, confirm_only=True)["skills"][0]["steps"][1]["agent_span_allowed"] == []


def test_a_workflow_saved_the_wrong_way_can_be_fixed_by_putting_a_step_between():
    doc = _doc(_span(), IRREVERSIBLE)
    out = insert_step_after(doc, "wait", 0)
    assert span_before_irreversible(out["skills"][0]["steps"], {}) is None


def test_a_re_recorded_part_that_lands_it_right_after_a_span_is_refused():
    with pytest.raises(ValueError, match=CODE):
        replace_step_range(_doc(_nav(), _span(), _nav()), 2, [IRREVERSIBLE])


# ── the Human Edit / chat commands: one plain refusal, nothing written, no undo entry ─────────────

@pytest.fixture()
def backend(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    spec = importlib.util.spec_from_file_location("cbackend_span_order", os.path.join(_PY_DIR, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from handlers import protocol

    mod._write = protocol._write = lambda _m: None
    return mod.Backend()


@pytest.mark.parametrize("command, payload", [
    ("cmd_reorder_steps", {"new_order": [1, 0, 2]}),
    ("cmd_delete_step", {"step_index": 1}),
    ("cmd_insert_step", {"action_kind": "agent_span", "insert_after": 1}),
])
def test_each_editor_command_refuses_in_plain_words_and_changes_nothing(backend, command, payload):
    from conxa_core.storage.json_store import read_skill, write_skill

    doc = _doc(_span(), _nav(), IRREVERSIBLE) if command != "cmd_insert_step" else _doc(_nav(), _nav(), IRREVERSIBLE)
    write_skill("skill_x", doc)
    before = read_skill("skill_x")
    with pytest.raises(_CommandError) as err:
        getattr(backend, command)({"skill_id": "skill_x", **payload}, "r")
    assert err.value.code == CODE and err.value.message == SPAN_BEFORE_IRREVERSIBLE
    assert read_skill("skill_x") == before and not backend._undo_stacks.get("skill_x")


def test_a_chat_move_is_refused_the_same_way_and_rolled_back(backend):
    from conxa_compile.compiler.step_key import step_keys
    from conxa_core.storage.json_store import read_skill, write_skill

    write_skill("skill_x", _doc(_span(), _nav(), IRREVERSIBLE))
    before = read_skill("skill_x")
    keys = step_keys(before["skills"][0]["steps"])
    with pytest.raises(_CommandError) as err:
        backend._accept_structural_proposal(
            {"skill_id": "skill_x", "op": "move_step", "step_key": keys[0], "after_step_key": keys[1]}, "r")
    assert err.value.message == SPAN_BEFORE_IRREVERSIBLE
    assert read_skill("skill_x") == before


# ── the build: a document saved before the rule is refused, not shipped ───────────────────────────

def _built(saved: dict) -> list:
    with tempfile.TemporaryDirectory() as td:
        _build_workflow_from_saved_skill(bundle_root=Path(td), workflow_slug="wf", saved_skill=saved)
        return json.loads((Path(td) / "skills" / "wf" / "execution.json").read_text())


def test_the_build_refuses_a_span_right_before_it():
    with pytest.raises(ValueError, match="can't come straight after"):
        _built(_doc(_nav(), _span(), IRREVERSIBLE))
    assert [s["type"] for s in _built(_doc(_nav(), _span(confirm_only=True), IRREVERSIBLE))][:2] == ["navigate", "agent_span"]


def test_the_build_refuses_it_when_a_dropped_unfinished_step_closes_the_gap():
    unfinished = _new_manual_step("extract", "https://app.test")   # no target, no name: dropped at build
    with pytest.raises(ValueError, match="Step 4: A step that can't be undone"):
        _built(_doc(_nav(), _span(), unfinished, IRREVERSIBLE))


# ── 13.2: a span nobody gave a goal is dropped at build with a warning ────────────────────────────

def test_a_new_span_starts_with_no_goal_and_is_dropped_with_a_plain_warning():
    fresh = _new_manual_step("agent_span", "https://app.test")
    assert fresh["value"] == "" and fresh["action"]["value"] == ""
    warnings: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        _build_workflow_from_saved_skill(bundle_root=Path(td), workflow_slug="wf", saved_skill=_doc(_nav(), fresh),
                                         on_warning=warnings.append)
        steps = json.loads((Path(td) / "skills" / "wf" / "execution.json").read_text())
    assert [s["type"] for s in steps] == ["navigate"]
    assert any("Step 2: the agent step has no goal yet" in w for w in warnings)


# ── F4: the span's own answer is never asked of the person ───────────────────────────────────────

def _answer_span() -> dict:
    step = _span()
    step.update({"agent_span_allowed": [], "agent_span_output_name": "article_category",
                 "agent_span_answer_schema": {"type": "object", "required": ["choice"],
                                              "properties": {"choice": {"type": "string", "enum": ["person", "place"]}}}})
    return step


def test_a_span_answer_used_in_a_later_step_is_not_declared_as_an_input():
    later = {"action": {"action": "type", "value": "{{article_category_choice}}"}, "intent": "type_name",
             "value": "{{article_category_choice}} {{typed}}"}
    doc, added = reconcile_inputs_with_step_values(_doc(_answer_span(), later))
    assert [i["id"] for i in doc["inputs"]] == ["typed"] and added
    assert run_produced_names(doc["skills"][0]["steps"]) == {"article_category", "article_category_choice"}


def test_the_package_drops_an_answer_an_older_edit_declared_as_an_input():
    later = {"action": {"action": "type", "value": "{{article_category_choice}} {{typed}}"}, "intent": "type_name",
             "value": "{{article_category_choice}} {{typed}}", "target": {"primary_selector": "#name", "fallback_selectors": []}}
    saved = {**_doc(_nav(), _answer_span(), later),
             "inputs": [{"id": "article_category_choice", "type": "text"}, {"id": "typed", "type": "text"}]}
    with tempfile.TemporaryDirectory() as td:
        _build_workflow_from_saved_skill(bundle_root=Path(td), workflow_slug="wf", saved_skill=saved)
        declared = [i["name"] for i in json.loads((Path(td) / "skills" / "wf" / "input.json").read_text())["inputs"]]
    assert "typed" in declared and "article_category_choice" not in declared


# ── 13.5: a click inside a span that looks irreversible runs only after the person's yes ──────────

def _span_pause(needs: str = "") -> dict:
    meta = {"step_index": 2, "goal": "Delete the record", "allowed": ["click"], "page_url": "https://app.test",
            "budget_left": {"actions": 3, "seconds": 60}, "candidates": [{"i": 0, "name": "Delete record"}]}
    if needs:
        meta["needs_user_confirmation"] = needs
    return {"content": [{"type": "text", "text": "x"}], "_meta": {"conxa/agent_span": meta}}


@pytest.mark.parametrize("person_says_yes", [True, False])
def test_the_irreversible_click_is_resent_confirmed_only_after_the_persons_yes(person_says_yes):
    from handlers.workflows import _drive_runtime_pauses

    done = {"content": [{"type": "text", "text": "Done."}]}
    queue = [_span_pause("Delete record"), done]
    calls: list[dict] = []
    asked: list[str] = []

    def call(args):
        calls.append(args)
        return queue.pop(0)

    def review(info):
        asked.append(info["prompt"])
        return person_says_yes

    out, stopped = _drive_runtime_pauses(
        _span_pause(), {"skill": "s"}, call=call, cancel_run=lambda _r: None, guard_hook=None, review_hook=review,
        guard_requested=False, previewed=False, mark=lambda _m: None,
        answer_span=lambda span, content: {"kind": "click", "candidate_index": 0})
    first = calls[0]["span_actions"]["2"]
    assert first == {"kind": "click", "candidate_index": 0}            # the model's own try carries no yes
    assert asked == ["The agent wants to do something that can't be undone: Delete record. Allow it?"]
    if person_says_yes:
        assert calls[1]["span_actions"]["2"] == {"kind": "click", "candidate_index": 0, "user_confirmed": True}
        assert out is done and stopped is None
    else:
        assert len(calls) == 1 and stopped["declined"] is True       # nothing pressed; the test ends there


# ── F2 / F-E: a span that ends unfinished says why, in plain words ───────────────────────────────

@pytest.mark.parametrize("message, expected", [
    ("Agent span at step 6 ended (gave_up): The agent gave up: it couldn't confirm the delete with the person",
     "The AI agent gave up on this part: it couldn't confirm the delete with the person."),
    ("Agent span at step 6 ended (gave_up): The agent gave up.", "The AI agent gave up on this part."),
    ("Agent span at step 2 ended (exhausted): The agent used its budget (25 actions or 600 s) without finishing.",
     "The AI agent used up the actions or time it was given for this part before finishing it."),
    ("Agent span at step 2 ended (answer_invalid): No usable answer after 3 attempt(s): x.",
     "The AI agent never gave an answer in the form this part asks for."),
])
def test_a_span_that_ends_unfinished_says_why(message, expected):
    from conxa_compile.conversation.failure_summary import summarize

    assert summarize(message) == expected


def test_a_run_the_runtime_refused_for_this_order_reads_plainly():
    from conxa_compile.conversation.failure_summary import summarize

    text = ("Step 3 (click) failed: This step can't be undone and comes straight after a part the AI agent does on its "
            "own, so the run was stopped before anything ran.")
    assert summarize(text).startswith("A step that can’t be undone comes straight after a part the AI agent")


# ── F7: a span is named by what it does ───────────────────────────────────────────────────────────

def test_only_a_confirm_only_question_is_titled_as_a_person_confirming():
    from conxa_compile.conversation.clarify import step_name

    assert step_name(_span(confirm_only=True)) == "A person confirms before the next step"
    assert step_name(_new_manual_step("agent_span", "")) == "The AI agent does a part on its own"


# ── Round 2 (2026-11-20) ──────────────────────────────────────────────────────────────────────────

def test_the_span_limit_is_one_number_on_both_sides():
    """13.3: the runtime stops before the 6th free span; Studio warns with the same number."""
    from conxa_compile.editor.patch_gate import MAX_AGENT_SPANS_PER_RUN

    run_js = (Path(__file__).resolve().parents[2] / "runtime" / "app" / "run.js").read_text(encoding="utf-8")
    assert f"const MAX_AGENT_SPANS_PER_RUN = {MAX_AGENT_SPANS_PER_RUN};" in run_js and MAX_AGENT_SPANS_PER_RUN == 5


def test_a_run_stopped_at_the_span_limit_reads_plainly():
    from conxa_compile.conversation.failure_summary import summarize

    text = ("Step 9 (agent_span) failed: This workflow uses the AI agent more than 5 times in one run. Conxa stopped here "
            "so it doesn't run away. Remove or merge some of the parts the AI agent does on its own.")
    out = summarize(text)
    assert out.startswith("This workflow uses the AI agent more than 5 times in one run") and "went wrong" not in out


def test_the_test_says_up_front_when_a_span_is_left_out_or_the_limit_is_passed():
    """13.2: a span with no goal is named in the chat, not only in the build log. 13.3: more than 5 free spans warns.
    A confirm-only question is a person's yes, so it never counts."""
    from handlers.test_flow import _agent_span_notice

    blank = _new_manual_step("agent_span", "https://app.test")
    assert _agent_span_notice([_nav(), _span()]) == ""
    one = _agent_span_notice([_nav(), blank, _span()])
    assert one.startswith("One part the AI agent does on its own has no goal yet, so it is left out of this test.")
    assert "2 parts" in _agent_span_notice([blank, blank])
    assert _agent_span_notice([_span()] * 5 + [_span(confirm_only=True)] * 3) == ""
    six = _agent_span_notice([_span()] * 6 + [blank])
    assert "no goal yet" in six and "uses the AI agent 6 times" in six and "before the 6th time" in six


def test_a_saved_span_is_described_in_plain_words():
    """G4: Human Edit's default description is not "Agent span — agent span goal"."""
    from conxa_compile.editor.describe import describe_step

    assert describe_step(_new_manual_step("agent_span", ""), 6) == "Step 7: The AI agent does a part on its own"
    assert describe_step(_span(confirm_only=True), 0) == "Step 1: A person confirms before the next step"


def test_ending_after_an_allowed_irreversible_click_never_says_nothing_was_sent():
    """G4: the person said yes to the agent's delete, then ended the test at its next request."""
    from handlers.workflows import _drive_runtime_pauses

    queue = [_span_pause("Delete record"), _span_pause("Delete record")]
    answers = iter([True, False])
    out, stopped = _drive_runtime_pauses(
        _span_pause(), {"skill": "s"}, call=lambda _a: queue.pop(0), cancel_run=lambda _r: None, guard_hook=None,
        review_hook=lambda _i: next(answers), guard_requested=False, previewed=False, mark=lambda _m: None,
        answer_span=lambda span, content: {"kind": "click", "candidate_index": 0})
    assert stopped["declined"] is True and stopped["after_confirmed"] is True
