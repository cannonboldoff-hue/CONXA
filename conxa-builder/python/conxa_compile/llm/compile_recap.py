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

from conxa_compile.llm.client import call_llm
from conxa_compile.llm.llm_cache import cache_key, read_cached, write_cached

_NAMESPACE = "compile_recap"
_CACHE_VERSION = 1
_MIN_CHARS, _MAX_CHARS = 40, 900
_ATTEMPTS = 3
_SNAKE_ID = re.compile(r"\b[a-z]+_[a-z_]+\b")


def _problem(message: str, facts: dict[str, Any]) -> str:
    """Why this answer cannot be used ('' when it can). Told back to the model on the next attempt."""
    if not (_MIN_CHARS <= len(message) <= _MAX_CHARS):
        return f"it must be between {_MIN_CHARS} and {_MAX_CHARS} characters"
    if "`" in message or "{{" in message or _SNAKE_ID.search(message):
        return "it must not contain backticks, braces or names with underscores"
    if facts.get("step_count") and str(facts["step_count"]) not in message:
        return f"it must say the number of steps ({facts['step_count']})"
    missing = [str(n) for n in facts.get("unsure_steps") or [] if str(n) not in message]
    return f"it must include exactly: {', '.join(missing)}" if missing else ""


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
        payload["previous_answer_rejected_because"] = problem
    return None
