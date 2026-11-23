"""Current entitlement meters and compile-credit reservation endpoints."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import current_principal, entitlement_http_error
from app.api.machine_binding import register_request_machine
from app.api.updates_routes import _require_admin
from app.services.entitlements import (
    _limits_from_billing,
    auto_claim_pending_grants_for,
    claim_execute_grant,
    commit_compile_credit,
    create_execute_grant,
    current_entitlements,
    execute_pool_status,
    get_installer_domain,
    list_claimed_grant_workspaces_for,
    list_execute_grants,
    list_machines,
    refund_compile_credit,
    release_compile_credit,
    reserve_compile_credit,
    revoke_execute_grant,
    revoke_machine,
    set_installer_domain,
)
from app.services.rbac import require_admin
from app.services.saas import (
    clerk_user_organizations,
    personal_workspace_id,
    upsert_billing,
    workspace_ids_for_email,
    workspace_name_for,
)

router = APIRouter(tags=["entitlements"])
logger = logging.getLogger(__name__)
_ASSIGNABLE_PLANS = {"free", "starter", "pro", "enterprise"}


class ReserveCompileBody(BaseModel):
    reservation_id: str = Field(..., min_length=1, max_length=256)
    workflow_id: str = Field(default="", max_length=128)
    session_id: str = Field(default="", max_length=128)


class ReservationBody(BaseModel):
    reservation_id: str = Field(..., min_length=1, max_length=256)


class RevokeMachineBody(BaseModel):
    machine_hash: str = Field(..., min_length=1, max_length=128)


class CreateExecuteGrantBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)


class RevokeExecuteGrantBody(BaseModel):
    grant_id: str = Field(..., min_length=1, max_length=128)


class ClaimExecuteGrantBody(BaseModel):
    grant_id: str = Field(..., min_length=1, max_length=128)


class InstallerDomainBody(BaseModel):
    domain: str = Field(..., min_length=1, max_length=253)


class AssignPlanBody(BaseModel):
    workspace_id: str | None = Field(default=None, min_length=1, max_length=256)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    plan: str = Field(..., min_length=1, max_length=32)
    duration_days: int | None = Field(default=None, gt=0, le=3650)
    # Per-customer limits (e.g. {"seats": 99}), stored as-is and validated per key when
    # read. Replaces any earlier overrides; {} clears them; omitted leaves them alone.
    entitlement_overrides: dict[str, Any] | None = None


@router.get("/entitlements/current")
def get_current_entitlements(request: Request) -> dict[str, Any]:
    try:
        return current_entitlements(current_principal(request))
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc


@router.post("/usage/compile/reserve")
def post_compile_reserve(body: ReserveCompileBody, request: Request) -> dict[str, Any]:
    try:
        principal = current_principal(request)
        register_request_machine(request, principal)
        return reserve_compile_credit(
            principal,
            reservation_id=body.reservation_id,
            workflow_id=body.workflow_id,
            session_id=body.session_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc


@router.post("/usage/compile/commit")
def post_compile_commit(body: ReservationBody, request: Request) -> dict[str, Any]:
    try:
        return commit_compile_credit(current_principal(request), body.reservation_id)
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc


@router.post("/usage/compile/release")
def post_compile_release(body: ReservationBody, request: Request) -> dict[str, Any]:
    try:
        return release_compile_credit(current_principal(request), body.reservation_id)
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc


@router.post("/usage/compile/refund")
def post_compile_refund(body: ReservationBody, request: Request) -> dict[str, Any]:
    """Refund a *committed* reservation whose compile then aborted on an
    infrastructure failure (cloud LLM proxy unavailable, vision anchor
    exhaustion) — see refund_compile_credit's docstring for why this is
    deliberately narrower than release (which only handles reserved ->
    released, before any credit was ever spent)."""
    try:
        return refund_compile_credit(current_principal(request), body.reservation_id)
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc


@router.get("/entitlements/machines")
def get_registered_machines(request: Request) -> dict[str, Any]:
    """Settings' device list — what's consuming this workspace's machine limit."""
    principal = current_principal(request)
    require_admin(principal)
    return {"machines": list_machines(principal.workspace_id)}


