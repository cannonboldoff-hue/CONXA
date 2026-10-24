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

### 4.2 MCP Tools

Defined in `server.js::_toolDefinitions()`:

| Tool | Description |
|---|---|
| `list_skills` | Installed skills, optionally filtered by company |
| `get_skill_inputs` | Input schema for a skill |
| `execute_skill` | Run one skill. Options: `dry_run`, `watch`, `step_overrides`, `review_results`, `resume_from` (below) |
| `execute_sequence` | Run an ordered list of skills in one browser session |
| `get_execution_status` | No args: `{active_runs, awaiting_auth, recent}` (running runs, runs detached waiting for sign-in with each app's `waiting`/`signed_in`/`closed`/`failed`, last 10 outcomes). With `run_id`: that run's `state` (`running`/`awaiting_auth`/`completed`/`failed`/`cancelled`/`unknown`) and, once finished, its `summary`. Also surfaces `waiting_for_host` (§4.6) |
| `cancel_execution` | Cancel a run; `run_id` required when more than one is active |
| `authenticate` | Open sign-in for every app in a skill's group and block (bounded) until done. Args: `skill` or `workspace_id`, `wait_seconds` (default 45, max 600). Returns `{status: signed_in\|waiting\|failed, apps?, message?, next}`. Idempotent: calling again re-waits on the open window. Parks the warm session for the next `execute_skill` (§4.6) |
| `get_runtime_status` | Diagnostics. `staged_runtime_version` is non-null when a host update is activated on disk but this process is the old exe |
| `create_schedule` / `list_schedules` / `delete_schedule` | Local cron schedules (§4.8). `list_schedules` never returns input values |

**Execute-skill options:**

- `dry_run: true` — every step runs except `destructive === true` ones, which are resolved (proving the target exists) but not dispatched (§9.6).
- `review_results: {"<step_index>": answer}` — resume payload for an `ai_review` pause, bound into `inputs` (§11.3).
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
└── logs/runtime.log, recovery.log     JSONL, rotated at 10 MB

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
- **Per-host platform serialization** (`host_lock.js`): runtime isolation doesn't protect the external platform from two runs mutating the same project (lost updates, cancelled deploys, bot detection). A mutex keyed by hostname, from `manifest.target_url` or every required app host of a group, is acquired for all hosts atomically, in sorted order, before any browser work (including auth). A blocked run polls every 250 ms; the wait counts against its deadline, fails with a message naming the host and the blocking run, and shows as `waiting_for_host`. Runs on different platforms stay fully parallel. The lock is held through a park.
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
- **Teardown:** `browser.js::teardownExecBrowser` is the one shared teardown. On a host-owned run, `browser.close()` only disconnects; Execute's `run_end` handler destroys views and clears the partition.
- **Isolation:** each run's views use a non-persistent partition `conxa-run-<runId>`.
- **Panel:** Chrome-style header (`BrowserPanel.tsx`): tab chips, back/forward/reload, editable URL, run selector when several runs are live. Login tabs are labelled with the app name (`label` on `new_view`/`new_tab`, both return `tabId`). Login teardown is tab-scoped, run end is run-scoped (`browser_panel.js::closeTab` promotes a neighbour and calls `runEnd` on the last tab).
- **Login and hand-over:** `_openInteractiveAuthWindow` uses the same host branch; `beginInteractiveAuth`'s non-blocking flow is unchanged. `handover.js`'s in-page resume banner (`exposeBinding` + `addInitScript`) works unmodified against a host-owned context. Because Execute's CDP connection never fires `disconnected` for one view, `_waitForInteractiveAuth` also polls for a closed login page every 1.5 s and has a 10-minute deadline (`LOGIN_WAIT_MS`) that closes the view and marks the attempt `loginTimedOut`.
- **Chat scope:** a panel belongs to the chat that started its run. `main.js`'s chat `executeTool` picks the run id itself (the runtime honors `_run_id`) and calls `browser_panel.js::tagRun(runId, sessionId)`; `panel:tabs` carries `chatId`. On a chat switch or new chat the renderer calls `panel:set-chat` → `setVisibleChat`, which parks (0x0) every view owned by another chat without destroying it, so the run keeps going; `BrowserPanel.tsx` shows only the current chat's runs and re-places the view on return. A new chat's Chromium is still created lazily by its first `new_view`. Runs started outside a chat (the form) are untagged and visible everywhere. Views die with the run (`run_end`), so a finished run's panel does not come back.
- **Hide / capture (2026-10-01):** the title-bar **Browser** toggle unmounts `BrowserPanel.tsx` and calls `panel:hide-all` → `browser_panel.js::hideAll`, which parks every view at 0x0 (a native view floats above the page, so unmounting alone would leave it on screen). Showing it again re-reports bounds. A new run or a login `focus` event un-hides it. `panel:capture` → `captureVisible()` grabs the last tab placed by `setActiveBounds` (`webContents.capturePage()`, JPEG) as a chat attachment. `screen:capture` in `main.js` hides the window, takes the primary display through `desktopCapturer`, and shows the window again.
- **Two run ids:** `server.js` carries `_runId` (per call; names hand-over resume files) and `_hostRunId` (the id Execute registered the view under). They diverge only across park/resume, where `_hostRunId` is restored from `_park.runId`. `run.js::runPlan` takes both as separate opts.

### 4.7a Build Studio Browser Panel — tests (BUILD-42, 2026-10-18), recording and sign-in (BUILD-46, 2026-10-20)

Build Studio reuses the §4.7 seam. A headed test run, a recording and a sign-in all draw their browser inside the Studio window instead of opening a separate Chromium window. Each panel run has a `kind` (`test` | `record` | `signin`); the renderer styles the pane by it.

- **Studio side:** `conxa-builder/electron/browser_panel.js` + `browser_control.js` (the same shape as Execute's, with the differences below). `main.js` picks a free loopback port, sets the `remote-debugging-port` / `remote-debugging-address=127.0.0.1` switches before `app.whenReady()`, starts the control server, and exports `CONXA_HOST_BROWSER_CDP` + `CONXA_HOST_CONTROL_URL` into its own environment **before** spawning the Python backend. The backend's runtime child inherits them (`runtime_tool.py` builds `proc_env` from `os.environ`), so `runtime/app/host_browser.js` takes the borrowed-view path with no runtime change. A run that is headless, or a Studio without the panel, never sets them and launches Chromium as before.
- **A finished run keeps its page.** The design shows the final page so the person can check the result themselves. `run_end` therefore only marks the run finished; the view is destroyed when the person closes the panel or a newer run starts (`closeRun`). Execute destroys on `run_end`.
- **Recording and sign-in in the panel (BUILD-46).** The 2026-10-18 decision to keep them in their own window ("can't be added from outside") was reversed after a spike proved each missing piece could be rebuilt (`docs/archive/spikes/in-app-recording-2026-10.md`, Gate A + Gate B). `SessionRegistry.create` decides once per session: `embedded = in_panel and settings.record_in_panel (SKILL_RECORD_IN_PANEL, default on) and both host env vars set`. Otherwise the launched path runs unchanged. How each recorder need is met:
  - **Page size.** A `record` run is a fixed 1280×720 page at DPR 1, identical to the launched recorder. `browser_panel.js` applies `Emulation.setDeviceMetricsOverride` with `scale = min(w/1280, h/720, 1)` through `webContents.debugger`, issued after the first `loadURL` (attaching earlier crashes Electron). It letterboxes the view in the pane's rect; popups adopt the run's size and scale. A `signin` run has no fixed size — the page fills the pane.
  - **Video.** `recorder/screencast_video.py::ScreencastVideo` rebuilds what `record_video_dir` did: a raw CDP WebSocket per page target (`Page.startScreencast`, JPEG) fed into Playwright's own ffmpeg at a constant 25 fps, repeating the last frame, back-filled from `start_wall_ms`, cropped back to CSS pixels by the panel's scale. It writes the same contract compile reads (§6.4): `recording.webm` / `recording-<tab>.webm` + `videos.json`. Frame extraction is unchanged.
  - **Scripts and bindings per page, never context-wide.** Playwright's one CDP context is Electron's default session and holds Studio's own window and other runs' views. `_classify_pages_sync` asks the panel which devtools targets belong to the run (control op `targets`) and only those pages are adopted (`_adopt_run_page_sync`: page-level `expose_binding` + `add_init_script` for the bridge and dialog override, plus the usual listeners). A sign-in page gets only membership and a close handler.
  - **Sign-in state.** Seeding and capture go through the run page's own CDP session (`host_view.py::seed_storage_state` / `capture_state`, the §4.7 AUTH-20/21 pattern). learn_auth's LIVE capture opens a background tab in the same run (control `new_tab`, `focus:false`), observes it, reads its cookie names from that partition, and closes it; OUT/RELOAD stay standalone headless probes. Known limit: `localStorage` of an origin whose tab already closed keeps what the previous save held.
  - **Owning the browser.** Teardown stops the videos, disconnects (`browser.close()` on a CDP connection only disconnects and drops the page scripts) and posts `close_run`. The panel closes a `record`/`signin` run whose last tab closed, which the recorder sees at once as "the browser closed". Downloads are claimed through `save_download`. Window raising (`bring_to_front`, `AllowSetForegroundWindow`) is skipped.
  - **Where it is drawn.** A conversation-led recording or sign-in shows in the right-hand `BrowserPane` with its controls (the live card mirrors them through `browserStore.flow`). A Group-page Connect is drawn in that page's own sign-in pane (`SignInPane` + the shared `useNativeViewRegion` hook); the right-hand pane ignores a `signin` run no conversation owns. A caller with nowhere to draw the view — any dialog, since a native view is hidden while a dialog is open (`RunGateDialog`, `RecordWorkflowDialog`) — passes `in_panel: false` and keeps the separate window (and, for sign-in, the docked `window_rect` placement).
- **Panel:** `BrowserPane.tsx` + `browserStore`. Built like Chrome: a tab strip (favicon or spinner, title, ×, middle-click closes, **+** opens a tab), back / forward / reload-or-stop, an address bar that loads a URL, adds `https://` to a bare host (`http://` for localhost/IPs) or searches Google (`browser_panel.js::_normalizeUrl`), a right-click menu (`_contextMenu`: open link in new tab, copy link, cut/copy/paste, back/forward/reload) and Chrome's keys caught inside the page through `before-input-event` (`_shortcut`: Ctrl+T/W/L/R, Ctrl+Shift+R, F5, F6, Alt+D, Alt+←/→, Ctrl+Tab/PageUp/PageDown, Ctrl+1–9). A tab the person opens (`personTab`, IPC `panel:new-tab`) is an ordinary run tab with no opener, so the recorder records it as `opened_by: user` and a typed address as `manual_navigate` (the marker page reads as `about:blank` in `from_url`) — verified against real Electron. Folds to a strip, resizes with a focusable divider. A native view floats above the page, so the pane reports its rect to the main process on every layout change (`panel:bounds`) and hides every view (`panel:hide`) while a dialog or sheet is open (`useNativeViewRegion`). Nothing can be drawn over the page, so a run's state lives in a coloured top edge (blue getting ready, amber sign-in, red recording, grey paused), the header and the footer. A record/sign-in pane can widen, folding the conversation to a 64 px rail. While any pane is on screen the left navigation steps aside (`resolveBrowserLayout` → `navMode: 'hidden'`, 60 % default width); it is derived, not stored, so folding or closing the browser restores the person's own navigation setting, and the navigation toggle only flips a per-run `browserStore.navShown` (navigation back as the rail).
- **Isolation:** each run gets the in-memory partition `conxa-run-<runId>`; popups open as tabs; downloads are saved silently under `%TEMP%/conxa-studio-downloads/<runId>/` and claimed by URL through the control channel (`save_download`).
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

**Freshness.** `captured_at` never expires, so `check_app_session_sync(app)` runs a bounded headless probe. It loads the state into a throwaway context, evaluates the learned definition if present, otherwise navigates to the success-URL prefix and classifies `ready` / `expired` (bounced to login, or state missing/corrupt). A navigation error counts as `ready` rather than a false "expired". Every verdict stamps `checked_at` (and `last_error` on expiry) via `set_group_app_checked`. `group_auth_status(group)` is cheap and read-only; it reports `state` plus `verified` (`checked_at` within 600 s). Probes run only from `cmd_start_recording` and the user's "Check now" (`cmd_check_group_app_auth`), never on page load.

#### 5.3.3 Recording with group auth

- **Seeding:** `cmd_start_recording` merges every captured app's state (`storage_state.merge_storage_states`: cookie union, per-origin localStorage merge) into one context.
- **Gate (AUTH-16):** `handlers/session.py::group_apps_needing_sign_in(group)` requires **every app in the group** to be captured and not expired, the same rule as the runtime pre-flight (§5.3.4). A workflow that starts on one app routinely crosses into another (start on GitHub, upload to Drive), so the start URL alone can't say which apps it needs. It probes every captured app not checked in the last 600 s (concurrently, each bounded at 20 s) and returns never-connected or expired apps. `cmd_start_recording` raises `auth_required` naming them before any browser opens; the conversation flow (`workflow_flow.py::_flow_missing_apps`) uses the same function and opens a sign-in card for each in turn ("sign-in expired" vs "needs sign-in"), recording only once the list is empty. `apps_for_workflow` no longer gates anything; it stays for the group page's platform chips.
- **Pre-flight only:** no mid-recording auth checks and no periodic autosave (`context.storage_state()` is heavy and made the window flicker). If a site logs out mid-recording, the user signs back in inline.
- **Write-back:** the recorder autosaves once at teardown to `merged_group_state.json`. `_refresh_group_app_sessions` then splits it per app with `storage_state.refresh_app_state` (keeping only domains/origins that app already owned), writes each app's file and calls `mark_group_app_unverified` (clears `checked_at` only). Cookie rotation and mid-recording re-logins stick, but the write-back never vouches for a session: an app that ended the recording signed out keeps its expired verdict and is probed again at the next gate. (It used to go through `set_group_app_auth`, which stamped every app freshly verified and hid an expired sign-in from the next record click.)

#### 5.3.4 Runtime pre-flight

`browser.js::getAuthContext(company, authManager, {groupId})` → `_resolveGroup` against `pack.json` → `getGroupAuthContext`:

- **Sessions** are keyed `${workspace_id}__${appId}` (`auth_manager.js` functions all take the key). **Precedence is newest-mtime-wins at read time** (`_loadSessionForKey`): a `_raw_state.json` newer than its encrypted sibling (Studio re-staged a session, or an SG-11 plaintext fallback) wins and is promoted to encrypted immediately (`test_session_precedence.js`).
- **Seeding** merges every stored app session, newest first, because `mergeStorageStates` keeps the first cookie per name/domain/path and a stale shared SSO cookie must not win (`test_session_auth.js`).
- **Validation:** only apps without a fresh stamp are probed, as throwaway tabs of the run's own session browser (`_probeInSession`). Stamps live in `<sessions>/_auth_validation_cache.json` for `CONXA_AUTH_VALIDATION_TTL_MS` (default 6 h) and record the session file's mtime, so a fresh login invalidates them (`test_auth_validation_cache.js`). `test_phase` markers `group_auth_validate_start`/`_done` reach Studio's Run Test log.
- **Detection** (pre-flight and the prover) navigates to `login_url`, never `success_url`:
  - with a learned definition: `evaluateAuthDefinition` (§5.3.5);
  - otherwise the **signed-out baseline**: `_signedOutBaseline(session, entryUrl)` loads the login URL in a fresh incognito context (cached per URL for the TTL), and `login_signals.js::isSignedInAgainstBaseline(baseline, current)` answers signed in when the probe is not login-shaped and differs from the baseline. `looksLikeLoginAnswer` = a visible password box, or a path with a `login|signin|sign-in|session-expired` segment at any depth (`LOGIN_PATH_RE`). No host or IdP lists. Keep `LOGIN_PATH_RE` separate from `run.js`'s broader `AUTH_FAILURE_URL_RE`.
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

**Learning at Done.** While the browser is open, `RecordingSession.learn_auth(login_url, existing)` observes the probe URL three ways: **LIVE** (a new tab of the signed-in context), **OUT** (cookie-less context), and **RELOAD** (fresh context from the just-saved state). If `existing` still passes `self_test` against these observations it's returned **unchanged** (so a routine Reconnect doesn't look like a setup change). Otherwise `conxa_compile.auth_learning.learn(live, out)` keeps only signals that differ between LIVE and OUT, and `self_test` must see LIVE→yes, OUT→not yes, RELOAD→yes. `learn_auth` is a cross-thread request/poll bridge to the recorder's sync-Playwright thread.

**Shape:** `{version, probe_url, signed_out: {final_path, markers, password_box}, signed_in: {final_path, endpoints}, session_keys, journey_hosts}`. A pure function of its observations: no timestamp, no cookie values, bodies, or query strings. UI markers with account-specific names are filtered (`_clean_markers`). Ships in `pack.json` `groups[].apps[]`.

**Evaluation:** `login_signals.js::evaluateAuthDefinition` mirrors `auth_learning.evaluate()`; both suites run the shared fixture `runtime/test/fixtures/auth_definition_cases.json`. Four signal classes, each yes/no/null:
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

`session.py::RecordingSession` wraps a Playwright context seeded with the group's merged storageState (§5.3.3). By default that is a view in Build Studio's own browser panel (§4.7a, "embedded"); with no panel it is a launched Chromium window. The steps below hold for both; where the embedded path differs (per-page scripts, screencast video, CDP-session cookies) §4.7a says how.

1. `bridge.js` is injected into every frame via `page.addInitScript`. It captures `click`, `dblclick`, `right_click`, `type`, `fill`, `focus`, `select`, `select_option`, `set_checkbox`, `set_radio`, `date_pick`, `drag_drop`, `keyboard_shortcut`, `upload`, `navigate`, `scroll`.
2. `session.py` synthesizes `tab_open`, `tab_switch`, `popup`, `frame_enter`, `frame_exit`, `browser_back`, `browser_forward`, `manual_navigate`, `dialog_accept`, `dialog_dismiss`. (`dialog_appeared` is declared but has no producer.)
3. Each event carries `action`, `url`, `frame` (iframe chain), `tab`, `target` (element signals), `value`, `ts`. `frame_utils.py::_frame_context_and_offset_sync` accumulates page-level bounding-box offsets up the iframe chain.
4. Events stream through `session_events.py` into `events.jsonl`.
5. On stop, the context closes and each tab's `.webm` gets a stable name (§6.4). Frames are extracted at compile time (§7.1), so a bad frame can be repaired by recompiling.

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
| Otherwise | `manual_navigate` (address bar, bookmark) |

`frameRequestedNavigation` fires on request, not commit, so a cancelled request (`preventDefault()`) leaves a stale flag. The TTL stops it from swallowing the next genuine manual navigation. All three events carry `value = {from_url, to_url}` and the tab that fired them (`src_page`).

**Compiled as real steps:**
- `browser_back` / `browser_forward` → `intent=history_back|history_forward`, `no_recovery_block`, `wait_for: url_change` on the recorded URL. Replay calls `page.goBack()`/`goForward()` on the resolved tab (`handlers.js`), never a guessed URL. The CI gate skill covers this.
- `manual_navigate` → a plain `navigate` step (`build.py::_navigate_step`): `page.goto(to_url)`, no target, intent, or anchors. **Except** one an action caused (below).

**A navigation an action caused is never a step.** Click "Submit" and the app redirects to `/orders/847291?token=...`: replaying that URL would hardcode a per-run id/token and force the page somewhere instead of letting the app decide. `compiler/navigation_effects.py::fold_action_caused_navigations` runs right after `clean_steps`/`fix_step_order` (before `_insert_tab_markers`, so the user-opened-tab navigate and the start navigate are never candidates). It drops a `manual_navigate` that is tagged `cause: "action"`, or (legacy sessions) that starts from the page of the previous same-tab interaction within 5 s, and records the destination on that interaction's `post_condition.url_delta`. `_build_assertions` then adds an **advisory** (`required=False`) `url_pattern` built by `generalize_url_pattern`: origin and literal path words kept, any segment with a digit or 16+ characters wildcarded, query and fragment dropped. The runtime never `goto`s it; it clicks, waits, and lets the app land where it lands. Genuine address-bar navigations, back/forward, popup and tab events are untouched.

**Two gates fail silently if a new navigation kind is missing:**
1. `ActionKind` (`conxa_core/models/events.py`) is a strict `Literal`. An unknown kind fails validation inside `_consume_payload_safe_sync`, lands in `binding_errors`, and never reaches `events.jsonl`.
2. `step_anchors.py::clean_steps` dedupes consecutive same-action, same-element events. Navigation kinds have empty element keys, so each must be exempted or A→B→C collapses into one step.

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
- **Click "after" capture waits for the DOM to settle.** Click listeners run in the capture phase, before the page's own handler, so a synchronous snapshot diffs the page against itself. `finalizeStateAfterSettle()` waits for a mutation-quiet window (`click_settle_quiet_ms`, 20 ms) capped at `click_settle_max_ms` (250 ms), queued in submission order.
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

**Alignment rule:** passes that read `cleaned_events[i]` for `steps[i]` (`_populate_hover_chains`, choice and date-picker collapsing, `_build_image_by_step_key`) must run while the two still align 1:1, or receive a synced representative-event list (as `collapse_choice_group_runs` returns).

### 7.2 Control-Specific Compilation

Each feature follows the same shape: the recorder captures context, the compiler derives `handler_hints.control_kind` plus a payload, and a pure runtime module (unit-tested without a browser) drives replay. Everything is Tier A (zero LLM). A skill compiled without `control_kind` is unaffected.

#### Date pickers (`control_kind = "date_picker"`)

Native `<input type=date|datetime-local|…>` already records a `date_pick` from `change`. Custom widgets (MUI, react-datepicker, flatpickr, Ant, jQuery UI) record as unrelated clicks with a frozen month-nav count and an ambiguous day text, and no recovery tier can fix that.

- **Record** (`bridge.js::buildDateContext`, on `click` and `select`): tags events inside a detected calendar grid with `date_context`: the role (`day`/`nav`/`time`/`year_select`/`month_select`), `grid`, the resolved ISO date, and the day cell's source attribute (`data-date`, `aria-label`, …) so a different date can be re-queried.
- **Compile** (`compiler/date_picker.py::collapse_date_picker_runs`): a maximal run of steps sharing a `date_context.grid` becomes one `date_pick` step (two for a range: `role: range_start|range_end`). A run with no day cell (for example year/month selects only) is left uncollapsed rather than inventing a day.
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
| `region_selector_vision.py` (`region_selector`, vision) | "Draw a new region" re-target only; screenshot + highlighted region + DOM, because recordings store no per-element geometry | — |

**Selectors are deterministic on the primary path.** `identity_bundle.py::generate_deterministic_signals()` reads the recorded DOM and emits Playwright-grammar signals ranked by durability. No LLM writes or scores selector strings (LLM-written selectors hallucinate ~30% of the time). `llm_selector_generator_v2.to_playwright_grammar()` survives only as a string formatter. Missing `input_type` is inferred by policy regex.

**Vision anchors.** `_downscale_and_encode` always re-encodes the screenshot as JPEG ≤ 1024 px on the long side, including when the bbox is missing; a raw frame is never sent. Recoverable failures (`_RECOVERABLE_VISION_ANCHOR_REASONS`) produce a per-step `vision_anchor_fallback` warning plus one aggregate `vision_anchor_fallback_summary` log event per compile.

**Workflow review** (`llm/workflow_review.py::build_workflow_review`): one multimodal call, after bindings are deduplicated and the first compile report exists. It sees every step's chosen anchor screenshot as `image_url` blocks keyed by `step_key`. Images are trimmed to a byte budget (`build.py::_trim_review_images`; highest-confidence steps drop first) and excluded from the cache key. It returns:

1. the **WorkflowIntentGraph** (goal, per-step `intent_token` and prose; parsed by `workflow_intent.py::_graph_from_raw`). `_apply_workflow_review_to_steps` always backfills `semantic_description` and fills `intent` only where the heuristic left it blank. There's no per-step LLM fallback;
2. the **second-opinion findings** (validated by `workflow_semantics.py::_validate_findings`), applied by `compiler/second_opinion.py::apply_second_opinion`.

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

**Failure and cost.** `SKILL_LLM_SEMANTIC_SUGGESTIONS_ENABLED` (default on) gates findings; the intent graph always generates. `llm/llm_cache.py` caches by content hash of the payload, URLs, and sibling bindings; failed or empty responses aren't cached. Any exception degrades to `(WorkflowIntentGraph(), [])`. The backfill runs outside the findings `try`, so disabled and failed stay identical. `compile_report["second_opinion"]` records what was applied (only when something was), and the report is rebuilt whenever anything was applied, archived, or backfilled.

**Reviewer edit log** (`editor/edit_log.py`): every mutating Human Edit command (hooked once in `backend.py::Backend.dispatch`, so undo/redo are covered) appends one line per changed field to `data/skills/{skill_id}/edits.jsonl`. Keyed on `step_key`, allow-listed fields only (`input_binding`, `value`, `intent`, `semantic_description`, `action.action`, `optional_hint`, `branch`, `validation.assertions`); failure-silent. `conxa-cloud/scripts/eval_suggestions.py` reports `override_rate` (edits on fields the pass wrote), `miss_rate`, and the copilot's accept and verified-fix rates.

### 7.4 Human Review Copilot

"Conxa Copilot" is a chat panel in Human Edit (`components/copilot/CopilotPanel.tsx`, `CopilotLauncher.tsx`; floating, not in the Tools rail). It diagnoses the latest test failure and proposes edits as accept/reject diffs. **The copilot proposes, the reviewer disposes.** It's a user-initiated editor action and never writes an element address.

#### Evidence bundle

`editor/evidence.py::build_evidence_bundle`, keyed on `step_key`:
- Runtime failure capture: `runs/{run_id}/_evidence/{evidence.json, failure.jpg, pre_step.jpg?, overlays.jsonl}` in Studio's sandbox `data/`.
- The compiled step's `identity_bundle`, `compiled_selectors`, `validation.assertions`, `target`, `intent`, `semantic_description`, `phase`, `consequence`.
- Compile-report confidence/warnings, `second_opinion`, `archived_steps`, or `{"available": false, "reason": "workflow edited since last compile"}` when `_invalidate_compile_report` has staled it.
- The recorded event's `post_condition.classified_effect` / `state_change.dom_diff` (`retarget.py::find_source_event`), deterministic prose (`describe.py::describe_step`), and prior edits (`edit_log.read_edits`).
- `last_test` (`{status, error, at}` from the `Workflow` record) and `runtime_evidence_absent` (a reason when evidence is empty). The prompt then treats `last_test.error` as authoritative instead of inferring a cause.

`Workflow.last_test_run_id` (set by `cmd_test_workflow` whatever the outcome) is the fallback run id.

**Failed-step resolution.** `failed_step_key = keys[failed_at]` uses a position frozen at failure against the current document. `evidence.json` also stores `failed_stable_hash`, and `_resolve_failed_step_key` nulls the key (and explains in `runtime_evidence_absent`) when the resolved key's base hash differs. With no recorded hash (older evidence, multi-skill sequences, marker steps) the position is trusted. Residual gaps: a reorder of two steps sharing a `stable_hash`, and `observed_overlays` resolution (no hash to check). The full fix is persisting `step_key` into `execution.json`.

**Runtime capture.** `failure_response.js::_writeStudioEvidence` runs on Studio failures (`!agentRecoveryEnabled`, since Studio forces tier 2). It writes `evidence.json` (element inventory, overlay probe, a slug + `ts`-filtered recovery-log tail, `failed_stable_hash`, `action_may_have_taken_effect`, `recovery_halt_reason`, `destructive_halt`, `verify_fail`) and a live `failure.jpg`. It never throws; `sweepOldRuns` reaps it.

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

1. **Field patches** `{step_key, field, patch, why}` (`copilot_proposals.py::gate_proposals`) run through `patch_gate.py::validate_editor_patch`. Allowed fields (`_ALLOWED_FIELDS`): `value`, `input_binding`, `intent`, `semantic_description`, `validation.assertions`, `consequence`, `entity_binding.confirmed`, `branch.timeout_ms`, `for_each.max_iterations`, `for_each.on_row_error`, `handler_hints.hover_chain`, `ai_review_*`, `handover_*`. Not `recovery` (the runtime never reads it). Accept re-resolves `step_key` (`resolve_step_index`, else `proposal_stale`) and calls `cmd_patch_step`.
2. **Overlay branches** `{overlay_id, control_index, primitive: try_dismiss|if_present, after_step_key, why}` (`gate_overlay_proposals`). The model picks from captured overlays and Python materializes selectors:
   - *Capture:* `cascade.js`'s `dismiss-overlay` remedy (on an `INTERCEPTED` classification) probes via `page_scripts.js::overlayProbe` (controls carry the resolver's six fields via a duplicated `controlDescriptor`; the container gets a `signal` from the id > role > css-path ladder). `overlay_capture.js::recordOverlayObservation` writes `overlays.jsonl`, deduped per run, whether or not the step passes. This is the only overlay capture on passing runs. `runId` is threaded explicitly for concurrency.
   - *Reading:* `evidence.py::_read_runtime_evidence` reads `evidence.json` and `overlays.jsonl` independently and exposes `observed_overlays`.
   - *Synthesis:* `editor/overlay_identity.py::bundle_from_descriptor` (pure) builds testid/css-id/role+name/text candidates with the compiler's own durability table, grammar, and filters. Signals get `source="runtime"`, `unique_at_compile=False`, and a Python-computed `stable_hash` (the resolver uses it only as a tie-breaker).
   - *Gating:* unknown `overlay_id` is dropped. `try_dismiss` uses `build_try_dismiss_from_hint` (its third caller). `if_present` targets the container signal with one nested click; with no container signal it downgrades to `try_dismiss` (an empty target makes `_saved_branch_step` silently drop the step).
   - *Accept:* `cmd_insert_step` + `cmd_patch_step` (+ `cmd_insert_branch_step` and a nested `cmd_patch_step` at `branch.steps[0]`) in one dispatch. Undo takes several presses. Rejections log against `overlay:{overlay_id}`.
