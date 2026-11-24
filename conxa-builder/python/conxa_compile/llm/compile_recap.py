"""The chat message posted right after a workflow is built, written in plain words by one model call.

Code decides every fact (counts, what was left out and why, uncertain steps, questions to come); the
model only phrases them, and ``write_recap`` checks the answer before it is used. After three failed tries it returns
None and the caller shows a warning line; there is no stand-in text. Never raises. Facts carry no selectors, targets or
page text. Cached on the exact facts: re-opening an unchanged compile costs nothing. Bump
``_CACHE_VERSION`` whenever the ``compile_recap`` prompt (conxa_core/llm/client.py) or the fact shape changes.
"""

from __future__ import annotations

import json
import re
from typing import Any

from conxa_core.config import settings

from conxa_compile.conversation.plain_words import plain_ok
from conxa_compile.llm.client import call_llm
from conxa_compile.llm.llm_cache import cache_key, read_cached, write_cached

_NAMESPACE = "compile_recap"
_CACHE_VERSION = 4
_MIN_CHARS, _MAX_CHARS = 40, 900
_ATTEMPTS = 3
# The reason given for drawing the recording could not keep (handlers/compile_flow.py puts it in ``left_out``).
DRAWING_LEFT_OUT = "drawing, or clicking in a drawing area, can't be recorded yet"
HUMAN_CHECK_LEFT_OUT = "a robot check can't be done automatically; only a person may do it"
LOST_LEFT_OUT = "they could not be kept in the workflow; the person is asked about them"   # BUILD-23/24
# A message must not claim the workflow does what the person did, or gets past a robot check, when one of these
# walls left part of it out (WF-8 8.12, 8.13).
_WALLS = (DRAWING_LEFT_OUT, HUMAN_CHECK_LEFT_OUT, LOST_LEFT_OUT)
_GETS_PAST = re.compile(r"\b(?:gets?|getting|goes|going) (?:past|through|around)\b|\b(?:solves?|passes|beats?) the robot", re.IGNORECASE)
# Claims a message must not make when something was left out: the facts say otherwise (WF-8 8.12).
_NOTHING_LEFT_OUT = re.compile(r"\bnothing (?:was |is |has been )?(?:left out|missed|dropped)", re.IGNORECASE)
_MENTIONS_TABS = re.compile(r"\b(?:new|another|second|other|first) (?:tab|window)\b", re.IGNORECASE)
_DOES_ALL_YOU_DID = re.compile(r"\b(?:same thing|everything|exactly what|all) you did\b", re.IGNORECASE)


def _problem(message: str, facts: dict[str, Any]) -> str:
    """Why this answer cannot be used ('' when it can). Told back to the model on the next attempt."""
    if not (_MIN_CHARS <= len(message) <= _MAX_CHARS):
        return f"it must be between {_MIN_CHARS} and {_MAX_CHARS} characters"
    if not plain_ok(message):
        return "it must not contain backticks, braces, names with underscores or field paths"
    if facts.get("step_count") and str(facts["step_count"]) not in message:
        return f"it must say the number of steps ({facts['step_count']})"
    left_out = facts.get("left_out") or []
    if left_out and _NOTHING_LEFT_OUT.search(message):
        return "something was left out (see left_out): never say nothing was left out"
    if any(d.get("reason") in _WALLS for d in left_out) and _DOES_ALL_YOU_DID.search(message):
        return "part of it was left out, so never say it does what they did"
    if any(d.get("reason") == HUMAN_CHECK_LEFT_OUT for d in left_out) and _GETS_PAST.search(message):
        return "the robot check was left out, so never say it gets past it"
    tabs = facts.get("tabs")
    if isinstance(tabs, dict) and not (tabs.get("opened") or tabs.get("switched")) and _MENTIONS_TABS.search(message):
        return "the workflow never opens or switches tabs (see tabs): do not mention tabs or windows"
    missing = [str(n) for n in facts.get("unsure_steps") or [] if str(n) not in message]
    return f"it must include exactly: {', '.join(missing)}" if missing else ""


_UNREACHABLE = re.compile(r"cloud_unreachable|urlerror|connection|timed? ?out|getaddrinfo|unreachable", re.IGNORECASE)


def plain_cause(error_detail: list[str] | None) -> str:
    """Why a model answer is missing, in one sentence a person understands ('' when nothing was recorded).

    Used for the missing summary (``write_recap``'s ``error_detail``) and a build step that got no answer
    (``compile_report["degraded"]`` detail). Only explains: it never stands in for the missing text."""
    lines = [str(e) for e in error_detail or [] if str(e).strip()]
    if not lines:
        return ""
    if all(e.startswith("answer rejected") for e in lines):
        return "The AI's wording didn't pass Studio's checks, so it isn't shown. Nothing else is affected."
    if _UNREACHABLE.search(" ".join(lines)):
        return "Studio couldn't reach Conxa Cloud. Check your internet connection, then build again."
    return "The AI service didn't answer. Building again usually fixes it."


def write_recap(facts: dict[str, Any], *, error_detail: list[str] | None = None) -> str | None:
    """The message, or None after ``_ATTEMPTS`` failed tries. There is no stand-in text: the caller says so."""
    key = cache_key(_CACHE_VERSION, facts=facts)
    cached = read_cached(_NAMESPACE, key, _CACHE_VERSION)
    if cached is not None:
        return str(cached.get("message") or "") or None
    payload: dict[str, Any] = dict(facts)
    for _ in range(_ATTEMPTS):
        try:
            raw = call_llm("compile_recap", {"user_text": json.dumps(payload, ensure_ascii=False)},
                           settings.llm_text_timeout_ms, error_detail=error_detail)
        except Exception as exc:  # noqa: BLE001 -- the caller reports a missing message; it never fails a compile
            if error_detail is not None:
                error_detail.append(f"{type(exc).__name__}: {exc}")
            continue
        message = " ".join(str(raw.get("message") or "").split()) if isinstance(raw, dict) else ""
        problem = _problem(message, facts)
        if not problem:
            write_cached(_NAMESPACE, key, {"message": message}, _CACHE_VERSION)
            return message
        if error_detail is not None:
            error_detail.append(f"answer rejected: {problem}")   # so "couldn't write the summary" has a recorded cause
        payload["previous_answer_rejected_because"] = problem
    return None
