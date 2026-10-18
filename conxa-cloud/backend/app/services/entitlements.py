"""Workspace entitlements, monthly usage meters, and quota reservations."""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator
from urllib.parse import quote

from sqlalchemy import text

from conxa_core.config import settings
from conxa_core.db import _get_engine, db_get, db_list, db_set  # type: ignore[attr-defined]
from app.services.saas import (
    Principal,
    billing_for,
    billing_for_workspace,
    clerk_user_organizations,
    membership_count_for,
    personal_workspace_id,
    upsert_billing,
)

USAGE_NS = "entitlement_usage"
RESERVATION_NS = "compile_reservations"
DEVICE_NS = "workspace_devices"
WORKFLOW_NS = "entitlement_workflows"
INSTALLER_DOMAIN_NS = "workspace_installer_domain"
EXECUTE_GRANT_NS = "execute_grants"
EXECUTE_GRANT_BY_USER_NS = "execute_grant_by_user"

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")

# "ai_credits" is the canonical name of the shared AI credits pool class (CLOUD-25).
# "human_edit" is its legacy wire name, accepted permanently for already-installed Studios,
# and normalized to "ai_credits" the moment a request is metered.
ALLOWED_USAGE_CLASSES = {"compile", "ai_credits", "human_edit", "execute_chat"}
LEGACY_USAGE_CLASS_ALIASES = {"human_edit": "ai_credits"}


def normalize_usage_class(usage_class: str) -> str:
    return LEGACY_USAGE_CLASS_ALIASES.get(usage_class, usage_class)


def pool_exceeded_code(usage_class: str) -> str:
    """The error code for an exhausted AI credits pool. Only callers that send the new
    "ai_credits" class get the new code; legacy "human_edit" callers and Conxa Execute
    ("execute_chat") keep the code their error mapping already knows."""
    return "ai_credits_exceeded" if usage_class == "ai_credits" else "human_edit_pool_exceeded"

# usage classes that draw from the same AI Usage Credits pool/wallet —
# separate *counters* for reporting, not separate quotas. Legacy "human_edit" counters written
# before CLOUD-25 are still summed, so no stored usage row needs migrating.
_POOL_USAGE_CLASSES = ("ai_credits", "human_edit", "execute_chat")


def _combined_pool_used(usage: dict[str, Any]) -> int:
    total = 0
    for cls in _POOL_USAGE_CLASSES:
        total += int(usage.get(f"{cls}_input_tokens") or 0) + int(usage.get(f"{cls}_output_tokens") or 0)
    return total

logger = logging.getLogger(__name__)

# The capability ladder (see docs/PRD.md §11): each tier is not just bigger
# numbers, it unlocks what a workspace can *do*. There is no limit on how many
# distinct product slugs a workspace may publish under — that used to be
# "skill_pack_slots" and was removed 2026-08-08 (see docs/Implementation-Plan.md);
# a company slug is just a named group of workflows, not a rationed slot. Reach is
# gated by `distribution` (internal vs. external) instead of a count.
PLAN_LIMITS: dict[str, dict[str, Any]] = {
    "free": {
        "seats": 1,
        "machines": 1,
        "execute_seats": 1,
        "compile_credits": 25,
        "human_edit_tokens": 500_000,
        "trial_days": 30,
        "distribution": "internal",
        "white_label": False,
        "ops_tier": "none",
        "analytics_retention_days": 0,
        "compile_pool": "free",
        "byok": False,
        "vision_fallback_on_exhaustion": False,
    },
    "starter": {
        "seats": 3,
        "machines": 3,
        "execute_seats": 25,
        "compile_credits": 200,
        "human_edit_tokens": 2_500_000,
        "trial_days": None,
        "distribution": "external",
        "white_label": False,
        "ops_tier": "basic",
        "analytics_retention_days": 90,
        "compile_pool": "starter",
        "byok": False,
        "vision_fallback_on_exhaustion": False,
    },
    "pro": {
        "seats": 10,
        "machines": 10,
        "execute_seats": 100,
        "compile_credits": 500,
        "human_edit_tokens": 10_000_000,
        "trial_days": None,
        "distribution": "external",
        "white_label": False,
        "ops_tier": "full",
        "analytics_retention_days": 365,
        "compile_pool": "pro",
        "byok": False,
        "vision_fallback_on_exhaustion": False,
    },
    # Enterprise workspaces must carry explicit overrides in billing metadata
    # for the numeric limits; the capability flags below are Enterprise's floor.
    "enterprise": {
        "seats": 0,
        "machines": 0,
        "execute_seats": 0,
        "compile_credits": None,
        "human_edit_tokens": 0,
        "trial_days": None,
        "distribution": "external",
        "white_label": True,
        "ops_tier": "full",
        "analytics_retention_days": None,
        "compile_pool": "premium",
        "byok": True,
        # Unlike the other capabilities here, this isn't plan-gated — it's an
        # ops reliability lever, not a paid feature. Conxa can enable it for
        # any workspace on any plan (including Free) via entitlement_overrides;
        # the plan default is False everywhere.
        "vision_fallback_on_exhaustion": False,
    },
    "development": {
        "seats": None,
        "machines": None,
        "execute_seats": None,
        "compile_credits": None,
        "human_edit_tokens": None,
        "trial_days": None,
        "distribution": "external",
        "white_label": True,
        "ops_tier": "full",
        "analytics_retention_days": None,
        "compile_pool": "premium",
        "byok": True,
        "vision_fallback_on_exhaustion": False,
    },
}

# Compile add-on packs (2026-08-22): ONE-TIME purchases, not subscriptions.
# A pack is bought once via a Cashfree Payment Link and its credits land in the
# workspace's credit wallet — a never-expiring balance consumed only after the
# plan's monthly allowance runs out (see _wallet / reserve_compile_credit /
# record_llm_usage). Priced proportionally off the 20-compile anchor
# (₹3,999 / 200k tokens, rounded down to ₹…,999 charm points). The wallet lives
# on billing as ``{"credit_wallet": {"compile_credits": n, "human_edit_tokens": n}}``.
ADDON_TIERS: dict[str, dict[str, Any]] = {
    "credits_addon_20": {
        "name": "+20 compiles",
        "amount": 3_999,
        "currency": "INR",
        "compile_credits": 20,
        "human_edit_tokens": 200_000,
    },
    "credits_addon_50": {
        "name": "+50 compiles",
        "amount": 9_999,
        "currency": "INR",
        "compile_credits": 50,
        "human_edit_tokens": 500_000,
    },
    "credits_addon_100": {
        "name": "+100 compiles",
        "amount": 19_999,
        "currency": "INR",
        "compile_credits": 100,
        "human_edit_tokens": 1_000_000,
    },
    "credits_addon_250": {
        "name": "+250 compiles",
        "amount": 49_999,
        "currency": "INR",
        "compile_credits": 250,
        "human_edit_tokens": 2_500_000,
    },
}

_lock = threading.RLock()


class EntitlementError(Exception):
    def __init__(self, code: str, status_code: int = 403) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class _FileKvStore:
    def get(self, namespace: str, key: str) -> Any | None:
        return db_get(namespace, key)

    def set(self, namespace: str, key: str, data: Any) -> None:
        db_set(namespace, key, data)

    def list(self, namespace: str) -> list[Any]:
        return db_list(namespace)


