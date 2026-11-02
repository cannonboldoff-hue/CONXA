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

from conxa_compile.compiler.step_key import step_keys
from conxa_compile.conversation import clarify
from conxa_compile.editor.workflow_mutations import click_text_label

# decision kind -> (compile-time question kind, pre-compile choice id -> compile-time choice id)
_MAP: dict[str, tuple[str, dict[str, str]]] = {
    "upload": ("upload", {"each_run": "input", "fixed_path": "fixed"}),
    "confirm_before": ("confirm", {"confirm": "yes", "no_confirm": "no"}),
    "popup_dismiss": ("popup", {"dismiss": "dismiss", "keep": "once"}),
}
_FORK_CHOICES = {"by_input", "ask_human", "stop"}     # never an "option:*": a person picks a popup button
# Pre-compile choices that ask the workflow to change (the others say "leave it as recorded").
_CHANGES = {"each_run", "confirm", "dismiss"}
# What an unapplied answer was about, in words a person recognises.
SUBJECT = {"upload": "the file", "confirm_before": "checking before a step", "popup_dismiss": "the popup"}


def plan(
    decisions: list[dict[str, Any]],
    doc: dict[str, Any],
    questions: list[dict[str, Any]],
    event_id_of_step_key: Callable[[str], Any],
) -> dict[str, Any]:
    """``{"groups": [...], "unapplied": [...]}``. A group is ``{"label", "ops", "receipt",
    "qid" (the compile-time question it answers, or None), "answer", "value" (a chosen value)}``.
    ``unapplied`` lists every upload / confirm / popup answer that asked for a change but matched nothing
    in the built workflow (``{"kind", "choice"}``): such an answer is never dropped without the person
    being told. (A popup decision that matches nothing is asked again after the compile, so it isn't listed.)"""
    groups: list[dict[str, Any]] = []
    unapplied: list[dict[str, Any]] = []
    steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
    keyed = list(zip(step_keys(steps), steps))

    def clicked_label(event_ids: set[Any]) -> str:
        """The visible label the compiled click steps for these recorded events are known by: the very
        text ``parameterize_click_text`` matches (the timeline's own label can differ from it)."""
        for key, step in keyed:
            if event_id_of_step_key(key) in event_ids:
                label = click_text_label(step)
                if label:
                    return label
        return ""

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
        if kind in ("value", "chosen_values"):
            for v in d.get("values") or []:
                literal = (clicked_label({e for e in v.get("event_ids") or [] if e is not None})
                           if v.get("kind") == "click" else "") or v["literal"]
                item = {"ref": v["ref"], "name": v["name"], "literal": literal, "kind": v.get("kind", "")}
                ops, receipt = clarify.plan_answer({"kind": "chosen_values", "items": [item]}, {"selected": [v["ref"]]}, doc)
                groups.append({"label": v["name"], "ops": ops, "receipt": receipt, "qid": None, "answer": None, "value": v})
        elif kind in _MAP and d.get("choice") in _MAP[kind][1]:
            ck, cmap = _MAP[kind]
            answer = {"choice": cmap[d["choice"]]}
            matched = questions_for(ck, events)
            for q in matched:
                ops, receipt = clarify.plan_answer(q, answer, doc)
                groups.append({"label": q["title"], "ops": ops, "receipt": receipt, "qid": q["id"], "answer": answer, "value": None})
            # "Leave it as recorded" changes nothing by design; only an answer that asked for a change is owed a report.
            if not matched and d["choice"] in _CHANGES:
                unapplied.append({"kind": kind, "choice": d["choice"]})
        elif kind == "fork" and d.get("choice") in _FORK_CHOICES:
            # Matched by the popup's own recorded event only. A popup that doesn't trace back to this event
            # is asked about after the compile: never guess which popup an answer was meant for.
            answer = {"choice": d["choice"]}
            for q in questions_for("fork", events):
                ops, receipt = clarify.plan_answer(q, answer, doc)
                groups.append({"label": q["title"], "ops": ops, "receipt": receipt, "qid": q["id"], "answer": answer, "value": None})
    return {"groups": groups, "unapplied": unapplied}
