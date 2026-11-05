"""scripts/eval_clarify.py: the totals the question-quality numbers come from."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_clarify.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_clarify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_totals_count_skips_kept_guesses_quiet_defaults_and_the_edits_nobody_asked_about(tmp_path):
    wf = tmp_path / "workflows" / "w1"
    _write(wf / "clarify_events.jsonl", [
        {"t": 10, "event": "shown", "qid": "a", "kind": "confirm", "facet": "consequence"},
        {"t": 14, "event": "answered", "qid": "a", "kind": "confirm", "facet": "consequence", "choice": "yes", "guess": "yes", "kept_guess": True},
        {"t": 15, "event": "shown", "qid": "b", "kind": "skip_when_empty", "facet": "input_domain", "would": "assume_show", "default": "run"},
        {"t": 16, "event": "answered", "qid": "b", "kind": "skip_when_empty", "facet": "input_domain", "choice": "run", "would": "assume_show", "default": "run"},
        {"t": 17, "event": "shown", "qid": "c", "kind": "popup", "facet": "alternatives"},
        {"t": 18, "event": "skipped", "qid": "c", "kind": "popup", "facet": "alternatives"},
        {"t": 19, "event": "test_failed", "step_key": "k"},
    ])
    (wf / "clarify.json").write_text(json.dumps({"skill_id": "s1", "step_count_at_compile": 6, "limits": {"output": 3},
                                                 "ledger_report": {"override": {"vacuous": {"count": 2, "example": "x"}}}}), encoding="utf-8")
    _write(tmp_path / "skills" / "s1" / "edits.jsonl", [
        {"source": "human", "field": "validation.assertions"},     # a success check nobody asked about: a miss
        {"source": "human", "field": "value"},                      # variability: also not asked about
        {"source": "clarify", "field": "value"},                    # an answer, not a miss
        {"source": "human", "field": "optional_hint"},              # alternatives were asked: not a miss
    ])
    out = _load().summarize(tmp_path)
    assert out["workflows"] == 1 and out["median_seconds_to_answer"] == 2.5
    assert out["kinds"]["popup"]["skip_rate"] == 1.0 and out["kinds"]["confirm"]["answered"] == 1
    assert out["guess_kept"] == {"overall": 1.0, "n": 1, "by_kind": {"confirm": 1.0}}
    assert out["shadow"]["quiet_answers_that_were_the_default"] == 1.0
    assert out["ledger_report"] == {"override.vacuous": 2} and out["silent_cuts"] == {"output": 3}
    assert out["misses_by_facet"] == {"postcondition": 1, "variability": 1}
    assert out["questions_per_workflow"] == [{"workflow": "w1", "asked": 3, "steps": 6, "misses": 2, "test_failed": 1}]
    assert _load().summarize(tmp_path / "empty")["workflows"] == 0
