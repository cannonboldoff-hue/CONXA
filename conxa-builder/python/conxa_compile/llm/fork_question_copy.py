"""Friendly wording for the question an author is asked about a popup that needs a decision
(an outcome fork -- see compiler/outcome_fork.py and conversation/clarify.py).

Purely cosmetic. The choices, what each one does and what the workflow will actually do are all
decided by code; this only asks a model to rephrase the QUESTION line for a non-technical reader,
and the deterministic title clarify.py builds is the fallback and the only thing that can ever be
wrong. Same shape as llm/loop_suggestion_copy.py, on the shared llm/copy_rewrite.py helper (cached,
never raises).
"""

from __future__ import annotations

from typing import Any

from conxa_compile.llm.copy_rewrite import rewrite_advisory_text

_NAMESPACE = "fork_question_copy"
_CACHE_VERSION = 1


def friendly_fork_question(
    popup_title: str,
    *,
    buttons: list[str],
    message_hint: str,
    fallback: str,
    error_detail: list[str] | None = None,
) -> str:
    return rewrite_advisory_text(
        "fork_question_copy",
        {"popup_title": popup_title, "buttons": buttons, "message_hint": message_hint},
        fallback=fallback,
        cache_namespace=_NAMESPACE,
        cache_version=_CACHE_VERSION,
        error_detail=error_detail,
    )
