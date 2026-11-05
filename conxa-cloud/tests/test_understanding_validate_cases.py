"""Golden cases for the understanding check: bad model answer in, checked answer and drop ledger out.

Every case lives in tests/fixtures/understanding_validate_cases.json. The report match is exact, so a drop
that stops being counted (or a new silent one) fails here, and a prompt or rule change has to say so."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conxa_compile.conversation import timeline_understanding as TU

DATA = json.loads((Path(__file__).parent / "fixtures" / "understanding_validate_cases.json").read_text("utf-8"))


def _flat(report: dict) -> dict[str, int]:
    return {f"{stage}.{rule}": slot["count"] for stage, rules in report.items() for rule, slot in rules.items()}


@pytest.mark.parametrize("case", DATA["cases"], ids=[c["name"] for c in DATA["cases"]])
def test_case(case):
    calls: list[dict] = []

    def model(task, payload, ms):
        calls.append(payload)
        return case.get("raw_retry", case["raw"]) if len(calls) > 1 else case["raw"]

    out = TU.understand_timeline(DATA["timelines"][case.get("timeline", "base")], "download invoices", call=model)
    want = case["expect"]
    assert out["grade"] == want["grade"]
    assert len(calls) == want["calls"]
    assert _flat(out["validation_report"]) == want["report"]
    assert sorted(f"{q['kind']}@{q['row']}:{q.get('ref', '')}" for q in out["questions"]) == sorted(want["questions"])
    if "retried" in want:
        assert bool(out.get("retried")) is want["retried"]
    if "source" in want:
        assert out["source"] == want["source"]
    for key, guess in want.get("guesses", {}).items():
        assert {f"{q['kind']}@{q['row']}": q["best_guess"] for q in out["questions"]}[key] == guess
    for ref, name in want.get("names", {}).items():
        assert out["values"][ref]["name"] == name
    for ref, verdict in want.get("verdicts", {}).items():
        assert out["values"][ref]["verdict"] == verdict
    assert out["prompt_version"] >= 1
    json.dumps(out)       # understanding.json is this dict: it must stay plain JSON
