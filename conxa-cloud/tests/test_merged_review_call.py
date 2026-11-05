"""BUILD-55 Phase A: the description check and the loop wording ride on the compile's one review call,
and the question wording is one cached background call. No model, no network."""

from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")))

from conxa_core.config import settings  # noqa: E402
from conxa_core.llm.client import _openai_messages_for_task  # noqa: E402
from conxa_compile.conversation import clarify, understanding  # noqa: E402
from conxa_compile.llm import workflow_review  # noqa: E402


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def _doc(url="https://github.com/github/gitignore"):
    step = {"action": {"action": "navigate"}, "intent": "open_repo", "url": url,
            "identity_bundle": {"stable_hash": "h"}}
    return {"skills": [{"steps": [step]}], "inputs": []}


# ── the prompt ──────────────────────────────────────────────────────────────────────────────


def _review_text(**extra):
    payload = {"input": {"steps": [{"key": "s0"}], "page_urls": [], "sibling_bindings": {}, **extra}}
    messages = _openai_messages_for_task("workflow_review", payload)
    return messages[0]["content"], messages[1]["content"]       # text only: a plain JSON string


def test_candidates_and_loops_are_sent_only_when_asked_for():
    _, plain = _review_text()
    assert "candidates" not in plain and "loop_candidates" not in plain
    _, with_task_only = _review_text(candidates=[{"ref": "url:h"}])          # no description: no check
    assert "candidates" not in with_task_only
    _, asked = _review_text(task_description="copy a file", candidates=[{"ref": "url:h"}], ends_with_checks=["Done"],
                            loop_candidates=[{"id": "a|b", "steps": []}])
    assert '"candidates"' in asked and '"ends_with_checks"' in asked and '"loop_candidates"' in asked


def test_the_prompt_asks_for_both_extra_answers_and_still_forbids_selectors():
    system, _ = _review_text()
    assert "understanding" in system and "loop_copy" in system and "verdict" in system
    assert "never write or invent a page selector" in system.lower()


# ── the review builder ──────────────────────────────────────────────────────────────────────


def _raw(**extra):
    return {"goal": "Copy a file", "steps": [], "decision_points": [], "expected_end_state": {}, "suggestions": [], **extra}


def test_the_review_hands_back_understanding_stamped_with_the_description_it_was_written_for(monkeypatch):
    monkeypatch.setattr(workflow_review, "infer_workflow_review", lambda **k: _raw(
        understanding={"goal_summary": "g", "values": []}, loop_copy=[{"id": "a|b", "text": "Hi?"}, "junk"]))
    monkeypatch.setattr(workflow_review, "read_cached", lambda *a, **k: None)
    monkeypatch.setattr(workflow_review, "write_cached", lambda *a, **k: None)
    out: dict = {}
    workflow_review.build_workflow_review([{"key": "k1", "action": "click"}], page_urls=[], task_description="  copy a file ",
                                          review_inputs={"candidates": [{"ref": "url:h"}]}, extras_out=out)
    assert out["understanding"] == {"goal_summary": "g", "values": [], "for_task": "copy a file"}
    assert out["loop_copy"] == [{"id": "a|b", "text": "Hi?"}]


def test_a_failed_review_leaves_the_extras_empty_and_never_raises(monkeypatch):
    monkeypatch.setattr(workflow_review, "infer_workflow_review", lambda **k: None)
    monkeypatch.setattr(workflow_review, "read_cached", lambda *a, **k: None)
    out: dict = {}
    graph, findings = workflow_review.build_workflow_review([{"key": "k1", "action": "click"}], page_urls=[], extras_out=out)
    assert graph.goal == "" and findings == [] and out == {"understanding": None, "loop_copy": []}


def test_the_cache_key_changes_with_what_the_call_is_asked(monkeypatch):
    keys: list[str] = []
    monkeypatch.setattr(workflow_review, "read_cached", lambda ns, key, v: keys.append(key))
    monkeypatch.setattr(workflow_review, "infer_workflow_review", lambda **k: None)
    steps = [{"key": "k1", "action": "click"}]
    workflow_review.build_workflow_review(steps, page_urls=[], task_description="t")
    workflow_review.build_workflow_review(steps, page_urls=[], task_description="t", review_inputs={"candidates": [{"ref": "url:h"}]})
    workflow_review.build_workflow_review(steps, page_urls=[], loop_candidates=[{"id": "a|b", "steps": []}])
    assert len(set(keys)) == 3


