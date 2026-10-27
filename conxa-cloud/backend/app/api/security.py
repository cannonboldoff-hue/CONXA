"""Production-facing request middleware: request IDs, body caps, and Clerk auth."""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from conxa_core.config import settings

from app.api.errors import message_for

PUBLIC_PATHS = {
    "/",
    "/health",
    "/healthz",
    # Render's deploy gate hits this directly (see Dockerfile) — it must be
    # reachable with no auth or every deploy would sit "unhealthy" against a
    # 401 that has nothing to do with DB readiness.
    "/readyz",
    "/api/v1/health",
    # Runtime phonehome — installed runtimes have no Clerk session, only sync tokens.
    # This stores best-effort device registration; spoofing just inflates counts.
    "/api/v1/telemetry/runtime-start",
    # Signed, unauthenticated self-update manifest polled by every installed
    # runtime (runtime/manifest_manager.js) — no Clerk session exists at that point.
    "/api/v1/manifest.json",
}

# Runtime telemetry ingestion uses its own package token; tracking reads stay behind Clerk.
PUBLIC_TRACKING_EVENT_PREFIXES = ("/api/tracking/", "/api/v1/tracking/")

# Installed runtimes read data-only skill-pack deltas during startup before any
# dashboard Clerk session exists. Event ingestion remains package-token guarded.
PUBLIC_SKILL_PACK_SYNC_PREFIXES = ("/api/v1/skill-packs/",)

# Versioned equivalents nested under /api/v1/workflows/{installer_version}/{company}/...
# (see skillpack_update_routes.versioned_router / tracking_routes.versioned_router).
# Matched by suffix rather than a blanket "/api/v1/workflows/" prefix because that
# same path segment also hosts workflow_routes.py's Clerk-protected dashboard
# endpoints (list/create/delete workflows) — only these two specific, package-token
# guarded sub-paths are exempt from the Clerk gate.
PUBLIC_VERSIONED_WORKFLOW_SUFFIXES_GET = ("/skill-packs/delta",)
# "/artifacts" is the per-skill recovery-artifact fetch (skillpack_update_routes.
# get_skill_artifacts) — sync-token guarded like the delta beside it. No Clerk-protected
# dashboard route under /api/v1/workflows/ ends in this segment, which is what keeps the
# suffix match from widening the exemption past the one route it is meant for.
PUBLIC_VERSIONED_WORKFLOW_SUFFIXES_POST = ("/tracking/events", "/artifacts")

# Installer downloads are fetched by end users who have no Clerk account; the
# company slug in the path is the only credential and the file is non-sensitive.
PUBLIC_PATH_PREFIXES = (
    "/api/v1/installers/",
    "/api/v1/updates/",
)

BUILD_ARTIFACT_UPLOAD_PATHS = (
    "/api/v1/workflows/publish",
    "/installer/upload",
    "/skill-packs/upload",
)

# The dashboard's "Report a bug" page posts straight to the backend (bypassing the
# Vercel proxy's ~4.5MB request cap) with base64 attachments up to
# settings.bug_report_max_bytes (25MB raw); base64 adds ~33% overhead, so give it
# headroom over the default JSON cap without reusing the much larger build-artifact one.
BUG_REPORT_UPLOAD_PATH = "/api/v1/bug-reports"
BUG_REPORT_UPLOAD_MAX_BYTES = 35 * 1024 * 1024


def _request_id(request: Request) -> str:
    rid = request.headers.get("x-request-id", "").strip()
    return rid[:128] if rid else secrets.token_hex(12)


def _is_public_path(path: str, method: str = "GET") -> bool:
    normalized = path.rstrip("/") or "/"
    if normalized in PUBLIC_PATHS:
        return True
    if any(normalized.startswith(p.rstrip("/")) for p in PUBLIC_PATH_PREFIXES):
        return True
    if method.upper() == "GET" and any(normalized.startswith(p.rstrip("/")) for p in PUBLIC_SKILL_PACK_SYNC_PREFIXES):
        return True
    if method.upper() == "GET" and normalized.startswith("/api/v1/workflows/"):
        return any(normalized.endswith(s) for s in PUBLIC_VERSIONED_WORKFLOW_SUFFIXES_GET)
    if method.upper() == "POST":
        if normalized.endswith("/events") and any(normalized.startswith(p) for p in PUBLIC_TRACKING_EVENT_PREFIXES):
            return True
        # Checked for every versioned-workflow POST, not just the ones ending in "/events" —
        # this used to sit inside that condition, which made the suffix list below unreachable
        # for anything but tracking ingest.
        if normalized.startswith("/api/v1/workflows/"):
            return any(normalized.endswith(s) for s in PUBLIC_VERSIONED_WORKFLOW_SUFFIXES_POST)
    return False


