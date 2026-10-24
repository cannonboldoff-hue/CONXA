# CLAUDE.md

This file is guidance for Claude Code when working in this repository.

---

## What This Is

Conxa is an AI-native automation platform. SaaS companies record browser workflows in the Build Studio (Electron + Python, Windows), compile them into structured skill packages with multi-signal element identity and self-healing recovery, publish them to the Conxa Cloud, and distribute them to end customers as `.exe` installers that run via Claude Desktop's MCP protocol. Execution is entirely local on the customer's machine — the cloud is coordination infrastructure, not an execution environment.

Three systems, three owners:

```
Company (SaaS vendor)        Conxa Cloud              Customer (end user)
─────────────────────        ───────────              ──────────────────
Build Studio (Windows)  →    Render + Vercel      →   Runtime (Claude Desktop)
Record + compile locally     Proxy / host / bill      Execute locally via MCP
```

---

## Documentation — Read First

The `docs/` folder is the authoritative source of truth for this codebase. Before making non-trivial changes, read the relevant doc rather than inferring from code alone.

| Doc | Read it when… |
|---|---|
| [`docs/TRD.md`](docs/TRD.md) | Touching the recorder, compiler, runtime, auth flows, API surface, or recovery cascade. This is the authoritative technical deep-dive: architecture, all auth flows with sequence diagrams, pipeline stages, data contracts, security model, known gaps. |
| [`docs/App-Flow.md`](docs/App-Flow.md) | Changing how a user flow works end-to-end — onboarding, record, compile, build, install, execute, update. |
| [`docs/Backend-Schema.md`](docs/Backend-Schema.md) | Changing data models, adding API endpoints, modifying KV namespaces, or changing telemetry event schemas. Contains ERD diagrams and full API contracts with request/response examples. |
| [`docs/UI-UX-Brief.md`](docs/UI-UX-Brief.md) | Changing any screen in the Build Studio or Cloud Dashboard. Documents every screen's purpose, inputs, outputs, and known UX issues. |
| [`docs/Implementation-Plan.md`](docs/Implementation-Plan.md) | Starting on a new engineering task. Contains the prioritised 4-phase roadmap with specific files to change and risk assessments per item. |
| [`docs/PRD.md`](docs/PRD.md) | Understanding product goals, personas, positioning, or long-term strategy. **Do not edit for individual features** — see doc maintenance rules below. |
| [`docs/cost_model.md`](docs/cost_model.md) | Making decisions that affect LLM usage at compile or execution time. |
| [`docs/Security.md`](docs/Security.md) | Touching auth, RBAC, billing, or any security-sensitive surface. Numbered gap tracker (SG-01…) with fix status, cross-referenced from `docs/TRD.md` §15/§17. |
| [`docs/Sales-Blockers.md`](docs/Sales-Blockers.md) | Checking what's still blocking an enterprise sale. Sales-framed view of the same Phase 1/2 checklist tracked in `docs/Implementation-Plan.md`. |
| [`docs/Auth-and-Updater.md`](docs/Auth-and-Updater.md) | Touching Build Studio auth (Clerk PKCE), runtime auth (per-company sync token), or either auto-updater (Build Studio electron-updater, runtime manifest-driven self-update). |
| [`SHIP-GUIDE.md`](SHIP-GUIDE.md) | Shipping a release — dev/prod promotion workflow, channel tagging, runtime host/app versioning. |
| [`research-analysis/07-go-to-market/agentic-discovery-strategy.md`](research-analysis/07-go-to-market/agentic-discovery-strategy.md) | Understanding how multi-agent skill discovery and the durability flywheel are gated and governed. |
| [`docs/archive/`](docs/archive/) | Looking for historical context only — superseded reports and rotated `FIX.md` logs. Not current guidance. Includes `docs/archive/refactors/PHASE_4_REFACTOR_REPORT.md`, which documents a failed approach to splitting `config.py` — read it before attempting that refactor again. |
| [`TODO.md`](TODO.md) | Picking up new engineering work. The single prioritized backlog spanning documentation, architecture, and every subsystem. |

---

## Design Context

Two UI surfaces each carry their own `PRODUCT.md` (strategic: register, users, positioning, brand personality) and `DESIGN.md` (visual: colors, typography, components) for the `/impeccable` design skill. Read the relevant pair before any non-trivial UI change in that app.

| Surface | Register / Platform | Files |
|---|---|---|
| Cloud dashboard + marketing site | brand / web | [`conxa-cloud/frontend/PRODUCT.md`](conxa-cloud/frontend/PRODUCT.md), [`conxa-cloud/frontend/DESIGN.md`](conxa-cloud/frontend/DESIGN.md) |
| Build Studio (Electron renderer) | product / web | [`conxa-builder/electron/renderer/PRODUCT.md`](conxa-builder/electron/renderer/PRODUCT.md), [`conxa-builder/electron/renderer/DESIGN.md`](conxa-builder/electron/renderer/DESIGN.md) |

---

## Working Principles

1. **Think before coding.** State assumptions. If multiple interpretations exist, surface them — don't silently pick one. If a simpler approach exists, call it out. If something is unclear, stop and ask.
2. **Simplicity first.** Write the minimum code needed. No speculative abstractions, no configurability for single-use code, no handling for impossible scenarios. If 200 lines could be 50, rewrite it.
3. **Surgical changes.** Touch only what the task requires. Don't refactor unrelated code; mention issues rather than fixing them. Match existing style. Every changed line must trace to the task. If your changes leave unused code, remove only what *you* introduced.
4. **Goal-driven.** Define success → implement → verify. Bug → reproduce → fix → verify. Refactor → ensure no behavior change.

