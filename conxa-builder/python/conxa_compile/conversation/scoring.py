"""Decide whether a question is worth a person's attention: ask, confirm, assume, assume and show, or defer.

The rule (docs/reports/Clarifying-Questions-Architecture-Investigation.md, 5.6): a wrong guess costs
``chance wrong x cost x runs it touches``; ask when that is high, otherwise let the compiler's default stand
and say so, confirm with a default, or leave it for a test to point at. Pure; no model call. The numbers are
starting values read off the question kind, to be tuned against the answers that phase 4 logs.

Three rules sit above the arithmetic and are not tunable:

- **Safety floor.** A decision popup, a step that cannot be undone and anything whose guess is unsafe always
  ask, and are never assumed or deferred (CLAUDE.md invariants).
- **A guess is a suggestion until the person confirms** (the understanding-check invariant). So a question
  asked BEFORE the build (``phase == "pre_compile"``) is never assumed or deferred: nothing stands in for the
  person's answer there. Only a question about the compiled document can be assumed, and only when one of its
  answers edits nothing, i.e. the compiler's own default.
- A question is assumed only if that no-edit answer exists (``default_answer``); otherwise it asks.
"""

from __future__ import annotations

from typing import Any

from conxa_compile.conversation import clarify, unknowns

CUTS = (0.08, 0.2, 0.4)        # chance wrong x cost: below the first assume, then assume and show, then confirm, else ask

# How much a wrong default hurts, 0..1 (cost of the mistake x how many runs it touches).
COST: dict[str, float] = {
    "confirm": 1.0, "confirm_before": 1.0, "fork": 1.0, "fork_then": 1.0, "known_popup": 1.0,
    "upload": 0.8, "value": 0.6, "chosen_values": 0.6, "values": 0.6,
    "missing_item": 0.5, "relative_date": 0.5, "note": 0.5, "test_followup": 0.5, "input_check": 0.5,
    "coverage": 0.4, "skip_when_empty": 0.3, "done_when": 0.3, "popup": 0.3, "popup_dismiss": 0.3,
    "output": 0.2, "dropped_value": 0.2,
}
# Chance the compiler's default is wrong when nothing else is known about the question.
P_WRONG: dict[str, float] = {
    "values": 0.5, "relative_date": 0.5, "missing_item": 0.5, "skip_when_empty": 0.3, "popup": 0.4,
    "done_when": 0.3, "output": 0.3, "dropped_value": 0.1,
}
# Kinds a failing test would point at by itself, so asking up front is wasted: left for the test.
DEFERRABLE = frozenset({"skip_when_empty", "done_when", "output", "relative_date"})
_OPEN = ("open", "queued")


def p_wrong(q: dict[str, Any]) -> float:
    if q.get("phase") == "pre_compile":
        return 0.25 if q.get("best_guess") else 0.5      # a model's guess is a suggestion, an unsure one is a coin flip
    return P_WRONG.get(str(q.get("kind")), 0.5)


def _with_memory(p: float, q: dict[str, Any], default: tuple[dict[str, Any], str] | None) -> float:
    """What this person usually answers on this app (``q["memory"]``, from answer_memory) moves the chance the
    compiler's default is wrong: halves it when they usually keep it, raises it when they usually change it.
    Needs at least two earlier answers and a clear majority; never touches a safety-floor question (decided before)."""
    mem = q.get("memory") or {}
    total = sum(mem.values())
    if total < 2 or default is None or "choice" not in default[0]:
        return p
    top, n = max(mem.items(), key=lambda kv: kv[1])
    if n / total < 0.8:
        return p
    return p * 0.5 if top == default[0]["choice"] else min(1.0, p * 1.5)


def default_answer(q: dict[str, Any], doc: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """The answer that edits nothing (what the compiler already did) with its receipt, or None if every
    answer edits the workflow. Never raises."""
    ids = [c.get("id") for c in q.get("choices") or []]
    tries = [{"selected": ids}, {"selected": []}] if q.get("multi") else [{"choice": c} for c in ids]
    for answer in tries:
        try:
            ops, receipt = clarify.plan_answer(q, answer, doc)
        except Exception:  # noqa: BLE001
            continue
        if not ops:
            return answer, receipt
    return None


def decide(q: dict[str, Any], doc: dict[str, Any] | None = None) -> dict[str, Any]:
    """``{"action", "p", "cost", "score", "reason"}``. ``doc`` (the compiled document) is needed to know
    whether the compiler's default can stand in for an answer."""
    kind = str(q.get("kind") or "")
    p, cost = p_wrong(q), COST.get(kind, 0.5)
    score = round(p * cost, 4)
    out = {"p": p, "cost": cost, "score": score}
    if kind in unknowns.SAFETY_KINDS:
        return {**out, "action": "ask", "reason": "safety floor"}
    if q.get("phase") == "pre_compile":
        # Never assumed or deferred: only whether it is a quick confirmation (a guess is ticked) or a real ask.
        return {**out, "action": "confirm" if q.get("best_guess") and score < CUTS[2] else "ask", "reason": "asked before the build"}
    default = default_answer(q, doc) if doc is not None else None
    if q.get("memory"):
        p = _with_memory(p, q, default)
        score = round(p * cost, 4)
        out = {"p": p, "cost": cost, "score": score}
    out["default"] = default[0].get("choice") if default and "choice" in default[0] else None     # for the log: was it kept?
    if score >= CUTS[2]:
        return {**out, "action": "ask", "reason": "likely wrong and costly"}
    if doc is None or default is None:
        return {**out, "action": "confirm", "reason": "every answer edits the workflow"}
    if score < CUTS[0]:
        return {**out, "action": "assume", "reason": "cheap if wrong"}
    if score < CUTS[1]:
        return {**out, "action": "assume_show", "reason": "cheap enough to assume, shown in the summary"}
    return {**out, "action": "defer" if kind in DEFERRABLE else "confirm",
            "reason": "a test will show it" if kind in DEFERRABLE else "worth one tap"}
