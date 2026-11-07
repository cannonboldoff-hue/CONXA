"""The chat message posted right after a workflow is built, written in plain words by one model call.

Code decides every fact (counts, what was left out and why, uncertain steps, questions to come); the
model only phrases them, and ``write_recap`` checks the answer before it is used. Any failure returns
None and the caller posts its own template text. Never raises. Facts carry no selectors, targets or
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
_SNAKE_ID = re.compile(r"\b[a-z]+_[a-z_]+\b")


def _valid(message: str, facts: dict[str, Any]) -> bool:
    if not (_MIN_CHARS <= len(message) <= _MAX_CHARS):
        return False
    if "`" in message or "{{" in message or _SNAKE_ID.search(message):
        return False
    if facts.get("step_count") and str(facts["step_count"]) not in message:
        return False
    return all(str(n) in message for n in facts.get("unsure_steps") or [])


def write_recap(facts: dict[str, Any], *, error_detail: list[str] | None = None) -> str | None:
    key = cache_key(_CACHE_VERSION, facts=facts)
    cached = read_cached(_NAMESPACE, key, _CACHE_VERSION)
    if cached is not None:
        return str(cached.get("message") or "") or None
    try:
        raw = call_llm("compile_recap", {"user_text": json.dumps(facts, ensure_ascii=False)},
                       settings.llm_text_timeout_ms, error_detail=error_detail)
    except Exception as exc:  # noqa: BLE001 -- wording only, never fail a compile over it
        if error_detail is not None:
            error_detail.append(f"{type(exc).__name__}: {exc}")
        return None
    message = " ".join(str(raw.get("message") or "").split()) if isinstance(raw, dict) else ""
    if not _valid(message, facts):
        return None
    write_cached(_NAMESPACE, key, {"message": message}, _CACHE_VERSION)
    return message