### Token and file constraints

- Don't read files larger than ~25 KB completely. Use `offset`/`limit`, or `grep`/`tail` to find the relevant chunk first.
- Session artifacts (`data/sessions/<id>/events.jsonl`, screenshots, compile reports) can be very large — always scope reads.

### Artifacts

- Whenever the user asks to create an artifact, also save a copy in `docs/artifacts/<artifact_name>`.

---

## Repository Layout

```
packages/conxa-core/        Shared Python foundation — pip package `conxa_core`
  conxa_core/               Installed by BOTH cloud backend and Build Studio
    config.py               Pydantic settings (env_prefix=SKILL_)
    db.py                   Dual store: Postgres (cloud) / filesystem (Studio) + healthcheck()
    models/                 Pydantic schemas: SkillPackage, RecordedEvent, Workflow, SkillPack
    storage/                JSON/SQLite stores, snapshots, workflow_store, skill_pack_store, skill_packages.py
                            (read/list side only — generation lives in conxa_compile, see below)
    llm/                    Router protocol + get/set_router singleton + shared OpenAI-compatible
                            HTTP/prompt-building engine (client.py) — the cloud's own provider
                            router imports this; the pipeline's call_llm() lives in conxa_compile
    metrics/, progress.py, workspace.py
  pyproject.toml            No bundled templates — those ship with conxa_compile now

conxa-builder/              Electron desktop studio — records + compiles + builds LOCALLY (Windows)
  electron/                 Electron main + React renderer (Vite + TypeScript)
  python/                   Python stdio backend (spawned by Electron; depends on conxa-core)
    backend.py              JSON-RPC dispatcher; wires cloud proxy via conxa_core.llm.set_router
    handlers/               cmd_* RPC handlers, grouped by domain, mixed into the Backend class;
                            protocol.py holds the shared substrate (_CommandError, event emission)
    requirements.txt        playwright, Pillow, bs4, lxml  (conxa-core installed separately)
    services/               auth_service, bootstrap, llm_proxy_client, metadata_reporter
    conxa_compile/          Full local pipeline (no cloud involvement):
      recorder/             Playwright capture + injected bridge.js
      pipeline/             Normalize / dedupe / enrich recorded events
      compiler/             Events → SkillPackage (selectors, assertions, recovery, fingerprint)
        identity_bundle.py  IdentityBundle/IdentitySignal with durability scoring + orthogonality classes
        selector_grammar.py Deterministic Playwright grammar generator (internal:role/testid/text + relational)
        stable_hash.py      Dynamic-class-stripped SHA-256 hash for stable fingerprinting
        selector_filters.py Anchor quality gates: is_low_quality_anchor, ephemeral anchor filtering
        selector_score.py   Confidence scoring from IdentityBundle signal quality
        build.py            _build_intent_graph (was _llm_compile_selectors); IdentityBundle runs before _build_target
      editor/               Workflow editor service + DTOs + patch gate
      llm/                  client.py — call_llm() dispatcher (routes through conxa_core.llm's
                            installed router); task clients (intent, relational vision anchor,
                            recovery, workflow intent graph, selector_regeneration.py +
                            region_selector_vision.py — user-edit re-compile only, see Key
                            Invariants)
      storage/              skill_packages_build.py — bundle generation/write/delete/rename
                            (read/list side is conxa_core.storage.skill_packages); formatters,
                            templates
      skill_package_templates/, installer_templates/   Templates copied into generated bundles/installers
      skill_pack_build_log.py  Request-scoped build log for skill-pack writes
      anchors/, confidence/, policy/
      skill_package_builder.py, installer_builder.py, conxa_runtime.py
  pyinstaller.spec          Bundles conxa_core + conxa_compile into dist/backend/

conxa-execute/              v0.1 Conxa Execute (PROD-19) — form + BYO chat; MCP client to installed runtime
  backend/                   Standalone cloud backend (own render.yaml, own Postgres) — metered
                            managed-chat proxy + token-pack billing (Execute Key identity, no
                            login system); see docs/TRD.md §3.6. BYOK keeps working unmodified

conxa-cloud/                Thin cloud SaaS — proxy / auth / billing / dashboard / hosting
  backend/                  FastAPI (depends on conxa-core; NO recorder/compiler/Playwright)
    app/
      main.py               Routers + fail-fast prod config validation + /healthz, /readyz
      api/                  llm_proxy, cashfree, product, publish, skillpack_update,
                            updates, tracking, job, workflows, security, entitlement_routes,
                            manifest_signer, installer_storage, skillpack_storage
      llm/router.py         Multi-provider pool: Groq, Google AI Studio, NVIDIA NIM
      services/             saas, rbac, llm_metering, jobs
    requirements.txt, build.sh, start.sh, Dockerfile, ROUTER_SETUP.md
  frontend/                 Next.js 16 dashboard (Dashboard, Skill Packages, Billing, Team, Settings)
    package.json            Clerk, TanStack Query, Tailwind 4, shadcn/ui, Framer Motion
  scripts/                  recompile_session.py, test_plugin.py
  tests/                    pytest suite (core + compile + cloud)
  pytest.ini                pythonpath = backend ../conxa-builder/python ../packages/conxa-core

runtime/                    Node.js MCP server — ships to ~/.conxa/ on customer machine
  bootstrap.js              Entry point loaded by host exe; enforces min_host compat; loads disk-resident app layer
  _pkg_stubs.js             Static dependency stubs bundled into host exe (makes node_modules available to app layer)
  server.js                 MCP stdio server (@modelcontextprotocol/sdk) — lives in conxa-app layer on disk
  run.js                    Step executor + GATE/VERIFY logic; delegates element resolution to resolve_adapter.js
  resolver.js               Pure, browser-independent element resolver — durability-walk + uniqueness/margin gate
  resolve_adapter.js        Browser-side adapter: pre-gathers candidate descriptors from live Playwright page for resolver.js
  recovery.js               L1 exception ladder + L2 a11y/re-hover/dialog (Tier A, zero LLM tokens)
  install_identity.js       Writes version.json and integrity metadata for the app layer on first install
  skill_loader.js           Skill pack loading + input validation
  browser.js                Playwright browser lifecycle
  host_browser.js           Borrows a browser view from Conxa Execute's own panel instead of
                            launching Chromium, when Execute is the MCP client (inert otherwise)
  auth_manager.js           Per-company token via keytar; AES-256-GCM session encryption
  sync.js                   Skill pack delta sync with SHA-256 atomic writes
  tracker.js                Telemetry batching → POST /tracking/{co}/events
  package.json              @yao-pkg/pkg bundles for win/mac; version = host exe version
  test/                     Unit + integration tests: test_resolver.js, test_resolve_adapter.js,
                            test_recovery.js, gate_replay.js (execution gate CI fixture)

data/                       Runtime state: sessions/, workflows/, skills/, saas/, cache/, chromium/

.github/workflows/
  build-runtime-host.yml    CI: builds host exe (--no-bytecode), tags host-vX.Y.Z releases
  build-runtime-app.yml     CI: obfuscates disk-resident app layer, tags app-vX.Y.Z releases;
                            execution gate (gate_replay.js, real skill replay against the
                            MIN_HOST exe) runs before the zip/release/publish steps and
                            fails the build if the app layer can't replay under MIN_HOST
  build-studio.yml          CI: Electron + Python bundle
  promote-release.yml       CI: dev→stable channel promotion, Ed25519 re-signing (see docs/TRD.md §16)

docs/
  TRD.md                    Authoritative technical deep-dive
  PRD.md                    Product strategy — vision, personas, positioning, roadmap
  App-Flow.md               End-to-end user flows with Mermaid diagrams
  Backend-Schema.md         Data models, API contracts, ERD diagrams, KV namespace map
  UI-UX-Brief.md            Every screen in Build Studio and Cloud Dashboard; UX issues
  Implementation-Plan.md    Prioritised engineering roadmap across 4 phases
  Security.md               Numbered security-gap tracker (SG-01…) with fix status
  Sales-Blockers.md         Sales/GTM-framed gap tracker — what blocks the first enterprise sale
  Auth-and-Updater.md       Build Studio + runtime auth flows and both auto-updaters
  cost_model.md             Unit economics — LLM cost per compile, hosting, revenue model
  archive/                  Superseded reports, refactor case studies, rotated FIX.md logs
```

