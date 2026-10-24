"""Understanding check: does the compiled workflow do what the person said, and which recorded
values were only true of the recording? (Gap analysis G3 / the "0 inputs, nothing to ask" defect.)

Clarify's ``values`` question can only see text the person *typed*. A page address the recording
opened ("which repository?"), a row it acted on ("which invoice?") and an item it clicked
("which file?") are just as specific to the recording, and nothing asked about them. This module
finds those places and has one model call decide, from the person's own description, which are
fixed and which change each run.

Two rules, the same ones every other Clarify step keeps:

- **Meaning only.** The model never sees or writes a selector. It labels candidate values
  (``fixed`` / ``varies`` / ``unsure``) and may name an input; code does the rewriting, through
  ``editor.workflow_mutations.parameterize_literal``.
- **Never blocks.** Any failure, an empty answer or no model at all degrades to "ask about the
  likely candidates, unticked" (or ask nothing without a description). The compile is untouched.

Everything but ``understand``'s model call is pure and unit-testable (``call`` is injected).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from conxa_compile.compiler.step_key import step_keys
from conxa_compile.conversation.clarify import _action_name, _steps, step_name

MAX_CANDIDATES = 12
MAX_CLICK_CANDIDATES = 6
_NAME_OK = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_VERDICTS = ("fixed", "varies", "unsure")
SourceLookup = Callable[[dict[str, Any]], "dict[str, Any] | None"]
Caller = Callable[[str, dict[str, Any], int], "dict[str, Any] | None"]


def _snake(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return s[:40].strip("_")


def _fingerprint_label(step: dict[str, Any]) -> str:
    fp = ((step.get("identity_bundle") or {}).get("fingerprint")) or {}
    for field in ("inner_text", "aria_label", "label_text", "title", "alt"):
        text = " ".join(str(fp.get(field) or "").split())
        if text:
            return text[:80]
    return ""


def _url_literal(url: str) -> tuple[str, str] | None:
    """(decoded url, the part of it a person would call "the thing"), or None for a site root."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    path = unquote(parts.path or "").strip("/")
    query = unquote(parts.query or "")
    literal = path + (("?" + query) if query else "")
    if not literal:
        return None
    return unquote(url), literal