3. **Structural ops** `{op, why, evidence_refs, …}` with `op` ∈ `insert_step`/`delete_step`/`move_step`/`update_inputs`/`replace_literals` (`gate_structural_proposals`). `evidence_refs` is required. An `insert_step` of a `SELECTOR_ACTIONS` kind must name `identity_from_step_key` (an existing step whose `identity_bundle`/`target` Python copies verbatim); other kinds must not. Accept (`_accept_structural_proposal`) is all-or-nothing: snapshot, run each op through its `cmd_*`, restore and drop undo entries on failure, collapse to one undo entry on success.

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
- **Thread store (`thread_store.py`).** Append-only JSONL: `data/workflows/<workflow_id>/thread.jsonl` (beside the workflow, not the skill — `skill_id` is born at compile and changes on re-record) and `data/chats/<chat_id>.jsonl` plus `chats/index.json`. Kinds: `user_message`, `assistant_message`, `event`, `card`, `summary`, `import_marker`. A card is *revised* by appending a line with the same `card_id` and a higher `revision`; reads fold to the latest at the first position, so history is never rewritten. Fail-silent like `copilot_sessions.py`. The old per-skill `copilot_sessions.jsonl` is imported read-only, once per skill.
- **Harness (`harness.py`).** One turn loop for Chat and workflow threads. (1) Deterministic routing first: a click on a card, or free text mapped to one of the card's allowed choices (exact label match, else one short `chat_map_answer` call; an id not on the card means "unclear" and nothing is applied) goes through the same executor and never reaches the conversational model. (2) Context under a budget (`context_pack.py`, 12k tokens for a workflow turn, 6k for chat; priority: lifecycle and open cards, **pinned facts**, the person's message, workflow digest — shrunk before dropped — failure evidence, rolling summary, recent turns oldest-trimmed-first). (3) The reply streams (`studio_chat_reply`, `thread_delta` events). When the message could ask for a change, the existing `copilot_diagnose` call runs (with the bounded `need[]` loop) and **every proposal passes `gate_proposals` / `gate_overlay_proposals` / `gate_structural_proposals`** before it can become a pending-change card; Apply goes through `cmd_accept_copilot_proposal`, Undo through `cmd_undo_workflow` (refused with `changed_since` if the skill's `meta.version` moved). Chat can also draft a workflow (`studio_chat_actions`); nothing is created until the draft card is confirmed. (4) Old turns fold into a stored `summary` item (`thread_summarize`).
- **Concurrency.** Turns are serialized per thread (a second message queues and emits `thread_turn: queued`); `thread_cancel` stops the stream and appends nothing half-finished. The cloud proxy is stateless (`ProxyBody` has no `messages`), so the context is rebuilt every turn.
- **Scoped LLM router.** Every RPC runs on its own thread, so one process-global router let a compile and a chat turn overwrite each other's `usage_class` and event sink. `conxa_core.llm.set_scoped_router` / `llm_scope` bind a router to the calling context; `get_router()` prefers it and falls back to the global (worker threads a handler spawns). `Backend._install_proxy_router` sets both, scoping only on worker threads.
- **Metering.** All conversation calls use `usage_class="ai_credits"` (CLOUD-25; `human_edit` is its permanent legacy alias). Entitlement / quota / unreachable errors become `error` cards with Try again.
- **Approval.** `Workflow.signed_off` is the only approval truth: any later skill write clears it (`invalidate_workflow_test_by_skill`), and the build gate checks it instead of `edited_at`, which moves on every write including a fresh compile. A *test* build (`cmd_build_skill_package{for_test:true}`, used by the copilot's verify and, later, the conversation's "Test it") skips the gate: a workflow must be testable before it can be approved.
- **Compile, Clarify and the summary (BUILD-39 / BUILD-32).** Finishing a recording starts a compile in the background (`handlers/compile_flow.py`). It waits its turn in a single-slot queue (`compile_queue.py`, a real position, no fake bar) and mirrors the compiler's *own* events onto one `compile` card (`compile_progress.py`): four honest rows — cleaned up the recording, found each element (n of m), worked out what each step is for and which values change (the second opinion is one pass, so it is one row), saved. `cmd_compile` is wrapped, not changed; a request-id observer (`protocol.JOB_OBSERVERS`) feeds the tracker from both the command's sink and the compiler's sub-step sink. Failure modes become cards: queued, out of credits (recording kept, Compile later), failed (says "no credit was used" only for an infrastructure failure the compile handler actually refunded). After a successful compile `clarify.generate_questions` finds, in the *compiled* skill document, the places only the recorder's author knows — **file uploads** (the recorded path won't exist on a customer's machine; the safe "ask each run" is the default), **steps that can't be undone** (confirm first?), **typed values** (fixed or input; skipped for values already marked while recording), **pop-ups** the recorder flagged (`optional_hint`), and **how we know it worked** (only existing text/URL/state assertions the compiler already wrote; ticking makes one `required`). At most five, one at a time, never blocking: unanswered keeps the compiler's default. Questions live in `clarify.json`, **not** in the skill, so a compile nobody answers is byte-identical. `plan_answer` turns an answer into ops for existing commands only — gated `cmd_patch_step`, `cmd_insert_step` (a confirm-only `ai_review`), `cmd_confirm_optional_interstitial` (the shared `build_try_dismiss_from_hint`), `cmd_update_workflow_inputs` — each edit-logged with `source="clarify"`, and a card shows "applied" only after those commands returned. If the workflow changed in Human Edit meanwhile the answer fails cleanly (`proposal_stale`). A typed reply to a tick-list question never answers it (it would read as "untick all"). The summary card's counts add up: steps compiled + steps added by answers = steps now.
- **Confirm-only review exemption.** `patch_gate.is_confirm_only_review`: a review whose schema is exactly `{confirmed: boolean}` and whose `on_failure` is `abort` may directly precede a destructive step (see §11.3). Known gap: the gate only runs when the *destructive* step is patched, so editing the review afterwards into a free-form one is not re-checked.
- **Test safety guard (BUILD-40 / RT-9).** A test never crosses a step that can't be undone without a person. The runtime (app layer only, no host change) accepts two Studio-only arguments: `_pause_before_irreversible` parks the run on the live page before the first step the compiler marked `destructive` (`run.js::executeOneStep`, parked like an `ai_review`, returns `_meta["conxa/guard"]`), and `_guard_release` with `resume_from` runs that step and every later one. It never touches `recovery_stage.js` or `retry_budget.js`, is inert without the argument (every customer and MCP run, and the CI gate), is skipped by `dry_run`, and fails closed inside a `for_each` body (a pause there would lose the loop cursor). The Studio (`handlers/workflows.py::_drive_runtime_pauses`, `handlers/test_flow.py`) asks the person: **Run the last N for real** or **End the test here** (which is "passed up to X" and is *not* a pass — the last real result is untouched). An engine that predates the guard would ignore the argument and send a real invoice, so the Studio first checks the staged app layer's `version.json` for `capabilities: ["test_guard"]` and otherwise **previews** (dry run), which also never counts as a pass. A confirm-only `ai_review` (Clarify's "a person confirms before sending") is answered by the **person**, never the sandbox model, and a "yes" also releases the guard in the same resume, so they are asked once, not twice; a "no" ends the test (the schema's `enum: [true]` means "no" is an invalid answer, so on a real run `on_failure: abort` ends it too). Runtime log lines `test_phase` now carry `step`, `step_total`, `intent`, `destructive`, so the test card names each step as it starts. Ships as a new `app-vX.Y.Z` tag (the CI step writes the capability); `MIN_HOST` is unchanged. **Approval** is a separate human action after a pass (`flow_approve` → `cmd_sign_off_workflow`, which packages but never publishes); a test build needs no approval, a release build does.
- **Auto-fix (BUILD-40).** Off the happy path and **outside the recovery cascade** — Tier A stays zero-token, auto-fix only runs after a test has already failed. With the Settings switch on (default; `data/studio_settings.json`), a failed test starts a loop of at most three attempts: the copilot's own `copilot_diagnose` call proposes at most one change, the same three gates filter it, it is applied by the same command a reviewer's Apply click uses (`source="auto_fix"` in `edits.jsonl`) and listed as a `pending_change` card with the exact change and Undo, then the workflow is rebuilt and re-tested **with the guard always on**. It stops on three failures, Stop, no in-gate proposal, or a change that can't apply, and offers Resume or Human Edit. This is a deliberate shift from "the reviewer accepts each proposal" (see the "Human Review is the gate" note in §7.3): the mitigations are the gates, the listing with Undo, the edit-log attribution, the guard on every re-test, and approval staying human and being revoked by any later edit.
- **Destructive flag.** The runtime reads only `step.destructive`, exported from `identity_bundle.destructive`; `sync_derived_step_fields` now re-syncs it whenever `consequence` is patched, so marking a step irreversible in Human Edit (or accepting a copilot proposal that does) actually reaches the runtime.

### 7.4b Connection, Engine State and Sign-in Expiry (BUILD-43, 2026-10-18)

Studio depends on two things the person can lose without losing their work: the local **engine** (the Python backend) and **Conxa Cloud**.

- **Engine state.** `main.js` tracks `running | restarting | stopped` and pushes `engine:status` to the renderer. An unexpected exit restarts the engine up to three times (the counter resets once a start has stayed up for a minute, so unrelated crashes over days don't add up); after that the state is `stopped` and the old native error box is replaced by an in-app notice. `engine:restart` (IPC, only honoured while stopped) resets the counter and starts it again; `engine:diagnostics` returns the state plus the last 60 stderr lines for “Copy diagnostics”. Workflows, sessions and threads are files on disk, so nothing is lost; when the engine returns, the renderer invalidates every cached query. While it is down the workspace is replaced by the notice, navigation stays visible but non-interactive, and the inspector is hidden.
- **Cloud reachability.** `cloud_ping` (`handlers/session.py`) GETs the cloud's `/healthz` and returns `{online, ms}` or `{online: false, detail}`; any HTTP answer counts as online, only a missing answer counts as offline, and it never raises. `useConnection` polls it every 30 s (8 s while offline), on the OS `online`/`offline` events, and from the banner's **Try again**; two consumers read the result: a slim banner above the workspace and the sidebar footer's connection line. Offline, the composer is disabled with its reason, **Try again** / **Compile now** on compile cards are disabled, and Publish says it is waiting to connect. Nothing is queued: an action that needs the cloud is simply not offered until it returns. Recording, Human Edit and tests that run on this computer keep working. **Approve** is local (it builds the package from the already-approved skill) and stays available.
- **Sign-in expired during a test.** The runtime tags a session-expiry stop with `_meta["conxa/session_expired"] = {run_id, step_index}` (the text is unchanged for every other client). `cmd_test_workflow` returns status `needs_sign_in`: it is not a failure, it does not write `last_test_*`, and it never starts auto-fix (an expired login says nothing about the workflow). The test card shows a “sign-in expired” state, `runtime_state.sign_in_expired` makes the lifecycle ▲ `sign_in_expired`, and the next test clears it. Studio does **not** wait for the person to sign in and resume the run itself — `wait_for_auth` stays false (AUTH-4); they sign in again and test again. An engine that predates the tag reports an ordinary failure.
- **Settings** is a popup (`SettingsDialog`) over whatever was open: Account, Usage (the meters, “AI credits”), Testing (the auto-fix switch) and About (version, updater, terms). `/settings` remains as a link target that opens it.
- **Leaving Human Edit** with an unsaved step change asks first (Save and leave / Discard / Keep editing). This is why `main.tsx` uses a data router (`createHashRouter`) with one splat route: `useBlocker` does not exist under `<HashRouter>`.
- **First launch.** `/` goes to `/welcome` when the workspace has no groups (and the lists loaded without error); the page shows the three-step flow and the computer's readiness from `deps_status` (signed in, recording browser, test runner), with **Install now** (`deps_apply`) when one is missing.

### 7.5 Identity-Capture Rules

Rules the recorder and compiler rely on for durable element identity. Violating one silently produces unmatchable bundles.

- **Recorded observations must be copied through.** `session.py::_finalize_payload_sync` builds an allow-listed `body` before `RecordedEvent.model_validate`. It must copy `branch_hint`, `date_context`, `choice_context`, `optionality`, and `post_condition`, and every field must be declared on the model (Pydantic silently drops undeclared ones).
- **A11y snapshots are captured in-thread** (`_capture_a11y_snapshot`, `aria_snapshot(timeout=…)`). Playwright's sync API is greenlet-bound, so a worker thread always fails. Without the tree, `resolves_to_nothing()` can't reject fabricated role+name signals.
- **Interactive roles** in `isInteractiveNode` include `gridcell`, `treeitem`, `menuitemcheckbox`, `menuitemradio`, `searchbox`, `spinbutton`, `slider`. Container roles (grid, listbox, menu, tree, radiogroup) stay out.
- **Calendar root is the outermost matching ancestor** (`findCalendarRoot`). Class-substring matches also hit day cells.
- **Dates format from local parts**, never `toISOString()`. `_parseDateFromString` strips ordinals and extracts embedded ISO / `Month D, YYYY` / `D Month YYYY` from aria-label prose.
- **No text selectors for option-content elements** (`select`/`datalist`/`optgroup`) or text over the cap: `buildTextSelector` emits `""`, and `identity_bundle.py` gates the `text_based` channel the same way (healing old recordings).
- **XPath** starts with `//` unless the walk reached the document root.
- **Deep CSS chains** are rejected (`is_brittle_deep_chain`), but `salvage_deep_css_tail()` keeps the shortest identifying tail without `:nth-*`, still subject to every quality gate.

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
          branch / for_each / ai_review_* / handover_*   # §11
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
- **Diagnostics follow tabs:** `server.js` attaches console/request/download listeners to every tab. Parks and failure responses use `runErr.failedPage`.
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
- **Full audit:** every assertion is evaluated; `results: [{type, target, required, ok, elapsed_ms}]` is returned. The primary path emits one `verify_result` event per step with assertions (`{si, ok, n, advFail}`). A required failure attaches `verifyResults` and `earlyDomSnapshot` to the error.
- **Fleet view:** `tracking._assertion_health_by_step` powers `assertion_health_by_step` on `/tracking/dashboard` (Self-healing page `/dashboard/healing`, worst first) and feeds the health score. Each row carries `previous_pass_rate` (same step, previous equal period — `tracking_analytics._with_previous_pass_rate`) so decay reads as a change, not just a low number.
- **Editor:** `StepConfigForm.tsx`'s Validation card (`components/validation/AssertionEditor.tsx`, shared with the re-target wizard) saves via `patch_step`. `patch_gate.py::validate_editor_patch` runs in `cmd_patch_step` and rejects removing a consequential step's only required assertion.

**Native dialogs at replay.** The loop looks one step ahead. When the next step is `dialog_accept`/`dialog_dismiss`, it arms `page.once("dialog", …)` before dispatching the current step, so the dialog resolves the instant it opens (`docs/Backend-Schema.md` §3.4h).

### 9.6 Safe Actions (PROD-3)

- **Destructive classification:** `destructive_semantics.classify_consequence()` marks click steps whose intent matches the destructive vocabulary, or commit/submit actions. Serialized as the execution step's top-level `destructive` (`_copy_saved_common`) and `consequence` (`reversible`/`irreversible`/…).
- **No re-identification on destructive steps:** `cascade.js::recoverStep` skips only the a11y re-probe (which resolves by accessible name and could land elsewhere) and emits `destructive_reidentify_skipped`. Same-selector stages still run. The EXEC-24 action guard separately blocks re-dispatch when the first attempt may have landed. `run.js` sets `destructiveHalt` from `step.destructive` whenever recovery fails (`test_cascade_destructive.js`).
- **Entity binding** (`compiler/entity_binding.py`, `EntityBinding` on `SkillStep`): for targets inside repeating rows, a `container_selector` matching every row plus an `identifier` (preferring a run input over the recorded text). `resolution.js::entityRoots` requires exactly one matching row; zero or several → `entityNotFound`, never the unscoped page. Primary and recovery resolution both narrow through it.
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

Emits `env_mismatch`. Role isn't captured (no reliable source). The exec context deliberately doesn't pin locale, timezone, or viewport (only the auth window's `STEALTH_CONTEXT_OPTIONS` does); this warns, it doesn't normalize.


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

**Editor.** All three are insertable. `if_present` bodies are editable via `insert_branch_step`/`delete_branch_step`/`reorder_branch_steps` and `cmd_patch_step(path="branch.steps[N]")` through `_apply_step_patch` (selector gates + bundle rebuild). `validate_editor_patch(in_branch_body=True)` rejects `recovery`/`validation` keys (dead config in a best-effort body). `candidates`/`options` accept quality-gated `branch` patches but have no dedicated UI (`BranchBodyEditor.tsx` covers `if_present` only; TODO.md BUILD-6). `StepEditorDTO.branch_summary`/`branch_steps` project them read-only.

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

Exactly one of `rows`/`items`. Enforced in `_saved_for_each_step` (compile) and `patch_gate.py::_validate_for_each_source` (against the step's effective, merged `for_each`).

- **Real execution per row.** `for_each` is intercepted in `executeOneStep` and calls `executeOneStep` for every body step per row, so the body gets tab resolution, GATE/VERIFY, settle retry, full recovery, cancellation, and dry-run's destructive skip. This is unlike `runBranchBody`.
- **Row sources:** `resolution.js::enumerateRows(page, spec, cap)` lists every matching row with its full trimmed text as the identifier, **once, before the loop** (re-enumerating a mutating list skips or double-processes rows). `splitListInput(raw, cap)` splits an input on commas (or takes an array), trimming, deduping, and capping identically.
- **Per-row scoping is entity binding:** body steps carry `entity_binding.identifier: "{{row_id}}"`. `inputs.<as>_id`/`<as>_index` are set per iteration and restored (or deleted) after the loop. `entityRoots` requires exactly one matching row, so duplicate row text fails closed. An `items` body usually has no binding, and `entityRoots` no-ops.
- **Caps:** `max_iterations` is required at compile time and clamped at runtime by `CONXA_MAX_LOOP_ITERATIONS` (default 100, `run_config.js`).
- **Errors:** `on_row_error: "stop"` halts on the first failed row (a silently skipped row is an unnoticed partial write); `"continue"` is opt-in. Cancellation and an `ai_review` pause always propagate. The loop pushes to `state.warnings` when it finds zero rows/items or finishes with failures under `continue`.
- **Entity binding for every step:** `build.py` detects bindings on all steps, not only irreversible ones. The publish gate stays irreversible-only but recurses into `for_each.steps`.
- **Loop variables are never inputs:** `upload_binding.py::for_each_loop_variable_names` excludes `{{<as>_id}}`/`{{<as>_index}}` from auto-declared inputs in both `reconcile_inputs_with_step_values` and `_merge_saved_inputs_with_execution_placeholders`, alongside the `downloaded_file*` family.
- **Editor:** insertable (category `iteration`), scaffolded by `_new_manual_step`, validated by `_validate_for_each_patch` (`steps` rejected on the parent patch). `StepEditorDTO.for_each_summary`/`for_each_steps` project the body read-only (`"{skill_id}:{i}.for_each.steps[j]"`). No dedicated nested-body UI yet (PROD-3-UI).

**`download_observed` fails loudly.** The marker throws `badInput` when no download arrives, when the race resolves nothing, or when the entry has no `.path` (`saveAs()` failed). So a download loop stops on the first missing file instead of "succeeding" with partial or no downloads.

**Second opinion guards relevant to loops:**
- `parameterize_literal` refuses any `value` already containing a `{{placeholder}}` (`PLACEHOLDER_RE`), so auto-bound download placeholders survive.
- `flag_noise` never archives a step that triggers a download or file chooser. `_review_inputs` computes `causes` (`file_download`/`file_chooser`) by looking past markers to the next real step; the prompt and `_validate_findings` refuse such findings, and `archive_flagged_steps` independently recomputes the lookahead and refuses (the structural guarantee).

#### "Generalize to a loop" suggestion

`compiler/loop_suggestion.py::detect_download_upload_loop_candidates` is fully deterministic. It fires when an upload is bound to exactly `{{downloaded_file}}` and that download's filename appears in an earlier `navigate` URL. Matching uses `url_contains_literal`/`replace_url_literal`, which decode the URL (`unquote`) rather than encode the filename (`C%2B%2B.gitignore`). v1 is URL-based only.

Once detection returns, `build.py::_rewrite_loop_suggestions_for_review` optionally rewrites each suggestion's `why` into plainer language via one small LLM call (task `loop_suggestion_copy`, `llm/loop_suggestion_copy.py`) before it reaches Human Edit's mandatory review popup (`ReviewQuestionsDialog.tsx`) — copy only, cached like every other compile-time LLM task, and on any failure (no provider, timeout, empty response, or a response with no usable `text` key) the deterministic `why` built above is used verbatim. Never touches `wrap_start_key`/`wrap_end_key`/`upload_step_key`/`template_literal`/`redundant_click_key`, so a failed rewrite changes nothing but the sentence a reviewer reads, and it never marks the compile `degraded` — a worse sentence is not a worse compile.

The call is grounded in the actual steps, not a bare filename: `_rewrite_loop_suggestions_for_review` resolves `wrap_start_key`/`wrap_end_key`/`upload_step_key` back to real compiled steps and hands the LLM a small role-tagged list (`role`: `"acquire"` | `"upload"`, plus that step's own `action` and `intent`/`semantic_description`; never a selector/target/identity_bundle). An earlier version passed only `{filename, removes_click: bool}` — with no picture of what the workflow does, the model confidently described the wrong mechanism (a delete) for what is always a download-then-upload pattern; the system prompt now states that pattern explicitly and the `role` tags rule out that guess.

This isn't a one-off fix bolted onto loop suggestions specifically — three small, generic pieces were pulled out so the next "make some compiler-generated text friendlier for a reviewer" need doesn't re-derive the same plumbing a third time (mirroring why `llm/llm_cache.py` itself got extracted — see that module's own docstring): `compiler/step_key.py::step_key_index_map` (the key→step-index lookup, companion to `step_keys()`), `compiler/step_description.py::describe_step` (the selector-free `{role, action, description}` shape any step can be reduced to for an LLM/reviewer), and `llm/copy_rewrite.py::rewrite_advisory_text` (the cache/fallback/failure-logging dance every advisory copy-rewrite task needs — `llm/loop_suggestion_copy.py` is now a ~15-line wrapper naming its own task/cache namespace, not a bespoke module).

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

Tests: `runtime/test/unit/test_for_each.js`, `test_download_race.js`; `conxa-cloud/tests/test_for_each_compile.py`, `test_for_each_patch_gate.py`, `test_for_each_dto.py`, `test_for_each_mutations.py`, `test_publish_entity_binding_gate.py`, `test_second_opinion.py`, `test_editor_derived_sync.py`, `test_loop_suggestion.py`, `test_apply_for_each_loop_suggestion.py`, `test_workflow_dto.py`, `test_loop_suggestion_copy.py`, `test_loop_suggestion_review_context.py`, `test_step_description.py`.

### 11.3 AI Review Step

`ai_review` is an author-placed reasoning checkpoint: not a page action, and **outside the recovery cascade**. It never touches `recovery_stage.js`, `retry_budget.js`, or the recovery ceiling. It isn't the "silent LLM fallback" the invariants forbid: it's explicit, editor-visible, and exists to invoke reasoning (CLAUDE.md Key Invariants).

**Shape.** Authored in Human Edit (`ai_review_prompt`, `ai_review_output_schema`, `ai_review_on_failure`, `ai_review_default_value`) and flattened to `{type: "ai_review", prompt, on_failure, output_schema?, default_value?, reference_screenshot_ref?, output_name?}`. A blank prompt drops the step at build with a warning.

**Two-call contract** (reuses the park primitive, not recovery):
1. `run.js` intercepts the step after tab resolution and throws `reviewPause`. `server.js` parks the page (same `_parks`, `PARK_TTL_MS`, `capturePageFingerprint`/`PARK_DIVERGENCE_TOLERANCE`) and returns a review request (`review_pause.js::buildReviewRequest`): the exact `resume_from`/`review_results` call to make, an optional reference image, a live DOM inventory (`domInventory()`), and a JPEG q80 screenshot. It also returns `_meta: {"conxa/ai_review": {step_index, prompt, output_schema}}` so programmatic callers needn't parse prose.
2. The caller resumes with `review_results: {"<step_index>": answer}`, bound into `inputs` (never through `applyStepOverrides`). `_resumeReviewStep` is **not** gated on agent recovery being enabled: any MCP caller can answer.

**On resume** the answer is checked against `output_schema` (hand-rolled: required fields, type, enum; no dependency) and the park's divergence check. Divergence or an invalid answer triggers a bounded re-ask (`REVIEW_RETRY_MAX = 2`), not a refusal. Then `on_failure` applies:
- `abort` (default): end the run;
- `use_default`: bind `ai_review_default_value` and continue on the same parked page;
- `continue`: bind `null`.

An unanswered pause is closed by the park TTL sweep.

**Build Studio sandbox** (tier 2, no agent): `cmd_test_workflow` detects the pause (via `_meta`, falling back to the prose `resume_from:` line) and answers it itself through `_answer_ai_review`. That's the `ai_review` LLM task, `usage_class="human_edit"`, text route first with one `route_vision` fallback on `CloudUnreachable`; quota/entitlement errors propagate. Capped at 5 pauses per test. `ai_review` is not in `VISION_TASKS`.

**Safety:** `patch_gate` refuses a destructive step directly after an `ai_review` (`destructive_step_cannot_directly_follow_ai_review`). A model's answer may gate whether a later step runs, never supply what a destructive step acts on. **Exemption (BUILD-39):** a *confirm-only* review — output schema exactly `{confirmed: boolean}`, `on_failure: abort` (`patch_gate.is_confirm_only_review`) — can only gate *whether* the next step runs, so it may sit directly before it; this is what Clarify's "a person confirms before sending" inserts. Anything wider (extra fields, a string answer, `continue`/`use_default`) is still refused.

**Not built** (TODO.md): writing a reference screenshot for an inserted `ai_review` (the field, gate, and loader exist; `_write_saved_visual_assets` only walks recorded steps), and branching on an answer (branches probe the DOM, not `inputs`).

### 11.4 Hand-Over Step

`handover` yields the live page to a **person** (2FA, CAPTCHA, e-signature, one-off login), then takes it back. Outside the recovery cascade, like `ai_review`. Only the hand-over shape of EXEC-21 is built; approve/reject and supply-a-judgement are open.

**Resume signals** (`runtime/app/handover.js::arm()` → `{signal, disarm}`; the first to fire wins; `disarm()` is idempotent):
1. an in-page "Done — resume workflow" banner: `context.exposeBinding` + `addInitScript`, plus an immediate `evalOn` on every live page, so it survives navigation and new tabs;
2. a file drop at `~/.conxa/resume/<run_id>.cmd` (`fs.watch` + 5 s poll), written by `conxa-runtime.exe resume <run_id>` (host subcommand in `bootstrap.js`);
3. a loopback HTTP listener (`127.0.0.1`, random port, per-pause token). This is the runtime's only inbound network surface and exists only while a hand-over is pending.

**Two speeds.** `run.js` races the signal against `CONXA_HANDOVER_INCALL_MS` (120 s). If the person is there, the run continues inside the same call. Otherwise it throws `handoverPause` and `server.js` parks the page, with three differences from an `ai_review` park:
- **The host lock is released** during the pause (`HANDOVER_PARK_TTL_MS`, 30 min, is too long to block sibling runs) and re-acquired on resume.
- **Extra tabs stay open** (the person may be mid-OAuth).
- **No divergence refusal:** page change is the point. `handover.js::revalidate()` re-resolves the recorded tab via `resolveStepPage` (never "whatever is current"), checks it's open, and, if `resume_when` was declared, requires that probe to be true. Otherwise the step fails cleanly.

**Self-driven resume:** when parking, `server.js` attaches `armed.signal.then(...)`; when a signal fires, the same process calls its own `_handleTool("execute_skill", {resume_from, watch: true})`, the same path an agent would use. In the Studio sandbox, `call_runtime_tool` drops idle processes after 300 s (`_IDLE_TIMEOUT_S`), so a hand-over pending longer than that dies in tests only.

**Authoring:** `action_registry.py` lists `handover` (validation category). Its value field is the **message shown to the person** (`VALUE_LABELS`). `handover_*` fields: `on_failure` ∈ `{abort, continue}`, optional `resume_when` + timeout. Flattened to `{type: "handover", message, on_failure, resume_when?, resume_when_timeout_ms?}`; no message → dropped.

**Unknown step types fail loudly.** `handlers.js::executeStep` throws on any type that isn't a known handler or one of the `run.js`-intercepted types (`ai_review`, `handover`, `for_each`, `outcome_fork`), so a guard step never silently vanishes on a newer runtime. Older runtimes still no-op, hence the version gate above.

**Limitations:** a person can't operate a native `alert`/`confirm`/`prompt` (the run's dialog listener intercepts it at CDP). A hand-over inside a `for_each` body isn't resumable (the pause propagates out like cancellation; no compile-time guard yet, TODO.md). Not durable across a runtime restart (EXEC-22).


### 11.4 Outcome Forks (popups that need a decision)

**Why.** A recorded popup like Drive's "file already exists: Replace / Keep both / Cancel" used to be treated as a nuisance: the recorder flagged the click as an optional interstitial (§11.1) and it became a `try_dismiss` whose first candidate was the button the author happened to press. That replayed one recorded choice forever (even "Replace"), pressed Escape (often Cancel) when the button was missing, and said nothing about the case where the popup never appears. A decision dialog is a different kind of thing: the app can legitimately land in several states, and what to do in each is a *policy*, not a selector.

**Recording.** `bridge.js::buildDialogContext` captures, for any action made inside a `dialog`/`alertdialog`/`aria-modal`/`<dialog open>` container, the popup's title, message and **every** visible button (the clicked one marked `chosen`) as `RecordedEvent.dialog_context` (`conxa_core/models/events.py::DialogContext`). Native `alert`/`confirm`/`prompt` events get the same shape (OK/Cancel) from `recorder/session.py::_native_dialog_context`. The field must be on both the observation allow-list (`session.py`) and the model, or `run_pipeline`'s re-validation drops it.

**Compile.** `compiler/outcome_fork.py` (pure, no LLM) runs after bindings and before the compile report / second opinion (`build.py`). `classify_dialog` → `decision` | `dismissable` | `consent` | `form`; only decisions become forks (consent banners, close-only popups, forms and native dialogs keep today's path). A run of consecutive steps inside one decision dialog collapses to one `outcome_fork` step (`SkillStep.outcome_fork`, shape in Backend-Schema §3.4j). The recorded option reuses the real compiled step(s); every other button gets a deterministic click from `editor/overlay_identity.py::bundle_from_descriptor`, scoped `<container> >> …` — **no model writes a selector**. Option `role` comes from a fork-local word list (replace/overwrite/discard/delete/remove/…, deliberately *not* the global destructive tokens) plus the recorded step's `consequence`; consequential options get `identity_bundle.destructive = True`. The step that opened the dialog has its required "dialog present" assertion relaxed, so the no-popup outcome doesn't fail VERIFY. Defaults before review: a harmless recorded choice → `auto` (`author_approved: false`); a consequential one → `ask_human`. The popup's message text never leaves the compile (`signals.dialog_context` is stripped; only value-free `message_tokens` ship).

**Runtime.** `run.js::runForkStep`, intercepted in `executeOneStep` like `for_each` (the chosen option's steps run through the real `executeOneStep`: tab resolution, GATE/VERIFY, recovery, dry-run skip — unlike best-effort `runBranchBody`). It watches up to `timeout_ms` (1.5–8 s, default 4 s) using `page_scripts.dialogProbe` and the native `dialogQueue`, then `fork.js` (pure) decides: `matchState` matches on title + the full button-label set (a missing button, a different title, or two equally good states → `unknown`, never first-wins); `decide` applies the policy:

| Policy | Behaviour |
|---|---|
| `auto` | run the named option; a **consequential option without `author_approved` pauses** |
| `by_input` | the caller's input (a `select` with the option labels + `ask`, default `ask`) picks the option via `choice.js::matchOption`; a bad value is `badInput`, `ask` pauses |
| `ask_human` | pause |
| `stop` | fail with the policy's message |

An unrecognised dialog follows `unknown_policy` (`ask_human` default). After running an option the popup must be gone (`pollNegative`) or the step fails. A pause inside a `for_each` degrades to a stop (the cursor can't be kept), and a destructive option with the RT-9 guard on pauses at the fork's own index. Audit: telemetry `fork_observed/fork_decided/fork_clean/fork_unknown/fork_paused/fork_answered`, recovery-log `fork_decision`.

**Asking a person.** A pause parks the page exactly like `ai_review` (`server.js`, `forkStepIndex`, valid choices stored on the park) and returns `fork_pause.js::buildForkRequest`: plain-language question, the choices (a state's own options, or — for an unrecognised popup — only the buttons actually on screen, plus `__stop__`), the screenshot, and `_meta["conxa/fork"] = {step_index, title, message, choices, suggested, unknown, requires_human: true}`. The agent is told to ask the user and not decide. Resume reuses `review_results: {"<i>": {"choice": "<id>"}}`; `_resumeForkStep` validates against the stored choices (bounded re-ask, `REVIEW_RETRY_MAX`), binds `__fork_choice_<i>`, and `runForkAnswer` re-observes the page (the person may have clicked in it themselves → `handled_in_page`). Build Studio's test driver extracts `conxa/fork` **before** the ai_review extractor (the prose also contains `resume_from`) and routes it to a person (`_fork_hook` → `waiting_fork` test card); with no hook the test ends at the popup.

**Hardening.** `cascade.js::layer1Ladder` and `run.js`'s agent-nominated dismissal both refuse to dismiss a decision dialog (`fork.js::isDecisionDialog`: ≥2 buttons, not all plain dismiss labels, no fields, not a consent banner): no Escape, no generic close, no learned selector. The step keeps failing as INTERCEPTED and escalates; the Tier B note tells the agent to ask the user.

**Author review.** `conversation/clarify.py` asks one `fork` question per un-reviewed popup state (priority 1, so the five-question cap keeps it): each option, "let the person running it choose each time" (`by_input`), "pause and ask me" (`ask_human`), "stop the run". The answer becomes the `set_fork_policy` op → `workflow_mutations.set_fork_policy`, the **only** writer of `author_approved` (choosing an option there is the approval; patch_gate permits only `intent`/`semantic_description` on a fork). The LLM (`fork_question_copy`, via `llm/copy_rewrite.py`) may only reword the question title; choices are fixed by code.

**Variant recording ("record the case where the popup does NOT appear").** After the questions, `_flow_offer_variants` posts an optional `fork_variant` card. Recording it uses `cmd_start_recording({variant_of})`: the take is registered in `Workflow.variant_session_ids` and **never** replaces, backs up, or clears the workflow's own recording (start/stop/cancel/empty all have variant branches). `cmd_apply_variant` compares the two takes by rule — no compile, no credit, no model — with `compiler/variant_align.py` (LCS over value-free event signatures): a variant with no popup flips `clean.observed`; a decision popup only the variant saw becomes a new state (`outcome_fork.state_from_dialog`, deterministic clicks); anything else is returned as `unexplained` and **never merged**. Applied as the `apply_variant_observation` mutation on the saved skill, so the author's edits and answers survive.

**Not built (TODO.md).** Native-dialog forks (need the dialog pre-armed before the triggering click, EXEC-29) — a native `alert/confirm` keeps its own `dialog_accept/dismiss` step; keeping fork signatures armed for the next steps to catch a popup that opens after the watch window; insertable forks in Human Edit; learning forks from runtime `fork_unknown` telemetry.

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

**Runtime use (phase 5).** *Author's standing answer:* `cmd_set_app_popup_policy` (`handlers/app_graph.py`) is the ONLY writer of `Popup.app_policy` and so of its `author_approved`: `auto` on a button (a consequential one also needs `confirm_risky: true`), `ask_human`, `stop`, or clear. Kept in `data/app_graphs/<ws>/<host>/policies.json`, applied on top of every re-fold, edited from the popup panel of the App maps page. *Shipping:* `skill_package_builder` writes `app_knowledge.json` into each skill (`app_graph/knowledge.py`) — decision popups only, for the hosts the skill visits (cap 40 per host), with title/buttons/message words/container, each button's role and the policy; **no screens, actions, workflow names or paths**. It rides in the pack's code files (`_CODE_FILES` in `skillpack_update_routes.py`, `release_diff._SKILL_FILES`), so it activates atomically with the skill version and only changes when the skill is published again; an absent, malformed, oversized or newer-schema file is simply "no extra knowledge" (`runtime/app/app_knowledge.js::load`). *At run time:* when `run.js::runForkStep` meets a dialog none of its own states recognise, `app_knowledge.lookupPopup` (the fork matcher: every known button present, compatible title, ambiguity is no match) looks it up on the page's host. A known popup follows `decideKnown`: `auto` clicks the named button only if it is visible and — when the map says it is consequential — only if the author approved it; no policy, `ask_human`, a missing button or an unapproved risky button **pause for a person** (the pause lists the popup's real visible buttons with their risk and suggests the one other workflows chose most; the run-time answer is still only ever one of those buttons); `stop` stops; inside a repeated step a pause becomes a stop. The RT-9 destructive guard and dry-run apply to the click exactly as they do to a fork option. Telemetry `fork_decided` has `src: "app_policy"` and `st: "map:<popup id>"`. *Not changed:* the Tier 1 ladder (`cascade.js`) still refuses to dismiss any decision dialog; mapping consent/dismissable popups to give it a known close button is TODO APP-1.

**Closing the loop (phase 6).** The runtime reports what it could not place: `run.js::emitAppObs` sends telemetry `app_obs` when a fork meets a popup none of its states recognise, and when a decision popup blocked a click and the Tier 1 ladder left it alone (`primaryErr.decisionDialog`). The payload (`app_knowledge.js::observation`) is host, route, <=8 title words, <=6 button labels and whether the shipped map knew it — scrubbed by `runtime/app/scrub.js`, which must agree with `app_graph/scrub.py` on every case in `runtime/test/fixtures/scrub_cases.json` (both sides run that file). A button label that needed any scrubbing is dropped whole, and the popup's message never leaves the machine (`docs/Security.md` SG-20). The cloud counts these per popup shape over the workspace's own visible runs on read (`GET /api/v1/app-graphs/{app}/field`, drift-queue plan gate, nothing written back); `cmd_sync_app_graphs` then pulls them (`services/app_graph_sync.py::pull_field`) and `app_graph/field.py` stores them in `field.json`: a row matching a covered popup raises its `field_seen`, any other becomes a **field-only popup** ("seen in real runs, not in any workflow") with button roles from the compiler's consequential-word list, which ships in `app_knowledge.json` and can get a policy like any other. Publishing stays the only path to runtimes.

**Agent discovery (phase 7).** Agents learn which web app each workflow works in. `list_skills` gains an `app` field (the host of the skill manifest's start URL, `app_knowledge.js::appOfManifest`), and the discoverability note `durable_context.js` writes into each registered agent host marks every workflow "(works in `host`)" (sync reads the active manifests from disk, `sync.js::_appsBySkill`; a skill with no start URL is listed without one). Deliberately **not** shipped: screens, paths or per-workflow routes — they are the author's data and the runtime has no use for them; the shipped map slice stays popups-only.

**Sharing across customers (phase 8) is not built.** It needs an explicit opt-in, a k-anonymity threshold across workspaces and a `docs/Security.md` re-review (SG-20 must be revisited first); design in `TODO.md` APP-1. Until then every map, every field count and every policy is private to one workspace.

**Tests:** cloud `test_app_graph.py`, `test_app_graph_routes.py`, `test_app_graph_sync.py`, `test_app_graph_hints.py`, `test_app_graph_knowledge.py`, `test_app_graph_field.py`; runtime `test_app_knowledge.js`, `test_scrub.js`, `test_durable_context.js`; Studio `test/appMapLayout.test.mjs` (`npm run test:app-map`).

---

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

**Human Edit pool** is spent only by editor-triggered LLM paths: `handlers/visual.py`, `handlers/workflow_editor.py`, the 1-click fix (`patch.py::_regenerate_compiled_selectors`), the region re-target (`region_selector_vision.py`), the Copilot, and sandbox `ai_review`. Deterministic editor actions work when it's exhausted. The proxy enforces trial and machine registration on every call.

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
| No authoring UI for `try_dismiss`/`wait_for_one_of` options, `for_each` bodies, Strict Mode, entity-binding confirmation | Human Edit | Low | BUILD-6, PROD-3-UI |
| Analytics retention is read-side only | `app/services/tracking.py` | Low | No write-side prune |
| Concurrency cap is flat | `run_registry.js` | Low | RT-3-CAP-SIZING |
| `beforeunload` dialogs unhandled | recorder + runtime | Low | TODO.md |


