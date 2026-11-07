# Refactor Audit — `conxa-cloud/backend`

**Date:** 2026-08-22
**Scope:** Read-only audit of `conxa-cloud/backend` (architecture, code quality, duplication, API/security patterns, tests, dependencies). No code was changed.
**Method:** 4 parallel exploration agents + cross-check against `docs/Security.md`, `TODO.md`, git history, and the archived Phase-4 refactor post-mortem.

---

## TL;DR (read this first)

The backend's architecture is **fundamentally healthy**: thin cloud, clean `api → services → conxa_core` layering intent, everything under `/api/v1`, fail-fast production boot, a good test suite (885 passing). The problem is not the design — it's that **shared logic grew inside route files instead of below them**, so routes now borrow each other's private functions, two billing/usage systems count the same thing differently, and magic strings are scattered everywhere.

There are also **3 probable real bugs** found during this audit (not refactors):

1. **Cashfree webhooks may be blocked in production** by the Clerk auth middleware → billing events never arrive. (`security.py:16–56`)
2. **Subscriptions webhook signature check fails open** when the payload has no signature field — unlike the orders webhook which correctly fails closed. (`cashfree_routes.py:606` vs `:695`)
3. **`GET /api/v1/workflows/generations` is unreachable** — another route shadows it, so it returns 404. Already tracked as CLOUD-12. (`main.py:138–140`)

Plus one test is currently failing (billing/addon credit stacking), which means **the safety net for refactoring has a hole right now**.

---

## Part 1 — Architecture & Structure

### What's good (don't touch)

| Thing | Why it's good |
|---|---|
| Router registration in `main.py` | Clean, consistent |
| Lifespan/GC design in `main.py` | Works well |
| `deps.py` dependency-injection pattern | Right way to do auth in FastAPI |
| Reuse of `conxa_core` for DB/models/config | Correct — no duplication of the foundation |
| LLM router provider pool (`llm/router.py`) | One request path for all 3 providers; adding a provider is config-only. Genuinely good design |

### Finding A1 — Routes lean on each other's private functions (HIGH)

Route modules import underscore-private names from *sibling route modules*. This means when one file gets refactored, others silently break:

- `release_routes.py:27` imports `PublishFile`, `_validate_rel_path`, `_validate_slug`, `_SEMVER_RE`, `_COMPONENT_VERSIONS_NS`, `_MANIFEST_NS`, `_compose_manifest` from `publish_routes.py`
- `release_routes.py:40` imports `_compose_manifest` from `updates_routes.py`
- `entitlement_routes.py:13` and `publish_routes.py:70` both import `_require_admin` from `updates_routes.py`

**Fix:** Move shared validators and manifest composition into `app/services/` (e.g., `publish_validation.py`, `update_manifest.py`) and move `_require_admin` into `deps.py`.

### Finding A2 — A service imports from an API module (inverted dependency) (MEDIUM-HIGH)

`app/services/skillpack_storage.py` lives under services but is imported by API routes as if it defines shared storage logic — actually the *file* `app/api/skillpack_storage.py` sits in `api/` while defining KV namespaces and file layout with no HTTP surface.

**Fix:** Move it to `app/services/skillpack_storage.py`. All imports are mechanical path updates.

### Finding A3 — Entitlements punches through into conxa-core internals (MEDIUM-HIGH)

`entitlements.py:19` imports the **private** `conxa_core.db._get_engine` and hand-rolls its own Postgres upsert SQL plus custom `_FileKvStore`/`_SqlKvStore` classes (lines 156–209). Any change to conxa-core's DB layer silently breaks entitlements. This is exactly the same class of boundary violation that killed the Phase-4 refactor (see Part 5).

**Fix:** Add a public transactional KV primitive (e.g., `kv_transaction(lock_key)` context manager) to `conxa_core.db` and delete entitlements' private reimplementation. Coordinate with Build Studio since it shares conxa-core.

### Finding A4 — God files (MEDIUM)