---

## Common Commands

### Developer setup (first-time)

```powershell
# Windows — run once after cloning
.\scripts\setup.ps1
```

```bash
# macOS / Linux — run once after cloning
./scripts/setup.sh
```

Both scripts install: conxa-core, Build Studio Python deps, Playwright Chromium, Electron node_modules, runtime node_modules.

After setup, start the dev server:

```bash
cd conxa-builder/electron && npm run dev
```

### Cloud backend

```bash
# Install shared foundation first (editable for dev), then cloud deps
pip install -e packages/conxa-core
cd conxa-cloud/backend && pip install -r requirements.txt   # or ./build.sh (used by Render)

# Run the API server (no Playwright — cloud does not record or compile)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# start.sh runs the same without --reload, binding to $PORT

# Tests — run from conxa-cloud/
# pytest.ini puts backend, conxa-builder/python, and packages/conxa-core on the path
cd .. && pytest -q tests

# Compile tools require conxa_compile on PYTHONPATH
PYTHONPATH=../conxa-builder/python python scripts/recompile_session.py <session_id>
PYTHONPATH=../conxa-builder/python python scripts/test_plugin.py <plugin-slug> --skip-phase2 --skip-phase5
```

### Build Studio backend (local pipeline)

```bash
pip install -e packages/conxa-core
cd conxa-builder/python && pip install -r requirements.txt && python -m playwright install chromium
python backend.py   # stdio JSON-RPC backend — normally spawned by Electron, not run directly
```

### Cloud frontend

```bash
cd conxa-cloud/frontend
npm install
npm run dev       # local dev server
npm run lint      # eslint
npm run build     # production build (Vercel)
```

### Runtime (Node.js MCP)

```bash
cd runtime
npm install
npm start                     # node server.js — MCP stdio mode
npm run build:win             # @yao-pkg/pkg → dist/runtime-win.exe
npm run build:mac             # @yao-pkg/pkg → dist/runtime-mac
```

### Configuration

Copy `.env.example` → `.env`. All backend settings use the `SKILL_` env prefix — see `packages/conxa-core/conxa_core/config.py`. LLM provider keys feed the multi-provider router — see `conxa-cloud/backend/ROUTER_SETUP.md`. Groq, Google AI Studio, and NVIDIA NIM are enabled by default.

---

## Architecture

