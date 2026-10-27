"""Replay the pre-compile decisions onto a freshly compiled workflow.

A decision was made about a recorded event, before any step existed. After the compile, each one
is matched to the compile-time question that asks the same thing about the step that event became
(through the step's source event), and turned into ops by the very same ``clarify.plan_answer`` a
button press uses -- there is no second editing path. A decision that finds no matching step is
simply not applied, and the compile-time question stays to be asked (Q2).

Pure: it plans, the handler runs the ops (all-or-nothing per group) and records what was answered.
"""

from __future__ import annotations

from typing import Any, Callable

from conxa_compile.conversation import clarify

# decision kind -> (compile-time question kind, pre-compile choice id -> compile-time choice id)
_MAP: dict[str, tuple[str, dict[str, str]]] = {
    "upload": ("upload", {"each_run": "input", "fixed_path": "fixed"}),
    "confirm_before": ("confirm", {"confirm": "yes", "no_confirm": "no"}),
    "popup_dismiss": ("popup", {"dismiss": "dismiss", "keep": "once"}),
}
_FORK_CHOICES = {"by_input", "ask_human", "stop"}     # never an "option:*": a person picks a popup button


def plan(
    decisions: list[dict[str, Any]],
    doc: dict[str, Any],
    questions: list[dict[str, Any]],
    event_id_of_step_key: Callable[[str], Any],
) -> dict[str, Any]:
    """``{"groups": [...], "description": str | None}``. A group is ``{"label", "ops", "receipt",
    "qid" (the compile-time question it answers, or None), "answer", "value" (a chosen value)}``."""
    groups: list[dict[str, Any]] = []
    description: str | None = None

    def questions_for(kind: str, event_ids: set[Any]) -> list[dict[str, Any]]:
        out = []
        for q in questions:
            if q.get("kind") != kind or q.get("status") not in ("open", "queued"):
                continue
            if event_ids & {event_id_of_step_key(k) for k in q.get("step_keys") or []}:
                out.append(q)
        return out

    for d in decisions:
        if d.get("skipped"):
            continue
        kind = d.get("kind")
        events = {e for e in d.get("event_ids") or [] if e is not None}
        if kind == "confirm_summary" and d.get("description"):
            description = str(d["description"])
        elif kind == "chosen_values":
            for v in d.get("values") or []:
                item = {"ref": v["ref"], "name": v["name"], "literal": v["literal"], "kind": v.get("kind", "")}
                ops, receipt = clarify.plan_answer({"kind": "chosen_values", "items": [item]}, {"selected": [v["ref"]]}, doc)
                groups.append({"label": v["name"], "ops": ops, "receipt": receipt, "qid": None, "answer": None, "value": v})
        elif kind in _MAP and d.get("choice") in _MAP[kind][1]:
            ck, cmap = _MAP[kind]
            answer = {"choice": cmap[d["choice"]]}
            for q in questions_for(ck, events):
                ops, receipt = clarify.plan_answer(q, answer, doc)
                groups.append({"label": q["title"], "ops": ops, "receipt": receipt, "qid": q["id"], "answer": answer, "value": None})
        elif kind == "fork" and d.get("choice") in _FORK_CHOICES:
            forks = questions_for("fork", events)
            if not forks:
                # The popup's own step may not trace back to an event: with exactly one popup step, it is that one.
                open_forks = [q for q in questions if q.get("kind") == "fork" and q.get("status") in ("open", "queued")]
                forks = open_forks if len({tuple(q.get("step_keys") or []) for q in open_forks}) == 1 else []
            answer = {"choice": d["choice"]}
            for q in forks:
                ops, receipt = clarify.plan_answer(q, answer, doc)
                groups.append({"label": q["title"], "ops": ops, "receipt": receipt, "qid": q["id"], "answer": answer, "value": None})
    return {"groups": groups, "description": description}


def failed_values_question(values: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A compile-time question for chosen values that could not be applied (the recorded text is not in
    the compiled steps), so the person can retry or leave them. Same card as the original."""
    cands = [{"ref": v["ref"], "kind": v.get("kind") or "url", "supported": True, "verdict": "varies", "name": v["name"],
              "literal": v["literal"], "recorded": v["literal"], "step_keys": []} for v in values]
    q = clarify._chosen_values_question({"candidates": cands}) if cands else None
    if q:
        q["body"] = "I couldn’t find these in the built steps, so they stayed as you recorded them. " + q["body"]
    return q