Biggest files by content weight:
- `services/tracking.py` — contains a single ~280-line function (`_dashboard_metrics`, lines 547–829)
- `api/publish_routes.py` — ~170-line publish impl + ~150-line installer upload impl
- `api/cashfree_routes.py` — payments + subscriptions + webhooks + tier normalization in one file
- `api/entitlements.py` — plans, credits, machines, usage, Clerk calls all together

**Fix:** Split where subdomains are obvious (`entitlements` → plans / credits / machines; `publish_routes` → publish vs installer-serving). `tracking_analytics.py` next door already proves the small-pure-functions style works — copy that pattern.

### Finding A5 — Naming convention drift (LOW)

Most api files end `_routes.py`; `installer_storage.py`, `skillpack_storage.py`, `manifest_signer.py`, `machine_binding.py`, `security.py`, `deps.py` don't. `product_ownership.py` contains **no product-ownership code** — just installer-generation validation (its own docstring admits consolidation happened elsewhere).

---

## Part 2 — Duplication & Code Quality

### Finding B1 — Two parallel LLM metering systems counting the same thing (HIGH)

"Count this org's LLM tokens this month" exists **twice**, with different storage, periods, and quotas:

| | `llm_metering.py` | `entitlements.record_llm_usage` |
|---|---|---|
| KV namespace | `"llm_usage"` | `"entitlement_usage"` |
| Period | calendar month | billing-cycle window |
| Quota source | one global setting | per-plan limits |

The LLM proxy writes **both meters on every request** and enforces **both quota systems**. They can disagree (e.g., mid-billing-cycle reset), and the frontend gets different "usage" answers depending on which endpoint it reads.

