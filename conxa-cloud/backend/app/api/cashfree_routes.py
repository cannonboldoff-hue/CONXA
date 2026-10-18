"""Cashfree billing endpoints — plans, subscriptions, one-time add-on orders,
payment links, and webhooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from conxa_core.config import settings
from conxa_core.db import db_get, db_set
from app.api.deps import current_principal
from app.services.entitlements import (
    ADDON_TIERS,
    PLAN_LIMITS,
    grant_credit_wallet,
)
from app.services.rbac import require_admin
from app.services.saas import Principal, upsert_billing

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

# Cashfree Subscriptions v1 API (subscription-plans / subscriptions/nonSeamless)
_CF_BASE = {
    "PROD": "https://api.cashfree.com",
    "TEST": "https://sandbox.cashfree.com",
}


def _plan_features(tier: str) -> list[str]:
    """Derive the human-readable feature list from the single source of truth
    (``PLAN_LIMITS`` in the entitlements service) so pricing copy can never drift
    from what is actually enforced. Order mirrors the capability ladder in
    docs/PRD.md §11: reach (seats/machines), spend (credits/tokens), then what
    the plan unlocks (distribution, ops depth, retention, BYOK)."""
    limits = PLAN_LIMITS.get(tier, {})

    # Enterprise carries 0 for the numeric limits because they come from contractual
    # overrides in billing metadata, not from this table. Rendering that 0 literally put
    # "0 seats" on the public pricing cards, which reads as a broken tier rather than a
    # negotiated one — so 0 means "agreed per contract" here, never a real ceiling.
    def _seat(n: int | None) -> str:
        if n is None:
            return "Unlimited seats"
        return "Seats agreed per contract" if n == 0 else f"{n} seat" + ("s" if n != 1 else "")

    def _machine(n: int | None) -> str:
        if n is None:
            return "Unlimited machines"
        return "Machines agreed per contract" if n == 0 else f"{n} machine" + ("s" if n != 1 else "")

    def _credits(n: int | None) -> str:
        return "Unlimited compile credits/month" if n is None else f"{n} compile credits/month"

    def _tokens(n: int | None) -> str:
        if n is None:
            return "Unlimited AI credits/month"
        if n == 0:
            return "Contracted AI credits reserve"
        if n >= 1_000_000:
            return f"{n // 1_000_000}M AI credits/month"
        return f"{n // 1_000}K AI credits/month"

    def _distribution(dist: str, white_label: bool) -> str:
        if dist != "external":
            return "Internal distribution only"
        return "External distribution, white-label" if white_label else "External distribution, Conxa-branded"

    def _ops(ops_tier: str) -> str:
        return {"none": "No ops dashboard", "basic": "Basic dashboard", "full": "Full dashboard, drift detection, audit export"}.get(
            ops_tier, "No ops dashboard"
        )

    def _retention(n: int | None) -> str:
        if n is None:
            return "Custom analytics retention"
        return "No analytics retention" if n == 0 else f"{n}-day analytics retention"

    features = [
        _seat(limits.get("seats")),
        _machine(limits.get("machines")),
        _credits(limits.get("compile_credits")),
        _tokens(limits.get("human_edit_tokens")),
        _distribution(str(limits.get("distribution") or "internal"), bool(limits.get("white_label"))),
        _ops(str(limits.get("ops_tier") or "none")),
        _retention(limits.get("analytics_retention_days")),
    ]
    if limits.get("byok"):
        features.append("Bring your own key (Azure OpenAI)")
    return features


TIER_INFO = {
    "free": {
        "name": "Free",
        "amount": 0,
        "currency": "INR",
        "period": None,
        "features": _plan_features("free"),
    },
    "starter": {
        "name": "Starter",
        "amount": 19_999,  # INR (Cashfree uses actual rupees, not paise)
        "currency": "INR",
        "period": "monthly",
        "features": _plan_features("starter"),
    },
    "pro": {
        "name": "Pro",
        "amount": 49_999,  # INR
        "currency": "INR",
        "period": "monthly",
        "features": _plan_features("pro"),
    },
    "enterprise": {
        "name": "Enterprise",
        "amount": 99_999,  # INR — floor of a negotiated, custom price
        "currency": "INR",
        "period": "monthly",
        "features": _plan_features("enterprise"),
    },
}


def _normalize_tier(tier: str) -> str:
    value = str(tier or "").strip().lower()
    return "starter" if value == "basic" else value


def _cf_base() -> str:
    env = (settings.cashfree_env or "TEST").upper()
    return _CF_BASE.get(env, _CF_BASE["TEST"])


def _cf_headers() -> dict[str, str]:
    if not settings.cashfree_app_id or not settings.cashfree_secret_key:
        raise HTTPException(status_code=500, detail="Cashfree credentials not configured")
    return {
        "X-Client-Id": settings.cashfree_app_id,
        "X-Client-Secret": settings.cashfree_secret_key,
        "Content-Type": "application/json",
    }


def _cf_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    url = f"{_cf_base()}{path}"
    with httpx.Client(timeout=30.0) as client:
        return client.request(method, url, headers=_cf_headers(), **kwargs)


def _configured_plan_id(tier: str) -> str:
    if tier == "starter":
        return settings.cashfree_starter_plan_id.strip()
    if tier == "pro":
        return settings.cashfree_pro_plan_id.strip()
    # Add-on packs are one-time Payment Link purchases — no plan IDs involved.
    return ""


def _plan_store_path() -> Path:
    return settings.data_dir / "cashfree_plans.json"


def _read_plan_store() -> dict[str, str]:
    data = db_get("cashfree", "plans")
    if data is not None:
        return data
    path = _plan_store_path()
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _write_plan_store(store: dict[str, str]) -> None:
    db_set("cashfree", "plans", store)
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _plan_store_path().write_text(json.dumps(store, indent=2))
    except OSError:
        pass


def _plan_store_key(tier: str) -> str:
    info = TIER_INFO[tier]
    return f"{tier}:{info['currency']}:{info['amount']}"


def _tier_from_plan_store_key(key: str) -> str:
    return _normalize_tier(str(key).split(":", 1)[0])


def _tier_for_plan_id(plan_id: str) -> str | None:
    if not plan_id:
        return None
    configured = {
        "starter": settings.cashfree_starter_plan_id.strip(),
        "pro": settings.cashfree_pro_plan_id.strip(),
    }
    for tier, configured_plan_id in configured.items():
        if configured_plan_id and plan_id == configured_plan_id:
            return tier
    for key, stored_plan_id in _read_plan_store().items():
        if plan_id == stored_plan_id:
            tier = _tier_from_plan_store_key(key)
            if tier in TIER_INFO and tier != "free":
                return tier
    return None


def _parse_next_charge(subscription: dict[str, Any]) -> int | None:
    """Parse next charge timestamp from Cashfree subscription (YYYY-MM-DD HH:MM:SS format)."""
    for key in ("nextChargeOn", "expiresOn", "next_payment_time"):
        value = subscription.get(key)
        if not value:
            continue
        try:
            # Cashfree v1 format: "2024-02-01 00:00:00"
            dt = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            try:
                # ISO format fallback
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return int(dt.timestamp())
            except (ValueError, AttributeError):
                pass
    return None


def _exception_detail(exc: Exception) -> str:
    message = str(exc).strip()
    if message and message.lower() != "none":
        return message
    args = [str(arg).strip() for arg in getattr(exc, "args", ()) if arg is not None and str(arg).strip()]
    if args:
        return "; ".join(args)
    return exc.__class__.__name__


def _ensure_plan(tier: str) -> str:
    """Create or retrieve Cashfree plan ID for tier. Returns plan_id."""
    tier = _normalize_tier(tier)
    if tier not in TIER_INFO or tier == "free":
        raise HTTPException(status_code=400, detail=f"invalid tier: {tier}")
    configured_plan_id = _configured_plan_id(tier)
    if configured_plan_id:
        return configured_plan_id
    if settings.auth_required:
        raise HTTPException(
            status_code=500,
            detail=f"Cashfree {tier} plan ID not configured",
        )
    store = _read_plan_store()
    plan_key = _plan_store_key(tier)
    if plan_key in store:
        return store[plan_key]
    info = TIER_INFO[tier]
    plan_id = f"conxa_{tier}_monthly"
    try:
        resp = _cf_request("POST", "/api/v2/subscription-plans", json={
            "planId": plan_id,
            "planName": f"Conxa {info['name']} Plan",
            "type": "PERIODIC",
            "recurringAmount": info["amount"],
            "maxAmount": info["amount"],
            "intervals": 1,
            "intervalType": "MONTH",
        })
        body = resp.json() if resp.content else {}
        if resp.status_code not in (200, 201) and body.get("status") != "OK":
            # 409 means plan already exists — treat as success
            if resp.status_code != 409:
                logger.error("cashfree_plan_create_failed tier=%s response=%s", tier, resp.text)
                raise HTTPException(status_code=500, detail="cashfree_plan_create_failed")
        store[plan_key] = plan_id
        _write_plan_store(store)
        return plan_id
    except HTTPException:
        raise
    except Exception as exc:
        detail = _exception_detail(exc)
        logger.exception("cashfree_plan_create_failed tier=%s error=%s", tier, detail)
        raise HTTPException(status_code=500, detail="cashfree_plan_create_failed") from exc


@router.get("/plans")
def list_plans() -> dict[str, Any]:
    """Return the four public tiers with features and pricing. This is what the
    marketing pricing page renders — deriving straight from TIER_INFO (itself
    built from PLAN_LIMITS via _plan_features) is what keeps the public price
    sheet from ever drifting out of sync with what's actually enforced. The
    add-on plan is deliberately excluded — it's a checkout item, not a tier."""
    return {
        "plans": [
            {"tier": tier, **TIER_INFO[tier]}
            for tier in ("free", "starter", "pro", "enterprise")
        ]
    }


