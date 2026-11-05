"""Understand a recording before it is compiled (Q1 of the understanding-first pipeline).

One text call reads the merged timeline (browser events lined up with what Pegasus saw) and
writes the understanding: a goal, a line per step, which recorded values change each run, the points it
settled itself, the questions worth asking, gaps and a short summary. Code does the rest:

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

from conxa_compile.compiler.destructive_semantics import label_has_destructive_word
from conxa_compile.compiler.selector_filters import scrub_email
from conxa_compile.conversation.clarify import _question

TASK = "timeline_understanding"
MAX_ROWS = 250
MAX_QUESTIONS = 5                 # shown at once; the rest are queued under "More questions"
MAX_CLICK_CANDIDATES = 6
MAX_TYPED_CANDIDATES = 6
MAX_URL_CANDIDATES = 8
MAX_DECIDED = 8
# An answer is graded ``ok`` / ``weak`` / ``unusable`` by code. ``weak`` is asked for once more (the one retry).
WEAK_REF_DROPS = 3                # this many invented refs or rows in one answer: the model was not looking at the rows
WEAK_UNJUDGED_SHARE = 0.5         # more than this share of the candidates left without a verdict
WEAK_MIN_CANDIDATES = 2           # fewer candidates than this: coverage says nothing
# A clicked target that was one of at least this many like items (a file in a list, a record in a
# table) is a value picked from a set, not a control. Two like buttons in a dialog footer are not.
IN_SET_MIN_PEERS = 3
_NAME_OK = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_VERDICTS = ("fixed", "varies", "unsure")
_FALLBACK_NAME = {"url": "page_address", "click": "item", "typed": "value"}

# One row per question effect: what a question's answer can do. The ids are code's (an answer maps to an
# edit by id); the model writes the sentence and the labels. ``note`` changes nothing: its answer rides
# into the compile as text, with 2-3 choices the model labels (``choices`` is None).
#   choices       (id, stock label, detail): the ids the model may use, and the words used if its label is unusable
#   guesses       the best_guess ids it may carry; none = never pre-filled (a popup is never answered for anyone)
#   unsafe        a guess that would approve a step that cannot be undone: never pre-filled
#   page_decides  the recorded page, not the model, says whether this is a decision popup (a fork) or a plain one
RULES: dict[str, dict[str, Any]] = {
    "value": {
        "choices": [("varies", "Different every time", "Whoever uses it types or picks it."),
                    ("fixed", "Always this one", "It stays exactly as you recorded it.")],
        "guesses": {"varies", "fixed"}, "unsafe": set(), "page_decides": False},
    "upload": {
        "choices": [("each_run", "No, whoever uses it picks the file", "They are asked for the file before it starts."),
                    ("fixed_path", "Yes, always use this file", "It uses the file you picked while recording.")],
        "guesses": {"each_run", "fixed_path"}, "unsafe": set(), "page_decides": False},
    "confirm_before": {
        "choices": [("confirm", "Yes, ask first", "A person approves this step each time before it happens."),
                    ("no_confirm", "No, just do it", "It runs on its own.")],
        "guesses": {"confirm", "no_confirm"}, "unsafe": {"no_confirm"}, "page_decides": False},
    "popup_dismiss": {
        "choices": [("dismiss", "Yes, close it", "If it shows up, it is closed. If not, it carries on."),
                    ("keep", "No, leave it", "It does nothing special about it.")],
        "guesses": {"dismiss", "keep"}, "unsafe": set(), "page_decides": True},
    "fork": {
        "choices": [("by_input", "Let whoever uses it decide each time", "They get a question to answer before it starts."),
                    ("ask_human", "Stop and ask a person each time", "It waits on the popup until someone decides."),
                    ("stop", "Stop completely", "Treat this popup as a problem and end here.")],
        "guesses": set(), "unsafe": set(), "page_decides": True},
    "note": {"choices": None, "guesses": set(), "unsafe": set(), "page_decides": False},
}
_FALLBACK_PROMPT = {
    "upload": "Is the file the same every time?",
    "confirm_before": "Should it check with someone before this step?",
    "popup_dismiss": "A message box appeared here. Should it close it when it shows up?",
    "fork": "A message box asked for a decision. What should happen when it does?",
}
_UPLOAD_ACTIONS = ("upload", "upload_intent")
# What the video model says when a file is picked or dragged in from outside the browser.
_FILE_PICK_RE = re.compile(
    r"\b(file (explorer|picker|dialog|chooser|browser|manager)|windows explorer|finder window|"
    r"open(ing)? (a |the )?(file )?dialog|(choos|select|pick|brows)(e|es|ed|ing|s)? (for )?(a |the )?files?|"
    r"(drag|drop)(s|ped|ging|ged)? (a |the )?files?)\b")
# An upload the browser did record this close to the picker means the pick was captured.
FILE_PICK_WINDOW_MS = 20_000
_FILE_PICK_CHOICES = (
    {"id": "c1", "label": "I’ll record this part again", "detail": ""},
    {"id": "c2", "label": "No file was needed here", "detail": ""},
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

    if not settings.compile_pass("fold_navigations"):
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


def _seen(segment: dict[str, Any] | None, prev_after: str = "") -> dict[str, Any] | None:
    """What the video model saw, in fewer words: ``purpose`` is folded into ``intent``, and
    ``screen_before`` is left out while the row before already said how its screen ended."""
    if not segment:
        return None
    out = {k: segment[k] for k in ("visible_target", "screen_after") if segment.get(k)}
    intent, purpose = str(segment.get("intent") or "").strip(), str(segment.get("purpose") or "").strip()
    if purpose and purpose.lower() not in intent.lower():
        intent = f"{intent} — {purpose}" if intent else purpose
    if intent:
        out["intent"] = intent
    if segment.get("screen_before") and not prev_after:
        out["screen_before"] = segment["screen_before"]
    return out or None


def _opened_it(row: dict[str, Any] | None) -> bool:
    """A click outside any popup whose own label names no destructive word. One that does is already
    asked about at compile time (clarify.py, "can't be undone")."""
    return bool(row) and row.get("action") in ("click", "dblclick", "right_click") and not row.get("dialog")         and not label_has_destructive_word(str(row.get("target") or ""))


def must_ask(merged: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Moments that always need a person, found in the browser's own signals: a popup asking for a
    decision, and a file going in. The model words these; it cannot drop them (validate puts them back).

    A file going in that the workflow downloaded itself earlier is not asked about: it is whatever
    that download produced, so there is nothing for the person to pick here. The question that
    matters (was the item that got downloaded a fixed one?) belongs to the click that chose it."""
    out = []
    downloaded: set[str] = set()
    prev: dict[str, Any] | None = None
    for r in _events(merged):
        if r.get("dialog") == "decision" and _opened_it(prev):
            # The page asked "are you sure?" right after this click. No word list: the dialog is the evidence.
            out.append({"row": prev["row_ref"], "effect": "confirm_before"})
        prev = r
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


