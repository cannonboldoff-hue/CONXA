"""The slot ledger: facets and slots for every question, vacuity, slot dedupe, report/enforce."""

from __future__ import annotations

import pytest

from conxa_compile.conversation import clarify, question_plan, unknowns
from conxa_compile.conversation import timeline_understanding as tu
from conxa_core.config import settings
from tests.test_compile_clarify import _SOURCES, _invoice_doc, _lookup


def _q(kind, qid, status="open", **extra):
    return {"id": qid, "kind": kind, "status": status, "choices": [{"id": "a"}, {"id": "b"}], **extra}


def test_every_question_kind_and_effect_names_a_facet():
    assert set(clarify._PRIORITY) <= set(unknowns.FACET_OF_KIND)
    assert set(tu.RULES) <= set(unknowns.FACET_OF_KIND)
    assert unknowns.SAFETY_KINDS <= set(unknowns.FACET_OF_KIND)


def test_priority_never_raises_on_a_kind_the_table_does_not_know():
    assert unknowns.priority({"kind": "input_check"}) == 9
    assert unknowns.priority({"kind": "value", "phase": "pre_compile", "cost_rank": 2}) == 2
    assert unknowns.priority({"kind": "value", "phase": "pre_compile"}) == 99
    assert unknowns.priority({"kind": "upload"}) < unknowns.priority({"kind": "done_when"})


def test_a_slot_is_named_by_recorded_events_so_it_survives_renumbered_steps():
    q = _q("confirm", "confirm:k#1", step_keys=["k#1"])
    first = unknowns.slot_of(q, {"k#1": 7}.get)
    renumbered = unknowns.slot_of({**q, "step_keys": ["other#9"], "id": "confirm:other#9"}, {"other#9": 7}.get)
    assert first == renumbered == "consequence/confirm:7"
    # the question asked before the build about the same click lands in the same slot
    assert unknowns.slot_of(_q("confirm_before", "confirm_before:r2", event_ids=[7])) == first
    # a popup's fork and its follow-up are different questions about one step
    fork = unknowns.slot_of(_q("fork", "f", step_keys=["k"], fork_state_id="s1"), {"k": 3}.get)
    then = unknowns.slot_of(_q("fork_then", "f2", step_keys=["k"], fork_state_id="s1"), {"k": 3}.get)
    assert fork != then


def test_a_question_whose_every_answer_edits_nothing_is_vacuous_and_a_real_one_is_not():
    doc = _invoice_doc()
    qs = {q["kind"]: q for q in clarify.generate_questions(doc, source_lookup=_lookup(_SOURCES), limit=None)}
    assert not unknowns.vacuous(qs["values"], doc) and not unknowns.vacuous(qs["confirm"], doc)
    empty = {**qs["values"], "recorded": {}}               # nothing to restore: ticking or not changes nothing
    assert unknowns.vacuous(empty, doc)
    assert not unknowns.vacuous({**empty, "kind": "dropped_value"}, doc)                       # advisory by design
    assert not unknowns.vacuous({**empty, "phase": "pre_compile"}, doc)                        # nothing applies before the build
    assert not unknowns.vacuous({**qs["confirm"], "choices": qs["confirm"]["choices"][:1]}, doc)   # one answer to pick: nothing to compare
    assert not unknowns.vacuous({**empty, "status": "answered"}, doc)


def test_dedupe_keeps_the_question_that_asks_first_and_never_drops_a_safety_kind():
    a = unknowns.annotate(_q("popup", "p1", step_keys=["k"]), {"k": 5}.get, "post_compile")
    b = unknowns.annotate(_q("popup_dismiss", "p2", phase="pre_compile", cost_rank=1, event_ids=[5]), None, "pre_compile")
    kept, dropped = unknowns.dedupe([a, b])
    assert [q["id"] for q in dropped] == ["p1"] and [q["id"] for q in kept] == ["p2"]       # the model's rank 1 asks before kind priority 4
    c = unknowns.annotate(_q("confirm", "c1", step_keys=["k"]), {"k": 5}.get, "post_compile")
    d = unknowns.annotate(_q("confirm_before", "c2", phase="pre_compile", event_ids=[5]), None, "pre_compile")
    assert unknowns.dedupe([c, d]) == ([c, d], [])
    done = unknowns.annotate(_q("popup", "p3", status="answered", step_keys=["k"]), {"k": 5}.get, "post_compile")
    assert unknowns.dedupe([a, done])[1] == []                                              # history is never dropped


@pytest.mark.parametrize("mode,expect_kept", [("off", 2), ("report", 2), ("enforce", 1)])
def test_review_reports_in_report_mode_and_drops_only_in_enforce_mode(monkeypatch, mode, expect_kept):
    monkeypatch.setattr(settings, "clarify_ledger", mode)
    qs = [_q("popup", "p1", step_keys=["k"]), _q("popup", "p2", step_keys=["k"])]
    out, report = question_plan.review(qs, doc=None, event_id_of_step_key={"k": 5}.get, found_at="post_compile")
    assert len(out) == expect_kept
    if mode == "off":
        assert report == {} and "unknown" not in qs[0]
    else:
        assert report["override"]["duplicate_slot"]["count"] == 1 and qs[0]["unknown"]["facet"] == "alternatives"