The full technical reference — pipeline stages, runtime filesystem layout, all auth flows, recovery cascade tiers, API surface, and known gaps — is in **[`docs/TRD.md`](docs/TRD.md)**. Read it before touching the recorder, compiler, or runtime.

Quick orientation:

- **Build Studio** (`conxa-builder/python/conxa_compile/`): `bridge.js` → `session.py` → `pipeline/run.py` → `compiler/build.py` → `skill_package_builder.py`. All local. Cloud is not involved. Compiler uses `IdentityBundle` as sole identity source on the primary compile path — LLM writes selector strings only via the 1-click fix API's re-compile path (see Key Invariants); otherwise LLM is limited to the intent graph, vision anchors, and recovery.
- **Cloud** (`conxa-cloud/`): coordination only — LLM proxy, skill pack hosting, telemetry ingest, billing. Does not record, compile, or execute.
- **Runtime** (`runtime/`): two-layer architecture on the customer's machine.
  - **Host exe** (`@yao-pkg/pkg`, built `--no-bytecode`): bundles `bootstrap.js` + `_pkg_stubs.js` only. Provides `__hostRequire` so disk-loaded app code can use Playwright etc. Built with `build-runtime-host.yml`, tagged `host-vX.Y.Z`.
  - **App layer** (`conxa-app/`, disk-resident at `~/.conxa/conxa-app/`): contains `server.js`, `run.js`, `resolver.js`, `resolve_adapter.js`, `recovery.js` and the rest. Obfuscated JS, independently updatable without reinstalling the host exe. `bootstrap.js` enforces `min_host` semver before loading. Built with `build-runtime-app.yml`, tagged `app-vX.Y.Z`. Versioned-directory rollback (via `version_manager.js`) on failed update. Studio sandbox (`sandbox/.conxa/`) mirrors this layout for local workflow tests.

MCP tools exposed by `runtime/server.js`: `execute_skill`, `execute_sequence`, `list_skills`, `get_skill_inputs`, `get_execution_status`, `cancel_execution`, `authenticate`, `get_runtime_status`. There is no `refresh_skills` tool. Concurrency (RT-3, 2026-08-22): several runs may be active at once (separate chats, or several `execute_skill` calls in one chat) — see `runtime/app/run_registry.js` and `docs/TRD.md` §4.6. `execute_skill`/`execute_sequence` are admitted up to `CONXA_MAX_CONCURRENT_RUNS` (default 5); `cancel_execution` takes an optional `run_id`, required once more than one run is active. Two runs touching the SAME external platform (e.g. both hitting Render) still serialize against each other via `runtime/app/host_lock.js` — runtime-internal isolation alone doesn't protect the platform's own application layer from concurrent writes; runs on different platforms stay fully parallel.

---

## Where to Look First

| Concern | Code path |
|---|---|
| Recorder event types | `conxa_compile/recorder/bridge.js` → `pipeline/` → `compiler/build.py` → `runtime/run.js` |
| IdentityBundle / signal compilation | `conxa_compile/compiler/identity_bundle.py`, `selector_grammar.py`, `stable_hash.py` |
| Selector scoring / anchor quality | `conxa_compile/compiler/selector_score.py`, `selector_filters.py` (anchor quality gates) |
| Runtime element resolution | `runtime/resolver.js` (pure, unit-testable) + `runtime/resolve_adapter.js` (Playwright adapter) |
| Runtime recovery cascade | `runtime/app/cascade.js` + `recovery.js` — Tier A (in-process, zero tokens) then Tier B (armed agent handoff). Canonical table: `docs/TRD.md` §10.1 |
| Conxa Execute's in-app browser panel | `runtime/app/host_browser.js` (runtime side: `connectOverCDP`, borrows a view instead of launching) + `conxa-execute/app/electron/browser_panel.js`/`browser_control.js` (Execute side: owns the `WebContentsView`s + loopback control channel). Inert for every non-Execute MCP client. See `docs/TRD.md` §4.7, `Done.md` EXEC-41 |
| Build Studio's in-app browser panel (tests, recording, sign-in) | `conxa-builder/electron/browser_panel.js`/`browser_control.js` (run kinds, fixed 1280×720 record viewport) + `conxa_compile/recorder/host_view.py` (recorder's side of the panel) + `recorder/screencast_video.py` (video without `record_video_dir`) + `RecordingSession.embedded` in `recorder/session.py`; renderer `components/shell/BrowserPane.tsx` + `useNativeViewRegion.ts`. Switch: `SKILL_RECORD_IN_PANEL`. See `docs/TRD.md` §4.7a, `Done.md` BUILD-46 |
| Assertions / outcome validation | `conxa_compile/compiler/validation_planner.py`; runtime `verifyAssertions()` in `run.js` |
| Skill package building | `conxa_compile/skill_package_builder.py` (data-only output, auth excluded) |
| LLM calls (compile side) | task clients in `conxa_compile/llm/` → `conxa_core.llm.get_router()` → cloud proxy |
| LLM provider pool (cloud) | `conxa-cloud/backend/app/llm/router.py` behind `POST /api/v1/llm/proxy/{text,vision}` |
| Frame / iframe handling | `docs/TRD.md` § "Iframe Pipeline"; `bridge.js`, `session.py`, `build.py`, `run.js` |
| Shared data models | `packages/conxa-core/conxa_core/models/` — SkillPackage, RecordedEvent, Workflow, SkillPack |
| Auth (Build Studio) | `conxa-builder/python/services/auth_service.py` — Clerk PKCE → OS keyring |
| Auth (Runtime) | `runtime/auth_manager.js` — per-company token in keytar; AES-256-GCM session |
| Auth (Cloud API) | `conxa-cloud/backend/app/api/security.py` — Clerk JWT via PyJWT + JWKS |
| Telemetry | `runtime/tracker.js` → `conxa-cloud/backend/app/api/tracking_routes.py` |
| Skill pack sync | `runtime/sync.js` ↔ `app/api/skillpack_update_routes.py` |
| MCP registration into agent hosts | `runtime/mcp_register.js` (`register-mcp`/`unregister-mcp` subcommands, dispatched from `bootstrap.js`) + `mcp_hosts.js`/`mcp_hosts_toml.js`/`mcp_hosts_yaml.js` (per-host tables) + `config_edit.js`/`config_edit_toml.js`/`config_edit_yaml.js` (atomic, ownership-checked writers) + `durable_context.js` (post-sync discoverability files) — see `docs/TRD.md` §4.3 |
| Runtime two-layer bootstrap | `runtime/bootstrap.js` — min_host check, app-layer load, `.bak` fallback |
| Host / app update manifests | `conxa-cloud/backend/app/api/updates_routes.py` (manifest_version 2; deps: `conxa-runtime`, `conxa-app`) |
| Studio sandbox for workflow tests | `conxa-builder/python/conxa_compile/conxa_runtime.py` — stages host exe + app layer under `sandbox/.conxa/` |
| CI execution gate | `runtime/test/gate_replay.js` + `runtime/test/gate-skill/` — real skill replay, **active** in both `build-runtime-host.yml` (vs. the freshly built exe) and `build-runtime-app.yml` (vs. the `MIN_HOST` exe). A red app-layer gate usually means `MIN_HOST` is stale, not that the gate is wrong |
| Frontend screens | `conxa-cloud/frontend/src/` — Dashboard, Skill Packages, Billing, Team, Settings |

