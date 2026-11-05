"""One counter for what a guard does to a model answer (Engine v2, C1).

The understanding check already ran every answer through five stages and counted each rule it
applied, so a bad prompt shows up as a number instead of a silence. This module is that counter,
lifted out so every other guard can count its drops the same way. A guard still decides what it
keeps; this only records what it did. ``report`` is plain JSON: ``{stage: {rule: {count, example}}}``.

``ref`` is always an id (a step key, a question id, a candidate ref), never model text or page text,
so a ledger line holds no customer data.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from conxa_compile.compiler.selector_filters import scrub_email
from conxa_core.config import settings

STAGES = ("shape", "reference", "override", "backfill", "safety")

LEDGER_FILE = "model_ledger.jsonl"


def _text(value: Any, limit: int) -> str:
    """Model text a person will read: one line, no email address (an account label carries one)."""
    return scrub_email(str(value or ""))[:limit]


def log(report: dict[str, Any] | None, stage: str, rule: str, example: Any = "") -> None:
    """Count one use of ``rule`` under ``stage``; the first example is kept. A ``None`` report is a no-op,
    so a guard can be called without a ledger."""
    if report is None:
        return
    slot = report.setdefault(stage, {}).setdefault(rule, {"count": 0, "example": _text(example, 60)})
    slot["count"] += 1


def count(report: dict[str, Any], stage: str, *rules: str) -> int:
    got = report.get(stage) or {}
    return sum(got[r]["count"] for r in (rules or tuple(got)) if r in got)


def summary(report: dict[str, Any]) -> dict[str, int]:
    """The line a person reads: shape and reference rules drop something, override and safety change
    something, backfill adds something."""
    return {
        "dropped": count(report, "shape") + count(report, "reference"),
        "changed": count(report, "override") + count(report, "safety"),
        "added": count(report, "backfill"),
    }


def flat(report: dict[str, Any]) -> dict[str, int]:
    """``{"stage.rule": n}``, the form the golden tests and the eval script compare."""
    return {f"{stage}.{rule}": slot["count"] for stage, rules in report.items() for rule, slot in rules.items()}


def entry(task: str, report: dict[str, Any], *, prompt_version: Any = "", grade: str = "",
          attempts: int = 1, applied: int = 0) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task": task,
        "prompt_version": prompt_version,
        "attempts": attempts,
        "grade": grade,
        "report": report,
        "applied": applied,
    }


def enabled() -> bool:
    """``SKILL_MODEL_LEDGER=off`` stops the file and the compile-report copy; counting itself is free."""
    return str(getattr(settings, "model_ledger", "observe")).lower() != "off"


def append(folder: Path | None, line: dict[str, Any]) -> None:
    """Add one line to ``<folder>/model_ledger.jsonl``. Never raises: a ledger is advice, not a gate."""
    if folder is None or not enabled():
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / LEDGER_FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def last_per_task(folder: Path | None) -> dict[str, dict[str, Any]]:
    """The newest line for each task, for Human Review."""
    out: dict[str, dict[str, Any]] = {}
    if folder is None:
        return out
    try:
        for raw in (folder / LEDGER_FILE).read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("task"):
                out[str(row["task"])] = row
    except OSError:
        pass
    return out