def missed_file_picks(merged: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """``(row_ref, prompt)`` for each moment the video shows a file picked or dragged in from outside
    the browser with no upload the browser recorded near it. The replay cannot repeat such a pick.
    Only video-only rows count: a row matched to a browser event was captured."""
    uploads = [r.get("t_ms") or 0 for r in _events(merged) if r.get("action") in _UPLOAD_ACTIONS]
    out = []
    for r in merged:
        seg = r.get("segment") or {}
        if r.get("kind") != "observation" or not seg:
            continue
        said = " ".join(str(seg.get(k) or "") for k in ("visible_target", "intent", "purpose", "screen_before", "screen_after"))
        if not _FILE_PICK_RE.search(said.lower()):
            continue
        t = r.get("t_ms") or 0
        if any(abs(u - t) <= FILE_PICK_WINDOW_MS for u in uploads):
            continue
        what = _text(seg.get("intent") or seg.get("visible_target"), 90).strip().rstrip(".")
        out.append((r["row_ref"], "Here a file was picked in a window outside the browser"
                    + (f" ({what})" if what else "")
                    + ", and the recording couldn’t see which file. The workflow can’t repeat this step."))
    return out


def _json_value(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return None


def told_from_hints(candidates: list[dict[str, Any]], varies: dict[str, Any] | None) -> dict[str, bool]:
    """``{candidate ref: True (changes each run) | False (stays as recorded)}`` for typed values the
    person already answered while recording (``hints.json``, keyed by the event's sequence = ``event_id``)."""
    told: dict[str, bool] = {}
    for c in candidates:
        if c["kind"] != "typed":
            continue
        for eid in c["event_ids"]:
            answer = (varies or {}).get(str(eid))
            if isinstance(answer, bool):
                told[c["ref"]] = answer
                break
    return told


def build_input(merged: list[dict[str, Any]], task: str, chat: str, candidates: list[dict[str, Any]], *,
                told: dict[str, bool] | None = None, existing_inputs: set[str] | None = None) -> dict[str, Any]:
    # Compact on purpose (the prompt in conxa_core/llm/client.py says how to read it): no empty keys,
    # no `kind` on an event, `url` only when it changes, and `seen` without what the row before said.
    rows: list[dict[str, Any]] = []
    prev_url, prev_after = None, ""
    for r in merged[:MAX_ROWS]:
        seen = _seen(r.get("segment"), prev_after)
        if r.get("kind") == "event":
            value = r.get("value")
            row = {
                "ref": r["row_ref"], "action": r.get("action"),
                "target": scrub_email(r["target"]) if r.get("target") else None,
                "role": r.get("role") or None,
                "value": "(hidden)" if value == "{{REDACTED}}" else (str(value)[:80] if value else None),
                "url": r.get("url") if r.get("url") != prev_url else None,
                "seen": seen, "dialog": r.get("dialog") or None, "peers": r.get("peers") or None,
            }
            prev_url = r.get("url") or prev_url
        else:
            row = {"ref": r["row_ref"], "kind": "observation", "type": r.get("type"),
                   "seconds": r.get("seconds"), "seen": seen}
        rows.append({k: v for k, v in row.items() if v not in (None, "")})
        prev_after = (r.get("segment") or {}).get("screen_after") or ""
    return {
        "task": (task or "").strip()[:300], "chat": (chat or "").strip()[:600], "rows": rows,
        "candidates": [{"ref": c["ref"], "kind": c["kind"], "recorded": c["recorded"], "rows": c["row_refs"],
                        **({"in_set": True} if c.get("in_set") else {})}
                       for c in candidates],
        "must_ask": must_ask(merged),
        **({"told": {ref: "varies" if v else "fixed" for ref, v in told.items()}} if told else {}),
        **({"existing_inputs": sorted(existing_inputs)} if existing_inputs else {}),
    }


def _text(value: Any, limit: int) -> str:
    """Model text a person will read: one line, no email address (an account label carries one)."""
    return scrub_email(str(value or ""))[:limit]


# The five things the check does to a model answer, in the order it does them. ``validation_report`` in
# understanding.json counts every use of a rule, so a bad prompt shows up as a number instead of a silence.
STAGES = ("shape", "reference", "override", "backfill", "safety")


def _log(report: dict[str, Any], stage: str, rule: str, example: Any = "") -> None:
    """Count one use of ``rule`` under ``stage``; the first example is kept. ``report`` is plain JSON."""
    slot = report.setdefault(stage, {}).setdefault(rule, {"count": 0, "example": _text(example, 60)})
    slot["count"] += 1


def _count(report: dict[str, Any], stage: str, *rules: str) -> int:
    got = report.get(stage) or {}
    return sum(got[r]["count"] for r in (rules or tuple(got)) if r in got)


def _fork_choices(row: dict[str, Any], offered: Any) -> list[dict[str, Any]]:
    """The choices for a popup that asks for a decision: one per button the popup showed (``btn:<label>``,
    fixed by code from the recording; the model never writes them), then the three generic ones. Picking a
    button is the author's approval to press it every time, exactly as it is after the compile; a button
    that changes or deletes things says so. Never pre-filled (``GUESSES`` has no fork entry)."""
    from conxa_compile.compiler.outcome_fork import option_role

    buttons = [str(b)[:60] for b in row.get("dialog_buttons") or [] if str(b).strip()]
    per_button = []
    for label in buttons:
        careful = option_role(label) == "consequential"
        per_button.append({
            "id": f"btn:{label}", "label": f"Always click \u201c{label}\u201d",
            "detail": ("Careful: this can change or delete things. If you pick it, it will happen on its own, without asking anyone."
                       if careful else "It presses this button on its own whenever the popup shows up."),
        })
    return per_button + _choices_for("fork", offered)


def _choices_for(effect: str, offered: Any) -> list[dict[str, Any]]:
    """Code's ids, the model's labels. A label that is missing or unusable falls back to the stock one."""
    if effect == "note":
        picked = [c for c in offered or [] if isinstance(c, dict) and _text(c.get("label"), 90)][:3]
        if len(picked) < 2:
            return []
        return [{"id": f"c{i}", "label": _text(c["label"], 90), "detail": ""} for i, c in enumerate(picked, 1)]
    given = {str(c.get("id")): c for c in offered or [] if isinstance(c, dict)}
    out = []
    for cid, label, detail in RULES[effect]["choices"]:
        mine = given.get(cid) or {}
        out.append({"id": cid, "label": _text(mine.get("label"), 90) or label, "detail": detail})
    return out


def _fallback_question(effect: str, row_ref: str, row: dict[str, Any]) -> dict[str, Any]:
    return {"kind": effect, "row": row_ref, "event_id": row.get("event_id"), "prompt": _FALLBACK_PROMPT[effect], "why": "",
            "choices": _fork_choices(row, None) if effect == "fork" else _choices_for(effect, None), "best_guess": "", "cost_rank": 1}


def _value_question(cand: dict[str, Any], row: dict[str, Any], guess: str) -> dict[str, Any]:
    label = _short_label(cand)
    return {"kind": "value", "row": row["row_ref"], "event_id": row.get("event_id"), "ref": cand["ref"],
            "prompt": f"Is “{label}” the same every time, or different each run?", "why": "",
            "choices": _choices_for("value", None), "best_guess": guess, "cost_rank": 3}


def _checked_questions(raw_questions: Any, rows: dict[str, Any], by_ref: dict[str, Any],
                       values: dict[str, dict[str, Any]], taken: set[str],
                       no_upload_question: frozenset[str] = frozenset(),
                       report: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    report = {} if report is None else report
    out: list[dict[str, Any]] = []
    for q in raw_questions or []:
        if not isinstance(q, dict):
            _log(report, "shape", "malformed_item", "question")
            continue
        effect = q.get("effect") or q.get("kind")
        row_ref = q.get("row")
        row = rows.get(row_ref) if isinstance(row_ref, str) else None
        prompt = _text(q.get("prompt"), 240)
        if not isinstance(effect, str) or effect not in RULES:
            _log(report, "shape", "unknown_effect", effect)
            continue
        if row is None:
            _log(report, "reference", "unknown_row", row_ref)
            continue
        if not prompt:
            _log(report, "shape", "empty_text", row_ref)
            continue
        if effect == "upload" and row["row_ref"] in no_upload_question:
            _log(report, "override", "upload_on_own_download", row_ref)
            continue
        ref = ""
        if effect == "value":
            ref = str(q.get("ref") or "")
            cand = by_ref.get(ref)
            if cand is None:
                _log(report, "reference", "unknown_ref", ref)
                continue
            if cand["kind"] == "typed":      # typed text is already an input once compiled
                _log(report, "override", "typed_value_question", ref)
                continue
            if ref not in values or values[ref]["verdict"] == "fixed":
                if ref in values:
                    _log(report, "override", "fixed_but_asked", ref)      # the model asked about what it called fixed
                values[ref] = {"verdict": "unsure", "literal": cand["literal"],
                               "name": _unique_name("", taken, _FALLBACK_NAME[cand["kind"]])}
        elif RULES[effect]["page_decides"]:
            # The page decides which, not the model: a decision popup is a fork, anything else is not.
            if row.get("dialog") == "decision":
                if effect != "fork":
                    _log(report, "override", "popup_became_fork", row_ref)
                effect = "fork"
            elif effect == "fork":
                _log(report, "override", "fork_without_decision_popup", row_ref)
                continue
        choices = _fork_choices(row, q.get("choices")) if effect == "fork" else _choices_for(effect, q.get("choices"))
        if not choices:
            _log(report, "shape", "no_choices", row_ref)
            continue
        guess = str(q.get("best_guess") or "")
        if guess and guess not in RULES[effect]["guesses"]:
            _log(report, "safety", "invalid_guess", f"{effect}:{guess}")
            guess = ""
        elif guess in RULES[effect]["unsafe"]:
            _log(report, "safety", "unsafe_guess", f"{effect}:{guess}")
            guess = ""
        elif effect == "value" and guess and values[ref]["verdict"] == "varies" and guess != "varies":
            _log(report, "override", "guess_contradicts_verdict", ref)      # the model's own two answers disagree
            guess = "varies"
        rank = q.get("cost_rank")
        out.append({"kind": effect, "row": row["row_ref"], "event_id": row.get("event_id"), "prompt": prompt,
                    "why": _text(q.get("why"), 200), "choices": choices, "best_guess": guess,
                    "cost_rank": rank if isinstance(rank, int) and not isinstance(rank, bool) else 99,
                    **({"ref": ref} if ref else {})})
    return out


def _dedupe(questions: list[dict[str, Any]], rows: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    """One question per recorded value and per (row, effect). A value question yields to a decision
    popup on its row; any other question on a row yields to that row's value question."""
    questions = sorted(questions, key=lambda q: q["cost_rank"])
    value_rows = {q["row"] for q in questions if q["kind"] == "value" and rows[q["row"]].get("dialog") != "decision"}
    seen: set[Any] = set()
    out = []
    for q in questions:
        key = ("ref", q["ref"]) if q["kind"] == "value" else (q["row"], q["kind"])
        if (q["kind"] == "value" and rows[q["row"]].get("dialog") == "decision") \
                or (q["kind"] not in ("value", "fork") and q["row"] in value_rows) or key in seen:
            _log(report, "override", "duplicate_question", f"{q['kind']}@{q['row']}")
            continue
        seen.add(key)
        out.append(q)
    return out


def _row_items(items: Any, rows: dict[str, Any], field: str, limit: int, report: dict[str, Any],
               reason_limit: int = 0) -> list[dict[str, Any]]:
    """``[{"row", field}]`` (plus ``reason`` when asked for) from a model list: unknown rows and empty text
    are dropped and counted. A point the model settled itself is shown as "settled, because ...", so
    one with no reason is dropped too."""
    out = []
    for d in items if isinstance(items, list) else []:
        if not isinstance(d, dict):
            _log(report, "shape", "malformed_item", field)
            continue
        row = d.get("row")
        if not isinstance(row, str) or row not in rows:
            _log(report, "reference", "unknown_row", row)
            continue
        text = _text(d.get(field), limit)
        if not text:
            _log(report, "shape", "empty_text", row)
            continue
        item = {"row": row, field: text}
        if reason_limit:
            item["reason"] = _text(d.get("reason"), reason_limit)
            if not item["reason"]:
                _log(report, "shape", "no_reason", row)
                continue
        out.append(item)
    return out


def validate(raw: Any, merged: list[dict[str, Any]], candidates: list[dict[str, Any]],
             existing_inputs: set[str] | None = None, report: dict[str, Any] | None = None,
             told: Any = ()) -> dict[str, Any]:
    """The model's answer held to what code can act on, in five stages (see ``STAGES``): shape (types, lengths,
    enums), reference (a ref or row that exists), override (a fact the recording knows beats the model's
    word), backfill (a point that always needs a person, put back) and safety (no guess that approves
    something that cannot be undone). Everything dropped, changed or added is counted in ``validation_report``.
    ``told``: candidate refs the person already answered; the model owes no verdict for them."""
    report = {} if report is None else report
    raw = raw if isinstance(raw, dict) else {}
    rows = {r["row_ref"]: r for r in merged}
    order = {ref: i for i, ref in enumerate(rows)}
    by_ref = {c["ref"]: c for c in candidates}
    taken = {n.lower() for n in existing_inputs or set()}

    values: dict[str, dict[str, Any]] = {}
    for item in raw.get("values") or []:
        if not isinstance(item, dict):
            _log(report, "shape", "malformed_item", "value")
            continue
        ref = item.get("ref")
        if not isinstance(ref, str) or ref not in by_ref:
            _log(report, "reference", "unknown_ref", ref)
            continue
        if ref in values:
            _log(report, "reference", "duplicate_ref", ref)
            continue
        verdict = str(item.get("verdict") or "").lower()
        if verdict not in _VERDICTS:
            _log(report, "shape", "bad_verdict", ref)
            continue
        cand = by_ref[ref]
        literal = cand["literal"]
        proposed = _text(item.get("literal"), 200)
        if cand["kind"] == "url" and proposed:
            if proposed.lower() in cand["recorded"].lower():
                literal = proposed
            else:
                _log(report, "reference", "literal_outside_recorded", ref)
        name = ""
        if verdict != "fixed":
            name = _unique_name(_snake(str(item.get("name") or "")), taken, _FALLBACK_NAME[cand["kind"]])
        values[ref] = {"verdict": verdict, "name": name, "literal": literal}
    if any(k in raw for k in _ANSWER_KEYS):       # a candidate the model gave no verdict for is treated as fixed: counted, because that is a silent default
        for c in candidates:
            if c["ref"] not in values and c["ref"] not in told:
                _log(report, "shape", "no_verdict", c["ref"])

    gaps = _row_items(raw.get("gaps"), rows, "note", 200, report)
    detailed = _row_items(raw.get("detailed_summary"), rows, "text", 240, report)
    decided = _row_items(raw.get("decided"), rows, "text", 160, report, reason_limit=160)
    for d in decided[MAX_DECIDED:]:
        _log(report, "shape", "decided_over_cap", d["row"])
    decided = decided[:MAX_DECIDED]

    must = must_ask(merged)
    # An upload of a file the workflow downloaded itself has no question (see must_ask), whoever asks.
    no_upload = frozenset(r["row_ref"] for r in _events(merged) if r.get("action") in _UPLOAD_ACTIONS) \
        - {m["row"] for m in must if m["effect"] == "upload"}
    questions = _checked_questions(raw.get("questions"), rows, by_ref, values, taken, no_upload, report)
    # The prompt tells the model never to ask about a gap; code enforces it. A free-form note on a
    # row the model itself called a gap is that same question. Only notes: a value, fork or upload
    # question on such a row is about the action, not about the gap.
    gap_rows = {g["row"] for g in gaps}
    for q in questions:
        if q["kind"] == "note" and q["row"] in gap_rows:
            _log(report, "override", "note_on_gap", q["row"])
    questions = [q for q in questions if not (q["kind"] == "note" and q["row"] in gap_rows)]
    for m in must:
        if not any(q["row"] == m["row"] and q["kind"] == m["effect"] for q in questions):
            _log(report, "backfill", "must_ask", f"{m['effect']}@{m['row']}")
            questions.append(_fallback_question(m["effect"], m["row"], rows[m["row"]]))
    # Gaps (idle time, video and browser log out of step) are kept for the log only, never asked:
    # the recording is replayed as captured, and a person cannot answer a timing note.
    # The one gap that does break a replay is a file picked outside the browser: that is asked.
    for row_ref, prompt in missed_file_picks(merged):
        _log(report, "backfill", "missed_file_pick", row_ref)
        questions.append({"kind": "note", "row": row_ref, "event_id": None, "prompt": prompt, "why": "",
                          "choices": list(_FILE_PICK_CHOICES), "best_guess": "", "cost_rank": 1})
    have = {q.get("ref") for q in questions}
    for ref, v in values.items():
        cand = by_ref[ref]
        if v["verdict"] == "fixed" or cand["kind"] == "typed" or ref in have:
            continue
        _log(report, "backfill", "value_question_added", ref)
        questions.append(_value_question(cand, rows[cand["row_refs"][0]], "varies" if v["verdict"] == "varies" else ""))
    questions = _dedupe(questions, rows, report)
    questions.sort(key=lambda q: (q["cost_rank"], order[q["row"]]))

    return {
        "goal": _text(raw.get("goal"), 200), "values": values, "gaps": gaps,
        "short_summary": _text(raw.get("short_summary"), 300), "detailed_summary": detailed,
        "decided": decided, "questions": questions, "validation_report": report,
    }


# What the router said about the last default call, per thread (two workflows can be understood at once).
# The `call` seam stays (task, payload, timeout_ms) -> dict | None, so the detail rides here, not in its signature.
_router = threading.local()


def _default_call(task_name: str, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any] | None:
    from conxa_compile.llm.client import call_llm  # noqa: PLC0415 -- Studio-only, imported lazily

    _router.detail = []
    return call_llm(task_name, {"input": payload}, timeout_ms, error_detail=_router.detail)


_ANSWER_KEYS = ("goal", "short_summary", "values", "questions")


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
        _log(result["validation_report"], "backfill", "unmentioned_selection", c["ref"])
        added = True
    if added:
        result["questions"].sort(key=lambda q: (q["cost_rank"], order[q["row"]]))


_RETRY_NOTE = ("Your last reply could not be read. Return ONLY the JSON object described, nothing before "
               "or after it, and keep `decided` and `questions` short.")
_RETRY_WEAK = ("Your last reply could be read but was not good enough: {reasons}. Return the whole JSON object again "
               "and fix exactly that. Use only refs and rows that are in the input.")
_GRADE_RANK = {"unusable": 0, "weak": 1, "ok": 2}


def _grade(out: dict[str, Any], candidates: list[dict[str, Any]], told: Any) -> tuple[str, list[str]]:
    """``(grade, reasons)`` for a checked answer, from code alone. ``unusable``: nothing to build on.
    ``weak``: readable and safe, but it did not do the job (see the ``WEAK_*`` constants); one retry
    with the reasons. Otherwise ``ok``. Run before the person's recording-time answers are laid over it."""
    if not (out["goal"] or out["short_summary"] or out["values"]):
        return "unusable", ["no goal, summary or values after checking"]
    report, reasons = out["validation_report"], []
    if not out["goal"]:
        reasons.append("no goal")
    owed = sum(1 for c in candidates if c["ref"] not in told)
    missing = _count(report, "shape", "no_verdict")
    if owed >= WEAK_MIN_CANDIDATES and missing > WEAK_UNJUDGED_SHARE * owed:
        reasons.append(f"no verdict for {missing} of the {owed} candidates")
    invented = _count(report, "reference", "unknown_ref", "unknown_row", "duplicate_ref")
    if invented >= WEAK_REF_DROPS:
        reasons.append(f"{invented} refs or rows that are not in the recording")
    if _count(report, "shape", "no_reason"):
        reasons.append("points in `decided` with no reason")
    return ("weak" if reasons else "ok"), reasons


def _attempt(caller: Caller, payload: dict[str, Any], timeout_ms: int) -> tuple[Any, list[Any], str]:
    """One model call: ``(answer or None, what the router said, why there is no answer)``. Never raises."""
    _router.detail = []
    try:
        raw = _unwrapped(caller(TASK, payload, timeout_ms))
        detail = list(getattr(_router, "detail", None) or [])
        reason = ("the model returned no answer" + (f" ({'; '.join(map(str, detail))[:200]})" if detail else ""))             if raw is None else ""
        return raw, detail, reason
    except Exception as exc:  # noqa: BLE001 -- advisory: a failed model call must never block the flow
        return None, [], f"{type(exc).__name__}: {exc}"[:300]


def _checked(raw: Any, merged: list[dict[str, Any]], candidates: list[dict[str, Any]],
             existing_inputs: set[str] | None, told: dict[str, bool]) -> tuple[dict[str, Any], str, list[str]]:
    """``(checked answer, grade, why)``: see ``_grade``. The person's recording-time answers win over the
    model's verdict and nothing is asked about a value they settled; they never make an empty reply
    look usable."""
    out = validate(raw, merged, candidates, existing_inputs, told=told)
    grade, why = _grade(out, candidates, told)
    by_ref = {c["ref"]: c for c in candidates}
    taken = {n.lower() for n in existing_inputs or set()} | {v["name"].lower() for v in out["values"].values() if v.get("name")}
    for ref, varies in told.items():
        cand, v = by_ref[ref], out["values"].get(ref)
        if v and v["verdict"] != ("varies" if varies else "fixed"):
            _log(out["validation_report"], "override", "person_hint", ref)      # the person's word beat the model's
        if varies:
            name = (v or {}).get("name") or _unique_name("", taken, _FALLBACK_NAME[cand["kind"]])
            taken.add(name.lower())
            out["values"][ref] = {"verdict": "varies", "name": name, "literal": cand["literal"]}
        else:
            out["values"][ref] = {"verdict": "fixed", "name": "", "literal": cand["literal"]}
        out["questions"] = [q for q in out["questions"] if q.get("ref") != ref]
    return out, grade, why


def understand_timeline(
    merged: list[dict[str, Any]],
    task: str = "",
    chat: str = "",
    *,
    existing_inputs: set[str] | None = None,
    hints: dict[str, Any] | None = None,
    call: Caller | None = None,
    timeout_ms: int = 120_000,
) -> dict[str, Any]:
    """The checked understanding plus ``candidates`` (each with verdict/name/literal) and
    ``source``: ``model`` (usable answer), ``fallback`` (call failed or empty), ``none`` (nothing to
    look at). ``hints`` is the recorder's ``varies`` map (what the person said while recording). A reply
    that arrives but is not ``ok`` (see ``_grade``) is asked for once more, and the better of the two is
    kept. Also saved: ``grade``, ``grade_reasons``, ``validation_report`` (what the check dropped, changed
    or added, by stage) and ``prompt_version``. Never raises."""
    from conxa_core.llm.client import TIMELINE_UNDERSTANDING_PROMPT_VERSION  # noqa: PLC0415 -- pulls in settings

    candidates = candidates_from_timeline(merged)
    result: dict[str, Any] = {"source": "none", "candidates": candidates, "goal": "", "values": {},
                              "gaps": [], "short_summary": "", "detailed_summary": [], "decided": [], "questions": []}
    if not _events(merged):
        return result
    result["prompt_version"] = TIMELINE_UNDERSTANDING_PROMPT_VERSION
    told = told_from_hints(candidates, hints)
    payload = build_input(merged, task, chat, candidates, told=told, existing_inputs=existing_inputs)
    caller = call or _default_call
    raw, detail, reason = _attempt(caller, payload, timeout_ms)
    checked, grade, why = _checked(raw, merged, candidates, existing_inputs, told)
    if raw is not None and grade != "ok":
        note = _RETRY_NOTE if grade == "unusable" else _RETRY_WEAK.format(reasons="; ".join(why))
        result["retried_because"] = why
        raw2, detail2, reason2 = _attempt(caller, {**payload, "retry": note}, timeout_ms)
        checked2, grade2, why2 = _checked(raw2, merged, candidates, existing_inputs, told)
        if _GRADE_RANK[grade2] > _GRADE_RANK[grade]:
            raw, detail, reason, checked, grade, why = raw2, detail2, reason2, checked2, grade2, why2
            result["retried"] = True
    result.update(checked)
    result["grade"], result["grade_reasons"] = grade, why
    usable = grade != "unusable"
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
                    _log(result["validation_report"], "backfill", "fallback_url_question", c["ref"])
        result["short_summary"] = f"{len(_events(merged))} recorded actions."
    _ask_about_unmentioned_selections(result, candidates, merged, existing_inputs)
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
