# Implementation Plan

**Status:** Current as of 2026-08-08 — **Phase 1 COMPLETE** (1.1–1.8, 1.10, 1.12 all done, superseded, or moot; 1.9 open). Phase 2 mostly done (2.1, 2.2, 2.3, 2.5 code/wiring, 2.6, 2.7, 2.8, 2.9, runtime-split + auto-update arch); 2.4 (macOS) and 2.5's certificate procurement remain open — see `TODO.md`. 1.12 is the pricing/positioning restructure following the Centelon pilot demo.
**Audience:** Engineering team

This plan is grounded in the actual codebase. Each item references the specific file or system that needs to change. Items are ordered by risk and dependency, not effort.

---

## Phase 1 — Architecture Consolidation

**Goal:** Close the gaps that make the current system unreliable in production. No new features. All changes are to correctness, reliability, and observability.

**Timeline estimate:** 4–6 weeks

---

### ✅ 1.1 Fix Runtime Auth Token Refresh (Critical) — DONE 2026-06-02

**What was broken:** `POST /api/v1/auth/refresh` was a stub — it echoed back any token with a 30-day expiry regardless of validity.

**What was fixed:**
- `post_auth_refresh` now calls `verify_clerk_jwt(body.token)` in production (`SKILL_AUTH_REQUIRED=true`); returns HTTP 401 for invalid/expired tokens.
- Response `expires_at` reflects the real JWT `exp` claim instead of a fake 30-day window.
- Added `/api/v1/auth/refresh` and `/api/v1/auth/cli/poll` to `PUBLIC_AUTH_PATHS` in `security.py` so the middleware does not block runtime calls (which have no `Authorization` header — the token in the body is the credential).
- Fixed URL bug in `runtime/auth_manager.js:_doRefresh()`: was calling `${CONXA_API}/auth/refresh` (missing `/api/v1/` prefix), now calls `${CONXA_API}/api/v1/auth/refresh`.
- Local dev behaviour unchanged: token echoed back when `SKILL_AUTH_REQUIRED=false`.

---

### ✅ 1.2 Implement Runtime Token Acquisition Flow — DONE 2026-06-02

**What was missing:** No in-product way for a runtime to acquire a token for a company.

**What was built:**
- Added `setup_company` MCP tool to `runtime/server.js` (two-phase):
  - Phase 1 (`setup_company(company)`): calls `getAuthChallengeUrl()`, returns `{auth_url, nonce}` for Claude to show the user.
  - Phase 2 (`setup_company(company, nonce)`): polls `POST /api/v1/auth/cli/poll` once; on success stores token via `setToken()` and triggers a skill pack sync.
- Changed `post_auth_cli_complete` to use a JSON body (`{nonce, token}`) instead of query params.
- Added 10-minute TTL enforcement in `post_auth_cli_poll`; returns `{status: "expired"}` when stale.
- `post_auth_cli_poll` now parses the token's JWT `exp` and returns the real expiry.
- Created `conxa-cloud/frontend/app/auth/cli/page.tsx` — the browser auth page the user visits to approve access (Clerk-authenticated; calls `/api/v1/auth/cli/complete` via the Next.js proxy).

**Superseded:** The Clerk-JWT/`setup_company` approach was replaced in the same session by the installer-embedded sync-token model below. The `setup_company` tool, `/auth/refresh`, and `/auth/cli/*` endpoints were all removed. The CLI auth page (`app/auth/cli/page.tsx`) created here was deleted.

---

### ✅ Installer-Provisioned Sync Token — DONE 2026-06-02

**What was built:** End-user runtimes now pull skill-pack updates using an installer-embedded sync token — no Conxa login required.

- **Cloud (`publish_routes.py`):** `_sync_token()` mints a `secrets.token_urlsafe(32)` at publish time (reused on republish), stored in `sync_tokens` KV namespace. `sync_token` embedded in cloud-side `pack.json` and returned in the publish response.
- **Cloud (`skillpack_update_routes.py`):** `get_skill_pack_delta` now calls `_verify_sync_token(company, token)` — `secrets.compare_digest` against `db_get("sync_tokens", company)`. 401 on mismatch; skipped in local dev.
- **Cloud (`main.py`, `security.py`):** Removed `auth_router` and `PUBLIC_AUTH_PATHS` (the `/auth/refresh` + `/auth/cli/*` endpoints are gone).
- **Build Studio (`backend.py`):** After publish, reads `sync_token` from response and writes it into local `pack.json` before the installer is staged. Hard error if publish response lacks `sync_token`.
- **Build Studio (`installer_builder.py`):** Guard added — fails fast if `pack.json` has no `sync_token` (catches packs built before publish).
- **Runtime (`sync.js`):** `_doSync` reads `pack.sync_token` directly; `authManager.getToken()` call removed. `syncSkillPacks` signature simplified (no `authManager` param).
- **Runtime (`server.js`):** `setup_company` tool and `_handleAuthCallback` removed.
- **Runtime (`auth_manager.js`):** Clerk-token machinery (`getToken`/`setToken`/`refreshToken`/`_doRefresh`) removed. Session encryption now uses a per-machine random key (`getSessionKey(company)`) generated on first use and stored in OS keychain (`conxa-session` service) — isolates individual users' session files from the shared installer secret.
- **Runtime (`browser.js`):** Both `authManager.getToken(company)` call sites replaced with `authManager.getSessionKey(company)`.

---

### ✅ 1.3 Move Nonce Store to Redis (or DB) — SUPERSEDED / MOOT

**No longer applicable.** The CLI auth flow that used `_auth_nonces` was removed when the
installer-embedded sync-token model replaced it (see "Installer-Provisioned Sync Token"
above). There is no nonce store in the codebase (`grep nonce` = 0 hits), so there is
nothing to migrate. Runtimes authenticate skill-pack sync with the per-company sync token,
which is durable (KV `sync_tokens` namespace) and already restart-safe.

---

### ✅ 1.4 Implement Real Per-File Delta Sync — DONE (per-skill granularity)

**Resolved by the Enterprise-Grade Auto-Update Architecture (2026-07-01).**
`skillpack_update_routes.py:_build_delta()` now compares **each skill independently**
against the client's last-known version for that specific skill (`component_versions` KV,
per-file SHA-256), so republishing one skill never re-ships the rest of the pack. The
`since` request is a JSON `{skill_slug: version}` map. This eliminates the whole-pack
transfer the item was written to fix.

**Residual (intentionally not done):** within a *changed* skill, all ~5 small JSON files
(`execution/recovery/inputs/manifest/validation.json`) are shipped even if only one
changed. Marginal payload benefit; deferred as low-value.

---

### ✅ 1.5 Move Rate Limit Cache to a Shared Store — DONE 2026-07-01

**What was broken:** `_rate_cache` in `skillpack_update_routes.py` was in-memory —
multi-instance deployments (Render horizontal scaling) and restarts had no shared limit.

**What was built:** the sync rate limit is now persisted in the existing `conxa_core.db`
KV store (new `rate_limits` namespace, keyed by the 16-char token hash, storing
`{last_ts}`) whenever a database is configured (`using_database()`), so the 5-minute window
holds across restarts and is shared across instances. Falls back to the in-memory dict in
local/Studio mode where no database is configured. **Redis was not introduced** — it is not
installed (`requirements.txt`) nor provisioned (`render.yaml`); the KV dual-store already
provides durable, shared storage. Helpers `_rate_limit_last()` / `_rate_limit_set()`.

**Files:** `skillpack_update_routes.py`. **Tests:** `tests/test_skillpack_sync.py`.

---

### ✅ 1.6 Wire RBAC to API Routes — DONE 2026-07-01

**What was broken:** `app/services/rbac.py`'s `require_admin()` guarded the publish/installer
routes (`publish_routes.py`) and subscription routes, but the remaining mutating dashboard
routes accepted any authenticated member.

**What was built:** `require_admin(principal)` (allows `admin`/`owner`, else HTTP 403) is now
enforced on the three previously-unguarded write routes, matching the existing
`principal_from_request` → `ensure_principal` → `require_admin` pattern:
- `plugin_routes.py` — `post_create_plugin` (POST `/plugins`)
- `plugin_routes.py` — `delete_plugin_endpoint` (DELETE `/plugins/{id}`)
- `product_routes.py` — `patch_bundle_release` (PATCH `/packages/bundles/{slug}/release`)

Enforced directly (no audit-only phase). Local/dev principals default to role `owner`, so
existing single-user workflows are unaffected. Intentionally-public runtime phone-home
endpoints (`/api/v1/telemetry/runtime-start`, tracking-event ingest, skill-pack sync, installer
downloads — see `PUBLIC_PATHS`/`PUBLIC_PATH_PREFIXES` in `app/api/security.py`) are left open
by design, authenticated by sync token rather than Clerk role.

**Files:** `app/services/rbac.py`, `plugin_routes.py`, `product_routes.py`.
**Tests:** `tests/test_product_routes.py` (member→403, admin→200).

---

### ✅ 1.7 Remove Stripe — DONE 2026-07-01 (full removal)

**What was present:** more than just config — `product_routes.py` carried live but orphaned
Stripe `checkout`/`portal`/`webhook` endpoints, `security.py` whitelisted the webhook,
`saas.py` computed a `stripe_configured` flag, the frontend surfaced it, and `stripe>=11.0.0`
was a backend dependency. The wired gateway is Cashfree (`cashfree_routes.py`).

**What was removed:** the three Stripe endpoints + `_stripe_client` helper, the
`/api/v1/webhooks/stripe` public-path entry, the `stripe_configured` billing flag (backend
`saas.py` + frontend `productApi.ts` type and the dead `createCheckout`/`createPortal`
callers), the `stripe>=11.0.0` requirement, the `stripe_*` config fields, and the Stripe
test assertion.

