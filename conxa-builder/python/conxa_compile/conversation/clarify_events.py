"""A value-free log of what happened to each clarifying question, one JSON line each.

``data/workflows/<id>/clarify_events.jsonl``. It holds question ids, kinds, facets and choice ids only, never a typed
value, a page text or a person's own words, so it can be read by ``scripts/eval_clarify.py`` and shared. It answers
"is the system asking too much, and was it asking the right things": which questions were skipped or taken back, how
often a pre-selected guess was kept, what scoring would have done, and which failures a question could have prevented.

Failure-silent like the thread store: a log that cannot be written never breaks a flow.
"""

from __future__ import annotations

import json
import time
from typing import Any

from conxa_core.config import settings

FILE = "clarify_events.jsonl"
# A reviewer's edit to one of these fields, in edits.jsonl, is the answer to a question nobody asked.
FACET_OF_EDIT_FIELD = {
    "input_binding": "variability", "value": "variability", "optional_hint": "alternatives", "branch": "alternatives",
    "validation.assertions": "postcondition", "_step": "scope",
}


def _path(workflow_id: str):
    return settings.data_dir / "workflows" / workflow_id / FILE


def log(workflow_id: str, event: str, question: dict[str, Any] | None = None, **extra: Any) -> None:
    """Append one event. ``question`` contributes its id, kind, phase, facet, guess and scoring decision."""
    record: dict[str, Any] = {"t": round(time.time(), 3), "event": event}
    if question:
        unknown = question.get("unknown") or {}
        decision = question.get("decision") or {}
        record.update({
            "qid": question.get("id"), "kind": question.get("kind"), "phase": question.get("phase") or "post_compile",
            "facet": unknown.get("facet") or "", "slot": unknown.get("slot") or "",
            **({"guess": question["best_guess"]} if question.get("best_guess") else {}),
            **({"would": decision["action"], "default": decision.get("default")} if decision.get("action") else {}),
            **({"assumed": True} if question.get("assumed") else {}),
        })
    record.update({k: v for k, v in extra.items() if v is not None})
    try:
        path = _path(workflow_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def answered(workflow_id: str, question: dict[str, Any], answer: dict[str, Any] | None) -> None:
    """The choice ids only. ``kept_guess`` says whether the pre-selected guess was the answer kept."""
    answer = answer or {}
    choice = answer.get("choice")
    log(workflow_id, "answered" if (choice or answer.get("selected")) else "skipped", question,
        choice=choice, selected=answer.get("selected") or None,
        kept_guess=(choice == question.get("best_guess")) if choice and question.get("best_guess") else None)


def read(workflow_id: str) -> list[dict[str, Any]]:
    try:
        lines = _path(workflow_id).read_text(encoding="utf-8").splitlines()
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