---

## Deployment

**Cloud backend** runs on Render. Root directory: `conxa-cloud/backend`. `build.sh` installs `packages/conxa-core` then `requirements.txt`. `start.sh` runs `uvicorn app.main:app` (`init_db()` creates schema on startup). A `Dockerfile` exists (build context = repo root). `GET /readyz` gates deploys (DB ping); `GET /healthz` is liveness. With `SKILL_AUTH_REQUIRED=true` the app **refuses to start** unless `SKILL_DATABASE_URL`, Clerk issuer/JWKS, `CORS_ORIGINS`, Cashfree credentials, and at least one LLM provider key are set. No silent fallback to filesystem DB in production.

**Cloud frontend** runs on Vercel. Project root: `conxa-cloud/frontend`. Build: `npm run build`. The Next.js route handler `/api/v1/*` proxies to `API_ORIGIN`. Requires `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY`.

**Runtime** ships as two artifacts embedded in the NSIS installer:
- **Host exe** (`dist/runtime-win.exe`): built with `--no-bytecode` so the Playwright selector engine works. Contains only `bootstrap.js` + `_pkg_stubs.js`. Tagged `host-vX.Y.Z`. Installed to `~/.conxa/` on Windows.
- **App layer** (`conxa-app/`): obfuscated JS bundle (`server.js`, `run.js`, `resolver.js`, etc.). Tagged `app-vX.Y.Z`. Staged beside the host exe at `~/.conxa/conxa-app/`. Independently updatable via cloud; `bootstrap.js` enforces `min_host` semver before loading it, and `version_manager.js` rolls back to the previous versioned directory on failure.

**The installer is a thin, static-config-only artifact for skill packs** — it stages only the company's `pack.json` (identity, `installer_version`, sync/tracking endpoints and tokens), never any skill file trees. After install, `runtime/sync.js`'s delta-sync (§Backend-Schema.md §5.9) downloads every skill and creates each skill's `current` junction on its own first run, exactly as it does for every later update. `{installer_version}` (a small Conxa-owned allow-list, e.g. `v1`/`v2`) is frozen into `pack.json` at build time and is never reassigned remotely — see `docs/Backend-Schema.md` §5.1a for the full versioned-endpoint scheme. Skill Pack Publishing (Build Studio's "Publish Skill Package" page) is the primary, mandatory, version-controlled release action; Build Installer is a secondary, advanced action that packages an already-published release and whose cloud upload is optional (a failed/skipped upload never fails the build).

Self-updates poll `/api/v1/updates/runtime-manifest` (manifest_version 2; deps keyed `conxa-runtime` + `conxa-app`). SHA-256 integrity required for both artifacts.

---

## Key Invariants

These are non-negotiable. Do not work around them.

