"""Multi-provider LLM router with cool-down, failover, and per-key rate-limit handling."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from conxa_core.config import settings

from conxa_core.llm.client import (
    VISION_TASKS,
    _chat_completions_url,
    _copilot_modality,
    _debug_log,
    _is_openai_compatible_endpoint,
    _iter_sse_deltas,
    _iter_sse_text_deltas,
    _normalize_openai_response,
    _openai_body_dict,
    _provider_top_level_error,
    _safe_error_snippet,
)
from conxa_core.progress import append_current_job_event, has_active_job_sink


logger = logging.getLogger(__name__)

# The BYOK-path (for_vision is None) alias for conxa_core.llm.client.VISION_TASKS — see that
# constant's docstring. Not a separate literal set; kept as a local name only so call sites in
# this file don't need to say the fully-qualified conxa_core name everywhere.
_VISION_TASK_NAMES = VISION_TASKS


# Request fields a particular model needs that the generic OpenAI-compatible body does not carry.
# One row per (endpoint host, model name) pair: add a row here rather than a special case in routing.
# ponytail: Kimi K3's reasoning_details isn't echoed back across execute_chat turns; add if multi-turn
# tool chains lose their thread.
_MODEL_REQUEST_EXTRAS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    # Kimi K3 on OpenRouter is pinned to one provider, with reasoning on and no fallbacks.
    ("openrouter.ai", "kimi-k3", {
        "reasoning": {"enabled": True},
        "provider": {"only": ["sail-research/fp4"], "allow_fallbacks": False},
    }),
)


def _model_request_extras(endpoint: str, model: str) -> dict[str, Any]:
    endpoint, model = endpoint.lower(), model.lower()
    out: dict[str, Any] = {}
    for host, model_part, extras in _MODEL_REQUEST_EXTRAS:
        if host in endpoint and model_part in model:
            out.update(extras)
    return out


@dataclass
class PoolEntry:
    """Single (provider, endpoint, key, model) tuple in the router pool."""
    provider: str
    endpoint: str
    api_key: str
    text_model: str
    vision_model: str
    pool: str = "free"  # "free" | "starter" | "pro" — see Settings.llm_starter_provider / llm_pro_provider
    # Model to retry on this same entry when the primary model fails (empty = no fallback).
    # Only Starter/Pro tiers set these today; Free-pool entries leave them empty.
    fallback_text_model: str = ""
    fallback_vision_model: str = ""
    # Copilot's screenshot+conversation turns (BUILD-26) — separate from vision_model/
    # cooled_until_vision so a rate-limited vision model never falsely cools down a distinct
    # multimodal model on the same entry, or vice versa. Empty = falls back to vision_model
    # (see _call_provider's model selection).
    multimodal_model: str = ""
    fallback_multimodal_model: str = ""
    # "bearer" (Authorization: Bearer <key>, every pooled provider) or
    # "api_key_header" (api-key: <key> — Azure OpenAI's REST auth, used only
    # by BYOK entries; see app/services/byok.py).
    auth_style: str = "bearer"
    requests_sent: int = 0
    requests_429: int = 0
    last_used_at: float = 0.0
    # Text and vision cooldowns are tracked separately: a text-only outage (e.g. a
    # burst of intent_generation timeouts) used to bench the same key's vision
    # capacity, so one flavor of call starving the pool would also fail the other.
    cooled_until_text: float = 0.0
    cooled_until_vision: float = 0.0
    cooled_until_multimodal: float = 0.0
    # Consecutive transient (timeout/connection) failures, used to back off the
    # cooldown per-entry (5s -> 15s -> 45s, capped) instead of a flat 60s that both
    # over-punishes a single blip and under-punishes a truly dead endpoint. Reset
    # to 0 on any successful call.
    consecutive_transient_failures: int = 0
    # 401/403 no longer removes the key from the pool outright (that had no
    # re-admission path — a transient auth glitch permanently shrank the pool
    # until process restart). Instead it's quarantined for a while and can be
    # selected again once the quarantine clears.
    quarantined_until: float = 0.0

    def cooled_until(self, *, for_vision: bool, multimodal: bool = False) -> float:
        if for_vision and multimodal:
            return self.cooled_until_multimodal
        return self.cooled_until_vision if for_vision else self.cooled_until_text

    def cool(self, *, for_vision: bool, until: float, multimodal: bool = False) -> None:
        if for_vision and multimodal:
            self.cooled_until_multimodal = until
        elif for_vision:
            self.cooled_until_vision = until
        else:
            self.cooled_until_text = until


class _DeterministicRejection(Exception):
    """A provider rejected the request in a way every provider will (bad payload —
    context length, unsupported response_format, malformed JSON body). Raised out
    of ``_call_provider`` so ``_route`` fails fast instead of burning the pool's
    other two entries retrying a request they'll reject identically, and so a
    healthy key doesn't get cooled down for a client-side mistake."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _parse_retry_after_secs(headers: Any) -> float | None:
    """Parse a numeric Retry-After header into seconds, or None if absent/invalid.

    Providers send this in seconds (not HTTP-date form) for rate-limit responses.
    """
    raw = headers.get("Retry-After") if headers is not None else None
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value > 3600:
        return None
    return value