@router.post("/create")
async def create_subscription(
    body: dict[str, str],
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Create a Cashfree subscription for a tier. Returns subscription_id and auth_link."""
    require_admin(principal)
    tier = _normalize_tier(body.get("tier", ""))
    if tier not in ("starter", "pro"):
        raise HTTPException(status_code=400, detail="tier must be 'starter' or 'pro'")
    try:
        plan_id = _ensure_plan(tier)
        info = TIER_INFO[tier]
        sub_id = f"conxa_{principal.workspace_id}_{tier}_{int(time.time())}"
        # The frontend never sends customer_email/customer_phone today (see
        # cashfreeApi.ts's single-argument createCashfreeSubscription call) — every
        # real subscription was silently created under a fake "user@conxa.in"
        # identity. Source the email from the signed-in principal (Clerk-verified)
        # instead of fabricating one; require it explicitly rather than guess.
        customer_email = body.get("customer_email") or principal.email
        if not customer_email:
            raise HTTPException(status_code=422, detail="customer_email_required")
        # ponytail: no real phone number source exists anywhere in this system
        # yet (Principal carries no phone field, Clerk's session doesn't require
        # one). Cashfree's API requires a value, so this stays a placeholder
        # until checkout collects a real one — add when Cashfree checkout gets
        # its own phone-collection step.
        customer_phone = body.get("customer_phone") or "9999999999"
        return_url = f"{settings.app_url}/billing"
        resp = _cf_request("POST", "/api/v2/subscriptions/nonSeamless/subscription", json={
            "subscriptionId": sub_id,
            "planId": plan_id,
            "authAmount": info["amount"],
            "customerEmail": customer_email,
            "customerPhone": customer_phone,
            "customerName": principal.workspace_id,
            "returnUrl": return_url,
            "expiresOn": "2099-12-31 00:00:00",
            "notificationChannels": ["EMAIL"],
        })
        if resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=500,
                detail=f"cashfree_subscription_create_failed: {resp.text}",
            )
        body_json = resp.json() if resp.content else {}
        data = body_json.get("data") or {}
        sub_reference_id = str(data.get("subReferenceId") or "")
        if not sub_reference_id:
            raise HTTPException(status_code=500, detail=f"cashfree_no_reference_id: {resp.text}")
        # Webhooks only carry Cashfree's subReferenceId (cf_planId is not a documented
        # webhook field), so remember workspace/tier here for lookup on webhook delivery.
        db_set("cashfree_sub_workspace", sub_reference_id, {
            "workspace_id": principal.workspace_id,
            "tier": tier,
        })
        return {
            "subscription_id": sub_reference_id,
            "auth_link": data.get("authLink", ""),
            "plan_id": plan_id,
            "amount": info["amount"],
            "currency": info["currency"],
            "tier": tier,
        }
    except HTTPException:
        raise
    except Exception as exc:
        detail = _exception_detail(exc)
        logger.exception(
            "cashfree_subscription_create_failed workspace_id=%s tier=%s error=%s",
            principal.workspace_id,
            tier,
            detail,
        )
        raise HTTPException(status_code=500, detail=f"subscription_error: {detail}") from exc


@router.post("/verify")
async def verify_subscription(
    body: dict[str, str],
    principal: Principal = Depends(current_principal),
) -> dict[str, bool]:
    """Verify subscription status via Cashfree API and update billing record."""
    subscription_id = body.get("subscription_id", "")
    if not subscription_id:
        raise HTTPException(status_code=400, detail="missing_fields")
    if not settings.cashfree_app_id:
        raise HTTPException(status_code=500, detail="Cashfree credentials not configured")
    try:
        resp = _cf_request(
            "GET",
            f"/api/v2/subscriptions/{subscription_id}",
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"subscription_fetch_failed: {resp.text}")
        body_json = resp.json() if resp.content else {}
        subscription = body_json.get("subscription") or body_json
        status = str(
            subscription.get("status") or subscription.get("subStatus")
            or subscription.get("subscriptionStatus") or ""
        ).upper()
        plan_id = str(subscription.get("planId") or "")
        tier = _tier_for_plan_id(plan_id)
        if not tier or tier == "free":
            raise HTTPException(status_code=400, detail="unknown_plan")
        upsert_billing(principal.workspace_id, {
            "plan": tier,
            "status": "active",
            "subscription_id": subscription_id,
            "current_period_end": _parse_next_charge(subscription),
        })
        logger.info(
            "cashfree_subscription_verified workspace_id=%s tier=%s status=%s",
            principal.workspace_id,
            tier,
            status,
        )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"verify_error: {_exception_detail(exc)}") from exc


@router.get("/addons")
def list_addons() -> dict[str, Any]:
    """Public add-on catalog for the Billing page. Add-ons are one-time
    purchases, deliberately excluded from /plans — this is their dedicated read side."""
    return {
        "addons": [
            {
                "tier": tier,
                "name": info["name"],
                "amount": info["amount"],
                "currency": info["currency"],
                "period": "one-time",
                "features": [
                    f"+{info['compile_credits']} compile credits (never expire)",
                    f"+{info['human_edit_tokens']:,} AI credits (never expire)",
                    "Used after your monthly allowance runs out",
                ],
            }
            for tier, info in sorted(
                ADDON_TIERS.items(), key=lambda kv: kv[1]["compile_credits"]
            )
        ]
    }


_ORDER_WORKSPACE_NS = "cashfree_order_workspace"
_ORDERS_GRANTED_NS = "cashfree_orders_granted"


def _grant_addon_order_once(order_id: str) -> dict[str, Any] | None:
    """Credit the wallet for a paid one-time add-on order exactly once. The
    granted-orders guard makes webhook replays and verify-after-webhook double
    deliveries harmless; returns the grant info or None when already granted."""
    order_id = str(order_id or "").strip()
    if not order_id:
        return None
    if db_get(_ORDERS_GRANTED_NS, order_id):
        return None
    mapping = db_get(_ORDER_WORKSPACE_NS, order_id) or {}
    tier = _normalize_tier(str(mapping.get("tier") or ""))
    workspace_id = str(mapping.get("workspace_id") or "")
    info = ADDON_TIERS.get(tier)
    if not workspace_id or not info:
        logger.warning("cashfree_addon_order_unmapped order_id=%s", order_id)
        return None
    db_set(_ORDERS_GRANTED_NS, order_id, {"workspace_id": workspace_id, "tier": tier})
    wallet = grant_credit_wallet(
        workspace_id,
        compile_credits=info["compile_credits"],
        human_edit_tokens=info["human_edit_tokens"],
    )
    logger.info(
        "cashfree_addon_order_granted workspace_id=%s tier=%s order_id=%s",
        workspace_id,
        tier,
        order_id,
    )
    return {"workspace_id": workspace_id, "tier": tier, "wallet": wallet}


@router.post("/addon/order")
async def create_addon_order(
    body: dict[str, str],
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """One-time purchase of a compile add-on pack via a Cashfree Payment Link.
    Returns {payment_link} for the frontend to redirect to. In local dev with
    no Cashfree credentials and auth off, grants immediately so the flow is
    testable end to end without hitting Cashfree."""
    require_admin(principal)
    tier = _normalize_tier(body.get("tier", ""))
    if tier not in ADDON_TIERS:
        raise HTTPException(status_code=400, detail="unknown_addon_tier")
    info = ADDON_TIERS[tier]

    if not settings.cashfree_app_id or not settings.cashfree_secret_key:
        if settings.auth_required:
            raise HTTPException(status_code=500, detail="Cashfree credentials not configured")
        wallet = grant_credit_wallet(
            principal.workspace_id,
            compile_credits=info["compile_credits"],
            human_edit_tokens=info["human_edit_tokens"],
        )
        return {
            "granted": True,
            "link_id": "",
            "payment_link": "",
            "tier": tier,
            "wallet": wallet,
        }

    customer_email = body.get("customer_email") or principal.email
    if not customer_email:
        raise HTTPException(status_code=422, detail="customer_email_required")
    # ponytail: see the matching comment in create_subscription — no real phone
    # number source exists yet.
    customer_phone = body.get("customer_phone") or "9999999999"
    link_id = f"conxa_{principal.workspace_id}_{tier}_{int(time.time())}"
    try:
        resp = _cf_request("POST", "/pg/links", json={
            "link_id": link_id,
            "link_amount": info["amount"],
            "link_currency": info["currency"],
            "link_purpose": f"Conxa {info['name']}",
            "customer_details": {
                "customer_email": customer_email,
                "customer_phone": customer_phone,
                "customer_name": principal.workspace_id,
            },
            "link_meta": {"return_url": f"{settings.app_url}/billing"},
            "link_notify": {"send_email": False, "send_sms": False},
        })
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"cashfree_addon_order_failed: {_exception_detail(exc)}",
        ) from exc
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"cashfree_addon_order_failed: {resp.text}",
        )
    data = resp.json() if resp.content else {}
    payment_link = str(data.get("link_url") or "")
    if not payment_link:
        raise HTTPException(status_code=502, detail=f"cashfree_no_payment_link: {resp.text}")
    # Webhooks carry our link_id back in data.link.link_id — remember which
    # workspace/tier it belongs to for lookup at grant time.
    db_set(_ORDER_WORKSPACE_NS, link_id, {
        "workspace_id": principal.workspace_id,
        "tier": tier,
    })
    return {"granted": False, "link_id": link_id, "payment_link": payment_link, "tier": tier}


@router.post("/addon/verify")
async def verify_addon_order(
    body: dict[str, str],
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Verify a one-time add-on Payment Link's status after the redirect back
    from Cashfree, granting the wallet credits on PAID — belt to the webhook's
    braces, since webhooks can't reach a locally-running backend."""
    link_id = str(body.get("link_id") or "").strip()
    if not link_id:
        raise HTTPException(status_code=400, detail="missing_fields")
    mapping = db_get(_ORDER_WORKSPACE_NS, link_id) or {}
    if str(mapping.get("workspace_id") or "") != principal.workspace_id:
        raise HTTPException(status_code=403, detail="addon_order_forbidden")
    try:
        resp = _cf_request("GET", f"/pg/links/{link_id}")
    except Exception as exc:
        logger.exception("cashfree_addon_verify_failed link_id=%s error=%s", link_id, _exception_detail(exc))
        raise HTTPException(status_code=500, detail="cashfree_addon_verify_failed") from exc
    if resp.status_code != 200:
        logger.error("cashfree_addon_verify_failed link_id=%s response=%s", link_id, resp.text)
        raise HTTPException(status_code=502, detail="cashfree_addon_verify_failed")
    data = resp.json() if resp.content else {}
    status = str(data.get("link_status") or "").upper()
    granted = None
    if status == "PAID":
        granted = _grant_addon_order_once(link_id)
    return {"status": status, "granted": bool(granted), "tier": mapping.get("tier", "")}


def _cf_webhook_signature(payload: dict[str, Any], secret: str) -> str:
    """Cashfree subscriptions v1 webhook signature: sort cf_-prefixed fields
    alphabetically, concatenate key+value with no delimiter, HMAC-SHA256 with
    the webhook secret, base64-encode."""
    cf_fields = {k: v for k, v in payload.items() if k.startswith("cf_")}
    message = "".join(f"{k}{payload[k]}" for k in sorted(cf_fields))
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


@router.post("/webhooks/cashfree")
async def handle_cashfree_webhook(request: Request) -> dict[str, bool]:
    """Handle Cashfree webhook events for subscriptions."""
    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid_json") from None

    if settings.cashfree_webhook_secret:
        expected = _cf_webhook_signature(payload, settings.cashfree_webhook_secret)
        received_sig = payload.get("signature", "")
        if received_sig and not hmac.compare_digest(expected, received_sig):
            raise HTTPException(status_code=400, detail="invalid_signature")

    event_type = payload.get("cf_event", "")
    sub_reference_id = str(payload.get("cf_subReferenceId") or "")
    subscription_id = str(payload.get("cf_subscriptionId") or "")
    plan_id = str(payload.get("cf_planId") or "")
    status = str(
        payload.get("cf_status") or payload.get("cf_subscriptionStatus") or ""
    ).upper()
    mapping = db_get("cashfree_sub_workspace", sub_reference_id) or {}
    workspace_id = mapping.get("workspace_id", "")

    if event_type in ("SUBSCRIPTION_STATUS_CHANGE", "SUBSCRIPTION_NEW_PAYMENT"):
        if workspace_id and sub_reference_id and (
            event_type == "SUBSCRIPTION_NEW_PAYMENT" or status == "ACTIVE"
        ):
            try:
                tier = _tier_for_plan_id(plan_id) or _normalize_tier(mapping.get("tier", ""))
                if tier and tier != "free":
                    upsert_billing(
                        workspace_id,
                        {
                            "plan": tier,
                            "status": "active",
                            "subscription_id": sub_reference_id,
                            "current_period_end": _parse_next_charge(payload),
                        },
                    )
            except Exception as exc:
                logger.exception(
                    "cashfree_webhook_billing_update_failed event=%s workspace_id=%s",
                    event_type,
                    workspace_id,
                )
                # A 200 here tells Cashfree the event was handled — it never
                # retries a 2xx. Returning it after the actual billing update
                # failed silently drops the event: the customer paid, and their
                # plan never upgraded, with nothing to prompt a retry. A non-2xx
                # makes Cashfree's own retry schedule do the recovery.
                raise HTTPException(status_code=502, detail="webhook_billing_update_failed") from exc
    elif event_type == "SUBSCRIPTION_PAYMENT_CANCELLED" or (
        event_type == "SUBSCRIPTION_STATUS_CHANGE" and status in ("CANCELLED", "EXPIRED", "ONHOLD")
    ):
        if workspace_id:
            try:
                upsert_billing(
                    workspace_id,
                    {
                        "plan": "free",
                        "status": "inactive",
                        "current_period_end": None,
                    },
                )
            except Exception as exc:
                logger.exception(
                    "cashfree_webhook_cancel_failed event=%s workspace_id=%s",
                    event_type,
                    workspace_id,
                )
                raise HTTPException(status_code=502, detail="webhook_cancel_failed") from exc
    else:
        logger.info(
            "cashfree_webhook_ignored event=%s subscription_id=%s status=%s",
            event_type,
            subscription_id,
            status,
        )

    return {"received": True}


def _cf_order_webhook_signature(timestamp: str, raw_body: bytes, secret: str) -> str:
    """Cashfree PG webhook signature: HMAC-SHA256 over ``timestamp + raw body``,
    base64-encoded (different scheme from the subscriptions v1 webhooks above)."""
    digest = hmac.new(secret.encode(), timestamp.encode() + raw_body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


@router.post("/webhooks/cashfree-orders")
async def handle_cashfree_order_webhook(request: Request) -> dict[str, bool]:
    """Cashfree PG webhooks for one-time add-on Payment Link orders. A
    PAYMENT_SUCCESS_WEBHOOK credits the workspace wallet exactly once
    (_grant_addon_order_once guards against replayed deliveries)."""
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid_json") from None

    if settings.cashfree_webhook_secret:
        timestamp = request.headers.get("x-webhook-timestamp", "")
        received_sig = request.headers.get("x-webhook-signature", "")
        expected = _cf_order_webhook_signature(
            timestamp, raw_body, settings.cashfree_webhook_secret
        )
        if not received_sig or not hmac.compare_digest(expected, received_sig):
            raise HTTPException(status_code=400, detail="invalid_signature")

    event_type = str(payload.get("type") or "")
    data = payload.get("data") or {}
    link = data.get("link") or {}
    payment = data.get("payment") or {}
    order_id = str(link.get("link_id") or payment.get("order_id") or "")

    if event_type == "PAYMENT_SUCCESS_WEBHOOK" and order_id:
        try:
            _grant_addon_order_once(order_id)
        except Exception as exc:
            logger.exception("cashfree_order_webhook_grant_failed order_id=%s", order_id)
            # Same reasoning as the subscription webhook above: a 200 here means
            # Cashfree never retries, and the customer paid for credits that
            # were never granted.
            raise HTTPException(status_code=502, detail="webhook_grant_failed") from exc
    else:
        logger.info(
            "cashfree_order_webhook_ignored type=%s order_id=%s",
            event_type,
            order_id,
        )

    return {"received": True}
