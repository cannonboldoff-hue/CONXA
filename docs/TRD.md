# Technical Reference Document (TRD)

**Scope:** Conxa platform: Build Studio, Conxa Cloud, Conxa Execute, Runtime.
**Nature:** Current-state reference. History lives in git, `Done.md`, and `docs/archive/fix-log/`.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Build Studio](#2-build-studio)
3. [Conxa Cloud](#3-conxa-cloud)
4. [Runtime (MCP)](#4-runtime-mcp)
5. [Authentication & Distribution](#5-authentication--distribution)
6. [Recording Pipeline](#6-recording-pipeline)
7. [Compilation Pipeline](#7-compilation-pipeline)
8. [Skill Packaging](#8-skill-packaging)
9. [Execution Pipeline](#9-execution-pipeline)
10. [Recovery Architecture](#10-recovery-architecture)
11. [Control-Flow Steps](#11-control-flow-steps)
12. [Telemetry](#12-telemetry)
13. [LLM Router & Entitlements](#13-llm-router--entitlements)
14. [Storage](#14-storage)
15. [Security Model](#15-security-model)
16. [Deployment](#16-deployment)
17. [Known Gaps & Tech Debt](#17-known-gaps--tech-debt)

---

## 1. System Overview

```
┌─────────────────────────────────────────────────┐
│  Build Studio (Windows)                         │
│  Electron + Python stdio backend                │
│  Records, compiles, packages — 100% local       │
└───────────────────┬─────────────────────────────┘
                    │ HTTPS / Bearer JWT (LLM proxy, publish, auth)
┌───────────────────▼─────────────────────────────┐
│  Conxa Cloud                                    │
│  FastAPI (Render) + Next.js (Vercel)            │
│  LLM proxy, skill pack hosting, telemetry,      │
│  billing, dashboard                             │
└───────────────────┬─────────────────────────────┘
                    │ HTTPS (skill sync, telemetry, self-update)
┌───────────────────▼─────────────────────────────┐
│  Runtime (end-user machine)                     │
│  Node.js MCP server; executes skills via        │
│  Playwright for any MCP client (Claude Desktop, │
│  Conxa Execute, other agent hosts)              │
└─────────────────────────────────────────────────┘
```

**Key principle:** execution happens entirely on the end-user's machine. The cloud is coordination and telemetry, never in the execution hot path. It does not record, compile, or execute.

---

## 2. Build Studio

### 2.1 Process Architecture

```
┌──────────────────────────────────────────────┐
│  Electron main (electron/main.js)            │
│  window lifecycle, IPC bridge (preload.js),  │
│  spawns Python backend, deep-link auth       │
└────────────────┬─────────────────────────────┘
                 │ IPC (contextBridge)
┌────────────────▼─────────────────────────────┐
│  React renderer (Vite + TS)                  │
│  electron/renderer/src/                      │
│  Dashboard, Workflows, HumanEdit, Compile,   │
│  Publish, BuildInstaller, Settings           │
│  State: Zustand (editorStore.ts)             │
└────────────────┬─────────────────────────────┘
                 │ lib/ipc.ts → window.conxa.send()
┌────────────────▼─────────────────────────────┐
│  Python backend (python/backend.py)          │
│  newline-delimited JSON-RPC over stdio       │
│    request  → {id, type, payload}            │
│    result   ← {id, type:"result", result}    │
│    error    ← {id, type:"error", code, msg}  │
│    event    ← {type:"event", id, ...}        │
│  One thread per request; background asyncio  │
│  loop for Playwright                         │
└──────────────────────────────────────────────┘
```

Handlers live in `python/handlers/` (one mixin per domain, `protocol.py` holds `_CommandError` and event emission).

**Editor assets are streamed, not inlined.** `editor/assets.py::asset_url()` returns a `conxa-asset://local/<relative-path>` URL (validated against `settings.data_dir`, no disk read). `main.js` registers `conxa-asset` as a privileged scheme and streams the file from `<CONXA_STUDIO_HOME>/data` only when an `<img>` requests it, so editor round-trips don't grow with workflow size.

### 2.2 Backend Commands

| Command | Purpose |
|---|---|
| `ping` | Health check |
| `bootstrap` | First-run dependency download (§2.4) |
| `login` / `logout` / `whoami` | Clerk PKCE auth (§5.2) |
| `start_recording` / `stop_recording` / `get_recording_status` | Playwright recording session |
| `run_pipeline` / `compile` | Normalize events / full compile → SkillPackage |
| `create_workflow` / `list_workflows` / `get_workflow` / `update_workflow` / `delete_workflow` | Workflow CRUD (`update_workflow` also renames and re-groups) |
| `list_groups` / `get_group` / `create_group` / `rename_group` / `delete_group` | Workflow group CRUD (§5.3) |
| `add_group_app` / `update_group_app` / `remove_group_app` | A group's app list |
| `get_group_auth_status` / `check_group_app_auth` / `start_group_app_auth` / `finish_group_app_auth` / `cancel_group_app_auth` | Per-app auth capture and bounded headless freshness recheck (§5.3) |
| `patch_step` / `reorder_steps` / `insert_step` / `delete_step` | Workflow editor mutations |
| `validate_workflow` / `sign_off_workflow` | Quality gate |
| `get_skill_pack` / `build_skill_package` | Workspace SkillPack + build |
| `publish` | Push skill pack to cloud (§5.6) |
| `build_installer` | NSIS installer + optional cloud upload (§8) |
| `test_workflow` | Local sandbox run |
| `list_skills` / `get_skill_document` / `delete_skill` / `rename_skill` | Skill library |
| `list_skill_packages` / `list_skill_package_files` | Skill pack browser |
| `get_metrics` / `get_usage` | Backend metrics / LLM quota |
| `get_failure_evidence` | Copilot evidence bundle for a failing step (§7.4) |
| `copilot_turn` / `accept_copilot_proposal` / `reject_copilot_proposal` / `copilot_save_session` | Human Review Copilot (§7.4) |

### 2.3 Data Directory

```
~/.conxa/                       (or SKILL_DATA_DIR)
├── workflows/{workflow_id}/
│   ├── workflow.json           Workflow model
│   └── auth/auth.json          Playwright storageState — NEVER in build output
├── sessions/{session_id}/
│   ├── events.jsonl            raw RecordedEvent stream
│   └── screenshots/
├── skills/
│   ├── {skill_id}.json         SkillPackage
│   └── {skill_id}/
│       ├── assets/             screenshot thumbnails
│       └── edits.jsonl         reviewer edit log: one line per changed field, keyed on
│                               step_key; copilot lines carry source/proposal_id/decision
├── skill-packs/{workspace_dir_slug}/
│   ├── pack.json               sync endpoint, tracking, skill_groups (§5.3)
│   └── {group_id|_default}/{skill_slug}/
│       ├── execution.json
│       ├── recovery.json
│       └── inputs.json
├── runs/{workflow_id}.jsonl
├── cache/sessions/             staged auth for sandbox runs
├── deps/
│   ├── nsis/makensis.exe
│   └── runtime/{ver}/          conxa-runtime.exe, keytar.node, runtime-app/
└── kv/                         filesystem DB fallback
```

`workspace_dir_slug(workspace_id)` is a character-safety transform, not an identity.

### 2.4 Bootstrap

On first launch `services/bootstrap.py::ensure_all()`:

1. Fetches `GET /api/v1/updates/deps-manifest` (public).
2. Runs `playwright install chromium` and, concurrently (`ThreadPoolExecutor`), downloads, SHA-256 verifies, and extracts every outdated dep: NSIS → `deps/nsis/`; `conxa-runtime.exe` + `keytar.node` → `deps/runtime/{ver}/`; app-layer zip → `deps/runtime/{ver}/runtime-app/`. The installed-versions ledger write is lock-protected.

Idempotent: present deps are skipped.

---

## 3. Conxa Cloud

### 3.1 Architecture

```
Vercel (frontend)                    Render (backend)
Next.js 16, conxa-cloud/frontend/    FastAPI + uvicorn, conxa-cloud/backend/
  (marketing)/  public site            app/main.py, app/api/, app/services/
  (protected)/  dashboard              app/llm/router.py
  sign-in/      Clerk                  PostgreSQL (SKILL_DATABASE_URL)
  api/v1/[...]  proxy → API_ORIGIN
```

### 3.2 API Routes

Everything is under `/api/v1/` except health checks and the permanent telemetry alias (§3.3). Full request/response contracts: `docs/Backend-Schema.md`.

**Health & public**

| Route | Purpose | Auth |
|---|---|---|
| `GET /healthz` / `GET /readyz` | Liveness / readiness (DB ping) | Public |
| `GET /api/v1/subscriptions/plans` | Price sheet derived from `PLAN_LIMITS` (excludes add-on) | Public |
| `GET /api/v1/workflows/generations` | `{current, supported, deprecated}` installer generations | Public |
| `POST /api/v1/admin/workflows/generations` | Flip the generation stamped into new builds | `CONXA_ADMIN_TOKEN` |
| `GET /api/v1/installers/{slug}` | Installer download; HMAC-SHA256 `ts`+`sig`, 10-min window when `SKILL_INSTALLER_SIGNING_KEY` set (SG-07) | Public / signed |

**LLM & entitlements** (§13)

| Route | Purpose | Auth |
|---|---|---|
| `POST /api/v1/llm/proxy/{text,vision}` (+ `/text/stream`) | Metered LLM proxy | Clerk JWT + `X-Conxa-Client` |
| `GET /api/v1/llm/proxy/usage` | Quota status | Clerk JWT |
| `GET /api/v1/entitlements/current` | Plan, meters, capability ladder, trial | Clerk JWT |
| `GET /api/v1/entitlements/machines` / `POST .../machines/revoke` | Build-device list / revoke | owner/admin |
| `POST /api/v1/usage/compile/{reserve,commit,release}` | Compile-credit reservation; `reserve` also registers `X-Conxa-Machine` and checks trial | Clerk JWT |
| `GET \| PUT \| DELETE /api/v1/workspace/llm-key` | Enterprise BYOK config; GET never returns the key | owner/admin |
| `GET /api/v1/execute/contexts` | Workspaces the caller can use Execute under (§3.6) | Clerk JWT |

**Publishing & sync**

| Route | Purpose | Auth |
|---|---|---|
| `POST /api/v1/workflows/{installer_version}/skill-packs/upload` | Skill pack publish (mandatory; cloud error fails the publish) | Clerk JWT |
| `POST /api/v1/workflows/publish` | Unversioned publish (§3.3) | Clerk JWT |
| `GET /api/v1/workflows/{installer_version}/skill-packs/versions` | Release history for the Publish page | Clerk JWT |
| `POST /api/v1/workflows/{installer_version}/installer/upload` | Installer upload (optional; failure only surfaces as `cloud_upload_error`) | Clerk JWT |
| `POST /api/v1/workflows/{slug}/installer/upload` | Unversioned installer upload (§3.3) | Clerk JWT |
| `GET /api/v1/workflows/{installer_version}/{workspace_id}/skill-packs/delta` | Runtime per-skill delta sync (§5.7) | Rate-limited; `sync_token` |
| `GET /api/v1/skill-packs/{workspace_id}/delta` | Unversioned delta sync (§3.3) | same |
| `GET /api/v1/workflows` / `GET /api/v1/workflows/skill-packs[/{workspace_id}]` | Dashboard workflow + skill pack views | Clerk JWT |
| `GET /api/v1/jobs/{job_id}` | Job status | Clerk JWT |

**Updates** (§5.7)

| Route | Purpose | Auth |
|---|---|---|
| `GET /api/v1/manifest.json` | Unified Ed25519-signed runtime manifest (host, app, per-skill versions, compat matrix, rollout %). Served from `manifest` KV, signed at write time | Public |
| `POST /api/v1/admin/component-versions/{component}` | CI / publish writes a component version (`conxa_runtime`, `conxa_app`, `skill_packs:{co}:{skill}`) and re-signs the manifest | `CONXA_ADMIN_TOKEN` |
| `GET /api/v1/updates/artifact/{tag}/{filename}` | Serves `app-vX.Y.Z` zips from GitHub as a direct 200 (host-v3.2.1's downloader treats a 302 as an error) | Public |
| `GET /api/v1/updates/deps-manifest` | Build Studio bootstrap deps | Public |
| `GET /api/v1/updates/{studio,execute}-manifest` | Desktop app download info | Public |
| `GET /api/v1/updates/{studio,execute}/latest.yml` | electron-updater feed; proxies GitHub, rewrites relative `files[].url` | Public |
| `GET /api/v1/updates/conxa-{runtime,app}-manifest` | Deprecated shims (§3.3) | Public |

**Telemetry & dashboard** (§12)

| Route | Purpose | Auth |
|---|---|---|
| `POST /api/v1/workflows/{installer_version}/{workspace_id}/tracking/events` | Telemetry ingest (also `/api/v1/tracking/...` and bare `/api/tracking/...`) | Tracking token |
| `POST /api/v1/telemetry/runtime-start` / `GET .../runtimes` | Runtime phone-home / registration list | Public / Clerk JWT |
| `GET /api/v1/tracking/companies` | Workspace list (not tier-gated) | Clerk JWT |
| `GET /api/v1/tracking/dashboard?range=24h\|7d\|30d\|90d` | Operations payload: adoption, reliability, health, rollups, cascade, heatmap, ROI, insights | `ops_tier` basic+ |
| `GET /api/v1/tracking/activity` | Recent runs | basic+ |
| `GET /api/v1/tracking/{workspace_id}/runs[/{run_id}]` | Run summaries / timeline | basic+ |
| `GET /api/v1/tracking/workflows/{workspace_id}/{slug}?range=` | Step-level drill-down | basic+ |
| `GET \| PUT /api/v1/tracking/roi-assumptions` | ROI baseline (PUT owner/admin) | basic+ |
| `GET /api/v1/tracking/{workspace_id}/drift` | Drift review queue (§10.7) | `ops_tier` full |
| `GET /api/v1/audit-events` | Workspace audit log | basic+ |
| `GET /api/v1/dashboard` | Dashboard data | Clerk JWT |
| `POST /api/v1/bug-reports` | "Report a bug" via Resend; called directly by the browser (bypasses the Next.js proxy's body limit) | Clerk JWT |

**Billing** (§3.5)

| Route | Purpose | Auth |
|---|---|---|
| `POST /api/v1/subscriptions/create` | Create Cashfree subscription → `auth_link` | Clerk JWT |
| `POST /api/v1/subscriptions/addon/{order,verify}` | One-time add-on via Payment Link / post-redirect verify | owner/admin / Clerk JWT |
| `POST /api/v1/subscriptions/webhooks/cashfree` | Subscription webhook; HMAC over sorted `cf_` fields | Webhook secret |
| `POST /api/v1/subscriptions/webhooks/cashfree-orders` | Add-on PG webhook; HMAC over `timestamp + raw body` | Webhook secret |
| `GET /api/v1/subscriptions/addons` | Add-on catalog (`ADDON_TIERS`) | Clerk JWT |

**Also documented elsewhere in this file**

| Route family | Section |
|---|---|
| `POST /api/v1/usage/compile/refund` | §13.4 |
| `…/releases/{version}/{release,rollback}`, `…/skills/{archive,unarchive}`, `…/deployments`, `…/diff`, `PUT …/groups/{group_id}` | §5.5, §5.3.1 |
| `POST …/skill-packs/{slug}/artifacts` | §10.2 |
| `GET /api/v1/tracking/{workspace_id}/reconcile`, `PUT …/policy` | §12.4, §12.5 |
| `/api/v1/entitlements/execute-grants[/revoke\|/claim]`, `/entitlements/installer-domain` | §13.7, §13.6 |
| `POST /api/v1/telemetry/runtimes/revoke` | §13.8 |
| `GET /api/v1/legal/{current,acceptance}`, `POST /api/v1/legal/acceptance` | §5.2 |

### 3.3 Permanent Back-Compat Surfaces

Kept forever: each is depended on by a shipped artifact (a `pack.json` baked at install time, a Studio release in the field, or client error parsing) that can't be migrated. Code sites carrying one point here.

| Surface | Depended on by | Removable when |
|---|---|---|
| Bare `POST /api/tracking/{workspace_id}/events` | Installers whose `pack.json.tracking.tracking_url` predates the versioned route | Never, practically |
| Unversioned `POST /api/v1/workflows/publish` | Pre-generation installers | Never, practically |
| Unversioned `/api/v1/workflows/{slug}/installer/{upload,versions}` (`{slug}` unused; derived from principal) | Studio's Publish/Build Installer pages; older generations | Studio only calls the versioned pair and no old installer remains |
| Unversioned `GET /api/v1/skill-packs/{workspace_id}/delta` | Installed `pack.json` URLs | Never, practically |
| `updates/conxa-{runtime,app}-manifest` shims | Runtimes not yet on the unified manifest | All runtimes migrated (self-reinforcing) |
| `human_edit_tokens` dual-emitted beside `ai_usage_credits` | Studio builds reading the old key from `/entitlements/current` | No such build remains (TODO.md) |
| `saas.py` static-secret proxy-identity fallback (no replay protection) | Pre-HMAC Execute deployment | That generation retired |

Two hazards: the unversioned and versioned installer routes share one FastAPI path template on one router, so one can shadow the other (this happened once). And five routers share the bare `/workflows` prefix, so `include_router` order in `main.py` matters (see the comment above those calls).

### 3.4 Request Middleware & Principal

`app/api/security.py::ProductionRequestMiddleware`:

1. Attaches a request ID.
2. Enforces body limits: 1 MB general; 250 MB for `BUILD_ARTIFACT_UPLOAD_PATHS` (`/workflows/publish`, any `/installer/upload`, any `/skill-packs/upload`). A path missing from that set silently gets 1 MB. Execute chat calls reuse `llm_vision_proxy_max_bytes` (`_body_limit_for_path`).
3. With `SKILL_AUTH_REQUIRED=true`, verifies `Authorization: Bearer` against Clerk JWKS and sets `request.state.auth`.
4. Public bypass: health, installer downloads, update manifests, telemetry ingest, delta GETs. Versioned runtime routes under `/api/v1/workflows/{installer_version}/{workspace_id}/…` are exempted **by suffix** (`PUBLIC_VERSIONED_WORKFLOW_SUFFIXES_GET = ("/skill-packs/delta",)`, `..._POST = ("/tracking/events",)`), because a `/api/v1/workflows/` prefix exemption would unauthenticate Clerk-protected dashboard routes. Both are token-guarded in their handlers.

`app/services/saas.py::Principal` (frozen dataclass): `user_id`, `workspace_id` (Clerk `org_id` or `personal_<user_id>`), `workspace_slug`, `workspace_name`, `role` (`owner|admin|member`), `email`, `name`, `auth_provider` (`clerk|local`), `identity_source`. Local dev (`SKILL_AUTH_REQUIRED=false`) uses a synthetic principal.

`_normalize_org_role` strips Clerk's `org:` prefix and lowercases. A user with no active org is in their own personal workspace and normalizes to **`owner`** (defaulting to `basic_member` locked solo users out of every `require_admin` route). Both identity paths pass `personal_workspace=not org_id`; covered by `tests/test_llm_proxy_and_publish.py`.

### 3.5 Billing

Cashfree (`app/api/cashfree_routes.py`, mounted at `/api/v1/subscriptions`).

- **Subscriptions:** `POST /create` calls Cashfree's non-seamless subscription API and returns `auth_link`. Workspace↔subscription↔tier mapping lives in `cashfree_sub_workspace` KV, because webhooks carry only the subscription reference. `POST /verify` resolves the tier from `planId`. Activation/charge webhooks persist `current_period_end`, so paid usage windows reset on the payment date.
- **Compile add-on packs** are one-time purchases: `POST /addon/order` creates a Payment Link (`POST /pg/links`). Payment is confirmed by the PG webhook or by `POST /addon/verify` after redirect; either credits a never-expiring wallet exactly once (`cashfree_orders_granted` KV guard). The wallet is drawn only after the monthly allowance runs out.

Contracts: `docs/Backend-Schema.md` §5.4.

### 3.6 Conxa Execute

Execute is a desktop app (`conxa-execute/app/`) with **no backend of its own**. It is a thin client of `conxa-cloud/backend`, like Build Studio and the dashboard.

- **Identity:** the same Clerk instance as Build Studio, with its own OAuth client (separate `client_id`, redirect ports `127.0.0.1:52841-52850/cb`). Scope includes `user:org:read` so tokens carry `org_id`. `electron/auth_service.js`: PKCE S256, 60 s refresh leeway, tokens in Electron `safeStorage` (`userData/clerk-session.bin`).
- **Billing:** no BYOK, wallet, or Execute-specific subscription. Workspace members get Execute on their plan; non-members get it via an Execute Seat grant (§13.7). Both draw from the workspace's AI Usage Credits pool, metered as `execute_chat_tokens`.
- **Context switcher:** `GET /api/v1/execute/contexts` returns the personal workspace, every Clerk org the user belongs to (`saas.py::clerk_user_organizations`), and every workspace where they hold a `claimed` grant. The same call auto-claims `pending` grants matching the verified email. The chosen `workspace_id` is sent as `target_workspace_id` and **re-verified on every call** (`llm_proxy_routes.py::_resolve_execute_chat_workspace` → `entitlements.py::execute_chat_access_for`).
- **Model:** no dedicated pool. `execute_chat` routes through `compile_pool_for(principal)` (the tier pool compile and Human Edit use), always on the vision path, so it lands on the tier's `*_MULTIMODAL_MODEL` (falling back to `*_VISION_MODEL`).
- **Chat proxy:** `POST /api/v1/llm/proxy/text/stream` with `X-Conxa-Client: conxa-execute`, `usage_class: "execute_chat"`. Scoped to that task only: `_openai_messages_for_task` passes `payload["messages"]` through and `_openai_body_dict` forwards `tools`/`tool_choice`; streaming uses `_iter_sse_deltas` (tagged `text`/`tool_call` chunks). Execute calls skip `register_request_machine` (seats, not machines, are the cap).
- **Client transport:** `vendor/opencode/loop/run_turn.js` accepts an `opts.chatCompletion` transport. `electron/execute_client.js::makeChatCompletion` maps OpenAI-shaped requests to the proxy body, parses the tagged SSE stream, accumulates tool-call fragments by index, and returns `{choices:[{message}]}`.
- **Sessions:** local only (`vendor/opencode/storage/storage.js`, see `app/NOTICE`). No cross-device sync.
- **In-app browser:** see §4.7.


---

## 4. Runtime (MCP)

### 4.1 Process Model

Two layers: a large, rarely-updated host binary and a small, frequently-updated app layer.

```
MCP client (Claude Desktop, Conxa Execute, …)
        │  MCP stdio
        ▼
conxa-runtime.exe   host layer: Node + npm deps + bootstrap.js (~85 MB, rare releases)
        │  loads from disk
        ▼
~/.conxa/conxa-app/current/server.js   app layer: obfuscated JS (~60 KB zip, every release)
        ├── run.js, resolver.js, resolve_adapter.js, cascade.js, recovery.js
        ├── skill_loader.js, sync.js, auth_manager.js, browser.js, tabs.js
        ├── page_scripts.js, tracker.js, run_registry.js, host_lock.js, …
        └── @modelcontextprotocol/sdk etc. via global.__hostRequire (bundled in host)
```

**Bootstrap** (`runtime/host/bootstrap.js`):

1. `env.js` normalizes install, data, app, API, and update-channel paths for every later module.
2. `register-mcp` / `unregister-mcp` / `sync` / `schedule` / `runner` subcommands dispatch here (§4.3, §4.8).
3. Normal launch: fetch and verify the signed manifest, optionally activate a newer app layer (§5.7), then `version_manager.resolveCurrent()` → check `version.json.min_host` (pure gate: `host/min_host_gate.js`) → `require` `server.js`. On failure, `version_manager.rollback()` flips `current` to the retained previous version and retries (no re-download).

The install-time `sync` subcommand (`host/cli_sync.js`) enforces the same `min_host` gate before loading the app layer's `sync.js`; an incompatible app layer is skipped and the first launch self-updates the host.

**Source vs deployed layout.** In the repo, host modules live in `runtime/host/` and app modules in `runtime/app/`. `runtime/host-manifest.json` is the authoritative frozen list (verified in CI by `check_host_manifest.js`: every file reachable from `bootstrap.js` must be listed). Edits to listed files ship only with a `host-v*` release. Deployed layouts are flat: the zip stages every app module side by side in `conxa-app/<version>/`.

**Obfuscation, not bytecode.** App-layer files are obfuscated JS (self-defending, rc4 string array). `@yao-pkg/pkg`'s embedded Node differs from official Node builds; `bytenode`'s `fixBytecode` masks the header mismatch, so V8 segfaults silently (0xC0000005). The host exe is built `--no-bytecode` for the same reason (Playwright's selector engine).

**`page_scripts.js` is the only place for browser-context functions.** It's obfuscated with mangled identifiers only (no self-defending/string-array), because Playwright serializes functions passed to `page.evaluate()`/`locator.evaluate()` via `toString()` and re-parses them in the page, where the obfuscator's module-scope decoder doesn't exist. `run.js`, `server.js`, `resolve_adapter.js`, and `drift.js` call into it. Never pass an inline arrow to `evaluate()` elsewhere in the app layer: it throws `ReferenceError` in production.

**The stderr copy of the log never blocks (2026-11-15).** `server.js::log()` writes every line to the file log (the record) and a copy to stderr for whoever launched the runtime (Build Studio reads step progress from it). A launcher that does not read stderr fills the pipe, and on Windows a write to a full pipe blocks the whole thread, so timers, the run deadline and the inspector stop and a run hangs for good (measured: five runtime processes froze at 114-115 KB of log, at the 31st run). Nothing on the main thread writes to stderr: `stderr_sink.js` owns the write on a worker thread behind a 256 KB byte budget and drops lines past it. At exit the worker gets 200 ms to drain; a still-stuck pipe ends the process rather than hanging `process.exit()` on a thread that cannot stop. Studio names a failed step from the result's step key and index, never from these progress lines, so lost lines cost progress rows only. Tests: `test_stderr_sink.js`.

### 4.2 MCP Tools

Defined in `server.js::_toolDefinitions()`:

| Tool | Description |
|---|---|
| `list_skills` | Installed skills, optionally filtered by company |
| `get_skill_inputs` | Input schema for a skill |
| `execute_skill` | Run one skill. Options: `dry_run`, `watch`, `step_overrides`, `review_results`, `resume_from` (below) |
| `execute_sequence` | Run an ordered list of skills in one browser session |
| `get_execution_status` | No args: `{active_runs, awaiting_auth, recent}` (running runs, runs detached waiting for sign-in with each app's `waiting`/`signed_in`/`closed`/`failed`, last 10 outcomes). With `run_id`: that run's `state` (`running`/`awaiting_auth`/`completed`/`failed`/`cancelled`/`paused`/`unknown`) and, once finished, its `summary`. Every execute call records its outcome (`server.js::_handleTool` -> `run_registry.recordCallResult`), whatever path it ended on. `list_skills` carries a plain `notice` (and every run answer one extra sentence) while the cloud refuses that workspace's sync or tracking key with 401/403 (`access_refused.js`). Also surfaces `waiting_for_host` (§4.6) |
| `cancel_execution` | Cancel a run; `run_id` required when more than one is active |
| `authenticate` | Open sign-in for every app in a skill's group and block (bounded) until done. Args: `skill` or `workspace_id`, `wait_seconds` (default 45, max 600). Returns `{status: signed_in\|waiting\|failed, apps?, message?, next}`. Idempotent: calling again re-waits on the open window. Parks the warm session for the next `execute_skill` (§4.6) |
| `get_runtime_status` | Diagnostics. `staged_runtime_version` is non-null when a host update is activated on disk but this process is the old exe |
| `create_schedule` / `list_schedules` / `delete_schedule` | Local cron schedules (§4.8). `list_schedules` never returns input values |

**Execute-skill options:**

- `dry_run: true` — every step runs except `destructive === true` ones, which are resolved (proving the target exists) but not dispatched (§9.6).
- `review_results: {"<step_index>": {"choice": id}}` — resume payload for a popup-decision pause (§11.4).
- `span_actions: {"<step_index>": {kind, ...}}` — the agent's next action for an `agent_span` (§11.7).
- `resume_from` alone resumes a hand-over pause; a hand-over can also resume outside MCP (§11.4).
- `step_overrides` — agent-supplied step fixes, the closing edge of Tier B recovery (§10.1).

**Datasets / bulk runs:** a compiled skill is always a single-record workflow. When a user asks to act on every row of a spreadsheet, CSV or document, the **MCP client/agent** owns the dataset: it reads the file, maps columns to the skill's declared inputs (`get_skill_inputs`) by meaning, confirms the mapping and record count with the user, then calls `execute_skill` once per record, one at a time, and reports a per-record summary. The runtime only ever receives one record's resolved input values. It never receives the file, its path, or its contents. The one exception is a skill's own declared `file_path` input for an upload step, which is the file to upload. The contract lives in `tool_defs.js::SERVER_INSTRUCTIONS`, sent as the MCP server `instructions` on `initialize`, and is repeated in the `execute_skill`/`get_skill_inputs`/per-skill tool descriptions and the `durable_context.js` discoverability note. Nothing on the execution path enforces it, and no column names or filenames are hardcoded. Conxa Execute's chat can't open files, so its prompt asks the user to paste the rows instead.

**Update gate** (`app/update_gate.js`): while `staged_runtime_version` is set, new `execute_skill`/`execute_sequence` calls are refused with "Quit and reopen <host> to finish". Scheduled runs are exempt; the gate releases itself after 3 launches that never picked up the update; `CONXA_SKIP_UPDATE_GATE=1` disables it.

**No `refresh_skills` tool.** Sync runs at startup (§4.4) and again when `execute_skill`'s integrity gate finds a real on-disk checksum mismatch. Before failing that gate, `ensureSkillIntegrity` re-reads the skill's `manifest.json` once, so a long-lived process (Studio's reused sandbox runtime) doesn't fail after a restage on stale in-memory checksums.

### 4.3 MCP Registration

`conxa-runtime.exe register-mcp` / `unregister-mcp` are host-layer subcommands dispatched right after `env.apply()`, before the app-layer load and the `min_host` check, so they work with no app layer staged. NSIS only invokes them (`ExecWait '... register-mcp'`); no config editing lives in the installer.

| File | Role |
|---|---|
| `mcp_hosts.js` | JSON-config hosts (Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, Gemini CLI, Cline, Zed, Copilot CLI, Factory, KiloCode, Antigravity, OpenCode, OpenClaw, Crush, OpenHands, Augment, Kiro, Junie, Qwen). Per row: `detect(ctx)`, `configPaths(ctx)` (plural: VS Code per profile, Cline two locations), `objectPath`, `shape` |
| `mcp_hosts_toml.js` | Codex CLI, Mistral Vibe: `# >>> conxa:<label> >>>` marker blocks, no TOML parser |
| `mcp_hosts_yaml.js` | Goose, Hermes: `yaml` Document API, comment-preserving |
| `config_edit.js` | JSON/JSONC writer (`jsonc-parser`). Atomic (temp, `fsync`, `rename`); ownership check (`isOwned()`: an entry is ours only if its `command` resolves inside our install root); read-before-write CAS |
| `config_edit_toml.js` | Marker-block editor. A regular table (`[mcp_servers.conxa]`) can exist once, so another outside our span is foreign; an array-of-tables (`[[mcp_servers]]`) conflicts only if another entry's `name` claims our key |
| `config_edit_yaml.js` | Document-API editor; no marker needed |
| `mcp_register.js` | Orchestrator. `computeIdentity()` derives the key (`conxa` vs `conxa-dev` from `env.js`'s dev flag) and the stable `conxa-runtime/current/conxa-runtime.exe` path; iterates all tables; one host failing never aborts the run; non-zero exit only if every detected host failed |
| `durable_context.js` | Writes a per-company skills/instructions file (`SKILL.md`, `AGENTS.md`, `global_rules.md`, …) into each registered host. Called from `sync.js` after `pack.skills` is written (a thin installer ships it empty); best-effort |

Because install and uninstall derive the key from the same `env.js` resolution, dev and stable installs on one machine use distinct keys that can't drift.

Flags: `--plan` prints the machine-readable receipt without touching files or network; `--dry-run` is the same without the JSON; `--only <id>[,<id>]` limits hosts.

Every run writes `<CONXA_DIR>\mcp-register-status.txt`: a summary line (`conxa register-mcp: 15 ok, 3 not installed, 1 left alone (not ours), 1 FAILED`) plus per-host detail. NSIS shows the first line in its completion dialog, the only way a partial failure becomes visible.

Tests: `runtime/test/unit/test_config_edit*.js`, `test_mcp_hosts.js`, `test_mcp_register*.js`, `test_durable_context.js`, all fixture-driven (temp dirs plus `USERPROFILE`/`HOME`/`APPDATA`/`LOCALAPPDATA` overrides). `npm test` runs `node --test "test/unit/*.js"`; browser/exe tests live in `runtime/test/e2e/` and run in CI gates.

### 4.4 Startup Sequence

```mermaid
sequenceDiagram
    participant CD as MCP client
    participant RT as bootstrap.js (host)
    participant App as conxa-app/server.js
    participant Cloud as Conxa Cloud

    CD->>RT: spawn conxa-runtime/current/conxa-runtime.exe (stdio)
    RT->>RT: env.apply()
    RT->>Cloud: GET /api/v1/manifest.json (every launch; Ed25519-verified)
    Cloud-->>RT: {conxa_runtime, conxa_app, skill_packs, minimum_versions, signature}
    RT->>RT: checkForUpdates(["conxa_app"]) → download, verify SHA-256, extract, activate()
    Note over RT: server.js not yet required, so the new app layer is live this launch
    RT->>RT: resolveCurrent(conxa-app) → min_host check
    RT->>App: require current/server.js (or rollback)
    App->>App: load skill index from cache
    App->>CD: MCP connect
    par startup sync
        App->>Cloud: checkForUpdates(["conxa_runtime"]) on the cached manifest → download, --selfcheck, activate() (next cold start)
    and
        App->>Cloud: GET …/skill-packs/delta?since={per-skill versions} (skipped if synced <5 min ago)
        App->>App: per changed skill: parallel downloads → <skill>/<version>/ → activate()
    and
        App->>App: re-encrypt plaintext session files (best effort)
    end
    App->>App: reload skill index
    App->>CD: sendToolListChanged()
    App->>Cloud: POST /api/v1/telemetry/runtime-start (fire-and-forget)
```

- **Execution gate:** `execute_skill` awaits startup sync (skill packs + host manifest check). Typically under 1 s. Failures fall through to cached data; a bad manifest signature is treated like a network failure (last verified cached manifest, or skip on first run).
- **Pre-load app update budget:** 3 s manifest timeout, 2 retries × 5 s for the zip. Any failure is swallowed and bootstrap loads whatever `current` points at.

### 4.5 On-Disk Layout

Every updateable component (host exe, app layer, each skill) is a **versioned directory** with a `current` directory junction (`version_manager.js`). Default retention: current + 2 previous, so rollback needs no download. Junctions, not pointer files, because MCP host configs store a literal path to the exe, and junctions need neither admin rights nor Developer Mode.

```
~/.conxa/                              CONXA_DIR
├── conxa-runtime/{vX.Y.Z}/, current   conxa-runtime.exe, keytar.node, version.json
├── conxa-app/{vX.Y.Z}/, current       app modules + version.json
├── manifest.json                      last verified signed manifest
├── chromium/                          Playwright browser (unversioned)
├── skill-packs/{company}/
│   ├── pack.json                      sync_endpoint, sync_token, groups[], skill_groups (§5.3)
│   └── {group_id|_default}/{skill_slug}/
│       ├── {vX.Y.Z}/                  execution.json, recovery.json, inputs.json,
│       │                              manifest.json (carries group_id), validation.json, version.json
│       └── current
├── resume/{run_id}.cmd                hand-over resume drops (§11.4)
└── logs/runtime.log, recovery.log     JSONL, rotated at 10 MB (recovery.log keeps 3 older files). recovery.log is the local-only run record: every line carries run_id/step_key; it holds resolver scores and the full proof verdict. The cloud tracker never reads it.

%APPDATA%/Conxa/                       CONXA_DATA_DIR
├── cache/sessions/{co}__{appId}_state.json       AES-256-GCM storageState (§5.3)
├── cache/sessions/{co}__{appId}_raw_state.json   plaintext fallback
├── cache/manifests.json               skill index cache
├── data/executions/{id}/state.json, checkpoint.json
├── data/runs/{workflow_id}.jsonl
├── locks/<host>.lock                  cross-process platform mutex (§4.6)
└── scheduler/                         §4.8
```

### 4.6 Concurrency

Runs execute **truly in parallel, capped**. A serialized queue doesn't work: Claude Desktop abandons a `tools/call` at ~240 s and one run's budget (`CONXA_EXECUTION_DEADLINE_MS`) is 210 s, so a queued run would time out before starting.

- **Admission** (`run_registry.js`): runs tracked by `runId`, admitted up to `CONXA_MAX_CONCURRENT_RUNS` (default 5, flat; a memory-derived default is deferred as `RT-3-CAP-SIZING`). Past the cap, calls are refused with the in-flight runs named and a pointer to `get_execution_status`, never to `cancel_execution` (the caller didn't start those runs).
- **Browser sessions** (`browser_session.js`): every live Chromium, headless or visible, is a session in one registry, so the cap bounds browser instances. A session is a lease: busy for the whole run, 90 s idle timer starting on release. A concurrent call that finds a leased session gets its own; two runs never share a Playwright context (`tabs.js`'s popup registry and the per-run download listener assume one). Key: `workspace::group::sortedRequiredApps::h|w` (`::host:<runId>` for Execute-owned sessions). A parked session with the same key is reused and skips pre-flight (this is how `authenticate` and the following `execute_skill` share one Chromium). Visible runs aren't parked. When every slot is leased, an idle session of another key is evicted; if none, the call is refused ("All N browser sessions are currently in use…"). Liveness is `browser.isConnected()` (a closed context doesn't throw from `pages()`).
- **Recovery parks** (`recovery_park.js`) are keyed `${workspace_id}:${slug}`, so one run's park never discards a sibling's. A park holds its lease for `CONXA_RECOVERY_PARK_TTL_MS` (default 180 s).
- **Per-host platform serialization** (`host_lock.js`): runtime isolation doesn't protect the external platform from two runs mutating the same project (lost updates, cancelled deploys, bot detection). A mutex keyed by hostname, from `manifest.target_url` plus every required app host of a group (a group with no sign-in apps still locks its starting-page host; a skill with no resolvable host takes no lock, fail open), is acquired for all hosts atomically, in sorted order, before any browser work (including auth). A blocked run polls every 250 ms; the wait counts against its deadline, fails with a message naming the host and the blocking run, and shows as `waiting_for_host`. Runs on different platforms stay fully parallel. The lock is held through a park.
  - **Cross-process:** with `locksDir` (`<CONXA_DATA_DIR>/locks`), an in-process win must also win a heartbeat-stamped file lock (`file_lock.js`). A lock frees when its file disappears, its PID dies, or its heartbeat is >15 s stale. Filesystem errors degrade to in-process locking (fail open).
- **Everything else is per-run**: the step loop, tab registry, tracker, `runs/{runId}/` download dir, deadline/cancel flags all live in one `execute_skill` closure.
- **Retry budget is shared on purpose**: `retry_budget.js` keys by `${slug}:${stepIndex}` so the budget persists across MCP calls; concurrent runs of the same skill share it (bounded worst case, see its `ponytail:` comment).
- **Limitation:** concurrent `watch: true` runs each get a visible window and compete for foreground focus (`bringToFront()`), except under Execute (§4.7).

### 4.7 Conxa Execute In-App Browser

When Execute is the MCP client, a headed run renders inside Execute's window (chat left, browser panel right) instead of an OS window.

**Seam:** `browser.js::_buildExecContext`. If `CONXA_HOST_BROWSER_CDP` is set (Execute's CDP endpoint) and the run is headed with a `runId`, the runtime calls `chromium.connectOverCDP()` and requests a `WebContentsView` over Execute's loopback control channel (`conxa-execute/app/electron/browser_control.js`). Every other client never sets the variable and takes `chromium.launch()`. Any connection failure falls back to a normal launch.

- **`runtime/app/host_browser.js`:** `acquire({runId, storageState})` connects, requests a view (`new_view`), then seeds state manually: cookies via `setPageCookies` (`Network.setCookies` on the run page's own CDP session — see the Electron limits below), `localStorage` by navigating each origin and setting it through `page_eval.js::evalOn` (no CDP call exists for this). `newTab({context, runId})` backs `tab_open` steps. `release({runId, tabId?})` disconnects (never closes Execute's browser) and posts `close_view` (one tab) or `run_end` (whole run); a missed message leaks one view until Execute's idle cleanup.
- **Electron limits:** no `Target.createBrowserContext` (use `browser.contexts()[0]`) and no `Target.createTarget`, so Execute creates every new page itself (`browser_panel.js`). The same limit breaks Playwright's `context.storageState()` once any visited origin's page has closed: it opens a hidden page to read that origin's `localStorage` and fails with `Target.createTarget: Not supported`. A signed-in host-owned login is therefore captured by `browser.js::_captureState`; launched sessions keep `storageState()` (AUTH-20). Cookies also need care. Each run's views live in their own in-memory partition (`conxa-run-<runId>`), but Playwright's single context is Electron's *default* session: `context.cookies()` reads it (always empty for a run) and `context.addCookies()` writes into it (invisible to a run's views). This was confirmed against real Electron. So cookies go through a CDP session on one of the run's own pages, whose `Network` domain uses that page's partition: `host_browser.js::pageCookies` (`Network.getAllCookies`) for capture and the learned-definition check, and `setPageCookies` (`Network.setCookies`) for seeding in `acquire()`. `_captureState` reads cookies and `localStorage` only from the sign-in's own pages, never `context.pages()`, which also holds other runs' views and Execute's own UI (AUTH-21). Site popups go through `setWindowOpenHandler` and appear to Playwright as normal `context.on("page")`. `browser_panel.js::_createTab` is the single place that makes a tab active and notifies the renderer.
- **Downloads:** Electron's download manager, not Playwright, handles a file downloaded in a run's partition. With no handler it opens a native Save As dialog, and `download.saveAs()` fails because Playwright's artifacts folder stays empty. `browser_panel.js` therefore sets a save path in every run partition's `will-download` (no dialog; files under `%TEMP%/conxa-execute-downloads/<runId>/`, removed at `run_end`). The runtime's download listener (`server.js`) claims the finished file by URL through the `save_download` control op (`host_browser.js::saveDownload`) instead of calling `download.saveAs()` (EXEC-46).
- **Uploads:** `host_browser.js::acquire` connects with `connectOverCDP(..., { isLocal: true })` when the endpoint is loopback, so `setInputFiles` hands the page file paths. Without it Playwright reads each file into memory and sends it through CDP: a 40 MB file outran the 2.5 s action budget and 50 MB or more was refused (WF-14).
- **Teardown:** `browser.js::teardownExecBrowser` is the one shared teardown. On a host-owned run, `browser.close()` only disconnects; Execute's `run_end` handler destroys views and clears the partition.
- **Isolation:** each run's views use a non-persistent partition `conxa-run-<runId>`.
- **Panel:** Chrome-style header (`BrowserPanel.tsx`): tab chips, back/forward/reload, editable URL, run selector when several runs are live. Login tabs are labelled with the app name (`label` on `new_view`/`new_tab`, both return `tabId`). Login teardown is tab-scoped, run end is run-scoped (`browser_panel.js::closeTab` promotes a neighbour and calls `runEnd` on the last tab).
- **Login and hand-over:** `_openInteractiveAuthWindow` uses the same host branch; `beginInteractiveAuth`'s non-blocking flow is unchanged. `handover.js`'s in-page resume banner (`exposeBinding` + `addInitScript`) works unmodified against a host-owned context. Because Execute's CDP connection never fires `disconnected` for one view, `_waitForInteractiveAuth` also polls for a closed login page every 1.5 s and has a 10-minute deadline (`LOGIN_WAIT_MS`) that closes the view and marks the attempt `loginTimedOut`.
- **Chat scope:** a panel belongs to the chat that started its run. `main.js`'s chat `executeTool` picks the run id itself (the runtime honors `_run_id`) and calls `browser_panel.js::tagRun(runId, sessionId)`; `panel:tabs` carries `chatId`. On a chat switch or new chat the renderer calls `panel:set-chat` → `setVisibleChat`, which parks (0x0) every view owned by another chat without destroying it, so the run keeps going; `BrowserPanel.tsx` shows only the current chat's runs and re-places the view on return. A new chat's Chromium is still created lazily by its first `new_view`. Runs started outside a chat (the form) are untagged and visible everywhere. Views die with the run (`run_end`), so a finished run's panel does not come back.
- **Hide / capture (2026-10-01):** the title-bar **Browser** toggle unmounts `BrowserPanel.tsx` and calls `panel:hide-all` → `browser_panel.js::hideAll`, which parks every view at 0x0 (a native view floats above the page, so unmounting alone would leave it on screen). Showing it again re-reports bounds. A new run or a login `focus` event un-hides it. `panel:capture` → `captureVisible()` grabs the last tab placed by `setActiveBounds` (`webContents.capturePage()`, JPEG) as a chat attachment. `screen:capture` in `main.js` hides the window, takes the primary display through `desktopCapturer`, and shows the window again.
- **Two run ids:** `server.js` carries `_runId` (per call; names hand-over resume files) and `_hostRunId` (the id Execute registered the view under). They diverge only across park/resume, where `_hostRunId` is restored from `_park.runId`. `run.js::runPlan` takes both as separate opts.

### 4.7a Build Studio Browser Panel — tests (BUILD-42, 2026-10-18), recording and sign-in (BUILD-46, 2026-10-20)
> **2026-11-21:** `prepareTestRun` restores a minimized Studio window before a test view is made (a test started at no size could not be trusted); `setMinimized` still holds an already-running test at its size. A run the runtime stops for page size returns `_meta["conxa/couldnt_run"]`, which Studio treats as no test result (Backend-Schema, test outcome classes).

Build Studio reuses the §4.7 seam. A headed test run, a recording and a sign-in all draw their browser inside the Studio window instead of opening a separate Chromium window. Each panel run has a `kind` (`test` | `record` | `signin`); the renderer styles the pane by it.

- **Studio side:** `conxa-builder/electron/browser_panel.js` + `browser_control.js` (the same shape as Execute's, with the differences below). `main.js` picks a free loopback port, sets the `remote-debugging-port` / `remote-debugging-address=127.0.0.1` switches before `app.whenReady()`, starts the control server, and exports `CONXA_HOST_BROWSER_CDP` + `CONXA_HOST_CONTROL_URL` into its own environment **before** spawning the Python backend. The backend's runtime child inherits them (`runtime_tool.py` builds `proc_env` from `os.environ`), so `runtime/app/host_browser.js` takes the borrowed-view path with no runtime change. A run that is headless, or a Studio without the panel, never sets them and launches Chromium as before.
- **A finished run keeps its page.** The design shows the final page so the person can check the result themselves. `run_end` therefore only marks the run finished; the view is destroyed when the person closes the panel or a newer run starts (`closeRun`). Execute destroys on `run_end`.
- **Recording and sign-in in the panel (BUILD-46).** The 2026-10-18 decision to keep them in their own window ("can't be added from outside") was reversed after a spike proved each missing piece could be rebuilt (`docs/archive/spikes/in-app-recording-2026-10.md`, Gate A + Gate B). `SessionRegistry.create` decides once per session: `embedded = in_panel and settings.record_in_panel (SKILL_RECORD_IN_PANEL, default on) and both host env vars set`. Otherwise the launched path runs unchanged. How each recorder need is met:
  - **Page size.** A `record` run is a page 1280 CSS px wide at DPR 1, with its viewport sent as `{width: 1280, height: 720, fit: "width"}`. `browser_panel.js` applies `Emulation.setDeviceMetricsOverride` through `webContents.debugger`, issued after the first `loadURL` (attaching earlier crashes Electron). With `fit: "width"` the view fills the pane's rect with no bars: `scale = rect.width / 1280` (above 1 is allowed) and the page height is `rect.height / scale`, kept between `MIN_H` 480 and `MAX_H` 1440 (a short pane centres the page, a tall one top-anchors it). Resizing the pane re-sends the override for every tab. A non-`record` run (a test) shrinks with the view's zoom factor instead (`webContents.setZoomFactor(scale)`, no metrics override): Playwright's CDP input is not mapped through the override's `scale` (clicks land at 1/scale), and a device scale factor leaves the layout full size and clipped, whereas under zoom the layout is `pane / zoom` = 1280 CSS px wide and CDP input lands exactly. Zoom is re-applied whenever a page becomes ready (Chromium keeps it per site) and reset to 1 when a run closes or is adopted by the recorder; popups adopt the run's size and scale. A viewport without `fit` (an older runtime, or Conxa Execute's panel) still letterboxes at `min(w/1280, h/720, 1)`; a `signin` run has no fixed size — the page fills the pane. The `scale` op returns `{scale, width, height}`; the screencast derives the page height from the frame's aspect (the view is the page box), makes the video `1280 × first-frame height` and reshapes later frames top-anchored if the pane was resized. `videos.json` gains `width`/`height`; compile maps bboxes against them.
  - **Who may change the page size.** The size is one shared setting per page inside Chromium, and every client that writes it overwrites the others. Playwright's `page.screenshot()` sends `Page.captureScreenshot` *with a clip*, after which Chromium restores the page to the native display scale (DPR 1.25 on a 125% screen): the page is drawn too big and clipped, and re-sending the same override is a no-op, so only clear-then-set repairs it. The rules that follow from that: (1) only the two host panels (`conxa-builder/electron/browser_panel.js`, `conxa-execute/app/electron/browser_panel.js`) set or clear the size; (2) every runtime screenshot goes through `runtime/app/page_geometry.js::capture` (raw CDP, no clip); (3) a panel reads the real `innerWidth × innerHeight @ DPR` back when a page becomes ready and, if it drifted, clears then sets (`_ensureGeometry`), and the control op `ensure_geometry` does the same for every tab of a run on request; (4) before each step a host-owned run calls `checkGeometry`, which measures the live page (width 1280 give or take a pixel, height at least `MIN_H`; any height and any DPR is right, since a zoomed page reads screen DPR × zoom) and probes that a mouse move lands where it was aimed and asks the host to repair it, and a page still at the wrong width ends the run as an app fault (`faultKind: "page_size"`, before any recovery). A host too old to know `ensure_geometry` only produces a warning. `conxa-cloud/tests/test_page_geometry_single_owner.py` fails the build if anything else writes the size or calls `.screenshot(` in `runtime/app`. A no-clip capture on a scaled display is `1280 × scale` pixels wide, the same picture at a higher resolution; nothing in the runtime reads pixel coordinates from it.
  - **A run's page behaves the same whether or not it is on screen (2026-11-14).** Showing the page is presentation; hiding it (another tab, a dialog over the pane, a folded pane, a covered or minimized window) must never resize, pause or blind it. Measured in a scratch Electron 42 probe: a view that was shown at size and is then hidden (`setVisible(false)`), set to 0×0 bounds or minimized still reports 1280×720, runs `requestAnimationFrame` and timers, and receives CDP mouse input, but **a view born at 0×0 reads 0×0**, and **`Page.captureScreenshot` never answers** while the view is not drawn (the runtime's failure report used to wait its full 30 s). So: (1) the panel never shrinks a view it is not showing: `_hideView` is `setVisible(false)` and the view keeps its bounds; a zero rect from the renderer (folded pane, minimized window) hides the views and leaves every size and scale alone (`hideAll`, `_paneHidden`); (2) the panel, not the renderer, places every new tab: `_addTab` lays the run out at once from the run's rect (a test run's first view from the last pane rect, `_lastRect`), so a step that opens a tab reads a real size right away; `ensureGeometry` lays the run out again when re-applying the zoom did not fix a page; (3) control op `capture` (both panels) takes a still with `webContents.capturePage({stayHidden, stayAwake})`, which answers in every state; `page_geometry.capture` gives its own CDP screenshot 1.5 s on a host-owned page, then asks the host (`host_browser.capture`), and a host too old for the op just returns no still. The capture is the view at its on-screen scale, so it is a fallback, never the first choice. `backgroundThrottling` was measured and changes nothing, so it is not set. Execute's panel pins its page size with a metrics override and so only gained `capture`. **Not verified:** a full test replay through the packaged Studio with the window minimized mid-run, which needs a compiled workflow and a live site; the size fault now also logs the measured size (`fault_detected.live`) so the next one can be read. **Minimized window (2026-11-17, BUILD-103 (1)):** a view made or sized while Studio's window is minimized reads 0×0 until restore (Electron 42 probe), so a test started or opening a tab while minimized stopped at step 1. `main.js` reports the window's `minimize`/`restore` to `browser_panel.setMinimized`: while minimized every zoomed (test, sign-in) run's tabs are held at `viewport.width` × the last pane height by `Emulation.setDeviceMetricsOverride` (DPR 1, `scale` 1) at zoom 1, including tabs opened meanwhile (`_emulate`); at restore the override is cleared, the pane zoom comes back and the shown run is laid out again. Record runs already hold an override and are unchanged. The window is never brought back up for a test. Probe: with the override a minimized page reads 1280×720, takes CDP clicks, and `capturePage({stayHidden, stayAwake})` returns a real still (the first capture of a never-drawn view can fail with `UnknownVizError`, so `capture` tries once more after 700 ms); the CDP screenshot answers only while the view is visible, so a hidden page's still still comes from the host op.
  - **Video.** `recorder/screencast_video.py::ScreencastVideo` rebuilds what `record_video_dir` did: a raw CDP WebSocket per page target (`Page.startScreencast`, JPEG) fed into Playwright's own ffmpeg at a constant 25 fps, repeating the last frame, back-filled from `start_wall_ms`, cropped back to CSS pixels by the panel's scale. It writes the same contract compile reads (§6.4): `recording.webm` / `recording-<tab>.webm` + `videos.json`. Frame extraction is unchanged.
  - **Scripts and bindings per page, never context-wide.** Playwright's one CDP context is Electron's default session and holds Studio's own window and other runs' views. `_classify_pages_sync` asks the panel which devtools targets belong to the run (control op `targets`) and only those pages are adopted (`_adopt_run_page_sync`: page-level `expose_binding` + `add_init_script` for the bridge and dialog override, plus the usual listeners). A sign-in page gets only membership and a close handler.
  - **Sign-in state.** Seeding and capture go through the run page's own CDP session (`host_view.py::seed_storage_state` / `capture_state`, the §4.7 AUTH-20/21 pattern). learn_auth's LIVE capture opens a background tab in the same run (control `new_tab`, `focus:false`), observes it, reads its cookie names from that partition, and closes it; OUT/RELOAD stay standalone headless probes. Known limit: `localStorage` of an origin whose tab already closed keeps what the previous save held.
  - **Owning the browser.** Teardown stops the videos, disconnects (`browser.close()` on a CDP connection only disconnects and drops the page scripts) and posts `close_run`. The panel closes a `record`/`signin` run whose last tab closed, which the recorder sees at once as "the browser closed". Downloads are claimed through `save_download`. Window raising (`bring_to_front`, `AllowSetForegroundWindow`) is skipped. Teardown only runs after the recorder's shutdown drain (≥ 5 s of pumping the live pages for late events), so the pane does not wait for it: when a recording card reaches `saving` / `saved` / `discarded`, `RecordingCard` calls `browserStore.releaseRun`, which drops the run from the renderer at once and ignores its late tab updates until the panel's `closed` message arrives.
  - **Where it is drawn.** A conversation-led recording or sign-in shows in the right-hand `BrowserPane` with its controls (the live card mirrors them through `browserStore.flow`). A Group-page Connect is drawn in that page's own sign-in pane (`SignInPane` + the shared `useNativeViewRegion` hook); the right-hand pane ignores a `signin` run no conversation owns. A caller with nowhere to draw the view — any dialog, since a native view is hidden while a dialog is open (`RunGateDialog`, `RecordWorkflowDialog`) — passes `in_panel: false` and keeps the separate window (and, for sign-in, the docked `window_rect` placement).
- **Section heading identity (2026-11-16).** `bridge.js::sectionHeading` records, for every action, the text of the last heading (`h1`–`h6`, `[role=heading]`) before the element in document order, measured from its outermost shadow host. It is kept in `extras.section_heading`; `extras.shadow_path` now also persists the bridge's host chain, which was computed and then dropped on save. `build.py` puts the heading on `ElementFingerprint.section_heading`, and never an email-bearing heading. The runtime's `extractDescriptor` computes the same value (`sectionHeading`), and `resolver.scoreCandidate` charges it (weight 0.15) only when both sides have one. Identical twins in different sections then separate by ≥ 0.15, twins in one section are still refused, and older packs score as before. It is not part of the stable hash.
- **Exact beats contained in element scoring (2026-11-16).** Locators match contained-in text/role names on purpose (c320dba, 2026-09-07). `resolver.js::scoreCandidate` therefore grades name and text agreement: the same words (after dropping a keyboard-shortcut tail on the live side) score full, contained-in scores `CONTAINED_CREDIT` (0.4), so alone it stays under the 0.5 confidence threshold, and a look-alike with a longer name is never acted on through text alone. A fingerprint role that is only the tag with no implicit role (`comparableRole`) and anchor phrases written as selectors (`textAnchor`) are not charged. In text checks (`assertions.js` text_present and `page_scripts.countTextOccurrences`), what visible text boxes hold counts as on the screen. Password, hidden and file inputs are never read.
- **Frames identified by a relative src; text checks inside frames (2026-11-16).** `frame_utils._iframe_fingerprint_from_attrs` keeps the `iframe[src=…]` signal for a relative src (checked against the frame's own URL with `urljoin`); before, such a frame (no id/name/title, common on demo and embed pages) got no signal and was silently dropped from `frame.chain`, so its steps compiled as top-page steps. `build.py::_insert_start_navigate_step` opens the tab's URL, not the frame document, when the first event is inside a frame. `assertions.js::countTextAcross` counts a text check's words over the page and all its frames, before and after the step, so a result shown inside a frame can be "new" and prove the step (it read the top document only: 0 before, 0 after). `clarify.py::_unproven_end` asks "How can we tell it worked?" with only a typing box when the last acting step has no required check or completion checks but its `video_context` shows the screen changed.
- **Where a page-loading click lands, and a recorded sign-out (2026-11-16).** A click that loads a new document kills the bridge before its "after" sample (bridge.js `_applyAfter`), and the navigation sweep skips renderer-initiated loads, so such a click used to keep no `url_delta` (the last step of a recording had no landing at all). The sweep now queues a `_landing` payload behind the action when a renderer-initiated, cross-document load commits right after an interaction; `_apply_landing_sync` writes `post_condition` {classified_effect: navigation, url_delta} onto the last action taken on the page that was left (never over a stronger effect or an existing landing; a later bridge amend keeps it). A click-caused `manual_navigate` onto a sign-in page is kept (`to_sign_in`) only for `navigation_effects.py` to fold onto its click; anything else on a sign-in page is still dropped. In the runtime, `run.js::landedAsRecorded(step, page)` (the step's own `url_pattern` evidence matches the page) makes a sign-in page or an `auth_expired` text at that address the step's result: `isAuthFailure` (run.js and server.js), `detectFault`, the completion gate and the `failure_signal` probe all apply it. Scoped to that one step: any other step landing on sign-in is still a dead session. Studio's group write-back after a recording now adopts the recording's cookies only once `check_group_sessions_sync` answers "ready" for them (run in the background after Finish), so a recording that signs out never replaces a working session.
- **Prompts in a host-owned browser (2026-11-16).** Electron does not implement `window.prompt()`: under CDP an alert and a confirm arrive as Playwright dialogs, a prompt raises nothing and does nothing (verified in a bare Electron view). The recorder already sidesteps this (its dialog server replaces the page's dialogs). On replay in a host-owned run (Studio's test pane, Conxa Execute), `run.js`'s one-step-ahead dialog pre-arm also calls `page_scripts.armPrompt` in every frame when the next step answers a recorded prompt: the next `prompt()` returns the recorded/given answer (`null` for a recorded Cancel), once, and restores the page's own. The `dialog_accept`/`dialog_dismiss` handler then sees `takePromptAnswered` and returns instead of waiting `CONXA_DIALOG_WAIT_MS` (120 s) for an event that never comes. A real Chromium keeps the native dialog path.
- **A tab the site opens (2026-11-16).** A popup or `target=_blank` tab comes through `setWindowOpenHandler`'s `createWindow` before Chromium has loaded anything into it, and attaching the debugger to a never-navigated `webContents` crashes Electron (the whole app, no message). So `browser_panel.js::_addTab` sets up such a tab's emulation and devtools target id only on its first `did-navigate` / `dom-ready` / `did-finish-load`; `targets` waits for that at most 5 s, so a tab that never loads cannot hold the recorder. Tabs the panel creates itself load their marker first and are wired at once, as before.
- **Panel:** `BrowserPane.tsx` + `browserStore`. Built like Chrome: a tab strip (favicon or spinner, title, ×, middle-click closes, **+** opens a tab), back / forward / reload-or-stop, an address bar that loads a URL, adds `https://` to a bare host (`http://` for localhost/IPs) or searches Google (`browser_panel.js::_normalizeUrl`), a right-click menu (`_contextMenu`: open link in new tab, copy link, cut/copy/paste, back/forward/reload) and Chrome's keys caught inside the page through `before-input-event` (`_shortcut`: Ctrl+T/W/L/R, Ctrl+Shift+R, F5, F6, Alt+D, Alt+←/→, Ctrl+Tab/PageUp/PageDown, Ctrl+1–9). A tab the person opens (`personTab`, IPC `panel:new-tab`) is an ordinary run tab with no opener, so the recorder records it as `opened_by: user` and a typed address as `manual_navigate` (the marker page reads as `about:blank` in `from_url`) — verified against real Electron. Folds to a strip, resizes with a focusable divider. A native view floats above the page, so the pane reports its rect to the main process on every layout change (`panel:bounds`) and hides every view (`panel:hide`) while a dialog or sheet is open (`useNativeViewRegion`). Nothing can be drawn over the page, so a run's state lives in a coloured top edge (blue getting ready, amber sign-in, red recording, grey paused), the header and the footer. A record/sign-in pane can widen, folding the conversation to a 64 px rail. While any pane is on screen the left navigation steps aside (`resolveBrowserLayout` → `navMode: 'hidden'`, 60 % default width); it is derived, not stored, so folding or closing the browser restores the person's own navigation setting, and the navigation toggle only flips a per-run `browserStore.navShown` (navigation back as the rail).
- **Isolation:** record and sign-in runs get the in-memory partition `conxa-run-<runId>`. Test runs share the on-disk partition `persist:conxa-test-cache` so the HTTP cache stays warm between tests (a cold cache per run made tests crawl on slow networks); cookies and site storage are wiped before each test (`prepareTestRun`, awaited by `new_view`) and when the run closes. A test overlapping another live run on the shared partition falls back to its own in-memory one. The cache itself (not cookies) stays on disk between sessions; popups open as tabs; downloads are saved silently under `%TEMP%/conxa-studio-downloads/<runId>/` and claimed by URL through the control channel (`save_download`).
- **Security:** the control channel is loopback-only with a random per-process token. The CDP port is loopback-only too, but it covers every `webContents` in Studio's process, including the app window itself. Anything on the machine that can reach `127.0.0.1:<port>` can drive Studio, which is the same exposure Execute already accepts. The port is random per launch and never written to disk.
- **Verified** in a real Electron process (scratch harness, not committed): a Playwright `connectOverCDP` client found the borrowed view by its marker URL, navigated it, typed into it, saw a `window.open` become a second tab (no stray window), claimed a silently-saved download, and the finished page stayed until `closeRun`. 12/12 checks. **Not verified:** a full skill replay through the packaged Studio, because that needs a compiled workflow and a live site.

### 4.8 Standalone Runner & Scheduler

Skills can run on a schedule with no chat app open. Per `docs/PRD.md` §14.5, workers and schedules live on customer infrastructure: no cloud scheduler, queue, or schedule sync.

**The daemon is an MCP client.** `scheduler_daemon.js` spawns the same host exe a chat host would (`process.execPath` packaged; `node app/server.js` in dev) and drives it over stdio with the vendored SDK client. So `server.js` needs no changes and every engine guarantee applies: auth pre-flight, startup sync, telemetry, the concurrency cap, host locks (now cross-process), parks, deadlines. The transport passes `env: process.env` explicitly (the SDK otherwise filters env vars and the engine boots the wrong dev/prod lane). Scheduled runs pass `watch: false` and `_trigger: "scheduled"` (visible in `execute_start` logs and the run registry).

| Module | Role |
|---|---|
| `app/cron_lite.js` | 5-field cron + `nextAfter()` (Vixie dom/dow OR rule, presets, DST-safe). No dependency |
| `app/scheduler_store.js` | One JSON per schedule in `scheduler/schedules/`. Inputs AES-256-GCM encrypted under a machine key (keychain `conxa-session/scheduler-v1`). Atomic writes. `listMeta()` never decrypts |
| `app/scheduler_daemon.js` | 30 s tick; pure `computeActions()`; engine child lifecycle (lazy spawn, 10-min idle kill, bounded restart); singleton `daemon.lock` (PID + heartbeat); `state.json`; command inbox; daily logs |
| `app/file_lock.js` | Cross-process platform mutex (§4.6) |
| `app/tray_windows.ps1` | Windows tray (`NotifyIcon`, no deps). View-only: actions write command files; exits when the daemon lock disappears |
| `app/scheduler_cli.js` | `schedule …` / `runner …` subcommands |
| `host/cli_schedule.js` | Host shim: resolves the app layer through the `min_host` gate, dispatches argv |

**Missed runs: catch up within grace.** A slot overdue ≤ `catchup_grace_minutes` fires; older slots produce one skip record fast-forwarded to the first slot young enough to fire. Capacity pressure defers (retry next tick) rather than skips. A `busy` refusal leaves the slot pending; only completed/failed/error advance it. `run_now` records `last_run` without touching slots.

**Entry points.** Chat: `create_schedule` validates slug and cron and returns the resolved target hosts (so the agent can say which schedules serialize). CLI: `schedule add|list|show|remove|enable|disable|run-now|pause|resume|daemon`, `runner start|stop|status|autostart on|off|setup|doctor`. Autostart writes a per-user Startup `.lnk` to `<exe> schedule daemon`. `doctor` checks Chromium, skill packs, keychain, cron validity, lock-dir writability, and (read-only) Windows AC standby timeout.

```
<CONXA_DATA_DIR>/scheduler/
  schedules/<sch_id>.json     schema: docs/Backend-Schema.md §1.4
  state.json                  daemon is sole writer
  daemon.lock                 {pid, heartbeat_at}
  commands/*.cmd              {"type":"pause"|"resume"|"quit"|"run_now", …}
  logs/scheduler-YYYY-MM-DD.log
```

**Release sequencing:** subcommand dispatch is a host change (needs `host-v*` + `host-manifest.json`); the rest ships as app updates. Old host + new app: tools absent. New host + old app: subcommands blocked by the `min_host` gate's error.

**Limitations:** host locks are per machine, so two runner VMs on one platform can still overlap (`docs/Runner-Machine.md`). Session lifetime caps unattended use (PROD-4); a dead login fails fast at pre-flight. No Mac tray.


---

## 5. Authentication & Distribution

### 5.1 Auth Systems

| System | Mechanism | Storage | Identity provider |
|---|---|---|---|
| Build Studio | Clerk PKCE OAuth | OS keyring (`keyring`, service `conxa-studio`) | Clerk (`clerk.conxa.in`) |
| Conxa Execute | Clerk PKCE OAuth (own client, same instance) | Electron `safeStorage` | Clerk |
| Cloud API | Clerk JWT via PyJWT + JWKS | stateless | Clerk |
| Cloud frontend | Clerk Next.js SDK | session cookie | Clerk |
| Runtime → Cloud | Installer-embedded `sync_token` / tracking token | `pack.json` | Conxa Cloud |
| Runtime → target platforms | Playwright storageState per app | AES-256-GCM files, key in OS keychain | The platform itself |

End users never sign in to Conxa. They sign in only to their own target platforms.

### 5.2 Build Studio Login & Legal Gate

```mermaid
sequenceDiagram
    participant Studio as Renderer
    participant Backend as Python backend
    participant Browser as System browser
    participant Clerk as clerk.conxa.in

    Studio->>Backend: {type:"login"}
    Backend->>Backend: PKCE verifier + challenge; listen on 127.0.0.1:52741
    Backend->>Browser: open authorize URL
    Browser->>Clerk: /oauth/authorize?code_challenge=…
    Clerk->>Browser: redirect 127.0.0.1:52741/cb?code=…
    Browser->>Backend: GET /cb?code&state
    Backend->>Clerk: POST /oauth/token (code + verifier)
    Backend->>Clerk: GET /oauth/userinfo
    Backend->>Backend: store tokens in OS keyring
    Backend-->>Studio: {org_id, user_id, name, email}
```

`auth_service.get_token()` refreshes transparently within 60 s of expiry. Every protected call sends `Authorization: Bearer`, verified by the middleware (§3.4) and turned into a `Principal` by `saas.py::principal_from_request()`.

**Startup gate chain** (`renderer/src/App.tsx`): deps bootstrap → mandatory update → Clerk sign-in → legal acceptance → routes. Acceptance comes after sign-in so the record names an identified user.

1. `legal_status` → `GET /api/v1/legal/current` (`{version, documents:[{id,title,url,sha256}]}`) and `GET /api/v1/legal/acceptance`.
2. If not accepted: blocking Terms & Privacy screen. `legal_accept` → `POST /api/v1/legal/acceptance` with `document_hashes`; the cloud verifies them against frozen per-version snapshots and writes once.

**Fail-closed:** if the cloud is unreachable, Studio shows a blocking retry screen (a Conxa outage stops Studio from opening). Unpackaged dev builds skip the gate. Contracts: `docs/Backend-Schema.md` §5.11, §7.

### 5.3 Workflow Groups & Target-Platform Sign-In

#### 5.3.1 Model and pack contract

A **WorkflowGroup** (`conxa_core.models.workflow.WorkflowGroup`, `storage/group_store.py`) is a business-domain folder ("Sales") that owns workflows and the apps they sign in to. Each **GroupApp** has `name`, `login_url`, `success_url`, a captured session path, `captured_at` (a session file exists), `checked_at` (last live verdict), `last_error`, `detect_warning`, and an optional learned `auth_definition` (§5.3.5).

- Every workflow belongs to exactly one group (`Workflow.group_id`); the workspace's `Default` group catches ungrouped ones (`workflow_store._migrate_workspace` assigns on read).
- Auth is captured **once per app, per group**. A group with no apps is valid: its skills run ungated.
- **A group is the unit of sign-in.** A skill runs only when every app in its group is signed in. There is no per-skill subset (`required_apps` was removed; an older manifest's field is ignored).

`pack.json` carries:
- `groups`: `[{id, name, apps: [{id, name, login_url, success_url, auth_definition}]}]`, the single source of truth for sign-in.
- `skill_groups`: `{skill_slug: group_id}`, the path index. Skills live at `skill-packs/{ws}/{group_id|_default}/{skill_slug}/` in both Studio build output and the runtime.

Each skill's `manifest.json` carries `group_id` and, when non-empty, `unclaimed_hosts`.

**No fallback.** A pack without `groups`, or a skill whose group is missing, fails at run time ("No sign-in group found … rebuild the pack in Build Studio"). `build_skill_package` refuses to build a workflow whose group was deleted.

**Touched apps and unclaimed hosts.** `Workflow.visited_hosts` holds hostnames a recording navigated (main frame + tabs), extracted by `recorder.session.extract_visited_hosts` at save time (and stored as `SkillMeta.visited_hosts` at compile). `group_store.apps_for_workflow(apps, target_url, protected_url, *visited_hosts)` matches by **site** (last two labels; IPs exactly) against each app's `login_url`/`success_url` and feeds the group page's platform chips (`cmd_get_group.used_apps`). It no longer gates recording, which requires every group app (§5.3.3). `group_store.unclaimed_hosts(...)`, from the same inputs, returns hosts no app covers. Warnings are non-blocking:
- Studio shows an amber "host · no sign-in" chip.
- The runtime returns `unclaimed_hosts` + `warning` from `authenticate` and emits a `no_signin_configured_for:<hosts>` `test_phase` during pre-flight.

**Host locks** (`runtime/app/target_hosts.js::resolveTargetHosts`) cover every group app's host (§4.6).

**Group sync to cloud.** Creating or renaming a group upserts `{id, name}` via `PUT /api/v1/workflows/{installer_version}/groups/{group_id}`, so Skill Packages shows the folder before anything is published. `Default` isn't synced until renamed. Deleting a Studio group doesn't delete the cloud folder. Cloud errors never fail the local operation.

**Group summary** (`cmd_list_groups`): `{id, slug, name, workflow_count, stages, workflow_preview, apps_total, apps_authenticated, ready, created_at, updated_at}`. `stages` counts `derive_workflow_stage` values and `workflow_preview` holds the first 3 workflows; both come from already-loaded data (`docs/UI-UX-Brief.md` §2.3).

**Success-URL wildcard.** `{}` in `success_url` means "anything after" (`vercel.com/{}`). `recorder.session.url_matches_pattern(url, pattern, exclude_prefix)` is the single matcher. In the runtime, `success_url` is only a navigation hint (`_protectedUrlOf`: `success_url` → learned landing address → `login_url`); detection never reads it (§5.3.4).

#### 5.3.2 Connecting an app (Build Studio)

1. `cmd_start_group_app_auth` launches the recorder in `auth_mode` at `login_url` with `wait_for_url=success_url`. `reached_wait_url` is a **passive hint only**; nothing auto-stops the session on it.
2. While the window is open, the recorder saves storage state once per distinct URL whenever the top page navigates off the login page (`_auth_saved_url`; sets `auth_captured`). It saves then, not at teardown, because once the `disconnected` handler clears `browser_open` a teardown save would hang CDP. `auth_captured` is informational: many login pages redirect once on load.
3. **Only the user's Done ends the session.** `GroupAuthWizard` polls `get_recording_status` every 1 s and auto-finishes only on browser close or a poll error. It offers Done and Cancel.
4. `cmd_finish_group_app_auth`:
   - requires a real session (`_has_captured_session`: at least one cookie or localStorage origin), otherwise `auth_capture_failed` ("You closed the login window before signing in…");
   - runs learning (§5.3.5). A failed self-test returns `{confirmed:false, reason}` and keeps the window open. "Save anyway" (`force:true`) skips learning;
   - persists `data/groups/{group_id}/auth/{app_id}.json` and marks the group's workflows `ready` once every app is connected. Sets `detect_warning` when `success_url` was never reached (amber note on the app card).

**Pending file.** While a sign-in runs, the recorder autosaves to `{app_id}.pending.json`, never the saved `{app_id}.json`. `cmd_finish_group_app_auth` promotes it with `os.replace` once captured, and `cmd_cancel_group_app_auth` deletes it. Before this, the teardown save of a cancelled or half-done sign-in overwrote a working saved sign-in.

**The sign-in tab's URL is live.** Playwright's sync API takes in browser events only while its thread is inside a Playwright call. A sign-in's pump loop makes none, so `page.url` (and `current_url`, `reached_wait_url`, the leave-the-login-page autosave) stayed on the first page forever. `RecordingSession._idle_sync` waits through `page.wait_for_timeout` in `auth_mode` instead of `time.sleep`.

**Automatic confirmation (conversation flow).** `workflow_flow.py::_flow_watch_sign_in` runs beside each waiting sign-in card. It polls the session's `current_url` every 1.5 s. Once the tab has been on a login-shaped page (`_LOGIN_PATH_RE`, the runtime's login/sign-in segments plus SSO hops) and then leaves it, or `reached_wait_url` is set, it runs the same confirmation as the button (`_flow_finish_sign_in` → `cmd_finish_group_app_auth`, which self-tests against a signed-out context, so a page that only looks done is not accepted). It checks once per page (query ignored), and an early "not confirmed" is silent. On success it opens the next app's sign-in or the recording. A lock plus a "card still waiting" check keeps the button and the watcher from both finishing. The Group page's Connect wizard still finishes on its own Done.

A hand-closed browser leaves any existing `auth_definition` untouched. `cmd_update_group_app` clears `auth_definition` when `login_url` changes.

**Freshness.** `captured_at` never expires, so `check_group_sessions_sync(targets, group_apps)` runs a bounded headless probe (`_probe_group_sessions_sync`): ONE throwaway Chromium holding the merged saved sessions of every app in the group (newest file first), one tab per checked app, stealth options on (`STEALTH_CONTEXT_OPTIONS`, `navigator.webdriver` masked), the same browser model a run uses. "Check now" on one app loads the whole group's sessions too. A second, cookie-less context fetches each login address as a signed-out visitor sees it (the baseline; skipped for an app with a learned rule). Every tab opens the app's `probe_url`/`login_url`, and the verdict is `auth_learning.classify_session`, the Python twin of `login_signals.js::classifySession` (the rule is listed in §5.3.4 and locked by the shared fixture `runtime/test/fixtures/session_check_cases.json`, run by `test_login_signals.js` and `test_session_check.py`). Only a positive confirmation is `ready`. `expired` is a missing/corrupt state or positive sign-in evidence. A navigation error, driver failure, probe timeout (30 s hard deadline) or unrecognised page is `unverified`, never `ready`. `ready`/`expired` stamp `checked_at` (and `last_error` on expiry) via `set_group_app_checked`; `unverified` clears `checked_at`, sets `check_note`, and leaves `last_error` alone, so it neither reads as signed in nor locks recording. A sign-in saved without the self-test (Save anyway, window closed by hand) is likewise left unverified. `group_auth_status(group)` is cheap and read-only; it reports `state`, `verified` (`checked_at` within 600 s), `check_note`, and `apps_verified` (the only count the UI shows as "signed in"). Probes run only from `cmd_start_recording` and the user's "Check now" (`cmd_check_group_app_auth`), never on page load.

#### 5.3.3 Recording with group auth

- **Seeding:** `cmd_start_recording` merges every captured app's state (`storage_state.merge_storage_states`: cookie union, per-origin localStorage merge) into one context.
- **Gate (AUTH-16):** `handlers/session.py::group_apps_needing_sign_in(group)` requires **every app in the group** to be captured and not expired, the same rule as the runtime pre-flight (§5.3.4). A workflow that starts on one app routinely crosses into another (start on GitHub, upload to Drive), so the start URL alone can't say which apps it needs. It probes every captured app not checked in the last 600 s (together, in one browser seeded with the whole group's sessions, bounded at 30 s) and returns every app that is never-connected, expired, **or not positively confirmed** (an inconclusive probe clears `checked_at`; a saved session file alone is never proof). `cmd_start_recording` raises `auth_required` naming them before any browser opens; the conversation flow (`workflow_flow.py::_flow_missing_apps`) uses the same function and opens a sign-in card for each in turn ("sign-in expired" vs "needs sign-in"), recording only once the list is empty. `apps_for_workflow` no longer gates anything; it stays for the group page's platform chips.
- **Pre-flight only:** no mid-recording auth checks and no periodic autosave (`context.storage_state()` is heavy and made the window flicker). If a site logs out mid-recording, the user signs back in inline.
- **Write-back:** the recorder autosaves once at teardown to `merged_group_state.json`. `_refresh_group_app_sessions` then splits it per app with `storage_state.refresh_app_state` (keeping only domains/origins that app already owned), writes each app's file and calls `mark_group_app_unverified` (clears `checked_at` only). Cookie rotation and mid-recording re-logins stick, but the write-back never vouches for a session: an app that ended the recording signed out keeps its expired verdict and is probed again at the next gate. (It used to go through `set_group_app_auth`, which stamped every app freshly verified and hid an expired sign-in from the next record click.)

#### 5.3.4 Runtime pre-flight

`browser.js::getAuthContext(company, authManager, {groupId})` → `_resolveGroup` against `pack.json` → `getGroupAuthContext`:

- **Sessions** are keyed `${workspace_id}__${appId}` (`auth_manager.js` functions all take the key). **Precedence is newest-mtime-wins at read time** (`_loadSessionForKey`): a `_raw_state.json` newer than its encrypted sibling (Studio re-staged a session, or an SG-11 plaintext fallback) wins and is promoted to encrypted immediately (`test_session_precedence.js`).
- **Seeding** merges every stored app session, newest first, because `mergeStorageStates` keeps the first cookie per name/domain/path and a stale shared SSO cookie must not win (`test_session_auth.js`).
- **Validation:** only apps without a fresh stamp are probed, as throwaway tabs of the run's own session browser (`_probeInSession`). Stamps live in `<sessions>/_auth_validation_cache.json` for `CONXA_AUTH_VALIDATION_TTL_MS` (default 10 min, equal to the recording gate's 600 s) and record the session file's mtime, so a fresh login invalidates them (`test_auth_validation_cache.js`). `test_phase` markers `group_auth_validate_start`/`_done` reach Studio's Run Test log.
- **Detection** (pre-flight, the prover, and Build Studio's Check now / Check all / recording gate, §5.3.2) navigates to `probe_url`/`login_url`, never `success_url`, then applies ONE rule, `login_signals.js::classifySession` (Python twin `auth_learning.classify_session`), returning `ready` / `expired` / `unverified`. Pre-flight counts only `ready` as signed in. The first check that fires wins:
  1. the page is not http(s) (navigation never completed) → `unverified`;
  2. the app has a learned definition → `evaluateAuthDefinition` `yes` → `ready`, `no` → `expired`; when it cannot tell, only sign-out evidence (3, 4, 6 below) moves the answer to `expired`, otherwise `unverified` (the success page and baseline are the generic path's proof, not this one's);
  3. a visible password box → `expired`;
  4. a sign-in word as a whole path segment at any depth (`login|log-in|signin|sign-in|logon|signon|sso|oauth|oauth2|auth|authorize|authenticate|servicelogin|session-expired`) → `expired`. No host or IdP lists;
  5. `success_url` matches (the part before a literal `{}` is a prefix) → `ready`;
  6. a visible sign-out control (Sign in / Log in / Sign up / Get started / Continue with … / Sign in with …) → `expired`;
  7. a baseline exists (`_signedOutBaseline`: the login URL fetched in a fresh incognito context, cached for the TTL) and the page's address differs from it → `ready`; the same address → `unverified`;
  8. no baseline and a host-owned (Execute) session, which cannot open a cookie-less context → `ready` (the accepted weaker fallback); no baseline otherwise → `unverified`.
  Between loading and step 3, a page that still looks like sign-in (3 or 4) is re-read every 200 ms for up to 3 s (`_settleOffLogin`), so a client-side redirect off the login page can finish.
- **Sign-in:** one Chromium per session (§4.6). Every missing/expired app gets its own sign-in tab at `login_url`, in the same context the run then uses, so earned cookies are already in place. The message counts the group's apps and names the missing ones. An app whose sign-in is already pending returns `already_open` before any session or probe, so a repeat call can't start a second session.
- **On success:** only the login tab and pages it opened (`opener()` chain) close. The app's slice is written immediately (`refreshAppState`: its previous domains/origins, its own login/success hosts on first login, and cookies no other app claims, such as the IdP's) and the stamp is set.
- **Failure:** a closed tab or browser, a timeout (`LOGIN_WAIT_MS`, 10 min), or a failed launch ends the wait with a specific reason (`describeAuthWait`) and discards the half-signed-in session. A launch failure returns `launch_failed` results (`authenticate` reports `failed`).
- **Headless limitation:** a chat-triggered `watch:false` run needing a human opens its own visible window (a second Chromium) and doesn't park it.

Host comparisons remain only for attribution (`captureReAuth`, `refreshAppState`); `_hostOf` strips a leading `www.`.

#### 5.3.5 Login-completion detection

A live sign-in is watched by `_waitForSessionLogin` / `_waitForInteractiveAuth`, both driven by one shared ladder in `login_signals.js` so they can't drift. For a launched session the wait polls every page of the context (sign-in in another tab, popups, SSO hops all count). A host-owned (Execute) session only watches pages its own login created, since Electron's context is shared (EXEC-45).

**With a learned `auth_definition`,** the ladder is replaced: a throwaway-tab probe (`_probeAuthDefinitionVerdict`) must return a confident **yes**. "No" and "unsure" keep waiting until timeout.

**Otherwise, the generic ladder:**

1. **Before snapshots**, taken in `beginInteractiveAuth` in a throwaway tab before the login window opens (so they can't race a fast sign-in): the judge's `beforeSnapshot` (`{url, hasPasswordBox}`) and the `ticketBaseline` (cookie names + storage key counts, never values). If the before snapshot is already not login-shaped, `alreadySignedIn` saves at once.
2. **Lookouts:** `passwordGone`; `newTickets` (the context's ticket signature changed from the baseline); `landed` (a level check: the login tab no longer looks like a login). A **pause sign** (OTP-shaped inputs or `autocomplete="one-time-code"`) suppresses everything while visible.
3. **`onSignIn` (mid-flow):** `_sampleLookouts` records each own tab's first sign-in state and latches `progressed` once it changes while still on a sign-in page. `ladderVerdict` returns `wait/on_sign_in` ahead of every rung while this holds. An untouched login tab never blocks, so a sign-in finished elsewhere is still found.
4. **Judge** (`_judgeVerdict`): asked only when a new lookout has fired since the last ask, or this login's tabs moved to a different page, and never while `onSignIn`. It re-fetches the login entry URL in a throwaway tab (focus-less, parked at 0×0 in Execute) and runs `judgeFromSnapshots(before, after)`: login-shaped → **no**; different and not login-shaped → **yes**; same address → falls back to the baseline compare, which may only answer **yes**.
5. **Backup rule:** only when `judgeSettled` (the judge answered for the page the person is on now): 2+ lookouts held for `CONXA_LOGIN_BACKUP_AGREE_MS` (default 10 s) saves.
6. **Human override:** a file `<CONXA_DIR>/login-done/<workspace_id>__<appId>.cmd` saves unconditionally. It's rooted at `CONXA_DIR` because Execute knows that path but not `CONXA_DATA_DIR`. It's written by `conxa-runtime login-done <key>` (host subcommand) or Execute's per-tab Done button. The renderer only sees `isLogin`; `browser_panel.js::loginDone` looks up the key server-side, threaded via `loginKey` on sign-in tab creation.

**After "save":**
- **Timekeeper** (`_waitForTicketsCalm`): two consecutive matching ticket samples after the decision, or `CONXA_LOGIN_TIMEKEEPER_BUDGET_MS` (default 5 s).
- **Prover** (`_proveSession`): re-tests the captured state in an isolated `browser.newContext({storageState})` (a fresh tab of the shared context under Execute). Failure doesn't block the save; it surfaces as `login_warnings`.
- **Account name** (`page_scripts.js::accountNameProbe`, the only text-reading probe): best-effort, surfaced as `signed_in_as`.
- **Landing address:** `_writeLanding` remembers the first real post-login origin (never login-shaped) as a navigation hint.

**Decision log:** every terminal outcome (`judge_yes`, `lookouts_agreed`, `human_override`, `timeout`, `closed`, `gone`, `abandoned`, `rejected_url`) is appended to `<CONXA_DIR>/logs/login_signals.log` (`login_decision_log.js`, `{ts, key, decision, waitedMs}`, size-capped) for tuning. It has no reader. `LOGIN_HUMAN_PROMPT_MS` (25 s) only logs `login_signal_inconclusive`.

Accepted trade-offs: a person who starts in the login tab and finishes elsewhere resolves via override or by closing that tab. A site whose login URL still shows a form to signed-in visitors never auto-completes, and pre-flight reads it as signed out anyway.

Design reference: `docs/artifacts/login-desk.html`.

#### 5.3.6 Learned authentication definitions

An optional per-app rule Build Studio observes, replacing the generic guess. `None` means the ladder above applies unchanged.

**Learning at Done.** While the browser is open, `RecordingSession.learn_auth(login_url, existing)` observes the probe URL three ways: **LIVE** (a new tab of the signed-in context), **OUT** (cookie-less context), and **RELOAD** (fresh context from the just-saved state). If `existing` still passes `self_test` against these observations it's returned **unchanged** (so a routine Reconnect doesn't look like a setup change). Otherwise `conxa_compile.auth_learning.learn(live, out)` keeps only signals that differ between LIVE and OUT, and `self_test` must see LIVE→yes, OUT→not yes, RELOAD→yes. When the sign-in address learns nothing that survives the self-test, the same three observations are repeated at the app's success address (`success_url`, the part before any `{}`) before giving up; a definition the app already has is tried first at its own `probe_url`. `learn_auth` is a cross-thread request/poll bridge to the recorder's sync-Playwright thread.

**Shape:** `{version, probe_url, signed_out: {final_path, markers, password_box, doc_denied?}, signed_in: {final_path, endpoints, doc_ok?}, session_keys, journey_hosts}`. `doc_ok`/`doc_denied` (the page's own HTTP status, e.g. `"2xx"` signed in, `"500"` signed out) are written only when the two sides answer a different status class, so every other definition stays byte-identical. A pure function of its observations: no timestamp, no cookie values, bodies, or query strings. UI markers with account-specific names are filtered (`_clean_markers`). Ships in `pack.json` `groups[].apps[]`.

**Evaluation:** `login_signals.js::evaluateAuthDefinition` mirrors `auth_learning.evaluate()`; both suites run the shared fixture `runtime/test/fixtures/auth_definition_cases.json`. Five signal classes, each yes/no/null (`doc`: the learned signed-out status is "no", the signed-in class is "yes"; null when the observer reports no status):
- **url**: exact match to the learned signed-in or signed-out path;
- **network**: a learned endpoint (`:id`-templated) returns its ok or denied status class;
- **dom**: a password box is "no"; absence of all signed-out markers is "yes";
- **session**: a learned cookie name is present (weak; never alone, never a veto).

**Yes** requires 2+ positive classes including one that isn't session. Any "no" vetoes. An OTP-shaped page is always "unsure". `browser.js::_gatherDefinitionObservation` navigates to `probe_url` with a response listener attached first; it serves pre-flight, the prover, and the judge.

#### 5.3.7 Waiting, scheduled runs, and mid-run expiry

**A run waiting for sign-in detaches.** `execute_skill` returns at once with "Authentication required — please sign in to X in the browser window that just opened … the workflow then starts on its own — do not run it again … check progress with get_execution_status (run_id: …)" plus `_meta["conxa/awaiting_auth"]`. It releases its host lock, and `_detachUntilSignedIn` waits in the background (`AUTH_DETACH_MAX_WAIT_MS`, 25 min; 10 min per tab). When every app is signed in it re-issues the same request with internal `_run_id` (so the caller's id stays valid) and `_after_auth` (no second detach), reusing the parked warm session. Outcomes land in `run_registry.js`'s recent-results ring. On failure the result is "Authentication failed or was cancelled…" naming the app, and the workflow never starts. An identical waiting request is joined (`findAwaiting`), not duplicated; `cancel_execution` reaches waiting runs. Awaiting runs hold a browser session but no run slot. **Conxa Execute's chat follows detached runs itself** (`conxa-execute/app/electron/run_follow.js` + `main.js::followRun`): the model's turn ends long before sign-in completes, so the main process polls `get_execution_status` until the run ends, updates the run's history entry, then makes one tool-less model call (a `[Conxa run update]` note, hidden in the UI) so the chat reports the outcome; a plain fallback line is posted if that call fails.

Not detached: scheduled runs, `wait_for_auth: false` (Build Studio's Run Test passes this, so a late login can't start an unattended sandbox run), launch failures, and runs resumed after their wait.

**Scheduled runs never prompt.** `server.js` passes `noPrompt`. `getGroupAuthContext` closes the session and returns `{authPending:false, expiredApps, message}` without `beginInteractiveAuth`, producing a `session_expired` failure naming the apps. The scheduler stores it as `last_run.message`.

**Mid-run expiry fails fast.** `run.js::isAuthFailure` short-circuits on a login redirect before the recovery cascade. `captureReAuth` resolves the dead app by the failing page's URL, then `manifest.target_url`, then `group.apps[0]` (logs `reauth_app_resolved`), clears that app's validation stamp (`auth_cache.clearValidation`) so the next call re-validates for real, and builds an app-named message. No window opens mid-run.
- If the resolved app has a learned definition (`hasAuthDefinition`), no window was opened, agent recovery is enabled, and this isn't already a retry (`args._mid_run_reauth`), `server.js` fires one background `execute_skill` under the same `run_id` with `resume_from: failedAt` and replies "opening a sign-in window now". That call runs the normal pre-flight and detaches like any sign-in.
- Otherwise the person calls `execute_skill` again.

**Run Test drift guard.** Run Test executes the built pack but stages the live group's sessions. `cmd_test_workflow` calls `handlers/protocol.py::_sign_in_setup_drift` (each live app's `id`, `login_url`, `success_url`, and `auth_definition` against the pack's `groups`) and raises `sign_in_setup_stale` ("Sign-in setup for the X group changed after the last build … Rebuild the skill package before testing."), the sign-in twin of `workflow_stale`.

Tests: `test_login_signals.js`, `test_login_multisignal.js`, `test_reauth_app_resolution.js`, `test_group_gates_every_app.js`, `conxa-cloud/tests/test_pipeline_gates.py::TestSignInSetupDrift`, `test_auth_learning.py`, `test_build_studio_backend.py` (finish-auth cases).

**Person confirmation (2026-11-15).** On a site whose signed-in and signed-out pages look the same (or answer an error at the same address), learning finds nothing to keep and the check can never confirm. "Save anyway" (`force:true`) now calls `group_store.set_group_app_person_confirmed`: `person_confirmed_at` and `checked_at` are stamped and `check_note` says "Confirmed by you". While `person_confirmed_at` is set, an `unverified` probe verdict keeps the app usable (`set_group_app_checked` restamps `checked_at`). With no learned rule, an `expired` read from the generic probe is downgraded to `unverified` too: on exactly the sites that needed the person's word, a sign-in form shows whether signed in or not, so it is not evidence against them. An app with a learned rule (`auth_definition`) that says signed out clears the confirmation and blocks recording. The pack carries `person_confirmed` per group app, and `runtime/app/browser.js::_isAuthenticated` honours it for an app with no learned rule (a run that really is signed out is caught mid-run by the sign-in-redirect check). A window closed by hand is still left unverified. Not done: probing the success address and the document HTTP status as extra learned signals (the person's word covers those sites meanwhile).

### 5.4 Runtime Tokens & Session Encryption

**Sync token.** `secrets.token_urlsafe(32)` minted at first publish (`publish_routes._sync_token()`), stored in `sync_tokens` KV keyed by `workspace_id`, reused across republishes, rotated by deleting the entry. Publish returns it. Studio writes it into local `pack.json`, and the installer stages `pack.json` verbatim. `installer_builder.py` fails with `skill_pack_not_published` if `sync_token` is missing.

**Tracking token.** Also minted at publish (`tracking_tokens[workspace_id]`) and returned with `tracking_url`; sent as `X-Tracking-Token`.

Neither token grants more than read access to the workspace's released skills or append-only telemetry. In local dev (`SKILL_AUTH_REQUIRED=false`) validation is skipped.

**Session encryption.** Target-platform storageState is encrypted at rest with AES-256-GCM using a per-machine key (32 random bytes per company, stored in keytar service `conxa-session`, HKDF via `auth_manager.js::_deriveKey()`). A leaked installer exposes only the sync token and can't decrypt any session file.

### 5.5 Publishing & Release

**Installers are thin.** An installer stages only `pack.json` (identity, `installer_version`, sync/tracking endpoints and tokens) into `$PROFILE\.conxa\skill-packs\{ws}\`. It never contains skill files. The runtime's first delta sync downloads every skill and creates each `current` junction, exactly like a later update. `installer_version` (a small allow-list such as `v1`/`v2`) is frozen into `pack.json` and never reassigned (`docs/Backend-Schema.md` §5.1a).

**Per-skill versions.** 1 workflow = 1 skill = 1 independent version history and release. Publishing, releasing, or rolling back a skill never touches another skill.

**Publishing is not deploying.** Build Studio can only publish; only a Clerk-authenticated cloud admin (`require_admin`) can release.

```mermaid
sequenceDiagram
    participant Studio as Build Studio
    participant Cloud as Conxa Cloud
    participant Admin as Cloud admin

    Studio->>Cloud: POST …/skill-packs/upload {files[], skill_pack_version, skills[]}
    Cloud->>Cloud: 409 if version exists (unless status ready/pending)
    Cloud->>Cloud: 409 skill_pack_artifact_unchanged if byte-identical
    Cloud->>Cloud: 1. immutable snapshot (release_files KV + disk)
    Cloud->>Cloud: 2. version row status="ready"; upsert skillpack_known_skills
    Cloud-->>Studio: 200 {tracking, sync_token, sync_url} — ready, not live
    Admin->>Cloud: POST …/releases/{version}/release
    Cloud->>Cloud: require status "ready" (else 400 release_not_ready)
    Cloud->>Cloud: 3. refresh mutable mirror; fold skill into pack.json skills/skill_groups
    Cloud->>Cloud: 4. refresh component_versions + signed manifest
    Cloud->>Cloud: 5. move stable channel pointer (the activation)
    Cloud->>Cloud: 6. row → "published", is_latest bookkeeping
```

- **Write ordering is the safety property.** Every write that can fail happens before the channel moves. A failure at steps 3–5 leaves the channel untouched and the row `ready`, so the release can be retried without republishing. A `ready` version waits indefinitely; there's no auto-promote.
- **Rollback** (`…/releases/{version}/rollback`, admin): requires a `published` row, reads its immutable snapshot, moves the channel, and refreshes mirror, component versions, and manifest. Nothing is rebuilt. The prior version becomes "superseded" by derivation. Always one skill; there's no group rollback.
- **Archive / unarchive** (`…/skills/archive|unarchive`, admin): removes the slug from `pack.json`'s `skills`/`skill_groups` union, so runtimes drop it from `list_skills` next sync (`skill_loader.js` indexes strictly against `pack.skills`). Nothing on disk, no version row, and no channel changes. Sets `archived` on `skillpack_known_skills` and records `skill_archived`/`skill_unarchived`. While archived, release and rollback return 409 `skill_archived`.
- **Deployment "failed" status:** `runtime/sync.js` records `pack.json.last_sync_errors[slug]` (`checksum_mismatch` | `download_failed` | `activation_failed`) and clears it on the next success. `sync_errors.js` sends it at phone-home (not sticky). `GET …/deployments` shows `failed` only while the installed version still lags the desired one.
- **Deterministic diff** (`app/services/release_diff.py`) aligns steps by semantic key (type/tab/frame/selector), not position, so a re-healed selector reads as "modified". Stdlib `difflib` only, byte-identical output, so the pre-publish preview and the `…/diff` endpoint agree.

**Installer upload** (`…/installer/upload`, raw `.exe`) is a separate, optional step from the Build Installer page. It stores to `data/installers/{ws}/` plus KV. Failure only sets `cloud_upload_error`.

Contracts and KV shapes: `docs/Backend-Schema.md` §5.1d. Product flow: `docs/App-Flow.md` §8.

### 5.6 Skill Sync

```mermaid
sequenceDiagram
    participant RT as Runtime (sync.js)
    participant Cloud as Conxa Cloud

    RT->>RT: read pack.json (sync_token, sync_endpoint)
    RT->>RT: since = {skill_slug: installed version} from nested skill dirs
    RT->>Cloud: GET …/skill-packs/delta?since={json} (Bearer sync_token)
    Cloud->>Cloud: compare_digest(token); compare each skill independently
    Cloud-->>RT: {skills: [{name, group, action:"no_change"} | {name, group, version, action:"update", files}]}
    loop each updated skill
        RT->>RT: write files to <skill>/<version>/ (.tmp → SHA-256 verify → rename)
        RT->>RT: version_manager.activate(keep: 3, requiredFiles: manifest.json)
    end
    RT->>RT: write pack.json (.tmp → rename); reload skill index
```

- `_build_delta()` (`skillpack_update_routes.py`) reads only **released** state (`pack.json` skills + `component_versions`); `ready` versions are invisible. `group` comes from `skill_groups` (fallback `_default`).
- A changed skill ships all its files (`execution.json`, `recovery.json`, `inputs.json`, `manifest.json`, `validation.json`); no per-file diff within a skill.
- **Legacy flat layout:** the client only checks nested paths, so a skill synced under the old flat `skill-packs/{ws}/{slug}/` redownloads once into the nested path. The cloud serves files still stored at an old flat path while reporting the resolved `group`.
- Rate limiting is KV-backed (`rate_limits` namespace), shared across instances. No Redis (`docs/Security.md` SG-04).
- Sync is skipped if it ran in the last 5 minutes; failures fall back to cached skills.

### 5.7 Runtime Self-Update

One **Ed25519-signed manifest** (`GET /api/v1/manifest.json`) drives everything. `manifest_manager.js` fetches it on every launch (last verified copy on failure), verifies it against a public key baked into the host exe at build time, and decides per component using:
- semver;
- `min_host` (an app version never activates on a host too old for it; refused outright rather than activated and rolled back);
- `minimum_versions` (forces an update regardless of rollout);
- a deterministic rollout bucket, `sha256(install_id + component) mod 100 < rollout.percentage`, stable across polls.

A bad signature is treated like a network failure.

`version_manager.js::activate()` validates the new version, flips `current`, and prunes beyond retention while protecting the version live just before. `rollback()` flips `current` back.

| Component | Where | Behavior |
|---|---|---|
| `conxa_app` | `bootstrap.js`, pre-load | Download zip, extract to `conxa-app/<version>/`, require `server.js` present, `activate()`, live this launch. Tight budget (2 retries × 5 s), failures swallowed. `manifest_manager.js`, `http_client.js`, `install_identity.js`, `version_manager.js` are baked into the host for this (the last also ships in the app zip for `sync.js`) |
| `conxa_runtime` | `server.js` `startupSync`, post-load, reusing `global.__conxaManifest` | Download `conxa-runtime.exe` + `keytar.node` to `conxa-runtime/<version>/`, spawn `--selfcheck` with its own `CONXA_DIR` (must exit 0 regardless of checksum), `activate()`. Effective next cold start. Generous budget (backoff, up to 2 min per attempt) |
| Chromium | `--install-playwright` | `playwright-core/cli` inside the exe, idempotent, run via `conxa-runtime/current/conxa-runtime.exe` |

While a host update is staged but not yet running, the update gate refuses new runs (§4.2).

### 5.8 Data Ownership

| Data | Owner | Location |
|---|---|---|
| Workflows, raw events, compiled skills, built packs | Build Studio | `~/.conxa/{workflows,sessions,skills,skill-packs}/` (§2.3) |
| Group app sessions | Build Studio (local only) | `data/groups/{group_id}/auth/{app_id}.json` |
| Published skill files + release snapshots | Cloud | `skillpack_files__{ws}` / `release_files` KV (durable) + `data/skill-packs/` disk cache |
| Installer binaries + history | Cloud | `installer_versions__{ws}` KV (base64, durable) + `data/installers/` disk cache |
| Sync / tracking tokens | Cloud | `sync_tokens`, `tracking_tokens` KV |
| Telemetry | Cloud | `tracking/{ws}` KV |
| Installed skills | End-user machine | `~/.conxa/skill-packs/` |
| Platform sessions | End-user machine | `%APPDATA%/Conxa/cache/sessions/` (encrypted) |
| Session encryption keys | End-user machine | OS keychain (keytar) |

**Render disk is ephemeral** (free plan, wiped on idle or redeploy). Local disk is a fast-path cache; Postgres-backed KV is the source of truth, and handlers rehydrate on miss (`_load_installer_from_db`, `_ensure_skill_pack_on_disk`).


---

## 6. Recording Pipeline

**Location:** `conxa-builder/python/conxa_compile/recorder/`

### 6.1 Capture

**A clock and a document id on every event (W2, 2026-11-18).** `bridge.js` mints one `DOC_ID` per document it lives in; every event carries it as `doc_id`, and the press time is `action.timestamp`. When `session.py::_capture_dom_snapshot_sync` saves (or reuses) a page copy for an event, the event's `snapshot` records `captured_ms` (wall clock when the recorder looked) and `doc_id` (read back from the page). A copy whose `doc_id` differs from the event's was taken after the page changed under the action. Anything that has to say *which action brought something up* uses these (`clarify.py::_cause_by_clock`), never the order of the copies; recordings made before fall back to copy order.

`session.py::RecordingSession` wraps a Playwright context seeded with the group's merged storageState (§5.3.3). By default that is a view in Build Studio's own browser panel (§4.7a, "embedded"); with no panel it is a launched Chromium window. The steps below hold for both; where the embedded path differs (per-page scripts, screencast video, CDP-session cookies) §4.7a says how.

1. `bridge.js` is injected into every frame via `page.addInitScript`. It captures `click`, `dblclick`, `right_click`, `type`, `fill`, `focus`, `select`, `select_option`, `set_checkbox`, `set_radio`, `date_pick`, `drag_drop`, `keyboard_shortcut`, `upload`, `navigate`, `scroll`.
2. `session.py` synthesizes `tab_open`, `tab_switch`, `popup`, `frame_enter`, `frame_exit`, `browser_back`, `browser_forward`, `manual_navigate`, `dialog_accept`, `dialog_dismiss`. (`dialog_appeared` is declared but has no producer.)
3. Each event carries `action`, `url`, `frame` (iframe chain), `tab`, `target` (element signals), `value`, `ts`. `frame_utils.py::_frame_context_and_offset_sync` accumulates page-level bounding-box offsets up the iframe chain.
4. Events stream through `session_events.py` into `events.jsonl`.
5. On stop, the context closes and each tab's `.webm` gets a stable name (§6.4). Frames are extracted at compile time (§7.1), so a bad frame can be repaired by recompiling.

**Elements a press changes or replaces (2026-11-16).** A click step describes its element as it was when the press began: `bridge.js` serializes the target on `pointerdown` (capture phase, before page handlers) and the `click` uses that description when it ends on the same node or on the node the page put in its place. Every serialized target carries `element_ref` (a per-document token for the DOM node, a `WeakMap`), and a focused text box typed into right after the clicked text box left the page carries `stands_in_for` (that box's token); `session.py` copies both into `extras`. They are recording-time facts, never selectors: `step_anchors.py::_same_node` uses them so a click and the typing that follows are one element even when the page swapped or rewrote it (no synthetic focus built from the later description), and `build.py::_element_changing_sequences` keeps the second opinion from archiving a press that changed the element a later step acts on. A click the browser makes because of a just-recorded Enter (implicit form submission, Enter on a focused button: `detail === 0`) is not recorded as a second step.

**Environment capture.** Once, after the start page loads, `_write_environment_sync` writes `environment.json` (locale, timezone, viewport, DPR, a formatted date sample, platform). See §9.7.

**File chooser.** `_on_file_chooser` suppresses the native picker so Studio shows its own dialog pointed at the download folder. `FileChooser.set_files()` still fires `change`, so `upload_intent` capture works.

**Native JS dialogs.** Registering a `dialog` listener stops Playwright's auto-dismiss, but Chromium still renders the dialog. `_on_dialog` holds it open and asks Studio (`js_dialog_request` → `RecordWorkflowDialog.tsx`, pinned `alwaysOnTop` → `cmd_resolve_js_dialog`). The pump loop (`_drain_js_dialog_sync`) resolves it with the human's real choice and prompt text, recorded as `dialog_accept`/`dialog_dismiss` with value `{type, message, value}`. It then calls `page.bring_to_front()` to repaint over stale dialog pixels on Windows. Unanswered after 120 s (`_JS_DIALOG_TIMEOUT_S`) it auto-accepts. `auth_mode` auto-accepts immediately. `beforeunload` is unhandled on record and replay (`TODO.md`).

### 6.2 History & Address-Bar Navigation

The bridge sees only in-page events, so browser-chrome navigation is detected through CDP. Each registered page gets its own CDP session (`context.new_cdp_session`) and a baseline `Page.getNavigationHistory` snapshot at registration (for `tab_0`, after the start `goto()`). Every main-frame `framenavigated` queues a check (`_queue_nav_history_check`). The pump loop drains it (`_drain_nav_history_checks_sync`), because CDP calls are unsafe inside Playwright callbacks; multiple navigations coalesce. Non-auth mode only.

Classification is strict (a missed event degrades gracefully; a false one corrupts replay):

| Observation | Event |
|---|---|
| `currentIndex` decreased | `browser_back` |
| Moved forward into a slot whose URL existed in the previous snapshot | `browser_forward` (URL equality keeps a truncating link click from reading as forward) |
| Otherwise, and `Page.frameRequestedNavigation` fired within 10 s (`_NAV_RENDERER_INITIATED_TTL_S`) **of the navigation's commit** | nothing: the page asked to navigate (link, submit, script), so the click/submit step reproduces it |
| Otherwise, but a same-document navigation (`Page.navigatedWithinDocument`: `pushState`/`replaceState`) happened, or a bridge interaction (click, submit, typing, ...) landed within 5 s before the commit | `manual_navigate` with `cause: "action"` (an effect of that action, kept as evidence; see below) |
| Otherwise, and this page's previous `manual_navigate` landed on this one's `from_url`, same site, within 5 s, with no interaction since | `manual_navigate` with `cause: "redirect"` (the site moved on by itself; the compiler drops it) |
| Otherwise | `manual_navigate` with `cause: "user"` (address bar, bookmark) |

Since 2026-11-04 every `manual_navigate` is tagged (`action` / `redirect` / `user`) and the compiler decides by the tag alone; the 5 s timing rule applies only to sessions recorded before the tag (untagged).

`frameRequestedNavigation` fires on request, not commit, so a cancelled request (`preventDefault()`) leaves a stale flag. The TTL stops it from swallowing the next genuine manual navigation. All three events carry `value = {from_url, to_url}` and the tab that fired them (`src_page`).

**Compiled as real steps:**
- `browser_back` / `browser_forward` → `intent=history_back|history_forward`, `no_recovery_block`, `wait_for: url_change` on the recorded URL. Replay calls `page.goBack()`/`goForward()` on the resolved tab (`handlers.js`), never a guessed URL. The CI gate skill covers this.
- `manual_navigate` → a plain `navigate` step (`build.py::_navigate_step`): `page.goto(to_url)`, no target, intent, or anchors. **Except** one an action caused (below).

**Event provenance (`RecordedEvent.origin`).** `bridge.js` stamps `origin: "script"` on any event whose browser event had `isTrusted === false` (page code calling `el.click()`, a dispatched event, a widget clicking its own button — e.g. Google's account button); absent = a person, and every recording made before the field existed. The stamp is set once, in the `onDoc` wrapper every listener goes through, and read by `serializeTarget`; `session.py::_finalize_payload_sync` copies it through its allow-list. `compiler/build.py::prepare_compile_events` drops `script` events first, before and independent of the setting below, recording each in `compile_report["dropped"]` with `pass: "not_from_user"` (never asked about as a lost typed value). Replay re-runs the page's own script by itself, so recording its effect too would double-fire it. A trusted browser-made click (a label forwarding to its input) is not covered. Known gap: no Human Edit restore for a dropped event yet (TODO.md BUILD-60).

**Drawing on a canvas (`unsupported_gesture`).** A press, drag or right-click whose target is a `<canvas>` (a drawing board, a canvas-drawn app such as Excalidraw) acts on pixels, not on an element a replay can find again. `bridge.js` records it as `unsupported_gesture` (value `{"kind": "canvas", "moved": bool}`), never as a click on whatever wraps the canvas; the canvas's own click and dblclick are not recorded. `prepare_compile_events` drops these after the provenance gate with `pass: "unsupported"`. Code, not the recap model, says so: a plain chat line (`clarify.unsupported_sentence`), the recap and summary lose the person's goal (the workflow does not do it), and an `unsupported` question (a safety-floor kind, one "I understand" choice) must be answered before test, approve or publish. A recording with nothing else is refused as empty with that reason (`handlers/session.py::_empty_reason`), so no compile is spent.

**Robot checks (`human_check`).** An action inside a reCAPTCHA (`/recaptcha/api2|enterprise/anchor|bframe`), hCaptcha (`hcaptcha.com`) or Cloudflare Turnstile (`challenges.cloudflare.com`) frame is never a step: `compiler/human_check.py::in_human_check` reads the event's frame chain, and `prepare_compile_events` drops it with `pass: "human_check"`. The person gets a code-written line (`clarify.human_check_sentence`) and a safety-floor `human_check` question: "Add a hand-over step" (code inserts a `handover` step after the step before the check, op `insert_handover`) or "Leave it out". The recap and summary lose the goal and may not say the workflow gets past the check (`llm/compile_recap.py::_problem`). At run time `runtime/app/human_check.js` ends the run as a fault (no recovery, no AI) before a step that acts inside such a frame, or any step while a challenge frame (reCAPTCHA bframe, hCaptcha challenge) is showing; a `handover` step is never stopped.

**Secrets in addresses and test inputs (`conxa_core/redact.py`, `runtime/app/redact.js`).** A form sent by GET puts its fields in the next page's address. Every address the recorder saves (events.jsonl, requests.json, recorder_diag.json) goes through `scrub_urls`: a query or fragment value is replaced by `REDACTED` when its name looks secret (`pw`, `password`, `token`, `secret`, `otp`…; the short `code`, `key`, `pin`, `sid`, `session`, `auth`, `sig` only as the whole name), or when its SHA-256 equals one of a value typed into a password-like box (`bridge.js` reports only the hash, through Studio's binding; `session.py` keeps it in memory). `prepare_compile_events` scrubs again by name, so an older recording compiles clean. The runtime scrubs every `runtime.log` and recovery-log line the same way, plus the values of secret-looking run inputs. A secret-looking test input is shown as a password box on the test card, never pre-filled, saved as a mask in the thread and in the workflow (`workflow_store._write_raw`; read back as absent), and kept only in memory for "test again" in the same Studio session.

**One recorded action = one step (`SKILL_COMPILE_KEEP_EVERY_ACTION`, default off since 2026-11-03).** "Action" means something the person did (see event provenance above). The setting is only the default for the per-pass switches in `conxa_core.config.Settings`; read a pass through `settings.compile_pass(name)`, where an explicit `SKILL_COMPILE_<NAME>` wins over the umbrella. Passes: `cleanup` (`clean_steps`/`fix_step_order`), `fold_navigations` (`fold_action_caused_navigations`; one switch, read by the compiler, the variant comparison, the understanding merge and the effect nesting so they cannot disagree), `collapse_choice`, `collapse_dates`, `flag_noise` (the second opinion's archive) and `upstream_dedupe` (the weak-hover filter and the focus/select/scroll dedupe in `pipeline/run.py`). **Decision-dialog forks are not one of them:** `compile_outcome_forks` defaults on and ignores the umbrella, because without it a recorded click on "Replace" would replay as a plain click and answer the popup for the person. The effect steps (download seen, new tab, dialog) stay separate and are not indented under their cause when `fold_navigations` is off. The recorder's 220 ms scroll debounce is a per-gesture capture rule and is unchanged.

**"Same site" has two named meanings (2026-11-05).** `conxa_core/site.py`: `same_host` (same machine name, `www.` ignored; used by the recorder's navigation tag and the compiler's redirect fold, deliberately not looser) and `site_of` (registrable domain from a vendored Public Suffix List snapshot, `conxa_core/_public_suffixes.py`, refreshed by `conxa-cloud/scripts/update_public_suffix.py`; used by `group_store.apps_for_workflow` / `unclaimed_hosts`, so `a.co.uk` and `b.co.uk` are different sites). The runtime's cookie-domain rule (`browser.js::_domainBelongsToHost`) answers a third question, which cookies belong with a host, and stays.

**One list of sign-in path words (2026-11-05).** `conxa_core/login_paths.py` and its twin `runtime/app/login_paths.js` hold two lists: `SIGN_IN_PAGE` (a page that is a sign-in page by name) and `SSO_HOP` (`/auth`, `/oauth2`, `/sso`, `/logout`, which are also ordinary in-app pages). Every caller picks the question it asks: the recorder drop (`LoginFlowTracker`), the session check (`auth_learning.looks_like_session_login`, `login_signals.looksLikeSessionLogin`), the live sign-in ladder, the sign-in capture check (`browser.js::_rejectReasonForProtectedUrl`) and a run's auth-failure check (`run.js::isAuthFailure`). In `isAuthFailure` a hop address counts only with a password box on the page or a sign-in title, so `/settings/auth` no longer reads as an expired session. `conxa-cloud/tests/test_login_paths_parity.py` checks the two lists hold the same words and `runtime/test/fixtures/login_path_cases.json` pins every use in both languages.

**Sign-in pages are kept out of a recording (`recorder/frame_utils.py::LoginFlowTracker`).** Signing in is never part of a workflow (the session is saved separately), so `session.py::_consume_payload_sync` drops events recorded on a sign-in page. Only a path that is a sign-in page by name (`login`, `log-in`, `signin`, `sign-in`, `sign_in`, `servicelogin`, `session-expired`) counts on its own; the single-sign-on hops (`auth`, `oauth`, `oauth2`, `sso`, `logout`) count only while a sign-in is under way (a strict match or a password-type input armed it; the next ordinary page clears it), so an in-app `/settings/auth` keeps its steps. `looks_like_login_page` stays wider and is used only where a wrong yes costs one extra sign-in (start rejection, session checks). Every drop is appended to `login_drops.json` beside `events.jsonl` (`{pass: "login_page", action}`, never a value, so a typed password cannot reach the report) and `compiler/build.py::_read_login_drops_sidecar` puts it in `compile_report["dropped"]`; Clarify never asks about it (`_MERGE_PASSES`). `runtime/test/fixtures/login_path_cases.json` is run by both languages.

**Frame bridge install policy.** `bridge.js` is injected by `add_init_script` and re-attaches its document listeners by itself on `document.open`/`write`; the pump's check in `session.py::_ensure_bridge_installed_in_frame_sync` is a backstop. Sync Playwright is bound to the thread that created it and has no per-call timeout, so the backstop makes fewer calls instead of bounding them: `bridge.js::announceReady` sends `{_bridge_ready: true}` over the `__skillReport` binding whenever it (re)installs, `_binding_sink_sync` records it per frame, and a frame that announced since its last navigation is left alone and re-checked only every `_BRIDGE_RECHECK_S` (15 s). A healthy check counts as an announcement; a failing one backs off 0.5 s, 1 s, 2 s, then every 4 s; a frame still inside `_BRIDGE_INSTALL_SETTLE_S` after attaching or navigating is never touched. Per-frame state is dropped on detach. The five slowest checks (over 250 ms) are written to `recorder_diag.json` as `slowest_frame_probes`.

**A navigation an action caused is never a step.** Click "Submit" and the app redirects to `/orders/847291?token=...`: replaying that URL would hardcode a per-run id/token and force the page somewhere instead of letting the app decide. `compiler/navigation_effects.py::fold_action_caused_navigations` runs right after `clean_steps`/`fix_step_order` (before `_insert_tab_markers`, so the user-opened-tab navigate and the start navigate are never candidates). It drops a `manual_navigate` that is tagged `cause: "action"` or `"redirect"` (a `"user"` tag is never dropped), or (untagged legacy sessions) that starts from the page of the previous same-tab interaction within 5 s, and records the destination on that interaction's `post_condition.url_delta`. `_build_assertions` then adds an **advisory** (`required=False`) `url_pattern` built by `generalize_url_pattern`: origin and literal path words kept, any segment with a digit or 16+ characters wildcarded, query and fragment dropped. The runtime never `goto`s it; it clicks, waits, and lets the app land where it lands. Genuine address-bar navigations, back/forward, popup and tab events are untouched.

**Two gates fail silently if a new navigation kind is missing:**
1. `ActionKind` (`conxa_core/models/events.py`) is a strict `Literal`. An unknown kind fails validation inside `_consume_payload_safe_sync`, lands in `binding_errors`, and never reaches `events.jsonl`.
2. `step_anchors.py::clean_steps` dedupes consecutive same-action, same-element events. Navigation kinds have empty element keys, so each must be exempted or A→B→C collapses into one step.

**Cause of a navigation (2026-11-15).** The recorder reads the history entry's `transitionType` (`Page.getNavigationHistory`). `typed`, `address_bar`, `auto_bookmark`, `generated` and `keyword` mean the person reached the address through the browser's own UI: tagged `cause: "user"`, `cause_source: "browser"`, even a second after a click. A click's redirect keeps the click's transition (`link`, `form_submit`), so it still folds into the click as its effect. Only when the browser gives no transition does the 5 s clock decide. `compiler/build.py::_is_failed_navigation` drops a typed navigation whose page is `chrome-error://...` (a misspelt host) from the compile and ledgers it as `failed_navigation`.

### 6.3 Iframe Chain

Every event's `frame` carries `src`, `frame_id`, and `parent_chain` (ordered parent frame ids). The chain is preserved verbatim through compile and execution. Bounding boxes are page-level.

### 6.4 Multi-Tab Recording

The context-level `page` listener instruments every tab. `_register_new_pages_sync` (one pump tick, ~0.2 s) assigns:
- `id`: `tab_0`, `tab_1`, … in discovery order;
- `opened_by`: `initial`, `site` (`page.opener()` resolves: link or `window.open`), or `user` (Ctrl+T; no `popup` event fires). The runtime waits for `site` tabs and creates `user` tabs (§9.2);
- `opener_tab` for `site` tabs.

Every event's `RecordedEvent.tab` (`TabContext`) names its tab; the compiler derives tab markers from it (§7.1) and the runtime resolves it per step (§9.2).

**Listener events are stamped with the firing tab.** `_attach_page_listeners(page)` binds `page` into the `download`/`dialog`/`popup` handlers and passes it as `src_page`, not whatever tab is active. Otherwise a popup from a background tab gets stamped `tab_0`, and `_insert_tab_markers` inserts a spurious `tab_switch`.

**A `popup` event names the popup's own tab**, resolved one tick late. `src_page` stays the opener (right for visuals and `page.url`), but the `tab` block must name the new tab, or no `tab_open` marker is derived and replay keeps driving the original tab from the background. Inside `_on_popup` no Playwright call is allowed and the popup isn't registered yet, so `_enqueue_synthetic(..., tab_key=id(popup))` stashes the key and `_consume_payload_sync` resolves it after `_register_new_pages_sync()`. A popup that closes before then falls back to the opener's tab.

**Video is per tab.** `tab_0` writes `recording.webm`; others write `recording-<tab_id>.webm`. `videos.json` maps `{tab_id: {file, start_wall_ms}}`, where `start_wall_ms` is the tab's own creation time, so an event's `visual.timestamp_ms` is relative to its tab's video. `frame_extractor.py` uses the map; sessions without it use `recording.webm`. A launched session gets the files from Playwright's `record_video_dir`; an embedded one from `ScreencastVideo` (§4.7a), one per tab, started when the tab registers (tab_0 before its first navigation) — its `start_wall_ms` is the video's own first frame time, which is more exact than registration time.

### 6.5 Post-Condition Evidence

The recorder classifies before/after evidence for the compiler's assertions (§9.5).

- **`post_condition`** (`bridge.js::finalizeStateWithAfter` → `buildPostCondition`): `classified_effect` (`navigation`/`dialog_opened`/`dialog_closed`/`expansion`/`value_set`/`content_change`/`none`), a redaction-safe `value_readback` (never for sensitive fields, per `isSensitiveEditable`), and `dialog_signal` (selector of the opened container). `StateChange.dom_diff` is carried through as the `content_change` signal. Both are optional on `RecordedEvent`.
- **A step is saved the moment the person acts; what the page did afterwards is amended in (BUILD-80).** Click listeners run in the capture phase, before the page's own handler, so a synchronous snapshot diffs the page against itself. `finalizeStateAfterSettle()` therefore reports the step at once (stamped `uid`, `bridge`, `seq`) and sends what it learns after a mutation-quiet window (`click_settle_quiet_ms`, 20 ms, capped at `click_settle_max_ms`, 250 ms; queued in submission order) as `{_amend: uid, state_change, post_condition, surface_context, date_field_after}`. The "did it finish" progress watch (`post_watch`, up to 15 s, Proof of Done) runs concurrently, outside that queue, and arrives as a second amendment; it starts only when progress is new since the act (a click made while an earlier upload's tray is already showing is not blamed for it). `RecordingSession._apply_amend_sync` merges an amendment into the saved step by `extras.uid`, before the pause and login drops; a step whose amendment never arrives is one with an unknown effect, never a missing step. **Stop is a barrier:** the shutdown drain calls `window.__skillFlush()` in every frame (open watches are cut, their `post_watch` marked `cut_by_stop` with no "done" words; pending typing and hover are sent) and keeps draining until every `seq` each bridge issued has arrived and no watch is open (`_STOP_BARRIER_MAX_S`, 10 s). `cmd_stop_recording` reads the events after the stop for that reason. **Nothing is lost silently:** `RecordingSession.capture_gaps()` (also written to `recorder_diag.json`) lists a missing step number per bridge, a number a bridge said it issued at Stop that never arrived, a step that failed to save, and (diagnostics only) trusted clicks that matched no control. The first three make `capture_gap_count()` non-zero and the Studio says so in plain words after the recording is saved (`workflow_flow.capture_gap_note`).
- **Hover capture is reveal-gated** (`hasMeaningfulHoverChange`) against `lastStableHoverSnapshot`, because CSS `:hover` applies before any JS runs. The baseline is taken at `DOMContentLoaded` and re-taken on a debounced `childList` MutationObserver, both guarded by `!pendingHover`, and never while parsing (a null baseline is safe; a blank one records noise). Hover signatures use scroll-independent `isRenderedElement`; `isVisibleElement` (rendered and on screen) decides what's worth recording. `childList`-only is deliberate: class/style reveals are attribute mutations.


---

### 6.6 Pause, Live Rows and Recording Hints (BUILD-38, 2026-10-18)

Recording from a workflow's conversation (`handlers/workflow_flow.py`) adds three things to the recorder and leaves `events.jsonl`'s shape alone.

- **Pause / resume.** `RecordingSession.pause()/resume()`. A payload is stamped `_paused` at *enqueue* time (`_binding_sink_sync`, `_enqueue_synthetic`) and dropped by `_consume_payload_sync` — stamping at drain time would replay whatever the queue was still holding when the person resumed. Nothing captured before the pause is touched; the video keeps running (frame extraction will cover the paused span). **Known limit:** the bridge keeps its per-field value baseline during a pause, so text typed into a field while paused is part of that field's value in the next `type` event after resume. Avoid editing a field you will type in again.
- **Live rows.** `RecordingSession.event_hook(event)` fires after each event is materialized; the flow handler turns it into a plain-language row (`recorder/plain.py::describe_event`) and broadcasts `{phase: "recording_event", session_id, row, count}`. Rows are not persisted into the thread (a card revision per action would bloat it); `recording_rows{session_id}` repaints the list after a reload. A hidden (password-like) field never shows its value and never offers the toggle.
- **“Changes each run?” hints.** `data/sessions/<session_id>/hints.json` → `{"varies": {"<sequence>": true|false}, "paused_spans": [...]}`. `run_pipeline` copies each answer onto its event as `extras.user_varies` (a no-op when there are no hints). `compiler/build.py::_derive_input_binding` honours only the `false` answer (“stays as recorded”: keep the literal, drop the input); `true` is what the compiler already does for a typed value. These are the author's meaning about a value, never a selector, so the primary-path selector invariant is untouched; with no hints a compile is byte-identical.
- **Closed browser.** `_flow_watch_recording` notices `browser_open == False` while the card is recording/paused and revises it to `browser_closed` with the count, offering “Save N actions” (the same finish command) or Discard.
- **Not changed:** recording still runs in its own Chromium window; BUILD-42 kept it there on purpose (§4.7a). Real sign-in and real recording were not exercised end-to-end against a live site; the flow is covered by unit and handler tests with fake sessions.

## 7. Compilation Pipeline

**Location:** `conxa-builder/python/conxa_compile/`

### 7.1 Pipeline Stages

```
recording*.webm + videos.json
   ▼ recorder/frame_extractor.py::extract_frames_for_session()   (in handlers/compile.py::cmd_compile)
   │   5 ffmpeg frames per event (before_far/near, at, after_near/far); idempotent,
   │   isolated per event (a failure only affects that step, which falls back to
   │   deterministic anchors); writes visual.frames / full_screenshot into events.jsonl
events.jsonl
   ▼ pipeline/normalize.py    canonicalize actions, filter noise, resolve frames
   ▼ pipeline/dedupe.py       consecutive duplicates, rapid clicks, select noise (§7.2)
   ▼ pipeline/enrich.py       DOM snapshot refs, surrounding text, visibility
   ▼ pipeline/selectors.py    raw selector candidates
   ▼ compiler/build.py::compile_skill_package()
      ├── clean_steps / fix_step_order, _insert_tab_markers
      ├── per step (heuristic intent from normalize_compiler_intent; no per-step LLM):
      │   ├── identity_bundle.py      IdentityBundle (deterministic, zero-LLM)   §10.2
      │   ├── anchor_vision_llm.py    relational anchor phrases (optional LLM)
      │   ├── validation_planner.py   wait_for + success_conditions
      │   ├── _build_assertions()     one required assertion per consequential action   §9.1
      │   ├── recovery_policy.py      RecoveryBlock
      │   └── confidence/layered.py   confidence score
      ├── _populate_hover_chains, collapse_choice_group_runs, collapse_date_picker_runs  §7.2
      ├── _build_image_by_step_key()  re-derives each step's chosen anchor frame (cache hit)
      ├── _build_compile_report()     status, per-step confidence
      ├── LLM: llm/workflow_review.py::build_workflow_review()   ONE multimodal call, last  §7.3
      └── → SkillPackage (models/skill_spec.py)
```

**Frame extraction runs at compile time**, so a recompile repairs only events still missing frames.

**Tab markers** (`_insert_tab_markers`, after `clean_steps`/`fix_step_order` so synthetic events don't have to satisfy their assumptions): wherever consecutive events' `tab.id` differs, a `tab_open` (first visit) or `tab_switch` (return, including to `tab_0`) marker precedes the first event on the new tab. Every step carries an explicit `SkillStep.tab` block (`_build_tab_context`), including `tab_0`; only pre-multi-tab recordings compile to an empty `tab`. Markers compile through `MARKER_ACTIONS` with `no_recovery_block`, like `frame_enter`/`frame_exit` (§10.7).

**Alignment rule (2026-11-05):** a pass that needs a step's recorded event finds it by `SkillStep.source_event` through `compiler/step_events.py::events_for_steps` (`_populate_hover_chains`, `convert_typed_date_steps`, `collapse_choice_group_runs`, `collapse_date_picker_runs`, and the upload binding, which falls back to the Nth upload event only for a step with no `source_event`). A step with no findable event reads as an empty event and passes through untouched, so the passes can run in any order and `collapse_choice_group_runs` returns only the steps. A step with no `source_event` is paired by position only when the step and event counts are equal.

### 7.2 Control-Specific Compilation

Each feature follows the same shape: the recorder captures context, the compiler derives `handler_hints.control_kind` plus a payload, and a pure runtime module (unit-tested without a browser) drives replay. Everything is Tier A (zero LLM). A skill compiled without `control_kind` is unaffected.

#### Date pickers (`control_kind = "date_picker"`)

Native `<input type=date|datetime-local|…>` already records a `date_pick` from `change`. Custom widgets (MUI, react-datepicker, flatpickr, Ant, jQuery UI) record as unrelated clicks with a frozen month-nav count and an ambiguous day text, and no recovery tier can fix that.

- **Record** (`bridge.js::buildDateContext`, on `click` and `select`): tags events inside a detected calendar grid with `date_context`: the role (`day`/`nav`/`time`/`year_select`/`month_select`), `grid`, the resolved ISO date, and the day cell's source attribute (`data-date`, `aria-label`, …) so a different date can be re-queried.
- **A grid is recognised three ways (2026-11-05):** `role=grid`/`application` with 20+ cells, a known library class (`CALENDAR_ROOT_SELECTORS`), or by shape (`bridge.js::_looksLikeMonthGrid`: 28+ distinct day numbers 1..31 among the leaves and a month-year header, tested only when the click started on a day number, and it never widens the root, since a bigger subtree would swallow the date field). The compiler's field-unchanged check (`date_picker.py::_left_field_unchanged`) still vetoes a click that did not change the field. A field-less booking calendar has the same shape and is still read as a picker.
- **Compile** (`compiler/date_picker.py::collapse_date_picker_runs`): a maximal run of steps sharing a `date_context.grid` becomes one `date_pick` step (two for a range: `role: range_start|range_end`). A run with no day cell (for example year/month selects only) is left uncollapsed rather than inventing a day. A day click counts as a pick by outcome: `bridge.js` reads the anchored field before (`field_display_value`) and after (`field_display_value_after`, set in `finalizeStateWithAfter`) the click, and a day whose field is unchanged is not a pick (`_left_field_unchanged`); a recording without the after-value, or an inline calendar with no field, is still judged by the widget shape.
  - `value`/`input_binding`: named by `input_binding.py`'s label→placeholder→aria_label ladder, falling back to `due_date`, `start_date`/`end_date`, or `due_datetime`. `reconcile_inputs_with_step_values` declares it `type: "date"` with the recorded date as default (the packaged manifest emits JSON-Schema `string` + `format: date|date-time`).
  - `handler_hints.date_picker`: open/grid/header/prev/next selectors, `year_select`/`month_select` selectors, day-cell attribute, `display_format` (best-effort guess from the field's readback), `kind` (single/range/datetime), and `strategy`: `typed_first` when `date_context.field` names an anchored field, `grid_only` for an inline calendar.
  - A preceding `click` or `focus` step whose selector matches `date_context.field` folds into the base step. (`focus` counts because `_normalize_prep_click_to_focus` already rewrote clicks on editable targets.)
  - No compile-time `value_equals` (display format is only knowable at replay). `grid_only` gets a `selector_present` built from the `{{binding}}` token when the cell has a machine attribute; otherwise none.
- **Typed text dates** (`compiler/date_format.py::detect_date_format` + `date_picker.py::convert_typed_date_steps`): a `type`/`fill` step whose recorded literal is a date (`25/09/2026`, `Sep 25, 2026`, `2026-09-25`) becomes a `date_pick` step with `handler_hints.date_picker = {kind: "text_field", display_format, recorded_value}` and a `{{binding}}` value, so the input stays a semantic `date` (default = the ISO recorded value) and the field's format is metadata. Day-vs-month order comes from a part > 12, else the field's placeholder tokens, else the recording browser's `environment.date_format_sample`; if none decides it, the step is left as plain text (never guessed). No `control_kind`, so no grid fallback; the literal `value_equals` is dropped.
- **Replay** (`runtime/app/date_picker.js` + `handlers.js`): the caller's value is read by `parseDateInput` (ISO, month-name forms, or numeric read through the step's own `display_format`) and rendered with `formatForDisplay` (tokens `YYYY YY MMMM MMM MM M DD D`); an unreadable value on a step that has a `display_format` fails as bad input instead of being typed in. A `text_field` step fills the formatted text (no Enter, which would submit the form), reads it back, and throws on mismatch. For other shapes: typed-first for any full ISO date (fill in the display format, verify readback via `_dateValueMatches`, throw on mismatch). Otherwise drive the grid: open, bounded month navigation that asserts the header changed on every click (so a min/max limit can't loop), `selectOption({label})` on year/month selects (by label, since option values are library-specific), a day click preferring the rebuilt machine attribute over exact day text (excluding disabled and adjacent-month cells), then an optional time click. Native `month`/`week`/`time` values aren't full ISO dates and fall through to plain fill.

#### Choice controls (`control_kind = "choice"`)

Radio/checkbox groups, native `<select>`, and ARIA radiogroup/listbox record their whole option set and compile to a multiple-choice input named after the **question**, not the recorded answer.

- **Record** (`bridge.js::buildChoiceContext`, skipped when `date_context` claimed the event): `kind`, `multi`, `group_key`, `group_label`, and every member's `{value, label, selector, checked}`. Needs ≥ 2 members (`_CHOICE_GROUP_MIN`). ARIA `role=radio|option` clicks are reclassified as `set_radio`/`select_option`. `_choiceOpenerSelector` records the control that opens a popup listbox (`[role=combobox][aria-controls=…]`, `[aria-haspopup=listbox]`, else the nearest ancestor with a stable id; rejects owners inside the popup) as `opener_selector`.
- **Compile** (`compiler/choice.py`): `derive_choice(ev)` for single-shot kinds; `collapse_choice_group_runs` merges a checkbox run into one `multi: true` step with `recorded_values` read from the last event's live snapshot (absolute state). `workflow_mutations.py::_choice_specs` declares `type: "select"` or `"multiselect"` with `options` and defaults. A real JSON-Schema `enum` reaches `inputs.json` and the MCP tool schema (`_normalize_saved_skill_inputs`, `server.js::_skillToolDefinitions`).
- **Replay** (`runtime/app/choice.js`): `matchOption`/`matchOptions` try exact, then normalized, then unique-prefix matching, stopping at the first rung with exactly one hit. Ambiguous or no match → `badInput` error listing valid options (skips recovery). No guessing and no falling back to the recorded answer. `handlers.js` acts on the matched option's selector (`selectOption({label})` for native select; direct click for ARIA options, after `_ensureChoiceMenuOpen` clicks the opener if the option isn't on the page). Checkbox groups read `inputs[input_binding]` as a list and set every member absolutely.

Related fixes that apply to all controls:
- `identity_bundle.py::_accessible_name` never uses `target.name` (the HTML group attribute).
- `action_semantics.py::is_editable_target` excludes `radio`/`checkbox`/`file`.
- `clean_steps`' prep-click merge covers every value-setting action (`_VALUE_SET_ACTIONS`).
- `pipeline/dedupe.py::collapse_select_interaction_noise` reduces `click`/`type`/`fill` events bracketing a `select` on the same element to just the `select`.
- `bridge.js::captureAssociatedLabel`'s nearest-sibling fallback skips text that is exactly a month+year (`_MONTH_YEAR_EXACT_RE`), so a calendar header can't become a field label.

Record-time fixes need a fresh recording; old sessions lack the context (Done.md REC-STALE-1).

#### Downloads and uploads

**Workspaces.**
- Recording saves downloads to `data/sessions/{session_id}/downloads/` (`_on_download`, collision-safe `_unique_download_name`). `cmd_start_recording` returns `downloads_dir`.
- Replay saves to `{CONXA_DATA_DIR}/runs/{runId}/`. `run.js::sweepOldRuns` runs at the start of every call and deletes other run dirs older than `CONXA_RUN_RETENTION_DAYS` (default 7), regardless of how they ended.
- A `.zip` is extracted immediately into a sibling folder on both sides (`extractZipOnce`; one wrapping top-level folder is unwrapped). Members are recorded as `zip_members`.

**Binding** (`compiler/upload_binding.py`, via `apply_bindings_to_compiled_steps` and `apply_bindings_to_export_steps`): browsers expose only `File.name`, so an upload binds to an **earlier** `download_observed` by filename, FIFO per name:

| Recorded pick | Bound value |
|---|---|
| The only download's file | `{{downloaded_file}}` |
| The Nth of several same-named downloads | `{{downloaded_file_N}}` |
| A zip itself | the zip, uploaded verbatim |
| One member of a zip | `{{downloaded_file_N_dir}}/name.pdf` |
| All remaining members of a zip | `{{downloaded_file_N_dir}}` |
| Some members of a zip | JSON path list `["{{…_dir}}/a.pdf", …]` (parsed by `resolveUploadPaths`) |
| Multi-select where every file matches unconsumed downloads | `{{downloaded_files_dir}}` (the run folder) |
| Anything partial or unmatched | untouched: `{{file_path}}` |

These placeholders are excluded from auto-declared inputs; `run.js`'s `download_observed` handler binds them at replay (a missing file throws `badInput`).

**Replay rules** (`handlers.js` `upload`):
- `downloaded_files_dir` is always set, so when the value equals it the handler checks that the folder exists and has at least one file, else throws `badInput`.
- After a successful `setInputFiles` from `downloaded_files_dir`, the consumed files are deleted best-effort, so a later hop doesn't re-upload them. A hand-authored directory is never touched.
- A plain `{{file_path}}` is passed through for `setInputFiles` to report not-found itself.

**Studio-owned file picker at record time.** `_on_file_chooser` suppresses Chromium's picker and emits `file_picker_request` (`id: null`). `RecordWorkflowDialog.tsx` opens Electron's `dialog.showOpenDialog` (All Files) at `default_dir`: the latest download's folder or the unwrapped zip folder (`_last_download_dir`/`_zip_extract_dir`). Downloads are fsync'd and `SHChangeNotify` is sent first so new files appear. `cmd_resolve_file_picker` → `resolve_file_pick()` queues the answer, and the pump loop calls `chooser.set_files(...)`. Cancel is a no-op.


### 7.3 LLM Usage & the Second Opinion

All compile-side LLM calls go through `conxa_core.llm.get_router()`. In Build Studio the router is `LLMProxyClient`, which forwards to the cloud's metered proxy (§13).

| Client | When | Cost |
|---|---|---|
| `anchor_vision_llm.py` | Per step, relational anchor phrases (optional) | Medium (screenshot) |
| `workflow_review.py` | Once per compile, last | Medium (screenshots) |
| `recovery_llm.py`, `semantic_llm.py` (`enrich_semantic`) | Human Edit 1-click fix only (`compiler/patch.py`), never on compile | Medium |
| `selector_regeneration.py` (`selector_generation`, text) | 1-click fix re-target only | — |

**Selectors are deterministic on the primary path.** `identity_bundle.py::generate_deterministic_signals()` reads the recorded DOM and emits Playwright-grammar signals ranked by durability. No LLM writes or scores selector strings (LLM-written selectors hallucinate ~30% of the time). `llm_selector_generator_v2.to_playwright_grammar()` survives only as a string formatter. Missing `input_type` is inferred by policy regex.

**Vision anchors.** `_downscale_and_encode` always re-encodes the screenshot as JPEG ≤ 1024 px on the long side, including when the bbox is missing; a raw frame is never sent. Recoverable failures (`_RECOVERABLE_VISION_ANCHOR_REASONS`) produce a per-step `vision_anchor_fallback` warning plus one aggregate `vision_anchor_fallback_summary` log event per compile.

**Workflow review** (`llm/workflow_review.py::build_workflow_review`): one multimodal call, after bindings are deduplicated and the first compile report exists. It sees every step's chosen anchor screenshot as `image_url` blocks keyed by `step_key`. Images are trimmed to a byte budget (`build.py::_trim_review_images`; highest-confidence steps drop first) and excluded from the cache key. It returns:

1. the **WorkflowIntentGraph** (goal, per-step `intent_token` and prose; parsed by `workflow_intent.py::_graph_from_raw`). `_apply_workflow_review_to_steps` always backfills `semantic_description` and fills `intent` only where the heuristic left it blank. There's no per-step LLM fallback;
2. the **second-opinion findings** (validated by `workflow_semantics.py::_validate_findings`), applied by `compiler/second_opinion.py::apply_second_opinion`.

**Output limits and long workflows.** The reply holds one entry per step (or row), so its output limit grows with the input: `client.py::_output_budget` gives `workflow_review` `4096 + 1000 × steps` and `timeline_understanding` `4096 + 1200 × rows` tokens, never below the `TASK_OUTPUT_BUDGET` value and never above `OUTPUT_CEILING` (128K, the largest reply our models give; it is only an upper bound, the provider bills what is written). A reply still stopped at the limit is tagged `_truncated` (`client.py::mark_if_cut_off`, set in the core client and the cloud router; `openapi_client._call_llm_or_none` turns it into `TRUNCATED_MARKER` in `error_detail`). Only then does `workflow_review.py::_review_in_windows` ask again in windows of `WINDOW_STEPS` (100): each carries its start, the total and the intents already written, only the first answers goal, understanding and loop wording, only the last answers the end state, and indexes are shifted back to whole-workflow positions. A cut-off window is halved, down to one step. Steps no call could review go to `error_detail` as `steps_not_reviewed: N`, which `build.py` records as `compile_report["degraded"]` and which stops the partial answer being cached or pinned. `timeline_understanding` keeps a 1000-row cap (`rows_omitted` reports what is dropped) and its one retry tells the model when its last reply was cut off.

| Finding | Effect |
|---|---|
| `rename_binding` | Sets `input_binding`, rewrites the matching `{{token}}` in `value` |
| `parameterize_literal` | Binds a literal, only on a step without a binding |
| `label_phase` | Sets `SkillStep.phase` |
| `suggest_optional` | Converts a step the recorder flagged stochastic (`optional_hint`) into a `try_dismiss` branch via `build_try_dismiss_from_hint`; clears the hint |
| `suggest_assertion` | Appends one `required=False` assertion of type `text_present`/`text_absent`/`url_changed`/`url_pattern`/`state_changed` only (never a selector-bearing type) |
| `flag_noise` | Removes a step the recorder observed to have no effect (`post_condition.classified_effect == "none"`), or a `navigate` identical to the previous navigate on the same tab (`duplicate_action`). Archived in full to `compile_report["archived_steps"]` (`{step_key, step, category, why}`), which never ships and has no restore command yet |

The pass **never** touches `target`, `identity_bundle`, `compiled_selectors`, `wait_for`/`success_conditions`, or the frame/tab chain.

**Applied, not suggested.** The reviewer sees finished work in Human Review, which is the gate; nothing marks a second-opinion change. Consequences, accepted knowingly:
- A compile is not byte-identical whether the pass ran or not. Only routes that apply nothing are; the content-hash cache keeps repeat compiles stable.
- `suggest_optional` still needs a recorder-observed state; confirmation happens in Human Review. `confirm_optional_interstitial` handles hints the pass left alone, and there's no un-confirm.

**Trust boundary.** Findings are keyed on `step_key` (`compiler/step_key.py`: `stable_hash` plus an occurrence ordinal; a hashed action+url fallback for element-less steps), never `step_index`. Validation silently drops unknown keys or kinds, invalid or colliding binding names, `suggest_optional` without `optional_hint`, invalid phases, and anything beyond ~1 finding per 2 steps. `apply_second_opinion` adds shape guards (for example a rename on a step with no binding). An empty list is a normal outcome.

**Proof picker (`compiler/proof_picker.py`, after the second opinion).** For each step that already has a
`validation.effect`, a decision model (`POST /llm/proxy/decide`, Backend-Schema.md) is asked one yes/no question per
probe type in `EFFECT_PROBES` and returns a probability for each. The top 5 at or above `MIN_P` (0.6) are *picks*. A
pick becomes a check only through that probe's gate in `compiler/proof_gates.py`, which builds it from evidence the
recording left on the compiled steps or returns None (never weaker evidence); the model only adds, never removes a
code-built check, and never touches the failure-signal veto. It sees value-free facts only (`app_graph/scrub.py`;
inputs by name). The four gates: `url_stays` (an advisory landed-address assertion whose template differs from the
address the step began on), `row_with_identity` (a save or send after a value was typed on the same page),
`value_equals` (a save after such a value; rung `completed`, never `result_exists`, so a form that restores a draft
cannot meet the bar the recording's absent results raise) and `later_step` (a later step consumes a file this step
produces). All four are in `_UNTESTED_PROBES`: they never raise `required_rung` until a clean test run saw them
answer yes, and a `later_step` yes never teaches the checklist (`calibrate_effect`). Every pick and its fate
(`built`, `already_built`, `gate_rejected`, `no_gate`) is written to `proof_picks.json` beside the session. One
failure (no key, offline, quota) ends the pass and leaves every step as code built it. Cached by the step facts
(`llm_cache`, namespace `proof_picks`).

**Runtime side of the four (`runtime/app/result_probes.js`, `run.js`).** Each answers yes / no / cant_tell and none
ever answers `not_yet` (that would turn "couldn't confirm", which stops the run, into "still working", which goes on).
`url_stays`: the address matches the landed pattern, was not matching before the action (`evidence.js::urlBefore`) and
still matches 600 ms later. `row_with_identity`: the run's value (4+ characters) appears on the page more often than
before the action (the notebook counts it like an upload's file name). `value_equals`: a fresh tab re-reads the earlier
field through that step's own identity bundle (`resolution.js::resolveStep`; never a password field) and finds the
value. `later_step` is settled after the plan ends (`run.js::resolveLaterProofs`): only a step that could not be
confirmed and may safely be repeated is upgraded, and only when the named later step really ran (not skipped).

**Effect judgement (W10, 2026-11-18).** The same call returns `effects`: one entry per step that can change data (click, select, checkbox, radio, upload, date, dialog answer) with `reversible`, `kind`, `confidence` and a plain `reason`, judged from what the step means in the workflow (summary, what the video saw, field label, dialog words, what follows), never from one word on a button. `workflow_semantics.py::_validate_effects` keeps one answer per known step; `second_opinion.py::apply_effect_judgements` writes it as `signals.semantic.effect_judgement` and never touches a target, selector, wait or address. `action_semantics.py::judged_irreversible` is the only reader: not reversible, or confidence under 0.6, counts as cannot be undone; a stored `person` answer beats the model's. `commit_intent_hit`, `step_has_destructive_intent` and `classify_consequence` return it for a judged step; the older word rules remain only for steps no one judged (packages compiled before this).

**Failure and cost.** `SKILL_LLM_SEMANTIC_SUGGESTIONS_ENABLED` (default on) gates findings; the intent graph always generates. `llm/llm_cache.py` caches by content hash of the payload, URLs, and sibling bindings; failed or empty responses aren't cached. Any exception degrades to `(WorkflowIntentGraph(), [])`. The backfill runs outside the findings `try`, so disabled and failed stay identical. `compile_report["second_opinion"]` records what was applied (only when something was), and the report is rebuilt whenever anything was applied, archived, or backfilled.

**Reviewer edit log** (`editor/edit_log.py`): every mutating Human Edit command (hooked once in `backend.py::Backend.dispatch`, so undo/redo are covered) appends one line per changed field to `data/skills/{skill_id}/edits.jsonl`. Keyed on `step_key`, allow-listed fields only (`input_binding`, `value`, `intent`, `semantic_description`, `action.action`, `optional_hint`, `branch`, `validation.assertions`); failure-silent. `conxa-cloud/scripts/eval_suggestions.py` reports `override_rate` (edits on fields the pass wrote), `miss_rate`, and the copilot's accept and verified-fix rates.

### 7.3a Model Ledger (Engine v2 C1, 2026-11-06)

Every guard that holds a model's answer to what code can act on counts what it drops, in the one shape the
understanding check already used: `{stage: {rule: {count, example}}}` over the five stages `shape`, `reference`,
`override`, `backfill`, `safety`. The counter is `conxa_compile/llm/model_ledger.py` (`log`, `count`, `summary`, `flat`,
`entry`, `append`); `timeline_understanding.py` re-imports it, so its `validation_report` is unchanged. The guard still
decides what it keeps, and `ref` in a count is always an id (a step key, a question id), never model or page text.

| Call | Where it counts | Rules |
|---|---|---|
| Second opinion | `llm/workflow_semantics.py::_validate_findings` (per gate), `llm/workflow_review.py` (unreadable reply) | `unknown_step_key`, `unknown_kind`, `settled_literal`, `bad_placeholder`, `name_collision`, `unobserved_optional`, `bad_phase`, `ungrounded_assertion`, `unobserved_noise`, `over_cap`, `malformed_item`, `unreadable_reply` |
| Question wording | `conversation/clarify.py::apply_reworded` | `reword_rejected`, `unknown_question`, `malformed_reply` |
| Chat intent router | `conversation/intent_router.py::route_intent` | `action_not_allowed`, `unknown_workflow`, `malformed_reply` |
| Chat change cards | `editor/copilot_proposals.py::gate_all` (one count per raw kind, not per gate: the three gates see the same list) | `gate_refused`, `malformed_item` |
| Agent span (Studio test) | `handlers/workflows.py::check_span_answer` | `malformed_reply`, `action_not_allowed`, `bad_candidate_index`, `unknown_candidate` |
| Tier B | Runtime, unchanged: `override_rejected` with a reason goes to the tracker (`failure_response.js`) | not mapped to these names yet (the cloud summary in C5 would do it) |

Each call appends one line to `model_ledger.jsonl` in the recording's session folder (a chat-level call: the
workflow's folder). The second opinion also writes `compile_report["model_ledger"]`, and only when something was
dropped, so a clean compile stays byte-identical. `compile_health.model_ledger` (`editor/workflow_dto.py`) merges both
for the Diagnostics panel, and the post-compile chat message says how many suggestions the checks turned down.
`SKILL_MODEL_LEDGER=off` stops the file and the report copy. It counts only: no guard changes what it keeps.
Not hooked into `call_llm`: reference and override checks need each caller's context.

### 7.4 Human Review Copilot

"Conxa Copilot" is a chat panel in Human Edit (`components/copilot/CopilotPanel.tsx`, `CopilotLauncher.tsx`; floating, not in the Tools rail). It diagnoses the latest test failure and proposes edits as accept/reject diffs. **The copilot proposes, the reviewer disposes.** It's a user-initiated editor action and never writes an element address.

#### Evidence bundle

`editor/evidence.py::build_evidence_bundle`, keyed on `step_key`:
- Runtime failure capture: `runs/{run_id}/_evidence/{evidence.json, failure.jpg, pre_step.jpg?, overlays.jsonl}` in Studio's sandbox `data/`.
- The compiled step's `identity_bundle`, `compiled_selectors`, `validation.assertions`, `target`, `intent`, `semantic_description`, `phase`, `consequence`.
- Compile-report confidence/warnings, `second_opinion`, `archived_steps`, or `{"available": false, "reason": "workflow edited since last compile"}` when `_invalidate_compile_report` has staled it.
- The recorded event's `post_condition.classified_effect` / `state_change.dom_diff` (`source_event.py::find_source_event`), deterministic prose (`describe.py::describe_step`), and prior edits (`edit_log.read_edits`).
- `last_test` (`{status, error, at}` from the `Workflow` record) and `runtime_evidence_absent` (a reason when evidence is empty). The prompt then treats `last_test.error` as authoritative instead of inferring a cause.

`Workflow.last_test_run_id` (set by `cmd_test_workflow` whatever the outcome) is the fallback run id.

**Failed-step resolution.** `failed_step_key = keys[failed_at]` uses a position frozen at failure against the current document. `evidence.json` also stores `failed_stable_hash`, and `_resolve_failed_step_key` nulls the key (and explains in `runtime_evidence_absent`) when the resolved key's base hash differs. With no recorded hash (older evidence, multi-skill sequences, marker steps) the position is trusted. Residual gaps: a reorder of two steps sharing a `stable_hash`, and `observed_overlays` resolution (no hash to check). The full fix is persisting `step_key` into `execution.json`.

**Runtime capture.** `failure_response.js::_writeStudioEvidence` runs on Studio failures (`!agentRecoveryEnabled`, since Studio forces tier 2). It writes `evidence.json` (element inventory, overlay probe, a slug + `ts`-filtered recovery-log tail, `failed_stable_hash`, `action_may_have_taken_effect`, `recovery_halt_reason`, `destructive_halt`, `verify_fail`, plus `failure_kind` — `ambiguous | not_found | entity_not_found | frame_not_found | verify_fail | other` — `input_refs` — `[{id, empty}]` for every `{{input}}` the failing step reads, **never the values**, since this file is read back into an LLM prompt and an input may be a password — and `signal_counts`, the matches each signal found) and a live `failure.jpg`. **Blame the input first:** when `failure_kind` is a find-failure (`ambiguous`/`not_found`/`entity_not_found`) and the failing step reads an input whose tested value differs from the recorded one, is empty, or breaks its own definition (`options`/`pattern`), `editor/input_suspects.py::find_input_suspects` flags it and `test_flow.py::_tf_input_check` asks the person ("that value was wrong — test again" / "the value is right — fix the workflow", question kind `input_check`) *before* Studio is allowed to propose a fix for any step. Evidence from a runtime that predates these keys means "unknown" and leaves the find-the-cause step exactly as it was. It never throws; `sweepOldRuns` reaps it.

#### LLM tasks and routing

- `copilot_reply`: streamed prose the reviewer reads live (plain text, no JSON mode).
- `copilot_diagnose`: strict JSON `{reply, proposals, need?}` for proposals. Its `reply` is a fallback when streaming produced nothing. The system prompt forbids `target`, `identity_bundle`, `compiled_selectors`, `primary_selector`, `fallback_selectors`.
- Both use `usage_class="ai_credits"` (the renamed `human_edit`, CLOUD-25). Image-then-text content when a failure screenshot exists; plain text otherwise.
- **Modality is payload-conditional** (`client.py::_copilot_modality`): no `image_base64` → `route_text`; with one → `route_vision` on the entry's `multimodal_model` slot (own cooldown, falls back to `vision_model`). In dev (`CONXA_ENV` unset or `dev`) it always picks `text_model` but still sends the screenshot, so point dev's text model at a multimodal model.
- **Stateless backend:** `copilot.py::copilot_turn` flattens evidence + transcript + latest message into one `user_text` each turn; `copilotStore.ts` holds the conversation. Editing an earlier message (`editFromIndex`) truncates the transcript client-side.

**Streaming path:** `stream_llm` → `LLMProxyClient.route_*(on_delta)` → `POST /api/v1/llm/proxy/{text,vision}/stream` → `LLMRouter._call_provider(on_delta)` with `"stream": true` and `_iter_sse_text_deltas`. The cloud frames `data: {"delta": …}` and a final `data: {"done": true, "text": …}`. Metering happens once after completion; a metering error after headers are sent is only logged. Failover works between attempts; a mid-stream failure keeps partial text. BYOK returns one immediate chunk. `cmd_copilot_turn` relays `{"phase": "copilot_delta", "text"}` on the generic event channel. The panel shows a thinking indicator from send until the first delta (no "call started" event exists).

**Reasoning-only streams.** A reasoning model can spend the whole budget on hidden reasoning and emit no content. `_iter_sse_text_deltas` tracks `reasoning_chars` and `finish_reason`. Empty text with reasoning or `finish_reason == "length"` raises `_DeterministicRejection` (one attempt, no cooldown), reported as `llm_reasoning_only_no_content`. OpenRouter endpoints get `reasoning: {"enabled": false}` for every task except `execute_chat`. There, `_iter_sse_deltas` yields `{"type":"reasoning"}` chunks that Execute renders live as "Thinking…" (`execute_client.js` `onDelta` → `chat:delta` keyed by `requestId` → `App.tsx`).

#### Proposals

Three kinds, all gated before display and accepted through existing editor RPCs (same undo, `patch_gate`, and `edits.jsonl` path as a manual edit):

1. **Field patches** `{step_key, field, patch, why}` (`copilot_proposals.py::gate_proposals`) run through `patch_gate.py::validate_editor_patch`. Editable fields come from `capability_manifest.py::editable_fields(kind)`: the per-kind key set the patch gate accepts (`*_ALLOWED_KEYS`) minus `ADDRESS_KEYS` (`target`, `identity_bundle`, `compiled_selectors`, `check_selector`, `signals`, `frame`, `action`, `recovery`, `branch`, `for_each`, `url`), plus the dotted meaning fields (`validation.assertions`, `branch.timeout_ms`, `for_each.*`, `consequence`, `entity_binding.confirmed`, `handler_hints.hover_chain`). `capability_manifest.py::chat_may_write(kind, patch)` is the one allow-list check: every leaf a chat patch writes must be one of those fields, so an address under any spelling or nesting (`URL`, `action.url`, `signals.context.page_url`, `start_url`) fails. It runs when a card is made and again at apply (`handlers/copilot.py::_refuse_address_edit`, also on `insert_step` / `body_insert_step` fields), where a refusal is the plain `ADDRESS_REFUSAL` message pointing to Human Edit; a new step whose fields ask for an address gets no card at all. A page address changes through chat only when `parameterize` turns part of it into an input: `_keep_address_keys` puts any other `url` change back (`_templated_from`), pairing an element-less step whose key moved with its address to the step at the same place (WF-12). `parameterize` on text a person typed becomes a `value` field edit (`_typed_value_edits`). So `run_if`, `wait_until_*`, `check_*`, `extract`, `call_workflow_*`, `agent_span_*`, `handover_*` are editable; `value` is not for `drag_drop` (CSS) or an element `wait_until` (selector). The manifest's `patchable_fields` is the same function, so the model's list and the gate cannot drift. An optional `path` (`branch.steps[N]` / `for_each.steps[N]`) addresses a body step, parsed by `step_path.py` exactly as `cmd_patch_step` does. Accept re-resolves `step_key` (`resolve_step_index`, else `proposal_stale`) and calls `cmd_patch_step`.
2. **Overlay branches** `{overlay_id, control_index, primitive: try_dismiss|if_present, after_step_key, why}` (`gate_overlay_proposals`). The model picks from captured overlays and Python materializes selectors:
   - *Capture:* `cascade.js`'s `dismiss-overlay` remedy (on an `INTERCEPTED` classification) probes via `page_scripts.js::overlayProbe` (controls carry the resolver's six fields via a duplicated `controlDescriptor`; the container gets a `signal` from the id > role > css-path ladder). `overlay_capture.js::recordOverlayObservation` writes `overlays.jsonl`, deduped per run, whether or not the step passes. This is the only overlay capture on passing runs. `runId` is threaded explicitly for concurrency.
   - *Reading:* `evidence.py::_read_runtime_evidence` reads `evidence.json` and `overlays.jsonl` independently and exposes `observed_overlays`.
   - *Synthesis:* `editor/overlay_identity.py::bundle_from_descriptor` (pure) builds testid/css-id/role+name/text candidates with the compiler's own durability table, grammar, and filters. Signals get `source="runtime"`, `unique_at_compile=False`, and a Python-computed `stable_hash` (the resolver uses it only as a tie-breaker).
   - *Gating:* unknown `overlay_id` is dropped. `try_dismiss` uses `build_try_dismiss_from_hint` (its third caller). `if_present` targets the container signal with one nested click; with no container signal it downgrades to `try_dismiss` (an empty target makes `_saved_branch_step` silently drop the step).
   - *Accept:* `cmd_insert_step` + `cmd_patch_step` (+ `cmd_insert_branch_step` and a nested `cmd_patch_step` at `branch.steps[0]`) in one dispatch. Undo takes several presses. Rejections log against `overlay:{overlay_id}`.
3. **Structural ops** `{op, why, evidence_refs, …}` with `op` ∈ `insert_step`/`delete_step`/`move_step`/`update_inputs`/`replace_literals`/`parameterize`/`rename_workflow`, plus the step-scoped ops `body_insert_step`/`body_delete_step`/`body_move_step` (the body of the named if-present or for_each step, through `insert_branch_step` etc. with a `container`), `set_popup_decision`/`flatten_popup` (through `set_fork_policy`/`flatten_fork_to_recorded_option`; the card wording and button label come from the fork), `set_date_offset`, the table-loop ops `set_loop_table`/`make_table_loop` (§11.2) (a request to point a step at a different element comes back as a top-level `needs_editor` hint and the conversation posts an `action_link` card that opens Human Edit on that step, `?step=N`) (`gate_structural_proposals`, `_gate_step_scoped_op`). An `insert_step` of `agent_span` is dropped without a non-empty goal (`fields.value`); the digest rows carry `run_if`, the agent goal, fork states/options and branch/loop bodies so the model can name them. `evidence_refs` is required. Every op is dry-run in the gate on a copy (`update_inputs` through `merge_skill_inputs`, `replace_literals` must match something, `parameterize` through `parameterize_click_text`/`parameterize_literal`), so a card that could only fail at Apply is never shown. `parameterize {literal, name}` is how chat makes a recorded value a run input: the model names the value and the input id, code rewrites steps, checks and the declared input exactly as the understanding check's "varies" answer does (applied through `cmd_parameterize_literal` when the person presses Apply). An `insert_step` of a `SELECTOR_ACTIONS` kind must name `identity_from_step_key` (an existing step whose `identity_bundle`/`target` Python copies verbatim); other kinds must not. One chat reply posts ONE `pending_change` card carrying every gated proposal (`proposals`, plus code-written `summaries` from `copilot_proposals.proposal_summary`); Apply runs them through `CopilotMixin._apply_change_set`, all-or-nothing with one undo entry across kinds. The proposal call runs before the reply streams, and the reply is given the offered summaries (or "none could be prepared"), so the prose cannot promise a change no card carries. Accept (`_accept_structural_proposal`) is all-or-nothing: snapshot, run each op through its `cmd_*`, restore and drop undo entries on failure, collapse to one undo entry on success.

**Decision log:** `edit_log.append_edit` records `source: "copilot"` and `proposal_id` on accept. `append_decision` records rejections (`decision: "rejected"`). `append_verification` records verify verdicts. All go to the same `edits.jsonl`.

#### Context, retrieval, and sessions

- **Capability manifest** (`editor/capability_manifest.py`): generated from the same `*_ALLOWED_KEYS` constants `validate_editor_patch` reads plus `action_registry.py`'s sets, so it can't drift. Hand-written `RUNTIME_NOTES` per kind. Sent once per session.
- **Digest** (`build_evidence_bundle(detail="digest")`): one compact triage row per step, no heavy fields, plus `inputs` and `prior_decisions` (last 30 accepts/rejects) so rejected ideas aren't re-proposed. Other callers use `detail="full"`.
- **Retrieval loop** (`editor/copilot_retrieval.py`): `copilot_diagnose` may return `need: [{tool, args}]` (`expand_step`, `get_step_screenshot`, `get_failure_evidence`, `get_edit_history`, `list_workflows`). Results are appended as a `Fetched:` block for up to `MAX_RETRIEVAL_ROUNDS` (2) extra calls. There are no provider tool calls, since the pool's support is uneven. Screenshots are resolved to file paths and re-encoded server-side.
- **Sessions:** "New session" archives the transcript to `skills/{skill_id}/copilot_sessions.jsonl` (`copilot_save_session`; an undecided pending proposal is dropped unlogged). `cmd_copilot_load_session` resumes the last one when a skill's panel first opens (`copilotStore.ts::ensureFor`).

#### Verified retest

`cmd_copilot_verify` is two-phase:
1. Without confirmation it counts steps with `consequence == "irreversible"` and returns `confirm_required`. This count is narrower than the runtime's `isNonIdempotent`, so the copy says "N steps that commit or destroy data" and always warns that the run hits the real system.
2. Confirmed, it runs `cmd_build_skill_package` then `cmd_test_workflow` (accepting always makes the workflow stale) and compares the new run's `failed_step_key` against the step under verification: `fixed`, `still_failing`, or `progressed`. A cancelled run writes no verdict.

`cmd_test_workflow` returns `run_id`, and `_CommandError("workflow_test_failed")` carries it.

### 7.4a Conversation Layer (BUILD-37, 2026-10-18)

The conversation-led Build Studio (plan: [`Build-Studio-Conversation-Plan.md`](Build-Studio-Conversation-Plan.md)) puts one continuous thread behind every workflow and a general Chat beside them. It lives in `conxa-builder/python/conxa_compile/conversation/`; the Human Review copilot of §7.4 becomes this same conversation (its gates, retrieval loop and `copilot_diagnose` task are reused unchanged).

- **Lifecycle (`lifecycle.py`).** `describe → configure → record → compile → clarify → edit → test → approve`, **derived** from data that already exists (`Workflow`, `compile_report`, `clarify.json`, `signed_off`) plus in-memory flags (`runtime_state.py`) for what is happening right now (recording, compiling, testing). Nothing is a second source of truth and no model ever sets it: it changes only inside command handlers. `derive_lifecycle` is pure; `lifecycle_for_workflow` does the reads.
- **Thread store (`thread_store.py`).** Append-only JSONL: `data/workflows/<workflow_id>/thread.jsonl` (beside the workflow, not the skill — `skill_id` is born at compile and changes on re-record) and `data/chats/<chat_id>.jsonl` plus `chats/index.json`. Kinds: `user_message`, `assistant_message`, `event`, `card`, `summary`, `import_marker`, `new_conversation`, `retract`. A card is *revised* by appending a line with the same `card_id` and a higher `revision`; reads fold to the latest at the first position, so history is never rewritten. Fail-silent like `copilot_sessions.py`. The old per-skill `copilot_sessions.jsonl` is imported read-only, once per skill. **One item per moment (2026-10-25):** a card that already carries a moment's final state (recording saved/discarded, compiled, test failed, find-the-cause stopped, approved, change applied/undone) no longer also writes an `event`; events remain only for moments with no card (workflow created, recording started, test started/passed). `thread_store.compact()` runs inside `load()` and rewrites an older thread once (original kept as `thread.jsonl.bak`) — it drops those duplicate events and backfills `summary` on failed `test_run` cards; it touches only threads that need it, so it is idempotent without a marker. A failed `test_run` card carries `summary` (one plain sentence from `conversation/failure_summary.py::summarize`) next to the raw `error`, which the renderer shows only behind "Technical details".
- **Harness (`harness.py`).** One turn loop for Chat and workflow threads. (1) Deterministic routing first: a click on a card, or free text mapped to one of the card's allowed choices (exact label match, else one short `chat_map_answer` call; an id not on the card means "unclear" and nothing is applied) goes through the same executor and never reaches the conversational model. **New conversation / Edit and resend (WF-12 12.7, 2026-11-20):** a `new_conversation` divider (`thread_new_conversation`, refused as a no-op when nothing was said since the last one) makes the model's turns and summary start after it (`thread_store.conversation_start`); nothing is removed, and open cards above it still ride in every context pack and stay answerable. `thread_turn{replaces}` replaces the person's last message and Conxa's replies to it with one `retract{ids}` line (`fold` leaves them out, the file keeps them) and answers the new text; `thread_store.editable_message` allows it only while nothing but plain replies or a summary followed the message on the raw lines (no card, card revision, divider, event, hidden message, or reply to a command Conxa acted on, marked `routed`), so nothing can be orphaned. (2) Context under a budget (`context_pack.py`, 12k tokens for a workflow turn, 6k for chat; priority: lifecycle and open cards, **pinned facts**, the person's message, workflow digest — shrunk before dropped — failure evidence, rolling summary, recent turns oldest-trimmed-first). (3) The reply streams (`studio_chat_reply`, `thread_delta` events). When the message could ask for a change, the existing `copilot_diagnose` call runs (with the bounded `need[]` loop) and **every proposal passes `gate_proposals` / `gate_overlay_proposals` / `gate_structural_proposals`** before it can become a pending-change card; Apply goes through `cmd_accept_copilot_proposal`, Undo through `cmd_undo_workflow` (refused with `changed_since` if the skill's `meta.version` moved). Chat can also draft a workflow (`studio_chat_actions`); nothing is created until the draft card is confirmed. (4) Old turns fold into a stored `summary` item (`thread_summarize`).
- **Concurrency.** Turns are serialized per thread (a second message queues and emits `thread_turn: queued`); `thread_cancel` stops the stream and appends nothing half-finished. The cloud proxy is stateless (`ProxyBody` has no `messages`), so the context is rebuilt every turn.
- **Scoped LLM router.** Every RPC runs on its own thread, so one process-global router let a compile and a chat turn overwrite each other's `usage_class` and event sink. `conxa_core.llm.set_scoped_router` / `llm_scope` bind a router to the calling context; `get_router()` prefers it and falls back to the global (worker threads a handler spawns). `Backend._install_proxy_router` sets both, scoping only on worker threads.
- **Metering.** All conversation calls use `usage_class="ai_credits"` (CLOUD-25; `human_edit` is its permanent legacy alias). Entitlement / quota / unreachable errors become `error` cards with Try again.
- **Approval.** `Workflow.signed_off` is the only approval truth: any later skill write clears it (`invalidate_workflow_test_by_skill`), and the build gate checks it instead of `edited_at`, which moves on every write including a fresh compile. A *test* build (`cmd_build_skill_package{for_test:true}`, used by the copilot's verify and, later, the conversation's "Test it") skips the gate: a workflow must be testable before it can be approved.
- **Compile, Clarify and the summary (BUILD-39 / BUILD-32).** Finishing a recording starts a compile in the background (`handlers/compile_flow.py`). It waits its turn in a single-slot queue (`compile_queue.py`, a real position, no fake bar) and mirrors the compiler's *own* events onto one `compile` card (`compile_progress.py`): four honest rows — cleaned up the recording, found each element (n of m), worked out what each step is for and which values change (the second opinion is one pass, so it is one row), saved. `cmd_compile` is wrapped, not changed; a request-id observer (`protocol.JOB_OBSERVERS`) feeds the tracker from both the command's sink and the compiler's sub-step sink. Failure modes become cards: queued, out of credits (recording kept, Compile later), failed (says "no credit was used" only for an infrastructure failure the compile handler actually refunded). After a successful compile `clarify.generate_questions` finds, in the *compiled* skill document, the places only the recorder's author knows — **file uploads** (the recorded path won't exist on a customer's machine; the safe "ask each run" is the default), **steps that can't be undone** (confirm first?), **typed values** (fixed or input; skipped for values already marked while recording), **pop-ups** the recorder flagged (`optional_hint`), and **how we know it worked** (only existing text/URL/state assertions the compiler already wrote; ticking makes one `required`). At most five, one at a time: a skipped question keeps the compiler's default, but while any asked question is open `test` is out of `allowed_actions` (questions, then answers, then the test; held-back `queued` questions never wait for a test). Questions live in `clarify.json`, **not** in the skill, so a compile nobody answers is byte-identical. `plan_answer` turns an answer into ops for existing commands only — gated `cmd_patch_step`, `cmd_insert_step` (a confirm-only `agent_span` question), `cmd_confirm_optional_interstitial` (the shared `build_try_dismiss_from_hint`), `cmd_update_workflow_inputs` — each edit-logged with `source="clarify"`, and a card shows "applied" only after those commands returned. If the workflow changed in Human Edit meanwhile the answer fails cleanly (`proposal_stale`). A typed reply to a tick-list question never answers it (it would read as "untick all"). The summary card's counts add up: steps compiled + steps added by answers = steps now.
- **Question wording is model-polished, never model-decided (`llm/clarify_question_copy.py`, task `clarify_question_copy`).** After `generate_questions`, and after the first question is already on screen, one batched, cached call (run in the background by `_flow_reword`, so no question waits on a model; it revises the open card in place and leaves any answered question alone) rewrites each question's title, body and (single-choice only) option label/detail into plain language; `clarify.apply_reworded` validates it field by field and keeps the template text for anything that fails (empty, over length, contains `{{`, or drops a “quoted” item). Ids, choice count/order and what each choice does stay code's; multi-choice labels, fork `option:` buttons and `dropped_value` (raw typed values) are never sent or changed. Any failure leaves the templates.
- **The post-compile "workflow ready" message is model-phrased over code-built facts (`llm/compile_recap.py`, task `compile_recap`).** `handlers/compile_flow.py::_flow_after_compile` builds the facts (step count, goal, input names, kinds and counts of dropped actions, uncertain step names, assumptions, coverage gap, open question count) — never selectors, targets or page text — and one cached call phrases them. `write_recap` rejects an answer that is too short/long, uses ids or backticks, omits the step count or drops an uncertain step name, and re-asks up to 3 times telling the model why; if all fail there is no stand-in text — a warning line ("Couldn’t write the summary of what was built") is posted and the questions and summary card still follow. It runs synchronously before `understanding` clears, because assistant messages are append-only.
- **"How do we know it worked?" (`clarify.py::_done_when_choices`).** Rebuilt from the document as it stands each time the question is posted (`refresh_question`), so a value an earlier answer turned into an input shows as "the <input> you choose" instead of the recorded text; check text is read from `target` (what the runtime checks). It also offers one unticked "You see the <input> on the screen" per run input and a free-text box (`custom_text`); both become a required `text_present` assertion on the last step through the same gated `patch` op. Prose follows inputs through one rule, not per-mutation patches: `conxa_core/models/prose_binding.py::bind_prose` (called by `write_skill`, so every save path gets it) replaces each input's recorded `default` with its `{{id}}` in `semantic_description` (any nesting), the intent graph's goal/step intents and `meta.description` -- never in an address (`target`, `identity_bundle`, `compiled_selectors`, `url`, `value`, assertions). `readable()` renders tokens for people (Clarify, summary); Human Edit shows the raw text because it saves it back.
- **Pre-build questions are model-written (`conversation/timeline_understanding.py`, 2026-10-28).** For a recording that goes through the understanding-first flow, code holds no list of questions and posts no fixed "Is this what you recorded?" / "Does this cover everything?" / "Which of these change?" cards: the summary, the few points the model settled by itself (`decided`, each with a reason) and any gap go out as one chat message. The model returns questions as `{effect, row, ref, prompt, why, choices, best_guess, cost_rank}`; `effect` is `value | upload | confirm_before | popup_dismiss | fork | note` and fixes the choice ids (code's), while the sentence and the labels are the model's. Code then checks them: a decision popup is told apart by the recorder's own `dialog_context` (`merge.py` adds `dialog` to each event row via `outcome_fork.classify_dialog`), so a popup that asks for a decision is always a `fork` and never pre-filled, a `fork` on a row with no popup is dropped, a `fork`/`upload` moment the model left out is put back with stock wording (`must_ask`; a click that sits right before a decision popup and names no destructive word also gets a `confirm_before` question, because the page itself asked "are you sure?" and no word list covers every language. The destructive words are policy data, `validation.destructive_intent_substrings` in `policy/default_policy.json`, pinned by `runtime/test/fixtures/destructive_cases.json`), one question is kept per recorded value and per (row, effect), and at most `MAX_QUESTIONS` show at once. The call is kept small (2026-11-04): up to `MAX_ROWS = 250` rows go in as compact JSON (empty keys left out, no `kind` on an event, `url` only when it changes, `seen` = `visible_target`/`intent`+`purpose` merged/`screen_after`, with `screen_before` only when the row before had no `screen_after`), and the model returns `goal, detailed_summary, values, decided, questions, gaps, short_summary`, in that order. `detailed_summary` (one short line per event row) comes before any verdict on purpose: it is the model's working, written before it judges anything, and nothing else reads it. `phases` and `success_check` are no longer asked for. The call also carries `told` (typed values the person already marked "changes each run"/"stays as recorded" while recording, from `hints.json` keyed by the event's sequence; code applies that verdict over the model's and drops any question about it), `existing_inputs` (input names an earlier compile of the workflow declared) and the system prompt holds one worked example. A reply that arrives but holds nothing usable is asked for once more (`retry` note in the payload, `retried: true` in `understanding.json`); no reply at all is not retried. `conxa-cloud/scripts/eval_understanding.py` replays saved recordings through any OpenAI-compatible endpoint and diffs verdicts and questions against a baseline. A `note` question changes nothing: its answer is stored as text and appended to the compile's `task_description`. Every model `gap` (something the video showed that the browser did not record) is asked as a `note` question with code's two stock choices (2026-11-03), never only mentioned in the message; an own-words answer to a note is kept as its text. A navigation the recorder tagged `cause: "action"` is not a value candidate (the compile folds it into the click, which is asked instead), and all model text and event targets pass `selector_filters.scrub_email`. A "different every time" click value replays through `workflow_mutations.click_text_label` (the label the compiled step is actually known by), a fork answer is applied only to the popup its own recorded event became, and a value that still cannot be applied is told once in chat, never asked again. After the build, every compile-time question kind is still asked for such a recording except `upload`, `chosen_values` and `coverage`, which Q1 already settled (`compile_flow._ASKED_BEFORE_COMPILE`). The kinds an earlier answer is replayed onto (`_REPLAY_KINDS`) are built from the document as compiled; the others (`_REOPENED_KINDS`: `values`, `relative_date`, `skip_when_empty`, `missing_item`, `output`, `done_when`, `dropped_value`) are built afterwards from the document replay left, with typed text already judged "stays as recorded" left unticked. The understanding call is told what the person typed in chat (`understand_flow.recording_chat`), and what its caps hid (rows, candidate values, the per-kind question caps) is counted in `limits` and said in chat.

**Clarifying questions: one ledger, scored, re-evaluated (BUILD-65; `docs/reports/Clarifying-Questions-Architecture-Investigation.md`).** Both question generators (Q1 `timeline_understanding`, before the build; Q2 `clarify.generate_questions`, after it) now feed one review step, `conversation/question_plan.py`. `conversation/unknowns.py` gives every question a `facet` (what kind of knowledge it fills: variability, input_domain, provenance, alternatives, consequence, postcondition, scope, ...) and a `slot` named by recorded event ids, so it survives a recompile that renumbers steps. `SKILL_CLARIFY_LEDGER` (`report` default, `enforce`, `off`) controls whether the vacuity check (every answer produces the same edits) and the one-question-per-slot dedupe only write `clarify.json["ledger_report"]` or also drop; both run after decisions are replayed, never touch a safety kind, and never run on a Q1 question. What an answered or skipped question settled is kept in `clarify.json["ledger"]` (separate from `questions`, which the compile replaces and the pencil rewinds). `SKILL_CLARIFY_SCORING` (`off` default, `shadow`, `on`) runs `conversation/scoring.py`: `chance the default is wrong x cost` against cuts 0.08 / 0.2 / 0.4 picks ask, confirm, assume, assume and show, or defer. Safety kinds (a decision popup, a step that cannot be undone) always ask; a question asked before the build is never assumed or deferred (a guess is a suggestion until the person confirms); a question is assumed only if one of its answers edits nothing (the compiler's default). `on` parks assumed questions as `assumed` (the harness was sure, so it is not a question: listed in the summary card, never counted, "ask me" opens them). A `defer` decision is ignored: that question stays `open`, because **every question that is asked must be answered**. What this person usually answers on an app (`answer_memory.py`, value-free) moves that chance, never for a safety kind. After an answer edits the workflow, `compile_flow._flow_reevaluate` runs the compile-time detectors again and adds a question the change newly makes possible (once). A dropdown, radio or date is a Q1 `control` candidate (`merge.py` rows carry the recorded label; the compiler already made it an input, so only "always this option" edits anything, replayed through the `values` answer); `SKILL_CLARIFY_AUDIT` (`off`, `shadow`, `on`) adds a second, adversarial model read (`clarify_audit`) that may ask at most two more `note` questions naming a facet; `SKILL_CLARIFY_NOTES_TO_CHAT` hands words typed to a question before the build to the workflow chat once, after the summary, so they can become a proposed change. `conversation/clarify_events.py` logs shown / answered / skipped / reopened / test_failed value-free to `workflows/<id>/clarify_events.jsonl`; `conxa-cloud/scripts/eval_clarify.py rates` turns that, `clarify.json` and `skills/<id>/edits.jsonl` into skip and reopen rates, guess accuracy, shadow agreement and misses by facet. Golden cases: `tests/fixtures/clarify_gold/`, `tests/fixtures/clarify_selection_cases.json`.
- **The check on that answer is counted, graded and retried once (`validate()` in `timeline_understanding.py`, 2026-11-05).** `validate()` works in five stages, in this order: shape (types, lengths, enums), reference (a ref or row that exists), override (a fact the recording knows beats the model's word), backfill (a point that always needs a person is put back) and safety (no guess that approves something that cannot be undone). The per-effect policy is one table, `RULES`. Everything it drops, changes or adds is counted under a named rule in `validation_report` (`{stage: {rule: {count, example}}}`) in `understanding.json`, beside `prompt_version` (`TIMELINE_UNDERSTANDING_PROMPT_VERSION` in `conxa_core/llm/client.py`; bump it with any prompt change) and a `grade`: `unusable` (nothing to build on: falls back), `weak` (no goal, more than half the candidates without a verdict, three or more invented refs or rows, or a settled point with no reason) or `ok`. A reply that is not `ok` is asked for once more (with the reasons, for a weak one) and the better of the two is kept, so a recording costs at most two model calls. `scripts/eval_understanding.py rates` totals fallback, retry and drop rates per prompt version; `tests/fixtures/understanding_validate_cases.json` pins the golden cases. The other model guards (`_validate_findings`, the question-wording check, the chat gates, fingerprint re-validation) are mapped onto the same five stages in the workflow graph artifact but do not yet count their drops.
- **What each of the two model calls is shown, and what is never left to a default (2026-11-05).** *Understanding call.* Each event row also carries the page `title` (only when it changes), the popup's own button labels (`buttons`), what the page said after the action (`said`, value-free words), `video: confirmed` when the video also showed the typed value, and a typed value up to 160 characters. Over `MAX_ROWS` (250) rows, scrolls, hovers, idle waits and video-only moments go first, never a row a candidate, a must-ask point or a popup rests on; whatever is still too long is cut at the end, counted as `rows_omitted` (in the input and in `understanding.json`), and a candidate none of whose rows were sent is marked `unsent`, left out of the input and asked about instead of assumed. A page address or a list-picked item the model gave no verdict for makes the reply `weak` (so it is asked for once more) however few candidates there are, and what is still unjudged afterwards becomes `unsure` plus an unticked question (page addresses only when there is a description to compare them with). Prompt version 2 adds two worked examples (a list pick with a typed name; a decision popup after an irreversible click). *Review call (`workflow_review`).* Video context is stamped on the steps before the call, so each step carries `seen` (visible_target, screen_after, purpose) and `field_label` (aria-label, label, placeholder or name of the control). When the recording went through the understanding-first flow (`understanding.json` exists), `timeline_understanding.review_context()` also gives each step its `summary` line and the call an `understanding` block (goal, settled values, points already decided). The goal becomes that goal, a `parameterize_literal` for a settled value is dropped before the findings cap (`_validate_findings(settled_literals=...)`), and the "does it match what I said" check is not asked (it was discarded anyway). The person's answer in `decisions.json` beats the model's verdict. An empty or unreadable reply is asked once more with a `retry` note unless the router was unreachable (`cloud_unreachable` in `error_detail`); the output budget is 12288 tokens (was 6144); `_PROMPT_VERSION` 6 keeps an older answer from being replayed.
- **The model words it; code checks it (`conxa_compile/display_text.py`, `timeline_understanding.py`, 2026-11-07).** The understanding call writes every sentence a person reads in the pre-compile message: the summary, the assumptions, the questions and the answer labels, and it names things for a person (the row `target` is sent raw, with `role`, `peers` and what the video saw). Code does not word them. It checks: (1) `has_machine_words` (a web address or part of one, `{{token}}`, code span, snake_case name, selector fragment) and (2) `ungrounded_names` (a name in quotes must appear in the recording, casefolded, so “New folder” passes for the page's `New folder Alt+C then F` and an invented name does not). Both are counted in `validation_report` (`override/machine_words`, `override/ungrounded_name`) and make the answer `weak`, so the existing one retry tells the model what it got wrong. Only if it is still wrong after that does code replace it: a stock sentence per effect (`_FALLBACK_PROMPT`) or, for a value question, `_VALUE_PROMPT` built from `display_text.display_label` of the raw name; a `decided` line, summary or goal is dropped. `display_text` (`clean_name`, `display_label`, `person_text`) is therefore a last resort, also used where no model is involved (`clarify._action_title`, `understand_flow._short_value`). Safety stays in code regardless of the model: answer ids, never pre-filling a popup or irreversible step, and raw `target` / `literal` for replay. A click candidate whose role is in `entity_binding.CONTROL_ROLES` and that is not an `in_set` list pick carries `app_button` as evidence for the model; if the model calls it `fixed` and still asks, the question is dropped (`fixed_app_button_not_asked`). The prompt (`TIMELINE_UNDERSTANDING_PROMPT_VERSION` 5) shares one rule, `PLAIN_WORDS` in `conxa_core/llm/client.py`. The chat message reads `I watched your recording. <summary>` + `I'll assume:` (max 3 lines, no reasons; the reason stays in `understanding.json`) + the closing line. A workflow's auto-filled description comes from the goal, not the summary.
- **Understanding check (`conversation/understanding.py`; gap analysis G3).** Clarify's `values` question only sees text the person *typed*, so a recording whose choices were a page address, a row or a clicked item compiled with no inputs and "nothing to ask". After every compile, `_flow_after_compile` now (1) extracts *candidates* deterministically — navigate addresses with a path, entity-bound row identifiers, and clicked items known only by their visible label; (2) reads the answer the compile's own `workflow_review` call already gave to this question (BUILD-55: no model call of its own; stored as `compile_report["understanding"]`, stamped `for_task` and used only while that still equals the description, for address / row / clicked-item candidates only, and only when there is a description) — that call saw the person's `task_description`, the plain step names and the candidates (never a selector) and returned a verdict per candidate (`fixed` / `varies` / `unsure`, plus an input name) and a coverage judgement; (3) validates it in code (unknown refs, bad names and a `literal` that isn't inside the recorded value are dropped); and (4) turns the result into at most two Clarify questions — `coverage` ("does this cover everything you wanted?", only when the model finds a clear gap) and `chosen_values` ("which of these change each run?", `varies` pre-ticked, `unsure` listed unticked, `fixed` not asked). Answers run the new deterministic `cmd_parameterize_literal` (`editor/workflow_mutations.py::parameterize_literal`), which rewrites the literal to `{{name}}` in exactly the two fields the runtime re-reads each run — a navigate step's `url` and an entity-bound step's `entity_binding.identifier` — and declares the input with the recorded value as its default; it never touches `target`, `identity_bundle` or a selector. A clicked item with no row scope is *reported but not offered* (nothing can yet change what a click's own text signals look for; the question body says so). The call is advisory and never blocks: a failed or empty answer falls back to asking about the candidates unticked when a description exists, and to asking nothing when it does not. *Every* open asked question holds back `test`, whatever its kind (since 2026-10-25; the unused `BLOCKING_KINDS` / `blocking_open` split was removed 2026-10-26). Three more things leave `test` out of `allowed_actions`: the `understanding` runtime flag (set from the end of `cmd_compile` until `clarify.json` is saved, so there is no window where a compiled workflow has no questions yet), `compiling`/`queued` (a recompile keeps `skill_id` set), and an open `pending_change` card. The rule is enforced server-side too: `ConversationMixin._require_allowed` re-checks `lifecycle.allowed_actions` in `_run_flow_action_inner` and `cmd_flow_test_prepare`, so a stale confirm card, a summary-card button or a direct command gets `not_allowed_now` instead of starting a test. `task_description` is also a pinned fact in the workflow chat's context and is passed to the compile's second opinion as meaning-only context (added to the review cache key only when non-empty). The verdicts and goal sentence are stored in `clarify.json` under `understanding`, never in the skill document.
- **A question's typed box is a third option (2026-11-17, WF-5 5.2).** What the person types when no option says what they want. `clarify.py::words_unusable` refuses blank, over-long (500), letterless or yes-and-no words before any model call; `answer_from_words` applies words that clearly name one option exactly like a click: the option's exact words, its number, a bare yes/no (a few languages), and, only on questions that decide nothing irreversible, a whole-word match with no word left over ("email" → "User email"). On a `confirm` or `fork` question (`is_safety_question`) nothing but an exact pick, a number or a bare yes/no chooses, so "wait ten seconds, then continue" is never read as "No, just do it" (WF-5 retest). Anything else is the person's wish (`compile_flow.py::_own_wish`): on a non-safety question one `chat_map_answer` call may name an option, taken only when that option's words hold every word of the wish (`adds_nothing`); otherwise one `copilot_diagnose` call, with the question, step and options as context, proposes a change through the same gates as a chat change (`gate_all`: never `ADDRESS_KEYS`, never re-pointing a step), posted as a `pending_change` card linked to the question (`refs.question_id`). The common wish "also check the page shows the <input> I typed" is built by code as one `text_present` check on the question's own step (`_check_wish_template`), never by counting steps; the model is asked only otherwise, given that step by key, and a typed wish never changes an agent or approval step (`_without_agent_steps`). One question has at most one live change card: a new attempt, a failed one or a picked option closes the earlier card (`_drop_wish_cards`), and the proposal call is tried twice. Under "How can we tell it worked?" a sentence that describes a check (`describes_a_check`) is a wish too, the ticked options applied with it; screen words over 120 characters are refused. The question closes only when that card's Apply succeeds (`_close_question_by_wish`, which also writes the approval or ticked checks before the card records its undo depth), and Undo on that card takes all of it back and opens the question again (`_reopen_after_wish_undo`), re-blocking test and approval; Dismiss, a refused or failed apply, or no valid change leaves it open with a plain reason on its card. On a step that can't be undone (`confirm`) the approval is always added with the wish's change: a typed wish never removes it, only picking "No, just do it" does; the receipt says what changed. Before the build there is no edit path, so a typed answer there counts only as an option (code, or `chat_map_answer` off safety questions, `confirm_before` included), otherwise it stays open and says to pick the closest option and ask in the chat once the workflow is built.
- **Conversation drives the flow (`conversation/actions.py`, `intent_router.py`; gap analysis G2/G9).** `lifecycle.allowed_actions` was computed and then ignored; free text got a prose reply and nothing happened. Now `actions.ACTIONS` is the single table of what each action id is (`flow` = a backend command, `nav` = the renderer opens something, `none` = card-only; `side_effect` = needs a confirm card; plus the exact phrases that mean it). After an open Clarify question has had its chance to take the message as an answer, `harness._run_locked` step 1.5 calls `route_intent(message, lifecycle.allowed_actions)`: an exact phrase needs no model; otherwise, only for a short message containing a command word, one `chat_route_intent` call may pick an id **from the allowed set** (anything else is "chat"), with slots held to what code knows (a test input must be a declared input name; an `open_workflow` id must be one of the person's workflows). A `nav` action posts an `action_link` card; a side-effect action posts a `confirm_action` card and nothing runs until the person presses Confirm (`_card_confirm_action` → `_run_flow_action`); a harmless one (stop the test, show the next question) runs at once. `_run_flow_action` maps each id onto the same `cmd_flow_*` commands the card buttons use and never raises — a failure becomes a sentence in the thread. Values stated in chat for a test pre-fill the `test_inputs` card; they never start a run. The renderer's `ActionStrip` (scrolls with the thread; no "Open Human Edit" button — the Human Edit panel is already on screen) shows one button per allowed action and runs them through `cmd_flow_do_action` (same table), replacing the old hard-coded record/sign-in tail card. General Chat's context now carries the person's workflows (name, group, lifecycle note, capped at 50), each group's apps and the apps with a learned map, all read-only, and can route "open the invoice workflow" to that workflow's thread.

- **Re-record from a step, branches and clicked-item text (BUILD-49..53).** *Re-record from a step:* `flow_rerecord_from` replays the workflow with `stop_before_step` set to the step's runtime index (`runtime_step_index` — the packager drops steps, so this is not the document index), then `_handoff_park` makes the runtime let go of the parked page without closing it (timer, listeners, lease, host lock released; CDP disconnect only). The panel's `adopt_view` turns that test run into a record run (same views and partition, fixed viewport, target ids looked up) and the recording takes the panel run's id as its session id (`host_view.adopt`). On Finish the take is compiled with `segment=True` and either spliced over the step and everything after (`cmd_splice_segment`) or, in branch mode, wrapped with the existing tail as a `wait_for_one_of` (`branch.flow`, `wrap_variant_branch`; refused when both ways begin with the same element). A flow branch runs its chosen body through `executeOneStep` (recovery, gates, tabs); a banner-style branch still swallows body errors. *Pauses:* the test guard inside a `for_each` body parks with a row cursor (`{stepIndex, rowId, bodyIndex, perRow}`, resumed by row id because deleted rows are gone); values a run produced before a pause are stored on the park (`producedInputs`). *Clicked-item text:* `parameterize_click_text` is the one edit outside the compiler that writes `identity_bundle` — substitution of one label by `{{name}}` in text signals and fingerprint text, dropping signals that would still pin the recorded item; the runtime fills the fingerprint from the run's inputs (`bundleFingerprint`) and ignores the recorded hash for it. *Capabilities:* `required_capabilities` in a skill manifest is checked against the app layer's own `version.json`.
- **Compile accounting and determinism (gap analysis G7/G8).** (1) *Nothing is silently dropped.* `compile_skill_package` records which recorded actions disappeared in the two cleanup passes (`clean_steps`/`fix_step_order`, `fold_action_caused_navigations`) by recorder sequence number into `compile_report["events_total"]` and `["dropped"][{sequence, pass, action}]` (`_record_drops`), and the compile message says how many were folded away; deliberate merges (a date picker's clicks becoming one step) are not listed. The packager removes a declared input that no kept step mentions (`_drop_unreferenced_inputs`, substring test so a loop's bare `items` name survives) and names it as a build warning (BUILD-24). (2) *The second opinion replays.* `build_workflow_review` pins the model's raw answer in `data/sessions/<id>/second_opinion.json` (keyed by the hash of its inputs, which include the task description); a recompile of the same recording replays it without a model call even if the shared cache was cleared. (3) *Recorder ordering (BUILD-47, part).* The launched recorder now pumps the page for `_DOWNLOAD_CLICK_GRACE_S` before queueing `download_observed`, as the embedded path already did, so the click that started a download is reported first and the compiler attaches the download to the right click. The popup-tab race is still open. (4) *Clarify keeps every question.* `generate_questions(limit=None)` returns all of them; the first `MAX_QUESTIONS` are `open`, the rest `queued` in `clarify.json` (never dropped). Held-back questions come forward on their own as soon as nothing is open, before the build (pre-compile) or the summary (post-compile). **No question can end unanswered:** there is no skip; typing your own words answers it (`answer = {"custom_text": ...}`, the chat then proposes any change); a re-record marks questions about vanished steps `superseded`. `clarify_store.summary()["unanswered"]` (open + applying + queued, never `assumed`) is a hard blocker: `lifecycle` offers no `test`/`approve`/`publish` while it is above 0, `cmd_flow_compile` refuses while a question asked before the build is open, `cmd_flow_approve` calls `_require_allowed`, and `cmd_publish_skill_pack` / `cmd_build_installer` refuse (`unanswered_questions`). An unanswered question is re-asked whenever the conversation opens (`_reask_unanswered`, never a second card) and named after any typed chat message; the renderer pins the open card above the composer. The `values` question now also covers single-choice and date controls (restoring the recorded option / ISO date when kept fixed).
- **Reading values back, conditional steps, missing records (gap analysis G4/G5).** (1) *Extract.* A recorded `extract` event (the recording card's "Capture a value": `bridge.js` `window.__conxaCapture`, switched on from the recorder's own thread by `RecordingSession.request_capture`/`_apply_capture_mode_sync`, one shot, the page never sees the click) compiles through `_finish_extract_steps` into a step whose target is built exactly like a click's (full IdentityBundle) and whose `value` is the output name (`SkillStep.extract` = `{source: text | value | attribute, attribute?, pattern?}`, no assertions). It is a normal editor kind (`action_registry`, `EXTRACT_ALLOWED_KEYS`, `_validate_extract_spec`) and the packager emits `{type: "extract", selector, output_name, extract}`. At run time `handlers.js::extract` resolves it through the usual resolution walk and recovery cascade, reads the value (`readExtractValue`; no match or an empty value is a non-retryable `badInput`, never an empty output) into `inputs[output_name]`, and `runPlan` returns `outputs` (`collectOutputs`). `execute_skill` adds an `Outputs:` block to its text and `structuredContent: {outputs}`, both absent when nothing was extracted so runs without outputs are byte-identical to before. Studio's test card shows them (`outputs_from_message`). A Clarify question (`output`) confirms each captured value is meant to be reported. (2) *`run_if`.* Any step except an agent span, hand-over or outcome fork may carry `run_if: {input, op: not_empty | equals | not_equals, value?}`; `run.js::runIfDecision` reads the run's inputs only (never the page), a malformed condition means "run the step", and a skipped step is reported. The gate refuses it on `agent_span`/`handover` so no input can switch off a guard in front of a destructive step. (3) *`on_missing: "skip"`* on a step bound to a record (`entity_binding`) skips it, with a warning, but only when `entityRoots` proves the list is on the page and none of its records is this run's (`entityAbsent`); an ambiguous match, a hung count, an unrendered list or an unsupplied identifier input still fails closed. (4) *Engine capability.* The app layer declares `outputs` and `run_if` in `version.json` (`build-runtime-app.yml`); `capabilities_needed()` makes Studio refuse a test on an older engine instead of running a step it would ignore or fail on. Not built: carrying values made during a run across a span/fork pause (they live in `inputs`, which a resume rebuilds from the caller's arguments), `outputSchema` on the per-skill MCP tools, and a "ask a person" `on_missing` mode.
- **Refining one part, and seeing the failure (gap analysis G6/G10).** (1) *Replay up to a step.* `execute_skill` accepts the Studio-only `_stop_before_step: N`; `run.js::executeOneStep` parks on the live page before step N (the guard's park, `stopBefore` in the run context, refused inside a loop body or popup choice) and returns `_meta["conxa/stopped_at"]` and no "Done."; `cmd_test_workflow(stop_before_step=N)` turns that into `status: "stopped_at_step"` (engine capability `stop_before`; an older engine is refused rather than allowed to run the whole workflow). (2) *Splice.* `replace_step_range` replaces `steps[start..]` with a separately compiled segment (`compile_skill_package(segment=True)` omits the synthetic start-page navigate); steps before the splice keep their keys, the intent graph is remapped, derived per-step data is invalidated and new placeholders become inputs. `cmd_splice_segment` applies it through the undo stack and calls `clarify_store.mark_stale`, which flags answers about replaced steps `stale` (answers about surviving steps stand). **Not built (needs a live Electron pass, BUILD-52):** handing the parked page to the recorder (`adopt_view` in `browser_control.js`, `host_view.acquire(existing_run)`, `RecordingSession` adopting an already-open page) and the conversation action that strings replay → record → compile → splice together. (3) *Vision.* The fix proposal after a failed test (`_tf_propose_fix`) and the chat's diagnosis on a thread whose last test failed (`harness._propose(with_failure_image=True)`) now pass the failure screenshot to `copilot_turn`, which already routes an image to the multimodal model. The failed test card shows the picture through `conxa-asset://local/sandbox-evidence/<run id>/_evidence/<file>`, a route `main.js` serves only for that exact shape (never other sandbox paths); `_tf_failure_image` derives it from the recorded evidence path and returns None for anything that is not under `_evidence`.
- **Test-loop resilience (gap analysis G11).** (1) *Sign in and carry on.* A test that paused on an expired sign-in used to end at "Test again", which restarted from the card's inputs and never signed anyone in. `cmd_flow_sign_in_then_test` sets the `retest_after_sign_in` runtime flag and opens the sign-in; `_flow_finish_sign_in` then runs the same test again with `last_test_inputs` (the guard stays on) instead of opening a recording, opens the next app's sign-in first if more are missing, and cancelling the sign-in clears the flag. A "sign in" typed in chat on a compiled workflow takes the same path. (2) *Interrupted tests are said to be interrupted.* Pause decisions live in memory by design (a parked browser cannot survive a restart), so a `test_run`/`autofix` card still saying it is running after a restart used to spin forever; `_reconcile_interrupted`, run whenever a workflow thread is opened, marks such a card `interrupted` (with "Test again") when Studio is not testing anything and the card is more than 30 s old. (3) *A confirmation can't be quietly weakened.* `validate_editor_patch(next_step=...)` refuses an edit that turns a confirm-only `agent_span` question into a free-form one while an irreversible step follows it (`confirmation_guards_a_destructive_step`); the existing check only inspected the destructive step when *it* was patched. **Not built (BUILD-53):** pausing the guard inside a `for_each` body (needs a persisted row cursor), and the general variant branch learner.
- **Confirm-only review exemption.** `patch_gate.is_confirm_only_review`: a review whose schema is exactly `{confirmed: boolean}` and whose `on_failure` is `abort` may directly precede a destructive step (see §11.3). Known gap: the gate only runs when the *destructive* step is patched, so editing the review afterwards into a free-form one is not re-checked.
- **Test safety guard (BUILD-40 / RT-9).** A test never crosses a step that can't be undone without a person. The runtime (app layer only, no host change) accepts two Studio-only arguments: `_pause_before_irreversible` parks the run on the live page before the first step the compiler marked `destructive` (`run.js::executeOneStep`, parked like an `agent_span`, returns `_meta["conxa/guard"]`), and `_guard_release` with `resume_from` runs that step and every later one. It never touches `recovery_stage.js` or `retry_budget.js`, is inert without the argument (every customer and MCP run, and the CI gate), is skipped by `dry_run`, and fails closed inside a `for_each` body (a pause there would lose the loop cursor). The Studio (`handlers/workflows.py::_drive_runtime_pauses`, `handlers/test_flow.py`) asks the person: **Run the last N for real** or **End the test here** (which is "passed up to X" and is *not* a pass — the last real result is untouched). An engine that predates the guard would ignore the argument and send a real invoice, so the Studio first checks the staged app layer's `version.json` for `capabilities: ["test_guard"]` and otherwise **previews** (dry run), which also never counts as a pass. A confirm-only `agent_span` question (Clarify's "a person confirms before sending") is answered by the **person**, never the sandbox model, and a "yes" also releases the guard in the same resume, so they are asked once, not twice; a "no" ends the test (the schema's `enum: [true]` means "no" is an invalid answer, so on a real run `on_failure: abort` ends it too). Runtime log lines `test_phase` now carry `step`, `step_total`, `intent`, `destructive`, so the test card names each step as it starts. Ships as a new `app-vX.Y.Z` tag (the CI step writes the capability); `MIN_HOST` is unchanged. **Approval** is a separate human action after a pass (`flow_approve` → `cmd_sign_off_workflow`, which packages but never publishes); a test build needs no approval, a release build does.
- **Find the cause (BUILD-40, reworked 2026-11-06).** Off the happy path and **outside the recovery cascade** — Tier A stays zero-token, this only runs after a test has already failed. With the Settings switch on (default; `data/studio_settings.json`, key still `autofix_enabled`), a failed test makes the copilot's own `copilot_diagnose` call explain the root cause in plain words and propose **at most one** change (`_tf_investigate`, `_tf_propose_fix`). The same three gates filter it and it appears as an ordinary `pending_change` card with Apply / Dismiss. **Nothing is applied by itself and there is no attempt cap:** the person presses Apply (edit-logged like any reviewer Apply) and Studio then re-tests at once **with the guard on** (`_tf_retest_after_fix`); if it fails again, it investigates again and asks again. The person can instead type their own fix in chat; the copilot turns it into a change card, and Apply on that also re-tests, because the re-test is keyed on the newest `test_run` card having failed (an edit resets `last_test_status`). The investigating card ends `done`, `no_fix` (no in-gate proposal: Studio says so and asks how the person would fix it), `stopped` or `failed`; the in-memory `investigating` flag makes the lifecycle show `investigating`. Approval stays human and is revoked by any later edit.
- **Destructive flag.** The runtime reads only `step.destructive`, exported from `identity_bundle.destructive`; `sync_derived_step_fields` now re-syncs it whenever `consequence` is patched, so marking a step irreversible in Human Edit (or accepting a copilot proposal that does) actually reaches the runtime.

**Video job (Engine v2 C2, 2026-11-06).** The video analysis is a submit-and-poll cloud job; its wait now outlives
one request and one Studio session. `conxa_compile/video/job.py` keeps `sessions/<id>/video_job.json`
(`fingerprint`, `job_id`, `state`, `attempts`, `first_try`, `next_try`, `last_error`, `blocked_code`; states `polling`,
`waiting_cloud`, `done`, `blocked`, `failed`). `VideoAnalysisClient.analyze` takes `resume_job_id` (poll a saved job
without uploading again; if the cloud lost it, submit afresh as before) and `on_job_id` (called with each new id).
When one 600 s budget runs out, `_flow_understand` waits 1, 2, 5, 10 minutes, then 30 minutes, for up to 24 hours,
then fails with the Retry card. The card says when it tries again; the `watching` flag stays on, so compile and test
stay blocked. Quota, trial, sign-in and unreadable-video failures stop at once (`blocked` / `failed`). At Studio start
`_resume_video_jobs` re-starts every recording with a live record and no `understanding.json`, and
`_reconcile_interrupted` leaves such a card alone. Pegasus stays required. The cloud's own job store is still in
memory (a cloud restart makes the saved id 404, which costs one new upload).

### 7.4b Connection, Engine State and Sign-in Expiry (BUILD-43, 2026-10-18)

Studio depends on two things the person can lose without losing their work: the local **engine** (the Python backend) and **Conxa Cloud**.

- **Engine state.** `main.js` tracks `running | restarting | stopped` and pushes `engine:status` to the renderer. An unexpected exit restarts the engine up to three times (the counter resets once a start has stayed up for a minute, so unrelated crashes over days don't add up); after that the state is `stopped` and the old native error box is replaced by an in-app notice. `engine:restart` (IPC, only honoured while stopped) resets the counter and starts it again; `engine:diagnostics` returns the state plus the last 60 stderr lines for “Copy diagnostics”. Workflows, sessions and threads are files on disk, so nothing is lost; when the engine returns, the renderer invalidates every cached query. While it is down the workspace is replaced by the notice, navigation stays visible but non-interactive, and the inspector is hidden.
- **Cloud reachability.** `cloud_ping` (`handlers/session.py`) GETs the cloud's `/healthz` and returns `{online, ms}` or `{online: false, detail}`; any HTTP answer counts as online, only a missing answer counts as offline, and it never raises. `useConnection` polls it every 30 s (8 s while offline), on the OS `online`/`offline` events, and from the banner's **Try again**; two consumers read the result: a slim banner above the workspace and the sidebar footer's connection line. Offline, the composer is disabled with its reason, **Try again** / **Compile now** on compile cards are disabled, and Publish says it is waiting to connect. Nothing is queued: an action that needs the cloud is simply not offered until it returns. Recording, Human Edit and tests that run on this computer keep working. **Approve** is local (it builds the package from the already-approved skill) and stays available.
- **Sign-in expired during a test.** The runtime tags a session-expiry stop with `_meta["conxa/session_expired"] = {run_id, step_index}` (the text is unchanged for every other client). `cmd_test_workflow` returns status `needs_sign_in`: it is not a failure, it does not write `last_test_*`, and it never starts find-the-cause (an expired login says nothing about the workflow). The test card shows a “sign-in expired” state, `runtime_state.sign_in_expired` makes the lifecycle ▲ `sign_in_expired`, and the next test clears it. Studio does **not** wait for the person to sign in and resume the run itself — `wait_for_auth` stays false (AUTH-4); they sign in again and test again. An engine that predates the tag reports an ordinary failure.
- **Settings** is a popup (`SettingsDialog`) over whatever was open: Account, Usage (the meters, “AI credits”), Testing (the find-the-cause switch) and About (version, updater, terms). `/settings` remains as a link target that opens it.
- **Leaving Human Edit** with an unsaved step change asks first (Save and leave / Discard / Keep editing). This is why `main.tsx` uses a data router (`createHashRouter`) with one splat route: `useBlocker` does not exist under `<HashRouter>`.
- **First launch.** `/` goes to `/welcome` when the workspace has no groups (and the lists loaded without error); the page shows the three-step flow and the computer's readiness from `deps_status` (signed in, recording browser, test runner), with **Install now** (`deps_apply`) when one is missing.

### 7.5 Identity-Capture Rules

Rules the recorder and compiler rely on for durable element identity. Violating one silently produces unmatchable bundles.

- **Recorded observations must be copied through.** `session.py::_finalize_payload_sync` builds an allow-listed `body` before `RecordedEvent.model_validate`. It must copy `branch_hint`, `date_context`, `choice_context`, `optionality`, and `post_condition`, and every field must be declared on the model (Pydantic silently drops undeclared ones, nested ones too: `AnchorRelation.ephemeral`, set by `bridge.js::_isTransientOverlay` for an anchor in an alert/status region or a fixed non-landmark overlay, exists for this reason; `clean_anchors`, the element fingerprint and the relational signal skip such anchors).
- **A11y snapshots are captured in-thread** (`_capture_a11y_snapshot`, `aria_snapshot(timeout=…)`). Playwright's sync API is greenlet-bound, so a worker thread always fails. Without the tree, `resolves_to_nothing()` can't reject fabricated role+name signals.
- **Pointer-cursor controls:** `isInteractiveNode` also accepts an element with its own pointer cursor (not inherited), 1–40 characters of visible text and no button or link around it (a script-wired `<p>Close</p>`).
- **Interactive roles** in `isInteractiveNode` include `gridcell`, `treeitem`, `menuitemcheckbox`, `menuitemradio`, `searchbox`, `spinbutton`, `slider`. Container roles (grid, listbox, menu, tree, radiogroup) stay out.
- **Calendar root is the outermost matching ancestor** (`findCalendarRoot`). Class-substring matches also hit day cells.
- **Dates format from local parts**, never `toISOString()`. `_parseDateFromString` strips ordinals and extracts embedded ISO / `Month D, YYYY` / `D Month YYYY` from aria-label prose.
- **No text selectors for option-content elements** (`select`/`datalist`/`optgroup`) or text over the cap: `buildTextSelector` emits `""`, and `identity_bundle.py` gates the `text_based` channel the same way (healing old recordings).
- **XPath** starts with `//` unless the walk reached the document root.
- **Deep CSS chains** are rejected (`is_brittle_deep_chain`), but `salvage_deep_css_tail()` keeps the shortest identifying tail without `:nth-*`, still subject to every quality gate.

- **Verified-unique attribute selectors (2026-11-15).** `named_attrs.py::synthesize_attr_selector` returns the first ladder rung that matches exactly one node in the recorded page across every data key; a rung that matches none is not trusted (the ladder's `role` is usually the implicit role of the tag, not an attribute, so it matched nothing in saved HTML and passed as "can't verify"). Only the plain `[data-key="value"]` rung may stand on zero matches, for an element missing from the snapshot. A signal known to match several elements is capped at durability 0.35.
- **Link address:** for a link, `a[href="..."]` is a candidate when unique on the saved page (`identity_bundle.py::_link_href_selector`; `href` is read from the page snapshot because the recorder does not keep it).
- **Form field name (2026-11-17):** an `input`/`select`/`textarea`'s own `name` gives `tag[name="..."]` (engine `attr`) only when it matches exactly one node on the saved page (a radio group shares one name), so boxes in an unlabelled table are told apart. A relational signal takes its role base's uniqueness (the runtime resolves it by that base alone), and signals are re-sorted after the caps so the first one, which is promoted and shown, is the best.
- **Numbers that change by themselves:** a role name loses a trailing count (`strip_volatile_count`: "Pull requests 58 (58)" -> "Pull requests"; Playwright matches a role name as a substring); text and names carrying digits are capped at 0.6; a bare-number text ("3") is never a signal. Framework-generated ids (`:rh`, `ember123`, `react-select-3-input`) are dynamic (`is_dynamic_id`).
- **Personal text:** `drop_personal_target_text` also removes an email address from the page title carried on every event; the package builder's email check scans only what ships (the title, page address and timing in `signals.context` are not shipped).
- **Input derivation:** `derive_input_binding` skips payload actions (`drag_drop`); the recorder's sibling-caption walk (`captureAssociatedLabel`) applies only to form controls and skips headings, so a page title never names a text box.
- **One home for a step's value:** `patch.py` writes `value` to both `step.value` and `action.value` (the packager prefers `action.value`).

### 7.6 SkillPackage Schema

```python
SkillPackage:
  meta: SkillMeta                     # id, version, title, source_session_id, visited_hosts
  inputs: list[dict]                  # input schema
  skills: list[SkillBlock]
    └── steps: list[SkillStep]
          action: str | dict
          intent: str
          semantic_description: str
          phase: str
          tab: dict                   # {id, index, opened_by, opener_tab}
          frame: dict                 # iframe chain
          identity_bundle: IdentityBundle
          element_fingerprint: ElementFingerprint
            role, tag, inner_text, aria_label, name, placeholder, label_text, alt,
            title, data_testid, input_type, css_class_tokens, anchor_phrases, position_hint
          compiled_selectors: list[str]
          validation: ValidationBlock
            assertions: list[Assertion]   # url_changed, url_pattern, selector_present/absent,
                                          # text_present/absent, value_equals, state_changed
          handler_hints: HandlerHints     # control_kind, date_picker, choice, hover_chain
          branch / for_each / agent_span_* / handover_*   # §11
          consequence, destructive
          recovery: RecoveryBlock
          snapshot_ref: str
  intent_graph: WorkflowIntentGraph    # goal, steps, decision_points
  compile_report: dict                 # status, steps_total, min_confidence, second_opinion, archived_steps
```

Full field reference: `docs/Backend-Schema.md` §3.

### 7.7 Contract vs. Executor Boundary

Browser replay is the first executor, not the last: skills may graduate to native API connectors (PROD-7) or a computer-use executor. Conxa's value is the layer above the executor (intent, verification, entity bindings, audit). So `skill_spec.py` tags every field `[contract]` (must hold for any executor) or `[executor]` (browser-replay detail). `docs/Backend-Schema.md` §3.0 is the source of truth.

- Branch primitives define their condition in contract terms (identity/state), with browser evaluation on the executor side.
- Every new `SkillStep`/`SkillPackage` field must be tagged at introduction and added to `docs/Backend-Schema.md` §3.0 in the same change (ARCH-3).


**Action contract (Engine v2 C3, 2026-11-06).** `packages/conxa-core/conxa_core/models/action_contract.json` states
what each of the 42 recorded kinds becomes (`step`), how the runtime runs it (`run`: `handler`, `noop`, `intercept`),
whether recovery applies, and which builder turns it into a step (`via`); `step_only` lists the editor-authored types
that are not recorder kinds. Nothing reads it at run time and it is not shipped to customers. Two tests fail the build
when the sides disagree: `conxa-cloud/tests/test_action_contract.py` (kinds equal `ActionKind`; step-only types equal
the editor registry minus `ActionKind`; recovery flags equal `action_policy`; the packager builds each `packager` kind
into the named step) and `runtime/test/unit/test_action_contract.js` (every named type is in `HANDLERS` /
`NOOP_STEP_TYPES` / `PRE_DISPATCH_STEP_TYPES` as stated, and nothing in those sets is missing from the file).
**Adding a kind:** add it to `ActionKind`, add one entry here, and the tests name what else is missing.

---

## 8. Skill Packaging

**Location:** `conxa-builder/python/conxa_compile/skill_package_builder.py`

`build_skill_package(workspace_id)` gathers every workflow in the workspace into a data-only package:

```
output/skill_package/{workspace_dir_slug}-skill-package/
├── skill_package.json   workspace_id, display_name, skills[]
├── CLAUDE.md, index.md  rendered from skill_package_templates/skill_package/
├── pack.json            groups, skill_groups, sync/tracking (§5.3.1, §5.4)
└── skills/{group_id|_default}/{skill_slug}/
    ├── execution.json   compiled steps
    ├── recovery.json    recovery blocks + anchors
    ├── inputs.json      input schema
    ├── manifest.json    group_id, unclaimed_hosts, target_url, …
    └── validation.json
```

**Invariant: auth never enters build output.** `skill_package_builder.py` excludes `auth/` and credentials. `handlers/workflows.py` refuses both publish and installer build if any `auth.json` is found under the built pack.

**Installer** (`installer_builder.py` + `installer_templates/setup.nsi.tmpl`): a per-user NSIS `.exe` (no UAC) that
1. stages `conxa-runtime.exe` + `keytar.node` into `conxa-runtime/<ver>/` and the app layer into `conxa-app/<ver>/`, with `current` junctions;
2. runs `conxa-runtime.exe --install-playwright` with `CONXA_DIR` set, so Chromium lands where the runtime reads it;
3. stages only `pack.json` into `skill-packs/{ws}/` (§5.5), then runs `conxa-runtime.exe sync` to pull the released skills (the `min_host` gate applies, §4.1);
4. runs `conxa-runtime.exe register-mcp` (§4.3) with `CONXA_ENV`/`CONXA_UPDATE_CHANNEL` set; the uninstaller runs `unregister-mcp`.

---

## 9. Execution Pipeline

**Location:** `runtime/app/run.js` (+ `handlers.js`, `resolution.js`, `tabs.js`, `cascade.js`)

### 9.1 Step Loop

`runPlan` is a thin `for` over `executeOneStep(ctx, state, steps, i)`. `for_each` reuses the same function per row (§11.2). `ctx` is run-constant (tabs, watch, dryRun, slug, tracker, cancelCheck, onStep, runId, hostRunId). `state` is mutated across steps (recoveredSteps, prevPage/prevStepType, warnings, dryRunSkipped, the flat `inputs` namespace).

```
1. poll the pause control file
2. waitForPageLoad()          only after a NAVIGATION_STEP_TYPES step (§9.3)
3. waitForUrlState()          pre-step URL gate, if step.url is set
4. resolveStepPage()          live page for step.tab (§9.2)
5. executeStep()
   ├── interpolate {{inputs}}
   ├── resolveStep()          IdentityBundle resolution (§10.2)
   ├── withLocator()          GATE, then the action handler (handlers.js)
   └── on miss: settle retry (§9.3) → recovery cascade (§10.1)
6. verifyStep()               assertions, independent of the action's own success (§9.5)
7. writeCheckpoint()          step-level resume point
8. tracker.emit()             telemetry
```

There's no artificial pacing between steps. The whole run is bounded by `EXECUTION_DEADLINE_MS` (`server.js`, 210 s default via `CONXA_EXECUTION_DEADLINE_MS`), sized to return an actionable failure inside Claude Desktop's ~240 s tool timeout.

**Short-circuits that skip recovery:** `isAuthFailure` (login redirect, §5.3.7) and errors marked `badInput` (wrong caller input, such as an unmatched choice, a folder for a single-file control, or a missing download). Re-finding the element can't fix those, so they must never spend Tier B tokens.

### 9.2 Multi-Tab Step Resolution

**Location:** `runtime/app/tabs.js`

Each step's `tab` block resolves to a live `Page` fresh on every step: declarative, like `frame_chain`, not stateful enter/exit. `runPlan` creates one `createTabRegistry(startPage)` per run, binding `tab_0` and registering the context `page` listener before any step runs, so an early-opened tab is queued.

`resolveStepPage(registry, step, {prevPage})`:
1. No `tab` or `tab_0` → the initial page.
2. Already bound and open → reuse.
3. `opened_by: "site"` → drain the queue, else `context.waitForEvent("page")` (`CONXA_TAB_OPEN_TIMEOUT_MS`, default 30 s).
4. `opened_by: "user"` → `context.newPage()` (the recorded `navigate` step then loads it). Under Execute, `host_browser.newTab` (§4.7).
5. Unresolvable → throw `.tabNotFound`. **Never fall back to the current page.**

- **A bound page is never handed out again.** The registry tracks a `bound` set (initial page, drained pages, and pages it created). Playwright fires `page` for pages the registry creates too, so without it a blank Ctrl+T page could be returned as a later popup.
- **Settling:** whenever execution moves to a different page than the previous step (including returning to an open tab), wait for `domcontentloaded` up to `CONXA_PAGE_LOAD_TIMEOUT_MS` (default 60 s), plus `bringToFront()` under watch. Site-opened tabs first wait to leave `about:blank`; user-opened tabs don't (nothing else will navigate them). Both waits are best-effort. The same 60 s budget applies to `navigate`'s `goto()` and URL assertions.
- **Foreground reclaim:** Chromium foregrounds every new tab, including unreferenced popups, and background tabs get rAF/timers throttled (stalling Playwright's `stable` check). The `page` listener sets `foregroundStale`; the next step settles with `forceFront` (even headless) and emits `foreground_reclaimed`. Calling `bringToFront()` on every step was rejected because concurrent watch runs would fight for focus.
- **Markers:** `tab_open`/`tab_switch`/`popup` have explicit empty handlers (the switch already happened in `resolveStepPage`). `stepInheritsPage(step)` keeps a marker with no `tab` block on the current page, a guard for legacy or mis-stamped packs; new packs always carry explicit blocks.
- **Diagnostics follow tabs:** `server.js` attaches console/request/download listeners to every tab. Parks and failure responses use `runErr.failedPage`. `run.js::executeOneStep` is the single choke point that stamps `failedPage` (the page the step resolved to) on any error leaving a step; no call site opts in.
- **Step identity is a path.** `executeOneStep` keeps `state.stepPath` (`[9, 0]` = first step inside step 9's chosen popup option, loop body or branch body). `onStep(i, { path, step })` reports it; `server.js` always reports progress against `path[0]` and marks nested steps with an `inner_` phase prefix, so a reader building one row per plan step (Build Studio's test card) never shows a body-local index as a plan step.
- **After answering a popup, the check is "is THAT popup gone"** (`run.js::confirmForkDialogClosed`, `fork.js::stillShowing`): the same recognition (`scoreState`) that matched the popup before the click. A progress tray or "complete" notice the app opens in answer is a different surface and is passed over; a different blocking popup is left to the next step with a warning. Whether a surface left open at the end of a run can stop the run is a **measured** fact, never a reading of its words: `page_scripts.js::dialogProbe` reports `blocks` (`true` = a modal, a covering fixed backdrop or an inert page; `false` = every point outside it reaches ordinary page content; `null` = could not tell, e.g. a frame), and `fork.js::isBlockingDecision` is `isDecisionDialog` AND `blocks !== false`, so a missing or unknown measurement counts as blocking. Wording can never turn a decision into a notice (the earlier outcome-word list was removed); the shared fixture `decision_surface_cases.json` pins `stops_when_left_open` per case.
- **Cleanup:** `closeExtraTabs` (per-call `_openedTabs`) closes extra tabs on every exit path; a parkable failure keeps only the parked tab. This matters because headless browsers are cached and reused.

### 9.3 Page-Load Waiting & Settle Fallback

The only inter-step wait is `waitForPageLoad()` after a step whose type is in `NAVIGATION_STEP_TYPES`: `domcontentloaded`, plus `networkidle` when `CONXA_WAIT_NETWORKIDLE=1`.

**Settle fallback** (`runtime/app/settle.js`): the compiled `wait_for` shape is inferred from one recorded load (`validation_planner.py::infer_wait_for_shape`) and lowered into assertions at compile time, so it can under-wait on a slow day. After an ordinary resolution or verify miss, and before recovery, settle polls `page_scripts.js::settleSignature` (text length, interactive count, node count, visible busy indicators) until two consecutive samples match with no busy indicator (`CONXA_SETTLE_BUDGET_MS` 8000, `CONXA_SETTLE_POLL_MS` 250).

If the page actually changed, the step retries once. A step whose action never dispatched retries action + verify. A step that may have acted (`verifyFail`/`mayHaveActed`) is only re-verified, so non-idempotent steps (`step_utils.js::isNonIdempotent`) are never double-submitted.

Kill switch: `CONXA_SETTLE_RETRY=0`. Telemetry: `settle_retry`, and `tier_ok` with `tier1_compiled_settled`.

### 9.4 File Uploads

Browsers expose only `File.name`, so nothing recorded is a valid path on the replay machine. Uploads are parameterized end to end (download binding: §7.2).

| Stage | Behavior | Where |
|---|---|---|
| Record | Studio's own picker (§6.1). `upload_intent` carries `[{name,size,type}]` for every selected file, never a path, and gets no frames of its own (the preceding click has them) | `session.py`, `bridge.js`, `frame_extractor.py` |
| Clean | An `upload`/`upload_intent` supersedes every earlier click/focus on the same file input, even with a menu click between (sites activate hidden inputs programmatically). Replay must never click a file input | `step_anchors.py::clean_steps` |
| Compile | File inputs aren't "editable", so no click→focus rewrite (the `focus` handler clicks first) | `action_semantics.py::is_editable_target` |
| Bind | Always the input name `file_path` | `input_binding.py::derive_input_binding` |
| Package | Recorded metadata (truthy JSON) is detected and replaced with `{{file_path}}`; hand-typed paths are kept. The input description gives the example filename and says a folder may be passed when the control accepts multiple files (it doesn't claim a count) | `skill_package_builder_saved_skill.py::_upload_input_descriptions` |
| Execute | `interpolate` → trim → strip one pair of surrounding quotes ("Copy as path") → a directory expands to its direct files, naturally sorted → `setInputFiles`. Hidden inputs skip the visibility GATE (`attached` only). A page `filechooser` listener suppresses native pickers. An empty path or empty folder **throws** | `run.js::resolveUploadPaths`, `HANDLERS.upload`, `resolution.js::gateLocator` |
| Gate | Multiple files against a live `el.multiple === false` → `badInput` (skips recovery). An unreadable probe stays permissive | `HANDLERS.upload`, `withLocator`, `runPlan` |
| Validate | Uploads rarely change URL or DOM, so a required `state_changed` assertion is added when nothing stronger claimed the enforced slot | `build.py::_build_assertions` |

Tests: `runtime/test/test_upload.js`, `conxa-cloud/tests/test_skill_package_builder.py`, `test_phases.py`, `test_recorder_session.py`, `test_element_fingerprint.py`, `test_frame_extractor.py`.

### 9.5 GATE & VERIFY

**GATE** (`resolution.js::gateLocator`, before every action): attached → visible → RAF-stable (bounding box unchanged across two frames) → enabled (`disabled`/`aria-disabled`). Confidence-adaptive budget, zero LLM. Hidden file inputs need only `attached`.

**VERIFY** (`run.js::verifyStep` + `assertions.js`, after every action): checks the step's compiled assertions independently of whether the action itself succeeded.

| Type | Meaning |
|---|---|
| `url_changed` | URL differs from the live pre-action URL (recorded `target` = before-URL, fallback only) |
| `url_pattern` | URL matches a regex |
| `selector_present` / `selector_absent` | element appears / disappears |
| `text_present` / `text_absent` | text appears / disappears |
| `value_equals` | field value (normalized, with a contains fallback for masked/formatted fields) |
| `state_changed` | some observable effect: URL, interactive count, or body-text delta beyond noise |

- **One required assertion per consequential action** (submit click, destructive confirm, text entry, select, upload), chosen by `build.py::_build_assertions`. Everything else is advisory. A required failure descends into recovery and is re-verified after every remedy; unrecovered, the step and run fail. Advisory failures are only recorded.
- **Evidence preference:** recorded `post_condition` (§6.5) beats generic `wait_for`/`success_conditions` inference. `dialog_opened` claims the required slot as `selector_present` on `dialog_signal`; `value_set` uses `value_readback` as `value_equals.expected`; `dom_diff` is the `content_change` fallback. `state_changed` is synthesized for commit/destructive clicks and uploads with no other evidence. `validation_planner.py::infer_success_conditions` demotes ephemeral elements (`selector_filters.py::is_ephemeral_anchor`: banners, toasts) to advisory `text_present`.
- **Web-first polling:** positive checks retry every `VERIFY_POLL_INTERVAL_MS` (250 ms) until `timeout_ms`. Negative checks must still hold after `NEGATIVE_STABILIZE_MS` (500 ms). `pollPositive`/`pollNegative` race each predicate against the remaining time via `withDeadline`, so a stuck `evaluate` can't hang the run.
- **Native dialog pending:** with `dialogQueue` non-empty, `verifyStep` returns `{pass: true, channel: "dialog_pending"}` without touching the page (the renderer is blocked, and "a dialog opened" is the real post-condition).
- **Completion gate (action ran ≠ action finished).** After a step passes VERIFY and before the run moves on, `run.js::confirmCompleted` calls `completion.js::waitForCompletion` for every non-idempotent step (`step_utils.js::isNonIdempotent`: click, upload, drag, keyboard shortcut…). A progress tray satisfies `state_changed`, so VERIFY alone reports an upload done while it still runs. The gate reads the page only (`page_scripts.completionProbe`: visible busy indicators plus the text of status regions and non-modal dialogs; `page_status.js`: data-changing xhr/fetch requests still open or refused) and counts only what is **new since just before the action**. `short` mode (every acting step) waits up to `CONXA_COMPLETION_SHORT_MS` (15 s) for visible progress to clear, then carries on; it never fails a step. `full` mode (a step whose effect kind is `upload`, an `upload` step in a pack without effects, and the plan's last acting step) also waits for open requests and fails the step when the app says it did not work (`fault_classify.js::classifyCompletionFailure`, narrower than `classifyFault`: a "0 failed" summary or "No results found" is never a failure) or a request was refused; it times out after `CONXA_COMPLETION_BUDGET_MS` (5 min). A request left open with nothing on screen is given `CONXA_NETWORK_GRACE_MS` (30 s; an upload gets the full budget) and then reported done with a warning. Bars that each show a fixed number (aria-valuenow, data-progress, a `<progress>` value) and stay unchanged are a picture, not work: a `short` wait lets them go after 3 s, a `full` wait after 20 s (the stall the compiler writes for a step that is not long), an upload never; the answer is `done` with `still`, so it counts as `cant_tell`, never a yes. A spinner or progress words still hold the wait (EXEC-61: GitHub draws its language and pulse charts as `role=progressbar`). A failed or timed-out gate throws a `fault` (`faultKind: action_failed` / `unfinished`) that skips the recovery cascade, because the action already happened and a retry could upload or submit twice. A decision popup or native dialog is handed off, never waited out or answered. Kill switch: `CONXA_COMPLETION_GATE=0`. One `completion` telemetry event per gated step. **Silence is not success (Proof of Done stage 1).** A `full` wait counts quiet as "done" only after a first sign the app took the work (a busy indicator, new status text, a data-changing request, a new address); with none inside `CONXA_COMPLETION_START_GRACE_MS` (3 s) it answers `no_sign`. A page that cannot be read answers `cant_tell`, never `done`. `no_sign`, `cant_tell` and a `full` wait that is still working become a plain run warning, never a bare pass; a `short` wait only records the event. The wait takes the run's `deadlineAt` and `cancelCheck`: it stops 20 s before the deadline with `still_working` (a `full` step fails with a plain "ran out of time" message so the answer still reaches the caller) and a cancel ends it at once. Progress trays are told apart from questions by `fork.js::isProgressSurface` (progress wording or flag, and every button is a tray control such as Minimize / Close / Cancel all), mirrored in `outcome_fork.py::is_progress_surface` and pinned by `decision_surface_cases.json`. A `full` wait also looks once more for a decision popup before saying `done`. A popup that **blocks** the page (`fork.isBlockingDecision`) and was left by the plan's final step goes through the unrecorded-popup rules (`askAboutBlockingPopup`) or stops the run, never a pass (the error carries `leftOpenSurface`: title, buttons, `blocks`). A decision-looking panel that leaves the page usable does not stop the wait: the gate returns `done` with `leftOpen`, and `run.js::proveStep` judges the step by its own recorded checks first. If the panel's words are among the step's recorded words (`effect.words`, `new_text` checks) it is the recorded result and ignored; otherwise the run carries on with a plain warning and `proof.steps[i].left_open`. A learned progress word never makes a text that carries the app's success wording count as work in flight (`completion.js::classifySample`). Not yet built: a Tier B classify-only question (notice or question) for live runs and a hand-over to the person in tests. **Honest answers (Proof of Done stage 2).** `assertions.js::verifyStep` and the gate now return one of four answers per check (`yes`/`no`/`not_yet`/`cant_tell`): a check that passed only because it could not look (no baseline, an unreadable page, no assertions) is `cant_tell`, and a native dialog in the way is `not_yet`. `run.js::proveStep` collects those answers into checks on the proof ladder (`performed` < `accepted` < `completed` < `result_exists`) and `proof.js::judge` returns a verdict by fixed rules (a failure signal first, then enough distinct witnesses at the required rung, then still-working, then couldn't-confirm). A step with no `effect` block needs `completed` where the gate waits (an upload, the plan's last acting step), `accepted` with a required assertion, otherwise `performed`. `proof.js::nextMove` is the next-move table: "couldn't confirm" on a step that is not safe to do twice (`destructive`, effect kind `upload`; `proof.js::effectKind`), or on any step of a scheduled run, ends the run (`couldntConfirm`, a `fault` that skips recovery); on a low-risk step it carries on with a plain note. Nothing is asked of a person unless the author set `on.unverified`. The result's first words and `structuredContent.outcome` report it (Backend-Schema section 3.4z); `verify_result` is emitted on every verify path and `proof` once per judged step. **Evidence notebook (Proof of Done stage 3).** For every gated step `run.js` opens `evidence.js` just before the action (beside the completion baseline) and closes it once the verdict is in, and again when the plan ends. While open it reads, on every page this run owns (the tab registry's pages, never the whole browser context: a borrowed host browser also holds other runs' views, so a context-wide listener would fail this run for another run's refusal, RT-10) and in every visible frame (a frame whose `<iframe>` is hidden is ignored), the completion probe, plus request and response metadata (method, a scrubbed route template from `route_template.js`, status, kind, size), downloads, new tabs and address changes. A small JSON reply to a data-changing request is read once and reduced to `{top_keys, error_flag}` (identifier-like key names only; an `error`/`errors`/`success:false`/`status:failed` body, other than a GraphQL reply with usable data, is a failure even on a 200) and discarded. Entries hold no query string, id, header or body, are capped at 300, and never leave the computer. `completion.js` reads the notebook when given one (`notebook` option) and falls back to the single-page probe otherwise; `completionProbe` also reports a MODAL dialog that is really a progress window (a progress bar, or progress wording). The notebook only observes. **Step checklists (Proof of Done stage 4).** The compiler gives a step that starts work a `validation.effect` (Backend-Schema section 3.4y): `compiler/effects.py` classifies the kind from the action and the button's words, and mines checks from the recorder's `post_watch` (what the app displayed when the work finished), the status words it said, and the kind of action, plus a generic "a data-changing request was answered" check that works on any website and an always-present failure veto. `required_rung` is capped at what `runtime/app/effect_probes.js` can answer today; the quorum starts at one witness. `run.js::proveStep` appends the answers to the legacy checks, so a veto beats everything and an older step is judged exactly as in stage 2; with the completion gate off the checklist is not used. A veto ends the run as an ordinary failure (the app said it failed), never as "couldn't confirm". A clean test run calibrates the checklist; the author's agreement in chat confirms it; publishing a pack whose steps cannot be undone requires one or the other. **Choose files, then press Upload (BUILD-103).** On many pages picking files sends nothing; a later button sends them. The recorder logs when each data-changing request left a recorded page (`requests.json`, method and time only). `effects.py::choose_file_presses` pairs a pick that sent nothing and after which the app did not react with the first later press on the same tab and address that sent a request. The pick is built with effect kind `choose_files` (no synthesized `state_changed`, required rung `accepted`); the press gets kind `upload` and the upload checklist. At runtime the rules follow the effect kind (`proof.js::effectKind`): the press gets the `full` gate and is never repeated, the pick is not gated, is safe to repeat (`step_utils.js::isNonIdempotent`), and is proven by the file box read back after `setInputFiles` (`page_scripts.chosenFileNames`, `handlers.js` upload). A pick whose box does not hold exactly the chosen files fails; on any other upload an empty box is only `cant_tell`, since a page that sends files on pick often clears its box. `input_has_files` never answers "yes" because the step ran. A recording without the request log compiles as before. **Long waits are parked, not failed (Proof of Done stage 5).** When a `full` step's work is still going as the call's own time limit nears (`still_working`, reason `deadline`), `run.js` throws `proofPause` instead of failing it. `server.js` parks the live page (`proofStepIndex`, `proofRecheck` = the page's state before the action, TTL `CONXA_PROOF_PARK_TTL_MS`, 15 min) and returns `Submitted.` with `structuredContent.outcome: "submitted"`, `resume_from`, and `_meta["conxa/proof_wait"]`. Calling `execute_skill` again with that `resume_from` adopts the park and sets `__proof_recheck_<i>`; `executeOneStep` then RE-CHECKS the step (a notebook opened against the saved baseline, the same gate and the same rules) and never clicks or uploads again; it may finish (`Done.`), fail, or park again. A pause inside a loop, a conditional step or a dry run still fails as before, because there is no cursor to resume from. The scheduler goes back itself (`scheduler_daemon.js`, every `CONXA_PROOF_RECHECK_WAIT_MS` up to `CONXA_PROOF_RECHECK_MAX` times) and reports "Couldn't confirm in time" as a failure if it never finishes. **The app's own words (Proof of Done stage 6).** The recorder's post-action watch also keeps the value-free words the page said WHILE the work ran (`post_watch.progress_words`, letters of any language, so a German "gelöscht" is one word). `compiler/effects.py::app_words` keeps the distinctive ones (five letters or more, letters only, a word used for two meanings dropped) on the effect, `app_graph/extract.py` carries them to the app map (`AppGraph.signals` {progress, success, failure}, a fold like the rest), and `app_graph/knowledge.py` ships them per host in `app_knowledge.json`. `runtime/app/app_knowledge.js::signalsFor` turns them into whole-word regular expressions for the host a page is on; `completion.js` counts them beside the English progress words, `fault_classify.js::classifyCompletionFailure` beside the English failure words, and the failure veto of a step's checklist uses the same. No LLM is involved and the map is always rebuilt, never edited. The cloud lists steps whose proof keeps coming back unconfirmed (`proof_health_by_step` in the dashboard payload, from the `proof` events: results only). **Going to look (Proof of Done stage 5).** An upload with a single named file gets a `fresh_look` check at rung `result_exists`. `run.js` counts the file's name on the page BEFORE the action (`evidence.open({fileName})` → `nameCountBefore`); afterwards `fresh_look.js` opens the same address in a separate tab (a GET of a page already open), counts the name again until it appears more often than before or `12 s` pass, then closes the tab, removes it from the tab registry's pending list (so the run can never bind it as a site-opened tab) and returns focus. It never clicks or types. Where a tab cannot be opened (a borrowed Conxa Execute browser) or the address is not an http(s) page the answer is `cant_tell`. It does NOT raise what the step requires by itself: `effects.py` caps `required_rung` at the probes shown to work, and `calibrate_effect` lifts it to `result_exists` only after a clean test run saw this check answer "yes" on this site (a check that did not is marked dead and ignored), so a site that never lists the new file cannot make a working upload stop with "couldn't confirm".
- **A witness must be able to say no (2026-11-13, false "Done" on a Drive upload).** A step is proven only by evidence that would have read differently had the action done nothing. Three rules in the existing ladder: (1) a data-changing request counts only when it is not **background traffic**: `evidence.js::ambientWatcher` learns, from every earlier step, the endpoints (method + host + route template) the run's pages already call; a notebook gets that set as it stood just before the action and marks those requests `amb`, so they neither prove `request_chain` nor start the completion gate (`mutationStarted`). An app that sends everything through one endpoint is all ambient: its requests prove nothing and the other witnesses must. (2) The recording is the expected outcome: when every `new_text` check (the words the recording saw when the work ended) answered "no", `proof.js::judge` raises the bar to `result_exists`, so only a witness that found the result itself (`fresh_look`) proves the step; otherwise `couldnt_confirm`. "Couldn't tell" is not absence. (3) A test run learns only from a pass that rested on a check at `completed` or higher that answered yes: `effects.py::calibrate_effect` then marks checks, stamping `calibration_basis`; "couldn't tell" never retires a check, and the recording's own words and checks the author confirmed are retired only by the author. A `calibrated`/quorum mark without a `calibration_basis` is ignored at load, so skills already marked by a false pass are repaired without touching them. The function an `evalOn()` runs in the page must come from `page_scripts.js` (`check_page_eval_seam.js` enforces it): every other app module is string-array obfuscated, and an inline function throws `a0l is not defined` in the page, which the caller's catch hid until it read as "fine".
- **What the runtime learns from a watch, and how a failure is named.** The recorder (`bridge.js::_runWatch`) keeps a text as a *progress* word only if it was gone by the time the work ended; text still on screen at the end is the result (`final_status`). `compiler/effects.py::app_words` also never keeps a word that says work is finished as a progress word, and never keeps a word from the person's own data in the step (a typed value, a chosen file's name). Because words are kept without their phrase, an older recording can still hold a result word such as `upload` as progress, so `completion.js::classifySample` never counts a text that says the work is finished (`FINISHED_RE`) as busy through the app's *learned* words; the English defaults still decide alone. A failed step carries a kind read from the flags `stepFailure` already sets (`failure_response.js::failureKind`: `decision_needed`, `couldnt_confirm`, `fault:<kind>`, `bad_input`, `tab_not_found`, then the find-failure kinds), plus `left_open_surface` and `action_may_have_taken_effect`, in `evidence.json`. Python reads that record (`test_flow.py::_tf_run_facts`) instead of the message text: the card's plain line (`failure_summary.summarize(message, kind)`), whether it may say "nothing was saved or sent", and the route after the failure. A deliberate stop is never sent to a model: one `needs_answer` card states the cause; only find-failures go to the diagnosis call, which posts one message and, when it names a change only a person can draw, the same Human Edit link card as chat (`harness.post_editor_hint_card`). `build.py::_build_structural_fingerprint` keeps only landmarks that are on the start page (the step whose click moved the page still counts; a later page or another tab does not), because the runtime checks them before step 1. In Dev, `conxa_runtime.py::stale_app_layer_files` reports app-layer sources edited after the staged copy was built, and a test says so before it starts.
- **Evidence must be tied to the step (2026-11-14, a Drive upload that worked but ended as "popup" and "still working").** Three measured links, never words or sites. (1) **Motion:** `completion.js::waitForCompletion` takes `stallMs` (the step's `effect.time.stall_after_s`); an indicator whose reading (`completionProbe` now returns each bar's value as `bars`, and `busyWhat`, a short description of the busy element) has not changed for that long is on screen, not working, so it no longer holds the step. Such a step ends `done` with `still: true`, which `run.js::gateAnswer` counts as `cant_tell`, never `yes`: quiet is not proof, so the step is then proven only by another witness (a data-changing request answered, a fresh look). Work seen to end (an indicator that cleared, a request that closed) is still a `yes`. A verdict that stops on a busy element now names it (`busyWhat`, tracker `bw`). (2) **Cause:** `dialogProbe.blocks` is measured (it now also returns `blocks_reason`); a popover mounted outside a fixed window-sized app shell is no longer read as a backdrop, because a backdrop must hold little text whether or not the dialog sits inside it. Before a run stops on a popup nothing answers, `run.js::parkAndRecheck` takes the pointer off the page once (`page_scripts.js::neutralPoint`, a spot that reaches only the page) and looks again after 400 ms; only a surface that is gone is excused (a hover card closes when its hover ends), a native dialog never is, and nothing is clicked or answered. (3) **Contrast:** every `text_present` is compared with how many times the text was on the page before the action (`assertions.js::watchedTexts`, `capturePreStepSignature(page, texts)`); on the same address, finding a text that was already there and is not there more often now answers `cant_tell` (the check still passes, as before, but lends no proof). At compile time `validation_planner.py::_merge_tokens` applies the existing echo filter to intent-derived words too, so words from the clicked control's own label ("download", "file") are never offered as results. **Studio's test browser (`conxa-builder/electron/browser_panel.js`)** lays a transparent shield view over a test run's page so the person's mouse and keys do not reach it (Electron's `before-mouse-event` cannot be used: it fires for the runtime's own CDP input too, spike 2026-11-14). The shield loads `about:blank?conxa_shield=1`; `runtime/app/tabs.js::isShieldPage` keeps it out of recorded-tab binding. It is lifted at `run_end`, when a recorder adopts the run, and over a person's own tab. Not built yet: see TODO EXEC-58, BUILD-83, BUILD-84, EXEC-59.
- **The author's own words prove completion (2026-11-16, a form that answers in the page alone).** `text_present`/`text_absent` mean "contained in the page's text, any letter case" (`assertions.js::textSelector`, `internal:text="…"i`), the same meaning as the before-count `countTextOccurrences`; an exact whole-element match never found "Asha Rao" inside "Name:Asha Rao". A `text_present` result carries `fresh: true` when its words were counted before the action, were absent then (or occur more often now on the same address), and are visible now, both measured by `countTextOccurrences`, or when the page's own text (without what boxes hold, `countPageText`, `pageCounts` in the baseline) has more of them than before: a form that saves a row moves the typed words out of its boxes into the page, so the count with boxes stays the same while a click that did nothing would leave the page text unchanged (WF-11). `proof.js::assertionRung` places a **required** fresh `text_present` at rung `completed` (only a person makes a text check required: the `done_when` answer, chat or Human Edit; the compiler writes them advisory), every other assertion at `accepted`. So a `send` step on an app that makes no data-changing request (client-side forms, apps that save locally) can be proven by the words the author said mean "it worked"; text already on the page, or present only hidden, never can. The `done_when` question offers "You see the <input> on the screen" whenever a step has completion checks to agree to, not only beside a compiler-written text check.
- **A new tab is a change (2026-11-16).** `state_changed` also answers yes when the step's page opened a new tab or window since the baseline (`assertions.js::popupsOpened`, Playwright's per-page `popup` event, counted from the first baseline on that page; never the whole context, which a borrowed host browser shares with other runs). A click whose whole effect is opening a tab leaves its own page unchanged, so the address/element/text comparison alone failed it every run.
- **A fresh required check is a change (WF-13 F6, 2026-11-20).** `verifyStep` evaluates `state_changed` last; when a **required** check on the same step already answered yes with `fresh: true` (new words, a box that was there and is gone), the page did change, so `state_changed` answers yes without polling (`proven_by: "fresh_check"`). A short result ("Name:person" after Submit) sits under the 20-character text tolerance, adds no element and keeps the address, so the counters alone failed a step its own required check proved. Words that were already on the page are not fresh, so `state_changed` still decides then.
- **Full audit:** every assertion is evaluated; `results: [{type, target, required, ok, elapsed_ms}]` is returned. The primary path emits one `verify_result` event per step with assertions (`{si, ok, n, advFail}`). A required failure attaches `verifyResults` and `earlyDomSnapshot` to the error.
- **Fleet view:** `tracking._assertion_health_by_step` powers `assertion_health_by_step` on `/tracking/dashboard` (Self-healing page `/dashboard/healing`, worst first) and feeds the health score. Each row carries `previous_pass_rate` (same step, previous equal period — `tracking_analytics._with_previous_pass_rate`) so decay reads as a change, not just a low number.
- **Editor:** `StepConfigForm.tsx`'s Validation card (`components/validation/AssertionEditor.tsx`, used by the Human Edit step form) saves via `patch_step`. `patch_gate.py::validate_editor_patch` runs in `cmd_patch_step` and rejects removing a consequential step's only required assertion.

- **A click is proven performed only if the page says it was received (2026-11-15).** `handlers.js::clickWithWitness` arms a trusted-click listener on the window (`page_scripts.js::armClickWitness`), clicks, and reads it back (`readClickWitness`): `received`, `elsewhere` (another element got it, named), `none` (nothing did) or `unknown` (the page cannot say: the element is gone, the page was replaced, a cross-origin frame). Only `elsewhere` and `none` fail the step, as an intercepted click (`clickNotReceived`, so `withLocator` does not re-click and the dismiss ladder in `cascade.js` gets its trigger); `elsewhere` also marks `mayHaveActed`. Tests: `test_click_witness.js`.
- **A thrown action is not an acted action (2026-11-15).** `locators.js::actAndMark` no longer marks `mayHaveActed` when Playwright gave up waiting for the element to be visible/enabled/stable/unobscured (`neverDispatched`): nothing was dispatched. And the settle branch in `run.js::runStepFrame` accepts "only re-verify" only when at least one real check ran and held: a step with no recorded check passes `verifyStep` trivially, which used to let a click that never happened end as "Done, please check". Reproduced with `docs/testing/fixtures/dismiss-fixture.html` (a covering banner left no click on the page yet the run said Done); now a covering banner without a known dismissal fails with "Another element was covering the target", and a known one is dismissed and the click lands.
- **Reveal by hover (GATE).** When a target stays hidden for the whole GATE budget, `resolution.js::revealByHover` hovers its nearest visible ancestors (`page_scripts.js::revealHoverPoints`, at most three, innermost first) and waits again. A hover chain is compiled only when the recording held a hover (capture is off by default), so this covers menus and row actions that exist only while hovered. A target that never shows still fails with the original error. Fixture: `docs/testing/fixtures/hover-fixture.html`.
- **`value_equals` reads rich-text boxes.** `page_scripts.js::readFieldValue` returns `value` for input/textarea/select and `innerText` for a contenteditable (`inputValue()` throws on those and the error was swallowed, so Gmail's body could never pass). When the stored selector finds nothing (generated ids), the check resolves the step's own identity (`resolveStep`) for the field it typed into.
- **Re-verification inside recovery is capped.** Each recovery stage that re-dispatches an action re-checks a required assertion for at most `RECOVERY_VERIFY_CAP_MS` (15 s) instead of the 60 s floor. The retry itself stays: a failed post-condition is how a wrong first resolution heals (`cascade.js::guardDecision`).
- **Navigation address with an empty placeholder.** `handlers.js` `navigate` refuses an address that still contains `{{...}}` after interpolation (`interpolate.js::leftoverPlaceholders`) as `badInput`, instead of opening `%7B%7B...%7D%7D`.
- **A drag needs both ends.** `drag_drop` with no `src_selector` or no drop target throws `badInput`; it used to return and report Done.

**Native dialogs at replay.** The loop looks one step ahead. When the next step is `dialog_accept`/`dialog_dismiss`, it arms `page.once("dialog", …)` before dispatching the current step, so the dialog resolves the instant it opens (`docs/Backend-Schema.md` §3.4h).

### 9.6 Safe Actions (PROD-3)

- **Destructive classification:** `destructive_semantics.classify_consequence()` marks click steps whose intent matches the destructive vocabulary, or commit/submit actions. A button whose own words only move forward (policy `navigation_text_tokens`: Continue, Next, Back, Sign in; whole words) and name no commit word is not a commit (`action_semantics.only_moves_forward`), so it is reversible and is checked by its page change. Serialized as the execution step's top-level `destructive` (`_copy_saved_common`) and `consequence` (`reversible`/`irreversible`/…).
- **No re-identification on destructive steps:** `cascade.js::recoverStep` skips only the a11y re-probe (which resolves by accessible name and could land elsewhere) and emits `destructive_reidentify_skipped`. Same-selector stages still run. The EXEC-24 action guard separately blocks re-dispatch when the first attempt may have landed. `run.js` sets `destructiveHalt` from `step.destructive` whenever recovery fails (`test_cascade_destructive.js`).
- **Entity binding** (`compiler/entity_binding.py`, `EntityBinding` on `SkillStep`): for targets inside repeating rows, a `container_selector` matching every row plus an `identifier` (preferring a run input over the recorded text). `resolution.js::entityRoots` requires exactly one matching row; zero or several → `entityNotFound`, never the unscoped page. Primary and recovery resolution both narrow through it. A row matches only when it shows the identifier as a whole value, compared without case or extra spaces (`page_scripts.js::rowsHoldingWholeValue`, one `evaluateAll` over the rows): the row's own text (a `for_each` row), one element's text (a cell) or one field's value. Never a substring: "Carol" is not in the row "Carol Ann" (WF-7 7.swap). So an upgraded identifier keeps the recorded text around the typed part (`template_identifier`: "Invoice #{{invoice_no}}"). A `text_present` check whose words end in a bound identifier (`step._bound_values`, set by `runPlan`) is found only where those words end a line, with closing punctuation allowed (`countTextEnding`): "Saved Carol" does not pass on "Saved Carol Ann".
- **When a binding is created (2026-11-15).** `entity_binding.py::detect_entity_binding(ev, dom_html, destructive, address_unique)`: a step that cannot do harm and whose own address is already unique and durable on the recorded page (test id, attribute, role: durability >= 0.9) gets none; the identifier must appear in exactly one sibling row of the recorded page (full snapshot first, the 2000-character ancestor chain only as a fallback), must not be the control's own text, and prefers words over bare numbers and prices. A step that can do harm keeps its binding even when no text names the row (best guess, `confirmed:false`) and Clarify asks `confirm_row` ("is this the row you mean?", a safety kind: never dropped or pre-answered; yes patches `entity_binding.confirmed`, no changes nothing and points to Human Edit). Previous behaviour picked the longest text with a digit, so two products at the same price made `Bound record could not be uniquely located`.
- **A row the person's own typing produced (2026-11-18, WF-7).** Before any of the rules above, a data row (`tr`/`li`/a row-named grid item) that shows, as a whole value, something typed earlier in the recording (`typed_values`: type/fill only, never a password) is bound by that value, even when it is the only row: a search that found one record, or the entry just added. `upgrade_entity_binding_identifiers` then makes it the run input (`{{search_by_name}}`), so a search that finds nothing, or leaves only a look-alike elsewhere on the page, stops at that step instead of acting. Text a later step types over (`edited_values`: the field's starting value, e.g. an inline-edited email) is never picked as a row's identifier, so the next run can still find the row. The runtime tells "more than one row shows it" (`entityRoots(...).several`, message `Bound record matches more than one row`) from "isn't on the page". The page snapshot can be saved just after the action changed the row (Edit became Save, the email became a box), so the recorded row is matched among the saved rows by the identifying text they share (`_same_row`), not only by containment; otherwise its own changed copy counted as "another row named Carol". Backstop: a control repeated on the rows of a list (`row_action_in_list`: its own words appear in other rows) that still gets no binding and has no durable unique address loses its positional signals (`POSITIONAL_ENGINES`: `xpath`, `css-structural`), so the look-alikes tie at the uniqueness margin and the run refuses instead of acting on whoever is now in that place. The agent stage obeys the same rules (`resolution.js::validateOverrideSelector`): an agent selector is checked inside the bound row only (a bound row that is missing or shown twice refuses it, `entity-not-found`), and one that matches a single element is still refused when the step's own signals find two or more elements within `uniqueMargin` of it (`lookalike`): `>> nth=2` or a numbered candidate cannot choose among identical row buttons. Clarify asks `missing_item` once per bound row and applies the answer to every step on it; unticking the value in `values` sends `drop_inputs.literals` so the row is named by the recorded text again.
- **Repeated clicks (2026-11-15).** The recorder emits one event per physical click. `clean_steps` keeps three or more identical consecutive clicks as one step with `repeat_count` (two still fold, since a double-click is two clicks plus a dblclick); the package builder emits `type: "repeat_click"` with `count` (capped at 200). It is a new step TYPE so a runtime that predates it fails closed on `Unrecognized step type`. Contract: `action_contract.json` `packager_only`. The runtime clicks `count` times through `clickWithWitness`, stopping at the first failure.
- **Unclassified clicks in a dry run.** A top-level click with no recorded classification (a hand-added step) is packaged with `consequence_unknown: true` and a dry run withholds it like a destructive one.
- **Halts are terminal:** `destructiveHalt` and `entityNotFound` are excluded from `parkable`, and `failure_response.js` returns a plain message, not a candidate digest.
- **Publish gate:** an irreversible step with an unconfirmed detected entity binding can't be saved (`patch_gate.py::irreversible_step_requires_confirmed_entity_binding`) or published (`handlers/workflows.py::_require_confirmed_entity_bindings`).
- **Dry run** (`dry_run: true`): destructive steps are resolved (fail closed if not findable) but not executed or verified. Skipped steps are returned as `dryRunSkipped` (`{index, intent}`), and `server.js` names them in the response.
- **Stage-then-commit lint** (`compiler/commit_ordering.py::lint_commit_ordering`): advisory `compile_report["commit_ordering_warnings"]` when an irreversible step is followed by another writing step (ignoring `MARKER_ACTIONS` and read-only steps); nearest offender only.
- **Compensation skill:** `SkillMeta.compensation_skill` (a sibling slug set in Human Edit; sidecar `compensation_skill.json` → `manifest.json`). On a failure with `actionMayHaveTakenEffect`, `server.js` names it as an available next step and never runs it automatically.
- **Strict Mode:** §10.1.
- Open: before/after screenshots and a per-skill safety score (`PROD-3-UI`).

Tests: `test_dry_run.js`, `test_cascade_destructive.js`, `conxa-cloud/tests/test_commit_ordering_lint.py`.

### 9.7 Pre-Execution Checks (advisory)

Both run before step 0, never block, and push plain-language messages into a shared `warnings: string[]` that `server.js` appends to `execute_skill`'s response on success and failure (via `err.warnings`), deduped across an `execute_sequence`.

**Drift gate** (`runtime/app/drift.js::detectPreExecDrift`): the pack's `structural_fingerprint` (~3 interactive landmarks, `_build_structural_fingerprint`, carried into `manifest.json`) is located on the live page (testid → aria-label → primary selector → text) and scored with the pure resolver. If ≥ 50% score below 0.5, it emits `drift_detected`. The cloud aggregates these as `pre_exec` in the `/drift` response.

**Environment mismatch** (`runtime/app/env_match.js::compareEnvironment`, pure): compares the recorded `environment.json` (§6.1; carried as `SkillMeta.environment` → skill dir → `manifest.json`) with the live `pageScripts.environmentSignature`:
- **locale:** primary language subtag differs (region-only doesn't warn);
- **timezone:** UTC offset differs;
- **viewport:** width differs by > 25% or crosses a 1024/768 px breakpoint.

Emits `env_mismatch`. Role isn't captured (no reliable source). The exec context doesn't pin locale or timezone; this warns, it doesn't normalize.

**Viewport is pinned (width).** Every run sees the page 1280 CSS px wide (`RUN_VIEWPORT.width`, `runtime/app/page_geometry.js`), the width the recorder records at (`host_view.py::RECORD_VIEWPORT`; `test_run_viewport_matches_record.py` keeps them equal, and keeps `MIN_H`/`MAX_H` equal between the runtime and the panel), whatever size the host window is, so width-based layout breakpoints never differ between record and replay. The height follows Build Studio's pane (viewport `fit: "width"`, §4.7a), so the recording's page height is saved as `environment.viewport.h` and a replay is created at that height (`page_geometry.js::runViewport`, clamped to 480-1440; `server.js` passes it as `viewportHeight`): a launched Chromium gets it as the context `viewport`, and a host panel gets it in the `new_view` control op (`host_browser.js::acquire`). Build Studio's panel fills its pane instead, and every tab of the run, popups included, shares the size; Conxa Execute's older panel ignores `fit` and letterboxes at the recorded height. Height is not fully layout-neutral (virtualised lists, lazy loading): a Studio test at a very different pane height than the recording can differ from a customer run. The viewport mismatch warning still compares widths only.


---

## 10. Recovery Architecture

### 10.1 Two-Tier Cascade & Ceiling

> **Canonical recovery reference.** `README.md`, `AGENTS.md`, `docs/PRD.md`, `docs/App-Flow.md`, and `docs/cost_model.md` link here. There are **two behavioral tiers**. Internal tier numbers 1–4 survive in `CONXA_MAX_RECOVERY_TIER`, `manifest.json`'s `max_recovery_tier`, and `recovery_tier{N}` telemetry. Any doc describing a 4- or 5-tier cascade is stale.

**The split is by problem class, not cost.** Tier A fixes *when* (timing, obstruction: the element was right, the moment was wrong). Tier B fixes *identity* (the element really moved). **Tier A may change when or where it looks, never which element it settles for.** Stages that guessed a different element by text affinity and clicked `.first()` were deleted: a wrong click mutates the page and hands Tier B a state that no longer matches the recording.

**Before the cascade:** `run.js::isAuthFailure(page, steps)` (URL/title heuristic) fails the step immediately on a login redirect (§5.3.7). A login page the recording itself navigated to is exempt: the live URL (query, hash, and trailing slash stripped) is compared against every `navigate` step's recorded `url`. `badInput` errors also skip the cascade (§9.1).

| Tier | Fixes | Mechanism | LLM | Where |
|---|---|---|---|---|
| **A: Reflex** (internal T1/T2) | Timing and known obstruction | L1 exception ladder over all bundle signals (re-resolve, scroll, dismiss-overlay, wait-stable, wait-enabled, wait-navigation), then Layer 2: a11y re-probe (role+name through the matcher, fingerprint-checked, uniqueness-gated), transient retry (+250 ms), re-hover for menus, dialog scope (same selector inside an open dialog) (§10.3) | Zero | `cascade.js::recoverStep`, in-process; CI-guarded by `check_recovery_purity.js` |
| **B: Reasoning** (internal T3/T4) | Identity, and unrecognized obstruction | One **armed** payload: ranked indexed candidate digest, step intent, expected post-condition, executed-steps trace, failure + pre-step screenshots, recording-time reference image when present, overlay probe when relevant. The agent nominates; the runtime verifies | Text + vision | Agent-mediated: `failure_response.js` responds to `execute_skill`; the client resumes by calling it again |

The runtime never calls the model itself for Tier B.

#### Tier B payload

- **Every round is armed.** The first round is the likeliest to succeed, so it gets the screenshots. `recovery_stage.js::nextRecoveryTier` depends only on the ceiling. A step gets **two rounds**; round two carries round one's nomination and what it actually matched.
- **Ranked digest** (`candidate_digest.js`): candidates are ranked against the recorded identity (anchor-text affinity > testid/id > role > tag), then serialized as `[i] role=… name="…"` up to 40k chars, whole entries only. **Rank-then-cap, never positional truncation.** The index is a transient prompt handle, never durable identity.
- **Current-state grounding** (`server.js::_buildFailureResponse`): the inventory is captured live after Tier A ran (remedies can change the page). The pre-cascade snapshot (`run.js::captureEarlyDomSnapshot`) is included, labeled, only when it differs. Also: compiled assertions, which assertion failed on a verify-fail, an executed-steps trace, and an instruction that the live page is ground truth and the reference image may be outdated.
- **The past:** `recorded_context` (parent, siblings, index, enclosing form from `bridge.js::captureAncestors`, emitted into `recovery.json` by `_recorded_context_for_saved_step`, rendered by `recordedContextBlock`); `visual_ref` (the recording screenshot, §10.2); `phase` (`_phase` via `handlers.js::enrichStepsWithRecovery`, rendered by `phaseHintBlock`). The reference image is described only when actually attached, since describing a missing image invites invention.

#### Closing edge: `step_overrides`

`execute_skill` accepts `step_overrides: {"<idx>": {"candidate_index": n, "confidence": 0-1, "why": "…"}}` (preferred) or `{"selector": "…"}`, keyed like `resume_from`, optionally with a `dismiss` field (below).

- A `candidate_index` resolves to a selector derived when the digest was built (`[data-testid]` > `#id` > `internal:role[name]` > text, stored per skill in `recovery_stage.js`). A stale index is reported as `agent_override_rejected {reason: "stale-candidate-index"}` and the next response carries a fresh digest.
- The selector is injected via `_explicit_selector` (`run.js::applyStepOverrides`) and validated (`validateOverrideSelector`) against the recorded fingerprint. A unique match passes. A multi-match passes only if the `scoreCandidate` winner clears the uniqueness margin. No match or a tie is rejected (never `.first()`), and a fresh request reports what the selector matched.
- Overrides are honored only at ceiling ≥ 3, so a stray override can't rewrite a Studio test.

**Page parking** (`recovery_park.js`): recovery spans calls, so on a parkable failure (single run, ceiling ≥ 3, selector/verify failure, not auth/cancel/halt) the live page, context, and browser are parked per `${workspace_id}:${slug}` with a fingerprint (`capturePageFingerprint`: URL + interactive count + visible-text hash) and a TTL (`CONXA_RECOVERY_PARK_TTL_MS`, 180 s). On resume the fingerprint is recomputed. A diverged page discards the park and refuses the override. With no matching park, the resume is refused and the agent is told to restart the skill (never a silent fresh page mid-plan). Events: `recovery_park_created`/`_resumed`/`_discarded`/`_state_mismatch`, `recovery_resume_refused`.

**Stagnation cap:** if the page fingerprint is unchanged across `STAGNATION_LIMIT = 2` rounds, further rounds are refused (`recovery_stagnant_stop`).

**Retry budget:** `RETRY_BUDGET_MAX = 3` per (skill, step index). The map is process-wide and persists across MCP calls. `runPlan` clears the running skill's slice at the start of every call (as well as on success), so a failed run doesn't leave the next run pre-exhausted.

#### App faults stop the run before the cascade (EXEC-49)

A step that failed because the **app** refused is not a moved element, and re-finding it cannot help, so `run.js::detectFault` (after the settle wait, before `recoverStep`) classifies it first and ends the run with a typed reason: `auth_expired`, `no_permission`, `network`, `rate_limited`, `duplicate`, `validation`, `not_found` (`fault_classify.js`, pure; `fc: fault_<kind>`, recovery-log `fault_detected`). Evidence is narrow on purpose: the browser being offline or a response status the page returned **after the step started** (`page_status.js`: a main-frame 4xx/5xx, or an xhr/fetch 401/403/429/5xx; a background 404 is routine and ignored) count for any step; the app's own words (a visible `role=alert` / `aria-live=assertive` region, or the message of an `aria-invalid` field, from `page_scripts.faultProbe`) count only when the step may already have acted, so a stale alert is never blamed on a step that never reached the page. A popup that offers a choice is never a fault, nor is a fork step. A fault is non-parkable (`stepFailure` keeps `fault`/`faultKind`, `server.js` `parkable` excludes it): **no Tier B round is spent.** The sign-in-expiry redirect keeps its own path (`isAuthFailure`, §5.3.7) and its mid-run re-sign-in.

Three more stops added 2026-11-18 (WF-6 false success). (1) **An alert no step answers is a fault.** A native `alert()` in the dialog queue whose next step is not a popup step (`dialog_accept`/`dialog_dismiss`/`outcome_fork`) is closed with its only button, taken off the queue and ends the run as a fault quoting its text (`fault_classify.js::alertFault`: typed by its words, else kind `alert`, "The app showed a message: …"). It is checked right after the action, when the completion gate hands off on a native dialog, in `detectFault`, at the start of the next step and once after the last step (`run.js::failOnUnexpectedAlert`); after the action it never enters recovery. A `confirm()`/`prompt()` is a question and is never answered here. (2) **A dropped connection is a `network` fault.** A data-changing request this step started that failed with a connection error (`ERR_EMPTY_RESPONSE`, `ERR_CONNECTION_RESET`…; not `ERR_ABORTED`/blocked/`ERR_FAILED`) counts like a status (`classifyFault({dropped})`). (3) **A step that sent data is never done again.** When a non-idempotent step may have acted and its evidence notebook saw a non-background data-changing request, but its result never showed, the run stops before `recoverStep` (`fc: sent_no_result`, `recoveryHaltReason: "action-sent-request"`), since every cascade stage would re-dispatch it. Separately, Proof of Done: a `still_working` verdict on the plan's last step (`ctx.lastStep`) is `couldnt_confirm`, since no next step can take it over.

The same flags protect two other deliberate stops from being mistaken for exhausted recovery: a fork `stop` (`forkDecisionNeeded`) and a bad input (`badInput`). Before 2026-11-05 `stepFailure` dropped those flags, so such a failure was parked and handed to an armed Tier B round.

#### Tier B can say "this is not a moved element" (EXEC-49)

`step_overrides: {"<i>": {"verdict": {"kind": "fault" | "decision" | "drift", "why": "..."}}}` ends the run with the agent's reason (`handlers.js::applyStepOverrides` sets `_agent_verdict`; `run.js::executeOneStep` throws it as a non-parkable stop). It is caution-only: a verdict can make a run stop sooner and can never make a step do more. The armed Tier B note (`failure_response.js::verdictNoteText`) offers it.

#### Unrecognized overlays

When an `INTERCEPTED` failure survives Tier A's known-pattern ladder (`dismiss_patterns.js::KNOWN_DISMISS_SELECTORS`), it's flagged `unknownOverlay`. `failure_response.js` then runs `page_scripts.js::overlayProbe`: hit-test three viewport points, walk to the outermost `dialog`/`role=dialog`/`aria-modal`/fixed/sticky ancestor covering ≥ 5% of the viewport, independent of the truncated inventory. Its controls join the digest tagged `[overlay]`, and the agent may add `dismiss: {candidate_index} | {selector} | {escape: true}`.

`dismissAgentNominated` clicks only if `isSafeDismissLabel` passes:
- allow-list: close, dismiss, cancel, skip, not now, no thanks, ×;
- deny-list: confirm, accept, agree, allow, submit, save, delete, remove, pay, buy, order, subscribe, decline, reject, opt-out;
- deny wins; an unlabeled icon button is trusted only inside dialog chrome.

The dismissal is applied once, just before the resumed action, and learned per host (`learned_dismissals.js`) so the next occurrence clears at Tier A for free. Events: `tierb_overlay_dismissed`, `overlay_dismiss_rejected`. Replay only; record-time `try_dismiss` is separate (§11.1).

#### Ceiling

`CONXA_MAX_RECOVERY_TIER` (1–4, default 4). Tier A always runs.
- **MCP → 4:** Tier A, then Tier B for up to two rounds.
- **3:** same as 4 (kept for compatibility).
- **Build Studio sandbox → 2** (`conxa_runtime.py`): no handoff, so a pack is judged on its own merits.

**Strict Mode** (PROD-3): a pack's `manifest.json` may set `max_recovery_tier`. The effective ceiling is `min(host, pack ?? host)` (`server.js::_effectiveRecoveryTier`) and is used for every agent-mediated decision. Status endpoints report the host baseline. Set per build via `CONXA_STRICT_MODE_MAX_TIER` (`skill_package_builder_output.py`); no Studio toggle yet.

**Observability:** `mcp_connected`, `execute_start`, and `get_runtime_status` report `max_recovery_tier`. The recovery log records `recovery_ceiling_reached`, `agent_recovery_requested` (`tier`, `round`), `agent_override_applied`/`_rejected`, `recovery_stagnant_stop`, `retry_budget_exhausted`, and the park events.

### 10.2 Recovery Artifact Delivery

Execution needs code files; recovery needs artifacts, and only on failure. So sync runs two passes and only the first gates anything.

| | Pass 1: code | Pass 2: artifacts |
|---|---|---|
| Content | `_CODE_FILES`: execution, recovery, inputs, manifest, validation JSON | everything else (today `visuals/`) |
| Transport | inline base64 in the delta | one zip per skill, `POST …/skill-packs/{slug}/artifacts` |
| Timing | awaited inside `syncSkillPacks`' timeout: **the execution gate** | started in `finally`, never awaited |
| Failure | `last_sync_errors`, skill shows failed | logged only |

- **The request is the delta.** The machine POSTs the hashes it holds (`artifact_store.haveHashes`) and receives the difference, so first install and update share one path. `artifacts: [{path, sha256}]` rides the delta response, including for `no_change` skills (so old installs backfill).
- **Store** (`runtime/app/artifact_store.js`): `<CONXA_DIR>/artifacts/<sha256><ext>`, beside `skill-packs/` so version pruning can't delete shared artifacts. Content addressing gives a free delta, immunity to renumbering, dedupe, and free resume. `put()` re-hashes before writing.
- **`artifacts.json`** in the active version dir maps paths to hashes (`resolveByPath`).
- **Not rate limited** (`get_skill_artifacts` skips `_check_rate_limit`). Auth is identical. Listed in `PUBLIC_VERSIONED_WORKFLOW_SUFFIXES_POST`.
- Install-time sync passes `artifacts: false`; the first launch collects them. A pack on the legacy unversioned `sync_endpoint` gets none until republished.

### 10.3 IdentityBundle Resolution

`IdentityBundle` is the **single source of truth** for element identity: durability-ranked, orthogonality-deduplicated `IdentitySignal`s plus a scoring `fingerprint`, `stable_hash`, `frame_chain`, `shadow_path`, and `guid_like_attrs`. There's no legacy `compiled_selectors` primary path. Frame roots come only from `identity_bundle.frame_chain`. A pack without bundles fails fast (recompile).

**Compile** (`identity_bundle.py`, `selector_grammar.py`, `selector_score.py`, `selector_filters.py`):
- Signals use Playwright native grammar: `internal:testid=`, named-attribute CSS (`li[role="menuitem"][data-key="19"]`), `internal:role=…[name=…]`, `internal:text=`, relational `>> right-of=`.
- `durability = base_durability(engine) × survival_prior × stability_adjustments`.
- One signal per orthogonality class (test-contract, named-attr, semantic-aria, visible-text, spatial-anchor, structural).
- Gates: uniqueness-at-compile, PII binding, xpath/shadow guard, anchor quality (`is_low_quality_anchor`, ephemeral filtering).
- `stable_hash` (`stable_hash.py`): SHA-256 over tag path + sorted static attributes + AX name, with dynamic classes (focus/hover/active/animation/`is-*`) stripped.
- **Fabricated-name drop:** a role/relational signal whose name matches zero nodes in the recorded a11y snapshot (`Locator.aria_snapshot()` YAML stored as `{"aria_snapshot": …}`; `_count_role` also reads the legacy tree) is discarded (`resolves_to_nothing`). Without a snapshot it passes unverified; a capture failure is recorded once in `binding_errors`.

**Accessible name: one rule, three consumers.** `identity_bundle.py::_accessible_name`, `resolver.js::scoreCandidate` (`fpName`), and `cascade.js::a11yRecoveryName` must move together:
- precedence `aria_label → alt → title → inner_text → placeholder`, plus `label_text` **only** for form controls (input/select/textarea/textbox/searchbox/combobox/listbox/spinbutton/checkbox/radio);
- `inner_text` only for name-from-content roles (`_NAME_FROM_CONTENT_ROLES`: button, link, heading, cell, gridcell, columnheader, rowheader, checkbox, radio, menuitem*, option, tab, treeitem, switch, tooltip), never for combobox/listbox/textbox/searchbox/spinbutton/`<select>`; dropped (not truncated) past 80 chars;
- never the HTML `name` attribute;
- trailing keyboard-accelerator tails (`Alt+C then U`, `Ctrl+S`) stripped;
- no name → no role signal (structural identity beats a fabricated name).

`_count_role` and replay's `resolve_adapter.js::roleLocator` (`getByRole({name})` without `exact`) both match names as case-insensitive substrings; ambiguity is the margin gate's job.

**Replay** (`resolve_adapter.js` + pure `resolver.js`): `signalToLocator` maps each signal to `getByTestId`/`getByRole`/`getByText`/`locator`; `gatherCandidates` pre-collects descriptors; `resolve()` walks signals in durability order. On a multi-match it scores candidates against the fingerprint and accepts the winner only if its margin over the runner-up clears `uniqueMargin` (default 0.15); otherwise it moves to the next signal. `stable_hash` breaks ties. **It never blindly takes candidate `[0]`.** A miss or ambiguity throws into the cascade. Recovery uses explicit-selector mode (`stepWithSelector`).

**Words that are the data are not identity (BUILD-101 (7)).** A captured value read as text with a digit in it ("Rs. 400") is taken out of the step's identity at compile (`compiler/content_free_identity.py`: fingerprint words, every address that names it, the hash name), so a different search still finds the same element by where it sits (its other addresses, label, section heading, role). A label-like text ("Order total") stays. A step never ends with no address. A list item the page offers as a choice ("Amelia Brown" for the typed "Amelia") gets an `entity_binding` with `match: "contains"` + `self_target`; it is inert until the person answers the `pick_match` question yes, then exactly one item holding the words must match (none or several fails closed) and that item is the element (`resolution.js`). **Step identity** is separate from all of this: a step's `step_id` is written once (`conxa_core/models/step_identity.py`), so changing how an element is described moves no stored answer, edit or test result. **Address checks follow the start site:** a `url_pattern` naming the recording's start site is stamped `start_origin` and swapped for the run's actual start site at run start (`runtime/app/start_origin.js`).

Scoring (`scoreCandidate`) weighs `data_testid` > `aria_label`/`role`/name > `inner_text` (≤ 120 chars) > `anchor_phrases` > `position_hint`, with role aliases for form inputs. Weight a candidate can't possibly earn is skipped rather than counted against it: `inner_text` for option-content elements (recorded `innerText` vs live `textContent` never match), and anchors when the candidate exposes no neighbor text. Absence of agreement isn't contradiction. The margin gate is unaffected.

### 10.4 Tier A Details

**Layer 1** (`recovery.js::classifyException` → `layer1Ladder`): one deterministic remedy per error class, then one retry of the primary selector.

| Class | Remedy |
|---|---|
| stale | re-resolve |
| intercepted | dismiss-overlay: Escape → known consent-toolkit accept/close selectors (`dismiss_patterns.js`; accept only, never decline; close entries scoped to dialogs) → host-learned winners (`learned_dismissals.js`, `{CONXA_DATA_DIR}/learned_overlays.json`, 30-day TTL, per-host/global caps) |
| out-of-bounds | scroll into view |
| not-stable | wait-stable |
| not-enabled | wait-enabled |
| verify-fail | `descend-layer2` (retrying the same action can't fix a failed post-condition) |

Learned dismissals are runtime state; the signed pack is never touched. Successes log `tier1_dismiss_pattern`. Dismissal is reactive only, never a proactive sweep.

**Layer 2:** a11y re-probe, transient retry, re-hover-then-retry (walks `handler_hints.hover_chain`), dialog scope (`[role=dialog]`, `[role=alertdialog]`, `[aria-modal=true]`, `.modal`, with the same selector). No Layer 2 stage may act on an element it hasn't verified is the recorded one.

**Re-verify:** every remedy that re-runs the action goes through `recoverWithSelector`, which re-runs `verifyStep` when the step has a required assertion. Success counts only if the post-condition holds again.

**`repair_event`:** on any recovery success the runner emits step id, tier, method, score/margin, `stable_hash`, app-version fingerprint, and drift hint. Ephemeral telemetry only; the pack isn't mutated (§10.7).

**Native dialogs:** a pending `alert`/`confirm`/`prompt` blocks the whole renderer. `recoverStep` refuses to start while `ctx.dialogQueue` is non-empty and fails the step naming the dialog. Normally the one-step lookahead (§9.5) prevents this.

**No unbounded `page.evaluate()`.** Playwright gives `evaluate` no timeout, and the deadline watchdog only checks between operations. Route calls through `page_eval.js` (`evalOn`/`withDeadline`). This is wired into `gateLocator` and `failure_response.js::gatherInventory`; remaining call sites are tracked under TODO.md EXEC-29.

### 10.5 Virtualized Lists: Scroll Until Found

Virtualized grids (AG Grid, react-window, TanStack Virtual, Angular CDK) render only visible rows, so a far row looks like `entity_not_found` or `resolve_miss`.

- **Detect (compile, advisory):** `compiler/virtualized.py::detect_virtualized_container` walks the recorded ancestors for a library class marker or inline overflow + fixed-height style (`aria-rowcount`/`aria-setsize` corroborate only, since `outer_html` is truncated at 2000 chars). A hit sets `handler_hints.virtualized_container`.
- **Resolve (runtime):** `resolution.js::maybeScrollForVirtualization` runs inside `resolveStep` before a miss becomes terminal. Container: the compiled hint → `entity_binding.container_selector` (heals old packs) → the dominant scrollable element, but only for `control_kind === "choice"`. Scrolling uses `page_scripts.js` (`scrollVirtualContainerStep`, `scrollDominantScrollableElement`) via `page_eval.js`.
- **No second loop:** `withLocator`'s PRIMARY retry (~120 ms) re-enters `resolveStep`; the container stays scrolled, so the search sweeps. At the bottom, one reset to top. Re-query uses compiled identity, never DOM index.
- **Budget:** the deadline is extended once (`CONXA_VIRTUAL_SCROLL_BUDGET_MS`, 15000) only when an evidence-grounded pass ran and nothing was acted on (`mayHaveActed` unset). `CONXA_VIRTUAL_SCROLL_MAX_PASSES` 40. `CONXA_VIRTUAL_SCROLL=0` disables it.
- Zero-token, pre-cascade. A scroll-resolved step emits `tier_ok` with `virtualScroll: true`, so it isn't mistaken for drift.

### 10.6 No-Recovery Steps

The compiler writes `no_recovery_block` on steps that are never retried:
- `frame_enter`/`frame_exit`, `tab_open`/`tab_switch`/`popup`: structural markers (the switch already happened);
- `browser_back`/`browser_forward`: history navigation;
- `if_present`/`try_dismiss`/`wait_for_one_of`: best-effort by design (§11.1).

At runtime the step kind decides; the runtime doesn't read `no_recovery_block` or `recovery.max_attempts` (the Copilot's capability manifest says so, §7.4).

### 10.7 Drift Flywheel (admin-gated)

`repair_event`s ingest with telemetry and aggregate into `GET /api/v1/tracking/{workspace_id}/drift` (`_drift_review_queue`), keyed by (workflow, version, step), marked `needs_review`. Pre-execution `drift_detected` events aggregate as `pre_exec` (`_pre_exec_drift_queue`). **Detection is automatic; publishing a fix is always an explicit admin action.** Nothing is re-signed or pushed automatically.


---

## 11. Control-Flow Steps

Control-flow steps are flattened into top-level execution-step fields by `skill_package_builder_saved_skill.py::_saved_step_to_execution_step`, recursively for nested bodies. A step whose required content is missing returns `None` and **silently vanishes** from `execution.json`, so every gate below must reject bad shapes before packaging.

**Version gate.** An older runtime silently no-ops an unknown `type`, which skips an entire branch body or loop. `SkillMeta.required_runtime` (checked with `semver.satisfies` at execute time) is the guard. `skill_package_builder_output.py` still defaults `CONXA_REQUIRED_RUNTIME` to `>=1.0.3` pack-wide; set it explicitly for packs using these step types (see `NOTE(branch-steps)`). The handlers now ship in tagged app layers, so the default can be raised (§17).

### 11.1 Branches: `if_present`, `try_dismiss`, `wait_for_one_of`

Optional interstitials (consent banners, optional MFA, A/B variants) would otherwise look like selector failures and spend Tier B tokens.

| Type | Shape | Behavior |
|---|---|---|
| `if_present` | `{selector\|identity_bundle, timeout_ms, steps}` | If the target appears within `timeout_ms`, run `steps` |
| `try_dismiss` | `{candidates, timeout_ms, fallback_escape?}` | Click the first present candidate; else Escape unless `fallback_escape: false` |
| `wait_for_one_of` | `{options: [{selector\|identity_bundle, steps?}], timeout_ms, required?}` | First option to appear wins and runs its steps. Timeout is a no-op unless `required: true`, which fails normally (and may recover) |

**Runtime** (`handlers.js`, `run.js`): `probePresent` is a non-throwing probe (selector count or `identity_bundle.signals`, polled via `pollPositive`). `runBranchBody` dispatches nested steps through `executeStep`/`HANDLERS`, swallowing errors. It is best-effort: no tab resolution, GATE/VERIFY, recovery, cancellation, or dry-run skip. Nested steps with only a `selector` are normalized by `resolvableBranchStep` into `_explicit_selector` mode.

**Schema:** `SkillStep.branch` holds `steps`, `candidates`, `options`, `timeout_ms`, `required`. Nested entries are saved-`SkillStep`-shaped dicts. The kinds are in `NO_RECOVERY_ACTION_TYPES`, `SELECTOR_ACTIONS`, and `INSERTABLE_ACTIONS`, not `MARKER_ACTIONS` (they have real probe targets). `runtime/test/gate-skill/` has a worked `if_present` example.

**From recording.** The recorder never emits branches; it observes. `bridge.js::detectOptionalContainer` walks up to 10 levels from the target for `[role=dialog]`/`[aria-modal=true]` (always stamped) or a consent-token id/class (`cookie`, `consent`, `gdpr`, `onetrust`, `truste`, `banner`), checked against recent `dom_diff.added` signatures (`_containerAppearedRecently`; unconfirmable still stamps). A match sets `optionality = "stochastic"` and `branch_hint = {kind: "try_dismiss", container_signal}`, carried verbatim to `SkillStep.optional_hint`. Two consumers convert it, both via `second_opinion.py::build_try_dismiss_from_hint` (see the CLAUDE.md invariant):
1. compile time: `suggest_optional` (§7.3);
2. review time: Human Edit's "Optional popup?" question (`ReviewQuestionsDialog.tsx`) → `cmd_confirm_optional_interstitial` → `confirm_optional_interstitial`. This is a structural mutation that bypasses `patch_gate`.

Either way the hint is consumed once, and there's no un-confirm.

**Editor.** All three are insertable. `if_present` bodies are editable via `insert_branch_step`/`delete_branch_step`/`reorder_branch_steps` and `cmd_patch_step(path="branch.steps[N]")` through `_apply_step_patch` (selector gates + bundle rebuild). `validate_editor_patch(in_branch_body=True)` rejects `recovery`/`validation` keys (dead config in a best-effort body). `candidates`/`options` accept quality-gated `branch` patches but have no dedicated UI (`BranchBodyEditor.tsx` covers `if_present` only). `StepEditorDTO.branch_summary`/`branch_steps` project them read-only.

### 11.2 Iteration: `for_each`

```jsonc
{ "type": "for_each",
  "rows": { "container_selector": "table#invoices tr" },  // live DOM scan …
  // "items": "files",                                    // … OR a named runtime input
  "as": "row",                  // body reads {{row_id}}, {{row_index}}
  "max_iterations": 50,         // required
  "on_row_error": "stop",       // "stop" (default) | "continue"
  "steps": [ … ] }
```

Exactly one of `rows`/`items`/`items_rows`. Enforced in `_saved_for_each_step` (compile) and `patch_gate.py::_validate_for_each_source` (against the step's effective, merged `for_each`).

**Table loops (`items_rows`, EXEC-60).** `items` splits one text input on commas, so a row can carry only one value. A loop that needs several values per row names a *table* input instead: `items_rows: "mails"` plus `columns: ["to","subject"]`, and the body reads `{{row.to}}`, `{{row.subject}}` (grammar: one optional `.column` part, shared fixture `runtime/test/fixtures/placeholder_cases.json`, read by `interpolate.js` and `placeholder_grammar.py`). The input is `type: "rows"` in the document (editor, `columns` required, 1 to 12 unique names) and a JSON-Schema array of objects in the pack (`inputs.json`, with `columns`). `run.js::runForEachStep` reads it with `resolution.js::readTableRows`: an array of objects, or its JSON text; every declared column must hold a value in every row (a blank column ends the run before the first row, because an empty `{{row.to}}` is how a message goes to nobody); rows are never merged or dropped (unlike `items`, identical rows all run); the cap, per-row proof, dry-run gates and the loop cursor are unchanged (row ids are positions, so a resumed run finds the same row). `{{row_id}}` is the first column's value. **Fail closed on an older engine:** the packager emits the loop as its own step type `for_each_rows` and adds the `loop_rows` capability to the manifest's `required_capabilities`; an engine that knows the gate refuses the pack before step 1, one that does not fails at the step with "Unrecognized step type" instead of reading the table as text and finishing Done. Studio's test card takes a table as lines (`a@x.com | Hello`, one row per line, `handlers/test_flow.py::rows_from_text`; a saved default, JSON text, is shown as those lines by `rows_to_text`); Conxa Execute shows a small table. When `wrap_repeated_entries` turns recorded copies into a table loop, a check on any body step that names a first-copy typed value (the Add click's "you see Alice") is rewritten to `{{row.<column>}}`, or dropped when it is an optional check on a word of that value (`_template_assertion_targets`, the same rule as `parameterize_literal`; longest value first, and only outside a `{{token}}` already in the check, so "Saved Alice Adams" gets both columns and "alice@example.com" stays the email), so later rows are not checked against row 1. The app layer's `capabilities` list is written by both builds (`scripts/build-app-local.ps1`, `build-runtime-app.yml`) from `runtime/app/version.json`, never a copied list (`test_app_capabilities_source.py`). **Chat makes and changes table loops (EXEC-63).** `for_each.items_rows`/`columns` stay out of `_LOOP_FIELDS` (a single patch cannot swap the one row source); instead two structural ops, each one code-owned write on one change card: `set_loop_table {step_key, columns}` (`workflow_mutations.py::set_loop_table`) turns an `items` loop's input into a `rows` input with those columns (same id and label; a comma default fills a one-column table) or changes a table loop's columns (recorded rows are kept only when every row still has every column), and switches the loop to `items_rows` in the same write; the body is untouched (`{{row_id}}` is the first column). It refuses a loop that reads rows off the page, a column a body step still uses, and an input used outside the loop. `make_table_loop {from_step_key, to_step_key, columns: [{name, step_key}]}` (`make_table_loop`) wraps recorded steps into a table loop by the same rewrite as repeated entries (`wrap_repeated_entries` with one copy): each named type/fill step's value becomes `{{row.<name>}}` and the list starts with the recorded row (no default when a value is unknown). The model names steps and column names only; the gate dry-runs the same mutation (`copilot_proposals.py::apply_table_loop_op`) and Apply runs it.

**Repeated entries become a table loop (2026-11-16).** `compiler/loop_suggestion.py::detect_repeated_entries(steps, literal_of)` finds the longest run of 2+ back-to-back copies of one block whose action/element sequence matches (`repeat_signature`: action + `step_key` base, so a fresh copy of the same form matches) and whose typed values differ in at least one position; identical copies are not a list. Differing positions become columns (named from the first copy's input binding, `_2` suffix dropped; at most 12), values typed the same in every copy are `fixed`. Clarify asks it first (`repeat_rows`, `_PRIORITY` -0.5, cost 1.0 so it is never assumed; reworded by the model like any question, never decided by it) and leaves the run's values out of the `values` question. **Yes** runs the `wrap_rows_loop` op: `editor/workflow_mutations.py::wrap_repeated_entries` re-checks the repeat on the live document (`suggestion_stale`), keeps the first copy as the body with `{{row.<column>}}` in values and in checks that named them, restores fixed values, removes copies 2..N, declares a `rows` input `entries` whose default is the recorded rows (JSON text), drops the old per-copy inputs, one undo entry. Questions about the removed steps are marked superseded ("Now part of the repeated steps") and the re-check asks the new ones. **No** patches every step in the run back to its recorded value and drops their inputs. Because an `agent_span` refuses to run inside a loop (`run.js`), a `confirm` question on a `for_each` whose body holds an irreversible step inserts the approval once, before the loop. The test guard still pauses inside the body with its row cursor. Human Edit's review dialog offers the same question while its card is open (`ReviewQuestionsDialog.tsx`, Yes = `list`, No = `same`) and answers it through that card, so both places reach one answer; an answer that fails is handed back to the conversation.

- **Real execution per row.** `for_each` is intercepted in `executeOneStep` and calls `executeOneStep` for every body step per row, so the body gets tab resolution, GATE/VERIFY, settle retry, full recovery, cancellation, and dry-run's destructive skip. This is unlike `runBranchBody`.
- **Row sources:** `resolution.js::enumerateRows(page, spec, cap)` lists every matching row with its full trimmed text as the identifier, **once, before the loop** (re-enumerating a mutating list skips or double-processes rows). `splitListInput(raw, cap)` splits an input on commas (or takes an array), trimming, deduping, and capping identically.
- **Per-row scoping is entity binding:** body steps carry `entity_binding.identifier: "{{row_id}}"`. `inputs.<as>_id`/`<as>_index` are set per iteration and restored (or deleted) after the loop. `entityRoots` requires exactly one matching row, so duplicate row text fails closed. An `items` body usually has no binding, and `entityRoots` no-ops.
- **Caps:** `max_iterations` is required at compile time and clamped at runtime by `CONXA_MAX_LOOP_ITERATIONS` (default 100, `run_config.js`).
- **Errors:** `on_row_error: "stop"` halts on the first failed row (a silently skipped row is an unnoticed partial write); `"continue"` is opt-in. Cancellation and a hand-over pause always propagate. The loop pushes to `state.warnings` when it finds zero rows/items or finishes with failures under `continue`.
- **Entity binding for every step:** `build.py` detects bindings on all steps, not only irreversible ones. The publish gate stays irreversible-only but recurses into `for_each.steps`.
- **Loop variables are never inputs:** `upload_binding.py::for_each_loop_variable_names` excludes `{{<as>_id}}`/`{{<as>_index}}` (and, for a table loop, `{{<as>.<column>}}`) from auto-declared inputs in both `reconcile_inputs_with_step_values` and `_merge_saved_inputs_with_execution_placeholders`, alongside the `downloaded_file*` family.
- **Editor:** insertable (category `iteration`), scaffolded by `_new_manual_step`, validated by `_validate_for_each_patch` (`steps` rejected on the parent patch). `StepEditorDTO.for_each_summary`/`for_each_steps` project the body read-only (`"{skill_id}:{i}.for_each.steps[j]"`). No dedicated nested-body UI yet (PROD-3-UI).

**`download_observed` fails loudly.** The marker throws `badInput` when no download arrives, when the race resolves nothing, or when the entry has no `.path` (`saveAs()` failed). So a download loop stops on the first missing file instead of "succeeding" with partial or no downloads.

**Download lifecycle (2026-10-24).** Studio's panel (`conxa-builder/electron/browser_panel.js::_hookDownloads`) tracks each download as `progressing → completed | cancelled | interrupted` with received/total bytes, pushes the list to the renderer on the `panel:tabs` message (`downloads`), and `BrowserPane.tsx` draws a Chrome-style Downloads button and list (cancel, retry, open, show in folder, clear). `saveDownload` fails with a typed prefix (`cancelled:`, `interrupted:`, `timeout:`, `empty:` — size 0 or ≠ reported total). The recorder turns such a failure into a `download_observed` event with `payload.status` (`cancelled|interrupted|timeout|empty|failed`) and `reason`; a payload with no `status` means completed. `_BindingState.add_download` skips non-completed downloads, so no upload binds to a file that never existed. At replay `server.js` keeps the failure reason (`{error}` queue entry) and the handler reports cancelled / interrupted / empty / timed-out distinctly; `CONXA_DOWNLOAD_WAIT_MS` (default 120 s) is the budget for a delayed download, and on a host panel it covers the transfer too, so a stalled download ends as "did not finish downloading within 120 seconds". `host_browser.js::_controlPost` relays the host's `{error}` text on a non-200 reply (a bare "HTTP 500" hid the typed prefix), and a `save_download` the host never answers becomes `timeout:`. Known limit: a replay that succeeds where the recording failed shifts later `downloaded_file_N` numbers by one.

**Second opinion guards relevant to loops:**
- `parameterize_literal` refuses any `value` already containing a `{{placeholder}}` (`PLACEHOLDER_RE`), so auto-bound download placeholders survive.
- `flag_noise` never archives a step that triggers a download or file chooser. `_review_inputs` computes `causes` (`file_download`/`file_chooser`) by looking past markers to the next real step; the prompt and `_validate_findings` refuse such findings, and `archive_flagged_steps` independently recomputes the lookahead and refuses (the structural guarantee).

#### "Generalize to a loop" suggestion

`compiler/loop_suggestion.py::detect_download_upload_loop_candidates` is fully deterministic. It fires when an upload is bound to exactly `{{downloaded_file}}` and that download's filename appears in an earlier `navigate` URL. Matching uses `url_contains_literal`/`replace_url_literal`, which decode the URL (`unquote`) rather than encode the filename (`C%2B%2B.gitignore`). v1 is URL-based only.

Once detection returns, `build.py::_rewrite_loop_suggestions_for_review` optionally replaces each suggestion's `why` with a plainer sentence written by the compile's own `workflow_review` call (BUILD-55: it is asked about each loop up front as `loop_candidates`, keyed by the deterministic `wrap_start_key|upload_step_key` pair, and answers `loop_copy`; no call of its own) before it reaches Human Edit's mandatory review popup (`ReviewQuestionsDialog.tsx`) — copy only, cached with the review, and when it is missing or unusable (the review failed, no sentence for this loop, empty, over 400 characters, or containing `{{`) the deterministic `why` built above is used verbatim. Never touches `wrap_start_key`/`wrap_end_key`/`upload_step_key`/`template_literal`/`redundant_click_key`, so a failed rewrite changes nothing but the sentence a reviewer reads, and it never marks the compile `degraded` — a worse sentence is not a worse compile.

The call is grounded in the actual steps, not a bare filename: `_rewrite_loop_suggestions_for_review` resolves `wrap_start_key`/`wrap_end_key`/`upload_step_key` back to real compiled steps and hands the LLM a small role-tagged list (`role`: `"acquire"` | `"upload"`, plus that step's own `action` and `intent`/`semantic_description`; never a selector/target/identity_bundle). An earlier version passed only `{filename, removes_click: bool}` — with no picture of what the workflow does, the model confidently described the wrong mechanism (a delete) for what is always a download-then-upload pattern; the system prompt now states that pattern explicitly and the `role` tags rule out that guess.

This isn't a one-off fix bolted onto loop suggestions specifically — three small, generic pieces were pulled out so the next "make some compiler-generated text friendlier for a reviewer" need doesn't re-derive the same plumbing a third time (mirroring why `llm/llm_cache.py` itself got extracted — see that module's own docstring): `compiler/step_key.py::step_key_index_map` (the key→step-index lookup, companion to `step_keys()`), `compiler/step_description.py::describe_step` (the selector-free `{role, action, description}` shape any step can be reduced to for an LLM/reviewer), and `llm/copy_rewrite.py::rewrite_advisory_text` (the cache/fallback/failure-logging dance every advisory copy-rewrite task needs — `llm/loop_suggestion_copy.py` was removed in BUILD-55 once its sentence moved into the review call (`copy_rewrite.py` still serves other copy tasks)).

- If the step before the `navigate` is a click on the same tab whose fingerprint text (`inner_text`/`aria_label`/`label_text`/`title`, never a selector) contains the filename, it's recorded as `redundant_click_key` (with a deterministic, no-LLM `redundant_click_why` one-liner) and the range extends to cover it.
- Findings go to `compile_report["for_each_suggestions"]`, computed after the second opinion settles.

**Two separate Human Edit questions, one atomic write (2026-09-27).** `redundant_click_key` genuinely being present drives a SECOND popup — "remove this leftover click too?" — asked only after the loop question itself is answered Yes, never folded into it. The click is never wrapped into the loop body regardless of that answer — a hardcoded click on one fixed filename would fire on every iteration only to have the per-item navigate override it — the only question is whether it's archived (Yes) or left in place as an inert standalone step immediately before the new loop (No). Both outcomes still land in the same single atomic write: `apply_for_each_loop_suggestion(doc, suggestion, remove_redundant_click: bool = True)`, `cmd_accept_for_each_suggestion`'s `remove_redundant_click` payload field.

`ReviewQuestionsDialog.tsx` itself has no idea any of this exists — it's a generic driver over a `ReviewItem` contract (`nextQuestion(answersSoFar) → {title, description} | null`, `commit(answersSoFar) → Promise<WorkflowResponse>`), not a hard-coded two-question flow. `buildLoopSuggestionReviewItem` is the one small function that knows a loop suggestion can need up to two dependent Yes/No answers before its one real mutation; a future compound suggestion needing its own multi-question chain (any number of questions, any dependency between them) writes its own `ReviewItem` builder and never touches the dialog. `buildOptionalPopupReviewItem` is the other current builder — a plain one-question item, showing the same contract covers the simple case too.

**Accept** (`workflow_mutations.py::apply_for_each_loop_suggestion`, `cmd_accept_for_each_suggestion`) is one atomic write, because `patch_gate` rejects `steps` on a `for_each` patch and an empty loop vanishes at packaging. The write:
- re-resolves every `step_key` and re-checks the URL match;
- wraps the range;
- templates the filename to `{{<as>_id}}` in the body's `url` and in `entity_binding.identifier` (case-insensitive substring, via the same rule as `upgrade_entity_binding_identifiers`; other text is never rewritten);
- rebinds the upload to `{{downloaded_files_dir}}`;
- declares the `files` input through `_validate_skill_inputs`;
- when `remove_redundant_click` (default true): archives the redundant click as `superseded_by_loop_navigate` in the document-level, append-only `doc["editor_archived_steps"]` (compile never touches it; the evidence bundle concatenates it with `compile_report["archived_steps"]`); when false: leaves it as an ordinary step immediately before the loop, unarchived, unwrapped.

Any apply clears **all** pending suggestions, since moving a `download_observed` shifts sibling occurrence ordinals.

**Reject** (`cmd_reject_for_each_suggestion`) logs to `edits.jsonl` with `source="for_each_suggestion"`. `workflow_dto.py::_compile_health` filters rejected suggestions on every read (`loop_suggestion.filter_rejected`), so a reload doesn't resurface them. UI (2026-09-27): no longer a dismissible banner — `ReviewQuestionsDialog.tsx`, the mandatory Yes/No popup Human Edit opens with (see App-Flow.md), Accept/Reject-s each pending suggestion before the editor becomes interactive.

Tests: `runtime/test/unit/test_for_each.js`, `test_download_race.js`; `conxa-cloud/tests/test_for_each_compile.py`, `test_for_each_patch_gate.py`, `test_for_each_dto.py`, `test_for_each_mutations.py`, `test_publish_entity_binding_gate.py`, `test_second_opinion.py`, `test_editor_derived_sync.py`, `test_loop_suggestion.py`, `test_apply_for_each_loop_suggestion.py`, `test_workflow_dto.py`, `test_loop_suggestion_review_context.py`, `test_step_description.py`.

### 11.3 AI Review Step (removed 2026-11-06)

The `ai_review` step is gone. Its job, an author-placed question the MCP caller answers with a structured object
that is bound into `inputs`, is now an `agent_span` with no allowed actions and an `answer` schema (§11.7). A
workflow pack that still contains an `ai_review` step is no longer understood: `handlers.js::executeStep` throws on
the unknown type (the skill fails loudly instead of silently skipping a guard), and the vendor rebuilds and republishes
it from Human Edit after re-creating the step as a question span. `review_results` (the resume payload) is kept
only for popup decisions (§11.4 / outcome forks).

### 11.4 Hand-Over Step

`handover` yields the live page to a **person** (2FA, CAPTCHA, e-signature, one-off login), then takes it back. Outside the recovery cascade, like `agent_span`. Only the hand-over shape of EXEC-21 is built. **Approve / reject is answered by `handover`** (2026-10-30 decision): a person who approves clicks Continue; a person who does not approve cancels the run, and `handover` has no separate reject outcome. A confirm-only `agent_span` question is answered by a person only in a Studio test; in a real run the connected model asks the chat, so it must not guard anything consequential on its own. Supply-a-judgement is answered by an `agent_span` question.

**Resume signals** (`runtime/app/handover.js::arm()` → `{signal, disarm}`; the first to fire wins; `disarm()` is idempotent):
1. an in-page "Done — resume workflow" banner: `context.exposeBinding` + `addInitScript`, plus an immediate `evalOn` on every live page, so it survives navigation and new tabs;
2. a file drop at `~/.conxa/resume/<run_id>.cmd` (`fs.watch` + 5 s poll), written by `conxa-runtime.exe resume <run_id>` (host subcommand in `bootstrap.js`);
3. a loopback HTTP listener (`127.0.0.1`, random port, per-pause token). This is the runtime's only inbound network surface and exists only while a hand-over is pending.

**Input shield (a person never touches a page the runtime is driving).** In a host panel (Build Studio's test browser, Conxa Execute) a transparent view sits over the run's page and takes the person's native mouse input, while the runtime's CDP input goes straight to the page beneath it (`browser_panel.js::_addShield`). CDP `Input.setIgnoreInputEvents` was tried and rejected: it also drops Playwright's click, hover, key and wheel. A sign-in tab, and any popup it opens, is never covered. Studio's shield also takes the keyboard; Execute's does not (the person can type in the chat), and hands the keyboard back to the app when it goes back on after a hand-over. The runtime drives it through control op `input` (`host_browser.js::setInputOpen`): `server.js` asks for the shield once at run start (Execute adds it only then, so an older runtime never meets a shield it cannot lift), `handover.js::arm()` takes it off, and `disarm()` puts it back. A hand-over is the only planned moment a person acts in the page. It also ends with the run, a recorder adopting the view, or the panel closing it. A plain Chrome window (no host panel) has no shield yet.

**Two speeds.** `run.js` races the signal against `CONXA_HANDOVER_INCALL_MS` (120 s). If the person is there, the run continues inside the same call. Otherwise it throws `handoverPause` and `server.js` parks the page, with three differences from an `agent_span` park:
- **The host lock is released** during the pause (`HANDOVER_PARK_TTL_MS`, 30 min, is too long to block sibling runs) and re-acquired on resume.
- **Extra tabs stay open** (the person may be mid-OAuth).
- **No divergence refusal:** page change is the point. `handover.js::revalidate()` re-resolves the recorded tab via `resolveStepPage` (never "whatever is current"), checks it's open, and, if `resume_when` was declared, requires that probe to be true. Otherwise the step fails cleanly.

**Self-driven resume:** when parking, `server.js` attaches `armed.signal.then(...)`; when a signal fires, the same process calls its own `_handleTool("execute_skill", {resume_from, watch: true})`, the same path an agent would use. In the Studio sandbox, `call_runtime_tool` drops idle processes after 300 s (`_IDLE_TIMEOUT_S`), so a hand-over pending longer than that dies in tests only.

**Authoring:** `action_registry.py` lists `handover` (validation category). Its value field is the **message shown to the person** (`VALUE_LABELS`). `handover_*` fields: `on_failure` ∈ `{abort, continue}`, optional `resume_when` + timeout. Flattened to `{type: "handover", message, on_failure, resume_when?, resume_when_timeout_ms?}`; no message → dropped.

**Unknown step types fail loudly.** `handlers.js::executeStep` throws on any type that isn't a known handler or one of the `run.js`-intercepted types (`agent_span`, `handover`, `for_each`, `outcome_fork`), so a guard step never silently vanishes on a newer runtime. Older runtimes still no-op, hence the version gate above.

**Limitations:** a person can't operate a native `alert`/`confirm`/`prompt` (the run's dialog listener intercepts it at CDP). A hand-over inside a `for_each` body isn't resumable (the pause propagates out like cancellation; no compile-time guard yet, TODO.md). Not durable across a runtime restart (EXEC-22).


### 11.4 Outcome Forks (popups that need a decision)

**Why.** A recorded popup like Drive's "file already exists: Replace / Keep both / Cancel" used to be treated as a nuisance: the recorder flagged the click as an optional interstitial (§11.1) and it became a `try_dismiss` whose first candidate was the button the author happened to press. That replayed one recorded choice forever (even "Replace"), pressed Escape (often Cancel) when the button was missing, and said nothing about the case where the popup never appears. A decision dialog is a different kind of thing: the app can legitimately land in several states, and what to do in each is a *policy*, not a selector.

**Recording.** `bridge.js::buildDialogContext` captures, for any action made inside a `dialog`/`alertdialog`/`aria-modal`/`<dialog open>` container, the popup's title, message and **every** visible button (the clicked one marked `chosen`) as `RecordedEvent.dialog_context` (`conxa_core/models/events.py::DialogContext`). Native `alert`/`confirm`/`prompt` events get the same shape (OK/Cancel) from `recorder/session.py::_native_dialog_context`. The field must be on both the observation allow-list (`session.py`) and the model, or `run_pipeline`'s re-validation drops it.

**Compile.** `compiler/outcome_fork.py` (pure, no LLM) runs after bindings and before the compile report / second opinion (`build.py`). `classify_dialog` → `decision` | `dismissable` | `consent` | `form`; only decisions become forks (consent banners, close-only popups, forms and native dialogs keep today's path). A run of consecutive steps inside one decision dialog collapses to one `outcome_fork` step (`SkillStep.outcome_fork`, shape in Backend-Schema §3.4j). The recorded option reuses the real compiled step(s); every other button gets a deterministic click from `editor/overlay_identity.py::bundle_from_descriptor`, scoped `<container> >> …` — **no model writes a selector**. Option `role` comes from a fork-local word list (replace/overwrite/discard/delete/remove/…, deliberately *not* the global destructive tokens) plus the recorded step's `consequence`; consequential options get `identity_bundle.destructive = True`. The step that opened the dialog has its required "dialog present" assertion relaxed, so the no-popup outcome doesn't fail VERIFY. Defaults before review: a harmless recorded choice → `auto` (`author_approved: false`); a consequential one → `ask_human`. The popup's message text never leaves the compile (`signals.dialog_context` is stripped; only value-free `message_tokens` ship).

**Runtime.** `run.js::runForkStep`, intercepted in `executeOneStep` like `for_each` (the chosen option's steps run through the real `executeOneStep`: tab resolution, GATE/VERIFY, recovery, dry-run skip — unlike best-effort `runBranchBody`). It watches up to `timeout_ms` (1.5–8 s, default 4 s) using `page_scripts.dialogProbe` and the native `dialogQueue`, then `fork.js` (pure) decides: `matchState` matches on title + the full button-label set (a missing button, a different title, or two equally good states → `unknown`, never first-wins); `decide` applies the policy:

| Policy | Behaviour |
|---|---|
| `auto` | run the named option; a **consequential option without `author_approved` stops the run** (never run, never asked about by default) |
| `by_input` | the caller's input (a `select` with the option labels + `ask`, default `ask`) picks the option via `choice.js::matchOption`; a bad value is `badInput`, `ask` pauses **only when the author chose it** (`policy.reviewed`), otherwise stops |
| `ask_human` | pause **only when `policy.reviewed` is true** (written by `set_fork_policy`); a compiler-written or older `ask_human` stops instead |
| `continue` | leave the popup alone and carry on (the default for a popup the recording never saw) |
| `stop` | fail with the policy's message |

**Nothing pauses on its own.** A surface the fork does not list (a toast, a progress panel such as Drive's "Uploading 1 item", a popup nobody taught it) is watched for up to `timeout_ms` — a known state ends the wait, anything else is re-looked at, so one that goes away counts as clean — and then follows `unknown_policy`: `continue` by default (an older compile's `ask_human` also means continue) or `stop`. Nothing clicks it; the run goes on as recorded, the warning `"<title>" was on screen and was left alone` travels with the run, and if it really blocks the next step that step fails with the warning attached. A person is asked only where one asked to be (a `reviewed` policy from the editor's `set_fork_policy`, an author's `app_policy: ask_human`, or a `handover` step added through chat). `dialogProbe` also reports `modal`, `live` and `progress` and prefers the topmost modal, so a late tray cannot shadow the real question; those facts feed wording and logs, never a pause. The same "is this a decision" definition is held by one fixture, `runtime/test/fixtures/decision_surface_cases.json`, which both `fork.js::isDecisionDialog` and `outcome_fork.py::classify_dialog` are tested against. After running an option the popup must be gone (`pollNegative`) or the step fails. A pause inside a `for_each` degrades to a stop (the cursor can't be kept), and a destructive option with the RT-9 guard on pauses at the fork's own index. Audit: telemetry `fork_observed/fork_decided/fork_clean/fork_unknown/fork_paused/fork_answered`, recovery-log `fork_decision`.

**Asking a person.** A pause parks the page exactly like `agent_span` (`server.js`, `forkStepIndex`, valid choices stored on the park) and returns `fork_pause.js::buildForkRequest`: plain-language question, the choices (a state's own options plus `__stop__`; the by-label branch for a popup the fork does not list remains for the answer path but no run reaches it by default now), the screenshot, and `_meta["conxa/fork"] = {step_index, title, message, choices, suggested, unknown, requires_human: true}`. The agent is told to ask the user and not decide. Resume reuses `review_results: {"<i>": {"choice": "<id>"}}`; `_resumeForkStep` validates against the stored choices (bounded re-ask, `REVIEW_RETRY_MAX`), binds `__fork_choice_<i>`, and `runForkAnswer` re-observes the page (the person may have clicked in it themselves → `handled_in_page`). Build Studio's test driver extracts `conxa/fork` from `_meta` only (the prose also contains `resume_from`) and routes it to a person (`_fork_hook` → `waiting_fork` test card); with no hook the test ends at the popup.

**An unrecorded popup is put to the MCP client (EXEC-49, 2026-11-05).** On a run whose caller can reason (effective recovery ceiling ≥ 3, or Build Studio's test with "let AI answer agent steps and unexpected popups" on, which passes `answer_popups`; never a scheduled run), a decision popup nobody recorded is not left alone: the run parks (`agentPopupPause`, the same park as a fork pause) and `fork_pause.js::buildAgentRequest` asks the client to pick one of the popup's own visible buttons, **deciding from the workflow's goal and asking the user in chat when unsure** (`_meta["conxa/fork"].requires_human = false`, `agent: true`). Two entry points: an unknown popup at a fork step, and a plain step whose click was blocked by one (`askAboutBlockingPopup`, after Tier A has refused to dismiss it). Nothing is offered when any button looks irreversible (`fork.js::isIrreversibleLabel`: delete, remove, erase, permanently, pay, buy, purchase, order, checkout, send, submit, publish, transfer, revoke): the run stops. It also stops in a dry run, inside a loop or option body, for a destructive step, and under `unknown_policy: stop`. The answer arrives as `__fork_choice_<i>` (with `__fork_agent_<i>` and, when the step's own action had already run, `__fork_acted_<i>`); `answerBlockingPopup` re-checks the label against the live page, clicks it, confirms the popup closed, then runs the step, or only re-checks its result when it had acted (never twice). Studio answers with `_answer_popup_choice` (metered `ai_credits`, validated against the offered buttons, `__stop__` when unsure). Recorded popups are unchanged. Security: SG-23.

**Late popups, loops, option tails (EXEC-49).** After a fork step ends clean or continue, its states stay armed for the next two steps of the same list (`handleLateFork`): a popup the fork lists that opens after the watch window is handled as that state instead of failing the next click as INTERCEPTED (a pause degrades to a stop; never armed in a dry run or inside an option body). The same popup handled more than twice in one run, counted per popup, loop row and called workflow, stops the run (`countSurface`, `surface_loop`). An option may carry `then`: `end` finishes the run successfully on that path, `stop` ends it as "that path is not covered" (see Backend-Schema §3.4j; capability `fork_then`).

**Hardening.** `cascade.js::layer1Ladder` and `run.js`'s agent-nominated dismissal both refuse to dismiss a decision dialog (`fork.js::isDecisionDialog`: ≥2 buttons, not all plain dismiss labels, no fields, not a consent banner): no Escape, no generic close, no learned selector. The step keeps failing as INTERCEPTED and escalates; the Tier B note tells the agent to ask the user.

**Author review.** `conversation/clarify.py` asks one `fork` question per un-reviewed popup state (priority 1, so the five-question cap keeps it): each option, "let the person running it choose each time" (`by_input`), "pause and ask me" (`ask_human`), "stop the run". The answer becomes the `set_fork_policy` op → `workflow_mutations.set_fork_policy`, the **only** writer of `author_approved` (choosing an option there is the approval; patch_gate permits only `intent`/`semantic_description` on a fork). The LLM may only reword the question's title and text, in the batched `clarify_question_copy` call (handed the popup's own button names as context; the per-popup `fork_question_copy` call was removed in BUILD-55); choices are fixed by code.

**Variant recording ("record the case where the popup does NOT appear").** After the questions, `_flow_offer_variants` posts an optional `fork_variant` card. Recording it uses `cmd_start_recording({variant_of})`: the take is registered in `Workflow.variant_session_ids` and **never** replaces, backs up, or clears the workflow's own recording (start/stop/cancel/empty all have variant branches). `cmd_apply_variant` compares the two takes by rule — no compile, no credit, no model — with `compiler/variant_align.py` (LCS over value-free event signatures): a variant with no popup flips `clean.observed`; a decision popup only the variant saw becomes a new state (`outcome_fork.state_from_dialog`, deterministic clicks); anything else is returned as `unexplained` and **never merged**. Applied as the `apply_variant_observation` mutation on the saved skill, so the author's edits and answers survive.

**Not built (TODO.md).** Native-dialog forks (need the dialog pre-armed before the triggering click, EXEC-29) — a native `alert/confirm` keeps its own `dialog_accept/dismiss` step; insertable forks in Human Edit; learning forks from runtime `fork_unknown` telemetry; counting Tier A dismissals toward the loop guard. (Done in EXEC-49: late popups, loop guard, option tails, agent-answered unrecorded popups, one choice per button in the pre-compile question with replay onto the compiled option, and the post-compile "it shows up every time" answer that flattens a single-state fork into the recorded steps.)

**Tests:** runtime `test_fork.js`, `test_fork_hardening.js`; cloud `test_outcome_fork.py`, `test_set_fork_policy.py`, `test_variant_align.py`, `test_variant_recording_handlers.py`, `test_test_flow.py` (fork pause routing), `test_recorder_observation_passthrough.py`.

---

### 11.5 App Maps (a learned map of each web app)

**What.** Every compiled workflow teaches Conxa a little about the app it ran on. An *app map* keeps that: **screens** (a host + route such as `/orders/:id`), **actions** (what a person did on a screen and which screen it led to) and **popups** (the decision popups of §11.4, with how often each appeared, how often the same step ran without it, and which option each recording chose), plus which workflow passes through which actions. One map per host and workspace. Phase 1 (Build Studio only, no behavior change) is built; viewer, cloud sync, hints, runtime use and sharing are the later phases tracked as `APP-*` in `TODO.md`.

**It is derived data.** `conxa_core/models/app_graph.py::fold_contributions` merges *contributions* — one per workflow per host, produced by `conxa_compile/app_graph/extract.py::contributions_from_doc` from the **compiled** steps (so the author's review edits are what the map learns). The fold is pure and order-independent; re-compiling a workflow replaces its contribution (no double counting) and deleting one removes it. Files: `data/app_graphs/<workspace>/<host>/contrib/<skill_id>.json` (inputs) and `graph.json` (the fold), written by `conxa_core/storage/app_graph_store.py`.

**Kept current by hooks, not call sites.** `json_store.write_skill`/`delete_skill` notify `conxa_core/storage/skill_hooks.py`; `conxa_compile.app_graph.install_hooks()` (registered at Studio startup) refreshes the maps, so a compile, every editor mutation and every undo is covered. A hook that fails is a warning and never fails the write. `handlers/compile.py` refreshes once more after the workflow points at its skill (the writes happen before that link exists), and `cmd_delete_workflow` removes the workflow's part. `cmd_rebuild_app_graphs` backfills from existing workflows.

**It holds the shape of an app, never its data.** All text enters through `conxa_compile/app_graph/scrub.py`: URLs, emails, quoted spans, file-like words and any word with a digit are removed; control labels longer than five words are dropped; a step bound to a table row (`entity_binding`) keeps no name, and a step bound to an input keeps only the field's own labels (its visible text may be the chosen value). No selectors, DOM, page text or screenshots. Typed values never appear (test: `test_extract_never_puts_a_typed_value_or_file_name_in_the_map`).

**No LLM anywhere in the map.** Screen identity is `sha16(host | route_template(url))` (`compiler/navigation_effects.py::route_template`, same dynamic-segment rule as `generalize_url_pattern`). v1 limitation: single-page apps that keep the URL while the view changes collapse into one screen — the compiled steps carry no page landmarks to split them (`TODO.md` APP-1).

**Sharing with teammates (phase 2).** `conxa-cloud/backend/app/api/app_graph_routes.py` stores contributions per workspace (KV `app_graph__{workspace_id}`, `docs/Backend-Schema.md` §5.0) with shape validation and size/count caps; it never folds. Studio's `services/app_graph_sync.py::sync` pushes contributions whose content hash changed, deletes the ones removed here, and pulls teammates' (a skill id is local to the machine that compiled it, so a teammate's contribution never replaces one built here). `_sync.json` remembers each contribution's last-exchanged hash and origin; a pulled contribution that leaves the cloud is removed here, one built here is never removed because someone else deleted their copy. It runs after a successful publish (best-effort) and via `cmd_sync_app_graphs`.

**Viewer (phase 3).** `renderer/src/pages/AppMapPage.tsx` (route `/app-maps`) reads `list_app_graphs` / `get_app_graph` and draws the graph as plain SVG laid out by `src/lib/appMapLayout.ts` (breadth-first columns from the entry screens; no graph library). Read-only for now. UI: `docs/UI-UX-Brief.md` addendum.

**Warnings from the map (phase 4).** *After a compile:* `app_graph/hints.py::known_popups` finds, step by step (using the extractor's own `walk_doc`, so "the same action" means one thing), decision popups that OTHER workflows met right after the same action in the same app where this skill has no fork; `conversation/clarify.py` turns up to two into a `known_popup` question (priority just below `fork`, fixed wording, two choices). "Yes" runs the `add_known_popup_fork` op → `cmd_add_known_popup_fork` → `workflow_mutations.add_known_popup_fork`, which inserts `outcome_fork.fork_from_known_popup`: every option is unrecorded (role+name click scoped to the dialog via `bundle_from_descriptor`, never model-written), `clean.observed` is true because this recording ran without the popup, `from_map: true` so it does not count as an appearance, and the policy is `ask_human` (suggesting the option other recordings chose most) — never `auto`. *While recording:* `RecordingHinter` (called from the recording event hook in `workflow_flow._flow_open_recording`) matches each recorded event to a map Action by screen (host + route) and control label and posts one "Heads up…" line to the conversation per popup per recording. Advisory only: it never changes the recording.

**Runtime use (phase 5).** *Author's standing answer:* `cmd_set_app_popup_policy` (`handlers/app_graph.py`) is the ONLY writer of `Popup.app_policy` and so of its `author_approved`: `auto` on a button (a consequential one also needs `confirm_risky: true`), `ask_human`, `stop`, or clear. Kept in `data/app_graphs/<ws>/<host>/policies.json`, applied on top of every re-fold, edited from the popup panel of the App maps page. *Shipping:* `skill_package_builder` writes `app_knowledge.json` into each skill (`app_graph/knowledge.py`) — decision popups only, for the hosts the skill visits (cap 40 per host), with title/buttons/message words/container, each button's role and the policy; **no screens, actions, workflow names or paths**. It rides in the pack's code files (`_CODE_FILES` in `skillpack_update_routes.py`, `release_diff._SKILL_FILES`), so it activates atomically with the skill version and only changes when the skill is published again; an absent, malformed, oversized or newer-schema file is simply "no extra knowledge" (`runtime/app/app_knowledge.js::load`). *At run time:* when `run.js::runForkStep` meets a dialog none of its own states recognise, `app_knowledge.lookupPopup` (the fork matcher: every known button present, compatible title, ambiguity is no match) looks it up on the page's host. A known popup follows `decideKnown`: `auto` clicks the named button only if it is visible and — when the map says it is consequential — only if the author approved it; no policy or a missing button leaves the popup alone and the run continues, an unapproved risky button stops the run, and only `ask_human` **pauses for a person** (the pause lists the popup's real visible buttons with their risk and suggests the one other workflows chose most; the run-time answer is still only ever one of those buttons); `stop` stops; inside a repeated step a pause becomes a stop. The RT-9 destructive guard and dry-run apply to the click exactly as they do to a fork option. Telemetry `fork_decided` has `src: "app_policy"` and `st: "map:<popup id>"`. *Not changed:* the Tier 1 ladder (`cascade.js`) still refuses to dismiss any decision dialog; mapping consent/dismissable popups to give it a known close button is TODO APP-1.

**Closing the loop (phase 6).** The runtime reports what it could not place: `run.js::emitAppObs` sends telemetry `app_obs` when a fork meets a popup none of its states recognise, and when a decision popup blocked a click and the Tier 1 ladder left it alone (`primaryErr.decisionDialog`). The payload (`app_knowledge.js::observation`) is host, route, <=8 title words, <=6 button labels and whether the shipped map knew it — scrubbed by `runtime/app/scrub.js`, which must agree with `app_graph/scrub.py` on every case in `runtime/test/fixtures/scrub_cases.json` (both sides run that file). A button label that needed any scrubbing is dropped whole, and the popup's message never leaves the machine (`docs/Security.md` SG-20). The cloud counts these per popup shape over the workspace's own visible runs on read (`GET /api/v1/app-graphs/{app}/field`, drift-queue plan gate, nothing written back); `cmd_sync_app_graphs` then pulls them (`services/app_graph_sync.py::pull_field`) and `app_graph/field.py` stores them in `field.json`: a row matching a covered popup raises its `field_seen`, any other becomes a **field-only popup** ("seen in real runs, not in any workflow") with button roles from the compiler's consequential-word list, which ships in `app_knowledge.json` and can get a policy like any other. Publishing stays the only path to runtimes.

**Agent discovery (phase 7).** Agents learn which web app each workflow works in. `list_skills` gains an `app` field (the host of the skill manifest's start URL, `app_knowledge.js::appOfManifest`), and the discoverability note `durable_context.js` writes into each registered agent host marks every workflow "(works in `host`)" (sync reads the active manifests from disk, `sync.js::_appsBySkill`; a skill with no start URL is listed without one). Deliberately **not** shipped: screens, paths or per-workflow routes — they are the author's data and the runtime has no use for them; the shipped map slice stays popups-only.

**Sharing across customers (phase 8) is not built.** It needs an explicit opt-in, a k-anonymity threshold across workspaces and a `docs/Security.md` re-review (SG-20 must be revisited first); design in `TODO.md` APP-1. Until then every map, every field count and every policy is private to one workspace.

**Tests:** cloud `test_app_graph.py`, `test_app_graph_routes.py`, `test_app_graph_sync.py`, `test_app_graph_hints.py`, `test_app_graph_knowledge.py`, `test_app_graph_field.py`; runtime `test_app_knowledge.js`, `test_scrub.js`, `test_durable_context.js`; Studio `test/appMapLayout.test.mjs` (`npm run test:app-map`).

---

### 11.6 Wait, Clipboard and Call-Workflow Steps (2026-10-30)

Three step kinds added from `docs/Block-Runtime-Vision.md`. All are authored like `handover` (§11.4: no selector,
no recovery block, config in `value` plus top-level fields; see `docs/Backend-Schema.md` §3) and each is gated by
an app-layer capability so an older engine refuses the pack.

**`wait_until`** (`handlers.js`). Waits for something that happens on its own: an element shows or goes
(`page.waitForSelector`), text shows, the address contains a fragment, or the page reaches `load` /
`domcontentloaded` / `networkidle`. Events only, never a clock time: one wait is clamped to 120 s because a whole
MCP call is cut off at about 210 s (`EXECUTION_DEADLINE_MS`). A timeout is a plain, non-retryable failure naming
what was waited for, unless `on_timeout` is `continue`. Waiting for a download stays with `download_observed`.
A wait of minutes or hours (or until a time of day) is not built; that belongs to the scheduler.

**`clipboard_write` / `clipboard_read`** (`handlers.js`). Use the browser's `navigator.clipboard` on a secure
(https) page in front; permission is granted per origin just in time. Every call goes through the page-eval
deadline seam (`page_eval.js`). **The browser clipboard is the computer's clipboard:** text written stays there
until something replaces it, and a read can see anything the person copied. So a read binds into the run's
inputs only (`declaredOutputNames(steps, true)` lets it survive a pause, but `collectOutputs` never reports it),
no error message contains clipboard text, and an empty clipboard fails the step. See `docs/Security.md` SG-22.
The OS-wide clipboard outside the browser is deliberately not covered.

**`call_workflow`** (`call_workflow.js`, `run.js::runCallWorkflowStep`). Runs another installed workflow's steps
in the middle of this one, through the same `executeOneStep` (tabs, Tier A recovery, GATE/VERIFY, dry-run).
- *Before the run starts* (`server.js`, per requested skill): every callee is loaded and checked, so a bad call
  is a refusal and not a surprise. Installed and intact; installed version equals the pinned `call.version`;
  no circle and at most `MAX_CALL_DEPTH` (3) levels; no `agent_span` / `handover` / `outcome_fork` inside it;
  inputs and results match what it declares. The callee's host joins the platform lock and its slug the policy
  gate (`_withCallees`).
- *Scope.* The callee sees only the inputs handed to it, its declared defaults and this run's download
  bindings; the caller gets back only the results the call asks for, under the names it chose. `state.inputs`
  is emptied and refilled in place and restored in `finally`.
- *Failure.* A failure inside a called workflow is final: its message is prefixed with the workflow's name and
  it carries no `failedAt`, so there is no resume point (a resume would repeat work already done) and no
  agent-recovery round trip (those address top-level steps). Tier A recovery of each callee step still runs. A
  mid-run sign-in expiry inside a callee is reported as a plain failure, not auto-retried.
- *Guards.* `state.loopDepth` is raised for the call, so the Studio destructive-step guard fails closed inside a
  call exactly as it does inside a loop.
- *Version pin.* At build (`call_workflow_pin.py`, from `_write_skill_packs_format`) each call is stamped with
  the version of the workflow it calls (built in the same call, or read from an earlier build). Rebuilding the
  callee alone changes its version and makes the caller refuse until it is rebuilt: deliberate.
- *Telemetry.* `call_start`, `call_done`; the callee's steps are counted under the caller.

### 11.7 Agent Span (Engine v2 C4, 2026-11-06: Browser-Use style; absorbed `ai_review`)

An author-placed span the caller's AI agent carries out one action at a time. The MCP client (Claude Desktop) is the
brain: the runtime parks the run, sends what it sees, executes the ONE action the client returns, and repeats (Build
Studio's test uses its own model, metered, in the client's place). Like `handover` it has no selector, never reaches
`executeStep`/`HANDLERS` or the Tier 1-4 recovery cascade, and never touches `recovery_stage.js` or `retry_budget.js`.
It is not a violation of "Tier A costs zero tokens" for the same reason `handover` is not a recovery step: it is
explicit, editor-visible and author-placed, and it sits outside every selector/a11y resolution path. A skill with a
span is no longer zero-token on every run, so its manifest carries `uses_agent: true` and `create_schedule` refuses it.

**Step.** `value` is the goal (`{{input}}` is filled from the run's inputs); top-level fields:
`agent_span_allowed` (a subset of `click`, `fill`, `select`, `scroll`, `send_keys`, `navigate`, `go_back`, `open_tab`,
`switch_tab`, `close_tab`; `done`, `give_up`, `wait` and `read_text` are always available; an empty list plus an answer
schema is a plain question), `agent_span_until` (optional: `text_present`, `text_absent`, `url_pattern`, `url_changed`
or `state_changed`, never a selector), `agent_span_budget` (`actions` 1-100, default 25; `seconds` 10-1800, default
600), `agent_span_on_budget` (`fail`), and the answer: `agent_span_answer_schema`, `agent_span_output_name`,
`agent_span_on_failure` (`abort` / `use_default` / `continue`), `agent_span_default_value`, `agent_span_attach`.
The packager writes `goal`, `allowed`, `until?`, `budget`, `on_budget`, `answer?: {schema, output_name?, on_failure,
default_value?}`, `attach?`; a span with no goal, or with neither an allowed action nor an answer schema, is dropped at
build with a warning (a new span starts with a blank goal, so an unwritten one is dropped, never shipped with
placeholder text). `patch_gate.py` refuses `run_if` and `recovery` on a span, refuses a free-form span directly
before a destructive step and a destructive step directly after one, refuses a `run_if` on a destructive step that
reads a free-form span answer (`destructive_step_cannot_be_gated_by_span_answer`), and refuses editing a confirm-only
span into a free-form one in front of a destructive step (`confirmation_guards_a_destructive_step`). A **confirm-only
question** (no allowed actions, answer schema exactly `{confirmed: boolean (only true)}`, `on_failure: abort`;
`patch_gate.is_confirm_only_span`) is the one span that may sit right before a step that cannot be undone: it can only
gate whether that step runs. Clarify's "a person confirms before sending" inserts one
(`workflow_mutations.insert_step_after(..., confirm_only=True)`). **Order rule on every path (WF-13).** The adjacency
rule is one function, `patch_gate.span_before_irreversible` / `check_step_order` ("destructive" is `_is_destructive`:
the compiler's `consequence` label or `judged_irreversible`, unsure counts as cannot-be-undone). Every structural
change runs it on the resulting step list: insert, move (Human Edit drag and chat `move_step`), delete (removing the
step between them), a re-recorded splice and a popup flattened into its steps (`workflow_mutations.py`). All codes for
it read one sentence (`patch_wording.SPAN_BEFORE_IRREVERSIBLE`); a refused command writes nothing and pushes no undo
entry. The build refuses a document saved that way (`_build_workflow_from_saved_skill`, also when a dropped unfinished
step closed the gap), and `run.js::spanBeforeIrreversible` refuses such a pack before any step runs
(`step_fail` `agent_span_before_irreversible`). A span's answer (`<name>`, `<name>_<field>`) and an extract or
clipboard-read name are filled by the run: they are never declared as inputs (`workflow_mutations.run_produced_names`),
and the packager drops one an older edit declared.

**Run.** `run.js` skips a span whose `until` already holds and that wants no answer (zero actions). It fails closed in a
dry run (withheld, listed), on a scheduled run (`ctx.agentSpans === false`), and inside a loop, option or called
workflow (so a loop-body span never runs, let alone counts). **Span limit (WF-13, owner decision 2026-11-20):** one run
starts at most `MAX_AGENT_SPANS_PER_RUN` (5, `run.js`; Studio mirrors it in `patch_gate.py`) free spans. Spans run only
at the top level and once each, so the count is the span's place among the plan's free spans; a confirm-only question
is a person's yes and never counts (and is never refused by it). The free span over the limit does not start: the run
ends before it with `step_fail` `agent_span_limit` and one plain sentence ("This workflow uses the AI agent more than 5
times in one run…"); a refused span is never parked for Tier B recovery (`agentSpanRefused`). The Studio test says it
up front (`test_flow.py::_agent_span_notice`, with any unfinished span the build will leave out) and the failed card
reads the same in plain words (`failure_summary.py`). Otherwise it parks (`spanStepIndex`) and `agent_span.js::buildSpanRequest` returns the goal, the allowed
actions, the budget left, the **workflow context** (skill name, the run's inputs with secret-looking names shown as
`(hidden)`, the steps already done and those still to come, the agent's own recent actions), the **tab list**, the
numbered list of visible interactive elements (up to 150; each carries `in_dialog`), any text it asked to read, any
attached files, and a screenshot. Page text, element labels, read text and files are framed as untrusted data. The
agent answers with `execute_skill` `resume_from: <i>` and `span_actions: {"<i>": {kind, ...}}`: `click`/`fill`/`select`
(`candidate_index`, `value`), `scroll`, `send_keys` (Enter, Tab, Escape, arrows, PageUp/Down, Home, End, Backspace),
`navigate`/`open_tab` (`url`), `go_back`, `switch_tab`/`close_tab` (`tab`), `wait` (seconds), `read_text`, `done`
(with `answer` when the step has a schema), or `{status: "give_up"}`. The runtime refuses, without spending budget, an
action the step does not allow, a number not in the list it just sent, a disabled element, a password / file /
hidden field, a URL that is not http(s), a tab that is not in the list, closing the start tab, and `done` while the
author's `until` does not hold. A click (or Enter on a focused control) whose label looks irreversible
(`fork.js::isIrreversibleLabel`) is refused with `needs_user_confirmation` until the same action is sent again with
`user_confirmed: true`; the request tells the agent to ask the person in the chat first, and such a refusal does not
count toward the refusal limit (after the fourth ordinary refusal, or the seventh confirm request, the span fails).
There is no host rule and no popup stop: a decision popup is announced in the request and its buttons are ordinary
elements. A span with an answer ends only on `done` with an answer that matches the schema (bounded re-ask, then
`on_failure`); one without ends on `done`, or when `until` holds after an action. Tabs the span opened are tracked and
closed when it ends and the start tab comes back to the front, so the recorded steps after it still run on the tab
they were recorded on. The answer is bound under `answer.output_name`, and each plain field also as
`<output_name>_<field>` (`run.js::flattenAnswer`); later steps use `{{x_field}}` and `run_if` reads it, so a question
span followed by `run_if` steps is the if/else; a named answer is reported in the run's `outputs` like an `extract`.
The failure codes are `agent_span_exhausted`, `_gave_up`, `_refused_too_often`, `_answer_invalid`, `_page_closed`;
refusals before a span starts are `agent_span_inside_loop`, `_no_agent`, `_limit`, `_before_irreversible`.
Telemetry: `span_start`, `span_action`, `span_done`. A span inside a `for_each` fails closed, as before
(the old `ai_review` loop placement is not carried over; see TODO.md).

**Studio test.** With "let AI answer" on, `handlers/workflows.py::_answer_agent_span` stands in for the agent through
the metered proxy (task `agent_span`); `check_span_answer` holds each answer to what the runtime accepts, counts
refusals in the model ledger, and drops any `user_confirmed` the model sets. A confirm-only question goes to a person
(`_review_hook`; a yes also releases the test guard in the same resume), and so does a `needs_user_confirmation`
request ("The agent wants to do something that can't be undone: <label>. Allow it?"); the action is then resent with
`user_confirmed: true`. With "let AI answer" off the test ends at the span ("passed up to here"). The runtime's
span limit (above) applies to the test as to any run; each span also has its own budget, and the stand-in may spend at
most `_MAX_AGENT_SPAN_ROUNDS` (100) rounds across one test run (`_MAX_PAUSES` = 5 counts popup decisions only). A span that ends unfinished is shown
with its reason in plain words (`failure_summary.py`: gave up + the agent's one-line why, ran out of actions or time,
kept trying what is not allowed, no usable answer). The capability
`agent_span_v2` in `runtime/app/version.json` and `manifest.required_capabilities` makes an older engine refuse the
pack. The popup-choice stand-in uses the separate LLM task `popup_choice`.

**Risk.** A span can leave the recorded site with the person's signed-in browser; see `docs/Security.md` SG-24.

## 12. Telemetry

### 12.1 Flow

```mermaid
sequenceDiagram
    participant RT as Runtime (tracker.js)
    participant Cloud as Conxa Cloud

    RT->>RT: createTracker(pack.tracking)
    RT->>Cloud: POST {tracking_url} (X-Tracking-Token)
    Note over RT,Cloud: wf_start flushed and awaited (≤ 2 s) before any browser opens
    Cloud->>Cloud: verify token; verify chain link on raw events; _cap_events
    Cloud->>Cloud: db_append("tracking/{ws}", run_id, batch)
    Cloud-->>RT: 202 Accepted
    Note over RT: failed POST → logs/telemetry-spill.jsonl, drained at next startup
```

The tracking token is baked into `pack.json` at publish (§5.4); end users never authenticate.

### 12.2 Events

Emitted by `runtime/app/tracker.js`. Illustrative, not exhaustive: `tracker.js` call sites are the source of truth.

| Code | When | Fields |
|---|---|---|
| `wf_start` | run begins | `ts`, `tot` |
| `step_ok` | step succeeds | `ts`, `si`, `tier` |
| `step_fail` | step fails | `ts`, `si`, `code` |
| `recovery_tier{N}` | recovery attempted | `ts`, `si`, `tier` |
| `tier_ok` | recovered/settled/scrolled success | `tier` (e.g. `tier1_compiled_settled`), `virtualScroll?` |
| `settle_retry` | settle fallback attempt (§9.3) | |
| `verify_result` | step with assertions verified | `si`, `ok`, `n`, `advFail` |
| `repair_event` | recovery success (§10.4) | step, tier, method, score/margin, `stable_hash`, drift hint |
| `drift_detected` / `env_mismatch` | pre-execution checks (§9.7) | |
| `for_each_done` | loop finished | `processed`, `failed` |
| `policy_block` | policy verdict fired (§12.5) | `code` (`outside_window`\|`denied_host`\|`host_unresolved`\|`policy_tz_unsupported`\|`policy_expired`), `pv`, `enf` |
| `wf_ok` | run succeeds | `ts`, `dur`, `tot`, `rec` |
| `wf_fail` | run fails | `ts`, `dur`, `fsi`, `fc` (includes policy codes) |

### 12.3 Batch Payload & Storage

```json
{ "rid": "run_id", "wfid": "workflow_id", "wfv": "workflow_version", "rv": "runtime_version",
  "uid": "user_id_hash", "wid": "workspace_id", "sv": 1,
  "seq": 0, "prev": "", "h": "hmac_sha256_hex",
  "evts": [{"e": "wf_start", "ts": 1717000000, "tot": 5}] }
```

- Stored in `kv_store` namespace `tracking/{ws}`, key `run_id`, appended via `db_append()`.
- Queried by Clerk-authenticated `tracking_routes.py`; `_batches_for_principal()` scopes by `workspace_id`.
- Full stored shape: `docs/Backend-Schema.md` §4.2.

### 12.4 Evidence Chain & Reconciliation (PROD-18)

- **Start record:** `server.js` flushes `wf_start` and awaits it (bounded 2 s) at the one seam every path crosses before opening a browser, so even a killed process leaves a start record.
- **Spill:** a failed POST appends to `{CONXA_DATA_DIR}/logs/telemetry-spill.jsonl` (`_spillAppend`; drop-**newest** past 200 lines, since the earliest evidence matters most) and drains at startup (`drainSpill` in `startupSync`). The in-memory ring (50 events, 2 s flush) drops oldest.
- **Chain:** `seq`/`prev`/`h`, where `h` is an HMAC-SHA256 keyed on the tracking token over the batch's canonical JSON, chained to the previous hash. The server verifies on **raw** events before `_cap_events` truncation (`tracking._verify_chain_link`). `_chain_state` per run: `verified`, `gap` (missing `seq`), `broken` (bad signature or link), `none` (runtime predates the chain; not treated as tampering).
- **Reconcile:** `GET /api/v1/tracking/{workspace_id}/reconcile` reports runs started, completed, abandoned, and in flight, plus chain gaps and breaks, reusing `_visible_run_records` (`docs/Backend-Schema.md` §5.7c).

What this proves and doesn't: `docs/Audit-and-Control.md` §1.

### 12.5 Governance Policy Gate (PROD-18)

An admin sets an Ed25519-signed policy (`PUT /api/v1/tracking/{workspace_id}/policy`, `docs/Backend-Schema.md` §4.2a) with per-skill execution windows and a host deny-list. It's signed with the manifest keypair (`app.api.manifest_signer`; rotation trade-off in `docs/Security.md` SG-19) and verified with `manifest_manager.js::verifyManifestSignature`. No new crypto or trust anchor.

`runtime/app/policy_gate.js` splits pure `evaluate()` from `loadPolicy()` (fetch/cache/verify). `server.js` calls it after resolving target hosts and before the host lock or any browser, so a refusal never touches the target app. Ships `enforce: false` (audit-only `policy_block` events); an admin flips `enforce: true`.

The one locally-enforceable control is `expires_at`: it's inside the signed document, so an expired policy refuses every skill in the workspace (`fc: "policy_expired"`) until it re-verifies. Other local bypasses (deleted cache plus blocked network) aren't preventable locally: "enforce locally, prove centrally" (`docs/Audit-and-Control.md` §3).

**Not built:** require-approval-before-step, a policy-authoring UI, and `require_receipt` enforcement.


---

## 13. LLM Router & Entitlements

**Location:** `conxa-cloud/backend/app/llm/router.py`, `app/api/llm_proxy_routes.py`, `app/services/entitlements.py`. Provider setup: `conxa-cloud/backend/ROUTER_SETUP.md`. Cost model: `docs/cost_model.md`.

### 13.1 Provider Pool

A flat pool of `PoolEntry(provider, endpoint, api_key, text_model, vision_model, multimodal_model, fallback_*_model, pool, auth_style)`. Multiple keys per provider expand to multiple entries. `pool` ∈ `free|starter|pro`. `auth_style` is `bearer` for everything except BYOK (`api_key_header`).

Free-pool defaults:
- **Groq:** `llama-3.3-70b-versatile` (text), `llama-4-scout-17b` (vision);
- **Google AI Studio:** `gemini-2.5-flash` (both);
- **NVIDIA NIM:** `llama-4-maverick-17b` (text), `llama-3.2-90b-vision` (vision).

Disabled by default: Cerebras, Together, OpenRouter, Mistral.

**Vision tasks: one constant.** `conxa_core.llm.client.VISION_TASKS` is the single set; the router imports it as `_VISION_TASK_NAMES`. Studio's `call_llm` uses `_is_vision_task` to pick `/proxy/vision` vs `/proxy/text`, and the cloud fixes `for_vision` from the URL hit. The router consults the set only when `for_vision is None` (the BYOK path). Copilot tasks route by payload instead (`_copilot_modality`, §7.4); `execute_chat` always takes the vision path onto the multimodal slot (§3.6).

### 13.2 Tiered Compile Pools

Starter and Pro each have an independent single-deployment block: `LLM_{TIER}_PROVIDER`, `_ENDPOINT`, `_API_KEYS`, `_TEXT_MODEL`, `_VISION_MODEL`, `_MULTIMODAL_MODEL`, `_FALLBACK_TEXT_MODEL`, `_FALLBACK_VISION_MODEL`. `Settings._tier_provider_configs()` builds entries tagged with the tier, appended after the Free list. An unset tier contributes nothing.

- `_meter_and_call` passes the workspace's `compile_pool` (`entitlements.compile_pool_for`) as `pool`; `_next_available_entry` filters on it.
- **Fallback model:** from `attempt > 0`, `_call_provider` uses the entry's fallback model on the same key. It's attempt-indexed (a `ponytail:` cut: multi-key tiers may reach a fresh key's fallback first). Free entries have none.
- **Missing pool:** falls back to any pool (logged via `_debug_log`) rather than failing a paying compile over misconfiguration. Enterprise/Development carry `compile_pool="premium"`, which only BYOK's synthetic entry matches, so they fall back to any pool without BYOK.

### 13.3 Router Behavior

- **Per-modality cooldown:** `cooled_until_text` / `cooled_until_vision` / the multimodal slot are separate, so a text-timeout storm doesn't bench vision.
- **Failure classes** (`_call_provider`):

| Failure | Handling |
|---|---|
| `429` | `Retry-After`, else 30 s |
| timeout / connection | per-entry exponential backoff (`consecutive_transient_failures`): 5 → 15 → 45 s, cap 60; resets on success |
| provider `5xx` / malformed JSON | 10 s |
| deterministic `400`/`413`/`422` | `_DeterministicRejection`: fail immediately, no cooldown, no cross-provider retry |
| reasoning-only empty stream | `_DeterministicRejection` → `llm_reasoning_only_no_content` (§7.4) |
| `401`/`403` | quarantined 300 s (`quarantined_until`), never removed from the pool |

- **Whole-request budget:** `llm_router_total_budget_secs` (75 s, under Render's ~100 s edge timeout). One deadline; every attempt's timeout is clamped to what's left.
- **Cooled pool:** if every matching entry is cooled (not merely absent), sleep once up to `wait_ceiling_secs` (20 s) for the soonest one, respecting `pool` and `for_vision`. No matching entry at all → fail fast.
- **Selection:** LRU cursor guarded by `_lru_lock` (unguarded access under FastAPI's thread pool made concurrent requests hammer the same entry). `max_retries = max(config, len(pool))`.
- **`call_entry_directly`:** BYOK only; no pool rotation, cooldown, or quarantine. No streaming (returns one chunk).
- **Errors:** `error_detail` collects one line per failed attempt (e.g. `HTTPError 429 rate_limited (cooled 2s): …`). A 502 returns `{"message": "llm_all_providers_failed", "error_detail": [≤ 8]}`, which Studio's client unpacks so compile warnings name the real failure.

### 13.4 Build Studio → Cloud Proxy

`services/llm_proxy_client.py` → `POST /api/v1/llm/proxy/{text,vision}[/stream]`:
- headers: `Authorization: Bearer <Clerk token>`, `X-Conxa-Client: build-studio`, `X-Conxa-Machine` (§13.6, omitted if unreadable);
- body `usage_class`: `compile` (default), `ai_credits` (canonical; `human_edit` is a permanent legacy alias) or `execute_chat`;
- `CloudUnreachable`, `QuotaExceeded`, and entitlement errors propagate to the compiler as `compile_error` events.

**Reliability:**
- **Admission:** `llm_proxy_max_concurrent_per_workspace` (4) caps in-flight proxy calls per workspace; excess gets `429 workspace_concurrency_limit` with `Retry-After`, so one tenant's burst can't drain the shared pool.
- **Client retry:** 502/503/504 or that 429 retry twice (1 s, 4 s + jitter, honoring `Retry-After`), then raise `ProxyUnavailable` (a `CloudUnreachable`). `None` means only a genuinely empty answer. Primary-path callers let it abort; optional callers degrade.
- **Vision anchor batching:** `compiler/build.py::_prefetch_vision_anchors` → `prefetch_vision_anchors_batch` sends up to `llm_anchor_vision_batch_size` (4) screenshots per `anchor_vision_batch` call to warm the cache. Best-effort, with a 60 s deadline (`_PREFETCH_VISION_ANCHORS_DEADLINE_SECS`) and a `vision_anchor_prefetch_start` log line. Intent comes from the intent graph or blank (no per-step LLM). Vision body limit is `llm_vision_proxy_max_bytes` (8 MB).
- **Compile-credit refund:** `POST /api/v1/usage/compile/refund` (`refund_compile_credit`) refunds a committed credit on infra-class aborts only (`CloudUnreachable`/`ProxyUnavailable`, or `VisionAnchorGenerationError` with `vision_llm_request_failed`). Content failures aren't refunded.
- **Vision fallback on exhaustion:** the `vision_fallback_on_exhaustion` entitlement (below) decides whether a step degrades to keyword anchors instead of stopping the compile when the vision pool is exhausted. Fetched per compile and applied locally (`backend.py::_apply_vision_fallback_entitlement`); `SKILL_VISION_ANCHOR_FALLBACK_ON_EXHAUSTION` is the offline fallback.

### 13.5 Entitlements & Meters

Defined in `PLAN_LIMITS` (`entitlements.py`), exposed at `GET /api/v1/entitlements/current`.

**Numeric meters:** `seats`, `machines` (§13.6), `execute_seats` (§13.7), `compile_credits`, and `human_edit_tokens`, shown as **"AI Usage Credits"** and dual-emitted as `ai_usage_credits` (§3.3). Internal names (`usage_class="human_edit"`, `human_edit_pool_exceeded`) are unchanged. `execute_chat` meters separately but draws from the same pool (`_combined_pool_used`).

**Capabilities:**

| Key | Values |
|---|---|
| `distribution` | `internal` (Free) / `external` (Starter+) |
| `white_label` | Enterprise only |
| `ops_tier` | `none` (Free), `basic` (Starter), `full` (Pro, Enterprise) |
| `compile_pool` | `free` / `starter` / `pro`; `premium` for Enterprise/Development |
| `byok` | Enterprise only (§13.9) |
| `vision_fallback_on_exhaustion` | default `False`; **not plan-gated**, an ops lever set via `entitlement_overrides` |

**Plans:**

| Plan | Seats | Machines | Execute seats | Compile credits/mo | AI Usage Credits/mo | Other |
|---|---|---|---|---|---|---|
| `free` | 1 | 1 | 1 | 25 | 500K | 30-day trial, internal, `ops_tier` none |
| `starter` | 3 | 3 | 25 | 200 | 2.5M | external, basic ops, own pool |
| `pro` | 10 | 10 | 100 | 500 | 10M | external, full ops, own pool, Conxa-branded |
| `enterprise` | overrides | overrides | overrides | overrides | overrides | white-label, full ops, `premium` pool, BYOK |
| `development` | unlimited | | | | | all capabilities |

Legacy `basic` normalizes to `starter`. **Usage period:** `billing:<current_period_end_unix>` for subscribed workspaces (resets at the payment date), else the UTC calendar month.

**Compile flow** (first compile and recompile alike): `reserve` (with `X-Conxa-Machine`; blocks on quota, machine limit, or trial) → `commit` before the first LLM-bearing stage → `release` if failing before commit (after commit the credit is spent, except refunds above). Compile LLM calls use `usage_class="compile"`.

**Human Edit pool** is spent only by editor-triggered LLM paths: `handlers/workflow_editor.py`, the 1-click fix (`patch.py::_regenerate_compiled_selectors`), the Copilot, and the sandbox `agent_span` / `popup_choice` answerers. Deterministic editor actions work when it's exhausted. The proxy enforces trial and machine registration on every call.

**Workflow-slot ledger:** `record_published_workflow` writes one never-resetting entry per `(workspace_id, workflow_id)` to `entitlement_workflows` on first publish. `_reconcile_workflow_locks` (on every `/entitlements/current` read and every publish; exposed as `workflow_lock`) keeps the most recent `compile_credits`-many workflows active and locks the rest oldest-first. `ensure_workflow_publishable`: republishing an active workflow is fine, a locked one gets 402 `workflow_locked`, a new one at the cap gets 402 `workflow_limit_exceeded`. Company-side only; delta sync is never gated.

**Distribution:** unlimited slugs and publishes. Installer upload accepts `distribution` and `white_label` query params, stored in installer KV metadata. `ensure_distribution_allowed` → 402 `distribution_not_permitted`; `ensure_white_label_allowed` → 402 `white_label_not_permitted`. This is server-side gating on what Studio declares (`installer_builder.py` doesn't stamp it into the artifact). Free's 1-install cap isn't enforced (§13.6). Starter installs are uncapped; its boundary is the visible internal stamp and branding.

**Ops tier** (`ensure_ops_tier`): Free gets 403 `ops_tier_required` on the dashboard, activity, workflow detail, ROI, runs, timelines, and audit log. Basic sees all but drift. Full sees everything. `/tracking/companies` and `/tracking/diagnostics` are ungated.

**Analytics retention:** `analytics_retention_days` (Free 0, Starter 90, Pro 365, Enterprise custom) filters `_visible_run_records` / `_visible_runtime_registrations` on read (`analytics_retention_cutoff_ms`). No write-side prune yet.

**Seats:** Clerk org membership is the source of truth when available (`CLERK_SECRET_KEY`); local state otherwise. `deps.py::current_principal` → `ensure_seats_available` checks a new (user, workspace) pair against the live count before creating the membership, and raises 402 `seat_limit_exceeded` on every request by an over-cap member. The Clerk invite itself can't be intercepted. Existing members are soft-locked on downgrade (`docs/Security.md` SG-18).

**Trial** (Free): `trial_started_at` is stamped on first sight (`saas.ensure_principal`, backstop in `billing_for`). `ensure_trial_active` returns 402 `trial_expired` at the LLM proxy, compile reserve, publish, and installer upload. Never on sync or telemetry, so installed machines keep working.

**Add-ons:** one-time Payment Links (§3.5) credit `credit_wallet` (`{compile_credits, human_edit_tokens}`), which never expires. The monthly allowance is used first (`reserve_compile_credit` marks wallet-funded reservations; `record_llm_usage`/`ensure_human_edit_available` do the same). Catalog `ADDON_TIERS` (+20/+50/+100/+250), served at `GET /api/v1/subscriptions/addons`.

**Stable error codes:** `compile_credit_limit_exceeded`, `human_edit_pool_exceeded`, `seat_limit_exceeded`, `machine_limit_exceeded`, `trial_expired`, `distribution_not_permitted`, `white_label_not_permitted`, `ops_tier_required`, `workflow_locked`, `workflow_limit_exceeded`, `entitlements_unavailable`, `invalid_usage_class`.

### 13.6 Machine Binding (Build Side)

Stops a single-machine trial from becoming a free multi-seat install; the general build-seat integrity control.

- Studio hashes Windows `MachineGuid` (`HKLM\SOFTWARE\Microsoft\Cryptography`) with SHA-256 (`services/machine_id.py`) and sends it as `X-Conxa-Machine` via `backend.py::_cloud_json` and the LLM proxy client. Only the hash leaves the machine.
- `app/api/machine_binding.py::register_request_machine` → `ensure_machine_slot`, enforced at `_meter_and_call` and `post_compile_reserve`. A known hash is touched (last_seen/last_ip); a new hash within `machines` is registered; at the limit it gets 402 `machine_limit_exceeded`. A revoked machine re-enters through the limit check.
- One `machines` limit covers both device slots and distinct active IPs (`ponytail:` simplification).
- Builds without the header aren't enforced; `settings.entitlements_enforce_machines` is the switch. Execute calls skip machine registration.
- **No install-side enforcement.** `POST /telemetry/runtime-start` is public and spoofable, so capping installs there would be a false control; a `pack.json` machine lock was tried and reverted because it broke legitimate reinstalls (TODO.md CLOUD-3).

**Installer naming and icon:** without an explicit `?filename=`, Free gets a random 10-letter name (`_random_installer_name()`). Paid plans use the workspace's unverified installer domain (`GET`/`POST /entitlements/installer-domain`, admin; pending verification, PROD-6), else `{slug}-Setup.exe`. The `.exe` icon (`installer_builder._stage_logo_icon`) is dropped on Free by `cmd_build_installer` (fail-safe: no icon if the entitlement call fails). This is separate from Enterprise-only `white_label`.

### 13.7 Execute Seat Grants

`execute_seats` caps how many non-members a workspace can grant Conxa Execute. Grants are standalone and never touch `seats`; members get Execute automatically.

**Data** (KV, `entitlements.py`):
- `execute_grants[grant_id]`: `{grant_id, workspace_id, email, status: pending|claimed|revoked, granted_at, granted_by, claimed_at, claimed_user_id, revoked_at, revoked_by}`;
- `execute_grant_by_user[user_id]`: the claimed primary pool (`execute_pool_binding_for_user`, O(1) for the metering hot path when no `target_workspace_id` is given).

**Functions:** `create_`/`revoke_`/`list_execute_grants`, `execute_grant_count` (same `_locked_store` discipline as machines); `claim_execute_grant(*, grant_id, user_id, email)`; `auto_claim_pending_grants_for(*, user_id, email)`; `list_claimed_grant_workspaces_for(user_id)`; `execute_chat_access_for(user_id, workspace_id)` (personal workspace, claimed grant, or real Clerk org membership).

**Never a synthetic `Principal`:** `ensure_ai_credits_available` (née `ensure_human_edit_available`)/`record_llm_usage` go through `saas.ensure_principal`, which would write a membership row and inflate `seats`. Execute uses workspace-scoped twins, `ensure_execute_pool_available` / `record_execute_pool_usage`, built on read-only `billing_for_workspace`, with the same `_locked_store("llm-usage:{ws}:{period}")` key, so concurrent grantees are safe.

**API** (in-process):
- admin: `GET /entitlements/execute-grants`, `POST /entitlements/execute-grants {email}` (checks the cap; idempotent for a pending email; no invite URL), `POST …/revoke {grant_id}`, fallback `POST …/claim {grant_id}`;
- `GET /api/v1/execute/contexts` (§3.6): every usable workspace with `credits_remaining`, auto-claiming pending grants for the caller's verified email.

### 13.8 Runtime Fleet Registry

The opposite of §13.6: **no limit, ever.** Installed runtimes register for visibility, security, and support only. This path must never call `ensure_machine_slot` or read `PLAN_LIMITS["machines"]`.

- `server.js::_phonehome()` posts `install_id`, `platform`, `runtime_version`, served workspace ids, `hostname`, `username` (the local OS account, the only "who" available), `os_release`, `os_arch`, and `capabilities` (`max_recovery_tier`, `update_channel`) to `POST /api/v1/telemetry/runtime-start`, plus `skill_versions` and `sync_errors` (§5.5). Optional fields are sticky.
- `GET /api/v1/telemetry/runtimes`: workspace-scoped, `limit`/`offset` (1–500), with `stale_count`/`version_distribution` over the full filtered set (in-process slice, fine to ~10k rows).
- `POST /api/v1/telemetry/runtimes/revoke` (owner/admin): a dashboard label only; never affects sync, phone-home, or execution. No un-revoke; a reinstall gets a fresh `install_id`.
- UI: `conxa-cloud/frontend/src/FleetPage.tsx` (`/fleet`), status `active`/`stale`/`revoked`. Contract: `docs/Backend-Schema.md` §5.9a.

### 13.9 Enterprise BYOK (Azure OpenAI)

A compliance feature: vision anchors send screenshots of internal screens to LLM providers, which fails bank security reviews. With BYOK, screenshots stay in the customer's Azure tenancy. Gated on `byok`. Bedrock and Vertex are in TODO.md.

- **Storage** (`app/services/byok.py`): KV `workspace_llm_keys`, `{provider: "azure_openai", endpoint, deployment, api_version, nonce_b64, ciphertext_b64}`. AES-256-GCM under `SKILL_BYOK_ENCRYPTION_KEY` (32 bytes, base64). A missing key refuses to encrypt or decrypt (`byok_not_configured`), never plaintext.
- **Routes** (`app/api/byok_routes.py`, owner/admin): `PUT`/`GET`/`DELETE /api/v1/workspace/llm-key`. `GET` returns metadata only.
- **Routing:** `byok_pool_entry_for(principal)` builds a one-off `PoolEntry` (endpoint `{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}`, `auth_style="api_key_header"`). `_meter_and_call` checks it first and uses `call_entry_directly`. `_is_openai_compatible_endpoint` accepts full `/chat/completions` URLs.
- Compile credits still apply (they meter reach, not Conxa's token cost).


---

## 14. Storage

### 14.1 KV Store

`conxa_core/db.py` is a dual-mode key-value store:

```
PostgreSQL (SKILL_DATABASE_URL set)          Filesystem (unset; dev / Build Studio)
  kv_store(namespace TEXT, key TEXT,           data/kv/{namespace}/{sha256(key)}.json
           data JSONB, created_at, updated_at)
  PRIMARY KEY (namespace, key)
```

With `SKILL_AUTH_REQUIRED=true` the app refuses to start without `SKILL_DATABASE_URL`; there's no filesystem fallback in production. `db.healthcheck()` backs `/readyz`.

Main namespaces (full map: `docs/Backend-Schema.md`):

| Namespace | Contents |
|---|---|
| `workflows`, `skill_packs_meta` | Workflow / SkillPack records by `workspace_id` |
| `sync_tokens`, `tracking_tokens` | `workspace_id` → `{token, version, workspace_id, owner_user_id, updated_at}` |
| `skillpack_files__{ws}`, `release_files`, `skillpack_known_skills` | published files, immutable release snapshots, per-skill rows (`archived`) |
| `installer_versions__{ws}` | installer binaries (base64) + metadata |
| `component_versions`, `manifest` | per-component versions; the signed unified manifest |
| `tracking/{ws}` | `run_id` → event batches |
| `runtime_registrations` | fleet registry (§13.8) |
| `entitlement_usage`, `compile_reservations`, `entitlement_workflows` | usage per period, reservations, workflow-slot ledger |
| `execute_grants`, `execute_grant_by_user` | Execute seat grants (§13.7) |
| `workspace_llm_keys` | BYOK (encrypted) |
| `cashfree_sub_workspace`, `cashfree_orders_granted` | billing mapping, add-on idempotency |
| `rate_limits` | delta-sync rate limiting |
| `selector_cache` | DOM hash → selector candidates (GC loop in `main.py` lifespan, every 6 h + startup) |

### 14.2 File Storage

- Build Studio: see §2.3.
- Cloud disk (`data/skill-packs/`, `data/installers/`): a fast-path cache over KV, rehydrated on miss (§5.8).
- Runtime: see §4.5.

---

## 15. Security Model

Numbered gaps and fix status live in `docs/Security.md` (SG-01…). This section is the boundary summary.

| Boundary | Mechanism |
|---|---|
| Cloud API | Clerk JWT (RS256 via JWKS); `require_admin` RBAC on publish, release, rollback, archive, workflow create/delete, billing, BYOK, grants |
| Build Studio / Execute login | Clerk PKCE (S256), tokens in OS keyring / `safeStorage` |
| Runtime → cloud | Installer-embedded `sync_token` (read-only, one workspace) and tracking token; `compare_digest` |
| Target-platform sessions | AES-256-GCM, per-machine key in the OS keychain; never leave the machine; never in build output |
| Update integrity | Ed25519-signed manifest (key baked into host) + SHA-256 per artifact + `--selfcheck` for the host |
| Governance policy | Ed25519-signed, same key; signed `expires_at` (§12.5) |
| Telemetry integrity | HMAC chain per batch (§12.4); no-token fallback rejected in production (`SKILL_TRACKING_HMAC_SECRET`/`SKILL_AUTH_REQUIRED`, SG-05) |
| Installer download | HMAC-SHA256 `ts`+`sig`, 10-min window, when `SKILL_INSTALLER_SIGNING_KEY` is set (SG-07); public in dev |
| Proxy identity (Execute) | HMAC-signed; static-secret fallback kept for an old deployment (§3.3) |
| Body limits | 1 MB general; 250 MB publish/upload; 8 MB vision/Execute chat |
| CORS | Explicit allow-list (`SKILL_CORS_ORIGINS`) + Vercel preview regex |
| Local inbound surface | Hand-over loopback listener only while pending, per-pause token (§11.4); Execute's browser control channel is loopback |
| Recovery safety | Uniqueness-gated resolution and overrides; destructive/entity halts; safe-label dismissal allow-list (§10, §9.6) |

**Accepted risks:**
- The sync token is shared by all of a workspace's installs, so a leaked installer grants read-only access to that workspace's released skills (not sessions).
- Runtime identity is per workspace, not per end user.
- Local file-drop signals (`resume/`, `login-done/`) trust the local user.

---

## 16. Deployment

### 16.1 Dev/Prod Isolation

One switch, **`CONXA_ENV`** (`dev` | `prod`), selects two fully isolated stacks that coexist on one machine.

- **Python** (cloud + Studio backend): `conxa_core.config.active_environment()` loads `.env.{dev,prod}` (`env_files()`), sets `settings.environment`, and refuses a `prod` process with auth off. `state_base_dir()` honors `CONXA_STUDIO_HOME`. Defaults to **dev** (the risk on a workstation is touching prod).
- **Runtime** (`runtime/app/env.js`, bundled into the host and applied first in `bootstrap.js`): normalizes `CONXA_DIR`, `CONXA_DATA_DIR`, `CONXA_APP_DIR`, `CONXA_UPDATE_CHANNEL`, `CONXA_API_URL`. Defaults to **prod** (a shipped install without `CONXA_ENV` must keep `~/.conxa` and `stable`).
- **Studio** (`conxa-builder/electron/env.js`): injects cloud, Clerk, and `CONXA_STUDIO_HOME` into the backend. Shipped default is prod.

| Concern | Dev | Prod | Lever |
|---|---|---|---|
| Env file | `.env.dev` | `.env.prod` | `CONXA_ENV` |
| Runtime install/data | `~/.conxa-dev` / `Conxa-Dev` | `~/.conxa` / `Conxa` | `CONXA_DIR`, `CONXA_DATA_DIR` |
| Studio state | `~/.conxa-build-studio-dev` | `~/.conxa-build-studio` | `CONXA_STUDIO_HOME` |
| Cloud API | `127.0.0.1:8000` | `apis.conxa.in` | `CONXA_CLOUD_API`, `CONXA_API_URL` |
| Update channel | `dev` | `stable` | `CONXA_UPDATE_CHANNEL` → `?channel=` |
| MCP entry key | `conxa-dev` | `conxa` | `env.js` dev flag (§4.3) |
| Billing | Cashfree `TEST` | Cashfree `PROD` | `CASHFREE_ENV` |

- **Operator switch:** `scripts/conxa.sh <dev|prod> <backend|frontend|studio|runtime>` (`conxa.ps1`; `make dev-*`/`prod-*`).
- **`pack.json` URLs** (`sync_endpoint`, `tracking_url`) are frozen at publish from the cloud's `SKILL_API_BASE_URL`, so dev installers never phone prod.
- **Releases go straight to stable:** a clean `app-vX.Y.Z`/`host-vX.Y.Z` tag builds in CI and posts to the prod cloud's stable channel (`CLOUD_API_URL`/`CLOUD_ADMIN_TOKEN`) via `POST /api/v1/admin/component-versions/{component}`. There's no hosted dev cloud and no promotion workflow; `?channel=dev` exists only for a local runtime pointed at a local backend. The cloud re-signs the manifest on every publish. Details: `SHIP-GUIDE.md`.

### 16.2 CI Gates

- `build-runtime-host.yml`: builds the host exe with `--no-bytecode`, verifies `host-manifest.json` (`check_host_manifest.js`), runs the execution gate (`runtime/test/gate_replay.js` + `gate-skill/`) against the fresh exe, tags `host-vX.Y.Z`.
- `build-runtime-app.yml`: obfuscates the app layer (`page_scripts.js` with mangling only, §4.1), runs the gate against the declared `MIN_HOST` exe **before** zip/release/publish, tags `app-vX.Y.Z`. A red gate usually means `MIN_HOST` is stale.
- `check_recovery_purity.js`: Tier A stays zero-token.
- `build-studio.yml`, `build-execute.yml`: Electron + NSIS desktop builds.

### 16.3 Cloud Backend (Render)

```
Root:          conxa-cloud/backend/   (Dockerfile also available; context = repo root)
Build:         ./build.sh   → pip install ../../packages/conxa-core && pip install -r requirements.txt
Start:         ./start.sh   → uvicorn app.main:app --host 0.0.0.0 --port $PORT  (init_db() on startup)
Liveness:      GET /healthz
Deploy gate:   GET /readyz  (DB ping)
```

With `SKILL_AUTH_REQUIRED=true`, `app/main.py::_validate_production_config` refuses to boot unless these are set: `SKILL_DATABASE_URL`, `SKILL_CLERK_ISSUER`, `SKILL_CLERK_JWKS_URL`, `SKILL_CORS_ORIGINS`, `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY`, `CASHFREE_WEBHOOK_SECRET`, `CASHFREE_STARTER_PLAN_ID`, `CASHFREE_PRO_PLAN_ID`, `SKILL_API_BASE_URL`, `SKILL_TRACKING_HMAC_SECRET`, `SKILL_INSTALLER_SIGNING_KEY`, `CONXA_MANIFEST_SIGNING_KEY`, and at least one `*_API_KEYS`. (Without the manifest key the manifest is served unsigned, every runtime discards it, and self-updates stop silently.)

**Optional** (degrade gracefully when unset):
- `LLM_STARTER_*` / `LLM_PRO_*`: tier pools (§13.2); unset gives no quality lift over Free.
- `SKILL_BYOK_ENCRYPTION_KEY`: BYOK refuses with `byok_not_configured`.
- `SKILL_RESEND_API_KEY`, `SKILL_BUG_REPORT_TO_EMAIL`: bug reports return `503 bug_reports_not_configured`. Resend is used because Render's free plan blocks SMTP.

The service runs on Render's free plan (ephemeral disk, §5.8). Conxa Execute has no service of its own (§3.6).

### 16.4 Cloud Frontend (Vercel)

Root `conxa-cloud/frontend/`, `npm run build`. The route handler `/api/v1/*` proxies to `API_ORIGIN`. Env: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `API_ORIGIN`.

### 16.5 Desktop Apps

- **Build Studio:** `electron-builder` + NSIS `.exe`, shipping Electron and the PyInstaller backend (`dist/backend/`, bundles `conxa_core` + `conxa_compile`). Chromium, NSIS, and the runtime are fetched at first launch (§2.4). Auto-updates via electron-updater from `/api/v1/updates/studio/latest.yml`.
- **Conxa Execute:** same shape (`build-execute.yml`), feed `/api/v1/updates/execute/latest.yml`, plus its own Clerk OAuth client (§3.6).
- Auth and updater details: `docs/Auth-and-Updater.md`.

### 16.6 Runtime (End-User Machine)

Ships inside the workspace installer (§8). Per-user, no UAC:
- **Windows:** `%USERPROFILE%\.conxa\` with versioned `conxa-runtime/` and `conxa-app/` plus `current` junctions (§4.5).
- **Mac:** build scripts exist (`npm run build:mac`); Windows is primary.

MCP registration is done only by `conxa-runtime.exe register-mcp` (§4.3). For Claude Desktop it detects the Microsoft Store/MSIX path (`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`) before `%APPDATA%\Claude\`, avoiding MSIX filesystem virtualization issues, and the equivalent for every other host.

---

## 17. Known Gaps & Tech Debt

Open items only; resolved history is in `Done.md`. The backlog of record is `TODO.md`.

| Gap | Where | Severity | Notes |
|---|---|---|---|
| Delta sync ships every file of a changed skill | `skillpack_update_routes.py::_build_delta` | Low | Per-skill granularity is real; per-file diffing within a skill isn't |
| Shared installer sync token | `sync_tokens` + `pack.json` | Low | Read-only, one workspace; sessions are protected by per-machine keys |
| Runtime identity is per workspace | `auth_manager.js` | Medium | No per-end-user identity at runtime |
| Fine-grained RBAC | `app/services/rbac.py` | Medium | `require_admin` only; per-skill/analyst roles pending |
| Free 1-install cap unenforced | §13.6 | Medium | Needs authenticated install provisioning (TODO.md CLOUD-3) |
| No CDN/blob storage | `blob_read_write_token` unwired | Low | Base64-in-Postgres is durable; revisit near the 250 MB upload cap or DB limits |
| `CONXA_REQUIRED_RUNTIME` default still `>=1.0.3` | `skill_package_builder_output.py` | Medium | Branch, `for_each`, choice, and hand-over handlers now ship in tagged app layers; until the floor is raised, a very old runtime silently skips those steps (§11) |
| Cancelled file-picker click with no upload | `step_anchors.py::clean_steps` | Medium | The lone click survives compile; the runtime's `filechooser` listener suppresses the picker, but the step's intent is lost (TODO.md BUILD-13) |
| `failed_step_key` / overlay positions can mis-resolve after reorders | `editor/evidence.py` | Low | Full fix: persist `step_key` into `execution.json` (§7.4) |
| Some `page.evaluate()` calls bypass `page_eval.js` | `runtime/app/` | Medium | TODO.md EXEC-29 follow-up |
| Hand-over not durable across restart; not resumable inside `for_each` | §11.4 | Medium | EXEC-22; no compile guard for hand-over in loops |
| No authoring UI for `try_dismiss`/`wait_for_one_of` options, `for_each` bodies, Strict Mode, entity-binding confirmation | Human Edit | Low | PROD-3-UI |
| Analytics retention is read-side only | `app/services/tracking.py` | Low | No write-side prune |
| Concurrency cap is flat | `run_registry.js` | Low | RT-3-CAP-SIZING |
| `beforeunload` dialogs unhandled | recorder + runtime | Low | TODO.md |

### Guided tour hint injection (BUILD-79)

`electron/tour_hint.js` draws a note inside the page only when all hold: run kind is `signin` (auth mode installs no recorder bridge, so nothing is captured), the page host equals the Conxa website host (`CONXA_WEB_ORIGIN`), and a hint is set (`browser_panel.js::_tourAllowed`). Record and test runs never receive it, because hint DOM and clicks would reach the recording video, DOM snapshots and trusted-click events. The practice company at `/practice` is a Cloud frontend route that needs a sign-in but no workspace; its data lives in the browser only.
