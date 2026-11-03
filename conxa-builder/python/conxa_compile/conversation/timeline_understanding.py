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
import sys
import threading
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from conxa_compile.compiler.selector_filters import scrub_email
from conxa_compile.conversation.clarify import _question

TASK = "timeline_understanding"
MAX_ROWS = 150
MAX_QUESTIONS = 5                 # shown at once; the rest are queued under "More questions"
MAX_CLICK_CANDIDATES = 6
MAX_TYPED_CANDIDATES = 6
MAX_URL_CANDIDATES = 8
MAX_DECIDED = 8
# A clicked target that was one of at least this many like items (a file in a list, a record in a
# table) is a value picked from a set, not a control. Two like buttons in a dialog footer are not.
IN_SET_MIN_PEERS = 3
_NAME_OK = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_VERDICTS = ("fixed", "varies", "unsure")
_FALLBACK_NAME = {"url": "page_address", "click": "item", "typed": "value"}

# What a question's answer can do. The ids are code's (an answer maps to an edit by id); the model
# writes the sentence and the labels. ``note`` changes nothing: its answer rides into the compile as text.
FALLBACK_CHOICES: dict[str, list[tuple[str, str, str]]] = {
    "value": [("varies", "Different every time", "Whoever uses it types or picks it."),
              ("fixed", "Always this one", "It stays exactly as you recorded it.")],
    "upload": [("each_run", "No, whoever uses it picks the file", "They are asked for the file before it starts."),
               ("fixed_path", "Yes, always use this file", "It uses the file you picked while recording.")],
    "confirm_before": [("confirm", "Yes, ask first", "A person approves this step each time before it happens."),
                       ("no_confirm", "No, just do it", "It runs on its own.")],
    "popup_dismiss": [("dismiss", "Yes, close it", "If it shows up, it is closed. If not, it carries on."),
                      ("keep", "No, leave it", "It does nothing special about it.")],
    "fork": [("by_input", "Let whoever uses it decide each time", "They get a question to answer before it starts."),
             ("ask_human", "Stop and ask a person each time", "It waits on the popup until someone decides."),
             ("stop", "Stop completely", "Treat this popup as a problem and end here.")],
}
_FALLBACK_PROMPT = {
    "upload": "Is the file the same every time?",
    "confirm_before": "Should it check with someone before this step?",
    "popup_dismiss": "A message box appeared here. Should it close it when it shows up?",
    "fork": "A message box asked for a decision. What should happen when it does?",
}
# effect -> best_guess it may carry. An effect with no entry is never pre-filled.
GUESSES: dict[str, set[str]] = {
    "value": {"varies", "fixed"},
    "upload": {"each_run", "fixed_path"},
    "confirm_before": {"confirm", "no_confirm"},
    "popup_dismiss": {"dismiss", "keep"},
}
EFFECTS = (*FALLBACK_CHOICES, "note")
# A guess that would approve a step that cannot be undone, or choose a popup option, is never pre-filled.
UNSAFE_GUESSES = {("confirm_before", "no_confirm")}
_UPLOAD_ACTIONS = ("upload", "upload_intent")
_GAP_CHOICES = (
    {"id": "c1", "label": "Yes, it’s fine as recorded", "detail": ""},
    {"id": "c2", "label": "No — use your own answer below to say what’s missing", "detail": ""},
)

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


def _caused_by_action(row: dict[str, Any]) -> bool:
    """A page load the recorder saw a click cause; the compile folds it away (unless every action is kept)."""
    from conxa_core.config import settings

    if settings.compile_keep_every_action:
        return False
    raw = row.get("value")
    data = _json_value(raw) if isinstance(raw, str) and raw.strip().startswith("{") else None
    return isinstance(data, dict) and data.get("cause") == "action"


def _short_label(cand: dict[str, Any]) -> str:
    """What a question names the value by: a page address by its last part (the file or record),
    anything else cut at a word, never mid-word."""
    text = str(cand.get("label") or cand["recorded"])
    if cand.get("kind") == "url":
        text = cand["literal"].split("?")[0].rstrip("/").rsplit("/", 1)[-1] or text
    if len(text) <= 60:
        return text
    return text[:60].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _events(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in merged if r.get("kind") == "event"]