**Fix:** Make `entitlements` the single source of truth (it's plan-aware). Reduce `llm_metering` to only its genuinely unique part (the char-based token estimator) or delete it.

### Finding B2 — Copy-pasted boilerplate across route files (HIGH)

The same blocks appear again and again:
- **Admin-token check**: defined once in `updates_routes._require_admin`, hand-copied into `entitlement_routes.py:13` and `publish_routes.py:70`
- **Owned-slug validation**: repeated inline in multiple publish/release handlers
- **Decode/hash helpers**: duplicated between release and rollback flows
- **Pagination**: each list endpoint rolls its own limit/offset math

**Fix:** FastAPI dependencies (`Depends(require_admin_principal)` etc.) and one pagination helper.

### Finding B3 — 15+ identical try/except error mappers (HIGH, easy win)

The pattern `except HTTPException: raise / except Exception: raise HTTPException(500, ...)` appears at least 15 times across cashfree, publish, release, and other routes. Three separate dialects exist: snake codes (`"invalid_slug"`), sentences (`"Skill pack not found."`), and human phrases.

**Fix:** One app-level exception handler in `main.py` replaces all of them. Delete `byok_routes._byok_http_error` (a bespoke mapper doing what the handler should do). Standardize errors as `{code, message}`.

### Finding B4 — Five identical failure tails inside the LLM router (HIGH within file)

In `router.py:_call_provider` (lines 345–538), the same 5-line failure-recording tail is repeated in **five** except branches (480–497, 500–509, 512–518, 520–528, 530–538). Extracting a helper saves ~40 lines and prevents future branches forgetting `error_detail`.

### Finding B5 — Two independent Clerk HTTP mini-clients (MEDIUM)

`saas._clerk_org_role` (164–204) and `entitlements._clerk_org_member_count` (677–700) both hand-roll urllib calls to Clerk with their own header/secret/error handling.

**Fix:** One `services/clerk_client.py`.

### Finding B6 — Magic strings scattered everywhere (HIGH for the top two)

- **KV namespace literals inline**: `"runtime_registrations"` appears 7× across 3 files; `"sync_tokens"`, `"tracking_tokens"` similarly scattered. Only `skillpack_storage.py` does it right (named constants).
- **Composite key `f"skill_packs:{company}:{slug}"` duplicated 5×** — if one site changes separator, runtime delta-sync silently breaks. → one helper function.
- **Plan names as literals in 4 places**, and the `"basic" → "starter"` alias is implemented independently twice (`cashfree_routes._normalize_tier` and `entitlements.normalize_plan`). → one `plans.py`.
- **Release status strings** `"ready"/"pending"/"published"` as bare literals in 6 places — an invisible state machine where a typo compiles fine and corrupts release state. → constants or enum.
- **30-day staleness defined 4 times** (once as a constant, three times hardcoded).

### Finding B7 — Long functions & deep nesting (MEDIUM)

Top offenders: `_dashboard_metrics` (~280 lines), `LLMRouter._call_provider` (~190), `_publish_skill_pack_impl` (~170), `insights` (~170), `_upload_installer_impl` (~150), Cashfree webhook handler (~85 with 4–5 nesting levels).

**Fix:** Split `_dashboard_metrics` into adoption/recovery/failure pure functions; split installer upload into validate/persist-disk/persist-kv/respond stages; extract payment-activation helpers out of the webhook.

### Finding B8 — Silent exception swallowing (LOW-MEDIUM)

Several best-effort try/excepts swallow errors without logging (`publish_routes.py:584–585, 696–697`, `updates_routes.py:205–206`, `skillpack_storage.read_pack_json_mirror`). During incidents, disk/KV drift becomes invisible. Add `logger.debug` minimum.

---

## Part 3 — API Surface, Auth & Security Patterns

### Route inventory

All routes live under `/api/v1` except the known `/api/tracking/{company}/events` alias (tracked in TODO.md). No new outliers found. ✅

### Finding C1 — Four different auth idioms coexist (MEDIUM)

1. `Depends(current_principal)` — product/tracking/cashfree routes
2. Manual `current_principal(request)` call inside body — publish/byok/workflow mutations
3. `principal_from_request + ensure_principal` — skillpack telemetry routes
4. Bare `principal_from_request` without persistence — workflow GET routes

Whether seat checks/membership recording happen depends on which idiom the author picked that day. This partially **reopens SG-18** (seat enforcement): workflow GETs (`workflow_routes.py:50, 62, 127, 134`) create membership rows via `ensure_principal` **without** the seat cap check.

**Fix:** Route everything through the `Depends(current_principal)` chain so the gate is visible in every handler signature.

### Finding C2 — `/jobs*` endpoints have zero authorization (MEDIUM-HIGH)

`job_routes.py:22–70` — no auth, no workspace scoping. Not covered by any existing Security.md gap.

### Finding C3 — Admin-token problems (MEDIUM)

- Token comparison in `updates_routes._require_admin` (:316) is not constant-time (unlike `security.py` which does it right)
- The token itself has **two sources of truth** (`updates_routes.py:29` vs `security.py:101`)

### Finding C4 — Legacy static-secret proxy branch still live (MEDIUM)

`saas.py:258–279` keeps the deprecated pre-HMAC proxy auth path alive alongside the fixed HMAC path (SG-02). Once the Vercel handler is confirmed migrated, delete it.

### Finding C5 — Public-path allowlist is a footgun (MEDIUM, architectural)

`security.py:16–56` holds route paths as string tuples decoupled from actual router definitions. Adding any GET under `/api/v1/skill-packs/` silently becomes public. Also contains dead entries (`/health`, `/api/v1/health` — routes that don't exist). The Cashfree webhook blockage (TL;DR bug #1) is a direct consequence of this manual-list design.

### Finding C6 — Sync HTTP/file IO inside `async def` handlers (MEDIUM-HIGH)

Six+ handlers do blocking httpx calls and file IO inside async functions, stalling the event loop under load: `cashfree_routes.py:155–158+`, `tracking_routes.py:44–87`, `publish_routes.py:489+`.

**Fix:** Small mechanical change — convert those six handlers to plain sync `def` (FastAPI runs them on a thread pool automatically).

### Finding C7 — Error responses leak upstream details (LOW)

Upstream provider text flows into client-visible error details (`cashfree_routes.py:341` etc.), some route errors lack request_id, and YAML parse failures degrade silently (`updates_routes.py:190–206`).

### Security.md cross-check

Verified against code: SG-01 through SG-09, SG-14–SG-18 mostly match their claimed status. The notable exception is **SG-18 (seats)** which has the side-door regression described in C1. New gaps found this audit that Security.md doesn't yet list: webhook middleware blockage, fail-open subscription signature, unauthenticated jobs, non-constant-time admin compare, public-path footgun.

### DB access pattern

Healthy overall — filesystem store in dev/tests, Postgres in prod behind `conxa_core.db`. Known scale-shaped debts (no unit-of-work, some full-scan patterns, N+1 loops in `saas.dashboard_for` which re-reads the entire metadata doc once per package) are mostly already tracked in TODO.md (CLOUD-2). Nothing needs action today beyond noting them.

---

## Part 4 — Tests, Dependencies & Risk of Refactoring

### Test suite health

**Current baseline (agent ran it): `885 passed, 1 failed, 1 skipped` (~108 s).**

⚠️ **One real failing test:** `tests/test_entitlements.py::test_addon_packs_stack_credits_and_human_edit_tokens` — expects 290 compile credits from add-on stacking, gets 200. Likely broken by commit `0d60de5` (four-tier add-on ladder). **This is billing logic failing its own test. Fix before any refactor.**

Well covered: updates, tracking, security, release channels/diffs, cashfree, byok, manifest signing, LLM router backoff, entitlements, skillpack sync, publish/release.

**Zero/near-zero coverage — add tests BEFORE touching these:**

| Module | Risk |
|---|---|
| `services/rbac.py` | High (auth-adjacent) |
| `services/jobs.py` + `job_routes.py` | Medium |
| `api/machine_binding.py` | Medium (new fleet feature) |
| `api/workflow_routes.py` | Medium |
| `main.py` prod-config validation | Medium |
| `services/saas.py` | High (partial coverage; also #4 churn hotspot) |

Test quality is good: integration-style via FastAPI TestClient, real logic with patched HTTP, tmp_path filesystem stores, no Postgres needed. Caveat: `conftest.py` globally disables auth and installs the concrete router — tests share global state around the settings singleton (see Part 5).

### Dependencies

Only 6 direct deps, all lean, none unused. But: `>=` pins with **no lockfile** — Render builds aren't reproducible vs your dev env. Note `requirements.txt` is also consumed by `build-studio.yml` CI. Hidden deps (`pydantic-settings`, SQLAlchemy) come transitively via conxa-core. Recommendation: lockfile or upper bounds. Low-medium severity.

### Git churn hotspots (last 6 months)

`publish_routes.py` (18 commits) > `skillpack_update_routes.py` (16) > `updates_routes.py` (15) > `services/saas.py` (13) > `tracking_routes.py` (11) = `main.py` (11) > `cashfree_routes.py` (10) > `entitlements.py` (9).

Churn concentrates exactly where recent features landed (release channels, add-on ladder, seats, fleet registry) — i.e., **the highest-risk refactor targets**. Sequence any refactor of these away from active feature work.

---

## Part 5 — Lessons From the Failed Phase-4 Refactor

`docs/archive/refactors/PHASE_4_REFACTOR_REPORT.md` documents a config.py split that was implemented and fully reverted after 15 test failures. Plain-language root cause:

> Pydantic inheritance created three separate settings objects instead of one shared singleton. Tests patch `settings.field` on ONE object; code importing from ANOTHER module saw stale values and wrote files to wrong places.

**Rules for any future backend refactor:**
1. Never attempt subclass-based splitting of the Settings class again. Only safe options: field-group mixins composed into one class (cosmetic), or converting ~50 modules to a `get_settings()` accessor (its own scoped project).
2. Trace real call graphs before assuming code is app-only — the last attempt's assumption about `llm/client.py` being Studio-only was wrong (the cloud router calls it live).
3. Validate every step against an exact baseline: `pytest -q tests` + `import app.main` smoke test. That's what made the revert clean.

---

## Part 6 — Prioritized Action Plan

### 🔴 Do first (bugs & safety net)

| # | Item | Effort |
|---|---|---|
| 1 | Verify Cashfree webhooks against live prod (Clerk middleware blocking?) — fix PUBLIC_PATHS + fail-open signature (F-3) **together** | Small |
| 2 | Fix failing addon-stacking test (`test_entitlements.py`) to restore trusted baseline | Small |
| 3 | Un-shadow `GET /workflows/generations` (CLOUD-12) | Small |
| 4 | Close `/jobs*` authz hole | Small |
| 5 | Fix SG-18 side door: route workflow GETs + telemetry reads through `current_principal` | Small |
| 6 | Make admin compare constant-time; merge token sources | XS |

### 🟠 High-value refactors (P0/P1)

| # | Item | Effort |
|---|---|---|
| 7 | App-level exception handler replacing 15+ try/except mappers; standardize `{code, message}` | Small |
| 8 | Centralize KV namespaces + composite-key helper + plan-name module + status enums + staleness const | Small |
| 9 | Merge the two LLM metering systems behind `entitlements` | Medium |
| 10 | Kill cross-route private imports: move validators/manifest-composition/admin-auth into services/deps | Medium |
| 11 | Move `skillpack_storage.py` from api/ → services/ | Small |
| 12 | Convert 6 async handlers with sync IO to plain `def` | Medium |

### 🟡 Later (P2)

| # | Item | Effort |
|---|---|---|
| 13 | Add transactional KV primitive to conxa-core; delete entitlements' private reimpl | Medium (coordinate w/ Studio) |
| 14 | Split god-files (`entitlements`, `publish_routes`, `cashfree_routes`, `_dashboard_metrics`) | Medium |
| 15 | Single Clerk client; pagination helper; Pydantic response models for top endpoints | Medium |
| 16 | Delete legacy proxy-secret branch; clean dead security.py entries; naming cleanup | Small |
| 17 | Lockfile/upper-bound dependencies | Small |
| 18 | Add tests for rbac/jobs/machine_binding/workflow_routes before touching them | Medium |

### ⛔ Explicitly do NOT change

- Router registration style in `main.py`
- Lifespan/GC design
- The `deps.py` dependency pattern (extend it, don't replace it)
- The LLM provider pool architecture (it's correct)
- Anything Settings-class-related per Part 5 lessons

---

## Appendix — Consolidated Findings Register

| ID | Finding | Location | Severity |
|---|---|---|---|
| O-1 | Workflows-generations route shadowed → 404 (CLOUD-12) | main.py:138–140 | High (functional) |
| O-2 | Cashfree webhooks likely blocked by Clerk middleware in prod | security.py:16–56 | High (probable bug) |
| F-3 | Subscriptions webhook signature fails open on missing sig | cashfree_routes.py:606 | High |
| B1 | Dual LLM metering systems | llm_metering.py vs entitlements.py | High |
| A1 | Cross-route private imports | release/entitlement/publish routes | High |
| B2 | Copy-pasted admin/slug/pagination boilerplate | across api/ | High |
| B3 | 15+ duplicate error mappers | across api/ | High |
| B6 | Magic strings: KV ns, keys, plans, statuses, staleness | many files | High |
| C1 | Four auth idioms; SG-18 seat bypass side door | deps.py + workflow GETs | Medium |
| C2 | /jobs* no authorization | job_routes.py:22–70 | Med-High |
| C6 | Sync IO in async handlers stalls event loop | cashfree/tracking/publish routes | Med-High |
| A2 | skillpack_storage in wrong layer | api/skillpack_storage.py | Med-High |
| A3 | Private conxa-core internals used | entitlements.py:19,156–209 | Med-High |
| A4 | God files / long functions | tracking, publish, cashfree, entitlements | Medium |
| C3 | Non-constant-time admin compare; dual token source | updates_routes.py:316, :29 | Medium |
| C4 | Legacy proxy-secret branch still live | saas.py:258–279 | Medium |
| C5 | Manual public-path allowlist footgun (+dead entries) | security.py:16–56 | Medium |
| B5 | Duplicate Clerk clients | saas.py / entitlements.py | Medium |
| B7/B8 | Long functions; silent exception swallowing | various | Low-Med |
| A5 | File-naming drift; misnamed product_ownership.py | api/ | Low |
| T-1 | Failing addon-stacking billing test | tests/test_entitlements.py | Med-High |
| T-2 | Untested: rbac, jobs, machine_binding, workflow_routes | services/api | Medium |
| D-1 | No dependency lockfile | requirements.txt | Low-Med |
