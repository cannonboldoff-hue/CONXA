# Runtime Refactor Audit

**Scope:** `runtime/` folder only — read-only audit, no code was changed.
**Method:** 5 parallel research passes — (1) architecture & layering, (2) `run.js` internals, (3) `server.js` + duplication, (4) test coverage & safety nets, (5) config/update module families.
**Date:** 2026-08-22

---

## 1. The Big Picture (in plain words)

The runtime is the thing that runs on the customer's machine and executes recorded skills via MCP. It ships in two pieces:

- **Host exe** — a small frozen launcher (`bootstrap.js` + `_pkg_stubs.js`), updated rarely.
- **App layer** — everything else on disk under `~/.conxa/conxa-app/`, updated frequently.

**Overall verdict:** the codebase is healthier than its two giant files suggest. There are **no circular dependencies**, the pure modules (`resolver.js`, `recovery.js`, `sync_errors.js`) are exemplary, and comments are unusually disciplined. The real problems are concentrated in three places:

1. **Two god-files** — `run.js` (~1,800 lines) and `server.js` (~1,600 lines) absorb nearly every concern.
2. **A fuzzy host/app boundary** — which files are frozen into the exe vs updatable is invisible, undeclared, and unenforced.
3. **One source of truth missing for environment/paths** — dev vs prod path resolution is re-derived inline in several files that can silently disagree.

### Health scorecard

| Area | Grade | One-liner |
|---|---|---|
| Dependency graph | A | No cycles; leaf modules stay pure |
| `resolver.js` + tests | A | Model citizen of the whole repo |
| Config-edit family | B+ | Better factored than the naming suggests |
| Update/version stack | B- | Clean responsibilities, but dead code and env drift |
| `run.js` | C | Capable but a 5-in-1 monolith |
| `server.js` | C- | 685-line tool dispatcher, zero unit tests |
| Host/app boundary | D | Undeclared, unenforced, already leaking (HIGH) |

---

## 2. Top 10 Problems (ranked)

| # | Problem | Where | Severity |
|---|---|---|---|
| 1 | The "host = 2 files" claim is false — ~13 more modules are frozen into the exe with no declared list and no CI guard | `bootstrap.js`, `package.json` pkg.scripts | **HIGH** |
| 2 | The `sync` subcommand loads app-layer code **without** the min_host gate | `bootstrap.js:59-62`, `cli_sync.js:24` | **HIGH** |
| 3 | Env/path/API-base defaults re-derived in server.js, browser.js, run.js instead of using `env.js` — silent dev/prod divergence | `server.js:15-20,335,348`, `browser.js:7-13`, `run.js:15`, `auth_manager.js:27` | **HIGH** |
| 4 | `bootstrap.js` min_host check has **zero tests** — the most fragile safety net in the system | `bootstrap.js:115-144` | **HIGH** |
| 5 | `server.js::_handleTool` execute branch is ~578 lines doing orchestration, validation, telemetry, browser lifecycle, and error taxonomy in one closure | `server.js:933-1511` | HIGH |
| 6 | `server.js` has zero unit tests (~1,600 lines); loading it connects stdio as a side effect, so it's hard to even import in a test | `server.js` | HIGH |
| 7 | `run.js::withLocator` hides three completely different control-flow modes behind one function signature — where future bugs will concentrate | `run.js:413-476` | HIGH |
| 8 | `frame_enter`/`frame_exit` no-recovery invariant enforced only by a hardcoded list inside the monolith, with no direct unit test | `run.js:46-55, 999-1001` | MED-HIGH |
| 9 | Tier 1/2 "zero LLM tokens" invariant protected by convention only — nothing stops a future network call inside a recovery remedy | `recovery.js` | MED |
| 10 | ~30 flat `.js` files give no hint which are host-frozen vs updatable-app | whole folder | MED |

---

## 3. Architecture & Layering Findings

### 3.1 The frozen surface is bigger than anyone thinks (HIGH)
The docs say the host exe contains only `bootstrap.js` + `_pkg_stubs.js`. In reality, bootstrap transitively bundles roughly **13 more files**: `env.js`, `version_manager.js`, `mcp_register.js` (+ the whole `config_edit*`/`mcp_hosts*` subtree), `cli_sync.js`, `manifest_manager.js`, `http_client.js`, `install_identity.js`. Every casual edit to any of those is secretly a *host release change*, frozen until customers get a new exe — and no CI check guards this membership list.