**Files:** `product_routes.py`, `security.py`, `saas.py`, `requirements.txt`,
`packages/conxa-core/conxa_core/config.py`, `frontend/src/api/productApi.ts`,
`tests/test_product_routes.py`.

---

### ✅ 1.8 Delete or Document research/frontend/ — MOOT

**No longer applicable.** The `research/frontend/` directory does not exist in the repo (it
was already deleted or never committed). No action needed. The authoritative UIs remain
`conxa-cloud/frontend/` and `conxa-builder/electron/renderer/`.

---

### 🚧 1.9 Build Studio Workflow Redesign — Phase 0/1 DONE 2026-07-07, Phases 2-6 open

Full gap analysis and phased plan: `conxa-builder-workflow-redesign.md` (repo root). Overlaps
and supersedes the UI-layer half of `TODO.md` BUILD-2. Reorganizes the Build Studio around a
stage-shaped sidebar (Record → Compile → Human Edit → Test Skill → Publish Skill Package →
Build Installer) instead of the compiler-pipeline-shaped one it replaced, with one shared
plugin selection instead of four independent per-page rails.

**Done (Phase 0 — prerequisites, and Phase 1 — IA consolidation):**
- Single derived workflow lifecycle stage (`handlers/status.py::derive_workflow_stage`)
  replacing three inconsistent status fields; `signed_off` and a compile-confidence summary
  exposed through the workflow DTO.
- Stage-shaped sidebar + persisted shared plugin selection (`store/selectionStore.ts`,
  `PluginSwitcher.tsx`); new Record/Compile/Human-Edit-list/Publish stage pages; Test Skill
  rescoped off its own rail.
- Sign-off auto-builds the plugin package the moment every workflow is compiled + signed off
  (`cmd_sign_off_workflow`), superseding the standalone Build Plugin page; "Finish editing"
  now awaits the RPC and surfaces failure instead of swallowing it.
- Package-file browser demoted from a top-level Packages page into an on-demand Inspector
  drawer, which also hosts the manual "Rebuild package" escape hatch.
- Installer release-version prefill suggests the next patch after the last real release
  instead of a hardcoded `0.1.0`.