# ── the question flow reads the review's answer ────────────────────────────────────────────


def _answer(doc, task, verdict="varies"):
    cands = understanding.extract_candidates(doc)
    return {"goal_summary": "g", "coverage": {"ok": False, "note": "Missing the send."}, "for_task": task,
            "values": [{"ref": c["ref"], "verdict": verdict, "name": "repository"} for c in cands]}


def test_the_stored_review_answer_is_used_without_any_model_call():
    doc = _doc()
    res = understanding.understand(doc, "copy a repo file", review=_answer(doc, "copy a repo file"))
    assert res["source"] == "model" and res["candidates"][0]["verdict"] == "varies"
    assert res["coverage"] == {"ok": False, "note": "Missing the send."}


def test_an_answer_written_for_another_description_is_not_trusted():
    doc = _doc()
    res = understanding.understand(doc, "a different task", review=_answer(doc, "copy a repo file"))
    assert res["source"] == "fallback" and res["candidates"][0]["verdict"] == "unsure"   # asked, unticked


def test_no_answer_and_no_description_asks_nothing():
    doc = _doc()
    assert understanding.understand(doc, "", review=None)["candidates"][0]["verdict"] == "fixed"
    assert understanding.understand({"skills": [{"steps": []}]}, "", review=None)["source"] == "none"


def test_review_inputs_ask_about_addresses_rows_and_clicks_but_not_typed_values():
    inputs = understanding.review_inputs(_doc())
    assert [c["kind"] for c in inputs["candidates"]] == ["url"] and "ends_with_checks" in inputs
    assert understanding.review_inputs({"skills": [{"steps": []}], "inputs": []}) == {}


# ── the wording call ────────────────────────────────────────────────────────────────────────


def _questions():
    return [clarify._question("coverage", "coverage", "Does this cover everything you wanted?", "Draft body.",
                              [{"id": "complete", "label": "Yes", "detail": ""}, {"id": "incomplete", "label": "No", "detail": ""}])]


def test_the_wording_call_is_cached_on_the_exact_drafts(monkeypatch):
    from conxa_compile.llm import clarify_question_copy as mod

    calls: list[dict] = []

    def fake(task, payload, timeout_ms, **k):
        calls.append(payload)
        return {"questions": [{"id": "coverage", "title": "Is that everything?", "body": "Anything missing?"}]}

    monkeypatch.setattr(mod, "call_llm", fake)
    first = mod.reword_questions(_questions())
    second = mod.reword_questions(_questions())
    assert len(calls) == 1 and first == second and "_v" not in second
    changed = _questions()
    changed[0]["body"] = "A different draft."
    mod.reword_questions(changed)
    assert len(calls) == 2


def test_a_failed_or_empty_wording_call_is_not_cached(monkeypatch):
    from conxa_compile.llm import clarify_question_copy as mod

    answers = iter([RuntimeError("down"), {"questions": []}, {"questions": [{"id": "coverage", "title": "T"}]}])

    def fake(*a, **k):
        a_ = next(answers)
        if isinstance(a_, Exception):
            raise a_
        return a_

    monkeypatch.setattr(mod, "call_llm", fake)
    assert mod.reword_questions(_questions()) is None
    assert mod.reword_questions(_questions()) == {"questions": []}
    assert mod.reword_questions(_questions())["questions"][0]["title"] == "T"     # the earlier two did not poison the cache


def test_popup_buttons_reach_the_wording_call_as_context_only():
    state = {"id": "s1", "signature": {"title": "File exists", "button_labels": ["Replace", "Keep both"]},
             "options": [{"id": "replace", "label": "Replace"}, {"id": "keep", "label": "Keep both"}]}
    q = clarify._fork_question("k", state, None)
    item = next(i for i in clarify.reword_payload([q]))
    assert item["popup_buttons"] == ["Replace", "Keep both"]
    assert all(not c["id"].startswith("option:") for c in item["choices"])     # the popup's own buttons are never reworded


# ── the review reads the understanding, and is asked once more when a reply is unreadable ──────────


def test_the_understanding_and_a_retry_note_reach_the_model_and_the_prompt_explains_them():
    system, plain = _review_text()
    assert '"understanding"' not in plain and '"retry"' not in plain
    _, asked = _review_text(understanding={"goal": "G", "settled": [{"literal": "x", "verdict": "fixed"}]}, retry="again")
    assert '"understanding"' in asked and '"retry"' in asked
    assert all(word in system for word in ("understanding", "field_label", "summary", "seen", "retry"))