**Fix direction:** declare the host-layer module list explicitly (a manifest file), add a CI check that the built exe matches it, or physically split folders (`runtime/host/`, `runtime/app/`, `runtime/shared/`).

### 3.2 min_host bypassed on the sync path (HIGH)
Loading `server.js` goes through the full semver gate (`tryLoad`). But the install-time `sync` subcommand dynamically requires the app layer's `sync.js` with **no version check**, and swallows failures. An incompatible app layer can execute against an old host during installation.

### 3.3 Seven globals act as an untyped service locator (MED)
The host↔app bridge is a pile of globals (`__hostRequire`, `__versionManager`, `__conxaEnv`, `__conxaManifest`, …). Every consumer invents its own guard idiom to detect host-vs-standalone mode. A typo degrades silently to a disk fallback that only breaks in packaged prod. Also: nobody ever sets `global.__manifestManager`, so that branch in `server.js:336-338` is dead defensive code.

**Fix direction:** centralize accessors in one tiny `host_bridge.js`.

### 3.4 Two layers write to the same customer files with independently-defined markers (MED)
Frozen `config_edit*`/`mcp_register.js` and updatable `durable_context.js` both write/clean labeled blocks in Claude-style config files, each defining its own marker conventions. If the app layer changes its marker format, the frozen uninstall path may fail to clean up what newer code wrote — orphaned blocks on customer machines.

### 3.5 Misc layer hygiene
- `_pkg_stubs.js` hand-duplicates the dependency list from `package.json` — a new dep works locally and breaks only in the packaged exe (**MED**).
- `auth_manager.js` uses a completely different idiom (`process.pkg` + native dlopen for keytar) than everyone else's `__hostRequire` — undocumented exception (**LOW**).
- `run.js`/`drift.js` reach into underscore-private exports of other modules (`_extractDescriptor`, `STALE_RE`) (**LOW**).
- `run.js` imports error classification from `tracker.js` — the execution engine shouldn't need the telemetry/network module loaded (**LOW**).

---

## 4. `run.js` Deep Dive (~1,800 lines)

It fuses five distinct jobs: action dispatch, target/frame resolution, verification, recovery cascade, and run orchestration. Only resolution + orchestration naturally belong together.

### Complexity hotspots

1. **`runPlan` (1597–1747, ~151 lines)** — the one true god-function. Its loop body handles cancellation, tab resolution, load pacing, screenshots, execution, verification, telemetry, auth short-circuit, cascade, and repair events — six different failure exits. → Extract "resolve step page" and "execute-or-recover one step" as named phases.
2. **`withLocator` (413–476)** — three modes behind one signature: PRIMARY-bundle retry loop, agent-override validation path, plain-string mode. Completely different control flow and error semantics per mode. → Split into `withPrimaryLocator` / `withStringLocator`.
3. **`evaluateAssertion` (1172–1238)** — if/else chain over 9 assertion types; should be a type→checker map. Also builds `new RegExp(target)` from interpolated pack input at 1190 (and 872, 881) — a ReDoS surface from hostile skill packs (**security note**).
4. **HANDLERS object (752–1047)** — mixes real DOM actions with filesystem/upload logic and branch-control primitives. `type` handler is a byte-for-byte copy of `fill`.
5. **`recoverStep` (1463–1502)** — the same "bail / recover / return" triple repeated 6 times; the stage list is data wearing a control-flow costume.
6. Smaller ones: override-validation duplicates resolver's margin logic with locally-copied constants (drift risk against the "never pick candidate[0]" invariant), remedy dispatch via string-compare chain, upload-path parsing handling four formats in one function.

### State & coupling smells
- `retryBudget` Map is defined here but **never checked here** — enforcement lives in server.js. Undocumented asymmetry.
- `inputs.__downloadCount` — a private counter smuggled into the user-visible inputs object, invisible to anyone inspecting inputs.
- Cross-language contracts (interpolation grammar ↔ Python compiler, a11y name precedence ↔ identity_bundle.py) asserted only in comments, never by tests.
- ~33 bare `catch (_) {}` blocks — mostly intentional, but there's no single debug hook to make silence observable.
- Every interactive step pays for a pre-step screenshot that is consumed only on failure — wasted work on the happy path.