- **Auth files never enter build output.** `auth/auth.json`, Playwright storageState, and credentials are local runtime state only. `skill_package_builder.py` enforces this — the check must remain.
- **Tier A recovery costs zero LLM tokens.** LLM fires at Tier B only. Do not introduce silent LLM fallbacks into compiled-selector or a11y resolution paths. `cascade.js` implements Tier A (exception ladder + a11y / re-hover / dialog-scope); the runtime escalates to Tier B (armed agent recovery) only after Tier A is exhausted. Internal ceiling numbers 1–4 still exist on `CONXA_MAX_RECOVERY_TIER` (Studio = 2, MCP = 4) — they are not the product names.
- **An `ai_review` step (EXEC-13) is not a violation of the rule above, and must not be read as one.** It is an explicit, author-placed, editor-visible reasoning checkpoint whose entire purpose is to invoke reasoning — not a fallback, silent or otherwise, and it does not sit in a selector/a11y resolution path. It deliberately sits *outside* the Tier 1-4 recovery cascade: it never touches `recovery_stage.js`'s escalation state or `retry_budget.js`'s retry-budget map, and its resume path is **not** gated on `CONXA_MAX_RECOVERY_TIER`/agent-recovery availability the way Tier B escalation is — an `ai_review` pause must be answerable by any MCP caller, including the Build Studio sandbox, which answers it itself through the metered `human_edit` LLM proxy rather than an agent. See `docs/TRD.md` §11.3.
- **Iframe chain is preserved verbatim** from recording through compile and execution. Bounding boxes are page-level (offsets accumulated up the parent chain in `session.py`).
- **A `try_dismiss` branch is built from one shared builder, never hand-rolled.** `compiler/second_opinion.py::build_try_dismiss_from_hint` is the only place a recorder `optional_hint` becomes a branch. Both callers use it: the compiler's second opinion at compile time, and `editor/workflow_mutations.py::confirm_optional_interstitial` when a reviewer confirms one the pass left alone. Two paths that build the same shape by hand will drift, and a branch that differs between them fails at replay in a way that looks like a site problem.
- **`frame_enter` / `frame_exit` steps get `no_recovery_block`.** These are navigation markers, not interactable elements. They are never retried.
- **All API routes live under `/api/v1`.** The frontend and runtime both depend on this prefix. Do not route anything else there. **Documented permanent exception:** `tracking_routes.py`'s public telemetry-ingest endpoint is also served bare at `/api/tracking/{company}/events` (in addition to `/api/v1/tracking/...` and the versioned `/api/v1/workflows/{installer_version}/{company}/tracking/events`). This is a deliberate, permanent back-compat alias for already-deployed runtimes whose installer-baked `pack.json.tracking.tracking_url` points at the bare path — it cannot be removed without breaking those installs, and it is not sanctioned as a precedent for adding further routes outside `/api/v1` by analogy.
- **The cloud does not compile or execute.** Recording, compilation, skill package building, and skill execution are local-only. Keep them that way.
- **Host exe built `--no-bytecode`.** V8 bytecode (.jsc) masks the Node version and causes the Playwright selector engine to segfault in pkg-bundled binaries. Never re-enable bytecode for the host exe.
- **Resolver never blindly picks `candidate[0]`.** `resolver.js` requires the winning candidate's margin over the runner-up to clear `uniqueMargin` (default 0.15); otherwise it falls through to the next signal. Do not add shortcut paths that skip this gate.
- **LLM does not write selector strings on the primary compile path.** `IdentityBundle` + `selector_grammar.py` are the sole selector generators when a workflow is first compiled. LLM is retained for: the workflow intent graph, relational vision anchors, and recovery describe-then-match (Tier B). Two narrow, user-initiated re-compile exceptions exist, neither part of the primary compiler: the 1-click fix API (`compiler/patch.py::_regenerate_compiled_selectors`, via `llm/selector_regeneration.py`), which re-runs LLM-assisted selector generation against the original DOM snapshot when a user manually re-targets a step's element in the editor; and the Human Edit re-target wizard's "draw a new region" path (`editor/retarget.py` → `llm/region_selector_vision.py`, task `region_selector`), which uses a vision LLM — screenshot with the drawn region highlighted, plus the recorded DOM — because no recording stores per-element geometry a text prompt could resolve a drawn region against.

  **This is a rule about element addresses, not about meaning, and it is deliberately narrower than it used to be.** The compiler's second opinion (`llm/workflow_semantics.py` → `compiler/second_opinion.py`, BUILD-25) *does* write to compiled steps on the primary path: `input_binding`, `{{placeholder}}` tokens inside `value`, `phase`, and — for a step the recorder already flagged as stochastic — the `try_dismiss` `branch`, `intent` and `recovery`. Stage (d) added two narrow exceptions, neither of which weakens the selector-string rule: `suggest_assertion` may additively append a `validation.assertions` entry, but only a text/URL/state type (`text_present`, `text_absent`, `url_changed`, `url_pattern`, `state_changed`) — never `selector_present`/`selector_absent`/`value_equals`, whose `target` is a raw Playwright selector — and always with `required=False`, so a wrong one can never halt a run. `flag_noise` may remove a step the recorder itself observed to have no effect, but archives it in full under `compile_report["archived_steps"]` rather than deleting it — see `compiler/second_opinion.py::archive_flagged_steps`. The pass still never touches `target`, `identity_bundle`, `compiled_selectors`, `wait_for`/`success_conditions`, or the frame/tab chain. The earlier framing ("the model annotates meaning, it does not write the program") no longer holds: it writes meaning into the program, and **Human Review is the gate** — a reviewer sees the applied result and edits a wrong call the same way they edit any other compiler output. The consequence, accepted knowingly: a compile is no longer byte-identical whether the pass ran or not. Every route that applies *nothing* (disabled, failed, empty) still is. See TODO.md BUILD-25 for the full trade.
