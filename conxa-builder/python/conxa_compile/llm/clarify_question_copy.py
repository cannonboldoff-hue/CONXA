"""Friendly wording for the follow-up questions asked after a compile (conversation/clarify.py).

Purely cosmetic. Code builds every question, its choices and what each choice does; one batched
model call only rephrases the title, body and single-choice option text -- popup questions
included, which is why this call is also handed each popup's own button names -- and
clarify.apply_reworded validates the answer field by field. Any failure returns None and the
template wording stands. Never raises.

The answer is cached on the exact drafts it was asked about (BUILD-55): recompiling an unchanged
workflow, or opening it again, costs nothing. Bump ``_CACHE_VERSION`` whenever the prompt for
``clarify_question_copy`` (conxa_core/llm/client.py) or the payload shape changes.
"""

from __future__ import annotations

from typing import Any

from conxa_core.config import settings

from conxa_compile.conversation.clarify import reword_payload
from conxa_compile.llm.client import call_llm
from conxa_compile.llm.llm_cache import cache_key, read_cached, write_cached

_NAMESPACE = "clarify_question_copy"
_CACHE_VERSION = 1


def reword_questions(questions: list[dict[str, Any]], *, error_detail: list[str] | None = None) -> dict[str, Any] | None:
    items = reword_payload(questions)
    if not items:
        return None
    key = cache_key(_CACHE_VERSION, items=items)
    cached = read_cached(_NAMESPACE, key, _CACHE_VERSION)
    if cached is not None:
        return {k: v for k, v in cached.items() if k != "_v"}
    try:
        raw = call_llm("clarify_question_copy", {"input": {"questions": items}},
                       settings.llm_text_timeout_ms, error_detail=error_detail)
    except Exception as exc:  # noqa: BLE001 -- wording only, never fail a compile over it
        if error_detail is not None:
            error_detail.append(f"{type(exc).__name__}: {exc}")
        return None
    if isinstance(raw, dict) and isinstance(raw.get("questions"), list) and raw["questions"]:
        write_cached(_NAMESPACE, key, raw, _CACHE_VERSION)
    return raw