### Proposed split (no behavior change, preserves all 30 exports via a barrel)
```
run/
├── config.js, interpolate.js, retry_budget.js, recovery_log.js
├── uploads.js, housekeeping.js
├── resolution/   frames.js · resolve_step.js · gate.js · override_check.js
├── locators.js
├── handlers/     actions.js · branches.js · uploads.js · downloads.js · index.js
├── verify/       assertions.js · polling.js · signature.js · verify_step.js
├── cascade.js    evidence.js    auth_detect.js
└── run_plan.js
```
Dependency direction stays one-way: `run_plan` → `cascade` → `verify` + `locators` → `resolution`. After the split, the public barrel shrinks from 30 exports to ~8.

---

## 5. `server.js` Deep Dive (~1,600 lines)

Good news first: delegation is genuinely good — the step loop, selectors, T1/T2 recovery, sync, and updates all live elsewhere. The bloat is in two places:

- **`_handleTool` execute branch (933–1511, ~578 lines):** input gates, integrity gate, semver gate, enrichment, overrides, retry budget, execution lock, deadline watchdog, abort wiring, tracker setup, park adoption, browser acquisition, page listeners, run loop, auth interception, session save, success response, and a giant catch with four exit shapes.
- **`_buildFailureResponse` (662–842, ~180 lines):** LLM prompt-engineering text assembly mixed with filesystem reads and Playwright screenshot capture.

Other findings:
- Session-save logic duplicated between `server.js:1346-1356` and `browser.js::_persistSession` — can drift on encryption policy (**MED**).
- `get_runtime_status` reads `update-pending.json` — **nothing writes that file anymore**; it reports a phantom field forever (**MED**, delete it).
- Module-level mutable state (`activeExecution`, `syncState`, `skillIndex`, `_parkedRecovery`) plus stdio-connect-on-load makes the file untestable without spawning the whole server.
- CLI installer mode (`--install-playwright`, ~85 lines) lives inside the MCP server file.

**Proposed split** (boundaries already exist as numbered comment banners in the file): thin composition root (~200 lines) + `logger.js`, `cli_installer.js`, `tool_defs.js`, `execution_state.js`, `recovery_park.js`, `failure_response.js`, `run_orchestrator.js`, `phonehome.js`. Start with `failure_response.js` and `recovery_park.js` — self-contained and immediately unit-testable without MCP.

---

## 6. Duplication Inventory

| Duplicate | Locations | Est. size | Severity |
|---|---|---|---|
| Env/path/API-base defaults (vs authoritative `env.js`) | `server.js:15-20,335,348`, `browser.js:7-13`, `run.js:15`, `auth_manager.js:27` | ~25 lines × 3 | **HIGH** |
| `_fetchJSON` / `_downloadBuffer` | `manifest_manager.js:96-123` vs `sync.js:14-49` | ~55 lines | MED → hoist into `http_client.js` |
| Session save flow | `server.js:1346-1356` vs `browser.js:209-222` | ~10 lines | MED |
| Marker-span block surgery | `config_edit_toml.js:52-115` vs `durable_context.js:108-141` | ~35 lines × 2 | MED |
| Register orchestrators (json/toml/yaml) | `mcp_register.js:88-156` — three near-identical bodies | ~50 lines | MED → unify behind a host adapter interface |
| Micro-helpers (`dirExists`, `envOverride`, …) | 4 copies across `mcp_hosts*` + `durable_context.js` | ~30 lines | LOW |
| `type` handler = `fill` handler | `run.js:773-783` | 5 lines | LOW |

**Not duplication (leave alone):**
- `config_edit_yaml/toml` properly reuse `config_edit`'s CAS skeleton — they differ where formats genuinely differ.
- `bootstrap.js` vs `server.js` update checks use disjoint components (`conxa_app` pre-load vs `conxa_runtime` post-load) — deliberate division.
- `run.js AUTH_FAILURE_URL_RE` vs `browser.js LOGIN_PATH_RE` are intentionally different (comments say "don't merge" — respect that).
- `atomicWrite` in sync.js vs config_edit.js share a name but differ meaningfully — rename one rather than merge.

**browser.js (666 lines)** does five jobs: browser factory + cache, session store/validation, interactive-login UX, WorkflowGroup model (reads pack.json directly — schema knowledge leaking into the browser layer), shutdown. Suggested split: `browser_launch.js`, `session_store.js`, `group_auth.js`, `interactive_auth.js`. Cohesion issue, not correctness.

---

## 7. Tests & Safety Nets