class _SqlKvStore:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def get(self, namespace: str, key: str) -> Any | None:
        row = self.conn.execute(
            text("SELECT data FROM kv_store WHERE namespace = :ns AND key = :key"),
            {"ns": namespace, "key": key},
        ).fetchone()
        return row[0] if row else None

    def set(self, namespace: str, key: str, data: Any) -> None:
        self.conn.execute(
            text(
                """
                INSERT INTO kv_store (namespace, key, data)
                VALUES (:ns, :key, CAST(:data AS jsonb))
                ON CONFLICT (namespace, key) DO UPDATE
                SET data = EXCLUDED.data, updated_at = now()
                """
            ),
            {"ns": namespace, "key": key, "data": json.dumps(data)},
        )

    def list(self, namespace: str) -> list[Any]:
        rows = self.conn.execute(
            text("SELECT data FROM kv_store WHERE namespace = :ns ORDER BY created_at"),
            {"ns": namespace},
        ).fetchall()
        return [row[0] for row in rows]


@contextmanager
def _locked_store(lock_key: str) -> Iterator[_FileKvStore | _SqlKvStore]:
    engine = _get_engine()
    if engine is None:
        with _lock:
            yield _FileKvStore()
        return
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})
        yield _SqlKvStore(conn)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_period(now: datetime | None = None) -> str:
    now = now or _now()
    return now.strftime("%Y-%m")


def reset_at_for_period(period: str) -> str:
    year_s, month_s = period.split("-", 1)
    year = int(year_s)
    month = int(month_s)
    if month == 12:
        reset = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        reset = datetime(year, month + 1, 1, tzinfo=UTC)
    return _iso(reset)


def _positive_epoch(value: Any) -> int | None:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        timestamp = 0
    return timestamp if timestamp > 0 else None


def usage_window_for_billing(billing: dict[str, Any]) -> tuple[str, str]:
    current_period_end = _positive_epoch(billing.get("current_period_end"))
    if current_period_end is not None:
        reset_at = datetime.fromtimestamp(current_period_end, UTC)
        return f"billing:{current_period_end}", _iso(reset_at)

    period = current_period()
    return period, reset_at_for_period(period)


def normalize_plan(billing: dict[str, Any]) -> str:
    """Resolves a billing record to its effective plan, honoring
    ``plan_expires_at`` — a time-boxed manual grant (see
    ``entitlement_routes.post_assign_plan``) that has passed reverts to
    "free" without needing a downgrade job. Real Cashfree subscriptions never
    set this field, so they're unaffected."""
    expires_at = _positive_epoch(billing.get("plan_expires_at"))
    if expires_at is not None and time.time() >= expires_at:
        return "free"
    value = str(billing.get("plan") or "free").strip().lower()
    if value == "basic":
        return "starter"
    if value in PLAN_LIMITS:
        return value
    # This gates every request for the workspace — a hard failure here would
    # take the whole workspace offline over what is almost always a stray
    # admin-tooling typo, not a request-level problem. Falls back to "free"
    # (the safe, least-privileged default) but logs loudly so a workspace
    # silently downgraded to free — including a paying one — gets noticed.
    logger.error("unknown_plan_value plan=%r workspace_billing_keys=%s", value, sorted(billing.keys()))
    return "free"


_QUOTA_ALIASES = {
    "seats": "seats",
    "seat_limit": "seats",
    "machines": "machines",
    "machine_limit": "machines",
    "execute_seats": "execute_seats",
    "execute_seat_limit": "execute_seats",
    "compile_credits": "compile_credits",
    "monthly_compile_credits": "compile_credits",
    "human_edit_tokens": "human_edit_tokens",
    "monthly_human_edit_tokens": "human_edit_tokens",
}

def _limits_from_billing(billing: dict[str, Any]) -> dict[str, Any]:
    plan = normalize_plan(billing)
    limits = dict(PLAN_LIMITS[plan])
    overrides = billing.get("entitlement_overrides") or billing.get("limits") or {}
    if not isinstance(overrides, dict):
        return limits

    for raw_key, target_key in _QUOTA_ALIASES.items():
        if raw_key not in overrides:
            continue
        raw_value = overrides.get(raw_key)
        if raw_value is None or str(raw_value).lower() == "unlimited":
            limits[target_key] = None
            continue
        try:
            limits[target_key] = max(0, int(raw_value))
        except (TypeError, ValueError):
            # Same reasoning as normalize_plan above: this gates every request,
            # so a malformed override silently falls back to the plan default
            # rather than 500ing every call for the workspace — but it's logged
            # loudly, since a wrong quota override is exactly the kind of thing
            # that should never fail silently in a billing system.
            logger.error(
                "invalid_entitlement_override key=%s value=%r target=%s", raw_key, raw_value, target_key
            )
            continue

    # analytics_retention_days shares the "None = unlimited/forever" sentinel
    # with the quota keys above but isn't a compile/seat quota, so it's kept
    # in its own loop rather than folded into _QUOTA_ALIASES.
    if "analytics_retention_days" in overrides:
        raw_value = overrides.get("analytics_retention_days")
        if raw_value is None or str(raw_value).lower() == "unlimited":
            limits["analytics_retention_days"] = None
        else:
            try:
                limits["analytics_retention_days"] = max(0, int(raw_value))
            except (TypeError, ValueError):
                pass

    if "distribution" in overrides and str(overrides["distribution"]) in ("internal", "external"):
        limits["distribution"] = str(overrides["distribution"])
    if "ops_tier" in overrides and str(overrides["ops_tier"]) in ("none", "basic", "full"):
        limits["ops_tier"] = str(overrides["ops_tier"])
    if "compile_pool" in overrides and str(overrides["compile_pool"]) in ("free", "starter", "pro", "premium"):
        limits["compile_pool"] = str(overrides["compile_pool"])
    if "white_label" in overrides:
        limits["white_label"] = bool(overrides["white_label"])
    if "byok" in overrides:
        limits["byok"] = bool(overrides["byok"])
    if "vision_fallback_on_exhaustion" in overrides:
        limits["vision_fallback_on_exhaustion"] = bool(overrides["vision_fallback_on_exhaustion"])
    if "trial_days" in overrides:
        raw_value = overrides.get("trial_days")
        limits["trial_days"] = None if raw_value in (None, "") else max(0, int(raw_value))

    return limits


def _wallet(billing: dict[str, Any]) -> dict[str, int]:
    """Read the never-expiring credit wallet off a billing record. Purchased via
    one-time add-on packs; drawn down only after the plan's monthly allowance
    is exhausted, and never reset by billing-period rollover."""
    raw = billing.get("credit_wallet") or {}
    if not isinstance(raw, dict):
        return {"compile_credits": 0, "human_edit_tokens": 0}
    try:
        compile_credits = max(0, int(raw.get("compile_credits") or 0))
    except (TypeError, ValueError):
        compile_credits = 0
    try:
        human_edit_tokens = max(0, int(raw.get("human_edit_tokens") or 0))
    except (TypeError, ValueError):
        human_edit_tokens = 0
    return {"compile_credits": compile_credits, "human_edit_tokens": human_edit_tokens}


def grant_credit_wallet(
    workspace_id: str,
    *,
    compile_credits: int = 0,
    human_edit_tokens: int = 0,
) -> dict[str, int]:
    """Add purchased add-on credits to the workspace wallet (payment confirmed).
    Idempotency is the caller's job — see cashfree_routes' granted-orders guard."""
    current = upsert_billing(workspace_id, {})
    wallet = _wallet(current)
    wallet["compile_credits"] += max(0, int(compile_credits))
    wallet["human_edit_tokens"] += max(0, int(human_edit_tokens))
    upsert_billing(workspace_id, {"credit_wallet": wallet})
    return wallet


