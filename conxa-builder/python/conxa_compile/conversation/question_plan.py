"""One review step for the questions both generators produce (ledger, vacuity, slot dedupe).

``review`` annotates every question with its facet and slot, then finds the ones that would be dropped:
a post-compile question whose every answer produces the same edits, and a second question about a slot
already asked. In ``report`` mode (the default) it only counts them in the returned ledger report; in
``enforce`` mode it also removes them; ``off`` does nothing. Run it AFTER decisions are replayed: replay
can only apply an answer to a question that still exists.
"""

from __future__ import annotations

from typing import Any, Callable

from conxa_compile.conversation import unknowns
from conxa_compile.conversation.timeline_understanding import _log


def mode() -> str:
    from conxa_core.config import settings

    value = str(getattr(settings, "clarify_ledger", "report") or "report").lower()
    return value if value in ("off", "report", "enforce") else "report"


def review(
    questions: list[dict[str, Any]],
    *,
    doc: dict[str, Any] | None,
    event_id_of_step_key: Callable[[str], Any] | None,
    found_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """``(questions, ledger_report)``. ``doc`` is the compiled document (None before the compile).
    The report has the same stage/rule/count/example shape as ``validation_report``."""
    report: dict[str, Any] = {}
    chosen = mode()
    if chosen == "off":
        return questions, report
    for q in questions:
        unknowns.annotate(q, event_id_of_step_key, found_at)
    gone: set[int] = set()
    if doc is not None:
        for q in questions:
            if unknowns.vacuous(q, doc):
                _log(report, "override", "vacuous", f"{q['kind']}:{q.get('id', '')}")
                gone.add(id(q))
    _, dupes = unknowns.dedupe([q for q in questions if id(q) not in gone])
    for q in dupes:
        _log(report, "override", "duplicate_slot", f"{q['kind']}:{q['unknown']['slot']}")
        gone.add(id(q))
    if chosen == "enforce":
        questions = [q for q in questions if id(q) not in gone]
    return questions, report


def scoring_mode() -> str:
    from conxa_core.config import settings

    value = str(getattr(settings, "clarify_scoring", "off") or "off").lower()
    return value if value in ("off", "shadow", "on") else "off"


def apply_scoring(
    questions: list[dict[str, Any]], doc: dict[str, Any] | None, found_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(questions, assumptions)``. ``shadow`` only stores each open question's decision on it
    (``q["decision"]``) and changes nothing. ``on`` also acts, for post-compile questions only: an
    ``assume`` / ``assume_show`` question keeps the compiler's default (it is parked as ``queued`` with
    ``assumed`` set, so "ask me" still opens it) and a ``defer`` question waits (``deferred``) until a test
    points at its steps. Questions asked before the build are never assumed or deferred (scoring.decide)."""
    chosen = scoring_mode()
    if chosen == "off":
        return questions, []
    from conxa_compile.conversation import answer_memory, scoring

    host = answer_memory.host_of(doc) if doc is not None and found_at == "post_compile" else ""
    for q in questions:
        if q.get("status") in ("open", "queued"):
            if host and (seen := answer_memory.recall(host, q)):
                q["memory"] = seen
            q["decision"] = scoring.decide(q, doc if found_at == "post_compile" else None)
    assumptions: list[dict[str, Any]] = []
    if chosen == "on" and found_at == "post_compile" and doc is not None:
        for q in questions:
            action = (q.get("decision") or {}).get("action")
            if q.get("status") != "open":
                continue
            if action in ("assume", "assume_show"):
                _, receipt = scoring.default_answer(q, doc) or ({}, "")
                q.update(status="queued", assumed=True)
                assumptions.append({"qid": q["id"], "kind": q["kind"], "title": q["title"], "default": receipt})
            elif action == "defer":
                q["status"] = "deferred"
    return questions, assumptions