_EXECUTE_CHAT_PROXY_PATHS = ("/api/v1/llm/proxy/text", "/api/v1/llm/proxy/text/stream")


def _body_limit_for_path(path: str, request: Request | None = None) -> int:
    normalized = path.rstrip("/") or "/"
    if normalized.endswith(BUILD_ARTIFACT_UPLOAD_PATHS) or normalized == "/api/v1/workflows/publish":
        return settings.build_artifact_upload_max_bytes
    if normalized == BUG_REPORT_UPLOAD_PATH:
        return BUG_REPORT_UPLOAD_MAX_BYTES
    if normalized == "/api/v1/video/analyze":
        return settings.video_analyze_max_bytes
    if normalized == "/api/v1/llm/proxy/vision":
        return settings.llm_vision_proxy_max_bytes
    if normalized in _EXECUTE_CHAT_PROXY_PATHS and request is not None:
        # A multi-turn chat history is a lot bigger than a one-shot compile
        # prompt — reuses the vision proxy's already-higher cap rather than
        # adding a third size knob for one caller.
        if request.headers.get("x-conxa-client", "").strip() == "conxa-execute":
            return settings.llm_vision_proxy_max_bytes
    return settings.max_json_body_bytes


def _bearer_token(request: Request) -> str:
    value = request.headers.get("authorization", "").strip()
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="missing_bearer_token")
    return token.strip()


# Server-side admin credential (CONXA_ADMIN_TOKEN). Requests bearing it are
# machine calls (CI, ops scripts, admin endpoints like
# /api/v1/entitlements/admin/billing) that have no Clerk session — they skip the
# Clerk gate and each route enforces the token itself via updates_routes._require_admin.
_ADMIN_TOKEN = os.environ.get("CONXA_ADMIN_TOKEN", "")


def _is_admin_token(token: str) -> bool:
    return bool(_ADMIN_TOKEN) and secrets.compare_digest(token, _ADMIN_TOKEN)


def verify_clerk_jwt(token: str) -> dict[str, Any]:
    """Verify a Clerk JWT when SKILL_AUTH_REQUIRED is enabled.

    PyJWT is intentionally imported lazily so local tests do not require the
    optional crypto dependency unless auth verification is actually enabled.
    """

    if not settings.clerk_issuer or not settings.clerk_jwks_url:
        raise HTTPException(status_code=500, detail="clerk_auth_not_configured")
    try:
        import jwt
        from jwt import PyJWKClient
    except Exception as exc:  # pragma: no cover - exercised only in auth deployments
        raise HTTPException(status_code=500, detail="pyjwt_dependency_missing") from exc

    try:
        signing_key = PyJWKClient(settings.clerk_jwks_url).get_signing_key_from_jwt(token)
        options = {"verify_aud": bool(settings.clerk_audience)}
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.clerk_audience or None,
            issuer=settings.clerk_issuer,
            options=options,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid_clerk_token") from exc

    # Browser session tokens carry `azp` (the page origin); Clerk OAuth access
    # tokens (Build Studio, Conxa Execute) carry no `azp`, only `client_id`.
    azp_values = settings.clerk_authorized_party_values
    if azp_values and (payload.get("azp") or payload.get("client_id")) not in azp_values:
        raise HTTPException(status_code=403, detail="invalid_authorized_party")
    return dict(payload)


class ProductionRequestMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        rid = _request_id(request)
        request.state.request_id = rid

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                size = 0
            if size > _body_limit_for_path(request.url.path, request):
                return JSONResponse(
                    {"detail": "request_body_too_large", "message": message_for("request_body_too_large"), "request_id": rid},
                    status_code=413,
                    headers={"x-request-id": rid},
                )

        is_public = _is_public_path(request.url.path, request.method)
        if settings.auth_required and not is_public:
            try:
                token = _bearer_token(request)
                if _is_admin_token(token):
                    claims = {"sub": "admin", "auth_source": "admin_token"}
                else:
                    claims = verify_clerk_jwt(token)
            except HTTPException as exc:
                detail_msg = message_for(exc.detail) if isinstance(exc.detail, str) else str(exc.detail)
                return JSONResponse(
                    {"detail": exc.detail, "message": detail_msg, "request_id": rid},
                    status_code=exc.status_code,
                    headers={"x-request-id": rid},
                )
            request.state.auth = {
                "subject": claims.get("sub"),
                "org_id": claims.get("org_id") or claims.get("orgid"),
                "claims": claims,
            }
            request.state.workspace_id = (
                claims.get("org_id") or claims.get("orgid") or claims.get("sub")
            )

        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response
