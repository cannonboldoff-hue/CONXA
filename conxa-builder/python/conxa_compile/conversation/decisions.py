"""Answers to the pre-compile questions, stored as decisions and replayed after compile.

A decision is keyed by recorded event id (never by step index), so it survives a recompile that
renumbers or merges steps. Storing one applies nothing: the compile replays them through the same
safe edits Clarify already uses. Lives beside the recording (``decisions.json`` in the session
folder), so a recompile, an edit or a re-answered question keeps every earlier answer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FILE = "decisions.json"


def load(session_dir: Path) -> list[dict[str, Any]]:
    path = Path(session_dir) / FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def decision_from_answer(question: dict[str, Any], answer: dict[str, Any] | None) -> dict[str, Any]:
    """What to remember about one answered question. ``answer`` is the card's reply:
    ``{"choice": id}``, ``{"selected": [ids]}`` or ``None``/``{}`` for a skipped question."""
    answer = answer if isinstance(answer, dict) else {}
    decision: dict[str, Any] = {
        "qid": question["id"], "kind": question["kind"],
        "event_ids": [e for e in question.get("event_ids") or [] if e is not None],
        "row_ref": question.get("row_ref") or "", "skipped": not (answer.get("choice") or answer.get("selected")),
    }
    if answer.get("choice"):
        decision["choice"] = str(answer["choice"])
    if question["kind"] == "value":
        # Only "different every time" changes anything; "always this one" leaves the recording as it is.
        picked = answer.get("choice") == "varies"
        decision["values"] = [
            {"ref": i["ref"], "name": i["name"], "literal": i["literal"], "kind": i.get("kind", ""),
             "event_ids": i.get("event_ids") or []}
            for i in question.get("items") or [] if picked
        ]
    if question["kind"] == "note" and decision.get("choice"):
        label = next((c["label"] for c in question.get("choices") or [] if c.get("id") == decision["choice"]), "")
        decision["note"] = f"{question.get('title') or ''} {label}".strip()
    return decision


def record(session_dir: Path, question: dict[str, Any], answer: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Store (or replace) the decision for ``question``. Returns every stored decision."""
    decisions = [d for d in load(session_dir) if d.get("qid") != question["id"]]
    decisions.append(decision_from_answer(question, answer))
    (Path(session_dir) / FILE).write_text(json.dumps(decisions), encoding="utf-8")
    return decisions