_SECRET_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "cookie",
    "cookies",
    "storage_state",
    "storage_state_path",
}


def _redact_url(value: str) -> str:
    if "?" not in value:
        return value
    base, _, query = value.partition("?")
    if not query:
        return base
    return f"{base}?[redacted_query]"


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in _SECRET_KEYS:
                out[key_text] = "[redacted]"
            elif lowered in {"image_base64", "base64"} and isinstance(item, str):
                out[key_text] = _redacted_blob(item)
            elif lowered == "url" and isinstance(item, str) and item.startswith("data:"):
                out[key_text] = _redacted_data_url(item)
            else:
                out[key_text] = _redact_value(item)
        return out
    if isinstance(value, list):
        return [_redact_value(item) for item in value[:20]]
    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value[:80]:
            return _redacted_data_url(value)
        if len(value) > 1600:
            return f"{value[:1600]}... [truncated {len(value) - 1600} chars]"
    return value


def _redacted_blob(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"[redacted_base64 chars={len(value)} sha256={digest}]"


def _redacted_data_url(value: str) -> str:
    prefix, _, payload = value.partition(",")
    return f"{prefix},[redacted chars={len(payload)} sha256={hashlib.sha256(payload.encode('utf-8', errors='ignore')).hexdigest()[:16]}]"


def _redacted_preview(value: Any) -> str:
    try:
        text = json.dumps(_redact_value(value), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= 2400 else f"{text[:2400]}... [truncated {len(text) - 2400} chars]"


def _log_llm_exception(
    req_id: int,
    entry: PoolEntry,
    endpoint: str,
    model: Any,
    task: str,
    attempt: int,
    status_code: int | None,
    duration_ms: float,
    message: str,
) -> None:
    append_current_job_event(
        "api_call",
        f"LLM request failed: {task}.",
        {
            "phase": "llm_request_failed",
            "request_id": req_id,
            "provider": entry.provider,
            "endpoint": _redact_url(endpoint),
            "model": model,
            "task": task,
            "attempt": attempt,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "error": message,
        },
    )


class LLMRouter:
    """Multi-provider LLM router with per-key cool-down and automatic failover."""

    def __init__(self):
        """Build the initial provider pool from enabled providers in settings."""
        self.pool: list[PoolEntry] = []
        self.cooldown_secs: int = settings.llm_router_cooldown_secs
        self.max_retries: int = settings.llm_router_max_retries
        # Bound on how long a single route_text/route_vision call will block waiting for a
        # cooled-down key to clear, instead of failing the request outright. Keeps a transient
        # 429 from costing a full compile step while never blocking past a caller's patience.
        self.wait_ceiling_secs: float = settings.llm_router_wait_ceiling_secs
        # Whole-call wall-clock ceiling across every attempt + wait, kept under Render's
        # free-tier proxy timeout so a degraded pool answers with a real 502 instead of
        # the edge dropping the connection first.
        self.total_budget_secs: float = settings.llm_router_total_budget_secs
        self._request_counter: int = 0
        self._last_lru_index: int = 0
        # Guards _last_lru_index. The pool list itself is never mutated after
        # __init__ any more (401/403 quarantines instead of removing — see
        # _call_provider), so this only has to protect the LRU cursor: under
        # FastAPI's ~40-thread pool, an unguarded read-increment-read on a shared
        # int meant concurrent requests could pick the same "next" entry and
        # hammer it, which is what caused self-inflicted 429 storms.
        self._lru_lock = threading.Lock()

        # Build pool from enabled providers
        for provider_cfg in settings.enabled_llm_providers():
            entry = PoolEntry(
                provider=provider_cfg.provider,
                endpoint=provider_cfg.endpoint,
                api_key=provider_cfg.api_key,
                text_model=provider_cfg.text_model,
                vision_model=provider_cfg.vision_model,
                pool=provider_cfg.pool,
                fallback_text_model=provider_cfg.fallback_text_model,
                fallback_vision_model=provider_cfg.fallback_vision_model,
                multimodal_model=provider_cfg.multimodal_model,
                fallback_multimodal_model=provider_cfg.fallback_multimodal_model,
            )
            self.pool.append(entry)
        # Ensures a full pool sweep gets tried even when max_retries (a config
        # constant sized for the smallest expected pool) is smaller than the
        # actual number of entries — otherwise a larger pool never gets a fair
        # shot at every key within one route_text/route_vision call.
        self.max_retries = max(self.max_retries, len(self.pool))

    def _next_available_entry(
        self, *, for_vision: bool = False, multimodal: bool = False, pool: str | None = None
    ) -> PoolEntry | None:
        """Pick next available entry from pool using LRU, skipping cooled entries.

        ``pool`` (None = no filter) restricts to "free", "starter", or "pro"
        entries — the caller passes the requesting workspace's compile_pool
        capability (docs/PRD.md §11). A pool with no matching entries falls
        through to None just like an exhausted pool, rather than silently
        mixing tiers."""
        if not self.pool:
            return None

        now = time.monotonic()
        attempts = 0
        max_attempts = len(self.pool) * 2

        while attempts < max_attempts:
            with self._lru_lock:
                self._last_lru_index = (self._last_lru_index + 1) % len(self.pool)
                index = self._last_lru_index
            entry = self.pool[index]
            attempts += 1

            # Skip cooled or quarantined entries (quarantine — see 401/403 handling
            # below — applies to every modality, unlike the per-modality cooldown)
            if entry.cooled_until(for_vision=for_vision, multimodal=multimodal) > now or entry.quarantined_until > now:
                continue

            # For vision tasks, skip entries without vision_model — multimodal_model is an
            # optional bonus on an already vision-admitted entry, never its own gate (it falls
            # back to vision_model when unset, so admission stays keyed on vision_model alone).
            if for_vision and not entry.vision_model:
                continue

            if pool is not None and entry.pool != pool:
                continue

            return entry

        return None

    def _soonest_cooldown(self, *, for_vision: bool, multimodal: bool = False, pool: str | None = None) -> float | None:
        """Earliest clear time (monotonic) among entries matching ``for_vision``
        and ``pool`` (None = no filter), or None if no such entries exist at all (a
        config gap, not a cooldown). Filtering on ``pool`` matters — without it a
        Free-tier caller could be told to wait out a Starter/Pro-only entry's cooldown.
        Accounts for quarantine too, since a quarantined entry isn't selectable
        even once its modality cooldown clears."""
        candidates = [
            e for e in self.pool
            if (not for_vision or e.vision_model) and (pool is None or e.pool == pool)
        ]
        if not candidates:
            return None
        return min(
            max(e.cooled_until(for_vision=for_vision, multimodal=multimodal), e.quarantined_until)
            for e in candidates
        )

    def _route(
        self,
        task: str,
        payload: dict[str, Any],
        timeout_ms: int,
        *,
        for_vision: bool,
        error_detail: list[str] | None,
        pool: str | None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any] | None:
        """Shared route_text/route_vision body: pick an entry, fall back across pools,
        and wait for cooled-down keys to clear (bounded by a total ``wait_ceiling_secs``
        budget across the whole call) instead of instantly failing on a transient 429.
        Every attempt plus every wait is also bounded by ``total_budget_secs`` wall-clock
        so a degraded pool answers with a real 502 well inside the proxy's own timeout,
        instead of the caller's request budget (up to 3 attempts x timeout_ms) blowing
        past it and getting cut off at the edge."""
        wait_budget = self.wait_ceiling_secs
        deadline = time.monotonic() + self.total_budget_secs
        # Computed once per call — Copilot's own two tasks pick "multimodal" vs. plain "vision"
        # per turn depending on whether a screenshot is attached (see _copilot_modality); every
        # other vision task is unaffected (multimodal stays False).
        multimodal = for_vision and (_copilot_modality(task, payload) == "multimodal")

        for attempt in range(self.max_retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _debug_log(f"router: total_budget_secs exhausted for task {task}")
                if error_detail is not None:
                    error_detail.append("router: total request budget exhausted")
                break

            entry = self._next_available_entry(for_vision=for_vision, multimodal=multimodal, pool=pool)
            if entry is None and pool is not None:
                _debug_log(f"router: pool={pool} exhausted{' for vision' if for_vision else ''}, falling back to any pool")
                entry = self._next_available_entry(for_vision=for_vision, multimodal=multimodal)

            if entry is None and wait_budget > 0:
                soonest = self._soonest_cooldown(for_vision=for_vision, multimodal=multimodal, pool=pool)
                if soonest is not None:
                    wait_s = min(soonest - time.monotonic(), remaining)
                    if 0 < wait_s <= wait_budget:
                        _debug_log(f"router: all entries cooled, waiting {wait_s:.1f}s for soonest to clear")
                        time.sleep(wait_s)
                        wait_budget -= wait_s
                        entry = self._next_available_entry(for_vision=for_vision, multimodal=multimodal, pool=pool)
                        if entry is None and pool is not None:
                            entry = self._next_available_entry(for_vision=for_vision, multimodal=multimodal)
                    else:
                        wait_budget = 0

            if entry is None:
                _debug_log("router: all providers cooled or exhausted")
                if error_detail is not None:
                    error_detail.append("router: all providers cooled or exhausted")
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if error_detail is not None:
                    error_detail.append("router: total request budget exhausted")
                break

            try:
                result = self._call_provider(
                    entry,
                    task,
                    payload,
                    min(timeout_ms, int(remaining * 1000)),
                    error_detail=error_detail,
                    attempt=attempt,
                    on_delta=on_delta,
                    for_vision=for_vision,
                    multimodal=multimodal,
                )
            except _DeterministicRejection:
                # Every provider will reject this exact payload the same way — stop
                # immediately instead of retrying two more times against a request
                # that can't succeed, and don't cool the (healthy) entry that saw it.
                break

            if result is not None:
                return result

            # Continue to next provider on failure
            _debug_log(f"router: retry {attempt + 1}/{self.max_retries} for task {task}")

        return None

    def route_text(
        self,
        task: str,
        payload: dict[str, Any],
        timeout_ms: int,
        *,
        error_detail: list[str] | None = None,
        pool: str | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any] | None:
        """Route a text-only LLM call to an available provider.

        ``pool`` restricts to "free", "starter", or "pro" providers; if the
        requested pool has no available entry, falls back to any pool rather
        than failing a paying customer's compile over a provider misconfiguration.

        ``on_delta``, when given, streams the reply: the provider request sets
        ``stream: true`` and drops JSON-object mode (see `_call_provider`), and `on_delta` fires
        with each text chunk as it arrives. Cross-provider failover still applies between
        attempts, but a failure mid-stream (after some chunks already reached `on_delta`) is not
        retried — the caller sees whatever text arrived. Today only `copilot_reply` uses this."""
        if not self.pool:
            raise RuntimeError(
                "No LLM providers enabled. Set at least one LLM_{TIER}_API_KEYS "
                "(e.g. LLM_FREE_API_KEYS=sk-or-... for the Free/OpenRouter pool)."
            )
        return self._route(
            task, payload, timeout_ms, for_vision=False, error_detail=error_detail, pool=pool,
            on_delta=on_delta,
        )

    def route_vision(
        self,
        task: str,
        payload: dict[str, Any],
        timeout_ms: int,
        *,
        error_detail: list[str] | None = None,
        pool: str | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any] | None:
        """Route a vision-capable LLM call to an available provider. See
        route_text for the ``pool`` fallback behavior and the ``on_delta`` streaming contract."""
        if not self.pool:
            raise RuntimeError(
                "No LLM providers enabled. Set at least one *_API_KEYS and "
                "*_ENABLED=true in .env. Note: vision tasks require providers with a vision_model."
            )
        return self._route(
            task, payload, timeout_ms, for_vision=True, error_detail=error_detail, pool=pool,
            on_delta=on_delta,
        )

    def call_entry_directly(
        self,
        entry: PoolEntry,
        task: str,
        payload: dict[str, Any],
        timeout_ms: int,
        *,
        error_detail: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Single-attempt call against a caller-supplied entry, bypassing pool
        selection and cross-provider failover entirely. Used for BYOK — there's
        exactly one deployment to call, so the shared pool's rotate/cool-down/
        drop-on-401 machinery (which assumes many interchangeable keys) doesn't
        apply; the entry is never added to self.pool, so it's never dropped
        from anything. One retry mirrors the pooled paths' minimum useful
        resilience against a single transient failure."""
        for attempt in range(min(2, self.max_retries)):
            try:
                result = self._call_provider(entry, task, payload, timeout_ms, error_detail=error_detail, attempt=attempt)
            except _DeterministicRejection:
                # Retrying the same single deployment won't change a deterministic
                # rejection's outcome.
                return None
            if result is not None:
                return result
        return None

    def _call_provider(
        self,
        entry: PoolEntry,
        task: str,
        payload: dict[str, Any],
        timeout_ms: int,
        *,
        error_detail: list[str] | None = None,
        attempt: int = 0,
        on_delta: Callable[[str], None] | None = None,
        for_vision: bool | None = None,
        multimodal: bool | None = None,
    ) -> dict[str, Any] | None:
        """Make a single HTTP request to a provider.

        ``for_vision``/``multimodal`` are normally supplied by ``_route`` (which already
        computed them once for the whole call). ``call_entry_directly`` (BYOK) never passes
        them, so they're recomputed here from ``task``/``payload`` — this is what keeps a
        BYOK image-less Copilot turn routed to the text model instead of always falling
        through to vision, matching the pooled path."""
        self._request_counter += 1
        req_id = self._request_counter
        now = time.monotonic()

        # Kept in sync with the two other copies of this literal set — conxa_core.llm.client.
        # _is_vision_task's docstring has the BUILD-26 note on why this is triplicated.
        is_vision_task = for_vision if for_vision is not None else (task in _VISION_TASK_NAMES)
        is_multimodal_task = (
            multimodal if multimodal is not None else (_copilot_modality(task, payload) == "multimodal")
        )
        model = payload.get("model")
        if not model:
            if not is_vision_task:
                primary_model, fallback_model = entry.text_model, entry.fallback_text_model
            elif is_multimodal_task:
                primary_model = entry.multimodal_model or entry.vision_model
                fallback_model = entry.fallback_multimodal_model or entry.fallback_vision_model
            else:
                primary_model, fallback_model = entry.vision_model, entry.fallback_vision_model
            # ponytail: retries land on the same entry when the pool has just one
            # matching entry (the common Starter/Pro shape), so attempt>0 == "primary
            # already failed on this entry" and switching to the fallback model is
            # correct. A multi-key tier can rotate to a fresh key on attempt>0 and
            # skip straight to its fallback model without trying that key's primary
            # first — track per-entry tried-model state if that edge case matters.
            model = fallback_model if (attempt > 0 and fallback_model) else primary_model

        # Prepare payload with the selected model
        payload_with_model = dict(payload)
        payload_with_model["model"] = model

        _debug_log(
            f"router: request_start req_id={req_id} provider={entry.provider} "
            f"endpoint={entry.endpoint} model={model} task={task} attempt={attempt}"
        )

        # Build OpenAI-compatible request
        if not _is_openai_compatible_endpoint(entry.endpoint):
            _debug_log(f"router: endpoint not openai-compatible {entry.endpoint}")
            if error_detail is not None:
                error_detail.append(f"endpoint not openai-compatible: {entry.endpoint}")
            return None

        ep = _chat_completions_url(entry.endpoint)
        headers = {"Content-Type": "application/json"}
        if entry.auth_style == "api_key_header":
            headers["api-key"] = entry.api_key
        else:
            headers["Authorization"] = f"Bearer {entry.api_key}"

        timeout_s = max(0.2, timeout_ms / 1000.0)

        started = time.perf_counter()

        try:
            # Streaming never uses json_object mode — the model would produce raw JSON text one
            # token at a time, which reads as garbage mid-stream (see route_text's docstring).
            body_dict = _openai_body_dict(task, payload_with_model, json_mode=on_delta is None)
            body_dict.update(_model_request_extras(entry.endpoint, str(body_dict.get("model", ""))))
            # OpenRouter groups requests sharing a session_id (Execute sends one per chat).
            chat_session_id = str(payload_with_model.get("session_id") or "").strip()[:128]
            if chat_session_id and "openrouter.ai" in entry.endpoint:
                body_dict["session_id"] = chat_session_id
            if on_delta is not None:
                body_dict["stream"] = True
            raw_body = json.dumps(body_dict).encode("utf-8")
            # Guarded on has_active_job_sink() — the proxy path never opens a job
            # scope, so this used to build a full redacted preview (including a
            # SHA-256 over any base64 image) on every single request and throw it
            # away unread. append_current_job_event's own internal check runs too
            # late: Python evaluates keyword arguments before the call.
            if has_active_job_sink():
                append_current_job_event(
                    "api_call",
                    f"LLM request started: {task}.",
                    {
                        "phase": "llm_request_start",
                        "request_id": req_id,
                        "provider": entry.provider,
                        "endpoint": _redact_url(ep),
                        "model": model,
                        "task": task,
                        "attempt": attempt,
                        "request_bytes": len(raw_body),
                        "request_preview": _redacted_preview(body_dict),
                    },
                )
            req = request.Request(ep, data=raw_body, headers=headers, method="POST")

            if on_delta is not None:
                # Streaming: read the provider's own SSE stream and forward each text delta as
                # it arrives. No JSON envelope to inspect for a provider-level error here — an
                # error surfaces as an HTTPError before any body streams (handled below), or as
                # an empty stream, which the caller (route_text/route_vision) treats as failure.
                full_text_parts: list[str] = []
                observed: dict[str, Any] = {}
                with request.urlopen(req, timeout=timeout_s) as res:
                    status_code = getattr(res, "status", None) or getattr(res, "code", None)
                    if task == "execute_chat":
                        # Tool-call-aware: on_delta receives a tagged dict
                        # ({"type": "text"|"tool_call", ...}) instead of a
                        # bare string — only this task's callers expect that
                        # shape (see llm_proxy_routes.py's _meter_and_stream).
                        for item in _iter_sse_deltas(res, observed=observed):
                            if item["type"] == "text":
                                full_text_parts.append(item["text"])
                            on_delta(item)
                    else:
                        for chunk in _iter_sse_text_deltas(res, observed=observed):
                            full_text_parts.append(chunk)
                            on_delta(chunk)

                entry.requests_sent += 1
                entry.last_used_at = now
                entry.consecutive_transient_failures = 0
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                full_text = "".join(full_text_parts)
                _debug_log(f"router: response_ok(stream) req_id={req_id} provider={entry.provider}")
                if has_active_job_sink():
                    append_current_job_event(
                        "api_call",
                        f"LLM request completed: {task}.",
                        {
                            "phase": "llm_request_done",
                            "request_id": req_id,
                            "provider": entry.provider,
                            "endpoint": _redact_url(ep),
                            "model": model,
                            "task": task,
                            "attempt": attempt,
                            "status_code": status_code,
                            "duration_ms": duration_ms,
                            "request_bytes": len(raw_body),
                            "response_bytes": len(full_text.encode("utf-8")),
                        },
                    )
                if not full_text:
                    # BUILD-33: a reasoning-capable model can spend its whole completion budget
                    # on hidden chain-of-thought and write zero content — a real, paid-for
                    # generation, not a dead provider. Every other retry against the same entry
                    # would hit the identical shape, so this is _DeterministicRejection's case
                    # too: fail fast (no cooldown, no more attempts on this call) instead of
                    # burning the pool's other entries and 2 more billed attempts on a request
                    # that can't produce content this way.
                    # ponytail: doesn't retry with a non-reasoning fallback model on the same
                    # entry before giving up — attempt>0 would pick one up if configured, but a
                    # dedicated retry-with-different-model path isn't worth it here; add if this
                    # condition turns out common enough that the fallback would routinely rescue it.
                    if observed.get("reasoning_chars", 0) > 0 or observed.get("finish_reason") == "length":
                        msg = (
                            f"reasoning_only_no_content: {observed.get('reasoning_chars', 0)} "
                            f"reasoning chars, finish_reason={observed.get('finish_reason')!r}, "
                            f"model={model}, provider={entry.provider}"
                        )
                        _debug_log(f"router: {msg}")
                        _log_llm_exception(req_id, entry, ep, model, task, attempt, status_code, duration_ms, msg)
                        if error_detail is not None:
                            error_detail.append(msg)
                        raise _DeterministicRejection(msg)
                    if error_detail is not None:
                        error_detail.append("stream produced no content")
                    return None
                return {"text": full_text, "output": full_text}

            with request.urlopen(req, timeout=timeout_s) as res:
                status_code = getattr(res, "status", None) or getattr(res, "code", None)
                raw = res.read().decode("utf-8")

            entry.requests_sent += 1
            entry.last_used_at = now
            entry.consecutive_transient_failures = 0
            duration_ms = round((time.perf_counter() - started) * 1000, 2)

            data_raw = json.loads(raw)
            if not isinstance(data_raw, dict):
                msg = f"unexpected_json_root: {type(data_raw).__name__}"
                _debug_log(f"router: {msg}")
                _log_llm_exception(req_id, entry, ep, model, task, attempt, status_code, duration_ms, msg)
                if error_detail is not None:
                    error_detail.append(msg)
                return None

            prov_msg = _provider_top_level_error(data_raw)
            if prov_msg:
                _debug_log(f"router: provider_error {prov_msg}")
                if has_active_job_sink():
                    append_current_job_event(
                        "api_call",
                        f"LLM provider returned an error: {task}.",
                        {
                            "phase": "llm_provider_error",
                            "request_id": req_id,
                            "provider": entry.provider,
                            "endpoint": _redact_url(ep),
                            "model": model,
                            "task": task,
                            "attempt": attempt,
                            "status_code": status_code,
                            "duration_ms": duration_ms,
                            "response_bytes": len(raw.encode("utf-8")),
                            "error": prov_msg,
                            "response_preview": _redacted_preview(data_raw),
                        },
                    )
                if error_detail is not None:
                    error_detail.append(f"provider_error: {prov_msg}")
                return None

            # BUILD-33 (non-streaming twin of the check above, line 647-672): a reasoning-
            # capable model can spend the whole completion budget on hidden chain-of-thought,
            # returning finish_reason "length" with empty message content — a real, paid-for
            # generation, not a dead provider. Every retry against the same entry hits the
            # identical shape, so this is _DeterministicRejection's case: fail fast instead of
            # burning the pool's other entries and more billed attempts on a request that can't
            # produce content this way.
            choices = data_raw.get("choices")
            first_choice = choices[0] if isinstance(choices, list) and choices else None
            finish_reason = first_choice.get("finish_reason") if isinstance(first_choice, dict) else None
            raw_message = first_choice.get("message") if isinstance(first_choice, dict) else None
            raw_content = raw_message.get("content") if isinstance(raw_message, dict) else None
            if isinstance(raw_content, list):
                has_content = any(
                    isinstance(part, dict) and part.get("type") == "text" and part.get("text")
                    for part in raw_content
                )
            else:
                has_content = bool(str(raw_content or "").strip())
            if not has_content and finish_reason == "length":
                msg = (
                    f"reasoning_only_no_content: finish_reason={finish_reason!r}, "
                    f"model={model}, provider={entry.provider}"
                )
                _debug_log(f"router: {msg}")
                _log_llm_exception(req_id, entry, ep, model, task, attempt, status_code, duration_ms, msg)
                if error_detail is not None:
                    error_detail.append(msg)
                raise _DeterministicRejection(msg)

            data = _normalize_openai_response(data_raw)
            _debug_log(f"router: response_ok req_id={req_id} provider={entry.provider}")
            if has_active_job_sink():
                append_current_job_event(
                    "api_call",
                    f"LLM request completed: {task}.",
                    {
                        "phase": "llm_request_done",
                        "request_id": req_id,
                        "provider": entry.provider,
                        "endpoint": _redact_url(ep),
                        "model": model,
                        "task": task,
                        "attempt": attempt,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                        "request_bytes": len(raw_body),
                        "response_bytes": len(raw.encode("utf-8")),
                        "response_preview": _redacted_preview(data_raw),
                        "normalized_preview": _redacted_preview(data),
                    },
                )
            return data if isinstance(data, dict) else None

        except error.HTTPError as exc:
            bod = _decode_http_error_body(exc)
            snippet = _safe_error_snippet(bod or str(exc.reason or exc))
            duration_ms = round((time.perf_counter() - started) * 1000, 2)

            # Handle 429 rate limit: cool this key for this modality. Honour the
            # provider's own Retry-After when present — a provider asking for 2s
            # shouldn't cost the pool the flat cooldown.
            if exc.code == 429:
                entry.requests_429 += 1
                retry_after = _parse_retry_after_secs(exc.headers)
                cooldown = retry_after if retry_after is not None else 30.0
                entry.cool(for_vision=is_vision_task, multimodal=is_multimodal_task, until=time.monotonic() + cooldown)
                msg = f"HTTPError 429 rate_limited (cooled {cooldown:g}s): {snippet}"
                _debug_log(f"router: {msg}")
                _log_llm_exception(req_id, entry, ep, model, task, attempt, exc.code, duration_ms, msg)
                if error_detail is not None:
                    error_detail.append(msg)
                return None

            # 401/403: quarantine the key rather than removing it. Removal had no
            # re-admission path — a transient auth glitch (WAF trip, regional
            # throttle) permanently shrank the pool toward empty until the process
            # restarted. Quarantine covers both modalities (an invalid key is
            # invalid for everything) and self-heals once it clears.
            if exc.code in {401, 403}:
                msg = f"HTTPError {exc.code} auth_failed (quarantined 300s): {snippet}"
                _debug_log(f"router: {msg}")
                _log_llm_exception(req_id, entry, ep, model, task, attempt, exc.code, duration_ms, msg)
                if error_detail is not None:
                    error_detail.append(msg)
                entry.quarantined_until = time.monotonic() + 300.0
                return None

            # Deterministic client errors (bad payload — context length, unsupported
            # response_format, malformed body) will be rejected by every provider
            # identically. Fail fast instead of cooling a healthy key and burning
            # the other two entries on a request that can't succeed anywhere.
            if exc.code in {400, 413, 422}:
                msg = f"HTTPError {exc.code} deterministic_rejection: {snippet}"
                _debug_log(f"router: {msg}")
                _log_llm_exception(req_id, entry, ep, model, task, attempt, exc.code, duration_ms, msg)
                if error_detail is not None:
                    error_detail.append(msg)
                raise _DeterministicRejection(msg) from exc

            # Other HTTP errors (5xx etc.): transient, flat cooldown and retry.
            cooldown = 10.0
            msg = f"HTTPError {exc.code}: {snippet}"
            entry.cool(for_vision=is_vision_task, multimodal=is_multimodal_task, until=time.monotonic() + cooldown)
            _debug_log(f"router: {msg} (cooled {cooldown:g}s)")
            _log_llm_exception(req_id, entry, ep, model, task, attempt, exc.code, duration_ms, msg)
            if error_detail is not None:
                error_detail.append(msg)
            return None

        except (error.URLError, TimeoutError, OSError) as exc:
            msg = f"{type(exc).__name__}: {exc}"
            entry.consecutive_transient_failures += 1
            cooldown = min(60.0, 5.0 * (3 ** (entry.consecutive_transient_failures - 1)))
            entry.cool(for_vision=is_vision_task, multimodal=is_multimodal_task, until=time.monotonic() + cooldown)
            _debug_log(f"router: transient_error (cooled {cooldown:g}s) {msg}")
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            _log_llm_exception(req_id, entry, ep, model, task, attempt, None, duration_ms, msg)
            if error_detail is not None:
                error_detail.append(msg)
            return None

        except (json.JSONDecodeError, ValueError) as exc:
            msg = f"{type(exc).__name__}: {exc}"
            entry.cool(for_vision=is_vision_task, multimodal=is_multimodal_task, until=time.monotonic() + 10.0)
            _debug_log(f"router: parse_error (cooled 10s) {msg}")
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            _log_llm_exception(req_id, entry, ep, model, task, attempt, None, duration_ms, msg)
            if error_detail is not None:
                error_detail.append(msg)
            return None

    def stats(self) -> dict[str, Any]:
        """Return pool statistics for compile reports."""
        return {
            "pool_size": len(self.pool),
            "entries": [
                {
                    "provider": entry.provider,
                    "endpoint": entry.endpoint,
                    "pool": entry.pool,
                    "requests_sent": entry.requests_sent,
                    "requests_429": entry.requests_429,
                    "cooled_text": entry.cooled_until_text > time.monotonic(),
                    "cooled_vision": entry.cooled_until_vision > time.monotonic(),
                    "cooled_multimodal": entry.cooled_until_multimodal > time.monotonic(),
                    "quarantined": entry.quarantined_until > time.monotonic(),
                }
                for entry in self.pool
            ],
        }


def _decode_http_error_body(exc: error.HTTPError) -> str:
    """Decode error response body from HTTP exception."""
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        # The provider's actual error text is the only diagnostic available when
        # a provider starts failing — losing it here means every failure in the
        # pool status/error_detail looks like a bare, unexplained HTTP code.
        logger.warning("llm_error_body_decode_failed", exc_info=True)
        return ""


# Global router instance
_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    """Get or initialize the global router."""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