- **App-layer min_host is enforced at load time.** `bootstrap.js` reads `version.json` from `conxa-app/` and refuses to load if `min_host` > current host semver. Do not bypass this check when bumping the app layer.
- **MCP host-config editing lives only in the runtime, never in the installer.** `conxa-runtime.exe register-mcp`/`unregister-mcp` (`runtime/mcp_register.js` + `mcp_hosts*.js` + `config_edit*.js`) is the sole place that reads or writes an AI agent host's config file. NSIS invokes the subcommand and nothing more — do not reintroduce config-editing logic (PowerShell or otherwise) into `setup.nsi.tmpl`. This is what makes install and uninstall derive the registration key (`conxa` vs `conxa-dev`) from the same source instead of two values that can drift apart — see MCP-4 in `Done.md` for the bug that shipped when they didn't.
- **One workspace has exactly one skill pack and one installer, forever.** `workspace_id` is the sole identity key for a SkillPack — there is no per-workspace multi-company concept. `company_slug` as a separately claimable, globally-unique identifier was removed (2026-08-21): KV namespaces (`skill_pack_meta`, `sync_tokens`, `tracking_tokens`), Clerk-authenticated dashboard routes (publish/release/groups/deployments), `pack.json`, and every subsystem (conxa-core, Build Studio, runtime, frontend) key off `workspace_id` directly. `conxa_core.workspace.workspace_dir_slug(workspace_id)` is a pure character-safety transform (filesystem/URL-safe), not an identity mechanism — never reintroduce a name-derived, separately-owned slug. Runtime-facing routes (sync delta, tracking ingest — bearer-token authenticated, no Clerk session) still carry `{workspace_id}` as a path segment for routing, but the bearer token is what actually authorizes the call. Deliberate, narrow exception (2026-08-21): `installer_builder.py::build_installer`'s `installer_name` param (a user-typed, unverified company domain, e.g. "acme.com") names that one installer's on-disk `skill-packs/<slug>/` folder and `.exe` filename via `workspace_dir_slug(domain)` — purely cosmetic, local to a single installer build. It is never written to any KV namespace, never used for auth or routing, and `workspace_id` remains the identity backing sync/tracking tokens and every cloud lookup; if this domain later needs to be a real identity key (e.g. cloud-side lookups by domain), that requires actual verification first, not just this cosmetic slug.
- **A popup that asks for a decision is never answered on anyone's behalf.** A recorded decision dialog ("file already exists: Replace / Keep both / Cancel") compiles to an `outcome_fork` step (`compiler/outcome_fork.py`, runtime `fork.js` + `run.js::runForkStep`), not a `try_dismiss`. A consequential option (replace / overwrite / delete ...) never runs under `auto` unless the author approved it — `policy.author_approved` is written only by `editor/workflow_mutations.py::set_fork_policy`, the compiler always writes `false`, and the runtime fails closed (to a person) without it. A person's answer comes only from a person (the Studio test driver routes `conxa/fork` pauses to `_fork_hook`, never to the LLM answerer), an unrecognised popup is only ever answered with one of its own visible buttons, and the Tier 1 ladder, learned dismissals and an agent's nominated dismissal all refuse to dismiss a decision dialog (`fork.js::isDecisionDialog`). Cookie banners and plain close-me popups keep the zero-token known-pattern path. See `docs/TRD.md` §11.4.
- **An app map holds the shape of an app, never its data, and it advises rather than decides.** `conxa_compile/app_graph/` folds every compiled workflow into per-host screens, actions and popups (`docs/TRD.md` §11.5). All text enters through `app_graph/scrub.py` (no typed values, page text, file names, emails, digits or selectors), no LLM touches it, and it is derived data that is always rebuilt from the skills, never edited. Nothing in the map answers a decision popup on anyone's behalf: that stays governed by the outcome-fork invariant above. The only standing answer is the author's own `Popup.app_policy`, written solely by `handlers/app_graph.py::cmd_set_app_popup_policy`; a consequential button never runs under it without `author_approved`, and the runtime (`app_knowledge.js::decideKnown`) fails closed to a person.
- **The understanding check labels meaning; code does every rewrite, and testing waits for it.** `conversation/understanding.py` (see the "Understanding check" paragraph in `docs/TRD.md`) may only label candidate values `fixed`/`varies`/`unsure`, name an input and judge coverage; it never sees or writes a selector, and its answer is validated in code (unknown refs, bad names, a `literal` not inside the recorded value are dropped). A "varies" answer becomes `editor/workflow_mutations.py::parameterize_literal`, which rewrites exactly a navigate `url`, an `entity_binding.identifier` and the matching text/URL assertion targets — never `target`, `identity_bundle` or a selector. One narrow exception, for a clicked item with no row scope: `parameterize_click_text` substitutes that label by `{{name}}` in text signals, fingerprint text and `compiled_selectors` (and drops signals that would still pin the recorded item), in code, and only a person's explicit act triggers it — the user's ticked answer, or Apply on a chat `parameterize` change card whose gate dry-ran the same function (`editor/copilot_proposals.py`); the model only names the recorded value and the input id and still never writes or sees a selector. A failed or empty answer must degrade to asking (unticked) or to asking nothing, never block a compile. While any asked question is open, the post-compile check or a compile is running (`understanding` / `compiling` runtime flags), or a chat change card is unapplied, `derive_lifecycle` leaves `test` out of `allowed_actions`, and `ConversationMixin._require_allowed` enforces that server-side for every entry point.
- **Chat can only do what the lifecycle allows, and a side effect always waits for Confirm.** `conversation/intent_router.py` maps free text to an id in `lifecycle.allowed_actions` (anything else is "chat"); `conversation/actions.py` is the single table, and the renderer's `lib/actions.ts` mirrors its ids (a test keeps them in sync). Actions with `side_effect` post a `confirm_action` card and run nothing until it is pressed; test inputs stated in chat only pre-fill the card.
- **A step that reads a value takes its target from the recording, and an input can never switch off a guard.** `extract` resolves through the same IdentityBundle walk and recovery cascade as a click and writes only `inputs[output_name]`; no match or an empty value is a non-retryable failure, never an empty output. `run_if` reads the run's inputs only (never the page) and is refused on `ai_review`, `handover` and outcome forks (`patch_gate`), so no input can skip the confirmation in front of a destructive step. `on_missing: "skip"` acts only on a *provable* absence (`entityRoots(...).absent`: the list is on the page and none of its records is this run's); ambiguous, hung, unrendered and unsupplied-identifier cases still fail closed.
- **A step never executes on a tab other than the one it was recorded on.** `runtime/tabs.js::resolveStepPage` resolves each step's live page fresh, per step, from `SkillStep.tab`. If the recorded tab cannot be resolved (a site-opened tab that never opens, a closed tab that should still exist), the step fails — it never falls back to whatever page happens to be current. This is the tab twin of the frame-chain rule above: a same-looking element on the wrong tab is worse than a clean failure.