def extract_candidates(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Recorded values that aren't typed text, grouped by what they say.

    ``kind`` is ``url`` (a navigate step's address), ``row`` (an entity-bound step's record
    identifier) or ``click`` (a clicked item known only by its visible label). ``supported`` is
    False for ``click``: nothing can yet change what a click's own text signals look for, so those
    are reported but never offered as a choice.
    """
    steps = _steps(doc)
    keys = step_keys(steps)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    def add(kind: str, key: str, step: dict[str, Any], recorded: str, literal: str, supported: bool) -> None:
        gk = (kind, literal.strip().lower())
        if gk not in groups:
            groups[gk] = {
                "ref": f"{kind}:{key}", "kind": kind, "step_keys": [], "step_name": step_name(step),
                "recorded": recorded[:200], "literal": literal.strip(), "supported": supported,
            }
            order.append(gk)
        if key not in groups[gk]["step_keys"]:
            groups[gk]["step_keys"].append(key)

    for i, s in enumerate(steps):
        act = _action_name(s)
        if act == "navigate":
            found = _url_literal(str(s.get("url") or ""))
            if found:
                add("url", keys[i], s, found[0], found[1], True)
        eb = s.get("entity_binding")
        if isinstance(eb, dict) and eb.get("identifier") and "{{" not in str(eb["identifier"]):
            ident = " ".join(str(eb["identifier"]).split())
            add("row", keys[i], s, ident, ident, True)
        elif act in ("click", "dblclick") and not s.get("optional_hint"):
            label = _fingerprint_label(s)
            if len(label) >= 2:
                add("click", keys[i], s, label, label, False)

    out: list[dict[str, Any]] = []
    clicks = 0
    for gk in order:
        c = groups[gk]
        if c["kind"] == "click":
            clicks += 1
            if clicks > MAX_CLICK_CANDIDATES:
                continue
        out.append(c)
    return out[:MAX_CANDIDATES]


def _end_checks(steps: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for s in steps[-3:]:
        for a in (s.get("validation") or {}).get("assertions") or []:
            if isinstance(a, dict) and a.get("type") == "text_present":
                text = str(a.get("text") or a.get("value") or a.get("target") or "").strip()
                if text:
                    out.append(text[:60])
    return out


def build_input(doc: dict[str, Any], task: str, candidates: list[dict[str, Any]]) -> str:
    steps = _steps(doc)
    payload = {
        "task": task.strip()[:300],
        "steps": [step_name(s) for s in steps][:40],
        "ends_with_checks": _end_checks(steps),
        "existing_inputs": [str(i.get("id")) for i in doc.get("inputs") or [] if isinstance(i, dict) and i.get("id")],
        "candidates": [
            {"ref": c["ref"], "kind": c["kind"], "step": c["step_name"], "recorded": c["recorded"]}
            for c in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _unique_name(base: str, taken: set[str]) -> str:
    name = base if _NAME_OK.match(base) else (_snake(base).lstrip("0123456789_") or "value")
    if not _NAME_OK.match(name):
        name = "value"
    cand, n = name, 2
    while cand.lower() in taken:
        cand = f"{name}_{n}"
        n += 1
    taken.add(cand.lower())
    return cand


_FALLBACK_NAME = {"url": "page_address", "row": "record_name", "click": "item"}


def validate(raw: Any, candidates: list[dict[str, Any]], existing_inputs: set[str]) -> dict[str, Any]:
    """The model's answer, held to what code can safely act on. Unknown refs, bad names and a
    ``literal`` that isn't really inside the recorded value are dropped, never trusted."""
    raw = raw if isinstance(raw, dict) else {}
    by_ref = {c["ref"]: c for c in candidates}
    taken = {n.lower() for n in existing_inputs}
    values: dict[str, dict[str, Any]] = {}
    for row in raw.get("values") or []:
        if not isinstance(row, dict) or row.get("ref") not in by_ref or row["ref"] in values:
            continue
        verdict = str(row.get("verdict") or "").lower()
        if verdict not in _VERDICTS:
            continue
        cand = by_ref[row["ref"]]
        literal = cand["literal"]
        proposed = " ".join(str(row.get("literal") or "").split())
        if cand["kind"] == "url" and proposed and proposed.lower() in cand["recorded"].lower():
            literal = proposed
        name = ""
        if verdict != "fixed":
            wanted = _snake(str(row.get("name") or ""))
            name = _unique_name(wanted or _FALLBACK_NAME[cand["kind"]], taken)
        values[row["ref"]] = {"verdict": verdict, "name": name, "literal": literal}
    cov = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    ok = cov.get("ok") is not False
    note = " ".join(str(cov.get("note") or "").split())[:200]
    return {
        "goal_summary": " ".join(str(raw.get("goal_summary") or "").split())[:200],
        "coverage": {"ok": ok or not note, "note": note if not ok else ""},
        "values": values,
    }


def understand(
    doc: dict[str, Any],
    task: str,
    *,
    call: Caller | None = None,
    timeout_ms: int = 30_000,
) -> dict[str, Any]:
    """``{"candidates": [...with verdict/name], "goal_summary", "coverage", "source"}``.

    Never raises. ``source`` is ``model`` when the answer was usable, ``fallback`` otherwise, and
    ``none`` when there was nothing to look at (no candidates and no description)."""
    candidates = extract_candidates(doc)
    task = (task or "").strip()
    existing = {str(i.get("id")) for i in doc.get("inputs") or [] if isinstance(i, dict) and i.get("id")}
    result = {"candidates": candidates, "goal_summary": "", "coverage": {"ok": True, "note": ""}, "source": "none"}
    if not candidates and not task:
        return result

    parsed: dict[str, Any] | None = None
    try:
        if call is None:
            from conxa_compile.llm.client import call_llm as call  # noqa: PLC0415
        parsed = call("workflow_understanding", {"user_text": build_input(doc, task, candidates)}, timeout_ms)
    except Exception:  # noqa: BLE001 -- advisory pass: a failed model call must never fail the compile flow
        parsed = None
    checked = validate(parsed, candidates, existing) if isinstance(parsed, dict) else None

    if checked is not None and (checked["values"] or checked["goal_summary"]):
        result["source"] = "model"
        result["goal_summary"] = checked["goal_summary"]
        result["coverage"] = checked["coverage"]
        values = checked["values"]
    else:
        # No usable answer. With a description, still ask about the likely candidates (unticked);
        # without one there is nothing to judge them against, so ask nothing.
        result["source"] = "fallback"
        taken = {n.lower() for n in existing}
        values = {}
        if task:
            for c in candidates:
                if c["kind"] != "click":
                    values[c["ref"]] = {"verdict": "unsure", "name": _unique_name(_FALLBACK_NAME[c["kind"]], taken), "literal": c["literal"]}
    for c in candidates:
        v = values.get(c["ref"])
        c.update(v if v else {"verdict": "fixed", "name": "", "literal": c["literal"]})
    return result