def _spend_wallet(
    workspace_id: str,
    *,
    compile_credits: int = 0,
    human_edit_tokens: int = 0,
) -> bool:
    """Atomically draw from the wallet if it can cover the amount. Returns False
    (and spends nothing) when the balance is insufficient. Floored at 0 on
    write so a concurrent-reservation race can't produce a negative balance."""
    current = upsert_billing(workspace_id, {})
    wallet = _wallet(current)
    need_compile = max(0, int(compile_credits))
    need_tokens = max(0, int(human_edit_tokens))
    if wallet["compile_credits"] < need_compile or wallet["human_edit_tokens"] < need_tokens:
        return False
    wallet["compile_credits"] = max(0, wallet["compile_credits"] - need_compile)
    wallet["human_edit_tokens"] = max(0, wallet["human_edit_tokens"] - need_tokens)
    upsert_billing(workspace_id, {"credit_wallet": wallet})
    return True


def usage_key(workspace_id: str, period: str) -> str:
    return f"{workspace_id}:{period}"


def _usage_defaults(workspace_id: str, period: str) -> dict[str, Any]:
    now = _iso(_now())
    return {
        "workspace_id": workspace_id,
        "period": period,
        "compile_credits_used": 0,
        "compile_input_tokens": 0,
        "compile_output_tokens": 0,
        "compile_requests": 0,
        "ai_credits_input_tokens": 0,
        "ai_credits_output_tokens": 0,
        "ai_credits_requests": 0,
        "human_edit_input_tokens": 0,
        "human_edit_output_tokens": 0,
        "human_edit_requests": 0,
        "created_at": now,
        "updated_at": now,
    }


def _get_usage(store: _FileKvStore | _SqlKvStore, workspace_id: str, period: str) -> dict[str, Any]:
    data = store.get(USAGE_NS, usage_key(workspace_id, period))
    usage = _usage_defaults(workspace_id, period)
    if isinstance(data, dict):
        usage.update(data)
    return usage


def _set_usage(store: _FileKvStore | _SqlKvStore, usage: dict[str, Any]) -> None:
    usage["updated_at"] = _iso(_now())
    store.set(USAGE_NS, usage_key(str(usage["workspace_id"]), str(usage["period"])), usage)


def _reservation_defaults(
    *,
    reservation_id: str,
    workspace_id: str,
    period: str,
    workflow_id: str,
    session_id: str,
) -> dict[str, Any]:
    now_ts = time.time()
    now_iso = _iso(_now())
    return {
        "reservation_id": reservation_id,
        "workspace_id": workspace_id,
        "period": period,
        "amount": 1,
        "status": "reserved",
        "workflow_id": workflow_id,
        "session_id": session_id,
        "idempotency_key": reservation_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "expires_at": now_ts + max(60, int(settings.entitlements_reservation_ttl_secs)),
    }


def _set_reservation(store: _FileKvStore | _SqlKvStore, row: dict[str, Any]) -> None:
    row["updated_at"] = _iso(_now())
    store.set(RESERVATION_NS, str(row["reservation_id"]), row)


def _reservation_matches(row: dict[str, Any], workspace_id: str, period: str) -> bool:
    return row.get("workspace_id") == workspace_id and row.get("period") == period


