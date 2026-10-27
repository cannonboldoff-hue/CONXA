"""Understand a recording before it is compiled (Q1 of the understanding-first pipeline).

One text call reads the merged timeline (browser events lined up with what Pegasus saw) and
writes the understanding: goal, phases, which recorded values change each run, how success
shows, gaps, a short and a detailed summary, and the questions worth asking. Code does the rest:

- **Meaning only.** The model never sees or writes a selector. Every field of its answer is
  held to what code can act on (unknown refs, rows and kinds are dropped, a name is snake-cased
  and made unique, a ``literal`` must sit inside the recorded value).
- **A best guess is a suggestion.** It is pre-selected on a card and counts only when the
  person presses Confirm. It is stripped, in code, from any question where a wrong guess would
  approve something that cannot be undone, and never offered for a decision popup.
- **Never blocks.** Any failure or empty answer degrades to a summary built from the timeline
  and the likely candidates, unticked.

``call`` is the model seam; the default goes through ``conxa_compile.llm.client.call_llm``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from conxa_compile.conversation.clarify import _chosen_values_question, _coverage_question, _question

TASK = "timeline_understanding"
MAX_ROWS = 150
MAX_QUESTIONS = 5                 # shown at once; the rest are queued under "More questions"
MAX_CLICK_CANDIDATES = 6
MAX_TYPED_CANDIDATES = 6
MAX_URL_CANDIDATES = 8
_NAME_OK = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_VERDICTS = ("fixed", "varies", "unsure")
_FALLBACK_NAME = {"url": "page_address", "click": "item", "typed": "value"}

# kind -> {allowed best_guess: choice id}. A kind with no entry is never pre-filled.
GUESSES: dict[str, set[str]] = {
    "upload": {"each_run", "fixed_path"},
    "confirm_before": {"confirm", "no_confirm"},
    "popup_dismiss": {"dismiss", "keep"},
    "branch_or_oneoff": {"one_off", "branch"},
    "fork": set(),
    "done_when": set(),
}
# A guess that would approve a step that cannot be undone, or choose a popup option, is never pre-filled.
UNSAFE_GUESSES = {("confirm_before", "no_confirm")}

Caller = Callable[[str, dict[str, Any], int], "dict[str, Any] | None"]


def _snake(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:40].strip("_")


def _unique_name(base: str, taken: set[str], fallback: str) -> str:
    name = base if _NAME_OK.match(base) else (_snake(base).lstrip("0123456789_") or fallback)
    if not _NAME_OK.match(name):
        name = fallback
    cand, n = name, 2
    while cand.lower() in taken:
        cand = f"{name}_{n}"
        n += 1
    taken.add(cand.lower())
    return cand


def _url_literal(url: str) -> tuple[str, str] | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    path = unquote(parts.path or "").strip("/")
    query = unquote(parts.query or "")
    literal = path + (("?" + query) if query else "")
    return (unquote(url), literal) if literal else None


def _nav_url(row: dict[str, Any]) -> str:
    raw = row.get("value")
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            return str(json.loads(raw).get("to_url") or "")
        except ValueError:
            pass
    return str(raw or row.get("url") or "")


def _events(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in merged if r.get("kind") == "event"]


def candidates_from_timeline(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recorded values that may be specific to this recording: page addresses, clicked items known
    only by their label, and text typed into a field. ``{ref, kind, event_ids, row_refs, recorded, literal}``."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    counts = {"url": 0, "click": 0, "typed": 0}
    caps = {"url": MAX_URL_CANDIDATES, "click": MAX_CLICK_CANDIDATES, "typed": MAX_TYPED_CANDIDATES}
    for row in _events(merged):
        action = row.get("action")
        if action in ("navigate", "manual_navigate"):
            found = _url_literal(_nav_url(row))
            kind, recorded, literal = ("url", found[0], found[1]) if found else (None, "", "")
        elif action in ("click", "dblclick") and len(row.get("target") or "") >= 2:
            kind, recorded, literal = "click", row["target"], row["target"]
        elif action in ("type", "fill") and str(row.get("value") or "").strip() not in ("", "{{REDACTED}}"):
            kind, recorded, literal = "typed", str(row["value"]), str(row["value"]).strip()
        else:
            kind = None
        if kind is None:
            continue
        key = (kind, literal.lower())
        if key not in groups:
            if counts[kind] >= caps[kind]:
                continue
            counts[kind] += 1
            groups[key] = {"ref": f"{kind}:{row.get('event_id')}", "kind": kind, "event_ids": [], "row_refs": [],
                           "recorded": recorded[:200], "literal": literal[:200],
                           "label": str(row.get("target") or "")[:60]}
        groups[key]["event_ids"].append(row.get("event_id"))
        groups[key]["row_refs"].append(row["row_ref"])
    return list(groups.values())


def _seen(segment: dict[str, Any] | None) -> dict[str, Any] | None:
    if not segment:
        return None
    keep = ("visible_target", "intent", "purpose", "screen_before", "screen_after")
    return {k: segment[k] for k in keep if segment.get(k)}


def build_input(merged: list[dict[str, Any]], task: str, chat: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for r in merged[:MAX_ROWS]:
        if r.get("kind") == "event":
            value = r.get("value")
            rows.append({
                "ref": r["row_ref"], "kind": "event", "action": r.get("action"), "target": r.get("target"),
                "value": "(hidden)" if value == "{{REDACTED}}" else (str(value)[:80] if value else None),
                "url": r.get("url"), "seen": _seen(r.get("segment")),
            })
        else:
            rows.append({"ref": r["row_ref"], "kind": "observation", "type": r.get("type"),
                         "seconds": r.get("seconds"), "seen": _seen(r.get("segment"))})
    return {
        "task": (task or "").strip()[:300], "chat": (chat or "").strip()[:600], "rows": rows,
        "candidates": [{"ref": c["ref"], "kind": c["kind"], "recorded": c["recorded"], "rows": c["row_refs"]}
                       for c in candidates],
    }


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def validate(raw: Any, merged: list[dict[str, Any]], candidates: list[dict[str, Any]],
             existing_inputs: set[str] | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    rows = {r["row_ref"]: r for r in merged}
    order = {ref: i for i, ref in enumerate(rows)}
    by_ref = {c["ref"]: c for c in candidates}
    taken = {n.lower() for n in existing_inputs or set()}

    values: dict[str, dict[str, Any]] = {}
    for item in raw.get("values") or []:
        if not isinstance(item, dict) or item.get("ref") not in by_ref or item["ref"] in values:
            continue
        verdict = str(item.get("verdict") or "").lower()
        if verdict not in _VERDICTS:
            continue
        cand = by_ref[item["ref"]]
        literal = cand["literal"]
        proposed = _text(item.get("literal"), 200)
        if cand["kind"] == "url" and proposed and proposed.lower() in cand["recorded"].lower():
            literal = proposed
        name = ""
        if verdict != "fixed":
            name = _unique_name(_snake(str(item.get("name") or "")), taken, _FALLBACK_NAME[cand["kind"]])
        values[item["ref"]] = {"verdict": verdict, "name": name, "literal": literal}

    phases = []
    for p in raw.get("phases") or []:
        if isinstance(p, dict) and p.get("from_row") in rows and p.get("to_row") in rows \
                and order[p["from_row"]] <= order[p["to_row"]] and _text(p.get("name"), 60):
            phases.append({"name": _text(p["name"], 60), "from_row": p["from_row"], "to_row": p["to_row"]})

    gaps = [{"row": g["row"], "note": _text(g.get("note"), 200)} for g in raw.get("gaps") or []
            if isinstance(g, dict) and g.get("row") in rows and _text(g.get("note"), 200)]
    detailed = [{"row": d["row"], "text": _text(d.get("text"), 240)} for d in raw.get("detailed_summary") or []
                if isinstance(d, dict) and d.get("row") in rows and _text(d.get("text"), 240)]

    questions = []
    for q in raw.get("questions") or []:
        if not isinstance(q, dict) or q.get("kind") not in GUESSES or q.get("row") not in rows or not _text(q.get("prompt"), 240):
            continue
        guess = str(q.get("best_guess") or "")
        if guess not in GUESSES[q["kind"]] or (q["kind"], guess) in UNSAFE_GUESSES:
            guess = ""
        rank = q.get("cost_rank")
        questions.append({
            "kind": q["kind"], "row": q["row"], "event_id": rows[q["row"]].get("event_id"),
            "prompt": _text(q["prompt"], 240), "best_guess": guess,
            "cost_rank": rank if isinstance(rank, int) and not isinstance(rank, bool) else 99,
        })
    questions.sort(key=lambda q: (q["cost_rank"], order[q["row"]]))

    return {
        "goal": _text(raw.get("goal"), 200), "phases": phases, "values": values,
        "success_check": _text(raw.get("success_check"), 200), "gaps": gaps,
        "short_summary": _text(raw.get("short_summary"), 300), "detailed_summary": detailed,
        "questions": questions,
    }


def _fallback_detailed(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in merged:
        seg = r.get("segment") or {}
        text = _text(seg.get("intent") or seg.get("screen_after") or "", 240)
        if text:
            out.append({"row": r["row_ref"], "text": text})
    return out


def _default_call(task_name: str, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any] | None:
    from conxa_compile.llm.client import call_llm  # noqa: PLC0415 -- Studio-only, imported lazily

    return call_llm(task_name, {"input": payload}, timeout_ms)


def understand_timeline(
    merged: list[dict[str, Any]],
    task: str = "",
    chat: str = "",
    *,
    existing_inputs: set[str] | None = None,
    call: Caller | None = None,
    timeout_ms: int = 60_000,
) -> dict[str, Any]:
    """The checked understanding plus ``candidates`` (each with verdict/name/literal) and
    ``source``: ``model`` (usable answer), ``fallback`` (call failed or empty), ``none`` (nothing to
    look at). Never raises."""
    candidates = candidates_from_timeline(merged)
    result: dict[str, Any] = {"source": "none", "candidates": candidates, "goal": "", "phases": [], "values": {},
                              "success_check": "", "gaps": [], "short_summary": "", "detailed_summary": [], "questions": []}
    if not _events(merged):
        return result
    try:
        raw = (call or _default_call)(TASK, build_input(merged, task, chat, candidates), timeout_ms)
    except Exception:  # noqa: BLE001 -- advisory: a failed model call must never block the flow
        raw = None
    checked = validate(raw, merged, candidates, existing_inputs)
    usable = bool(checked["goal"] or checked["short_summary"] or checked["values"])
    result.update(checked)
    result["source"] = "model" if usable else "fallback"
    if not usable:
        taken = {n.lower() for n in existing_inputs or set()}
        if task.strip():
            for c in candidates:
                if c["kind"] == "url":
                    result["values"][c["ref"]] = {"verdict": "unsure", "literal": c["literal"],
                                                  "name": _unique_name("", taken, _FALLBACK_NAME["url"])}
        result["short_summary"] = f"{len(_events(merged))} recorded actions."
    if not result["detailed_summary"]:
        result["detailed_summary"] = _fallback_detailed(merged)
    for c in candidates:
        c.update(result["values"].get(c["ref"]) or {"verdict": "fixed", "name": "", "literal": c["literal"]})
    return result


# --- questions ------------------------------------------------------------------------------

_Q = {
    "upload": ("Is the file the same every time?", [
        ("each_run", "No, whoever uses it picks the file", "They are asked for the file before it starts."),
        ("fixed_path", "Yes, always use this file", "It uses the file you picked while recording.")]),
    "confirm_before": ("Should it check with someone before this step?", [
        ("confirm", "Yes, ask first", "A person approves this step each time before it happens."),
        ("no_confirm", "No, just do it", "It runs on its own.")]),
    "popup_dismiss": ("A message box appeared here. Should it close it when it shows up?", [
        ("dismiss", "Yes, close it", "If it shows up, it is closed. If not, it carries on."),
        ("keep", "No, leave it", "It does nothing special about it.")]),
    "branch_or_oneoff": ("Was this step something you always do, or just this once?", [
        ("branch", "Only sometimes", "It does this step only when it applies."),
        ("one_off", "Every time", "It does this step each time.")]),
    "fork": ("A message box asked for a decision. What should happen when it does?", [
        ("by_input", "Let whoever uses it decide each time", "They get a question to answer before it starts."),
        ("ask_human", "Stop and ask a person each time", "It waits on the popup until someone decides."),
        ("stop", "Stop completely", "Treat this popup as a problem and end here.")]),
}


def _choices(rows: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    return [{"id": i, "label": label, "detail": detail} for i, label, detail in rows]


def questions_from_understanding(und: dict[str, Any], merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clarify-shaped questions (``phase: "pre_compile"``), most important first. The first
    MAX_QUESTIONS are ``open``; the rest ``queued``. A card pre-selects ``best_guess`` but it only
    counts when the person presses Confirm. Answers become decisions (decisions.py), not edits."""
    rows = {r["row_ref"]: r for r in merged}
    out: list[dict[str, Any]] = []

    short = und.get("short_summary") or ""
    steps = [f"{d['text']}" for d in und.get("detailed_summary") or []]
    body = short + ("\n\n" + "\n".join(f"• {s}" for s in steps[:12]) if steps else "")
    out.append(_question("confirm_summary", "confirm_summary", "Is this what you recorded?", body, _choices([
        ("yes", "Yes, that’s right", "I’ll build it from this."),
        ("no", "No, something is wrong", "I’ll stop here so you can record it again.")]),
        phase="pre_compile", best_guess="yes", summary=short, goal=und.get("goal") or ""))

    if und.get("gaps"):
        q = _coverage_question(" ".join(g["note"] for g in und["gaps"][:2]))
        q["phase"] = "pre_compile"
        out.append(q)

    cands = [{**c, "supported": True, "step_keys": []} for c in und.get("candidates") or []
             if c.get("verdict") in ("varies", "unsure") and c.get("name")]
    chosen = _chosen_values_question({"candidates": cands}) if cands else None
    if chosen:
        by_ref = {c["ref"]: c for c in cands}
        chosen["phase"] = "pre_compile"
        chosen["event_ids"] = sorted({e for c in cands for e in c.get("event_ids") or [] if e is not None})
        for item in chosen["items"]:
            item["event_ids"] = by_ref[item["ref"]].get("event_ids") or []
            item["kind"] = by_ref[item["ref"]]["kind"]
        out.append(chosen)

    for q in und.get("questions") or []:
        if q["kind"] == "done_when":
            choices = [("success_check", und.get("success_check") or q["prompt"], "It checks for this at the end."),
                       ("skip", "Don’t check", "It finishes without checking.")]
            title = "How will you know it worked?"
        else:
            title, choices = _Q[q["kind"]]
        out.append(_question(
            q["kind"], f"{q['kind']}:{q['row']}", q["prompt"] or title, "", _choices(list(choices)),
            phase="pre_compile", best_guess=q["best_guess"], row_ref=q["row"],
            event_ids=[q["event_id"]] if q.get("event_id") is not None else [],
            target=rows.get(q["row"], {}).get("target") or ""))

    for n, q in enumerate(out):
        q["status"] = "open" if n < MAX_QUESTIONS else "queued"
    return out