**Open (Phases 2-6, see the redesign doc's §12 for the full breakdown):**
- Phase 2 — explicit, asynchronous, concurrent Compile with a background job model (today's
  `cmd_compile` is synchronous; a process-global LLM router (`conxa_core.llm.set_router`)
  must be made concurrency-safe first — see the redesign doc's T0.2).
  **Partially unblocked 2026-08-15 (renderer side only):** the run now lives in
  `store/compileStore.ts` with the event subscription in `AppChrome`, so a compile survives
  navigation and the page reattaches to it. This also fixed a live billing bug — remounting
  `CompileProgress` used to re-fire `cmd('compile')` and reserve a *second* compile credit for
  the same recording; `start()` is now idempotent on `workflowId:sessionId:mode`. Still open
  and unchanged: real concurrency. Only one run is tracked, because `window.conxa.cmd` never
  exposes the backend request id, so streamed events can't be correlated to a run — plumbing a
  request id through `main.js` + `preload.js` is the prerequisite, alongside T0.2.
- Phase 3 — Human Edit/Test Skill polish. ✅ **Confidence banner DONE 2026-07-10** (delivered as
  part of the broader Human Edit vs. Skill Package redesign, not standalone — see §1.11 below):
  `CompileHealthBanner.tsx`, mounted under the page header, shows `compile_report.status`/
  `min_confidence`/steps-below-threshold. "How Claude sees this skill" panel already existed
  pre-Phase-3 (`HowClaudeSeesThisPanel.tsx`). Still open: Tier-2 recovery caveat, Approve rename
  + ceremony.
- ✅ **Phase 4 — DONE 2026-07-09** (delivered as part of the broader Skill-Pack-Centric
  Publishing & Versioned Installer Architecture redesign, not standalone): Publish Skill
  Package split fully out of Build Installer. `Backend._publish_skill_pack_for_installer`
  renamed to `_publish_skill_pack` and moved behind a new mandatory `cmd_publish_skill_pack`
  RPC; `PublishPage.tsx` is now the real implementation (version/release-notes entry, release
  history, skill-pack-slot meter) instead of the Phase-1 stub; `BuildInstallerPage.tsx` is
  slimmed to a secondary/advanced action gated on a release already existing, with installer
  cloud-upload now optional/non-fatal. Distribution status beyond release history/sync+tracking
  endpoints (e.g. per-customer rollout tracking) was not built — still open if wanted.
  See `docs/Backend-Schema.md` §5.1/§5.1a/§5.1b and `docs/UI-UX-Brief.md` §2.10/§2.12.
- Phase 5 — move the installer NSIS build to Conxa Cloud (deferred; needs `CLOUD-2`'s durable
  job queue first). The versioned `{installer_version}` endpoint scheme and thin-installer
  architecture landed in the Phase-4 work above specifically to make this drop-in later
  without any vendor-facing change — see `docs/Backend-Schema.md` §5.1a.
- Phase 6 — headless `conxa` CLI over the same RPC handlers.

**Files (Phase 0/1):** `conxa-builder/python/handlers/status.py` (new),
`handlers/compile.py`, `handlers/workflow_editor.py`, `handlers/plugins.py`,
`packages/conxa-core/conxa_core/models/plugin.py`,
`conxa-builder/electron/renderer/src/components/layout/AppChrome.tsx`,
`store/selectionStore.ts` (new), `components/PluginSwitcher.tsx` (new),
`components/StagePath.tsx` (new), `components/inspector/InspectorDrawer.tsx` (new),
`pages/CompilePage.tsx`/`HumanEditListPage.tsx`/`PublishPage.tsx` (new),
`pages/PluginDetailPage.tsx`, `pages/TestPluginPage.tsx`, `pages/BuildInstallerPage.tsx`;
deleted `pages/SkillPackagesPage.tsx`; `pages/PluginsPage.tsx` renamed to `pages/Dashboard.tsx`
(component renamed `PluginsPage` -> `Dashboard` to match); `pages/BuildPage.tsx` was briefly
deleted then restored and repurposed as `pages/RecordPage.tsx`'s left-rail-plus-workspace shell
(its build-pipeline content replaced with the Record Login/Create Workflow actions previously
inline on `PluginDetailPage.tsx`) — Build Plugin itself has no page; the file just lent its
layout to a different page.

---

### ✅ 1.11 Human Edit vs. Skill Package Redesign (three-tier IA + branch-step authoring) — DONE 2026-07-10

**Source:** `research-analysis/Human-Edit-vs-Skill-Package.md` (the audit) and `TODO.md` BUILD-5
(now resolved) / EXEC-1 (editor-authoring half now resolved).

**What was missing:** Human Edit hid several skill-package components the runtime actually acts
on — an approver could hit "Approve" without ever seeing a step's conditional sub-body, the
compiled workflow "plan" (`intent_graph`), the recovery ladder that fires on failure, the element
fingerprint the resolver scores against, or a workflow-level compile-health summary. Separately,
EXEC-1's branch primitives (`if_present`/`try_dismiss`/`wait_for_one_of`, shipped 2026-07-09) had
no Build Studio authoring path — they could only be produced by hand-editing a skill JSON.

**What was built:**
- **Read-only visibility (backend, `conxa_compile/editor/dto.py` + `workflow_dto.py`):**
  `StepEditorDTO` gained `recovery_view`, `fingerprint` (incl. diagnostics-only `stable_hash`/
  `compat_fingerprint`), `handler_hints_view`, `safety`, `branch_summary`, `branch_steps`
  (path-addressed, e.g. `skill_x:2.branch.steps[0]`); `WorkflowResponse` gained `intent_graph`
  (verbatim `WorkflowIntentGraph`) and `compile_health` (derived from `compile_report` + `meta`,
  incl. `llm_router_stats`). All additive projections through the existing `build_workflow_response`
  chokepoint — every editor response (`cmd_get_workflow` and the shared `_skill_response` helper)
  picks them up automatically; none of it is writable.
- **Branch-step authoring (backend, closes EXEC-1's remaining gap):** `if_present`/`try_dismiss`/
  `wait_for_one_of` are now `INSERTABLE_ACTIONS` (`editor/action_registry.py`); `_new_manual_step`
  scaffolds an empty `branch` block per kind (`editor/workflow_mutations.py`). `if_present`'s
  nested body is fully editable: new `insert_branch_step`/`delete_branch_step`/
  `reorder_branch_steps` mutation functions + `cmd_insert_branch_step`/`cmd_delete_branch_step`/
  `cmd_reorder_branch_steps` RPCs (`handlers/workflow_editor.py`) for structural edits, and
  `cmd_patch_step` gained an optional `path` parameter (`"branch.steps[N]"`) for per-field edits
  on a nested step, routed through a new shared `_apply_step_patch` helper so both the top-level
  and nested flows go through the same selector-quality-gate + `identity_bundle` rebuild logic.
  `patch_gate.py::validate_editor_patch` gained `_validate_branch_patch` (selector-quality-gates
  `try_dismiss` candidates and `wait_for_one_of` option selectors, rejects nested-body content
  smuggled through the parent's own patch) and an `in_branch_body` flag that rejects
  `recovery`/`validation` patches on nested steps (branch bodies are best-effort and never enter
  Tier A/B recovery — CLAUDE.md invariant).
- **Frontend — Tier 1 (Review, default view):** `CompileHealthBanner.tsx` (workflow-level status
  banner under the page header), `WorkflowPlanPanel.tsx` (new "Workflow plan" tool pane), safety
  badges + `BranchSummaryBadge` on `WorkflowStepItem.tsx`, `BranchSubList.tsx` (indented
  collapsible branch-body preview in the step list — selecting a nested row sets a new
  `editorStore.focusedBranchIndex`), conditional action kinds added to the Add-action menu.
- **Frontend — Tier 2 (Reliability, per-step):** `RecoveryBehaviorCard.tsx`,
  `ElementFingerprintCard.tsx`, and the previously-unmounted `StepIdentitySummary.tsx` (see
  `TODO.md` BUILD-4) all mounted behind a new "Reliability" collapsible in `StepConfigForm.tsx`.
  `BranchBodyEditor.tsx` — a standalone, path-aware add/remove/reorder/edit surface for
  `if_present` nested bodies, mounted in `InlineRetargetFlow.tsx` (deliberately not built on
  `StepConfigForm` itself, whose `patchStep` calls are hardcoded to the top-level `step_index`
  across ~7 call sites — reusing it would have risked the primary editing surface for a
  secondary flow).
- **Frontend — Tier 3 (Diagnostics):** new `DiagnosticsPanel.tsx` tool pane — compile report,
  LLM router stats, compiler policy version, required runtime, and the selected step's integrity
  hashes.
- **Scope trims (tracked as follow-ups):** `try_dismiss`/`wait_for_one_of` got read-only summary
  badges only, no authoring UI for their candidate/option lists (`TODO.md` BUILD-6);
  `validation.success_conditions` was left as-is per explicit user direction (`TODO.md` BUILD-7).
  `plugin_builder_output.py`'s `CONXA_REQUIRED_RUNTIME` floor was **not** bumped — confirmed the
  branch-executor commit isn't in any tagged `app-vX.Y.Z` release yet; the code comment now names
  the exact commit for a future release to act on.
- Tests: `conxa-cloud/tests/test_patch_gate.py` (`TestBranchStepPatches`,
  `TestBranchBodyStepValidation` — 16 new cases), `test_workflow_dto.py` (15 new projection
  cases), `test_plugin_builder.py` (editor-authored `if_present` step round-trips through the
  same saved-skill → execution-step serialization as a hand-built fixture).

**Files:** `conxa_compile/editor/dto.py`, `workflow_dto.py`, `action_registry.py`,
`workflow_mutations.py`, `patch_gate.py`; `handlers/workflow_editor.py`;
`plugin_builder_output.py` (comment only); `conxa-builder/electron/renderer/src/types/workflow.ts`;
`components/CompileHealthBanner.tsx`, `WorkflowPlanPanel.tsx`, `DiagnosticsPanel.tsx`,
`RecoveryBehaviorCard.tsx`, `ElementFingerprintCard.tsx`, `branch/BranchBodyEditor.tsx` (all new);
`components/workflowViewer/BranchSubList.tsx` (new), `WorkflowStepItem.tsx`, `WorkflowViewer.tsx`;
`components/StepConfigForm.tsx`, `components/retarget/InlineRetargetFlow.tsx`; `pages/HumanEditPage.tsx`;
`api/workflowApi.ts`; `store/editorStore.ts`; `lib/editorHelp.tsx`, `lib/workflowViewerHelpers.ts`.

---

### ✅ 1.10 Enforced Post-Condition Validation — DONE 2026-07-09

**What was missing:** A step was considered "done" once its action executed without throwing.
The existing post-condition mechanism (`verifyStep`, `runtime/run.js`) was under-powered: text
entry (`fill`/`type`) compiled zero assertions at all; most compiled assertions were advisory
(`required=False`) and so never failed a step; commit/submit clicks with no recorded URL/DOM
evidence got no enforced check at all (a silently no-op button would pass); and recovery re-ran
the failed action without ever re-checking the post-condition, so a "recovered" step could still
leave the intended result unmet.

**What was built:**
- **Data model** (`packages/conxa-core/conxa_core/models/skill_spec.py`): `Assertion` gained an
  `expected` field; vocabulary extended to `value_equals` (field-value check, normalized +
  contains fallback) and `state_changed` (no-op guard for evidence-less commits).
- **Compiler** (`conxa_compile/compiler/build.py:_build_assertions`): a deterministic "primary
  signal picker" — every consequential action (text entry/select, commit/submit/destructive
  click) compiles with exactly one `required=True` assertion; everything else stays advisory.
  Evidence-less commit clicks synthesize a required `state_changed` check rather than going
  unenforced. `conxa_compile/editor/patch_gate.py` gained a matching invariant (a human edit
  can't silently drop the last required assertion on a consequential step).
- **Runtime** (`runtime/run.js`): new `value_equals`/`state_changed` handlers in `verifyStep`;
  the recovery cascade (`recoverWithSelector` and everything that funnels through it) now
  re-invokes `verifyStep` after re-running the action and only reports success if the
  post-condition re-holds; a verify-fail (`recovery.js` `descend-layer2`) skips the exception-ladder
  single-remedy retry and continues the rest of Tier A (a11y / re-hover / dialog).
- **Human Editor:** the retarget wizard's Phase 3, "Confirm & apply," is now "Validation" —
  `RetargetPhaseValidation.tsx` shows the enforced post-condition plus advisory checks as an
  editable flat list, round-tripped through `cmd_retarget_apply`'s new `edited_assertions`
  payload field.
- No forced recompile — packs compiled before this change carry no new assertions and behave
  exactly as before; enforcement only strengthens on steps that are recompiled or retargeted.
- Tests: `runtime/test/test_verify.js` (value_equals/state_changed), new
  `runtime/test/test_recovery_verify.js` (proves the recovered-but-unverified gap is closed),
  `conxa-cloud/tests/test_element_fingerprint.py` (compiler assertion emission), new
  `conxa-cloud/tests/test_patch_gate.py`.

**Deferred (not part of this item):** a compound AND/OR `wait_tree` validation editor
(`ValidationEditor.tsx` remains orphaned/unused — its leaf-field UI idioms were reused, not its
tree model); wiring `validate_editor_patch` into `cmd_patch_step` (it was already unwired/unused
before this change — see `TODO.md`).

---

### ✅ 1.12 Pricing & Positioning Restructure — DONE 2026-08-08

**What was broken:** Following the Centelon pilot demo, the PRD carried two competing primary
customers (SaaS vendors, enterprises) with no resolution between them, the marketing site showed
single-app tasks instead of business processes, and pricing existed with four different numbers across
`PLAN_LIMITS`, `docs/cost_model.md`, `cashfree_routes.TIER_INFO`, and `docs/PRD.md`. See
`Conxa-Pilot-Conclusions.pdf` (internal) for the full pilot writeup.

**What shipped:**
- **Positioning** — `docs/PRD.md` rewritten around a single capability ladder (Free proves it works →
  Starter/Pro run an organization → Pro/Enterprise ship to customers), the "own the process, not the
  software" reframe, a new "What Our IP Actually Is" section, and the workflow qualification checklist.
  Marketing site: new `/pricing` page rendering live from `GET /api/v1/subscriptions/plans`, rewritten
  `Examples.tsx` (cross-system processes instead of single-app tasks), new FAQ entries, `publicDocs.ts`
  cleanup.
- **Pricing model** — `PLAN_LIMITS` (`conxa-cloud/backend/app/services/entitlements.py`) extended with
  capability keys (`distribution`, `white_label`, `ops_tier`, `compile_pool`, `byok`, `trial_days`,
  `analytics_retention_days`) alongside the four numeric meters. INR pricing: Starter ₹19,999/mo, Pro
  ₹49,999/mo, Enterprise custom from ₹99,999/mo.
- **Skill pack slots removed entirely** — no limit on how many product slugs a workspace publishes
  under. Replaced by `machines` as the numeric meter (`workspace_devices` KV, machine-hash header
  `X-Conxa-Machine`) — the actual trial-abuse/seat-integrity control.
- **New enforcement**: 30-day Free trial expiry (`ensure_trial_active`, every building chokepoint);
  machine binding at the LLM proxy and compile-reserve chokepoints; distribution/white-label gating on
  installer upload and publish; `ops_tier` gating across the dashboard/audit/drift routes; analytics
  retention filtering on read; a compile-credit add-on (+25/mo, stacks via Cashfree); a tiered
  free/starter/pro LLM router pool.
- **Enterprise BYOK** (Azure OpenAI) — `app/services/byok.py`, AES-256-GCM key-at-rest, new
  `PUT/GET/DELETE /api/v1/workspace/llm-key` routes, `LLMRouter.call_entry_directly` for the one-off
  deployment call path.
- **Deferred, not built** (see `TODO.md`): Free's running-side 1-install cap (the obvious enforcement
  point is deliberately public/unauthenticated telemetry — a real gate needs an authenticated
  install-provisioning step); write-side telemetry retention pruning (read-side filtering is
  correctness-complete); build-queue priority (no server-side job queue exists to prioritize —
  `jobs.py::enqueue_job` has zero callers today); Bedrock/Vertex BYOK; a Settings BYOK/device-list UI
  panel.

**Files:** `conxa-cloud/backend/app/services/entitlements.py`, `app/services/byok.py`,
`app/services/saas.py`, `app/services/tracking.py`, `app/api/llm_proxy_routes.py`,
`app/api/entitlement_routes.py`, `app/api/machine_binding.py`, `app/api/byok_routes.py`,
`app/api/publish_routes.py`, `app/api/tracking_routes.py`, `app/api/product_routes.py`,
`app/api/cashfree_routes.py`, `app/llm/router.py`, `packages/conxa-core/conxa_core/config.py`,
`conxa-builder/python/services/machine_id.py`, `conxa-builder/python/services/llm_proxy_client.py`,
`conxa-builder/python/backend.py`, `conxa-cloud/frontend/app/(marketing)/pricing/page.tsx`,
`conxa-cloud/frontend/src/components/marketing/sections/PricingTable.tsx`, `docs/PRD.md`,
`docs/cost_model.md`, `docs/TRD.md` §13, `docs/Backend-Schema.md` §5.3/§5.4/§7.

**Verified:** 715 backend tests passing (`cd conxa-cloud && pytest -q tests`), including new coverage
for machine limits, trial expiry, distribution gating, ops_tier gating, addon stacking, and BYOK
routing. Frontend `npm run lint` and `npm run build` both clean, `/pricing` renders as a static route.

---

### ✅ 1.13 Plugin → Workflow/SkillPack Data Model Rename — DONE 2026-08-12

**What was changed:** The core data model and API contracts were fundamentally restructured to reflect the new cardinality: N Workflows per Workspace : 1 SkillPack per Company.

**Old model:** One `Plugin` per company, holding a list of `PluginWorkflow` children and shared `build`/`installer` metadata. Multiple recordings per automation required cloning the Plugin entity.

**New model:**
- **`Workflow`** — flat, exactly ONE login session + ONE recording + ONE compiled skill per workflow entity. Fields: id, slug, name, status (needs_auth/ready/error), auth (optional WorkflowAuth), recording_status (recorded/compiled/error), skill_id, compile_status, etc. Stored per-workflow.
- **`SkillPack`** — workspace-scoped (keyed by workspace_id), shared across all workflows in that workspace. Holds the single build + installer metadata. One per workspace, forever.

**What shipped (2026-08-12):**
- **Core models:** `packages/conxa-core/conxa_core/models/workflow.py` (new `Workflow`, `WorkflowAuth`, `SkillPack`, `SkillPackBuild`, `SkillPackInstaller`); old `plugin.py` deleted.
- **Storage:** `workflow_store.py` (replaces `plugin_store.py`, KV namespace `workflows`), `skill_pack_store.py` (new, KV namespace `skill_packs_meta`, keyed by workspace_id).
- **Studio Python backend:** `handlers/workflows.py` (replaces `handlers/plugins.py`), `skill_package_builder.py` (replaces `plugin_builder.py`), updated `handlers/compile.py`, `handlers/session.py`, `handlers/runs.py`.
- **Cloud backend:** `/api/v1/workflows/*` routes (replace `/api/v1/plugins/*`), telemetry field renames (`plugin_id`/`plugin_ver` → `workflow_id`/`workflow_ver` in wire format `wfid`/`wfv`), updated `tracking_routes.py`, `publish_routes.py`, `skillpack_update_routes.py`, `entitlements.py`.
- **Runtime:** telemetry wire format `wfid`/`wfv` (was `pid`/`pv`), server.js MCP tools updated, tracker.js + test fixtures.
- **Studio UI:** `/workflows` (Workflow List) replaces `/record`, `/workflows/:id` (Workflow Detail) replaces Plugin Detail + Record, inline auth + recording actions, deleted Record page entirely. `WorkflowSwitcher` replaces `PluginSwitcher`.
- **Cloud dashboard:** `/packages` (Skill Packages List, replaces `/plugins`) `/packages/:workspace_id` (Skill Package Versions, replaces Plugin Detail), scoped to workspace_id.
- **Docs:** `docs/Backend-Schema.md` §2 (data models), §6.1 (ERD), §7 (KV namespaces) rewritten; `docs/App-Flow.md` restructured (Workflow Detail inline actions); `docs/UI-UX-Brief.md` redesigned (Workflow List/Detail specs); `docs/Implementation-Plan.md` updated; `CLAUDE.md` updated for new file/symbol references.

**Files (non-exhaustive):** `packages/conxa-core/conxa_core/models/workflow.py`, `storage/workflow_store.py`, `storage/skill_pack_store.py`, `conxa-builder/python/handlers/workflows.py`, `conxa_compile/skill_package_builder.py`, `conxa-cloud/backend/app/api/workflow_routes.py`, `publish_routes.py`, `skillpack_update_routes.py`, `tracking_routes.py`, `conxa-builder/electron/renderer/src/pages/WorkflowListPage.tsx`, `WorkflowPage.tsx`, `api/workflowsApi.ts`, `conxa-cloud/frontend/src/SkillPackagesPage.tsx`, `docs/*.md`.

**Tests:** 721/722 backend tests passing, 54/54 runtime tests passing, Studio UI tsc clean, cloud frontend lint + build clean. One pre-existing external-API test skipped.

**Breaking change (customer impact):** API routes moved from `/api/v1/plugins/...` to `/api/v1/workflows/...`. Old installed customer runtimes (using baked-in `/api/v1/plugins/...` endpoints) will break until reinstalled. This was an explicit accepted tradeoff per the design decision — no backward-compat support.

**Deferred (still open):** End-to-end UI/backend functional tests (e2e: create workflow → record login → record workflow → compile → edit → sign-off → build skill package, confirming two workflows share one skill package + installer). Tracked in `TODO.md`.

---

### ✅ 1.14 Group Page absorbs the Workflow Detail page — DONE 2026-08-13

**What was changed:** The standalone `/workflows/:workflowId` detail page (`WorkflowPage.tsx`, added in 1.13) was removed. Every action it owned — record, compile/recompile, review (Human Edit), test, and (once passing) hand-off to Publish — now lives inline on each workflow's row on its group's page (`GroupPage.tsx`), as a five-node `WorkflowStageRail` (Record → Compile → Review → Test → Ready to Package) replacing the old read-only `StagePath` dots. `RecordWorkflowDialog` and `DeleteWorkflowButton` were extracted into standalone components so the group page's row could reuse them; the Test node expands `WorkflowTestRow` inline instead of navigating to Test Skill. `GroupAuthWizard` gained an `editable` mode (per-app edit/remove) so the group page no longer needs a separate app list. `/workflows/:workflowId` now redirects to the workflow's owning group, keeping existing deep links, the compile page's "← Back", and Human Edit's `?from=` working unchanged. `store/selectionStore.ts` (the removed page's only consumer) was deleted.

**Files:** `conxa-builder/electron/renderer/src/pages/GroupPage.tsx`, `App.tsx`, `components/StagePath.tsx` (`WorkflowStageRail`), `components/RecordWorkflowDialog.tsx` (new), `components/DeleteWorkflowButton.tsx` (new), `components/GroupAuthWizard.tsx`, `components/EntitlementMeters.tsx` (`MeterBadge`); deleted `pages/WorkflowPage.tsx`, `store/selectionStore.ts`. Docs: `docs/UI-UX-Brief.md` §2.3a/§2.8/§2.12, `docs/App-Flow.md` §4.

**Verified:** `npx tsc --noEmit` and `npm run build:renderer` clean, `npm run lint` clean (0 errors, 2 pre-existing unrelated warnings).

---

### ✅ 1.15 Conxa Execute Seat Grants + AI Usage Credits Rename — Backend + UI DONE 2026-09-16

**What was changed:** Connects Build Studio's per-workspace plan billing to Conxa Execute, which
previously had no workspace concept at all. A new `execute_seats` quota (Free=1, Starter=25, Pro=100,
Enterprise=contracted) lets a workspace admin grant Conxa Execute access to people who never touch
Build Studio, independent of Clerk org membership. A granted person's Execute chat usage draws
entirely from the granting workspace's shared LLM token pool — the existing per-workspace "Human Edit
Pool" (`human_edit_tokens`), renamed customer-facing to **"AI Usage Credits"** since it's no longer
just a Build Studio concept.

**What shipped:**
- **conxa-cloud backend:** `execute_seats` in `PLAN_LIMITS`/`_QUOTA_ALIASES`; `ExecuteGrant` KV data
  model (`execute_grants`/`execute_grant_by_user` namespaces) with
  create/list/revoke/claim/binding-lookup functions mirroring the existing machine-registry pattern;
  admin CRUD routes plus a Phase-1 Cloud-Dashboard claim fallback
  (`app/api/entitlement_routes.py`); a new service-token-authenticated internal bridge
  (`app/api/execute_bridge_routes.py`, `/api/v1/internal/execute/*`) for conxa-execute to check/debit
  the pool; a workspace-id-scoped refactor of `ensure_human_edit_available`/`record_llm_usage` (new
  `ensure_execute_pool_available`/`record_execute_pool_usage`) so a claimed grant never registers the
  claimant as a Build Studio workspace member — see the correctness note in `docs/TRD.md` §13.7.
  `GET /entitlements/current` dual-emits `ai_usage_credits` alongside the deprecated
  `human_edit_tokens` key.
- **conxa-execute backend:** `app/cloud_bridge.py` (new), `app/routes_grants.py` (new, `POST
  /v1/execute-grants/claim`), `auth.py::get_current_claims` (additive), `routes_proxy.py`'s `/v1/
  entitlement` (new `workspace_pool` mode) and `/v1/chat/completions` (reroutes pre-flight/post-debit
  through the Cloud bridge for a pool-bound user, no personal wallet/subscription fallback).
- **Rename surface:** conxa-cloud/frontend (`BillingPage.tsx`, `productApi.ts`, `billingData.ts`,
  `PricingTable.tsx`, `content/publicDocs.ts`), conxa-builder/electron renderer
  (`EntitlementMeters.tsx`, `usageApi.ts`, `errorMessages.ts`, `GroupPage.tsx`), conxa-builder/python
  (`backend.py`, `llm_proxy_client.py`, `copilot.py` docstring). Internal identifiers, storage keys,
  `usage_class="human_edit"`, and the `human_edit_pool_exceeded` error code are all unchanged —
  display-only rename, zero migration. The "Human Edit" workflow-editing screen/feature name
  (`HumanEditPage.tsx`) is a distinct concept and was deliberately left untouched.
- **Docs:** `docs/TRD.md` §3.6, §13.5, new §13.7; `docs/Backend-Schema.md` §5.3 + KV namespace table.

**Tests:** 3 new tests in `conxa-cloud/tests/test_entitlements.py` (seat-cap + idempotent re-invite,
claim binds the pool without creating a workspace membership, email-mismatch rejection + revoke frees
the slot); full `conxa-cloud` suite (1528 tests) and both frontends' typechecks pass clean.

**UI shipped 2026-09-16 (CLOUD-22):** a Conxa Execute Seats panel on the Cloud Dashboard's Team page
(list/create/revoke, invite links), a public `/claim/[grantId]` landing page, and a "Have an invite
code?" redeem field in Conxa Execute's Settings showing "Paid by `<workspace>`" once claimed. Fixed
along the way: the invite link previously routed through the Cloud Dashboard's own Clerk app, which
can never bind the identity Conxa Execute's separate Clerk app checks (claiming now happens inside
Conxa Execute itself); and a claimed grant wasn't switching Conxa Execute's locally saved chat mode
off BYOK, which would have silently kept routing chat around the metered pool. A small additive
backend change (`saas.py::workspace_name_for`, `entitlements.py::execute_pool_status`) lets the
internal Execute bridge return the workspace's display name and real remaining-credits count.
No transactional email sender exists in this repo — an admin still shares the invite link manually.
See `docs/UI-UX-Brief.md` §3.7 and `TODO.md` CLOUD-22 for details.

**Update (2026-09-17 — the standalone-backend/separate-Clerk-app premise above was reversed):**
Conxa Execute's own backend (`conxa-execute/backend/`), its separate Clerk application, the
`execute_bridge_routes.py` service-token bridge, BYOK mode, and the invite-link/claim-code UI
described above are all **deleted**. Execute now shares conxa-cloud's backend and Clerk app
directly; grants auto-claim by email with no code to paste; chat is metered as its own
`execute_chat` usage class (same pool as `human_edit`, separate counter). See `docs/TRD.md` §3.6
and §13.4c for the current design, and `TODO.md`'s Execute-merge item for the full change list.

---

**Phase 1 status: COMPLETE except for 1.9, tracked above as new work discovered after this
phase's original closure.** The rest of Phase 1 (1.1-1.8, 1.10-1.13) is done, superseded, or moot;
other open work has moved to Phase 2 (drift gate, macOS, code signing, selector-cache GC,
billing enforcement, error-message UX) and new discoveries (e2e testing, orphaned dev scripts).

### ✅ 1.16 Conversation-led Build Studio — DONE 2026-10-18 (phases 0-8; see open follow-ups)

One continuous conversation per workflow (`/w/<id>`) replaces the page-per-feature flow: shell and panels (BUILD-36), thread store, lifecycle and harness with the `ai_credits` metering rename (BUILD-37, CLOUD-25), recording in the conversation (BUILD-38), compile card, Clarify and summary (BUILD-39), test guard, auto-fix and approval (BUILD-40, RT-9), Human Edit restyle (BUILD-41), the embedded test browser (BUILD-42), recording and sign-in in that same browser panel (BUILD-46, 2026-10-20; follow-ups BUILD-48) and system states, Settings popup and first launch (BUILD-43). Design: `docs/artifacts/build-studio-redesign/`; plan and decisions: `docs/Build-Studio-Conversation-Plan.md`; technical detail: TRD §4.7a, §7.4a, §7.4b, §11.3; flow: App-Flow §3a; screens: UI-UX-Brief §12-14. **Needs before it ships:** a new `app-vX.Y.Z` runtime tag (the guard and the session-expiry tag are app-layer changes), a cloud redeploy (new LLM tasks, `ai_credits`), and a run against a live site (see BUILD-44, BUILD-45).

---

## Phase 2 — Production Readiness

**Goal:** Make the platform ready for enterprise evaluation and reliable at scale. This phase adds observability, correctness, and operational controls.

**Timeline estimate:** 6–10 weeks

---

### ✅ 2.1 Device & Runtime Registration — DONE 2026-06-02

**What was missing:** The cloud had no visibility into deployed runtimes. `POST /api/v1/telemetry/runtime-start` was a no-op stub.

**What was built:**
- `post_telemetry_runtime_start` now stores a registration record per `(company, platform)` in the `runtime_registrations` KV namespace. Workspace is derived from the `sync_tokens` KV entry (set at publish time) — no new credential needed from the runtime.
- Added `GET /api/v1/telemetry/runtimes` (Clerk-authed, workspace-scoped): returns registrations, stale count (not seen in 30 days), and version distribution.
- Fixed `_phonehome()` in `runtime/server.js`: was calling `${CONXA_API}/telemetry/runtime-start` (missing `/api/v1/`); now calls `${CONXA_API}/api/v1/telemetry/runtime-start` using the module-level `CONXA_API`.
- Moved phonehome to fire after sync so `companies[]` reflects the current skill index rather than the pre-sync cache.
- Added `RuntimeRegistrationsCard` to the Dashboard: shows active/stale status per company, version distribution, last-seen time.
- Added `/api/v1/telemetry/runtime-start` to `PUBLIC_PATHS` in `security.py` (exact path only) so installed runtimes don't need Clerk auth for this non-critical endpoint.

**Files:** `skillpack_update_routes.py`, `security.py`, `runtime/server.js`, `DashboardPage.tsx`, `pluginApi.ts`

> **Superseded 2026-08-07 (operations-dashboard redesign):** `DashboardPage.tsx` no longer
> exists. Runtime-registration visibility now lives in `src/dashboard/sections/FootprintStrip.tsx`
> (installs, active users, active companies, runtimes gone quiet) on `/dashboard`, and the stale
> count is also surfaced on `/dashboard/impact` and feeds the platform health score as its
> "runtime freshness" factor. See `docs/UI-UX-Brief.md` §3.2.

---

### 2.2 Implement Drift Detection

**✅ Implemented (2026-07-01).** The pre-execution gate ships. `structural_fingerprint` is now
plumbed from `SkillMeta` into the runtime `manifest.json` (`plugin_builder.py`) and checked at run
start in `runtime/drift.js` (called from `runPlan` in `run.js`). It scores the recorded landmarks
against the live page with the pure resolver (no LLM) and emits `drift_detected` — warn only, never
blocks. The cloud aggregates these per plugin version and surfaces them at `GET /drift`
(`_pre_exec_drift_queue`). Unit tests: `runtime/test/test_drift.js`, `tests/test_skill_pack_fingerprint.py`.

**Original design notes:** `SkillMeta.structural_fingerprint` stores the hash of the first 3 steps' landmark selectors. This was designed for pre-execution drift detection.

**Fix:**
- In `runtime/run.js`, before executing step 0: check if current page's landmark selectors match `structural_fingerprint`.
- If mismatch exceeds threshold: emit `drift_detected` event and warn Claude: "This workflow may need to be recompiled. The page structure has changed."
- Do not block execution — warn only.

**Files:** `runtime/run.js`, `runtime/tracker.js`

**Partially addressed by the Final Selector Architecture (§2.9):** runtime now emits structured
`repair_event` drift signals on every recovery, aggregated into an admin review queue at
`GET /api/v1/tracking/{company}/drift`. Pre-execution `structural_fingerprint` matching is still
open; runtime-side post-hoc drift surfacing is implemented.

---

### ✅ 2.9 Final Selector Architecture — DONE 2026-06-22

**What was built:** end-to-end durability-ranked element identity + zero-token replay/recovery.

- **Compile (Python):** `IdentityBundle` / `IdentitySignal` (`packages/conxa-core/.../skill_spec.py`),
  durability scoring + orthogonality classes (`selector_score.py`), uniqueness / PII-bind /
  xpath-shadow gates (`selector_filters.py`), `stable_hash.py`, deterministic-floor Playwright-grammar
  generator (`identity_bundle.py`), wired through `compiler/build.py`. Multi-signal
  `FrameFingerprint` (`recorder/session.py`), `shadow_path`, and `hover_chain` hints
  (`action_semantics.py`).
- **Replay (Node):** pure `runtime/resolver.js` (strict uniqueness gate, stable_hash tie-break),
  GATE + VERIFY in `runtime/run.js`.
- **Recover (Node):** `runtime/recovery.js` L1 exception ladder + L2 re-hover/a11y cascade,
  structured `repair_event` emission.
- **Flywheel (Cloud):** admin-gated drift queue `GET /api/v1/tracking/{company}/drift`
  (`tracking_routes.py`).

**Tests:** `tests/test_element_fingerprint.py` 66/66; Node `test_resolver.js` / `test_verify.js`
/ `test_recovery.js` all green.

**Still open:** LLM enrichment of residual-uncertainty signals (deterministic floor ships now);
closed-shadow CDP pierce fallback; pre-execution `structural_fingerprint` drift gate (§2.2).

---

### ✅ 2.3 Audit Log — DONE 2026-06-02

**What was missing:** No record of which user took which action. The Settings page called `GET /api/v1/audit-events` which was a stub returning `[]`.

**What was built:**
- New `add_audit_event(principal, action, resource_type, resource_id, metadata)` helper in `app/services/saas.py` appends to a per-workspace `audit_events` list (capped at the last 500) in the SaaS state store. `GET /api/v1/audit-events` — implemented as `list_audit_events()` in `app/api/product_routes.py`, backed by `audit_events_for(principal, limit)` — returns workspace-scoped entries, most recent first, paginated by `limit` param (max 500).
- Events written on: `publish` (publish_routes.py), `installer_upload` (publish_routes.py), `plugin_create` and `plugin_delete` (plugin_routes.py).
- Settings page now gets real data instead of the old stub.

**Fields per entry:** `id`, `workspace_id`, `user_id`, `action`, `resource_type`, `resource_id`, `metadata`, `created_at` (epoch seconds).

**Files:** `app/services/saas.py`, `app/api/product_routes.py`, `publish_routes.py`, `plugin_routes.py`

> **Correction (2026-07-04):** this entry originally named `audit_routes.py` (new) and `v1_alias_routes.py` as the implementing files. Neither exists in the current codebase — the functionality described above is real and shipped, but lives in `product_routes.py` + `services/saas.py` as corrected above.

---

### ✅ Runtime Split Architecture — DONE 2026-06-20

**What was built:** Eliminated the 89 MB self-update download on every code release by splitting the monolithic `runtime-win.exe` into two independently-updateable layers.

**Changes:**
- **Host layer** (`conxa-runtime.exe`, ~85 MB): Node.js + all npm deps + `bootstrap.js`. Updated only when Node.js, Playwright, or native deps change (quarterly).
- **App layer** (`conxa-app/`, ~60 KB zip): all application JS obfuscated via `javascript-obfuscator`. Hot-synced on every cold start with no restart required.
- `bootstrap.js` (new) is the pkg entry point. Loads `conxa-app/server.js` from disk; falls back to bundled copy if absent or `min_host` incompatible.

> **Correction (2026-07-04):** this entry originally described the app layer as compiled to V8 bytecode (`.jsc` via `bytenode`) and loaded as `server.jsc`. That approach was tried and reverted — V8 bytecode masks the Node version and caused the Playwright selector engine to segfault in pkg-bundled binaries (see `docs/TRD.md` §4.4/§5.7 and the `--no-bytecode` Key Invariant in `CLAUDE.md`). The app layer ships as plain obfuscated JS (`server.js`), not bytecode.
- `(global.__hostRequire || require)` bridge lets disk-loaded `.jsc` files resolve npm deps bundled in the host VFS.
- **Sync optimisation:** `sync.js` rewritten — parallel company sync (`Promise.allSettled`), parallel file downloads (`Promise.all`), 5-min recency skip (client-side, prevents 429s), reduced timeouts (delta: 3s, files: 8s). Outer timeout: 15s → 4s.
- **`syncState` execution gate:** `execute_skill` awaits both skill-pack sync and app-layer update before running. Never hangs (all failures caught, gate opens with cached data).
- **Cloud API:** new endpoints `GET/POST /api/v1/updates/conxa-runtime-manifest` and `GET/POST /api/v1/updates/conxa-app-manifest`. POST endpoints require `CONXA_ADMIN_TOKEN` (CI-only). Old `runtime-manifest` endpoint replaced.
- **CI workflows** split into `build-runtime-host.yml` (`host-v*` tags) and `build-runtime-app.yml` (`app-v*` tags).
- **Installer** now stages `conxa-runtime.exe` + `runtime-app/` (pre-extracted) so first run needs no network.

**Result:** Code-only release download: 89 MB → ~60 KB. Update time: ~70s → <1s on any connection.

**Files:** `runtime/bootstrap.js` (new), `runtime/server.js`, `runtime/sync.js`, `runtime/browser.js`, `runtime/package.json`, `conxa-cloud/backend/app/api/updates_routes.py`, `conxa-builder/python/conxa_compile/installer_builder.py`, `conxa-builder/python/services/bootstrap.py`, `conxa-builder/python/conxa_compile/installer_templates/setup.nsi.tmpl`, `.github/workflows/build-runtime-app.yml` (new), `.github/workflows/build-runtime-host.yml` (new), `.env.example`

---

### ✅ Enterprise-Grade Auto-Update Architecture — DONE 2026-07-01

**What was built:** Replaced the two-layer split's `.bak`/`.next` single-backup update mechanism and two unsigned manifest endpoints with a versioned-directory + single-signed-manifest architecture. See TRD.md §4.1, §4.4, §4.5, §5.7, §5.7 for the authoritative reference.

**Changes:**
- **Versioned directories.** Every component — `conxa-runtime`, `conxa-app`, and each individual skill — is now `<component>/<version>/` with a `current` directory junction, retaining the last 3 versions (`runtime/version_manager.js`, new). Rollback is instant and needs no re-download; junctions were chosen over JSON pointer files specifically because Claude Desktop's MCP config stores a literal path to the host exe, which only the OS can resolve transparently.
- **One Ed25519-signed manifest.** `GET /api/v1/manifest.json` (new) replaces the two `conxa-runtime-manifest`/`conxa-app-manifest` endpoints as the runtime's source of truth (old endpoints kept as deprecated shims reading the same data). Signed server-side with a private key that never touches CI; the runtime verifies against a public key baked into the host exe and discards anything that fails verification, same as a network failure. `runtime/manifest_manager.js` (new) is the client.
- **Real staged rollouts.** Each component version carries a `rollout.percentage`; the runtime deterministically buckets itself (hash of install_id, salted per component) so a canary rollout is stable across polls, not re-randomized every check.
- **Independent per-skill versioning.** `skillpack_update_routes.py`'s delta endpoint now compares each skill's own version (from a new `component_versions` KV namespace) instead of one shared per-company version — republishing one skill never triggers a re-download of the others. `runtime/sync.js` rewritten to match.
- **Selfcheck before activation.** A newly downloaded host exe is spawned once with `--selfcheck` before `current` is ever pointed at it — a matching SHA-256 only proves the download wasn't corrupted, not that the binary boots.
- **Cloud persistence.** Manifest/component-version state moved from process-local Python globals (lost on every Render restart or across worker processes) to the existing `conxa_core.db` KV dual-store, in new `component_versions` and `manifest` namespaces.
- **Installer** now lays out the versioned structure from the start (`installer_builder.py` nests each skill under its own `v`-prefixed version directory; `setup.nsi.tmpl` creates the `current` junctions and registers the MCP command through `conxa-runtime\current\`), so the initial install already matches the layout every later update writes into. No customer migration needed — pre-production, greenfield.

**Result:** Instant no-network rollback (vs. one-step-only before); tamper-proof update manifest (vs. unsigned); staged rollout capability (vs. all-or-nothing); per-skill update granularity (vs. whole-company re-sync).

**Follow-ups since:**
- **Same-launch app updates + `min_host` enforcement (2026-08-03).** The `conxa_app` check moved out of `server.js`'s `startupSync` and into `bootstrap.js`, running *before* the app layer is `require()`'d — so a new app version is live on the launch that downloaded it instead of the next one. The manifest's local TTL cache was dropped (every launch fetches fresh, cache is failure-fallback only), `checkForUpdates()` gained a `components` filter, and the host leg now reuses the manifest bootstrap already fetched. A `min_host` floor is checked at decision time as well as load time, so a too-new app layer is never installed on an old host rather than being activated and rolled back on every launch. The pre-load leg runs on a deliberately tight budget (3s manifest, 2 retries × 5s zip) with every failure swallowed. `runtime/bootstrap.js`, `manifest_manager.js`, `server.js`, `test/test_manifest_manager.js`; TRD §4.4/§5.7/§5.7.
- **Signing key required in production (2026-08-04).** `_validate_production_config()` now refuses to boot without `CONXA_MANIFEST_SIGNING_KEY`. Absent it, the manifest is served unsigned and every runtime silently discards it — self-updates would stop fleet-wide with no error on either end. `conxa-cloud/backend/app/main.py`, `tests/test_product_routes.py`.
- **CI execution gate re-enabled (2026-08-04).** `build-runtime-app.yml` replays a real skill against the declared `MIN_HOST` exe before the zip/release/publish steps. Its first run caught a stale `MIN_HOST` (`host-v1.1.2` → `host-v2.0.0`); every app layer published since 2026-07-30 had been shipping a false `min_host` claim. See TODO.md ARCH-2.

**Files:** `runtime/version_manager.js` (new), `runtime/manifest_manager.js` (new), `runtime/bootstrap.js`, `runtime/server.js`, `runtime/sync.js`, `runtime/skill_loader.js`, `runtime/test/test_version_manager.js` (new), `runtime/test/test_manifest_manager.js` (new), `runtime/test/gate_replay.js`, `packages/conxa-core/conxa_core/models/manifest.py` (new), `conxa-cloud/backend/app/api/manifest_signer.py` (new), `conxa-cloud/backend/app/api/updates_routes.py`, `skillpack_update_routes.py`, `publish_routes.py`, `conxa-cloud/tests/test_manifest_signing.py` (new), `conxa-builder/python/conxa_compile/installer_builder.py`, `conxa-builder/python/conxa_compile/installer_templates/setup.nsi.tmpl`, `.github/workflows/build-runtime-host.yml`, `build-runtime-app.yml`

---

### 2.4 macOS Runtime Support

**What's present:** The build scripts reference macOS targets (`build:mac` in `runtime/package.json`). `CONXA_DIR` resolves to `~/.conxa` on non-Windows. The runtime code is platform-aware.

**What's missing:** No macOS installer builder in `installer_builder.py`. No tested macOS distribution path.

**Fix:**
- Add macOS installer generation (PKG or DMG) to `installer_builder.py`.
- Test runtime on macOS (auth_manager, keytar, Playwright).
- Add macOS to the `updates/runtime-manifest` response.

**Files:** `conxa-builder/python/services/installer_builder.py`, `updates_routes.py`

---

### 2.5 Installer Code Signing

**Status (corrected 2026-07-04): code/wiring done, certificate not yet procured.** `installer_builder.py` already runs a conditional `signtool.exe sign /sha1 ... /fd SHA256 /tr http://timestamp.digicert.com` step after the NSIS build, gated on `CONXA_SIGNTOOL_PATH` (default `signtool.exe`) and `CONXA_SIGN_CERT_SHA1` env vars. This entry previously described the signing step itself as missing — it isn't; only a real Windows EV code-signing certificate installed in the local certificate store (referenced by its SHA-1 thumbprint via `CONXA_SIGN_CERT_SHA1`) remains to be procured. Until that happens, the installer builds unsigned and Windows SmartScreen will show an "Unknown Publisher" warning.

**Remaining work:**
- Procure a Windows EV code-signing certificate and install it in the build machine's certificate store.
- Set `CONXA_SIGN_CERT_SHA1` (and `CONXA_SIGNTOOL_PATH` if `signtool.exe` isn't on PATH) in the CI/build environment.

**Files:** `conxa-builder/python/conxa_compile/installer_builder.py` (signing step already implemented)

---

### 2.6 Selector Cache GC

**✅ Implemented (2026-07-01).** `snapshots_gc.py` only covered session snapshot blobs; the selector
cache had *no* bulk GC (only lazy per-read expiry, which never deleted). Added
`selector_cache.cleanup_expired_entries()` (purges expired KV entries + on-disk cache files) and a
background loop in the cloud lifespan (`main.py`) that runs it plus `cleanup_old_snapshots()` at
startup and every `gc_interval_secs` (default 6h). Test: `tests/test_selector_cache_gc.py`.

**Original notes — What's present:** Selector cache (`conxa_core/storage/selector_cache.py`) has a `ttl_days` config (30 days). GC function exists (`snapshots_gc.py`).

**Was missing:** No scheduled job ran the GC. The cache grew without bound.

**Fix:**
- Add a startup task (or Render cron job) to run selector cache GC on schedule.
- Log items evicted and cache size.

**Files:** `conxa_cloud/backend/app/main.py` (lifespan), `selector_cache.py`, `snapshots_gc.py`

---

### 2.7 Hardened Billing Integration

**✅ Implemented (2026-07-01).** Correction to earlier notes: the payment provider is **Cashfree**
(`cashfree_routes.py`), not Razorpay, and a full entitlements service already existed
(`app/services/entitlements.py`, `PLAN_LIMITS` for Free/Starter/Pro/Enterprise/development) — it was
simply gated off. This item: (a) turned the `entitlements_enforce_*` flags on by default
(`config.py`); (b) added a plan/installer-slot gate at publish (`publish_routes.py`); (c) kept
compile-credit enforcement via the existing reserve→commit→release protocol Build Studio already
drives (`backend.py`), and the Human-Edit token pool at the LLM proxy; (d) reconciled the flat
`llm_metering` token backstop with the plan-aware meters (documented inline in `llm_proxy_routes.py`);
(e) derived the Billing-page feature copy from `PLAN_LIMITS` so numbers can't drift. `development` and
any `None` limit stay unlimited, so local dev is unaffected.

**Files:** `app/services/entitlements.py`, `app/api/publish_routes.py`, `app/api/llm_proxy_routes.py`, `app/api/cashfree_routes.py`, `packages/conxa-core/conxa_core/config.py`

---

### 2.8 Error Code User-Friendly Mapping (UI)

**✅ Implemented (2026-07-01).** Added `renderer/src/lib/errorMessages.ts` (a `Record<code, message>`
covering the full backend `_CommandError` set plus transport codes) and upgraded the shared
`errorMessage(err, fallback)` helper in `workflowApi.ts` to prefer `errorMessages[err.code]`, then the
raw backend message, then the caller's fallback. Direct `.message` display sites (BuildInstallerPage,
CompileProgress, RecordingFeed, SetupWizard, LoginOverlay) now route through the helper; the many
`toast.error(errorMessage(...))` sites improve automatically.

**Files:** `conxa-builder/electron/renderer/src/lib/errorMessages.ts` (new), `renderer/src/api/workflowApi.ts`, and the display sites above

---

## Phase 3 — Enterprise Readiness

**Goal:** Pass enterprise security review and support multi-engineer team workflows.

**Timeline estimate:** 8–12 weeks

---

### 3.1 SSO / SAML

- Enable Clerk Enterprise with SAML support.
- Configure per-organization SSO.
- Map SAML groups to Conxa workspace roles.
- Session management: enforce SSO session timeout policy.

**Dependencies:** Clerk Enterprise plan.

---

### 3.2 Multi-User Workspace Publishing

**Current state:** Only the slug owner can publish updates.

**Fix:**
- Any workspace member with `admin` or `owner` role can publish to a workspace-owned slug.
- Add workspace transfer for slug ownership.
- Implement invitation flow (currently UI exists in Team page but backend not fully wired).

**Files:** `publish_routes.py`, `app/services/saas.py`, `app/services/rbac.py`

---

### 3.3 On-Premise Option

- Package the FastAPI backend as a self-hosted option (Docker Compose).
- Replace Render-specific dependencies with configurable alternatives.
- Document self-hosted configuration.
- Build Studio points to customer's own cloud backend via Settings.

**Files:** `conxa-cloud/backend/Dockerfile` (already exists), `docker-compose.yml` (new)

---

### ✅ 3.4 Workflow Version History & Rollback — DONE 2026-08-19

**What was built:** the full Enterprise Skill Package Release System — immutable
per-version artifact snapshots, a stable-channel pointer, rollback, a deterministic
diff, a per-slug release audit trail, and a Release Center UI in Build Studio (primary)
with a read-only mirror on the Cloud dashboard.

- **Cloud (`app/api/skillpack_storage.py`):** new immutable per-version snapshot
  storage (`skillpack_release_files_ns`/`skill_pack_release_dir`,
  `write_release_snapshot`/`read_release_snapshot`) alongside the existing mutable
  "currently live" mirror (`write_mutable_mirror_files`, `write_pack_json_mirror`/
  `read_pack_json_mirror`) that `_build_delta` already served unchanged.
- **Cloud (`app/services/release_channel.py`, new):** the stable-channel pointer
  (`skillpack_channels` KV, one row per slug) and the per-slug, unbounded release
  event log (`skillpack_release_events__{slug}`), mirrored into the existing
  `saas.add_audit_event` so the dashboard's Audit page needed no changes.
- **Cloud (`app/services/release_diff.py`, new):** deterministic (stdlib `difflib`,
  no LLM) diff between two release file sets, aligning execution steps by a
  semantic content key rather than position so a re-healed selector reads as
  "modified", not "removed + added".
- **Cloud (`app/api/publish_routes.py`):** `_publish_skill_pack_impl` rewritten as
  a release transaction — immutable snapshot + pending version row + mutable
  mirror + manifest, all written before the channel pointer moves as the final,
  single act of activation; a duplicate version or a byte-identical republish is
  rejected outright (409 `skill_pack_version_exists` / `skill_pack_artifact_unchanged`).
- **Cloud (`app/api/release_routes.py`, new):** `POST .../releases/preview`,
  `GET .../releases/{version}`, `GET .../releases/{version}/diff`,
  `POST .../releases/{version}/rollback`, `GET .../deployments`,
  `GET .../releases/events` — all under `/api/v1/workflows/{installer_version}/...`, workspace derived from Clerk session, `require_admin`-gated.
- **Cloud (`app/api/skillpack_update_routes.py`):** telemetry's `TelemetryBody`
  gained an optional `skill_versions` field; `_build_delta`/`_skill_version`
  themselves are **unchanged** — rollback restores the mutable mirror + `component_versions`
  from the immutable snapshot, so the sync hot path never needs to know about channels.
- **Build Studio (`python/handlers/workflows.py`, `python/backend.py`):** thin
  proxy RPC handlers (`cmd_release_preview`, `cmd_release_detail`, `cmd_release_diff`,
  `cmd_rollback_release`, `cmd_list_deployments`, `cmd_release_events`); publish now
  emits real `stage` markers (`validated`/`uploading`/`published`/`failed`) instead
  of one opaque log line.
- **Build Studio (`renderer/src/pages/PublishPage.tsx`):** rewritten into the full
  Release Center (candidate → diff → where-it-goes → history → deployment → audit),
  with explicit idle/publishing/success/failure states. New pure-logic module
  `renderer/src/lib/releaseState.ts` (state derivation, diff summarization, badges)
  covered by `test/releaseState.test.mjs` (`node --experimental-strip-types --test`).
- **Cloud dashboard (`SkillPackageVersionsPage.tsx`):** read-only Release History +
  Deployment sections — no publish/rollback controls; those stay Studio-only.
- **Runtime (`server.js`, `installed_versions.js` new):** phone-home telemetry now
  reports installed skill versions per company, read off the same `current`
  junction / `version.json` sync.js already writes. Optional field — an
  already-deployed runtime that hasn't self-updated yet just reports nothing for it.

**Known limitations (by design, not oversight):** rollback is only available for
releases published after this change (pre-existing published packs have no
per-version snapshot to roll back to). The "Build Studio (primary) with a
read-only mirror on Cloud" ownership split described above, and the
up-to-date/pending/offline/unknown-only deployment status, were both
superseded by §3.4a below — Cloud is now the release/deployment/audit owner,
and deployment status has a real `failed` state.

**Files:** `app/api/publish_routes.py`, `app/api/release_routes.py` (new),
`app/api/skillpack_storage.py`, `app/api/skillpack_update_routes.py`,
`app/services/release_channel.py` (new), `app/services/release_diff.py` (new),
`app/main.py`, `conxa-cloud/tests/test_release_channel.py` (new, 17 tests),
`python/handlers/workflows.py`, `python/backend.py`,
`renderer/src/pages/PublishPage.tsx`, `renderer/src/lib/releaseState.ts` (new),
`renderer/src/components/release/*` (new), `runtime/server.js`,
`runtime/installed_versions.js` (new), Cloud dashboard `SkillPackageVersionsPage.tsx`

---

### ✅ 3.4a Build Studio / Cloud Publish-Release Separation — DONE 2026-08-19

**Supersedes §3.4's "Build Studio (primary) with a read-only mirror on Cloud"
framing and closes its "updating/failed" known limitation.** Publishing and
deploying were the same atomic transaction — clicking Publish in Build Studio
immediately activated the version for every customer machine. Redesigned so
Build Studio is BUILD/PUBLISH only and Conxa Cloud is the sole
RELEASE/DEPLOYMENT control plane: `Workflow → Compile → Test → Publish` (Studio)
→ `Ready for Release → Review → Release/Deploy → Desired Version` (Cloud) →
`Sync → Verify → Install → Execute → Report Status` (Runtime). See
`docs/App-Flow.md` §8/§8.1/§8.1a and `docs/TRD.md` §5.5 for the full mechanism.

- **Cloud (`app/api/publish_routes.py`):** `_publish_skill_pack_impl` now stops
  after writing the immutable snapshot + a version row with the new status
  `"ready"` — it no longer touches the mutable mirror, `component_versions`, the
  manifest, or the stable channel. `PublishBody` gained `group_name`,
  `workflow_name` (display-only, for Cloud's Groups/Workflows nav) and
  `tests_passed` (Build Studio's local test-gate result, shown to Cloud
  reviewers).
- **Cloud (`app/api/release_routes.py`):** new `POST .../releases/{version}/release`
  — Cloud-only, `require_admin`-gated, the only endpoint that activates a
  `"ready"` version (mirrors `.../rollback`'s structure, opposite precondition).
  New `GET .../groups` backing the Skill Packages → Group → Workflow nav.
  `GET .../deployments` gained a `failed` status derived from telemetry (below).
- **Cloud (`app/api/skillpack_storage.py`):** new `skillpack_known_skills` KV
  (`record_known_skill`/`list_known_skills`), upserted at publish time —
  deliberately independent of `pack.json`'s `skills`/`skill_groups` union (which
  still only updates at Release) so an unreleased "ready" skill is still visible
  to Cloud admins.
- **Cloud (`app/services/release_channel.py`):** new `EVT_RELEASE_STARTED`/
  `_SUCCEEDED`/`_FAILED` events, parallel to the existing publish/rollback pairs.
- **Runtime (`sync.js`, `sync_errors.js` new):** a checksum mismatch or
  download/activation failure is now recorded per-skill in
  `pack.json.last_sync_errors`, cleared on the next successful activation, and
  reported (not sticky, unlike `skill_versions`) at the next phone-home — this
  is what closes §3.4's "failed" gap without a new transport, reusing the
  existing `runtime-start` telemetry endpoint.
- **Build Studio (`renderer/src/pages/PublishPage.tsx`):** trimmed to a bare
  post-publish confirmation ("v{version} is Ready for Release in Conxa Cloud")
  — Release History, Deployment, Audit, and Rollback no longer render here.
  `ReleaseHistoryTable.tsx`/`DeploymentPanel.tsx`/`ReleaseAuditLog.tsx`/
  `RollbackDialog.tsx` deleted from Build Studio; `cmd_rollback_release`,
  `cmd_list_deployments`, `cmd_release_events` removed from
  `python/handlers/workflows.py` (no longer called from Studio's UI).
- **Cloud dashboard:** the former read-only mirror is now the authoritative,
  interactive surface — new `GroupPage.tsx`, `WorkflowReleasePage.tsx`, and a
  ported `components/release/*` (with a new `ReleaseDialog.tsx` for the
  Release/Deploy action) under Skill Packages → Group → Workflow.

**Files:** `app/api/publish_routes.py`, `app/api/release_routes.py`,
`app/api/skillpack_storage.py`, `app/api/skillpack_update_routes.py`,
`app/services/release_channel.py`, `conxa-cloud/tests/test_release_channel.py`,
`conxa-cloud/tests/test_llm_proxy_and_publish.py`,
`conxa-cloud/tests/test_build_studio_backend.py`,
`python/handlers/workflows.py`, `python/backend.py`,
`renderer/src/pages/PublishPage.tsx`, `renderer/src/lib/releaseState.ts`,
`renderer/src/api/workflowsApi.ts`, `electron/test/releaseState.test.mjs`,
`runtime/sync.js`, `runtime/sync_errors.js` (new), `runtime/server.js`,
`runtime/test/test_sync.js`, `runtime/test/test_sync_errors.js` (new),
`conxa-cloud/backend/app/api/skillpack_update_routes.py`,
Cloud frontend `SkillPackagesPage.tsx`, `SkillPackageVersionsPage.tsx`,
`GroupPage.tsx` (new), `WorkflowReleasePage.tsx` (new), `api/workflowsApi.ts`,
`lib/releaseState.ts` (new), `lib/queryKeys.ts`, `components/release/*` (new),
`test/releaseState.test.mjs` (new)

---

### ✅ 3.4b Studio groups sync to Cloud Skill Packages — DONE 2026-08-20

Creating or renaming a group in Build Studio now upserts the same id and name
onto Conxa Cloud (`PUT .../groups/{group_id}` + `skillpack_known_groups` KV).
`GET .../groups` unions that registry with publish-time known-skills, so an
empty folder appears on Skill Packages immediately. Default is not synced
until renamed. Delete in Studio does not remove the Cloud folder.

---

### ✅ 3.4c Remove Publish Ownership (Slug Claiming) — DONE 2026-08-21

One workspace now has exactly one SkillPack, forever. The `publish_owners` KV
namespace (which tracked slug ownership for workspaces that claimed multiple
company slugs) has been removed entirely. All models, routes, and KV stores
now key by workspace_id directly. SkillPack, tracking_tokens, and sync_tokens
are now workspace-scoped, not slug-scoped. Dashboard routes derive workspace
from authenticated principal; runtime routes use {workspace_id} in the path.

**Files:** `conxa-core/conxa_core/models/workflow.py`, `conxa-cloud/backend/app/api/publish_routes.py`, `app/api/release_routes.py`, `app/api/skillpack_update_routes.py`, `app/api/tracking_routes.py`, removed `app/api/product_ownership.py`'s `_assert_owner`/`_claim_owner` logic.

---

### 3.5 Advanced RBAC

- Per-skill access controls (who can read vs. publish specific skills).
- Read-only analyst role (can view telemetry but not trigger builds or publish).
- API key support for CI/CD publishing (Build Studio not required for publishing).

**Files:** `app/services/rbac.py`, `publish_routes.py`

---

### 3.6 Compliance Package

- SOC 2 evidence export (audit log, access controls documentation).
- Data residency option (EU storage).
- Data deletion API (GDPR: delete all telemetry for a run_id or workspace).
- Privacy policy compliance for telemetry (opt-out flag in pack.json).

---

## Phase 4 — AI Agent Platform

**Goal:** Evolve from a packaging/distribution layer into the foundation for AI-native automation products.

**Timeline estimate:** 12–24 weeks (in parallel with Phase 3)

---

### 4.1 Conditional Steps & Branching Logic

**What's needed:** Skills currently execute linearly. Enterprise workflows have conditional paths (e.g. "if the user exists, update them; otherwise create them").

**Design:**
- Add `condition` field to `SkillStep` with a `condition_type` (e.g. `selector_present`, `url_matches`, `assertion_result`).
- Add `branch` field pointing to an alternative skill block.
- Runtime evaluates conditions and branches accordingly.

**Files:** `conxa_core/models/skill_spec.py`, `runtime/run.js`, `conxa-builder/python/conxa_compile/compiler/build.py`

---

### 4.2 Dynamic Input Resolution

**What's needed:** Currently, all inputs must be explicitly provided by the user or Claude before execution starts. Future: Claude derives inputs from conversation context automatically.

**Design:**
- Add `resolve_from_context: bool` flag per input.
- MCP execution tool passes `conversation_context` alongside explicit inputs.
- Runtime uses context for `{{variable}}` substitution when the input is not explicitly set.

**Files:** `runtime/server.js`, `runtime/run.js`, `conxa_core/models/skill_spec.py`

---

### 4.3 Multi-App Skill Sequences

**What's needed:** `execute_sequence` tool already exists and runs skills in a shared browser session. But sequences are ad-hoc (Claude orchestrates). Persistent, named sequences would allow companies to publish "orchestrated workflows" as products.

**Design:**
- Add `SequencePackage` to the skill package schema.
- Publisher defines a sequence: `[{skill: "login"}, {skill: "export_report"}, {skill: "email_report"}]`.
- Runtime `execute_sequence` tool accepts a sequence slug alongside individual skill slugs.

**Files:** `conxa_core/models/`, `runtime/server.js`, `plugin_builder.py`

---

### 4.4 Public Skill Registry / Marketplace

**What's needed:** A searchable directory of published skill packages that any user can browse and install.

**Design:**
- `GET /api/v1/registry/search?q=...&category=...` returns public packages.
- Companies opt-in to public listing at publish time.
- End users install via `install_plugin(slug)` MCP tool (already exists in server.js).
- Marketplace UI in cloud dashboard.

**Files:** Cloud backend new route, Cloud frontend new page, `runtime/server.js` (`install_plugin` tool)

---

### 4.5 API-First Publishing SDK

**What's needed:** Companies want to integrate Conxa publishing into their CI/CD pipeline. Currently requires Build Studio (Windows only).

**Design:**
- Python SDK that wraps the Build Studio compilation pipeline.
- CLI: `conxa compile --session-id ... --output ./dist`.
- CI integration: GitHub Action that compiles + publishes on merge.
- The Build Studio Python backend (`conxa_compile`) is already a self-contained package — the SDK is a thin wrapper.

**Files:** New package in `packages/conxa-sdk/`, GitHub Actions workflow template

---

## Dependency Map

```
Phase 1 (must complete before Phase 2):
  1.1 (auth fix) → 1.2 (token flow) → blocks 2.1 (device registration)
  1.4 (real delta) → blocks 2.2 (drift detection reads manifest)
  1.6 (RBAC wired) → blocks 3.2 (multi-user publishing)

Phase 2 (must complete before Phase 3):
  2.1 (device registration) → blocks 3.3 (on-premise, needs registration model)
  2.3 (audit log) → blocks 3.6 (compliance package)
  2.7 (billing hardened) → blocks Phase 3 (enterprise plans)

Phase 3 and 4 can proceed in parallel.
```

---

## Risk Summary

| Risk | Phase | Severity | Mitigation |
|---|---|---|---|
| Runtime installations without valid tokens break after 1.1 | 1 | High | **Resolved** — replaced by installer-embedded sync_token; no Conxa login required |
| RBAC enforcement breaks existing admin workflows | 1 | Medium | Roll out in audit-only mode first; log violations before enforcing |
| Delta sync format change breaks older runtimes | 1 | Medium | Support both manifest-diff and full-pack responses based on request params |
| macOS Playwright + keytar compatibility unknown | 2 | Medium | Test on macOS before committing to timeline |
| Stripe fields removed breaks env that had them set | 1 | Low | Only removing from config schema; env vars with SKILL_STRIPE_ prefix just get ignored |
| Slug claim race condition | 3 | Low | First publish claims; enforce idempotency within same workspace |
