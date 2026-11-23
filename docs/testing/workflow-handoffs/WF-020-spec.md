# WF-020 spec — Cloud API: bad tokens, plan limits, cross-company (source: 01 §WF-20)

## WF-20 — Cloud API: bad tokens, plan limits, cross-company

**No browser.** Local backend: `cd conxa-cloud/backend && uvicorn app.main:app --reload --port 8000`.
Use a **test workspace**, never a real customer's. Check
`GET /api/v1/entitlements/current` before and after each row. Change plan without paying through
`POST /api/v1/entitlements/admin/billing` with the admin token.

**Sign-in and isolation**

| # | Call | Must happen | If wrong |
|---|---|---|---|
| 20.1 | Protected route with no token; with a garbage token; with an expired token | 401, never 500 or a stack trace | S1 |
| 20.2 | Company A's token reading company B's packs, workflows, telemetry | 401/403 or empty; never B's data | S0 |
| 20.3 | Admin route with a normal user's token | Refused | S0 |
| 20.4 | LLM proxy without a token | Refused | S0 |
| 20.5 | Any route outside `/api/v1` other than `/api/tracking/{company}/events` | Does not exist | S1 |

**Plan limits** (each across Free → Starter → Pro → Enterprise; payment-shaped → 402,
permission → 403)

| # | Limit | Error | Must also hold |
|---|---|---|---|
| 20.6 | Compile credits | 402 `compile_credit_limit_exceeded` | Same id twice counts once; release returns; reservation expires |
| 20.7 | Human-edit token pool | 402 `human_edit_pool_exceeded` | Compile pool unaffected |
| 20.8 | Machines | 402 `machine_limit_exceeded` | Revoke frees a slot |
| 20.9 | Seats | 402 `seat_limit_exceeded` | Existing members keep working; note: fails open if Clerk is unreachable |
| 20.10 | Workflows | 402 `workflow_limit_exceeded` / `workflow_locked` | Republish at cap OK; downgrade locks oldest first; upgrade unlocks |
| 20.11 | Trial expiry | 402 `trial_expired` | Sync and tracking keep working |
| 20.12 | External distribution, white-label | 402 | Ladder as priced |
| 20.13 | Ops screens | **403** `ops_tier_required` | Basic on Starter, all on Pro+ |
| 20.14 | Timed grant, analytics retention, pool routing, BYOK, add-ons | As configured | Grant reverts with no job; add-on stacks and unstacks |
| 20.15 | Each `ENTITLEMENTS_ENFORCE_*` kill switch | Off disables only its gate | A fresh deploy with no env enforces everything (S0 if not) |
| 20.16 | Enterprise with no overrides | Behaves sanely, doesn't block everything | — |

**Failures go to:** `app/api/security.py`, entitlements owners. 20.2–20.4 → stop, security issue.

---
