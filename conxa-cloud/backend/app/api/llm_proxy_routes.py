"""Metered LLM proxy for the Build Studio desktop app.

Build Studio runs the compiler locally but has no LLM keys. It forwards every
text/vision LLM call here; the cloud holds the provider pool, enforces a
per-org monthly token quota, and records usage for billing/analytics.

Auth: inherits Clerk JWT verification from ProductionRequestMiddleware. These
routes additionally require the ``X-Conxa-Client`` header (the proxy is called
by the desktop backend, never a browser) and reject browsers via that header
rather than CORS.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from conxa_core.config import settings
from app.api.deps import current_principal, entitlement_http_error
from app.api.machine_binding import register_request_machine
from app.llm.router import get_router
from app.services import llm_metering
from app.services.byok import byok_pool_entry_for
from app.services.entitlements import (
    ALLOWED_USAGE_CLASSES,
    EntitlementError,
    compile_pool_for,
    current_entitlements,
    ensure_execute_pool_available,
    ensure_ai_credits_available,
    ensure_trial_active,
    execute_chat_access_for,
    record_execute_pool_usage,
    record_llm_usage,
)

router = APIRouter(prefix="/llm/proxy", tags=["llm-proxy"], include_in_schema=False)
logger = logging.getLogger(__name__)

_STREAM_DONE = object()

# One workspace's compile burst used to be able to drain the shared provider pool
# (every entry cooled/quarantined) into 502s for every other tenant. Caps
# concurrent in-flight LLM calls per workspace; over the cap gets a 429 with
# Retry-After — cheap to hold since it's just a counter, not a real semaphore
# blocking a thread.
_inflight_lock = threading.Lock()
_inflight_by_workspace: dict[str, int] = {}


class _TooManyInFlight(Exception):
    pass


def _acquire_workspace_slot(workspace_id: str) -> None:
    limit = settings.llm_proxy_max_concurrent_per_workspace
    if limit <= 0:
        return
    with _inflight_lock:
        current = _inflight_by_workspace.get(workspace_id, 0)
        if current >= limit:
            raise _TooManyInFlight()
        _inflight_by_workspace[workspace_id] = current + 1


def _release_workspace_slot(workspace_id: str) -> None:
    with _inflight_lock:
        current = _inflight_by_workspace.get(workspace_id, 0)
        if current <= 1:
            _inflight_by_workspace.pop(workspace_id, None)
        else:
            _inflight_by_workspace[workspace_id] = current - 1


class ProxyBody(BaseModel):
    task: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=30_000, ge=1_000, le=120_000)
    usage_class: str = Field(default="compile", max_length=32)
    # execute_chat only: which of the caller's several eligible Execute
    # contexts (personal / a team they're a member of / a team that granted
    # them a seat) this turn should bill against — see the personal/team
    # switcher, GET /api/v1/execute/contexts. Verified server-side via
    # execute_chat_access_for, never trusted from the client alone.
    target_workspace_id: str | None = Field(default=None, max_length=256)


_EXECUTE_CLIENT_HEADER = "conxa-execute"


def _require_known_client(request: Request) -> str:
    """Returns the caller's X-Conxa-Client value once it's confirmed to be one
    of the two first-party callers this proxy is meant for — never a browser.
    Callers branch on the return value (e.g. to skip machine-slot
    registration for Execute, whose cap is execute_seats, not machines)."""
    expected = settings.llm_proxy_client_header.strip()
    got = request.headers.get("x-conxa-client", "").strip()
    if got == _EXECUTE_CLIENT_HEADER:
        return got
    if expected and got == expected:
        return got
    raise HTTPException(status_code=403, detail="proxy_requires_known_client")


def _resolve_execute_chat_workspace(principal, body: ProxyBody) -> str:
    """execute_chat bills against the workspace the caller explicitly chose
    in Execute's context switcher — falling back to the token's own
    workspace_id when none was given — never a Principal-derived default
    the caller didn't ask for. Raises 403 if the caller doesn't actually
    have Execute access to the requested workspace."""
    target = (body.target_workspace_id or "").strip() or principal.workspace_id
    if target == principal.workspace_id:
        return target  # the caller's own token-resolved workspace — always allowed, no lookup needed
    if not execute_chat_access_for(principal.user_id, target):
        raise HTTPException(status_code=403, detail="execute_context_not_available")
    return target


def _meter_and_call(request: Request, body: ProxyBody, *, vision: bool) -> dict[str, Any]:
    client = _require_known_client(request)
    principal = current_principal(request)
    usage_class = str(body.usage_class or "compile").strip()
    if usage_class not in ALLOWED_USAGE_CLASSES:
        raise HTTPException(status_code=400, detail="invalid_usage_class")
    # execute_chat bills whichever context the caller's switcher picked —
    # never the token's own workspace_id by default the way every other
    # usage_class does.
    org_id = _resolve_execute_chat_workspace(principal, body) if usage_class == "execute_chat" else principal.workspace_id

    try:
        ensure_trial_active(principal)
        if client != _EXECUTE_CLIENT_HEADER:
            # Execute grantees are capped by execute_seats, not the machines
            # meter — registering one here would consume a machine slot that
            # belongs to a real installed Build Studio/runtime device.
            register_request_machine(request, principal)
    except EntitlementError as exc:
        raise entitlement_http_error(exc) from exc

    if usage_class == "compile" and llm_metering.quota_exceeded(org_id, settings.llm_proxy_monthly_token_quota):
        raise HTTPException(status_code=429, detail="quota_exceeded")

    input_tokens = llm_metering.estimate_request_tokens(body.payload)
    if usage_class == "execute_chat":
        try:
            ensure_execute_pool_available(org_id, estimated_tokens=input_tokens)
        except EntitlementError as exc:
            raise entitlement_http_error(exc) from exc
    elif usage_class in ("ai_credits", "human_edit"):
        try:
            ensure_ai_credits_available(principal, estimated_tokens=input_tokens, usage_class=usage_class)
        except EntitlementError as exc:
            raise entitlement_http_error(exc) from exc

    try:
        _acquire_workspace_slot(org_id)
    except _TooManyInFlight:
        raise HTTPException(
            status_code=429,
            detail="workspace_concurrency_limit",
            headers={"Retry-After": "3"},
        ) from None

    router_impl = get_router()
    error_detail: list[str] = []
    pool = compile_pool_for(principal)
    try:
        byok_entry = byok_pool_entry_for(principal)
        if byok_entry is not None:
            # Enterprise BYOK: exactly one deployment, no pool selection.
            result = router_impl.call_entry_directly(
                byok_entry, body.task, body.payload, body.timeout_ms, error_detail=error_detail
            )
        elif vision:
            result = router_impl.route_vision(
                body.task, body.payload, body.timeout_ms,
                error_detail=error_detail, pool=pool,
            )
        else:
            result = router_impl.route_text(
                body.task, body.payload, body.timeout_ms,
                error_detail=error_detail, pool=pool,
            )
    except RuntimeError as exc:
        # No providers configured — treat as upstream unavailable. Log the real
        # reason server-side; the client gets the bare code (see app/api/errors.py).
        logger.error("llm_unavailable org_id=%s error=%s", org_id, exc)
        raise HTTPException(status_code=502, detail="llm_unavailable") from exc
    finally:
        _release_workspace_slot(org_id)

    if result is None:
        raise HTTPException(
            status_code=502,
            detail={"message": "llm_all_providers_failed", "error_detail": error_detail[:8]},
        )

    output_tokens = llm_metering.estimate_response_tokens(result)
    llm_metering.record_usage(org_id, input_tokens=input_tokens, output_tokens=output_tokens)
    try:
        if usage_class == "execute_chat":
            record_execute_pool_usage(org_id, input_tokens=input_tokens, output_tokens=output_tokens)
        else:
            record_llm_usage(
                principal,
                usage_class=usage_class,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc
    return result


@router.post("/text")
def proxy_text(body: ProxyBody, request: Request) -> dict[str, Any]:
    return _meter_and_call(request, body, vision=False)


@router.post("/vision")
def proxy_vision(body: ProxyBody, request: Request) -> dict[str, Any]:
    return _meter_and_call(request, body, vision=True)


def _meter_and_stream(request: Request, body: ProxyBody, *, vision: bool) -> StreamingResponse:
    """Streaming sibling of _meter_and_call (BUILD-26 stage c) — same entitlement/quota/
    concurrency gates up front, but the actual LLM call runs on a background thread so its
    `on_delta` callback can push chunks into a queue this function drains into an SSE-ish
    response (`data: {"delta": ...}` per chunk, `data: {"done": true, "text": ...}` at the end).
    Usage is metered once, after the full text is known — never per chunk. Only text/vision
    tasks that ask for `on_delta` (currently just `copilot_reply`) should hit this path; every
    other task keeps using the blocking endpoints above."""
    client = _require_known_client(request)
    principal = current_principal(request)
    usage_class = str(body.usage_class or "compile").strip()
    if usage_class not in ALLOWED_USAGE_CLASSES:
        raise HTTPException(status_code=400, detail="invalid_usage_class")
    org_id = _resolve_execute_chat_workspace(principal, body) if usage_class == "execute_chat" else principal.workspace_id

    try:
        ensure_trial_active(principal)
        if client != _EXECUTE_CLIENT_HEADER:
            register_request_machine(request, principal)
    except EntitlementError as exc:
        raise entitlement_http_error(exc) from exc

    if usage_class == "compile" and llm_metering.quota_exceeded(org_id, settings.llm_proxy_monthly_token_quota):
        raise HTTPException(status_code=429, detail="quota_exceeded")

    input_tokens = llm_metering.estimate_request_tokens(body.payload)
    if usage_class == "execute_chat":
        try:
            ensure_execute_pool_available(org_id, estimated_tokens=input_tokens)
        except EntitlementError as exc:
            raise entitlement_http_error(exc) from exc
    elif usage_class in ("ai_credits", "human_edit"):
        try:
            ensure_ai_credits_available(principal, estimated_tokens=input_tokens, usage_class=usage_class)
        except EntitlementError as exc:
            raise entitlement_http_error(exc) from exc

    try:
        _acquire_workspace_slot(org_id)
    except _TooManyInFlight:
        raise HTTPException(
            status_code=429,
            detail="workspace_concurrency_limit",
            headers={"Retry-After": "3"},
        ) from None

    router_impl = get_router()
    error_detail: list[str] = []
    chunks: "queue.Queue[Any]" = queue.Queue()
    pool = compile_pool_for(principal)

    def _worker() -> None:
        try:
            byok_entry = byok_pool_entry_for(principal)
            if byok_entry is not None:
                # BYOK has no streaming call path (call_entry_directly has no on_delta) — fall
                # back to one blocking call and deliver it as a single chunk, so a BYOK tenant
                # still gets a valid (if non-incremental) reply instead of an error.
                result = router_impl.call_entry_directly(
                    byok_entry, body.task, body.payload, body.timeout_ms, error_detail=error_detail
                )
                text = str(
                    (result or {}).get("text")
                    or (result or {}).get("output")
                    or (result or {}).get("reply")
                    or ""
                )
                if text:
                    chunks.put(text)
            elif vision:
                router_impl.route_vision(
                    body.task, body.payload, body.timeout_ms,
                    error_detail=error_detail, pool=pool, on_delta=chunks.put,
                )
            else:
                router_impl.route_text(
                    body.task, body.payload, body.timeout_ms,
                    error_detail=error_detail, pool=pool, on_delta=chunks.put,
                )
        except Exception as exc:  # noqa: BLE001 — surfaced to the generator below, never crashes silently
            chunks.put(exc)
        finally:
            chunks.put(_STREAM_DONE)

    threading.Thread(target=_worker, daemon=True).start()

    is_tool_calling_task = body.task == "execute_chat"

    def _generate():
        full_text_parts: list[str] = []
        saw_tool_call = False
        worker_error: Exception | None = None
        try:
            while True:
                item = chunks.get()
                if item is _STREAM_DONE:
                    break
                if isinstance(item, Exception):
                    worker_error = item
                    break
                if is_tool_calling_task:
                    # item is {"type": "text"|"tool_call", ...} — forwarded as-is
                    # so the caller (Execute's turn loop) can tell tool-call
                    # fragments apart from prose; only text counts toward the
                    # final full_text/usage estimate below.
                    if item.get("type") == "text":
                        full_text_parts.append(item["text"])
                    elif item.get("type") == "tool_call":
                        saw_tool_call = True
                    yield f"data: {json.dumps(item)}\n\n"
                else:
                    full_text_parts.append(item)
                    yield f"data: {json.dumps({'delta': item})}\n\n"

            full_text = "".join(full_text_parts)
            if not full_text and not saw_tool_call:
                # BUILD-33: distinguish "every provider genuinely failed" from "a reasoning
                # model spent its whole budget on hidden chain-of-thought and wrote nothing" —
                # the router already labels the latter in error_detail before giving up.
                # (A tool-call-only turn with no prose is a normal execute_chat outcome,
                # not this failure — saw_tool_call above excludes it.)
                if worker_error:
                    message = str(worker_error)
                elif any(line.startswith("reasoning_only_no_content") for line in error_detail):
                    message = "llm_reasoning_only_no_content"
                else:
                    message = "llm_all_providers_failed"
                # The blocking sibling (_meter_and_call) ships error_detail to the caller;
                # this streaming path used to just drop it — a provider outage here left
                # nothing in the logs and nothing on the wire, only the bare code. Log it
                # server-side and send it along (same [:8] cap as the blocking path) so the
                # real reason survives.
                logger.warning(
                    "llm_stream_failed org_id=%s task=%s message=%s error_detail=%s",
                    org_id, body.task, message, error_detail[:8],
                )
                yield f"data: {json.dumps({'error': message, 'error_detail': error_detail[:8]})}\n\n"
                return

            output_tokens = llm_metering.estimate_response_tokens({"text": full_text})
            llm_metering.record_usage(org_id, input_tokens=input_tokens, output_tokens=output_tokens)
            try:
                if usage_class == "execute_chat":
                    record_execute_pool_usage(org_id, input_tokens=input_tokens, output_tokens=output_tokens)
                else:
                    record_llm_usage(
                        principal, usage_class=usage_class,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                    )
            except Exception:  # noqa: BLE001 — the reply already streamed to the client; headers
                # are long committed by this point, so an entitlement-recording failure here can
                # only be logged, never turned into an HTTP error the way _meter_and_call does.
                # workspace_id + both token counts are logged so the lost usage
                # is reconstructable from logs rather than just "it failed".
                logger.exception(
                    "record_llm_usage_failed_after_stream org_id=%s workspace_id=%s usage_class=%s "
                    "input_tokens=%d output_tokens=%d",
                    org_id, principal.workspace_id, usage_class, input_tokens, output_tokens,
                )
            yield f"data: {json.dumps({'done': True, 'text': full_text})}\n\n"
        finally:
            _release_workspace_slot(org_id)

    return StreamingResponse(_generate(), media_type="text/event-stream")


@router.post("/text/stream")
def proxy_text_stream(body: ProxyBody, request: Request) -> StreamingResponse:
    return _meter_and_stream(request, body, vision=False)


@router.post("/vision/stream")
def proxy_vision_stream(body: ProxyBody, request: Request) -> StreamingResponse:
    return _meter_and_stream(request, body, vision=True)


@router.get("/usage")
def proxy_usage(request: Request) -> dict[str, Any]:
    """Current-month usage for the calling org (Build Studio shows this in Settings)."""
    _require_known_client(request)
    principal = current_principal(request)
    org_id = principal.workspace_id
    usage = llm_metering.get_usage(org_id)
    try:
        entitlements = current_entitlements(principal)
    except Exception:
        entitlements = None
    return {"org_id": org_id, "usage": usage, "quota": settings.llm_proxy_monthly_token_quota, "entitlements": entitlements}