def candidates_from_timeline(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recorded values that may be specific to this recording: page addresses, clicked items known
    only by their label, and text typed into a field. ``{ref, kind, event_ids, row_refs, recorded, literal}``."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    counts = {"url": 0, "click": 0, "typed": 0, "click_set": 0}
    caps = {"url": MAX_URL_CANDIDATES, "click": MAX_CLICK_CANDIDATES, "typed": MAX_TYPED_CANDIDATES,
            "click_set": MAX_CLICK_CANDIDATES}
    for row in _events(merged):
        action = row.get("action")
        if action in ("navigate", "manual_navigate"):
            if _caused_by_action(row):
                continue      # a click's page load is folded into that click: nothing to ask, the click is asked
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
        in_set = kind == "click" and int(row.get("peers") or 0) >= IN_SET_MIN_PEERS
        key = (kind, literal.lower())
        if key not in groups:
            # Picked-from-a-list clicks have their own allowance: a handful of earlier control clicks
            # must not crowd out the one click that chose the item.
            bucket = "click_set" if in_set else kind
            if counts[bucket] >= caps[bucket]:
                continue
            counts[bucket] += 1
            groups[key] = {"ref": f"{kind}:{row.get('event_id')}", "kind": kind, "event_ids": [], "row_refs": [],
                           "recorded": recorded[:200], "literal": literal[:200],
                           "label": str(row.get("target") or "")[:200], "in_set": False}
        if in_set:
            groups[key]["in_set"] = True
        groups[key]["event_ids"].append(row.get("event_id"))
        groups[key]["row_refs"].append(row["row_ref"])
    return list(groups.values())


def _seen(segment: dict[str, Any] | None) -> dict[str, Any] | None:
    if not segment:
        return None
    keep = ("visible_target", "intent", "purpose", "screen_before", "screen_after")
    return {k: segment[k] for k in keep if segment.get(k)}


def must_ask(merged: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Moments that always need a person, found in the browser's own signals: a popup asking for a
    decision, and a file going in. The model words these; it cannot drop them (validate puts them back).

    A file going in that the workflow downloaded itself earlier is not asked about: it is whatever
    that download produced, so there is nothing for the person to pick here. The question that
    matters (was the item that got downloaded a fixed one?) belongs to the click that chose it."""
    out = []
    downloaded: set[str] = set()
    for r in _events(merged):
        if r.get("action") == "download_observed":
            data = _json_value(r.get("value"))
            name = str(data.get("suggested_filename") or "").strip() if isinstance(data, dict) else ""
            if name:
                downloaded.add(name)
        elif r.get("dialog") == "decision":
            out.append({"row": r["row_ref"], "effect": "fork"})
        elif r.get("action") in _UPLOAD_ACTIONS:
            data = _json_value(r.get("value"))
            names = [str(i.get("name") or "").strip() for i in data if isinstance(i, dict)] if isinstance(data, list) else []
            if names and all(n in downloaded for n in names):
                continue
            out.append({"row": r["row_ref"], "effect": "upload"})
    return out


def _json_value(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return None


def build_input(merged: list[dict[str, Any]], task: str, chat: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for r in merged[:MAX_ROWS]:
        if r.get("kind") == "event":
            value = r.get("value")
            rows.append({
                "ref": r["row_ref"], "kind": "event", "action": r.get("action"),
                "target": scrub_email(r["target"]) if r.get("target") else r.get("target"),
                "role": r.get("role") or None,
                "value": "(hidden)" if value == "{{REDACTED}}" else (str(value)[:80] if value else None),
                "url": r.get("url"), "seen": _seen(r.get("segment")),
                **({"dialog": r["dialog"]} if r.get("dialog") else {}),
                **({"peers": r["peers"]} if r.get("peers") else {}),
            })
        else:
            rows.append({"ref": r["row_ref"], "kind": "observation", "type": r.get("type"),
                         "seconds": r.get("seconds"), "seen": _seen(r.get("segment"))})
    return {
        "task": (task or "").strip()[:300], "chat": (chat or "").strip()[:600], "rows": rows,
        "candidates": [{"ref": c["ref"], "kind": c["kind"], "recorded": c["recorded"], "rows": c["row_refs"],
                        **({"in_set": True} if c.get("in_set") else {})}
                       for c in candidates],
        "must_ask": must_ask(merged),
    }


def _text(value: Any, limit: int) -> str:
    """Model text a person will read: one line, no email address (an account label carries one)."""
    return scrub_email(str(value or ""))[:limit]


def _choices_for(effect: str, offered: Any) -> list[dict[str, Any]]:
    """Code's ids, the model's labels. A label that is missing or unusable falls back to the stock one."""
    if effect == "note":
        picked = [c for c in offered or [] if isinstance(c, dict) and _text(c.get("label"), 90)][:3]
        if len(picked) < 2:
            return []
        return [{"id": f"c{i}", "label": _text(c["label"], 90), "detail": ""} for i, c in enumerate(picked, 1)]
    given = {str(c.get("id")): c for c in offered or [] if isinstance(c, dict)}
    out = []
    for cid, label, detail in FALLBACK_CHOICES[effect]:
        mine = given.get(cid) or {}
        out.append({"id": cid, "label": _text(mine.get("label"), 90) or label, "detail": detail})
    return out


def _fallback_question(effect: str, row_ref: str, row: dict[str, Any]) -> dict[str, Any]:
    return {"kind": effect, "row": row_ref, "event_id": row.get("event_id"), "prompt": _FALLBACK_PROMPT[effect], "why": "",
            "choices": _choices_for(effect, None), "best_guess": "", "cost_rank": 1}


def _value_question(cand: dict[str, Any], row: dict[str, Any], guess: str) -> dict[str, Any]:
    label = _short_label(cand)
    return {"kind": "value", "row": row["row_ref"], "event_id": row.get("event_id"), "ref": cand["ref"],
            "prompt": f"Is “{label}” the same every time, or different each run?", "why": "",
            "choices": _choices_for("value", None), "best_guess": guess, "cost_rank": 3}


def _checked_questions(raw_questions: Any, rows: dict[str, Any], by_ref: dict[str, Any],
                       values: dict[str, dict[str, Any]], taken: set[str],
                       no_upload_question: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for q in raw_questions or []:
        if not isinstance(q, dict):
            continue
        effect = q.get("effect") or q.get("kind")
        row = rows.get(q.get("row"))
        prompt = _text(q.get("prompt"), 240)
        if effect not in EFFECTS or row is None or not prompt:
            continue
        if effect == "upload" and row["row_ref"] in no_upload_question:
            continue
        ref = ""
        if effect == "value":
            ref = str(q.get("ref") or "")
            cand = by_ref.get(ref)
            if cand is None or cand["kind"] == "typed":      # typed text is already an input once compiled
                continue
            if ref not in values or values[ref]["verdict"] == "fixed":
                values[ref] = {"verdict": "unsure", "literal": cand["literal"],
                               "name": _unique_name("", taken, _FALLBACK_NAME[cand["kind"]])}
        elif effect in ("fork", "popup_dismiss"):
            # The page decides which, not the model: a decision popup is a fork, anything else is not.
            if row.get("dialog") == "decision":
                effect = "fork"
            elif effect == "fork":
                continue
        choices = _choices_for(effect, q.get("choices"))
        if not choices:
            continue
        guess = str(q.get("best_guess") or "")
        if guess not in GUESSES.get(effect, set()) or (effect, guess) in UNSAFE_GUESSES:
            guess = ""
        rank = q.get("cost_rank")
        out.append({"kind": effect, "row": row["row_ref"], "event_id": row.get("event_id"), "prompt": prompt,
                    "why": _text(q.get("why"), 200), "choices": choices, "best_guess": guess,
                    "cost_rank": rank if isinstance(rank, int) and not isinstance(rank, bool) else 99,
                    **({"ref": ref} if ref else {})})
    return out


def _dedupe(questions: list[dict[str, Any]], rows: dict[str, Any]) -> list[dict[str, Any]]:
    """One question per recorded value and per (row, effect). A value question yields to a decision
    popup on its row; any other question on a row yields to that row's value question."""
    questions = sorted(questions, key=lambda q: q["cost_rank"])
    value_rows = {q["row"] for q in questions if q["kind"] == "value" and rows[q["row"]].get("dialog") != "decision"}
    seen: set[Any] = set()
    out = []
    for q in questions:
        if q["kind"] == "value" and rows[q["row"]].get("dialog") == "decision":
            continue
        if q["kind"] not in ("value", "fork") and q["row"] in value_rows:
            continue
        key = ("ref", q["ref"]) if q["kind"] == "value" else (q["row"], q["kind"])
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


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

    decided = [{"row": d["row"], "text": _text(d.get("text"), 160), "reason": _text(d.get("reason"), 160)}
               for d in raw.get("decided") or []
               if isinstance(d, dict) and d.get("row") in rows and _text(d.get("text"), 160)][:MAX_DECIDED]

    must = must_ask(merged)
    # An upload of a file the workflow downloaded itself has no question (see must_ask), whoever asks.
    no_upload = frozenset(r["row_ref"] for r in _events(merged) if r.get("action") in _UPLOAD_ACTIONS) \
        - {m["row"] for m in must if m["effect"] == "upload"}
    questions = _checked_questions(raw.get("questions"), rows, by_ref, values, taken, no_upload)
    for m in must:
        if not any(q["row"] == m["row"] and q["kind"] == m["effect"] for q in questions):
            questions.append(_fallback_question(m["effect"], m["row"], rows[m["row"]]))
    # Something the video showed that the browser did not record is asked, never only mentioned.
    asked_rows = {q["row"] for q in questions}
    for g in gaps:
        if g["row"] not in asked_rows:
            questions.append({"kind": "note", "row": g["row"], "event_id": rows[g["row"]].get("event_id"),
                              "prompt": f"{g['note']} Is it fine as recorded?", "why": "", "choices": list(_GAP_CHOICES),
                              "best_guess": "", "cost_rank": 4})
    have = {q.get("ref") for q in questions}
    for ref, v in values.items():
        cand = by_ref[ref]
        if v["verdict"] == "fixed" or cand["kind"] == "typed" or ref in have:
            continue
        questions.append(_value_question(cand, rows[cand["row_refs"][0]], "varies" if v["verdict"] == "varies" else ""))
    questions = _dedupe(questions, rows)
    questions.sort(key=lambda q: (q["cost_rank"], order[q["row"]]))

    return {
        "goal": _text(raw.get("goal"), 200), "phases": phases, "values": values,
        "success_check": _text(raw.get("success_check"), 200), "gaps": gaps,
        "short_summary": _text(raw.get("short_summary"), 300), "detailed_summary": detailed,
        "decided": decided, "questions": questions,
    }


def _fallback_detailed(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in merged:
        seg = r.get("segment") or {}
        text = _text(seg.get("intent") or seg.get("screen_after") or "", 240)
        if text:
            out.append({"row": r["row_ref"], "text": text})
    return out


# What the router said about the last default call, per thread (two workflows can be understood at once).
# The `call` seam stays (task, payload, timeout_ms) -> dict | None, so the detail rides here, not in its signature.
_router = threading.local()


def _default_call(task_name: str, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any] | None:
    from conxa_compile.llm.client import call_llm  # noqa: PLC0415 -- Studio-only, imported lazily

    _router.detail = []
    return call_llm(task_name, {"input": payload}, timeout_ms, error_detail=_router.detail)


_ANSWER_KEYS = ("goal", "short_summary", "values", "questions", "phases")


def _unwrapped(raw: Any) -> Any:
    """An answer nested one level down (``{"understanding": {...}}``) is the same answer. Only when the
    top level holds none of the expected keys and exactly one value does; nothing else is guessed."""
    if not isinstance(raw, dict) or any(k in raw for k in _ANSWER_KEYS):
        return raw
    inner = [v for v in raw.values() if isinstance(v, dict) and any(k in v for k in _ANSWER_KEYS)]
    return inner[0] if len(inner) == 1 else raw


def _describe_unusable(raw: Any, detail: list[str]) -> str:
    """Why an answer that arrived could not be used, for ``fallback_reason``: its top-level keys and the
    start of any text it carried (a reply that was cut off or not JSON comes back as ``text``), plus what
    the router said. Never the prompt, and never more than a few hundred characters."""
    if not isinstance(raw, dict):
        return "the answer had no goal, summary or values after checking"
    text = raw.get("text") or raw.get("output")
    parts = [f"answer keys: {sorted(str(k) for k in raw)[:8]}"]
    if isinstance(text, str) and text.strip():
        parts.append(f"text ({len(text)} chars) starts: {' '.join(text.split())[:300]!r}")
    if detail:
        parts.append(f"router: {'; '.join(str(d) for d in detail)[:200]}")
    return "the answer had no goal, summary or values after checking; " + "; ".join(parts)


def _ask_about_unmentioned_selections(result: dict[str, Any], candidates: list[dict[str, Any]],
                                      merged: list[dict[str, Any]], existing_inputs: set[str] | None) -> None:
    """An item picked out of a list that the model said nothing about is asked, not assumed fixed.

    "Fixed" must be a decision somebody made: the model (it may well say the task names this exact
    item) or the person. A silent default is wrong in the common case, because choosing from a list
    is nearly always choosing *this time's* item. Holds whether the model answered or the call
    failed, since the structural signal needs neither."""
    taken = {n.lower() for n in existing_inputs or set()} | {v["name"].lower() for v in result["values"].values() if v.get("name")}
    rows = {r["row_ref"]: r for r in merged}
    order = {ref: i for i, ref in enumerate(rows)}
    added = False
    for c in candidates:
        if not c.get("in_set") or c["ref"] in result["values"]:
            continue
        result["values"][c["ref"]] = {"verdict": "unsure", "literal": c["literal"],
                                      "name": _unique_name("", taken, _FALLBACK_NAME[c["kind"]])}
        result["questions"].append(_value_question(c, rows[c["row_refs"][0]], ""))      # asked, nothing pre-ticked
        added = True
    if added:
        result["questions"].sort(key=lambda q: (q["cost_rank"], order[q["row"]]))


def understand_timeline(
    merged: list[dict[str, Any]],
    task: str = "",
    chat: str = "",
    *,
    existing_inputs: set[str] | None = None,
    call: Caller | None = None,
    timeout_ms: int = 120_000,
) -> dict[str, Any]:
    """The checked understanding plus ``candidates`` (each with verdict/name/literal) and
    ``source``: ``model`` (usable answer), ``fallback`` (call failed or empty), ``none`` (nothing to
    look at). Never raises."""
    candidates = candidates_from_timeline(merged)
    result: dict[str, Any] = {"source": "none", "candidates": candidates, "goal": "", "phases": [], "values": {},
                              "success_check": "", "gaps": [], "short_summary": "", "detailed_summary": [], "decided": [], "questions": []}
    if not _events(merged):
        return result
    reason = ""
    _router.detail = []
    try:
        raw = (call or _default_call)(TASK, build_input(merged, task, chat, candidates), timeout_ms)
        detail = list(getattr(_router, "detail", None) or [])
        raw = _unwrapped(raw)
        if raw is None:
            reason = "the model returned no answer" + (f" ({'; '.join(map(str, detail))[:200]})" if detail else "")
    except Exception as exc:  # noqa: BLE001 -- advisory: a failed model call must never block the flow
        raw, detail = None, []
        reason = f"{type(exc).__name__}: {exc}"[:300]
    checked = validate(raw, merged, candidates, existing_inputs)
    usable = bool(checked["goal"] or checked["short_summary"] or checked["values"])
    result.update(checked)
    result["source"] = "model" if usable else "fallback"
    if not usable:
        # Kept in understanding.json: a silent fallback cannot be diagnosed afterwards.
        result["fallback_reason"] = reason or _describe_unusable(raw, detail)
        print(f"[timeline_understanding] fell back: {result['fallback_reason']}", file=sys.stderr)
        taken = {n.lower() for n in existing_inputs or set()}
        if task.strip():
            for c in candidates:
                if c["kind"] == "url":
                    result["values"][c["ref"]] = {"verdict": "unsure", "literal": c["literal"],
                                                  "name": _unique_name("", taken, _FALLBACK_NAME["url"])}
                    row = next(r for r in merged if r["row_ref"] == c["row_refs"][0])
                    result["questions"].append(_value_question(c, row, ""))      # asked, nothing pre-ticked
        result["short_summary"] = f"{len(_events(merged))} recorded actions."
    _ask_about_unmentioned_selections(result, candidates, merged, existing_inputs)
    if not result["detailed_summary"]:
        result["detailed_summary"] = _fallback_detailed(merged)
    for c in candidates:
        c.update(result["values"].get(c["ref"]) or {"verdict": "fixed", "name": "", "literal": c["literal"]})
    return result


# --- questions ------------------------------------------------------------------------------

def questions_from_understanding(und: dict[str, Any], merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clarify-shaped questions (``phase: "pre_compile"``), most important first, exactly the ones
    ``validate`` kept: nothing is added here. The first MAX_QUESTIONS are ``open``; the rest
    ``queued``. A card pre-selects ``best_guess`` but it only counts when the person presses Confirm.
    Answers become decisions (decisions.py), not edits."""
    rows = {r["row_ref"]: r for r in merged}
    cands = {c["ref"]: c for c in und.get("candidates") or []}
    out: list[dict[str, Any]] = []
    for q in und.get("questions") or []:
        extra: dict[str, Any] = {}
        qid = f"{q['kind']}:{q['row']}"
        if q["kind"] == "value":
            c = cands.get(q["ref"])
            if c is None or not c.get("name"):
                continue
            qid = f"value:{q['ref']}"
            extra["items"] = [{"ref": c["ref"], "name": c["name"], "literal": c["literal"], "recorded": c["recorded"],
                               "kind": c["kind"], "event_ids": c.get("event_ids") or []}]
        out.append(_question(
            q["kind"], qid, q["prompt"], q.get("why") or "", [dict(c) for c in q["choices"]],
            phase="pre_compile", best_guess=q["best_guess"], row_ref=q["row"],
            event_ids=[q["event_id"]] if q.get("event_id") is not None else [],
            target=rows.get(q["row"], {}).get("target") or "", **extra))
    for n, q in enumerate(out):
        q["status"] = "open" if n < MAX_QUESTIONS else "queued"
    return out
