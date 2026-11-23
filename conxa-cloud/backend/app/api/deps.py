"""Shared FastAPI dependencies and error mappers for the API routers.

Centralizes the request-to-``Principal`` resolution and the entitlement
error-to-HTTP mapping that were previously copy-pasted across routers.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from conxa_core.workspace import workspace_dir_slug

from app.services.entitlements import EntitlementError, ensure_seats_available
from app.services.saas import Principal, ensure_principal, is_known_member, principal_from_request


def current_principal(request: Request) -> Principal:
    """Resolve the caller's ``Principal`` and persist its identity.

    Usable both as a FastAPI dependency (``Depends(current_principal)``) and as
    a plain helper (``current_principal(request)``).

    A (user, workspace) pair not yet seen would be turned into a new
    membership row by ``ensure_principal`` below — check the seat cap first
    so an over-the-limit new member never gets persisted as a member at all.
    """
    principal = principal_from_request(request)
    if not is_known_member(principal.user_id, principal.workspace_id):
        try:
            ensure_seats_available(principal)
        except EntitlementError as exc:
            raise entitlement_http_error(exc) from exc
    ensure_principal(principal)
    return principal


def own_workspace_slug(principal: Principal, path_value: str) -> str:
    """A company id taken from a URL must be the caller's own workspace (id or slug form). Returns the slug."""
    own = workspace_dir_slug(principal.workspace_id)
    if path_value not in (principal.workspace_id, own):
        raise HTTPException(status_code=403, detail="workspace_forbidden")
    return own


def entitlement_http_error(exc: Exception) -> HTTPException:
    """Map an entitlements-service failure to its HTTP response.

    ``EntitlementError`` carries an explicit status/code; any other failure is
    reported as a 503 so a transient store outage never leaks a 500.
    """
    if isinstance(exc, HTTPException):
        return exc  # e.g. current_principal's 402 seat_limit_exceeded: keep it, never a 503
    if isinstance(exc, EntitlementError):
        return HTTPException(status_code=exc.status_code, detail=exc.code)
    return HTTPException(status_code=503, detail="entitlements_unavailable")
