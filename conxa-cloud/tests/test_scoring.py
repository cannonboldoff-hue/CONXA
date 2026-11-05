"""Ask / confirm / assume / defer: the arithmetic, the safety floor, and what each action does to the list."""

from __future__ import annotations

import pytest

from conxa_compile.conversation import clarify, question_plan, scoring
from conxa_core.config import settings
from tests.test_compile_clarify import _SOURCES, _invoice_doc, _lookup


def _optional_doc():
    return {"inputs": [{"id": "note", "default": "", "optional": True}],
            "skills": [{"steps": [{"action": {"action": "type"}, "input_binding": "note", "value": "{{note}}", "intent": "Type note"}]}]}


def _qs(doc=None):
    doc = doc or _invoice_doc()
    return doc, {q["kind"]: q for q in clarify.generate_questions(doc, source_lookup=_lookup(_SOURCES), limit=None)}


def test_the_safety_floor_always_asks_whatever_the_arithmetic_says():
    doc, qs = _qs()
    assert scoring.decide(qs["confirm"], doc)["action"] == "ask"
    for kind in ("fork", "fork_then", "known_popup", "confirm_before"):
        assert scoring.decide({"kind": kind, "choices": [], "status": "open"}, doc)["action"] == "ask"


def test_a_question_asked_before_the_build_is_never_assumed_or_deferred():
    guessed = {"kind": "value", "phase": "pre_compile", "best_guess": "varies", "status": "open", "choices": []}
    unsure = {**guessed, "best_guess": ""}
    assert scoring.decide(guessed, {})["action"] == "confirm"          # a ticked guess: one tap, still the person's
    assert scoring.decide(unsure, {})["action"] == "ask"
    assert scoring.decide({**guessed, "kind": "done_when"}, {})["action"] in ("confirm", "ask")


def test_cheap_compiled_questions_are_assumed_and_a_likely_costly_one_is_confirmed_or_asked():
    doc, qs = _qs()
    assert scoring.decide(qs["values"], doc)["action"] == "confirm"             # 0.5 x 0.6: worth one tap
    assert scoring.decide(qs["done_when"], doc)["action"] == "assume_show"      # 0.3 x 0.3, and "tick nothing" edits nothing
    optional_doc, optional = _qs(_optional_doc())
    assert scoring.decide(optional["skip_when_empty"], optional_doc)["action"] == "assume_show"
    assert scoring.decide(qs["popup"], doc)["action"] == "confirm"              # both its answers edit the workflow: no default to stand in
    assert scoring.decide({"kind": "dropped_value", "choices": [{"id": "ok"}, {"id": "missing"}], "status": "open"}, doc)["action"] == "assume"
    assert scoring.decide(qs["upload"], doc)["action"] == "ask"                 # 0.5 x 0.8 = 0.4 reaches the top cut
    assert scoring.decide(qs["values"], None)["action"] == "confirm"            # no document: cannot tell the default stands


def test_a_question_every_answer_of_which_edits_the_workflow_cannot_be_assumed():
    doc, qs = _qs()
    assert scoring.default_answer(qs["popup"], doc) is None                     # closing it and keeping it as a step both edit
    assert scoring.decide(qs["popup"], doc)["action"] == "confirm"
    assert scoring.default_answer(qs["done_when"], doc) is not None


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_apply_scoring_records_in_shadow_and_acts_only_when_on(monkeypatch, mode):
    monkeypatch.setattr(settings, "clarify_scoring", mode)
    doc = _optional_doc()
    doc["skills"][0]["steps"].append({"action": {"action": "click"}, "intent": "send", "consequence": "irreversible"})
    doc["skills"][0]["steps"].insert(0, {"action": {"action": "navigate"}, "intent": "open"})
    out = clarify.generate_questions(doc, limit=None)
    out, assumptions = question_plan.apply_scoring(out, doc, "post_compile")
    by = {q["kind"]: q for q in out}
    if mode == "off":
        assert "decision" not in by["skip_when_empty"] and assumptions == []
    else:
        assert by["skip_when_empty"]["decision"]["action"] == "assume_show" and by["confirm"]["decision"]["action"] == "ask"
    if mode == "on":
        assert by["skip_when_empty"]["status"] == "queued" and by["skip_when_empty"]["assumed"] is True
        assert by["confirm"]["status"] == "open"
        assert [a["qid"] for a in assumptions] == [by["skip_when_empty"]["id"]] and assumptions[0]["default"]
    else:
        assert by["skip_when_empty"]["status"] == "open" and assumptions == []


def test_a_deferrable_question_waits_and_questions_before_the_build_are_untouched(monkeypatch):
    monkeypatch.setattr(settings, "clarify_scoring", "on")
    doc = {"skills": [{"steps": [{"action": {"action": "date_pick"}, "intent": "pick due", "input_binding": "due", "value": "{{due}}",
                                  "handler_hints": {"date_picker": {"recorded_value": "2099-01-01"}}}]}], "inputs": [{"id": "due"}]}
    q = {"id": "rd", "kind": "relative_date", "status": "open", "step_keys": ["k"], "days": 7, "binding": "due",
         "choices": [{"id": "relative"}, {"id": "fixed_input"}]}
    pre = {"id": "v", "kind": "value", "phase": "pre_compile", "status": "open", "best_guess": "varies", "choices": []}
    out, assumptions = question_plan.apply_scoring([q, pre], doc, "post_compile")
    assert q["status"] == "deferred" and assumptions == [] and pre["status"] == "open" and pre["decision"]["action"] == "confirm"


def test_what_this_person_usually_answers_on_this_app_moves_the_chance_the_default_is_wrong(tmp_path, monkeypatch):
    from conxa_compile.conversation import answer_memory

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    doc = _optional_doc()
    q = next(x for x in clarify.generate_questions(doc, limit=None) if x["kind"] == "skip_when_empty")
    q["step_name"] = "Type note"
    base = scoring.decide(q, doc)
    assert base["action"] == "assume_show"
    answer_memory.remember("app.example", q, {"choice": "skip"})
    assert answer_memory.recall("app.example", q) == {"skip": 1} and answer_memory.recall("other.example", q) == {}
    answer_memory.remember("app.example", q, {"choice": "skip"})
    q["memory"] = answer_memory.recall("app.example", q)
    changed = scoring.decide(q, doc)                      # they always skip a blank box, the compiler's default runs it: less sure it is right
    assert changed["p"] > base["p"] and changed["action"] != "assume"
    q["memory"] = {"run": 3}
    assert scoring.decide(q, doc)["p"] < base["p"]        # they always keep the default: surer
    # the safety floor ignores memory
    confirm = {"kind": "confirm", "status": "open", "choices": [], "memory": {"no": 5}}
    assert scoring.decide(confirm, doc)["action"] == "ask" and answer_memory.key_of("app.example", confirm) is None
