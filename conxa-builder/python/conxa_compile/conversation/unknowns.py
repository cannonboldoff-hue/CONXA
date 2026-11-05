"""The slot ledger: every clarifying question, from either generator, names the thing it settles.

A question is an *unknown* about one slot of the finished workflow (docs/reports/Clarifying-Questions-
Architecture-Investigation.md, section 5). This module only labels and checks; it never asks and never edits.

- ``annotate`` gives a question its ``facet`` (what kind of knowledge it fills) and ``slot`` (which part of
  the workflow it is about). A slot is named by **recorded event ids**, never step indexes, so it survives a
  recompile that renumbers or merges steps (same rule as ``decisions.py``).
- ``vacuous`` says a question whose every answer produces the same edits cannot matter.
- ``dedupe`` keeps one question per slot.

Everything is pure. Safety kinds (a decision popup, a step that cannot be undone) are never dropped.
"""

from __future__ import annotations

from typing import Any, Callable

from conxa_compile.conversation import clarify

# What each question kind fills. The first six Q1 effects and every Q2 kind are here; `other` is for a
# free-text `note`, which must name its own slot.
FACET_OF_KIND: dict[str, str] = {
    "value": "variability", "chosen_values": "variability", "values": "variability", "upload": "variability",
    "relative_date": "provenance",
    "skip_when_empty": "input_domain", "test_followup": "variability", "input_check": "input_domain",
    "missing_item": "alternatives", "fork": "alternatives", "fork_then": "alternatives", "known_popup": "alternatives",
    "popup": "alternatives", "popup_dismiss": "alternatives",
    "confirm": "consequence", "confirm_before": "consequence",
    "output": "postcondition", "done_when": "postcondition",
    "dropped_value": "scope", "coverage": "scope",
    "note": "other",
}
# Two kinds on the same events are one slot only when they are the same sub-question (a popup's fork and its
# follow-up are different questions about one step).
_PART = {"confirm_before": "confirm", "popup_dismiss": "popup", "value": "value", "chosen_values": "value", "values": "value"}
# Never dropped, never pre-answered: a decision popup is not answered for anyone, an irreversible step always asks.
SAFETY_KINDS = frozenset({"fork", "fork_then", "known_popup", "confirm", "confirm_before"})
# Questions that change nothing by design (they only inform): they cannot be "vacuous".
ADVISORY_KINDS = frozenset({"coverage", "dropped_value", "input_check", "note"})
_PHASE_OPEN = ("open", "queued")


def priority(q: dict[str, Any]) -> float:
    """Lower asks first. A Q2 kind uses ``clarify._PRIORITY``; a Q1 question carries the model's ``cost_rank``.
    Never raises on a kind the table does not know."""
    if q.get("phase") == "pre_compile":
        rank = q.get("cost_rank")
        return float(rank) if isinstance(rank, (int, float)) and not isinstance(rank, bool) else 99.0
    return float(clarify._PRIORITY.get(q.get("kind"), 9))


def _events(q: dict[str, Any], event_id_of_step_key: Callable[[str], Any] | None) -> list[str]:
    found = [e for e in q.get("event_ids") or [] if e is not None]
    if not found and event_id_of_step_key:
        found = [e for k in q.get("step_keys") or [] if (e := event_id_of_step_key(k)) is not None]
    return sorted({str(e) for e in found})


def facet_of(q: dict[str, Any]) -> str:
    """What the question fills. A ``note`` from the second read names its own facet; every other kind has a fixed one."""
    kind = str(q.get("kind") or "")
    return str(q["facet"]) if kind == "note" and q.get("facet") else FACET_OF_KIND.get(kind, "other")


def slot_of(q: dict[str, Any], event_id_of_step_key: Callable[[str], Any] | None = None) -> str:
    kind = str(q.get("kind") or "")
    facet = facet_of(q)
    events = _events(q, event_id_of_step_key)
    state = f"#{q['fork_state_id']}" if q.get("fork_state_id") else ""
    return f"{facet}/{_PART.get(kind, kind)}:{','.join(events) or q.get('id', '')}{state}"


def annotate(q: dict[str, Any], event_id_of_step_key: Callable[[str], Any] | None, found_at: str) -> dict[str, Any]:
    """Add ``q["unknown"]`` in place and return ``q``. Additive: nothing that reads a question looks at it yet."""
    kind = str(q.get("kind") or "")
    q["unknown"] = {"facet": facet_of(q), "slot": slot_of(q, event_id_of_step_key),
                    "found_at": found_at, "safety_floor": kind in SAFETY_KINDS}
    return q


def vacuous(q: dict[str, Any], doc: dict[str, Any]) -> bool:
    """True when every answer to a compiled-document question produces the same edits. Only for post-compile
    questions that name steps: a Q1 question applies nothing until the compile, so every answer looks empty."""
    if (q.get("kind") in ADVISORY_KINDS or q.get("phase") == "pre_compile" or not q.get("step_keys")
            or q.get("status") not in _PHASE_OPEN):
        return False
    ids = [c.get("id") for c in q.get("choices") or []]
    if len(ids) < (1 if q.get("multi") else 2):
        return False        # a single-answer question with one choice has nothing to compare
    try:
        if q.get("multi"):
            outcomes = [clarify.plan_answer(q, {"selected": picked}, doc)[0] for picked in ([], ids)]
        else:
            outcomes = [clarify.plan_answer(q, {"choice": c}, doc)[0] for c in ids]
    except Exception:  # noqa: BLE001 -- a question the planner cannot judge is kept
        return False
    return all(o == outcomes[0] for o in outcomes)


def dedupe(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(kept, dropped)``: one open/queued question per slot, the one that asks first wins. Answered and
    skipped questions are history and never dropped; a safety kind is never dropped."""
    best: dict[str, dict[str, Any]] = {}
    for q in questions:
        slot = (q.get("unknown") or {}).get("slot")
        if slot and q.get("status") in _PHASE_OPEN and (slot not in best or priority(q) < priority(best[slot])):
            best[slot] = q
    kept, dropped = [], []
    for q in questions:
        slot = (q.get("unknown") or {}).get("slot")
        if (slot and q.get("status") in _PHASE_OPEN and best.get(slot) is not q
                and not (q.get("unknown") or {}).get("safety_floor")):
            dropped.append(q)
        else:
            kept.append(q)
    return kept, dropped