### Coverage snapshot
- **Excellent:** `resolver.js`, `recovery.js`, `resolve_adapter.js`, `sync.js`, config/host-register families, version managers — mostly offline pure-mock `node:test`.
- **Dangerous gaps:** `bootstrap.js` (**zero tests**, hosts the min_host invariant), `server.js` (**zero unit tests**), `tracker.js`, `skill_loader.js` (only happy path via gate replay), `browser.js` lifecycle.

### Invariant risk assessment

| Invariant | Enforced by | Protected by tests? | Risk |
|---|---|---|---|
| Resolver never picks candidate[0] (margin ≥ 0.15) | `resolver.js:128-170` | Yes — but the **default value itself isn't pinned** by any test | LOW-MED |
| frame_enter/frame_exit no recovery | hardcoded list `run.js:46-55` | Indirectly only | **MED-HIGH** — weakest enforced |
| Tier 1/2 zero LLM tokens | convention (no imports today) | Ceiling gate checks output shape, not absence of network calls | MED |
| min_host at load time | `bootstrap.js:115-144` | **Nothing** — not even the rejection path | **HIGH** |
| Host `--no-bytecode` | package.json flags | Actively guarded by `gate_replay.js` in CI | LOW |

### Test-infrastructure traps
- `npm test` runs `node --test` over the whole directory including Playwright/spawn/machine-specific scripts — CI deliberately avoids it and uses an explicit 9-file list instead. Meaning: **most of the suite runs in zero pipelines.**
- Several custom-harness scripts aren't wired anywhere; `test_mcp_client.js` contains a hardcoded machine path and is effectively dead.
- `gate_recovery_ceiling.js` (proves tier-ceiling behavior) is **not in any CI workflow** — manually verified only.

---

## 8. Recommended Roadmap (suggested order, all optional — nothing implemented)

**Phase 0 — Safety nets before touching anything (~1 day)**
1. Extract bootstrap's min_host evaluation into a pure function; unit-test accept/reject/malformed-json/rollback.
2. Pin `DEFAULT_UNIQUE_MARGIN === 0.15` and export+test `NOOP_STEP_TYPES` membership for frame markers.
3. Split tests into `unit/` (offline, run everywhere incl. CI) vs `e2e/`; make `npm test` run only unit.

**Phase 1 — Correctness fixes (small diffs)**
4. Route all env/path/API resolution through `env.js` (delete the fallback copies).
5. Apply the min_host gate to the `sync` subcommand path.
6. Remove dead `update-pending.json` reporting and dead `__manifestManager` branch.
7. Hoist shared `getJSON`/`getBuffer` into `http_client.js`; fix `_pkg_stubs.js` ↔ package.json drift risk with a build-time check.

**Phase 2 — Declare the boundary**
8. Publish the host-frozen module manifest + CI check guarding exe membership.
9. Centralize host-globals accessors in one `host_bridge.js`.

**Phase 3 — Decompose the monoliths (incremental, behavior-preserving)**
10. `server.js`: extract `failure_response.js` + `recovery_park.js` first (immediately testable), then tool defs, execution state, orchestrator.
11. `run.js`: extract along the seams above — cascade, verify, handlers, resolution — keeping the 30-export barrel stable.
12. `browser.js`: split launch/session/group-auth/interactive-auth.

**Phase 4 — Polish**
13. Unify mcp_register orchestrators behind a host adapter interface; merge marker-span surgery implementations.
14. Mechanical invariant guard: lint rule banning `http/https/fetch` imports transitively from `recovery.js`; wire `gate_recovery_ceiling.js` into CI.
15. Add a direct `skill_loader.js` input-validation test suite; delete/adapt machine-specific dead test scripts.

---

## 9. What's Already Good (don't break while refactoring)

- Zero circular dependencies; leaf modules depend only on Node builtins.
- `resolver.js` purity (browser-independent, fully unit-tested) is honored everywhere — keep it that way.
- Comment discipline in `run.js`/`server.js` is unusually high; complexity comes from breadth, not neglect. The numbered section banners in server.js are effectively the target module list.
- `gate_replay.js` is a genuinely strong CI gate: real host exe, real staged layout, real MCP handshake, real click.
- Entry-point sharing (cli_sync reusing sync.js unchanged) shows the layering idea works when applied.
- The dual-mode global-with-local-fallback pattern is sound — it just needs centralizing, not redesigning.

---

*Report generated from read-only analysis. All file:line references verified at audit time.*
