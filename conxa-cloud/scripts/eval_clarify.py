"""How good are the clarifying questions? Reads what the Studio already writes beside each workflow.

    # from the repo root, with PYTHONPATH=conxa-builder/python:packages/conxa-core and LLM_FREE_API_KEYS=x
    python conxa-cloud/scripts/eval_clarify.py rates            # every workflow under the Studio data dir
    python conxa-cloud/scripts/eval_clarify.py rates --data-dir path/to/data --json out.json

Inputs per workflow (``<data_dir>/workflows/<id>/``): ``clarify_events.jsonl`` (shown / answered / skipped /
reopened / test_failed, value-free), ``clarify.json`` (``ledger_report``, ``limits``, step count) and, through its
``skill_id``, ``<data_dir>/skills/<skill_id>/edits.jsonl`` (what a reviewer or the copilot changed afterwards).
It judges nothing by itself: the numbers below are the evidence for tuning ``scoring.py``.

Too many questions?   skip rate and reopen rate per kind; vacuous / duplicate counts; questions per workflow.
Guesses right?        how often the pre-selected guess was the answer kept.
Scoring right?        in shadow mode, how often an answer to a question scoring would have assumed or deferred was
                      the compiler's default anyway (high = it was safe to not ask).
Anything missed?      edits after the build to a field that no question of the same facet had covered (``misses``),
                      and failed tests (``test_failed``).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from conxa_compile.conversation.clarify_events import FACET_OF_EDIT_FIELD


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def summarize(data_dir: Path) -> dict[str, Any]:
    """Totals over every workflow that has a ``clarify_events.jsonl``. Pure apart from reading files."""
    kinds: dict[str, Counter] = defaultdict(Counter)
    guess = Counter()
    guess_by_kind: dict[str, Counter] = defaultdict(Counter)
    shadow = Counter()
    seconds: list[float] = []
    ledger = Counter()
    cuts = Counter()
    misses: Counter = Counter()
    per_workflow: list[dict[str, Any]] = []
    root = Path(data_dir)
    for wf_dir in sorted((root / "workflows").glob("*")):
        events = _read_jsonl(wf_dir / "clarify_events.jsonl")
        if not events:
            continue
        shown_at: dict[str, float] = {}
        facets_asked: set[str] = set()
        asked = 0
        for e in events:
            kind, qid, name = e.get("kind") or "", e.get("qid") or "", e.get("event")
            if name == "shown":
                asked += 1
                shown_at[qid] = e.get("t", 0)
                kinds[kind]["shown"] += 1
                if e.get("facet"):
                    facets_asked.add(e["facet"])
            elif name in ("answered", "skipped", "reopened"):
                kinds[kind][name] += 1
                if name == "answered" and qid in shown_at and e.get("t"):
                    seconds.append(e["t"] - shown_at[qid])
                if name == "answered" and "kept_guess" in e:
                    guess["with_guess"] += 1
                    guess["kept"] += bool(e["kept_guess"])
                    guess_by_kind[kind]["with_guess"] += 1
                    guess_by_kind[kind]["kept"] += bool(e["kept_guess"])
            if e.get("would"):
                shadow["would_" + e["would"]] += 1
                if name == "answered" and e["would"] in ("assume", "assume_show", "defer") and e.get("default") is not None:
                    shadow["quiet_answered"] += 1
                    shadow["quiet_was_default"] += e.get("choice") == e["default"]
        clar = _read_json(wf_dir / "clarify.json")
        for stage, rules in (clar.get("ledger_report") or {}).items():
            for rule, v in rules.items():
                ledger[f"{stage}.{rule}"] += int((v or {}).get("count") or 0)
        for kind, n in (clar.get("limits") or {}).items():
            cuts[kind] += int(n)
        skill_id = clar.get("skill_id")
        miss_here = 0
        if skill_id:
            for edit in _read_jsonl(root / "skills" / str(skill_id) / "edits.jsonl"):
                if edit.get("source") not in ("human", "copilot"):
                    continue
                facet = FACET_OF_EDIT_FIELD.get(str(edit.get("field")))
                if facet and facet not in facets_asked:
                    misses[facet] += 1
                    miss_here += 1
        failed = sum(1 for e in events if e.get("event") == "test_failed")
        per_workflow.append({"workflow": wf_dir.name, "asked": asked, "steps": clar.get("step_count_at_compile"),
                             "misses": miss_here, "test_failed": failed})
    return {
        "workflows": len(per_workflow),
        "kinds": {k: {**dict(v), "skip_rate": _rate(v["skipped"], v["shown"]), "reopen_rate": _rate(v["reopened"], v["shown"])}
                  for k, v in sorted(kinds.items())},
        "guess_kept": {"overall": _rate(guess["kept"], guess["with_guess"]), "n": guess["with_guess"],
                       "by_kind": {k: _rate(v["kept"], v["with_guess"]) for k, v in sorted(guess_by_kind.items())}},
        "shadow": {**dict(shadow), "quiet_answers_that_were_the_default": _rate(shadow["quiet_was_default"], shadow["quiet_answered"])},
        "median_seconds_to_answer": round(statistics.median(seconds), 1) if seconds else None,
        "ledger_report": dict(ledger), "silent_cuts": dict(cuts), "misses_by_facet": dict(misses),
        "questions_per_workflow": per_workflow,
    }


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["rates"])
    ap.add_argument("--data-dir", type=Path, default=None, help="the Studio data dir (default: conxa_core settings)")
    ap.add_argument("--json", type=Path, default=None, help="also write the totals here")
    args = ap.parse_args(argv)
    data_dir = args.data_dir
    if data_dir is None:
        from conxa_core.config import settings

        data_dir = settings.data_dir
    out = summarize(data_dir)
    print(json.dumps(out, indent=2))
    if args.json:
        args.json.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
