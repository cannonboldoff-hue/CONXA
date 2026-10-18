"""One error response shape for every route, with a human-readable message and a
request id every failure can be traced to a log line by.

Additive, not a replacement: ``detail`` keeps carrying exactly what each
``raise HTTPException(..., detail=...)`` call site already sent — both a bare
string code and the one dict-valued ``llm_all_providers_failed`` body (see
llm_proxy_routes.py) — because it is a real wire contract two shipped clients
parse directly: the frontend's ``apiBase.ts`` and Build Studio's
``services/llm_proxy_client.py`` (LLMProxyClient._post reads
``error_body.get("detail")`` and does string/dict inspection on it). Neither
gets touched by this change. ``message`` and ``request_id`` are new, additive
fields a client may use instead.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Machine code -> one human sentence. An unmapped code falls through to the
# code itself (snake_case, readable enough, never a crash) — the gap is then
# visible in the response instead of silently swallowed, so it's easy to spot
# and add here.
ERROR_MESSAGES: dict[str, str] = {
    "admin_role_required": "You need admin access in this workspace to do that.",
    "seat_limit_exceeded": "This workspace is at its seat limit. Free up a seat or upgrade your plan to add another person.",
    "machine_limit_exceeded": "This workspace has reached its device limit. Revoke an old device or upgrade your plan.",
    "compile_credit_limit_exceeded": "You're out of compile credits for this billing period. They reset next period, or you can add more.",
    "human_edit_pool_exceeded": "You're out of AI credits for this billing period.",
    "ai_credits_exceeded": "You're out of AI credits for this billing period.",
    "execute_seat_limit_exceeded": "This workspace has no more Execute seats available.",
    "trial_expired": "Your trial has ended. Upgrade to keep going.",
    "entitlements_unavailable": "We couldn't check your plan limits just now. Try again in a moment.",
    "invalid_usage_class": "That request wasn't recognized. Try again, or contact support if it keeps happening.",
    "invalid_slug": "That name isn't valid — use only letters, numbers, hyphens and underscores.",
    "invalid_file_path": "One of the uploaded file paths isn't valid.",
    "unknown_job_id": "We couldn't find that job. It may have already finished or been removed.",
    "invalid_or_expired_download_link": "This download link has expired. Generate a new one.",
    "unsupported_installer_version": "That installer generation isn't supported.",
    "installer_not_published": "No installer has been uploaded for this yet.",
    "installer_version_exists": "That version number has already been uploaded.",
    "pack_json_corrupted": "This workspace's skill pack data is corrupted and needs attention. Contact support.",
    "workspace_concurrency_limit": "Another request from this workspace is still running. Try again in a few seconds.",
    "llm_all_providers_failed": "Every AI provider we tried is unavailable right now. Try again shortly.",
    "llm_unavailable": "The AI service is unavailable right now. Try again shortly.",
    "cashfree_plan_create_failed": "We couldn't set up billing for that plan right now. Try again, or contact support.",
    "cashfree_addon_verify_failed": "We couldn't verify that payment right now. If you were charged, it will be credited automatically shortly — contact support if it isn't within a few minutes.",
    "quota_exceeded": "The monthly usage limit for this workspace has been reached.",
    "legal_version_mismatch": "Our terms have been updated since you last accepted them. Please review and accept the new version.",
    "policy_invalid": "That policy setting isn't valid.",
    "api_origin_not_configured": "The service isn't configured correctly. Contact support.",
    "backend_unavailable": "We couldn't reach the server. Check your connection and try again.",
    "clerk_auth_not_configured": "Sign-in isn't configured correctly. Contact support.",
    "pyjwt_dependency_missing": "Sign-in isn't configured correctly. Contact support.",
    "invalid_clerk_token": "Your session has expired. Please sign in again.",
    "invalid_authorized_party": "Your session isn't valid for this application. Please sign in again.",
    "missing_bearer_token": "You need to be signed in to do that.",
    "request_body_too_large": "That upload is too large.",
    "credits_unavailable": "We couldn't check your remaining credits just now.",
    "customer_email_required": "We need an email address on file to set up billing. Add one to your account and try again.",
    "webhook_billing_update_failed": "We received your payment but couldn't update your plan yet. This will retry automatically.",
    "webhook_cancel_failed": "We couldn't process your subscription cancellation yet. This will retry automatically.",
    "webhook_grant_failed": "We received your payment but couldn't grant your credits yet. This will retry automatically.",
}


def message_for(code: str) -> str:
    return ERROR_MESSAGES.get(code, code)


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid if isinstance(rid, str) and rid else "unknown"


def _human_message(detail: object) -> str:
    if isinstance(detail, str):
        return message_for(detail)
    if isinstance(detail, dict):
        # The one dict-detail shape in use (llm_all_providers_failed) carries its
        # own code under "message" — reuse it rather than stringifying the dict.
        inner = detail.get("message")
        if isinstance(inner, str):
            return message_for(inner)
    return "Something went wrong. Please try again."


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    rid = _request_id(request)
    body = {
        "detail": exc.detail,
        "message": _human_message(exc.detail),
        "request_id": rid,
    }
    headers = dict(exc.headers) if exc.headers else {}
    headers["x-request-id"] = rid
    return JSONResponse(body, status_code=exc.status_code, headers=headers)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    rid = _request_id(request)
    logger.error(
        "unhandled_error path=%s method=%s request_id=%s",
        request.url.path,
        request.method,
        rid,
        exc_info=exc,
    )
    body = {
        "detail": "internal_error",
        "message": "Something went wrong on our side. Quote this reference if you contact support.",
        "request_id": rid,
    }
    return JSONResponse(body, status_code=500, headers={"x-request-id": rid})