def test_the_understanding_sets_the_goal_and_a_settled_value_is_not_suggested_again(monkeypatch):
    monkeypatch.setattr(workflow_review, "read_cached", lambda *a, **k: None)
    monkeypatch.setattr(workflow_review, "write_cached", lambda *a, **k: None)
    monkeypatch.setattr(workflow_review, "infer_workflow_review", lambda **k: _raw(goal="Its own goal", suggestions=[
        {"step_key": "k1", "kind": "parameterize_literal", "current": " INV-20931 ", "proposed": "invoice_number", "why": "w"},
        {"step_key": "k2", "kind": "parameterize_literal", "current": "acme", "proposed": "customer", "why": "w"}]))
    steps = [{"key": "k1", "action": "type"}, {"key": "k2", "action": "type"}]
    ctx = {"goal": "Download an invoice.", "settled": [{"literal": "inv-20931", "verdict": "varies"}]}
    graph, findings = workflow_review.build_workflow_review(steps, page_urls=[], prior_understanding=ctx)
    assert graph.goal == "Download an invoice."
    assert [f["current"] for f in findings] == ["acme"]
    graph, findings = workflow_review.build_workflow_review(steps, page_urls=[])
    # the cap on findings is a share of the steps, so only the first survives without the understanding:
    # which is why a settled value is dropped before the cap, not after it
    assert graph.goal == "Its own goal" and [f["current"].strip() for f in findings] == ["INV-20931"]


def test_an_unreadable_review_reply_is_asked_for_once_more_but_an_unreachable_router_is_not(monkeypatch):
    monkeypatch.setattr(workflow_review, "read_cached", lambda *a, **k: None)
    monkeypatch.setattr(workflow_review, "write_cached", lambda *a, **k: None)
    calls: list[dict] = []

    def flaky(**k):
        calls.append(k)
        return None if len(calls) == 1 else _raw()

    monkeypatch.setattr(workflow_review, "infer_workflow_review", flaky)
    graph, _ = workflow_review.build_workflow_review([{"key": "k1", "action": "click"}], page_urls=[])
    assert len(calls) == 2 and not calls[0].get("retry") and calls[1]["retry"] and graph.goal == "Copy a file"

    calls.clear()

    def down(**k):
        calls.append(k)
        k["error_detail"].append("cloud_unreachable")
        return None

    monkeypatch.setattr(workflow_review, "infer_workflow_review", down)
    workflow_review.build_workflow_review([{"key": "k1", "action": "click"}], page_urls=[], error_detail=[])
    assert len(calls) == 1


def test_the_cache_key_changes_with_the_understanding(monkeypatch):
    keys: list[str] = []
    monkeypatch.setattr(workflow_review, "read_cached", lambda ns, key, v: keys.append(key))
    monkeypatch.setattr(workflow_review, "infer_workflow_review", lambda **k: None)
    steps = [{"key": "k1", "action": "click"}]
    workflow_review.build_workflow_review(steps, page_urls=[])
    workflow_review.build_workflow_review(steps, page_urls=[], prior_understanding={"goal": "G"})
    assert len(set(keys)) == 2


def test_review_inputs_carry_the_field_label_what_the_video_saw_and_the_understanding_line():
    from conxa_compile.compiler.build import _review_inputs, _understanding_for_review
    from conxa_core.models.skill_spec import SkillStep, VideoContext

    typed = SkillStep(action={"action": "type"}, intent="enter_value", source_event=3,
                      video_context=VideoContext(visible_target="Search box", screen_after="one row left", purpose="find it"))
    typed.identity_bundle.fingerprint.placeholder = "Search invoices"
    bare = SkillStep(action={"action": "click"}, intent="click", source_event=None)
    ctx = {"goal": "G", "summary_by_event": {"3": "Types the invoice number."}, "settled": [], "decided": ["d"]}
    out = _review_inputs([typed, bare], {}, ctx)
    assert out[0]["field_label"] == "Search invoices" and out[0]["summary"] == "Types the invoice number."
    assert out[0]["seen"] == {"visible_target": "Search box", "screen_after": "one row left", "purpose": "find it"}
    assert not {"field_label", "seen", "summary"} & set(out[1])
    assert _understanding_for_review(ctx) == {"goal": "G", "decided": ["d"]} and _understanding_for_review(None) is None
    assert "summary" not in _review_inputs([typed], {})[0]                       # no understanding: exactly as before
