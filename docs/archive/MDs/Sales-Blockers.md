# Conxa — Sales Blockers & Implementation Roadmap

**Date:** 2026-07-04 (reviewed 2026-09-02 — BYOK, seat enforcement, Fleet/Machine Registry, and the
PROD-3 safe-action system all shipped since the last review; code-signing remains the sole open
blocker, see below)  
**Purpose:** Define what code must ship before Conxa can close its first enterprise customer.

---

## The Two Blockers That Actually Matter Now (2026-08-08)

Following the Centelon pilot demo (7 Aug 2026 — see `Conxa-Pilot-Conclusions.pdf`, internal), the
product's blockers split into two different kinds, and this doc only owns one of them:

1. **Code signing is still the engineering blocker, and it just got sharper.** The pricing/capability
   ladder now puts external distribution — the "ship it to your customers" rung — on Pro and
   Enterprise, and Enterprise additionally gets white-label installers (`docs/PRD.md` §11). Distribution
   is the entire point of those tiers, and distribution is exactly what an unsigned `.exe` breaks.
   Nothing here changed about the fix: it's still a ~$200/year certificate purchase with zero
   remaining engineering work (§2.5 below).
2. **The ICP is a hypothesis, and no amount of code fixes that.** "A pricing ladder is not evidence
   anyone will climb it. We have decided what to charge for. Nobody has said yes." That gets answered
   by the follow-up conversation with Centelon and the qualification checklist below — not by this
   document, and not by more engineering. Track it in `docs/Implementation-Plan.md`, not here.

Use the **Workflow Qualification Checklist** (new, from the pilot) before promising any customer that a
workflow can be automated — MFA policy, bot protection, terms of service, session lifetime. Better to
disqualify in week one than fail in week six:

| Check | What kills the deal | Notes |
|---|---|---|
| MFA policy | A fresh OTP on every login | The one genuine blocker — can't be worked around, don't try |
| Bot protection / CAPTCHA / IP allowlisting | Aggressive detection built for adversarial traffic | Mostly irrelevant for line-of-business software (ERPs, internal CRMs, loan origination, vendor portals) — nobody puts bot protection on internal LOB software. Bites consumer platforms, which is why they're gone from the marketing site |
| Terms of service | A clause banning automated access | Check before demoing, not after |
| Session lifetime | How long an authenticated session lasts unattended | Determines whether the workflow can run truly unattended vs. needs a human nearby |

See `docs/PRD.md`'s "Workflow Qualification Checklist" section for the full reasoning.

---

## What Shipped Since the Last Review (2026-08-08 → 2026-09-02)

None of this changes the blocker list — code signing is still the only critical-path item — but it
closes several objections a buyer's security/procurement review would otherwise raise:

- **Bring-your-own-key (Enterprise BYOK).** Enterprise customers can now point Conxa's compile-time
  LLM calls at their own Azure OpenAI key instead of Conxa's pooled providers — the key is stored
  AES-256-GCM encrypted at rest. Answers the "our data can't touch a third-party model pool" data-
  residency objection that regulated buyers (finance, HR, payroll) tend to raise. `docs/Implementation-Plan.md` §"Pricing model".
- **Per-plan LLM compile pools.** Starter and Pro now each get their own provider/model pool instead
  of sharing one, so plan tiers actually differ in automation quality, not just price on paper.
- **Seat limits enforced.** A new member's first request is checked against the plan's seat cap
  instead of billing silently drifting from what was sold.
- **Safe-action system (PROD-3) — the "won't touch the wrong record" pitch is now real, not
  asserted.** Destructive steps (pay, delete, submit) are classified at record time, scoped to the
  one record they were bound to, and self-repair without ever substituting a different-looking
  button — shipped 2026-08-29. This is the concrete answer to "how do we know it won't delete the
  wrong invoice," which is the first question every finance/HR/payroll prospect asks.
- **Governance & audit hardening (PROD-18).** Tamper-resistant audit trail and run-time policy
  windows/deny-lists shipped (an explicit require-approval step is still deferred — not required for
  a first sale). Strengthens the same security-review artifact as the existing audit log (2.3 below).
- **Terms & Privacy acceptance gate (PROD-17), recorded server-side.** Removes an "did they actually
  agree to this" gap a legal review would flag.
- **Company Agent Fleet dashboard (Machine Registry).** Runtime identity, a paginated list of every
  machine running Conxa, and a revoke endpoint — the concrete answer to "how do we see and kill a
  machine" in a fleet security review. See `docs/TRD.md` §13.8.
