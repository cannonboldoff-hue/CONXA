"""Cloud LLM proxy client used by the local compiler.

Build Studio has no provider keys; it forwards every text/vision LLM call to
the cloud ``/llm/proxy/*`` endpoints with the Clerk JWT. This object exposes the
same ``route_text`` / ``route_vision`` signature as ``app.llm.router.LLMRouter``
so it can be injected wherever the compiler expects a router.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from services.machine_id import get_machine_id_hash

# Minimum HTTP timeout for proxied calls (double-hop: Studio → cloud → LLM provider).
# The per-task timeout_ms (e.g. llm_text_timeout_ms=2000) was designed for direct
# LLM endpoints; proxied calls need a much larger budget.
_PROXY_MIN_TIMEOUT_S = 90.0

# 502/503/504 mean the cloud router exhausted its own pool for this one attempt —
# possibly transient (a cooldown clearing seconds later). Two retries with backoff
# catch that without turning a real outage into a long hang: this is a client-side
# retry ON TOP OF the router's own internal failover, not instead of it.
_RETRYABLE_HTTP_CODES = {502, 503, 504}
_RETRY_BACKOFFS_S = (1.0, 4.0)

# Short text tasks a person is waiting on, or that sit in front of a screen: they get the caller's
# own timeout (plus a little slack) as a hard deadline across every attempt and backoff, instead of
# the 90 s floor above (BUILD-55 F04). Everything else keeps the long budget, because a compile's
# own text calls are calibrated for it.
_DEADLINE_SLACK_S = 5.0
_INTERACTIVE_TEXT_TASKS = frozenset({
    "workflow_understanding", "clarify_question_copy", "fork_question_copy", "chat_map_answer",
    "chat_route_intent", "studio_chat_actions", "thread_summarize", "record_plan",
})


def _has_time(deadline: float | None, wait_s: float) -> bool:
    """True when a wait of ``wait_s`` still leaves room before ``deadline`` (None = no deadline)."""
    return deadline is None or time.monotonic() + wait_s < deadline


def _parse_retry_after_secs(headers: Any) -> float | None:
    """Parse a numeric Retry-After header into seconds, or None if absent/invalid.
    Mirrors app.llm.router's version — duplicated rather than imported since the
    Studio process can't depend on the cloud backend package."""
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


class QuotaExceeded(RuntimeError):
    """The org hit its monthly LLM token quota (HTTP 429)."""