@router.post("/entitlements/machines/revoke")
def post_revoke_machine(body: RevokeMachineBody, request: Request) -> dict[str, Any]:
    principal = current_principal(request)
    require_admin(principal)
    revoke_machine(principal, body.machine_hash)
    return {"machine_hash": body.machine_hash, "revoked": True}


@router.get("/entitlements/execute-grants")
def get_execute_grants(request: Request) -> dict[str, Any]:
    """Admin's list of who's been handed one of this workspace's Conxa
    Execute seats (pending, claimed, or revoked)."""
    principal = current_principal(request)
    require_admin(principal)
    return {"grants": list_execute_grants(principal.workspace_id)}


@router.post("/entitlements/execute-grants")
def post_create_execute_grant(body: CreateExecuteGrantBody, request: Request) -> dict[str, Any]:
    """Invite a person to claim an Execute seat, independent of Build Studio
    org membership — checked against the workspace's execute_seats cap.
    Nothing further to send them: the grant auto-claims the next time that
    email signs into Conxa Execute (see auto_claim_pending_grants_for) —
    Execute and Build Studio share one Clerk app now, so no invite link or
    code is needed."""
    principal = current_principal(request)
    require_admin(principal)
    try:
        return create_execute_grant(principal, body.email)
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc


@router.post("/entitlements/execute-grants/revoke")
def post_revoke_execute_grant(body: RevokeExecuteGrantBody, request: Request) -> dict[str, Any]:
    principal = current_principal(request)
    require_admin(principal)
    revoke_execute_grant(principal, body.grant_id)
    return {"grant_id": body.grant_id, "revoked": True}


@router.post("/entitlements/execute-grants/claim")
def post_claim_execute_grant(body: ClaimExecuteGrantBody, request: Request) -> dict[str, Any]:
    """Phase-1 claim path: a signed-in Cloud Dashboard user claims a grant
    made out to their own email. The normal path is now automatic — see
    GET /api/v1/execute/contexts's auto_claim_pending_grants_for call — so
    this stays useful only as a support/testing fallback."""
    principal = current_principal(request)
    try:
        return claim_execute_grant(grant_id=body.grant_id, user_id=principal.user_id, email=principal.email or "")
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc


@router.get("/execute/contexts")
def get_execute_contexts(request: Request) -> dict[str, Any]:
    """Conxa Execute's personal/team context switcher: every workspace this
    signed-in Clerk user can use Execute under. Called right after sign-in,
    which is also the trigger for auto-claiming any pending Execute grant
    invited to this person's verified email (see auto_claim_pending_grants_for
    — this replaces the old copy-paste invite-code flow now that Execute and
    Build Studio share one Clerk app). A full workspace member gets Execute
    for free as part of the team plan; a grant-only person gets it without
    ever becoming a member (see claim_execute_grant's docstring)."""
    principal = current_principal(request)
    if principal.email:
        auto_claim_pending_grants_for(user_id=principal.user_id, email=principal.email)

    contexts: dict[str, dict[str, Any]] = {}

    personal_id = personal_workspace_id(principal.user_id)

    orgs = clerk_user_organizations(principal.user_id)
    if orgs is None:
        # Clerk lookup unavailable — fall back to just the org this request's
        # own token carries, rather than showing no team context at all.
        if principal.workspace_id != personal_id:
            contexts[principal.workspace_id] = {
                "workspace_id": principal.workspace_id,
                "workspace_name": principal.workspace_name,
                "kind": "member",
            }
    else:
        for org in orgs:
            contexts[org["workspace_id"]] = {
                "workspace_id": org["workspace_id"],
                "workspace_name": org["workspace_name"],
                "kind": "member",
            }

    for grant in list_claimed_grant_workspaces_for(principal.user_id):
        ws_id = grant["workspace_id"]
        if ws_id in contexts:
            continue  # already have Execute via full membership — no need to also show the grant
        contexts[ws_id] = {"workspace_id": ws_id, "workspace_name": workspace_name_for(ws_id), "kind": "grant"}

    for ctx in contexts.values():
        try:
            ctx["credits_remaining"] = execute_pool_status(ctx["workspace_id"])["remaining"]
        except Exception:  # noqa: BLE001
            # None is already the right sentinel here — the frontend must not
            # read it as 0 remaining — it just wasn't logged, so a pool lookup
            # that's been silently failing for a workspace was invisible.
            logger.warning("execute_pool_status_failed workspace_id=%s", ctx["workspace_id"], exc_info=True)
            ctx["credits_remaining"] = None

    if not contexts:
        # Solo user with no team or grant: their own workspace is the only one left.
        contexts[personal_id] = {"workspace_id": personal_id, "workspace_name": "Personal", "kind": "member"}

    return {"contexts": list(contexts.values()), "active_workspace_id": principal.workspace_id}