def _expire_reservations(store: _FileKvStore | _SqlKvStore, workspace_id: str, period: str) -> None:
    now_ts = time.time()
    for row in store.list(RESERVATION_NS):
        if not isinstance(row, dict) or not _reservation_matches(row, workspace_id, period):
            continue
        if row.get("status") != "reserved":
            continue
        try:
            expires_at = float(row.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at <= now_ts:
            row["status"] = "expired"
            _set_reservation(store, row)


def _active_reserved_amount(store: _FileKvStore | _SqlKvStore, workspace_id: str, period: str) -> int:
    now_ts = time.time()
    total = 0
    for row in store.list(RESERVATION_NS):
        if not isinstance(row, dict) or not _reservation_matches(row, workspace_id, period):
            continue
        if row.get("status") != "reserved":
            continue
        try:
            expires_at = float(row.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at > now_ts:
            total += int(row.get("amount") or 0)
    return total


def _device_key(workspace_id: str, machine_hash: str) -> str:
    return f"{workspace_id}:{machine_hash}"


def _workspace_devices(store: _FileKvStore | _SqlKvStore, workspace_id: str) -> list[dict[str, Any]]:
    """Active (non-revoked) devices — a revoked machine frees its slot
    immediately rather than counting against the limit forever."""
    rows: list[dict[str, Any]] = []
    for row in store.list(DEVICE_NS):
        if isinstance(row, dict) and row.get("workspace_id") == workspace_id and not row.get("revoked"):
            rows.append(row)
    return rows


def list_machines(workspace_id: str) -> list[dict[str, Any]]:
    """All devices ever seen for this workspace, including revoked ones, for
    the Settings device-list UI — unlike _workspace_devices, this doesn't
    filter revoked rows out, so the UI can show revocation history."""
    with _locked_store(f"machines:{workspace_id}") as store:
        rows = [
            row for row in store.list(DEVICE_NS)
            if isinstance(row, dict) and row.get("workspace_id") == workspace_id
        ]
    rows.sort(key=lambda row: str(row.get("last_seen") or ""), reverse=True)
    return rows


def machine_count(workspace_id: str) -> int:
    with _locked_store(f"machines:{workspace_id}") as store:
        return len(_workspace_devices(store, workspace_id))


def ensure_machine_slot(principal: Principal, machine_hash: str, ip: str = "") -> dict[str, Any]:
    """Register a build-side device against the workspace's machine limit.

    Known hash: touch last_seen/last_ip and allow, uncounted. New hash within
    the limit: register and allow. New hash at the limit: reject — this is the
    control that stops a single-machine free trial from quietly becoming a
    free Pro seat (see docs/PRD.md §11).
    """
    machine_hash = str(machine_hash or "").strip()
    if not machine_hash:
        raise EntitlementError("invalid_machine_id", 400)
    billing = billing_for(principal)
    limits = _limits_from_billing(billing)
    limit = limits["machines"]
    workspace_id = principal.workspace_id
    with _locked_store(f"machines:{workspace_id}") as store:
        existing = store.get(DEVICE_NS, _device_key(workspace_id, machine_hash))
        now_iso = _iso(_now())
        # A revoked machine is treated as brand-new — falls through to the
        # limit check below rather than silently un-revoking on next touch.
        if isinstance(existing, dict) and existing.get("revoked"):
            existing = None
        if isinstance(existing, dict):
            existing["last_seen"] = now_iso
            if ip:
                existing["last_ip"] = ip
            store.set(DEVICE_NS, _device_key(workspace_id, machine_hash), existing)
            used = len(_workspace_devices(store, workspace_id))
            return {"machine_hash": machine_hash, "registered": True, "used": used, "limit": limit}
        used = len(_workspace_devices(store, workspace_id))
        if settings.entitlements_enforce_machines and limit is not None and used >= int(limit):
            raise EntitlementError("machine_limit_exceeded", 402)
        row = {
            "workspace_id": workspace_id,
            "machine_hash": machine_hash,
            "last_ip": ip,
            "first_seen": now_iso,
            "last_seen": now_iso,
        }
        store.set(DEVICE_NS, _device_key(workspace_id, machine_hash), row)
        used += 1
    return {"machine_hash": machine_hash, "registered": True, "used": used, "limit": limit}


def revoke_machine(principal: Principal, machine_hash: str) -> None:
    workspace_id = principal.workspace_id
    with _locked_store(f"machines:{workspace_id}") as store:
        existing = store.get(DEVICE_NS, _device_key(workspace_id, machine_hash))
        if isinstance(existing, dict) and existing.get("workspace_id") == workspace_id:
            store.set(DEVICE_NS, _device_key(workspace_id, machine_hash), {**existing, "revoked": True})


def _workspace_execute_grants(store: _FileKvStore | _SqlKvStore, workspace_id: str) -> list[dict[str, Any]]:
    """Active (pending or claimed) grants — a revoked grant frees its slot
    immediately, same shape as _workspace_devices."""
    rows: list[dict[str, Any]] = []
    for row in store.list(EXECUTE_GRANT_NS):
        if (
            isinstance(row, dict)
            and row.get("workspace_id") == workspace_id
            and row.get("status") in ("pending", "claimed")
        ):
            rows.append(row)
    return rows


def execute_grant_count(workspace_id: str) -> int:
    with _locked_store(f"execute-grants:{workspace_id}") as store:
        return len(_workspace_execute_grants(store, workspace_id))


def list_execute_grants(workspace_id: str) -> list[dict[str, Any]]:
    """All grants ever created for this workspace, including revoked ones, for
    the admin UI — unlike _workspace_execute_grants, doesn't filter them out."""
    with _locked_store(f"execute-grants:{workspace_id}") as store:
        rows = [
            row for row in store.list(EXECUTE_GRANT_NS)
            if isinstance(row, dict) and row.get("workspace_id") == workspace_id
        ]
    rows.sort(key=lambda row: str(row.get("granted_at") or ""), reverse=True)
    return rows


def create_execute_grant(principal: Principal, email: str) -> dict[str, Any]:
    """Invite a person (by email, independent of Clerk org membership) to
    claim one of the workspace's Conxa Execute seats. Idempotent on a repeat
    invite to the same still-pending email — resending an invite link should
    never consume a second seat."""
    email = str(email or "").strip().lower()
    if not email or "@" not in email:
        raise EntitlementError("invalid_email", 400)
    billing = billing_for(principal)
    limits = _limits_from_billing(billing)
    limit = limits["execute_seats"]
    workspace_id = principal.workspace_id
    with _locked_store(f"execute-grants:{workspace_id}") as store:
        for row in store.list(EXECUTE_GRANT_NS):
            if not isinstance(row, dict) or row.get("workspace_id") != workspace_id or row.get("email") != email:
                continue
            if row.get("status") == "pending":
                return row
            if row.get("status") == "claimed":
                raise EntitlementError("execute_grant_already_claimed", 409)
        used = len(_workspace_execute_grants(store, workspace_id))
        if settings.entitlements_enforce_execute_seats and limit is not None and used >= int(limit):
            raise EntitlementError("execute_seat_limit_exceeded", 402)
        now_iso = _iso(_now())
        grant_id = secrets.token_urlsafe(32)
        row = {
            "grant_id": grant_id,
            "workspace_id": workspace_id,
            "email": email,
            "status": "pending",
            "granted_at": now_iso,
            "granted_by": principal.user_id,
            "claimed_at": None,
            "claimed_user_id": None,
            "revoked_at": None,
            "revoked_by": None,
        }
        store.set(EXECUTE_GRANT_NS, grant_id, row)
    return row


def revoke_execute_grant(principal: Principal, grant_id: str) -> None:
    workspace_id = principal.workspace_id
    with _locked_store(f"execute-grants:{workspace_id}") as store:
        existing = store.get(EXECUTE_GRANT_NS, grant_id)
        if not isinstance(existing, dict) or existing.get("workspace_id") != workspace_id:
            return
        claimed_user_id = existing.get("claimed_user_id")
        store.set(
            EXECUTE_GRANT_NS,
            grant_id,
            {**existing, "status": "revoked", "revoked_at": _iso(_now()), "revoked_by": principal.user_id},
        )
        # Frees the pool binding immediately — the very next chat turn from
        # this person stops resolving to the workspace's pool rather than
        # waiting for some later sync.
        if claimed_user_id:
            store.set(EXECUTE_GRANT_BY_USER_NS, claimed_user_id, None)


def claim_execute_grant(*, grant_id: str, user_id: str, email: str) -> dict[str, Any]:
    """Binds a Clerk user_id to the workspace that invited their email.
    Deliberately takes plain strings rather than a Principal — this must
    never go through billing_for/ensure_principal, which would register the
    claimant as a Build Studio workspace member (see the metering split in
    ensure_execute_pool_available/record_execute_pool_usage below)."""
    email = str(email or "").strip().lower()
    with _locked_store("execute-grants:claims") as store:
        grant = store.get(EXECUTE_GRANT_NS, grant_id)
        if not isinstance(grant, dict):
            raise EntitlementError("execute_grant_not_found", 404)
        if grant.get("status") != "pending":
            raise EntitlementError("execute_grant_not_pending", 409)
        if grant.get("email") != email:
            raise EntitlementError("execute_grant_email_mismatch", 403)
        workspace_id = grant["workspace_id"]
        now_iso = _iso(_now())
        store.set(
            EXECUTE_GRANT_NS,
            grant_id,
            {**grant, "status": "claimed", "claimed_at": now_iso, "claimed_user_id": user_id},
        )
        store.set(
            EXECUTE_GRANT_BY_USER_NS,
            user_id,
            {"grant_id": grant_id, "workspace_id": workspace_id, "email": email, "claimed_at": now_iso},
        )
    return {"workspace_id": workspace_id, "status": "claimed"}


def list_claimed_grant_workspaces_for(user_id: str) -> list[dict[str, Any]]:
    """Every workspace this Clerk user_id holds a claimed (non-revoked)
    execute_grants row for — for Execute's personal/team context switcher,
    which needs every grant a person holds, not just the single "active"
    execute_grant_by_user pointer execute_pool_binding_for_user reads."""
    with _locked_store("execute-grants:by-claimant") as store:
        rows = [
            row for row in store.list(EXECUTE_GRANT_NS)
            if isinstance(row, dict) and row.get("status") == "claimed" and row.get("claimed_user_id") == user_id
        ]
    return [{"workspace_id": r["workspace_id"], "grant_id": r["grant_id"]} for r in rows]


def auto_claim_pending_grants_for(*, user_id: str, email: str) -> list[dict[str, Any]]:
    """Call right after a person signs into Execute: binds any pending
    execute_grants invited under their verified email to their Clerk
    user_id automatically. Replaces the old manual invite-code paste, which
    only existed because Execute and Build Studio used to be separate Clerk
    applications with no shared identity — now that they're one, the email
    match Clerk already verified is enough. Safe to call on every sign-in;
    a no-op once a matching grant is already claimed."""
    email = str(email or "").strip().lower()
    if not email:
        return []
    claimed: list[dict[str, Any]] = []
    with _locked_store("execute-grants:auto-claim") as store:
        pending = [
            row for row in store.list(EXECUTE_GRANT_NS)
            if isinstance(row, dict) and row.get("status") == "pending" and row.get("email") == email
        ]
        for grant in pending:
            now_iso = _iso(_now())
            updated = {**grant, "status": "claimed", "claimed_at": now_iso, "claimed_user_id": user_id}
            store.set(EXECUTE_GRANT_NS, grant["grant_id"], updated)
            store.set(
                EXECUTE_GRANT_BY_USER_NS,
                user_id,
                {"grant_id": grant["grant_id"], "workspace_id": grant["workspace_id"], "email": email, "claimed_at": now_iso},
            )
            claimed.append(updated)
    return claimed


def execute_chat_access_for(user_id: str, workspace_id: str) -> bool:
    """True if this Clerk user may run Execute chat against workspace_id —
    their own personal workspace, a workspace they hold a claimed grant for,
    or a Clerk org they're a real member of. Backs Execute's personal/team
    context switcher: the client names which context is active, but the
    server verifies it on every call rather than trusting that choice."""
    if workspace_id == personal_workspace_id(user_id):
        return True
    if any(g["workspace_id"] == workspace_id for g in list_claimed_grant_workspaces_for(user_id)):
        return True
    orgs = clerk_user_organizations(user_id)
    return bool(orgs) and any(o["workspace_id"] == workspace_id for o in orgs)


def execute_pool_binding_for_user(user_id: str) -> dict[str, Any] | None:
    """Internal-only lookup: is this Clerk user_id bound to a workspace's
    shared AI Usage Credits pool via a claimed Execute grant? O(1) so it's
    cheap to call on every Execute chat turn."""
    with _locked_store("execute-grants:lookup") as store:
        row = store.get(EXECUTE_GRANT_BY_USER_NS, user_id)
    return row if isinstance(row, dict) else None


def trial_expired(billing: dict[str, Any]) -> bool:
    """True only for a free-plan workspace past its trial window. Paid and
    development plans never expire regardless of trial_started_at."""
    plan = normalize_plan(billing)
    if plan != "free":
        return False
    trial_days = PLAN_LIMITS["free"]["trial_days"]
    if not trial_days:
        return False
    started = _positive_epoch(billing.get("trial_started_at"))
    if started is None:
        return False
    return time.time() - started > trial_days * 86400


def trial_ends_at(billing: dict[str, Any]) -> str | None:
    plan = normalize_plan(billing)
    trial_days = PLAN_LIMITS["free"]["trial_days"] if plan == "free" else None
    started = _positive_epoch(billing.get("trial_started_at"))
    if plan != "free" or not trial_days or started is None:
        return None
    return _iso(datetime.fromtimestamp(started + trial_days * 86400, UTC))


def compile_pool_for(principal: Principal) -> str:
    """"free", "starter", or "pro" — which router pool this workspace's plan
    compiles against (docs/PRD.md §11). Free trials get the multi-provider free-tier
    rotation; Starter and Pro each route to their own single designated provider,
    where compile quality directly determines skill reliability. Enterprise/
    Development still carry "premium" (unchanged) and fall back to any pool
    since no provider is tagged "premium" anymore."""
    limits = _limits_from_billing(billing_for(principal))
    return str(limits.get("compile_pool") or "free")


def byok_enabled_for(principal: Principal) -> bool:
    """True only for plans carrying the ``byok`` capability (Enterprise, or an
    explicit override) — the gate app/services/byok.py checks before letting a
    workspace store or use its own Azure OpenAI deployment."""
    return bool(_limits_from_billing(billing_for(principal)).get("byok"))


def analytics_retention_cutoff_ms(principal: Principal) -> int | None:
    """Epoch-ms cutoff below which telemetry is outside this plan's retention
    window — None means unlimited (Enterprise custom or a None override).
    A cutoff of "now" (Free's 0-day retention) means everything is filtered."""
    billing = billing_for(principal)
    limits = _limits_from_billing(billing)
    days = limits.get("analytics_retention_days")
    if days is None:
        return None
    return int((time.time() - int(days) * 86400) * 1000)


_OPS_TIER_RANK = {"none": 0, "basic": 1, "full": 2}


def ensure_ops_tier(principal: Principal, minimum: str) -> None:
    """Gates the ops surfaces the pilot feedback called out as what enterprises
    actually pay for — dashboard, drift detection, audit export (docs/PRD.md
    §11). ``minimum`` is "basic" or "full"; Free (ops_tier "none") is blocked
    from all of it, Starter ("basic") clears "basic" gates only."""
    billing = billing_for(principal)
    limits = _limits_from_billing(billing)
    have = _OPS_TIER_RANK.get(str(limits.get("ops_tier") or "none"), 0)
    need = _OPS_TIER_RANK.get(minimum, 0)
    if have < need:
        raise EntitlementError("ops_tier_required", 403)


def ensure_trial_active(principal: Principal) -> None:
    """Blocks *building* — compile, human-edit LLM calls, publish, installer
    upload — once a free trial has run past its 30 days. Deliberately not
    called from skill sync or telemetry ingest: execution is local and the
    cloud isn't in that path, so an already-installed machine keeps working
    even after the trial that built it has expired (docs/PRD.md §11)."""
    billing = billing_for(principal)
    if trial_expired(billing):
        raise EntitlementError("trial_expired", 402)


def ensure_seats_available(principal: Principal) -> None:
    """Blocks a brand-new member's first request once the workspace is
    already at its seat cap (docs/PRD.md §11). Existing members are
    unaffected even if a later downgrade puts the workspace over its new
    limit — same soft-lock shape as ensure_workflow_publishable. Uses
    billing_for_workspace (read-only) rather than billing_for, which would
    itself create the very membership row this check needs to run before.
    Clerk's live member count already includes the arriving member by the
    time they can authenticate, so the gate is `count > limit`, not `>=`."""
    billing = billing_for_workspace(principal.workspace_id)
    limit = _limits_from_billing(billing)["seats"]
    if limit is None or not settings.entitlements_enforce_seats:
        return
    count = _clerk_org_member_count(principal)
    if count is None:
        # Clerk lookup unavailable (local dev, transient API failure) — fail
        # open rather than lock everyone out of a workspace we can't measure.
        return
    if count > int(limit):
        raise EntitlementError("seat_limit_exceeded", 402)


def _meter(used: int, limit: int | None, *, reserved: int = 0) -> dict[str, Any]:
    if limit is None:
        return {"used": used, "limit": None, "remaining": None, "unlimited": True}
    remaining = max(0, int(limit) - int(used) - int(reserved))
    return {"used": used, "limit": int(limit), "remaining": remaining, "unlimited": False}


def _clerk_org_member_count(principal: Principal) -> int | None:
    if principal.auth_provider != "clerk" or not principal.workspace_id.startswith("org_"):
        return None
    secret = str(settings.clerk_secret_key or "").strip()
    if not secret:
        return None
    req = urllib.request.Request(
        f"https://api.clerk.com/v1/organizations/{quote(principal.workspace_id, safe='')}/memberships?limit=1"
    )
    req.add_header("Authorization", f"Bearer {secret}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        try:
            return max(1, int(payload.get("total_count")))
        except (TypeError, ValueError):
            data = payload.get("data")
            if isinstance(data, list):
                return max(1, len(data))
    return None


def current_entitlements(principal: Principal) -> dict[str, Any]:
    billing = billing_for(principal)
    plan = normalize_plan(billing)
    limits = _limits_from_billing(billing)
    period, reset_at = usage_window_for_billing(billing)
    workspace_id = principal.workspace_id
    with _locked_store(f"entitlements:{workspace_id}:{period}") as store:
        _expire_reservations(store, workspace_id, period)
        usage = _get_usage(store, workspace_id, period)
        reserved_compile = _active_reserved_amount(store, workspace_id, period)
    human_edit_used = _combined_pool_used(usage)
    execute_chat_used = int(usage.get("execute_chat_input_tokens") or 0) + int(
        usage.get("execute_chat_output_tokens") or 0
    )
    return {
        "workspace_id": workspace_id,
        "plan": plan,
        "period": period,
        "reset_at": reset_at,
        "trial_ends_at": trial_ends_at(billing),
        "trial_expired": trial_expired(billing),
        # One-time add-on purchases land here — a never-expiring balance drawn
        # down only once the plan's monthly allowance is exhausted.
        # "ai_usage_credits" dual-emits the same value as the legacy
        # "human_edit_tokens" key — see the meters block above for why.
        "wallet": {**_wallet(billing), "ai_usage_credits": _wallet(billing)["human_edit_tokens"]},
        "meters": {
            "seats": _meter(
                _clerk_org_member_count(principal) or membership_count_for(workspace_id),
                limits["seats"],
            ),
            "machines": _meter(machine_count(workspace_id), limits["machines"]),
            "execute_seats": _meter(execute_grant_count(workspace_id), limits["execute_seats"]),
            "compile_credits": _meter(
                int(usage.get("compile_credits_used") or 0),
                limits["compile_credits"],
                reserved=reserved_compile,
            ),
            # "human_edit_tokens" is the legacy wire name for this meter, kept
            # so already-installed Build Studio builds (Electron, auto-updates
            # asynchronously) don't break between this deploy and their next
            # update. "ai_usage_credits" is the canonical name going forward —
            # same value, new name. TODO(remove human_edit_tokens key once
            # MIN_HOST-equivalent Electron floor no longer predates this): drop
            # the legacy key.
            "human_edit_tokens": _meter(human_edit_used, limits["human_edit_tokens"]),
            "ai_usage_credits": _meter(human_edit_used, limits["human_edit_tokens"]),
            # Line-item breakdown only — execute_chat draws from the same pool/
            # limit as human_edit above, it is not a separate quota. "limit"
            # here is always None (unlimited) precisely because it isn't
            # separately capped; ai_usage_credits is the real gate.
            "execute_chat_tokens": _meter(execute_chat_used, None),
        },
        # The capability ladder (docs/PRD.md §11) — what this plan unlocks, not
        # just how much of it. Consumed by the pricing page, the dashboard nav,
        # and the installer/publish gates in app/api/installer_storage.py and
        # app/api/publish_routes.py.
        "capabilities": {
            "distribution": limits["distribution"],
            "white_label": limits["white_label"],
            "ops_tier": limits["ops_tier"],
            "compile_pool": limits["compile_pool"],
            "byok": limits["byok"],
            # Whether a compile that exhausts the whole vision-anchor provider
            # pool degrades to keyword anchors instead of hard-stopping. Build
            # Studio fetches this once per compile (handlers/compile.py) and
            # applies it locally — the compiler itself is local-only, this is
            # just the cloud-controlled policy value for it. A local env var
            # (SKILL_VISION_ANCHOR_FALLBACK_ON_EXHAUSTION) remains as a fallback
            # for dev / when this fetch itself fails.
            #
            # Deliberately NOT plan-gated (defaults False on every plan above,
            # including Enterprise) — this is an ops reliability lever, not a
            # paid feature. Conxa can flip it for any workspace on any plan,
            # Free included, via entitlement_overrides at any time.
            "vision_fallback_on_exhaustion": limits["vision_fallback_on_exhaustion"],
        },
        # Persistent workflow slot ledger — separate from the monthly
        # compile_credits meter above, which resets every period. This is what
        # a downgrade below the workspace's current published-workflow count
        # soft-locks against (see ensure_workflow_publishable).
        "workflow_lock": workflow_lock_status(principal),
    }


def reserve_compile_credit(
    principal: Principal,
    *,
    reservation_id: str,
    workflow_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    if not reservation_id.strip():
        raise EntitlementError("invalid_reservation_id", 400)
    billing = billing_for(principal)
    if trial_expired(billing):
        raise EntitlementError("trial_expired", 402)
    limits = _limits_from_billing(billing)
    period, _reset_at = usage_window_for_billing(billing)
    workspace_id = principal.workspace_id
    with _locked_store(f"compile-reserve:{workspace_id}:{period}") as store:
        _expire_reservations(store, workspace_id, period)
        usage = _get_usage(store, workspace_id, period)
        existing = store.get(RESERVATION_NS, reservation_id)
        if isinstance(existing, dict):
            if existing.get("workspace_id") != workspace_id:
                raise EntitlementError("compile_reservation_conflict", 409)
            reserved_amount = _active_reserved_amount(store, workspace_id, period)
            remaining = _remaining_compile(limits["compile_credits"], usage, reserved_amount)
            return {
                "reservation_id": reservation_id,
                "status": str(existing.get("status") or "reserved"),
                "remaining_compile_credits": remaining,
            }
        reserved_amount = _active_reserved_amount(store, workspace_id, period)
        limit = limits["compile_credits"]
        funded_by_wallet = False
        if (
            settings.entitlements_enforce_compile
            and limit is not None
            and int(usage.get("compile_credits_used") or 0) + reserved_amount + 1 > int(limit)
        ):
            # Monthly allowance exhausted — fall back to the never-expiring
            # wallet bought via one-time add-on packs. The actual deduction
            # happens at commit time (see commit_compile_credit); here we only
            # check affordability so released/expired reservations need no refund.
            funded_by_wallet = _wallet(billing)["compile_credits"] >= 1
            if not funded_by_wallet:
                raise EntitlementError("compile_credit_limit_exceeded", 402)
        row = _reservation_defaults(
            reservation_id=reservation_id,
            workspace_id=workspace_id,
            period=period,
            workflow_id=workflow_id,
            session_id=session_id,
        )
        if funded_by_wallet:
            row["funded_by"] = "wallet"
        _set_reservation(store, row)
        remaining = _remaining_compile(limit, usage, reserved_amount + 1)
    return {
        "reservation_id": reservation_id,
        "status": "reserved",
        "remaining_compile_credits": remaining,
    }


def _remaining_compile(
    limit: int | None,
    usage: dict[str, Any],
    reserved_amount: int,
) -> int | None:
    if limit is None:
        return None
    return max(0, int(limit) - int(usage.get("compile_credits_used") or 0) - reserved_amount)


def commit_compile_credit(principal: Principal, reservation_id: str) -> dict[str, Any]:
    billing = billing_for(principal)
    limits = _limits_from_billing(billing)
    period, _reset_at = usage_window_for_billing(billing)
    workspace_id = principal.workspace_id
    with _locked_store(f"compile-commit:{workspace_id}:{period}") as store:
        _expire_reservations(store, workspace_id, period)
        usage = _get_usage(store, workspace_id, period)
        row = store.get(RESERVATION_NS, reservation_id)
        if not isinstance(row, dict) or row.get("workspace_id") != workspace_id:
            raise EntitlementError("compile_reservation_not_found", 404)
        status = str(row.get("status") or "")
        if status == "committed":
            return {"reservation_id": reservation_id, "status": "committed"}
        if status != "reserved":
            raise EntitlementError("compile_reservation_not_reserved", 409)
        usage["compile_credits_used"] = int(usage.get("compile_credits_used") or 0) + int(
            row.get("amount") or 0
        )
        _set_usage(store, usage)
        row["status"] = "committed"
        _set_reservation(store, row)
        if str(row.get("funded_by") or "") == "wallet":
            # Draw the wallet outside the usage-store lock — the reservation was
            # already affordability-checked at reserve time; floored at 0 inside
            # _spend_wallet so a concurrent race can't go negative.
            _spend_wallet(workspace_id, compile_credits=max(1, int(row.get("amount") or 0)))
        remaining = _remaining_compile(
            limits["compile_credits"],
            usage,
            _active_reserved_amount(store, workspace_id, period),
        )
    return {"reservation_id": reservation_id, "status": "committed", "remaining_compile_credits": remaining}


def refund_compile_credit(principal: Principal, reservation_id: str) -> dict[str, Any]:
    """Committed -> refunded: give back a compile credit already committed and spent
    when the compile aborted on an infrastructure failure (cloud LLM proxy
    unavailable, vision anchor exhaustion) rather than anything the user did.
    The credit is committed before any LLM call (handlers/compile.py) so an
    infra failure otherwise burns it for nothing — deliberately narrow: call
    this only for infra-class aborts (CloudUnreachable/ProxyUnavailable/
    VisionAnchorGenerationError), never for a content/user error, or a partial
    refund policy becomes a free-compile lever.

    Uses the same lock key as commit_compile_credit — both mutate this period's
    usage["compile_credits_used"], so they need to serialize against each other."""
    billing = billing_for(principal)
    period, _reset_at = usage_window_for_billing(billing)
    workspace_id = principal.workspace_id
    with _locked_store(f"compile-commit:{workspace_id}:{period}") as store:
        row = store.get(RESERVATION_NS, reservation_id)
        if not isinstance(row, dict) or row.get("workspace_id") != workspace_id:
            raise EntitlementError("compile_reservation_not_found", 404)
        status = str(row.get("status") or "")
        if status == "refunded":
            return {"reservation_id": reservation_id, "status": "refunded"}
        if status != "committed":
            raise EntitlementError("compile_reservation_not_committed", 409)
        amount = max(1, int(row.get("amount") or 0))
        usage = _get_usage(store, workspace_id, period)
        usage["compile_credits_used"] = max(0, int(usage.get("compile_credits_used") or 0) - amount)
        _set_usage(store, usage)
        row["status"] = "refunded"
        _set_reservation(store, row)
        funded_by_wallet = str(row.get("funded_by") or "") == "wallet"
    if funded_by_wallet:
        # Outside the usage-store lock, mirroring commit_compile_credit's own
        # _spend_wallet call — the wallet is its own atomic unit.
        grant_credit_wallet(workspace_id, compile_credits=amount)
    return {"reservation_id": reservation_id, "status": "refunded"}


def release_compile_credit(principal: Principal, reservation_id: str) -> dict[str, Any]:
    billing = billing_for(principal)
    period, _reset_at = usage_window_for_billing(billing)
    workspace_id = principal.workspace_id
    with _locked_store(f"compile-release:{workspace_id}:{period}") as store:
        row = store.get(RESERVATION_NS, reservation_id)
        if not isinstance(row, dict) or row.get("workspace_id") != workspace_id:
            raise EntitlementError("compile_reservation_not_found", 404)
        status = str(row.get("status") or "")
        if status == "reserved":
            row["status"] = "released"
            _set_reservation(store, row)
            status = "released"
    return {"reservation_id": reservation_id, "status": status}


def _workflow_key(workspace_id: str, workflow_id: str) -> str:
    return f"{workspace_id}:{workflow_id}"


def _reconcile_workflow_locks(
    store: _FileKvStore | _SqlKvStore, workspace_id: str, limit: int | None
) -> list[dict[str, Any]]:
    """Keep the ``limit`` most-recently-published workflows active and lock the
    rest, oldest first. Re-run on every read so a plan change — upgrade,
    downgrade, or an admin override — takes effect on its own, without a
    separate downgrade migration step (the Pro→Starter soft-lock story,
    docs/PRD.md §11)."""
    rows = [
        row
        for row in store.list(WORKFLOW_NS)
        if isinstance(row, dict) and row.get("workspace_id") == workspace_id
    ]
    # created_at is second-resolution (_iso truncates microseconds for display),
    # so two workflows published in the same request — the common case, e.g.
    # `_publish(..., ["wf1", "wf2"])` — routinely tie on it. Break ties with
    # created_at_ns (nanosecond, sort-only, never shown) so lock order doesn't
    # depend on incidental KV-store enumeration order. Legacy rows written
    # before this field existed sort as 0 — they're already locked/unlocked
    # from a prior reconcile pass, so a stable placement here doesn't matter.
    rows.sort(key=lambda r: (str(r.get("created_at") or ""), r.get("created_at_ns") or 0))
    cutoff = max(0, len(rows) - int(limit)) if limit is not None else 0
    for index, row in enumerate(rows):
        should_lock = index < cutoff
        if bool(row.get("locked")) != should_lock:
            row["locked"] = should_lock
            store.set(
                WORKFLOW_NS,
                _workflow_key(workspace_id, str(row.get("workflow_id") or "")),
                row,
            )
    rows.sort(key=lambda r: (str(r.get("created_at") or ""), r.get("created_at_ns") or 0), reverse=True)
    return rows


def record_published_workflow(workspace_id: str, workflow_id: str) -> None:
    """Durable ledger entry for a published workflow. Unlike the monthly
    compile-credit meter this never resets, so a downgrade later has
    something stable to soft-lock the oldest excess against."""
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        return
    key = _workflow_key(workspace_id, workflow_id)
    with _locked_store(f"workflow-ledger:{workspace_id}") as store:
        if isinstance(store.get(WORKFLOW_NS, key), dict):
            return
        store.set(
            WORKFLOW_NS,
            key,
            {
                "workspace_id": workspace_id,
                "workflow_id": workflow_id,
                "created_at": _iso(_now()),
                "created_at_ns": time.time_ns(),
                "locked": False,
            },
        )


def ensure_workflow_publishable(principal: Principal, workflow_ids: list[str]) -> None:
    """Publish-time gate: a plan's compile-credit number doubles as the max
    number of workflows a workspace may keep active at once (docs/PRD.md §11).
    Republishing an already-active workflow (a new version) is always allowed;
    a locked workflow must wait for an upgrade to free room — there's no
    delete-to-free-a-slot flow yet; a brand-new workflow is blocked once the
    workspace is already at its cap."""
    billing = billing_for(principal)
    limits = _limits_from_billing(billing)
    limit = limits["compile_credits"]
    if limit is None or not settings.entitlements_enforce_compile:
        return
    workspace_id = principal.workspace_id
    with _locked_store(f"workflow-ledger:{workspace_id}") as store:
        rows = _reconcile_workflow_locks(store, workspace_id, limit)
        active = sum(1 for row in rows if not row.get("locked"))
        known = {
            str(row.get("workflow_id") or ""): row for row in rows
        }
        for raw_workflow_id in workflow_ids:
            workflow_id = str(raw_workflow_id or "").strip()
            if not workflow_id:
                continue
            existing = known.get(workflow_id)
            if existing is None:
                if active >= int(limit):
                    raise EntitlementError("workflow_limit_exceeded", 402)
                active += 1
            elif existing.get("locked"):
                raise EntitlementError("workflow_locked", 402)


def workflow_lock_status(principal: Principal) -> dict[str, Any]:
    """Dashboard view of the workflow ledger: active/locked counts plus the
    per-workflow rows, newest first — what a "300 workflows locked, upgrade to
    reactivate" banner reads from."""
    limits = _limits_from_billing(billing_for(principal))
    limit = limits["compile_credits"]
    workspace_id = principal.workspace_id
    with _locked_store(f"workflow-ledger:{workspace_id}") as store:
        rows = _reconcile_workflow_locks(store, workspace_id, limit)
    locked = sum(1 for row in rows if row.get("locked"))
    return {"limit": limit, "active": len(rows) - locked, "locked": locked, "workflows": rows}


def _record_llm_usage_for_workspace(
    workspace_id: str,
    billing: dict[str, Any],
    *,
    usage_class: str,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, Any]:
    limits = _limits_from_billing(billing)
    period, _reset_at = usage_window_for_billing(billing)
    requested_class = usage_class
    usage_class = normalize_usage_class(usage_class)
    with _locked_store(f"llm-usage:{workspace_id}:{period}") as store:
        usage = _get_usage(store, workspace_id, period)
        if usage_class in _POOL_USAGE_CLASSES:
            # ai_credits and execute_chat share one pool/quota/wallet — the
            # gate below checks their combined total; only the per-class
            # counter fields below are kept separate, for reporting.
            used = _combined_pool_used(usage)
            limit = limits["human_edit_tokens"]
            incoming = max(0, int(input_tokens)) + max(0, int(output_tokens))
            if settings.entitlements_enforce_human_edit and limit is not None and used >= int(limit):
                # Monthly pool exhausted — draw from the never-expiring wallet
                # (one-time add-on purchases) when it can cover this request.
                if not _spend_wallet(workspace_id, human_edit_tokens=incoming):
                    raise EntitlementError(pool_exceeded_code(requested_class), 402)
            usage[f"{usage_class}_input_tokens"] = int(usage.get(f"{usage_class}_input_tokens") or 0) + max(
                0, int(input_tokens)
            )
            usage[f"{usage_class}_output_tokens"] = int(usage.get(f"{usage_class}_output_tokens") or 0) + max(
                0, int(output_tokens)
            )
            usage[f"{usage_class}_requests"] = int(usage.get(f"{usage_class}_requests") or 0) + 1
        else:
            usage["compile_input_tokens"] = int(usage.get("compile_input_tokens") or 0) + max(
                0, int(input_tokens)
            )
            usage["compile_output_tokens"] = int(usage.get("compile_output_tokens") or 0) + max(
                0, int(output_tokens)
            )
            usage["compile_requests"] = int(usage.get("compile_requests") or 0) + 1
        _set_usage(store, usage)
    return usage


def record_llm_usage(
    principal: Principal,
    *,
    usage_class: str,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, Any]:
    usage_class = str(usage_class or "compile").strip()
    if usage_class not in ALLOWED_USAGE_CLASSES:
        raise EntitlementError("invalid_usage_class", 400)
    billing = billing_for(principal)
    return _record_llm_usage_for_workspace(
        principal.workspace_id,
        billing,
        usage_class=usage_class,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def record_execute_pool_usage(workspace_id: str, *, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    """Debits a Conxa Execute grantee's chat usage against their granting
    workspace's shared AI Usage Credits pool. Uses billing_for_workspace
    (read-only) rather than billing_for/a synthetic Principal — the latter
    would call ensure_principal and silently register the grantee as a Build
    Studio workspace member, inflating the workspace's own seats meter (see
    create_execute_grant / claim_execute_grant docstrings). Meters as
    usage_class="execute_chat" — same pool, same wallet fallback, same lock
    key as the Build Studio Human Edit / AI Usage Credits path, tracked as
    its own line item (see _combined_pool_used)."""
    billing = billing_for_workspace(workspace_id)
    return _record_llm_usage_for_workspace(
        workspace_id,
        billing,
        usage_class="execute_chat",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _ensure_human_edit_available_for_billing(
    billing: dict[str, Any], workspace_id: str, estimated_tokens: int = 0, usage_class: str = "ai_credits"
) -> None:
    limits = _limits_from_billing(billing)
    limit = limits["human_edit_tokens"]
    if limit is None:
        return
    period, _reset_at = usage_window_for_billing(billing)
    with _locked_store(f"human-edit-check:{workspace_id}:{period}") as store:
        usage = _get_usage(store, workspace_id, period)
    used = _combined_pool_used(usage)
    if settings.entitlements_enforce_human_edit and used >= int(limit):
        # Wallet fallback mirrors record_llm_usage — a positive never-expiring
        # balance keeps the AI Usage Credits pool open past the monthly
        # allowance.
        if _wallet(billing)["human_edit_tokens"] <= 0:
            raise EntitlementError(pool_exceeded_code(usage_class), 402)


def ensure_ai_credits_available(
    principal: Principal, *, estimated_tokens: int = 0, usage_class: str = "ai_credits"
) -> None:
    billing = billing_for(principal)
    _ensure_human_edit_available_for_billing(billing, principal.workspace_id, estimated_tokens, usage_class)


# Pre-CLOUD-25 name, kept so existing imports and tests keep working.
ensure_human_edit_available = ensure_ai_credits_available


def ensure_execute_pool_available(workspace_id: str, *, estimated_tokens: int = 0) -> None:
    """Pre-flight pool check for a Conxa Execute grantee, scoped to the
    granting workspace_id — the workspace-id-only twin of
    ensure_human_edit_available, see record_execute_pool_usage for why this
    must not go through a synthetic Principal."""
    billing = billing_for_workspace(workspace_id)
    _ensure_human_edit_available_for_billing(billing, workspace_id, estimated_tokens, "execute_chat")


def execute_pool_status(workspace_id: str) -> dict[str, Any]:
    """Read-only AI Usage Credits pool snapshot for a Conxa Execute grantee's
    'Paid by <workspace>' display — the workspace-id-only twin of the
    ai_usage_credits meter in current_entitlements. Never raises;
    ensure_execute_pool_available above remains the sole enforcement path."""
    billing = billing_for_workspace(workspace_id)
    limits = _limits_from_billing(billing)
    period, _reset_at = usage_window_for_billing(billing)
    with _locked_store(f"human-edit-check:{workspace_id}:{period}") as store:
        usage = _get_usage(store, workspace_id, period)
    used = _combined_pool_used(usage)
    meter = _meter(used, limits["human_edit_tokens"])
    return {**meter, "wallet_balance": _wallet(billing)["human_edit_tokens"]}


def ensure_distribution_allowed(principal: Principal, *, external: bool) -> None:
    """Gate the ladder's top rung: Free and Starter build internal-only
    installers; Pro and Enterprise may distribute externally (docs/PRD.md §11).
    Called from installer upload and publish — server-side, not just UI-hidden."""
    if not external:
        return
    billing = billing_for(principal)
    limits = _limits_from_billing(billing)
    if settings.entitlements_enforce_distribution and limits["distribution"] != "external":
        raise EntitlementError("distribution_not_permitted", 402)


def ensure_white_label_allowed(principal: Principal, *, custom_branding: bool) -> None:
    """White-label installer branding is an Enterprise-only capability."""
    if not custom_branding:
        return
    billing = billing_for(principal)
    limits = _limits_from_billing(billing)
    if settings.entitlements_enforce_distribution and not limits["white_label"]:
        raise EntitlementError("white_label_not_permitted", 402)


def get_installer_domain(workspace_id: str) -> str:
    """Unverified, workspace-supplied domain used to name paid-plan installers
    (see docs/PRD.md §11). No proof of ownership yet — see TODO.md PROD-6."""
    row = db_get(INSTALLER_DOMAIN_NS, workspace_id)
    return str(row.get("domain") or "") if isinstance(row, dict) else ""


def set_installer_domain(principal: Principal, domain: str) -> str:
    domain = str(domain or "").strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain).split("/", 1)[0]
    if not _DOMAIN_RE.match(domain):
        raise EntitlementError("invalid_domain", 400)
    db_set(INSTALLER_DOMAIN_NS, principal.workspace_id, {"workspace_id": principal.workspace_id, "domain": domain})
    return domain