---

## Maintaining the Docs

The `docs/` files are living documentation. After making significant changes to the codebase, update the relevant doc so it stays accurate. A doc that drifts from the code is worse than no doc.

### Which doc to update after which changes

| Change type | Update |
|---|---|
| Architecture change — new system, new component, new communication path | `docs/TRD.md` |
| Auth flow change — new token type, new validation step, new storage | `docs/TRD.md` |
| New or changed API endpoint | `docs/TRD.md`, `docs/Backend-Schema.md` |
| Data model change — new field, new model, new KV namespace | `docs/Backend-Schema.md` |
| New telemetry event code | `docs/Backend-Schema.md` |
| Recovery cascade change — new tier, changed tier order or cost | `docs/TRD.md`, `docs/App-Flow.md` |
| User flow change — new screen, new step in an existing flow | `docs/App-Flow.md` |
| New screen or significant screen redesign | `docs/UI-UX-Brief.md` |
| Engineering task completed from the roadmap | `docs/Implementation-Plan.md` (mark done or update status) |
| New gap or tech debt identified | `docs/Implementation-Plan.md` |

### What does NOT require a doc update

- Bug fixes that do not change observable behavior or data contracts.
- Internal refactors that keep the same API surface and data models.
- Configuration changes, dependency version bumps.
- Test additions.

When in doubt, update the doc. A stale sentence in the TRD costs future engineers hours.

### FIX.md rotation

Keep appending to `FIX.md` after every prompt as usual. When `FIX.md` crosses a calendar-day boundary, rotate the completed portion into `docs/archive/fix-log/FIX-<YYYY-MM-DD>.md` (one file per day, split by each entry's own trailing `— YYYY-MM-DD` date, not a blind line-number cut — entries aren't always strictly chronological top-to-bottom), add a row to `docs/archive/fix-log/INDEX.md`, and leave only the current entries live in `FIX.md`.

### TODO.md

`TODO.md` at the repo root is the single prioritized backlog spanning documentation, architecture, and every subsystem. When you complete an item from it, **move it to [`Done.md`](Done.md)** — cut the whole entry out of `TODO.md` (leave nothing struck-through behind) and file it in `Done.md` under its area section (the ID prefix: BUILD, EXEC, CLOUD, RT, …) in ID order, keeping its ID and title and adding a `**Resolved:** YYYY-MM-DD` line plus one line on what fixed it; then add its number to the summary table at the top of `Done.md` and bump that area's count. Update any `TODO.md` cross-references to it ("depends on X") to point at `Done.md`. When you discover new, still-open work during a task, add it to `TODO.md` rather than leaving it undocumented.

### PRD update policy

**Do not edit `docs/PRD.md` for individual features, bug fixes, or incremental improvements.**

`docs/PRD.md` is a strategic document. It reflects the company's vision, product positioning, competitive analysis, and multi-year roadmap. It should only change when something fundamentally shifts at the company level:

- A new target market or customer segment is adopted.
- The business model changes (e.g., from per-skill-package to platform licensing).
- The distribution model changes significantly (e.g., moving beyond MCP).
- The core value proposition shifts (e.g., adding a cloud execution tier).
- A new phase is added to the roadmap after completing a major milestone.

For everything else — adding a feature, fixing a flow, shipping a phase — update the technical docs (`TRD.md`, `Backend-Schema.md`, `App-Flow.md`, `Implementation-Plan.md`) and leave the PRD alone.

After every prompt, add an entry to FIX.md summarizing what changed. Write it so a non-technical person (a founder, a salesperson, a new hire) understands it on first read — no jargon, no file paths, no function names, no acronyms.

Rules for each entry:
- 2-4 short sentences, plain words, one idea per sentence.
- Say what the problem was (in real-world terms — "the button did nothing when clicked", not "handler was unbound"), what changed, and why it matters to a user.
- Use an analogy if it helps ("like a light switch that was wired to the wrong bulb").
- No code names, class names, or filenames. If you must reference a screen or feature, use its plain product name (e.g., "the Publish page", not `publish.py`).
- End with a one-line "— YYYY-MM-DD" date, as today.

Example entry:
> **Fixed the workflow editor freezing when renaming a step — 2026-07-16**
> Renaming a step in the editor used to lock up the screen. Now it saves instantly. This was breaking anyone trying to fix typos in their recorded workflows.

Bad entry (too technical — do not do this):
> Fixed race condition in `patch.py::_regenerate_compiled_selectors` where debounce wasn't awaited.