@router.get("/entitlements/installer-domain")
def get_installer_domain_route(request: Request) -> dict[str, Any]:
    """Unverified, workspace-supplied domain. Any plan may set it (cosmetic only); paid plans use it
    to name the installer, Free ignores it — see docs/PRD.md §11 and TODO.md PROD-6."""
    principal = current_principal(request)
    require_admin(principal)
    return {"domain": get_installer_domain(principal.workspace_id)}


@router.post("/entitlements/installer-domain")
def post_installer_domain(body: InstallerDomainBody, request: Request) -> dict[str, Any]:
    principal = current_principal(request)
    require_admin(principal)
    try:
        domain = set_installer_domain(principal, body.domain)
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc
    return {"domain": domain}


@router.post("/entitlements/admin/billing")
def post_assign_plan(body: AssignPlanBody, authorization: str = Header(default="")) -> dict[str, Any]:
    """Admin-only (Bearer CONXA_ADMIN_TOKEN). Manually sets a workspace's plan —
    for comps, sales demos, or support fixes outside the normal Cashfree
    checkout/webhook flow (see cashfree_routes.py). Bypasses payment; the
    caller is trusted to have a real reason.

    ``duration_days``, if given, time-boxes the grant: entitlements.normalize_plan
    reverts the workspace to "free" once ``plan_expires_at`` passes, with no
    downgrade job needed. Omit it for a permanent grant (also clears any prior
    expiry when re-assigning a workspace that previously had a timed grant).

    Pass either ``workspace_id`` directly or ``email`` — the customer's login
    email, resolved to a workspace_id via every user we've seen sign in with
    it. ``email`` only works once they've signed into the dashboard at least
    once, and 409s (with the candidate workspace_ids) if it's ambiguous
    across more than one workspace."""
    _require_admin(authorization)
    plan = body.plan.strip().lower()
    if plan not in _ASSIGNABLE_PLANS:
        raise HTTPException(status_code=400, detail=f"unknown_plan: must be one of {sorted(_ASSIGNABLE_PLANS)}")

    workspace_id = (body.workspace_id or "").strip()
    if not workspace_id:
        if not body.email:
            raise HTTPException(status_code=400, detail="workspace_id or email required")
        matches = workspace_ids_for_email(body.email)
        if not matches:
            raise HTTPException(status_code=404, detail=f"no workspace found for {body.email}")
        if len(matches) > 1:
            raise HTTPException(
                status_code=409,
                detail=f"{body.email} belongs to multiple workspaces, pass workspace_id: {matches}",
            )
        workspace_id = matches[0]

    expires_at = int(time.time() + body.duration_days * 86400) if body.duration_days else None
    patch: dict[str, Any] = {"plan": plan, "status": "active", "plan_expires_at": expires_at}
    if body.entitlement_overrides is not None:
        patch["entitlement_overrides"] = body.entitlement_overrides
    billing = upsert_billing(workspace_id, patch)
    return {
        "workspace_id": workspace_id,
        "plan": plan,
        "expires_at": expires_at,
        "limits": _limits_from_billing(billing),
        "billing": billing,
    }
