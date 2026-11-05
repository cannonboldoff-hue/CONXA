"""Golden cases for the clarifying questions: which slots a finished workflow raises, and which action scoring picks.

``fixtures/clarify_gold/cases.json``: small compiled documents with the (kind, facet) pairs a person must be asked about.
Precision is checked by the plain case (nothing to ask) and by exact equality; recall by every expected pair.
Synthetic for now; replace or add real recordings as they are labelled (docs/reports/Clarifying-Questions-...md, 6).
``fixtures/clarify_selection_cases.json``: one row per decision scoring must make.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from conxa_compile.conversation import clarify, question_plan, scoring
from conxa_core.config import settings

FIX = Path(__file__).parent / "fixtures"
GOLD = json.loads((FIX / "clarify_gold" / "cases.json").read_text(encoding="utf-8"))
SELECTION = json.loads((FIX / "clarify_selection_cases.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _enforce_the_ledger(monkeypatch):
    # the vacuity check is what keeps "which of these change every time?" off a field recorded empty
    monkeypatch.setattr(settings, "clarify_ledger", "enforce")


def _detected(case):
    doc = {"meta": {"version": 1}, "skills": [{"steps": case["steps"]}], "inputs": case["inputs"]}
    today = datetime.date.fromisoformat(case["today"]) if case.get("today") else None
    sources = case.get("sources") or {}
    qs = clarify.generate_questions(doc, limit=None, today=today, source_lookup=lambda step: sources.get(step.get("intent")))
    qs, _ = question_plan.review(qs, doc=doc, event_id_of_step_key=None, found_at="post_compile")
    return {(q["kind"], q["unknown"]["facet"]) for q in qs}


@pytest.mark.parametrize("case", GOLD, ids=[c["name"] for c in GOLD])
def test_each_workflow_raises_exactly_the_slots_it_needs(case):
    assert _detected(case) == {tuple(e) for e in case["expect"]}


def test_slot_recall_and_precision_over_the_whole_set():
    found = expected = right = 0
    for case in GOLD:
        got, want = _detected(case), {tuple(e) for e in case["expect"]}
        found, expected, right = found + len(got), expected + len(want), right + len(got & want)
    assert right / expected == 1.0 and right / found == 1.0


@pytest.mark.parametrize("row", SELECTION, ids=[r["name"] for r in SELECTION])
def test_scoring_picks_the_action_the_table_says(row, monkeypatch):
    monkeypatch.setattr(scoring, "default_answer", lambda q, doc: ({"choice": "relative" if row["kind"] == "relative_date" else "run"}, "ok") if row.get("default") else None)
    q = {"kind": row["kind"], "status": "open", "choices": [], "phase": row.get("phase"), "best_guess": row.get("best_guess", ""),
         "memory": row.get("memory")}
    assert scoring.decide(q, {} if not row.get("phase") else None)["action"] == row["expect"]
