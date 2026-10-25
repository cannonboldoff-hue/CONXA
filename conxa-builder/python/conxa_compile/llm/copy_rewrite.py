"""Generic compile-time "ask an LLM to rewrite this into friendlier copy" helper.

Every advisory, non-primary LLM call whose only job is turning something the compiler
already decided deterministically into better prose for a human reviewer — never a
decision-maker, never allowed to change what actually got compiled — should call this
instead of hand-rolling its own cache + fallback + failure-logging dance.
`llm/loop_suggestion_copy.py` was the first caller (since folded into the compile's review call, BUILD-55): it existed as a
bespoke module before this was pulled out, mirroring the same duplication llm_cache.py's
own docstring describes ("every task client under conxa_compile/llm/ has hand-rolled the
identical dual-write cache... this extracts that one pattern").

Every task built to go through here must have a client.py branch whose system prompt asks
for strict JSON `{"text": "..."}` — this helper only ever reads that one key.
"""

from __future__ import annotations

from typing import Any

from conxa_core.config import settings

from conxa_compile.llm.client import call_llm
from conxa_compile.llm.llm_cache import cache_key, read_cached, write_cached


def rewrite_advisory_text(
    task: str,
    input_payload: dict[str, Any],
    *,
    fallback: str,
    cache_namespace: str,
    cache_version: int,
    timeout_ms: int | None = None,
    error_detail: list[str] | None = None,
) -> str:
    """Returns a plain-language rewrite from LLM task `task`, or `fallback` itself on any
    failure — never raises, so a provider outage, missing config, or a malformed response
    never blocks a compile over UI copy. `cache_namespace`/`cache_version` are the caller's
    own (see llm_cache.py) — bump the version whenever `input_payload`'s shape changes, so a
    stale cache entry keyed against the old, thinner payload is never served as if it were
    built from richer context."""
    key = cache_key(cache_version, task=task, input=input_payload)
    cached = read_cached(cache_namespace, key, cache_version)
    if cached is not None:
        text = str(cached.get("text") or "").strip()
        if text:
            return text

    payload: dict[str, Any] = {"input": input_payload}
    try:
        raw = call_llm(task, payload, timeout_ms or settings.llm_text_timeout_ms, error_detail=error_detail)
    except Exception as exc:  # noqa: BLE001 — advisory copy only, never fail compile over this
        if error_detail is not None:
            error_detail.append(f"{type(exc).__name__}: {exc}")
        return fallback

    text = str((raw or {}).get("text") or "").strip()
    if not text:
        # A response came back (no exception) but with no usable "text" — a model that
        # ignored the JSON-key instruction, an empty completion, or a router-level failure
        # that returns None instead of raising. Record it too, not just the exception path
        # above — otherwise this looks identical to "nothing was even attempted."
        if error_detail is not None:
            error_detail.append(f"empty or malformed response: {raw!r}")
        return fallback
    write_cached(cache_namespace, key, {"text": text}, cache_version)
    return text
