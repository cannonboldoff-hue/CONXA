# Manually Granting Paid Access (Comps / Support / Demos)

How to give a workspace Starter or Pro access from the backend without them paying — for sales comps, support fixes, or demos. This bypasses Cashfree entirely.

Two ways to do it: a direct Python call (run once, on the backend host) or an HTTP admin endpoint (for remote/deployed environments). Both end up calling the same function, `upsert_billing()`.

---

## 1. Find the workspace — or just use their email

The HTTP endpoint (option B below) now accepts the customer's **email** directly, so most of the time you can skip finding a `workspace_id` at all — just pass `"email": "customer@example.com"` instead of `"workspace_id"`.

This only works if they've logged into the Cloud Dashboard at least once (so we have a record of their email), and it 409s with the list of candidate workspace_ids if that email is somehow in more than one workspace — pass `workspace_id` explicitly in that case.

If you'd rather find the `workspace_id` yourself (the Clerk org id `org_...` for a team, or the user's personal-workspace id for an individual account):
- The Cloud Dashboard URL/Settings page while logged in as that user.
- Or query the backend's SaaS state for their email/membership record.

---

## 2. Pick a plan value and, optionally, a duration

Valid plan values (from `PLAN_LIMITS` in `conxa-cloud/backend/app/services/entitlements.py`):

- `free`
- `starter`
- `pro`
- `enterprise`

Anything else is rejected. Don't use `development` — that's reserved for local-auth workspaces, not a real grant.

**Duration** is optional, in days — pass it and the grant auto-expires on its own; omit it for a permanent grant:

| Grant | `duration_days` |
|---|---|
| 1 month | `30` |
| 3 months | `90` |
| 6 months | `180` |
| 1 year | `365` |
| Permanent | omit the field |

This is calendar-approximate (30-day "months"), not billing-cycle-exact — fine for comps, not meant to mimic a real subscription's renewal date.

---

## 3a. Option A — Direct Python call (run on the backend host)

From `conxa-cloud/backend/`, with `packages/conxa-core` on `PYTHONPATH` (same setup `pytest.ini` uses):

```bash
cd conxa-cloud/backend
python -c "
import time
from app.services.saas import upsert_billing

duration_days = 30  # 1 month — set to None for a permanent grant
expires_at = int(time.time() + duration_days * 86400) if duration_days else None

upsert_billing('<workspace_id>', {'plan': 'pro', 'status': 'active', 'plan_expires_at': expires_at})
"
```

- In **production**, this writes straight to Postgres through `conxa_core.db` — it's a real, immediate change, no restart needed.
- In **dev**, it writes the local filesystem JSON state store instead.
- Swap `'pro'` for `'starter'` (or `'free'` to revoke a comp later).
- Always pass `plan_expires_at` explicitly (even as `None`) — otherwise a workspace that previously had a timed grant keeps its old expiry.

---

## 3b. Option B — HTTP admin endpoint (for remote environments)

```bash
# by email — the common case
curl -X POST https://<host>/api/v1/entitlements/admin/billing \
  -H "Authorization: Bearer $CONXA_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email": "customer@example.com", "plan": "pro", "duration_days": 30}'

# by workspace_id — if you already have it, or the email was ambiguous
curl -X POST https://<host>/api/v1/entitlements/admin/billing \
  -H "Authorization: Bearer $CONXA_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id": "<workspace_id>", "plan": "pro", "duration_days": 30}'
```

- Auth is the same `CONXA_ADMIN_TOKEN` env var already used by the CI/publish admin endpoints — no new secret to provision.
- Pass exactly one of `email` or `workspace_id`.
- Drop `"duration_days"` entirely for a permanent grant.
- Returns the updated billing record, the resolved plan's limits, `workspace_id` (useful when you looked it up by email), and `expires_at` (Unix seconds, or `null` if permanent).
- 400 if the plan name is invalid or neither `email`/`workspace_id` is given, 401/503 if the token is missing or wrong, 404 if the email has never signed in, 409 if the email spans multiple workspaces (the error names them).

This is implemented in `conxa-cloud/backend/app/api/entitlement_routes.py` (`POST /entitlements/admin/billing`).

---

## What this does and doesn't do

- Takes effect immediately — the workspace's entitlement limits (seats, machines, compile credits, ops tier, etc.) update on their next `/entitlements/current` read.
- **Time-boxed grants expire themselves.** `entitlements.normalize_plan()` checks `plan_expires_at` on every read; once it passes, the workspace silently reads back as `free` — no cron job, no manual downgrade step. Nothing deletes the `pro`/`starter` value sitting in `billing.plan`; it's just ignored once expired, so re-granting later doesn't need to know the old plan.
- Does **not** create a Cashfree subscription. `current_period_end` (the separate field Cashfree owns) is left as-is, and there's no real recurring charge behind the grant.
- **To revoke a comp early, use the same method** (`plan: "free"`, no duration) — don't try to run it through the Cashfree cancel flow, since there's no real subscription for Cashfree to know about.