class EntitlementBlocked(RuntimeError):
    """The cloud entitlement service blocked this LLM request."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CloudUnreachable(RuntimeError):
    """The proxy could not be reached (network error / no internet)."""


class ProxyUnavailable(CloudUnreachable):
    """The proxy answered but every provider failed (502 llm_all_providers_failed),
    even after this client's own retries. A CloudUnreachable subclass so every
    existing `except CloudUnreachable` call site (build.py, pipeline/run.py,
    handlers/compile.py) already handles it correctly — this is a genuine
    infrastructure failure, not a call site returning an empty answer.

    Deliberately distinct from a plain ``None`` return: before this, a 502 and a
    real "the model answered nothing" were indistinguishable, so callers like
    intent_llm.py couldn't tell "retry me" from "give up gracefully" apart, and
    the compile's semantic cache silently filled with rule_fallback entries with
    no visible signal that the LLM had stopped answering at all.
    """

    def __init__(self, message: str, error_detail: list[str] | None = None) -> None:
        super().__init__(message)
        self.error_detail = error_detail or []


class LLMProxyClient:
    def __init__(
        self,
        cloud_api: str,
        token_provider: Callable[[], str],
        *,
        client_header: str = "build-studio",
        usage_class: str = "compile",
        on_api_call: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._cloud_api = cloud_api.rstrip("/")
        self._token_provider = token_provider
        self._client_header = client_header
        self._usage_class = usage_class
        self._on_api_call = on_api_call

    # -- public interface mirroring LLMRouter --------------------------------

    def route_text(
        self,
        task: str,
        payload: dict[str, Any],
        timeout_ms: int,
        *,
        error_detail: list[str] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any] | None:
        return self._post("text", task, payload, timeout_ms, error_detail=error_detail, on_delta=on_delta)

    def route_vision(
        self,
        task: str,
        payload: dict[str, Any],
        timeout_ms: int,
        *,
        error_detail: list[str] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any] | None:
        return self._post("vision", task, payload, timeout_ms, error_detail=error_detail, on_delta=on_delta)

    # -- internals -----------------------------------------------------------

    def _post(
        self,
        kind: str,
        task: str,
        payload: dict[str, Any],
        timeout_ms: int,
        *,
        error_detail: list[str] | None,
        _retried: bool = False,
        _retry_count: int = 0,
        on_delta: Callable[[str], None] | None = None,
        _deadline: float | None = None,
    ) -> dict[str, Any] | None:
        streaming = on_delta is not None
        if _deadline is None and kind == "text" and not streaming and task in _INTERACTIVE_TEXT_TASKS:
            _deadline = time.monotonic() + timeout_ms / 1000 + _DEADLINE_SLACK_S
        url = f"{self._cloud_api}/api/v1/llm/proxy/{kind}{'/stream' if streaming else ''}"
        body = json.dumps(
            {
                "task": task,
                "payload": payload,
                "timeout_ms": int(timeout_ms),
                "usage_class": self._usage_class,
            }
        ).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Conxa-Client", self._client_header)
        req.add_header("Authorization", f"Bearer {self._token_provider()}")
        machine_hash = get_machine_id_hash()
        if machine_hash:
            req.add_header("X-Conxa-Machine", machine_hash)

        # Use a minimum 90s budget for proxied calls; the caller's timeout_ms is
        # calibrated for direct LLM endpoints, not a double-hop proxy.
        if _deadline is None:
            http_timeout_s = max(timeout_ms / 1000, _PROXY_MIN_TIMEOUT_S) + 5.0
        else:
            http_timeout_s = max(1.0, _deadline - time.monotonic())
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=http_timeout_s) as resp:
                if not streaming:
                    data = json.loads(resp.read().decode("utf-8"))
                    duration_ms = int((time.monotonic() - t0) * 1000)
                    if self._on_api_call is not None:
                        self._on_api_call({"task": task, "kind": kind, "duration_ms": duration_ms, "status": "ok"})
                    return data if isinstance(data, dict) else None

                # Streaming: consume the proxy's own `data: {...}` delta framing (llm_proxy_routes.py's
                # /stream endpoints) — not raw provider SSE, which the cloud already normalized away.
                full_text_parts: list[str] = []
                saw_error: str | None = None
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        frame = json.loads(line[len("data:") :].strip())
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(frame, dict):
                        continue
                    if "delta" in frame:
                        chunk = str(frame["delta"])
                        full_text_parts.append(chunk)
                        on_delta(chunk)
                    elif frame.get("done"):
                        text = str(frame.get("text") or "".join(full_text_parts))
                        duration_ms = int((time.monotonic() - t0) * 1000)
                        if self._on_api_call is not None:
                            self._on_api_call({"task": task, "kind": kind, "duration_ms": duration_ms, "status": "ok"})
                        return {"text": text, "output": text}
                    elif frame.get("error"):
                        saw_error = str(frame["error"])

                # Stream ended without a "done" frame — still show whatever text arrived rather
                # than discard it, since the reviewer may already be reading it as it streamed.
                duration_ms = int((time.monotonic() - t0) * 1000)
                full_text = "".join(full_text_parts)
                if full_text:
                    if self._on_api_call is not None:
                        self._on_api_call({"task": task, "kind": kind, "duration_ms": duration_ms, "status": "ok"})
                    return {"text": full_text, "output": full_text}
                if self._on_api_call is not None:
                    self._on_api_call({"task": task, "kind": kind, "duration_ms": duration_ms, "status": "error"})
                raise ProxyUnavailable(
                    f"Cloud LLM proxy stream ended without a result ({saw_error or 'no provider produced output'})",
                    error_detail=[saw_error] if saw_error else [],
                )
        except urllib.error.HTTPError as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            if self._on_api_call is not None:
                self._on_api_call({"task": task, "kind": kind, "duration_ms": duration_ms, "status": f"http_{exc.code}"})
            if exc.code == 401 and not _retried:
                # Token likely expired — let the auth layer refresh, then retry once.
                return self._post(
                    kind, task, payload, timeout_ms,
                    error_detail=error_detail, _retried=True, on_delta=on_delta, _deadline=_deadline,
                )
            detail: Any = ""
            try:
                error_body = json.loads(exc.read().decode("utf-8"))
                detail = error_body.get("detail")
            except Exception:
                detail = ""
            detail_str = detail if isinstance(detail, str) else ""

            if exc.code == 429 and detail_str == "workspace_concurrency_limit":
                # A different tenant/compile is using this workspace's concurrency
                # slots right now — not a quota problem. Retry with backoff like a
                # 502, honouring Retry-After (llm_proxy_routes.py sends 3s), instead
                # of surfacing a misleading "quota reached" to the user.
                if _retry_count < len(_RETRY_BACKOFFS_S):
                    retry_after = _parse_retry_after_secs(exc.headers)
                    base = retry_after if retry_after is not None else _RETRY_BACKOFFS_S[_retry_count]
                    wait_s = base + random.uniform(0, 0.5)
                    if _has_time(_deadline, wait_s):
                        time.sleep(wait_s)
                        return self._post(
                            kind, task, payload, timeout_ms,
                            error_detail=error_detail, _retried=_retried, _retry_count=_retry_count + 1,
                            on_delta=on_delta, _deadline=_deadline,
                        )
                raise ProxyUnavailable(
                    "Cloud LLM proxy workspace concurrency limit exceeded after retries",
                    error_detail=[f"proxy HTTP 429: {detail_str}"],
                ) from exc
            if exc.code == 429:
                raise QuotaExceeded("Monthly LLM quota reached") from exc
            if detail_str == "invalid_usage_class" and self._usage_class == "ai_credits":
                # An older cloud that predates CLOUD-25 doesn't know "ai_credits" yet. Fall back
                # to its legacy name once; the choice sticks for this client's lifetime.
                self._usage_class = "human_edit"
                return self._post(
                    kind, task, payload, timeout_ms,
                    error_detail=error_detail, _retried=_retried, _retry_count=_retry_count, on_delta=on_delta,
                    _deadline=_deadline,
                )
            if detail_str in {
                "compile_credit_limit_exceeded",
                "human_edit_pool_exceeded",
                "ai_credits_exceeded",
                "machine_limit_exceeded",
                "execute_seat_limit_exceeded",
                "trial_expired",
                "entitlements_unavailable",
                "invalid_usage_class",
            }:
                raise EntitlementBlocked(detail_str) from exc
            collected = error_detail if error_detail is not None else []
            # 502 llm_all_providers_failed carries {"message": ..., "error_detail": [...]}
            # (llm_proxy_routes.py) — surface the provider's real failure reason instead
            # of just the HTTP status, so a 429 doesn't look identical to every other 502.
            if isinstance(detail, dict):
                message = str(detail.get("message") or "")
                collected.append(f"proxy HTTP {exc.code}: {message}" if message else f"proxy HTTP {exc.code}")
                nested = detail.get("error_detail")
                if isinstance(nested, list):
                    collected.extend(str(item) for item in nested)
            else:
                collected.append(f"proxy HTTP {exc.code}")

            retry_wait = (
                (_parse_retry_after_secs(exc.headers) or _RETRY_BACKOFFS_S[_retry_count])
                if _retry_count < len(_RETRY_BACKOFFS_S) else None
            )
            if exc.code in _RETRYABLE_HTTP_CODES and retry_wait is not None and _has_time(_deadline, retry_wait + 0.5):
                # A 502/503/504 here means the cloud's own router exhausted its
                # pool for this one attempt — possibly a moment-in-time thing, a
                # cooldown that clears seconds later. Retry with backoff instead
                # of the old bare `return None`, which every caller (intent_llm's
                # `continue`, etc.) used to re-fire immediately with no pause,
                # tripling load right when the pool was already drained.
                retry_after = _parse_retry_after_secs(exc.headers)
                base = retry_after if retry_after is not None else _RETRY_BACKOFFS_S[_retry_count]
                wait_s = base + random.uniform(0, 0.5)
                time.sleep(wait_s)
                return self._post(
                    kind, task, payload, timeout_ms,
                    error_detail=error_detail, _retried=_retried, _retry_count=_retry_count + 1,
                    on_delta=on_delta, _deadline=_deadline,
                )

            if exc.code in _RETRYABLE_HTTP_CODES:
                raise ProxyUnavailable(
                    f"Cloud LLM proxy unavailable after {_retry_count} retries (HTTP {exc.code})",
                    error_detail=collected,
                ) from exc
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # urllib wraps connect/header timeouts in URLError; Windows raises the
            # body-read timeout as a raw TimeoutError/OSError ("The read operation
            # timed out"). Both cases mean the proxy is unreachable or too slow.
            duration_ms = int((time.monotonic() - t0) * 1000)
            if self._on_api_call is not None:
                self._on_api_call({"task": task, "kind": kind, "duration_ms": duration_ms, "status": "error"})
            raise CloudUnreachable(
                f"Cloud LLM proxy unreachable or timed out ({exc})"
            ) from exc