- **Compile credit add-on ladder (four tiers, one-time wallet purchases)** and **manual plan grants
  by customer email** — sales/ops can now provision or top up an account without an engineer.

---

## What Changed Since This Doc Was Written

Nothing here reopened, and **Windows installer code signing (2.5) is still the single hard
blocker** — the signing step still runs automatically the moment `CONXA_SIGN_CERT_SHA1` is set
(`installer_builder.py`); no certificate has been procured. Three things landed since that
strengthen the sales position rather than change the blocker list:

- **Customer-facing operations dashboard (2026-08-07).** A buyer-legible health score with its own
  breakdown, a risk queue, per-workflow reliability with version comparison, self-healing analytics,
  and an ROI view whose time-saved assumption is editable and labelled an estimate (measured vs.
  estimated numbers are kept visually separate). This is the artifact to show in a security or
  procurement review — see `docs/UI-UX-Brief.md`.
- **CI execution gate re-enabled (2026-08-04).** Every app-layer release now replays a real skill
  against the declared minimum host build before it can ship. "We can't publish a runtime that
  fails to execute" is now enforced, not asserted.
- **Self-update manifests are enforced-signed (2026-08-04).** Production refuses to start without
  `CONXA_MANIFEST_SIGNING_KEY`, so an unsigned update manifest can't reach customer machines. The
  runtime binary itself is still not Authenticode-signed — that is the same gap as 2.5 and
  `docs/Security.md` SG-09.

---

## Executive Summary

The product works. Core features ship. **You can demonstrate to customers today.**

Since this doc was first written, almost every gap has closed. **Phase 1 is complete.** Of the eight Phase 2 items, six are done — device registration, audit log, RBAC (Phase 1), drift detection, selector-cache GC, billing enforcement, and friendly error messages have all shipped.

**One hard sales blocker remains: Windows installer code signing (2.5).** The signing code itself is fully implemented and already wired into the installer build — what's left is procuring a certificate and setting one env var. macOS support (2.4) is the only other open Phase 2 item, and it's an upsell, not a blocker.

This doc separates:

1. **Blocking Gaps** — code that must be written before signing a customer
2. **Gating Items** — code that must be working before that customer goes live
3. **Nice-to-Have** — features that differentiate but don't block deals

---

## Phase 1 Status: Architecture Consolidation

**Current state: COMPLETE.** All 8 items are done, superseded, or moot.

