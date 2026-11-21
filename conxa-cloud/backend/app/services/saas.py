"""Workspace, billing, release, and audit metadata for the SaaS product layer.

The compiler still persists artifacts on disk. This module provides the
workspace-scoped product metadata around those artifacts and keeps a file-backed
local implementation for development until SKILL_DATABASE_URL is wired to a
database-backed repository.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request

from conxa_core.config import settings
from conxa_core.db import db_get, db_set
from conxa_core.metrics.store import metrics
from conxa_core.workspace import LOCAL_WORKSPACE_ID  # single source of truth
from app.services.jobs import job_store
from conxa_core.storage.json_store import list_skill_summaries
from conxa_core.storage.skill_packages import list_skill_bundle_summaries

logger = logging.getLogger(__name__)

LOCAL_USER_ID = "user_local"
LOCAL_WORKSPACE_SLUG = "local"

_lock = threading.RLock()


@dataclass(frozen=True)
class Principal:
    user_id: str
    workspace_id: str
    workspace_slug: str
    workspace_name: str
    role: str = "owner"
    email: str | None = None
    name: str | None = None
    auth_provider: str = "local"
    identity_source: str = "local"
    proxy_identity_trusted: bool = False
    proxy_identity_status: str = "backend_secret_missing"

    def public_user(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "email": self.email,
            "name": self.name,
            "auth_provider": self.auth_provider,
        }

    def public_workspace(self) -> dict[str, Any]:
        return {
            "id": self.workspace_id,
            "slug": self.workspace_slug,
            "name": self.workspace_name,
            "role": self.role,
        }


def _metadata_path() -> Path:
    path = settings.data_dir / "saas" / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _default_state() -> dict[str, Any]:
    now = time.time()
    return {
        "users": {
            LOCAL_USER_ID: {
                "id": LOCAL_USER_ID,
                "email": "local@ai-native.dev",
                "name": "Local Developer",
                "created_at": now,
            }
        },
        "workspaces": {
            LOCAL_WORKSPACE_ID: {
                "id": LOCAL_WORKSPACE_ID,
                "slug": LOCAL_WORKSPACE_SLUG,
                "name": "Local workspace",
                "created_at": now,
                "legacy_imported_at": now,
            }
        },
        "memberships": [
            {"user_id": LOCAL_USER_ID, "workspace_id": LOCAL_WORKSPACE_ID, "role": "owner", "created_at": now}
        ],
        "billing": {
            LOCAL_WORKSPACE_ID: {
                "plan": "development",
                "status": "inactive",
                "customer_id": None,
                "subscription_id": None,
                "current_period_end": None,
                "updated_at": now,
            }
        },
        "package_releases": {},
        "audit_events": [],
    }


def _read_state() -> dict[str, Any]:
    data = db_get("saas", "metadata")
    if data is None:
        path = _metadata_path()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
    state = _default_state()
    if isinstance(data, dict):
        for key in state:
            if key in data:
                state[key] = data[key]
    return state


def _write_state(state: dict[str, Any]) -> None:
    db_set("saas", "metadata", state)
    try:
        path = _metadata_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _slug_from_org_id(org_id: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in org_id).strip("-")
    return text or "workspace"


def _normalize_org_role(role: str | None, *, personal_workspace: bool = False) -> str:
    value = str(role or "").strip().lower()
    if value.startswith("org:"):
        value = value.removeprefix("org:").strip()
    if value:
        return value
    # No active Clerk org means the principal is in their own personal workspace
    # (`personal_{user_id}`), which nobody else can reach — they are its sole member
    # and owner. Defaulting them to `basic_member` locked them out of every
    # `require_admin` route (publish, workflow create/delete, subscribe).
    return "owner" if personal_workspace else "basic_member"


def personal_workspace_id(user_id: str) -> str:
    return f"personal_{user_id}"


def _clerk_org_role(user_id: str, org_id: str) -> str | None:
    """Query Clerk Backend API for the user's role in an org.

    Clerk OAuth access tokens (used by Build Studio's PKCE login) carry org_id
    but NOT org_role — the session-token JWT template does not apply to OAuth
    tokens. When org_role is absent from the claims we resolve it here. Requires
    CLERK_SECRET_KEY to be set on the backend.
    """
    secret = settings.clerk_secret_key.strip()
    if not secret:
        logger.warning("clerk_org_role: CLERK_SECRET_KEY not set — cannot resolve org role for %s", user_id)
        return None
    url = f"https://api.clerk.com/v1/users/{user_id}/organization_memberships?limit=100"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {secret}")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
    )
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for m in data.get("data", []):
            if isinstance(m, dict) and m.get("organization", {}).get("id") == org_id:
                role = str(m.get("role") or "")
                logger.info("clerk_org_role: resolved %s in %s → %s", user_id, org_id, role)
                return role
        logger.warning("clerk_org_role: user %s has no membership in org %s", user_id, org_id)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:
            pass
        logger.error("clerk_org_role: Clerk API returned HTTP %s: %s", exc.code, body.decode("utf-8", errors="replace")[:300])
    except Exception as exc:
        logger.error("clerk_org_role: unexpected error: %s", exc)
    return None


def clerk_user_organizations(user_id: str) -> list[dict[str, Any]] | None:
    """Every Clerk organization this user belongs to — used by Conxa Execute's
    personal/team context switcher, which needs the full list up front rather
    than the one org_id a given JWT happens to carry. Returns None (not an
    empty list) when the lookup itself fails, so callers can tell "no orgs"
    apart from "couldn't check" and fall back to just the personal workspace.
    Requires CLERK_SECRET_KEY, same as _clerk_org_role above."""
    secret = settings.clerk_secret_key.strip()
    if not secret:
        return None
    url = f"https://api.clerk.com/v1/users/{user_id}/organization_memberships?limit=100"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {secret}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        # The docstring's None/[] distinction only works if this is actually
        # visible somewhere — it was silently returning None before, same
        # failure mode as _clerk_org_role above except without even the log.
        logger.error("clerk_user_organizations: lookup failed for user %s: %s", user_id, exc)
        return None
    out: list[dict[str, Any]] = []
    for m in data.get("data", []) if isinstance(data, dict) else []:
        org = m.get("organization") if isinstance(m, dict) else None
        if isinstance(org, dict) and org.get("id"):
            out.append({"workspace_id": org["id"], "workspace_name": org.get("name") or org["id"], "role": m.get("role")})
    return out


def visible_workspace_ids_for(principal: Principal) -> list[str]:
    ids = [principal.workspace_id]
    personal_id = personal_workspace_id(principal.user_id)
    if principal.auth_provider == "clerk" and principal.workspace_id != personal_id:
        ids.append(personal_id)
    return ids


def _trusted_proxy_identity(request: Request, subject: str = "") -> tuple[dict[str, str], str]:
    expected_secret = settings.api_proxy_shared_secret.strip()
    if not expected_secret:
        return {}, "backend_secret_missing"

    # HMAC path (preferred): X-Conxa-Proxy-Ts + X-Conxa-Proxy-Sig — replay-protected.
    ts_header = request.headers.get("x-conxa-proxy-ts", "").strip()
    sig_header = request.headers.get("x-conxa-proxy-sig", "").strip()

    if ts_header and sig_header:
        try:
            ts = int(ts_header)
        except (ValueError, TypeError):
            return {}, "proxy_ts_invalid"

        if abs(time.time() - ts) > settings.api_proxy_signing_window:
            return {}, "proxy_ts_stale"

        user_id = request.headers.get("x-conxa-user-id", "").strip()
        if not user_id:
            return {}, "proxy_user_missing"

        expected_sig = hmac.new(
            expected_secret.encode(),
            f"{ts}:{user_id}".encode(),
            "sha256",
        ).hexdigest()
        if not secrets.compare_digest(sig_header, expected_sig):
            return {}, "proxy_sig_invalid"

        if subject and user_id != subject:
            return {}, "proxy_subject_mismatch"

        return (
            {
                "user_id": user_id,
                "org_id": request.headers.get("x-conxa-org-id", "").strip(),
                "org_role": request.headers.get("x-conxa-org-role", "").strip(),
                "org_name": request.headers.get("x-conxa-org-name", "").strip(),
            },
            "trusted",
        )

    # Legacy static-secret path (deprecated — no replay protection).
    provided = request.headers.get("x-conxa-proxy-secret", "").strip()
    if not provided:
        return {}, "proxy_secret_missing"
    if not secrets.compare_digest(provided, expected_secret):
        return {}, "proxy_secret_mismatch"

    user_id = request.headers.get("x-conxa-user-id", "").strip()
    if not user_id:
        return {}, "proxy_user_missing"
    if subject and user_id != subject:
        return {}, "proxy_subject_mismatch"

    return (
        {
            "user_id": user_id,
            "org_id": request.headers.get("x-conxa-org-id", "").strip(),
            "org_role": request.headers.get("x-conxa-org-role", "").strip(),
            "org_name": request.headers.get("x-conxa-org-name", "").strip(),
        },
        "trusted",
    )


def principal_from_request(request: Request) -> Principal:
    auth = getattr(request.state, "auth", None)
    proxy_identity, proxy_identity_status = _trusted_proxy_identity(request)
    if not isinstance(auth, dict) or not auth.get("subject"):
        if proxy_identity:
            subject = proxy_identity["user_id"]
            org_id = proxy_identity.get("org_id") or personal_workspace_id(subject)
            workspace_slug = _slug_from_org_id(org_id)
            return Principal(
                user_id=subject,
                workspace_id=org_id,
                workspace_slug=workspace_slug,
                workspace_name=proxy_identity.get("org_name") or "Workspace",
                role=_normalize_org_role(
                    proxy_identity.get("org_role"),
                    personal_workspace=not proxy_identity.get("org_id"),
                ),
                auth_provider="clerk",
                identity_source="trusted_proxy",
                proxy_identity_trusted=True,
                proxy_identity_status=proxy_identity_status,
            )
        return Principal(
            user_id=LOCAL_USER_ID,
            workspace_id=LOCAL_WORKSPACE_ID,
            workspace_slug=LOCAL_WORKSPACE_SLUG,
            workspace_name="Local workspace",
            email="local@ai-native.dev",
            name="Local Developer",
            proxy_identity_status=proxy_identity_status,
        )

    claims = auth.get("claims") if isinstance(auth.get("claims"), dict) else {}
    subject = str(auth["subject"])
    proxy_identity, proxy_identity_status = _trusted_proxy_identity(request, subject)
    raw_org_id = proxy_identity.get("org_id") or auth.get("org_id")
    org_id = str(raw_org_id or personal_workspace_id(subject))
    workspace_slug = _slug_from_org_id(org_id)
    raw_role = proxy_identity.get("org_role") or auth.get("org_role") or claims.get("org_role")
    if not raw_role and raw_org_id:
        # Clerk OAuth tokens carry org_id but not org_role — resolve via Backend API.
        raw_role = _clerk_org_role(subject, str(raw_org_id))
    org_role = _normalize_org_role(raw_role, personal_workspace=not raw_org_id)
    identity_source = "trusted_proxy" if proxy_identity else "clerk_jwt"
    return Principal(
        user_id=subject,
        workspace_id=org_id,
        workspace_slug=workspace_slug,
        workspace_name=str(proxy_identity.get("org_name") or claims.get("org_name") or claims.get("azp") or "Workspace"),
        role=org_role,
        email=str(claims.get("email") or claims.get("primary_email_address") or "") or None,
        name=str(claims.get("name") or claims.get("full_name") or "") or None,
        auth_provider="clerk",
        identity_source=identity_source,
        proxy_identity_trusted=bool(proxy_identity),
        proxy_identity_status=proxy_identity_status,
    )


def _same_fields(old: Any, new: dict[str, Any]) -> bool:
    return isinstance(old, dict) and all(old.get(key) == value for key, value in new.items())


def ensure_principal(principal: Principal) -> None:
    with _lock:
        state = _read_state()
        now = time.time()
        users = state.setdefault("users", {})
        workspaces = state.setdefault("workspaces", {})
        user = {
            "id": principal.user_id,
            "email": principal.email,
            "name": principal.name,
            "auth_provider": principal.auth_provider,
        }
        workspace = {
            "id": principal.workspace_id,
            "slug": principal.workspace_slug,
            "name": principal.workspace_name,
            "legacy_imported_at": (workspaces.get(principal.workspace_id) or {}).get("legacy_imported_at", now),
        }
        # This runs several times on every request and the document holds every workspace, so rewrite it only
        # when something real changed (CLOUD-26). updated_at therefore means "last changed", not "last seen".
        changed = not _same_fields(users.get(principal.user_id), user) or not _same_fields(
            workspaces.get(principal.workspace_id), workspace
        )
        users[principal.user_id] = {**user, "updated_at": now}
        workspaces[principal.workspace_id] = {**workspace, "updated_at": now}
        memberships = state.setdefault("memberships", [])
        if not any(
            row.get("user_id") == principal.user_id and row.get("workspace_id") == principal.workspace_id
            for row in memberships
            if isinstance(row, dict)
        ):
            changed = True
            memberships.append(
                {
                    "user_id": principal.user_id,
                    "workspace_id": principal.workspace_id,
                    "role": "owner",
                    "created_at": now,
                }
            )
        billing = state.setdefault("billing", {})
        changed = changed or principal.workspace_id not in billing
        billing.setdefault(
            principal.workspace_id,
            {
                "plan": "development" if principal.auth_provider == "local" else "free",
                "status": "inactive",
                "customer_id": None,
                "subscription_id": None,
                "current_period_end": None,
                # Stamped once, on first-ever sight of this workspace, so the free
                # plan's 30-day trial clock starts here rather than at first billing
                # read. Paid plans never consult this field (see entitlements.trial_expired).
                "trial_started_at": now,
                "updated_at": now,
            },
        )
        if changed:
            _write_state(state)


def add_audit_event(
    principal: Principal,
    action: str,
    *,
    resource_type: str,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _lock:
        state = _read_state()
        event = {
            "id": f"evt_{int(time.time() * 1000)}",
            "workspace_id": principal.workspace_id,
            "user_id": principal.user_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "metadata": metadata or {},
            "created_at": time.time(),
        }
        events = state.setdefault("audit_events", [])
        events.append(event)
        state["audit_events"] = events[-500:]
        _write_state(state)
        return event


def audit_events_for(principal: Principal, limit: int = 100) -> list[dict[str, Any]]:
    ensure_principal(principal)
    with _lock:
        state = _read_state()
        rows = [
            row
            for row in state.get("audit_events", [])
            if isinstance(row, dict) and row.get("workspace_id") == principal.workspace_id
        ]
    rows.sort(key=lambda row: float(row.get("created_at") or 0), reverse=True)
    return rows[: max(1, min(limit, 500))]


def billing_for(principal: Principal) -> dict[str, Any]:
    ensure_principal(principal)
    with _lock:
        state = _read_state()
        billing = dict(state.get("billing", {}).get(principal.workspace_id) or {})
        # Backstop for workspaces created before trial_started_at existed: stamp
        # it now rather than leaving it missing, which would otherwise read as
        # "trial started at the epoch" and expire instantly (see entitlements.trial_expired).
        if not billing.get("trial_started_at"):
            billing["trial_started_at"] = time.time()
            state.setdefault("billing", {})[principal.workspace_id] = billing
            _write_state(state)
    billing.setdefault("plan", "development" if principal.auth_provider == "local" else "free")
    billing.setdefault("status", "inactive")
    return billing


def billing_for_workspace(workspace_id: str) -> dict[str, Any]:
    """Read-only billing lookup by workspace_id alone, for call sites (like
    delta-sync) that only have a sync_token's workspace_id, not a full
    Principal. Unlike billing_for, does not create/backstop workspace state."""
    with _lock:
        state = _read_state()
        billing = dict(state.get("billing", {}).get(workspace_id) or {})
    billing.setdefault("plan", "free")
    billing.setdefault("status", "inactive")
    return billing


def workspace_name_for(workspace_id: str) -> str:
    """Read-only display-name lookup by workspace_id alone, same call-site
    shape as billing_for_workspace — for the Execute bridge's 'Paid by
    <workspace>' UI, which only ever has a workspace_id, never a Principal."""
    with _lock:
        state = _read_state()
        row = state.get("workspaces", {}).get(workspace_id) or {}
    name = str(row.get("name") or "").strip()
    return name or "your workspace"


def workspace_ids_for_email(email: str) -> list[str]:
    """Workspace IDs for every user we've ever seen sign in with this email
    (case-insensitive). Used by the manual plan-grant admin endpoint to turn
    a customer's email into the workspace_id upsert_billing needs — nothing
    else in the product looks a user up by email, so this stays local rather
    than calling out to Clerk's Admin API."""
    needle = email.strip().lower()
    if not needle:
        return []
    with _lock:
        state = _read_state()
        user_ids = {
            uid
            for uid, row in state.get("users", {}).items()
            if isinstance(row, dict) and str(row.get("email") or "").strip().lower() == needle
        }
        return sorted(
            {
                row.get("workspace_id")
                for row in state.get("memberships", [])
                if isinstance(row, dict) and row.get("user_id") in user_ids and row.get("workspace_id")
            }
        )


def membership_count_for(workspace_id: str) -> int:
    """Best-effort local/dev seat count backed by SaaS membership state."""
    with _lock:
        state = _read_state()
        count = sum(
            1
            for row in state.get("memberships", [])
            if isinstance(row, dict) and row.get("workspace_id") == workspace_id
        )
    return max(1, count)


def is_known_member(user_id: str, workspace_id: str) -> bool:
    """True once a (user, workspace) pair already has a membership row — lets
    a caller distinguish an established member from one about to be created
    for the first time (see entitlements.ensure_seats_available)."""
    with _lock:
        state = _read_state()
        return any(
            row.get("user_id") == user_id and row.get("workspace_id") == workspace_id
            for row in state.get("memberships", [])
            if isinstance(row, dict)
        )


def upsert_billing(workspace_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        state = _read_state()
        current = dict(state.setdefault("billing", {}).get(workspace_id) or {})
        current.update(patch)
        current["updated_at"] = time.time()
        state["billing"][workspace_id] = current
        _write_state(state)
        return current


def _release_key(principal: Principal, bundle_slug: str) -> str:
    return f"{principal.workspace_id}:{bundle_slug}"


def release_for(principal: Principal, bundle_slug: str) -> dict[str, Any]:
    ensure_principal(principal)
    with _lock:
        state = _read_state()
        release = dict(state.get("package_releases", {}).get(_release_key(principal, bundle_slug)) or {})
    release.setdefault("bundle_slug", bundle_slug)
    release.setdefault("workspace_id", principal.workspace_id)
    release.setdefault("state", "draft")
    release.setdefault("version", "0.1.0")
    release.setdefault("release_notes", "")
    release.setdefault("published_by", None)
    release.setdefault("published_at", None)
    release.setdefault("archived_at", None)
    return release


def update_release(principal: Principal, bundle_slug: str, patch: dict[str, Any]) -> dict[str, Any]:
    ensure_principal(principal)
    with _lock:
        state = _read_state()
        key = _release_key(principal, bundle_slug)
        current = release_for(principal, bundle_slug)
        current.update(patch)
        current["bundle_slug"] = bundle_slug
        current["workspace_id"] = principal.workspace_id
        current["updated_at"] = time.time()
        state.setdefault("package_releases", {})[key] = current
        _write_state(state)
    add_audit_event(
        principal,
        "package_release_updated",
        resource_type="package_bundle",
        resource_id=bundle_slug,
        metadata={"state": current.get("state"), "version": current.get("version")},
    )
    return current


def usage_for(principal: Principal) -> dict[str, Any]:
    ensure_principal(principal)
    skills = list_skill_summaries()
    packages = list_skill_bundle_summaries()
    jobs = job_store.list()
    workflow_count = sum(len(pkg.get("workflows") or []) for pkg in packages)
    return {
        "workspace_id": principal.workspace_id,
        "skills": len(skills),
        "packages": len(packages),
        "workflows": workflow_count,
        "jobs": len(jobs),
        "active_jobs": sum(1 for job in jobs if job.status in {"queued", "running"}),
        "metrics": metrics.snapshot(),
        "limits": {
            "skills": None,
            "packages": None,
            "monthly_recordings": None,
        },
    }


def dashboard_for(principal: Principal) -> dict[str, Any]:
    ensure_principal(principal)
    skills = list_skill_summaries()
    packages = list_skill_bundle_summaries()
    jobs = [job.public() for job in job_store.list()]
    releases = [release_for(principal, str(pkg.get("package_name"))) for pkg in packages]
    published = sum(1 for release in releases if release.get("state") == "published")
    return {
        "workspace": principal.public_workspace(),
        "stats": {
            "skills": len(skills),
            "packages": len(packages),
            "workflows": sum(len(pkg.get("workflows") or []) for pkg in packages),
            "active_jobs": sum(1 for job in jobs if job.get("status") in {"queued", "running"}),
            "published_packages": published,
        },
        "recent_workflows": skills[:6],
        "recent_packages": packages[:6],
        "active_jobs": [job for job in jobs if job.get("status") in {"queued", "running"}][:6],
        "package_health": [
            {
                "package_name": pkg.get("package_name"),
                "workflow_count": len(pkg.get("workflows") or []),
                "file_count": len(pkg.get("files") or []),
                "release_state": release_for(principal, str(pkg.get("package_name"))).get("state"),
            }
            for pkg in packages[:6]
        ],
        "usage": usage_for(principal),
    }