| Item | Status | Blocks Sales? | Est. Days |
|---|---|---|---|
| ✅ 1.1 Auth token refresh fix | DONE | No | — |
| ✅ 1.2 Token acquisition flow | DONE (superseded) | No | — |
| ✅ Installer-provisioned sync token | DONE | No | — |
| ✅ 1.3 Move nonce store to Redis | MOOT (nonce flow removed) | No | — |
| ✅ 1.4 Real per-file delta sync | DONE (per-skill granularity) | No | — |
| ✅ 1.5 Rate limit cache to shared store | DONE (KV store, not Redis) | No | — |
| ✅ 1.6 Wire RBAC to API routes | DONE | **was YES** | — |
| ✅ 1.7 Remove Stripe fields | DONE (full removal) | No | — |
| ✅ 1.8 Delete research/frontend/ | MOOT (dir doesn't exist) | No | — |

**Notes:**
- 1.3 became moot when the installer-embedded sync-token model replaced the CLI auth flow — there is no nonce store left to migrate.
- 1.4 was resolved by the Enterprise-Grade Auto-Update Architecture: the delta endpoint now compares each skill independently, so republishing one skill never re-ships the pack.
- 1.5 was solved with the existing `conxa_core.db` KV dual-store (new `rate_limits` namespace), shared across instances and restart-safe. **Redis was not introduced** — the KV store already provides durable, shared storage.

---

## Phase 2 Status: Production Readiness

**Current state: 6/8 done.** These were the **sales gates** — most are now closed.

| Item | Status | Blocks Sales? | Enterprise? | Est. Days |
|---|---|---|---|---|
| ✅ 2.1 Device & Runtime Registration | DONE | was **YES** | Yes | — |
| ✅ 2.2 Drift Detection | DONE | No | Yes | — |
| ✅ 2.3 Audit Log | DONE | was **YES** | Yes | — |
| 2.4 macOS Runtime Support | Open (groundwork inert) | No | Upsell | 7 |
| 2.5 Installer Code Signing (Windows) | **Open (code done, cert not procured)** | **YES** | Yes | 3 |
| ✅ 2.6 Selector Cache GC | DONE | No | Yes | — |
| ✅ 2.7 Hardened Billing Integration | DONE | No | Yes | — |
| ✅ 2.8 Error Code UX Mapping | DONE | No | All | — |

---

## The Actual Blockers

### Critical (Block Contract Signature)

#### 2.5 Installer Code Signing (Windows) — **THE REMAINING BLOCKER**

**Status (corrected 2026-07-04):** Open, but the code is fully implemented, not just scaffolding — `installer_builder.py` already runs a conditional `signtool.exe sign /sha1 ... /fd SHA256 /tr http://timestamp.digicert.com` step after the NSIS build. It only activates when `CONXA_SIGN_CERT_SHA1` (a certificate thumbprint, not a file path) is set and a matching cert is installed in the local certificate store. As of this writing, no certificate has been procured, so builds still ship unsigned.

**Why it matters:**  
Unsigned `.exe` triggers Windows SmartScreen: "Unknown Publisher — Windows protected your PC." Users click "Run anyway" — but many enterprises block unsigned executables via GPO.

**What's left:**
- Obtain a Windows EV code signing cert (cost: ~$200/yr) and install it in the build machine's certificate store.
- Set `CONXA_SIGN_CERT_SHA1` (the cert's thumbprint) and `CONXA_SIGNTOOL_PATH` (if `signtool.exe` isn't already on PATH) in the build/CI environment — no code changes needed.

**Business impact:** Without this, large enterprises cannot deploy to their fleet. SmartScreen block = dead on arrival.

**Effort:** ~3 days, entirely cert procurement — no remaining engineering work.

---

#### ✅ 2.1 Device & Runtime Registration — DONE (2026-06-02)

`POST /api/v1/telemetry/runtime-start` now stores a registration per `(company, platform)` in the `runtime_registrations` KV namespace; `GET /api/v1/telemetry/runtimes` returns active count, stale count (>30 days), and version distribution; the Dashboard shows a `RuntimeRegistrationsCard`. Enterprise POC reporting ("how many installations?") is answerable.

#### ✅ 2.3 Audit Log — DONE (2026-06-02)

`audit_routes.py` writes `audit_log` entries on publish, installer upload, workflow create, and workflow delete, with `user_id`, `workspace_id`, `action`, `resource_id`, `ip`, `created_at`. `GET /api/v1/audit-events` (Clerk-authed, workspace-scoped) backs the Settings page. The security-checklist boilerplate item is covered.

#### ✅ 1.6 Wire RBAC to API Routes — DONE (2026-07-01)

`require_admin(principal)` (admin/owner → allow, else HTTP 403) is enforced on the previously-unguarded write routes (`workflow_create`, `workflow_delete`, `patch_bundle_release`), matching the existing publish/subscription guards. "Who can publish?" now has an enforced answer.

---

### High Priority (Go-Live Gates) — ALL DONE

These didn't block the first demo/POC, and they're now all shipped, so they no longer gate go-live either.

#### ✅ 1.3 Nonce Store — MOOT

The CLI auth flow that used the in-memory nonce dict was removed with the installer-embedded sync-token model. No nonce store exists to migrate; sync auth uses the durable KV `sync_tokens` namespace.

#### ✅ 1.4 Real Per-File Delta Sync — DONE (per-skill granularity)

`_build_delta()` compares each skill independently against the client's last-known version (`component_versions` KV, per-file SHA-256). Republishing one skill no longer re-ships the whole pack. (Residual: the ~5 small JSON files within a *changed* skill still ship together — deferred as low-value.)

#### ✅ 1.5 Rate Limit Cache to Shared Store — DONE

Sync rate limit persisted in the KV store (`rate_limits` namespace), shared across instances and restart-safe. Falls back to in-memory in local/Studio mode.

#### ✅ 2.2 Drift Detection — DONE (2026-07-01)

`structural_fingerprint` is plumbed from `SkillMeta` into runtime `manifest.json` and checked at run start in `runtime/drift.js` (from `runPlan`). It scores recorded landmarks against the live page with the pure resolver (no LLM) and emits `drift_detected` — warn only, never blocks. Cloud aggregates per skill package version at `GET /drift`.

#### ✅ 2.6 Selector Cache GC — DONE (2026-07-01)

`selector_cache.cleanup_expired_entries()` purges expired KV entries and on-disk cache files; a background loop in the cloud lifespan (`main.py`) runs it plus `cleanup_old_snapshots()` at startup and every `gc_interval_secs` (default 6h). The cache no longer grows without bound.

#### ✅ 2.7 Billing Enforcement — DONE (2026-07-01)

The entitlements service (`entitlements.py`, `PLAN_LIMITS` for Free/Starter/Pro/Enterprise) is now enforced: `entitlements_enforce_*` flags on by default, a plan/installer-slot gate at publish, compile-credit reserve→commit→release, and a Human-Edit token pool at the LLM proxy. Billing-page copy is derived from `PLAN_LIMITS` so numbers can't drift. Payment provider is **Cashfree** (not Razorpay/Stripe). `development` and any `None` limit stay unlimited.

#### ✅ 2.8 Error Code UX Mapping — DONE (2026-07-01)

`renderer/src/lib/errorMessages.ts` maps the full backend `_CommandError` set plus transport codes to plain-English strings; `errorMessage(err, fallback)` prefers the mapping, then the raw message, then the fallback. Build Studio display sites route through it.

---

### Optional (Ship Later, Upsell)

These are valuable but not required to close a deal.

- **2.4 macOS Support** — Only open Phase 2 item besides code signing. Build scripts reference macOS targets and the runtime is platform-aware, but there's no macOS installer builder or tested distribution path yet. Upsell to Mac teams; do after Windows launch. (~7 days)
- **3.1 SSO/SAML** — Enterprise feature. Ship after you have 1–2 enterprise customers who ask for it.
- **3.2 Multi-User Publishing** — Team feature. Upsell to larger accounts.

---

## Path to First Sale

### Minimum Viable Sales Package (MVSP)

**Code that must ship to close the first enterprise deal:**

| Item | Phase | Effort | Timeline |
|---|---|---|---|
| ✅ 1.6 RBAC wired | 1 | done | — |
| ✅ 2.1 Device registration | 2 | done | — |
| ✅ 2.3 Audit log | 2 | done | — |
| 2.5 Code signing | 2 | 3d | **Only remaining critical-path item** |
| ✅ 1.3 Nonce (moot) | 1 | done | — |
| ✅ 1.4 Delta sync | 1 | done | — |
| ✅ 1.5 Rate limit shared store | 1 | done | — |

**Remaining to signature: ~3 days of engineering (code signing).** Everything else on the critical path and the go-live gates is shipped.

---

### Deployment Readiness Checklist

Before you ask a customer to sign:

- [x] RBAC enforced on all write routes
- [x] Audit log visible in dashboard (last 30 days)
- [ ] Windows `.exe` is code-signed (SmartScreen trusted) — **only open item**
- [x] Device registration working (dashboard shows "active runtimes")
- [x] Error messages are user-friendly (no raw codes)
- [x] Nonce store survives cloud redeploy (moot — flow removed; sync token is durable)
- [x] Rate limits consistent across cloud instances

---

## Revenue Impact

| Gap | Blocks... | Status |
|---|---|---|
| No audit log | Security review | ✅ Resolved — `GET /api/v1/audit-events` |
| No code signing | Enterprise GPO policy | **Open — the remaining blocker** |
| No device registration | Usage reporting | ✅ Resolved — `runtime_registrations` |
| No RBAC | Role enforcement | ✅ Resolved — `require_admin` on write routes |

---

## Recommended Sequence

**Now:** 2.5 (Windows code signing) — procure the EV cert and set `CONXA_SIGN_CERT_SHA1`; the signing step itself already runs automatically once that's set. This is the last thing between the current build and a fleet-deployable, GPO-safe installer.

**After first Windows sale:** 2.4 (macOS support) to expand the addressable market to Mac teams.

**On enterprise demand:** Phase 3 items (SSO/SAML, multi-user publishing, on-prem).

This gets you to "first customer in production" in ~3 days rather than the ~3 weeks this doc originally estimated — the rest of the critical path has landed.

---

## What to Sell Before This Is Done

- **Demos** — show the workflow working. No code signing needed for a demo.
- **POCs** — limited use on a dev machine. Audit log, RBAC, and device registration are all live now.
- **Evaluations** — Build Studio + Runtime working locally, with real billing tiers, drift detection, and friendly errors.

**What you cannot sell yet:**
- Production **fleet** deployment at scale (needs Windows code signing so GPO-locked machines will run the installer).
- macOS deployments (Windows-only until 2.4 ships).
- Enterprise contracts requiring SSO/SAML (Phase 3).
