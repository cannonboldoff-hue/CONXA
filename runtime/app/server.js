#!/usr/bin/env node
"use strict";
const path   = require("path");
const fs     = require("fs");
const os     = require("os");
const httpClient = require("./http_client");
const hostBridge = require("./host_bridge");
const semver = require("./host_bridge").hostRequire("semver");
const { loadInstallId } = require("./install_identity");
const { installedSkillVersions } = require("./installed_versions");
const { collectSyncErrors } = require("./sync_errors");
const pageScripts = require("./page_scripts");
const { plainCause } = require("./errors");
const hostBrowser = require("./host_browser");
const { DOWNLOAD_WAIT_TIMEOUT_MS } = require("./run_config");

// ─── 1. Resolve CONXA_DIR (install, read-only) and CONXA_DATA_DIR (user-writable) ─
// env.js is the single source of truth for dev/prod path roots. Under the host exe,
// bootstrap.js ran env.apply() before loading this file, so process.env already holds
// the resolved values and the || fallbacks never fire; they exist for standalone dev
// mode (`node server.js`), where resolve() re-derives the same per-env defaults.
const _envInfo       = hostBridge.env();
const CONXA_DIR      = process.env.CONXA_DIR || _envInfo.conxaDir;
const CONXA_DATA_DIR = process.env.CONXA_DATA_DIR || _envInfo.dataDir;

const SKILL_PACKS_DIR = path.join(CONXA_DIR, "skill-packs");
const CACHE_DIR       = path.join(CONXA_DATA_DIR, "cache");
const SESSIONS_DIR    = path.join(CACHE_DIR, "sessions");
const LOG_FILE        = path.join(CONXA_DATA_DIR, "logs", "runtime.log");
const RUNTIME_VERSION = hostBridge.runtimeVersion(process.env.CONXA_DEV_RUNTIME_VERSION);
// scripts/build-runtime-local.ps1 and the checked-in package.json both stamp
// unreleased builds as "0.0.0-local.<timestamp>" / "0.0.0-dev" — there is no
// real release number to compare against, so required_runtime gates (which
// assume a real semver floor like ">=1.0.3") are meaningless here and must
// not block local testing.
const IS_LOCAL_DEV_RUNTIME = RUNTIME_VERSION.startsWith("0.0.0-");
const INSTALL_ID      = loadInstallId(CONXA_DATA_DIR);

// ─── Recovery tier ceiling ────────────────────────────────────────────────────
// The zero-token cascade (Tier 1 deterministic ladder + Tier 2 a11y/fallback) always
// runs inside run.js. Tiers 3 (LLM semantic) and 4 (vision) are agent-mediated: when the
// in-process cascade is exhausted the runtime hands a structured recovery request back to
// the MCP client (Claude), which reasons over the live DOM / screenshot and resumes with a
// corrected selector via `step_overrides`. That handoff only makes sense when an agent is
// actually in the loop.
//   • Claude / MCP execution  → ceiling 4 (default): all four tiers enabled.
//   • Build Studio sandbox test → ceiling 2 (CONXA_MAX_RECOVERY_TIER=2): deterministic,
//     no agent handoff — a step that survives T1/T2 fails honestly so the compiled pack is
//     tested on its own merits.
const { clampRecoveryTier } = require("./recovery");
const MAX_RECOVERY_TIER = clampRecoveryTier(process.env.CONXA_MAX_RECOVERY_TIER, 4);
// Agent-mediated recovery (T3 semantic + T4 vision) is available only above the T2 ceiling.
const AGENT_RECOVERY_ENABLED = MAX_RECOVERY_TIER >= 3;

// PROD-3 "Strict Mode" — a skill pack may declare its own manifest.max_recovery_tier, but only
// ever to be MORE restrictive than this host: the effective ceiling for a run is always
// min(host ceiling, pack ceiling), never the reverse. A pack with no declared ceiling (older
// packs, or one that doesn't opt in) runs at the host's own ceiling, unchanged. Used only where a
// SPECIFIC run's behavior is decided (override application, park/resume, failure-response
// messaging) — status/registration endpoints intentionally keep reporting the host baseline
// (MAX_RECOVERY_TIER/AGENT_RECOVERY_ENABLED), since they answer "what can this host do," not
// "what was this one run capped at."
function _effectiveRecoveryTier(manifest) {
  const declared = manifest && manifest.max_recovery_tier;
  const tier = Number.isFinite(declared)
    ? Math.min(MAX_RECOVERY_TIER, clampRecoveryTier(declared, MAX_RECOVERY_TIER))
    : MAX_RECOVERY_TIER;
  return { maxRecoveryTier: tier, agentRecoveryEnabled: tier >= 3 };
}

// ─── Execution wall-clock budget ──────────────────────────────────────────────
// The MCP client (Claude Desktop) abandons a tools/call after its own request timeout (~240s
// observed: it sends notifications/cancelled at exactly +4 min) and then shows the user
// "No result received". Honouring that cancel (above) stops a zombie run, but the SDK drops the
// response to an already-cancelled request, so the user still sees a 4-minute hang whenever a
// single execution out-runs the client budget — e.g. a step whose target never renders (bad
// input / empty search) burns minutes accumulating bounded recovery stages before failing.
// The cure is to never let an execution reach the client's deadline: we cap total wall-clock a
// safe margin below it, abort through the same cancelCheck path, and return an actionable failure
// the client still receives. Default 210s leaves ~30s for teardown + response transport under the
// 240s client timeout. Build Studio's fast successful runs never approach this.
const EXECUTION_DEADLINE_MS = Number(process.env.CONXA_EXECUTION_DEADLINE_MS) || 210000;

// When sign-in is missing, execute_skill does NOT wait for the human inside the MCP request (which
// the client abandons after 60-240s, far shorter than an MFA/SSO/CAPTCHA login can take). It returns
// at once and the run waits in the background — see _detachUntilSignedIn. Each sign-in tab has its
// own 10-minute deadline (browser.js LOGIN_WAIT_MS) plus one reopen; this is the outer bound on the
// background wait for all of them.
const AUTH_DETACH_MAX_WAIT_MS = 25 * 60 * 1000;

// ─── Parked recovery page (Tier 3/4 cross-call self-healing) ──────────────────
// State + page-fingerprint helpers live in recovery_park.js; see that file's
// header for why the failed page is parked instead of torn down.
const { PARK_DIVERGENCE_TOLERANCE, capturePageFingerprint, parkKey } = require("./recovery_park");
const _getParkedRecovery    = require("./recovery_park").getParked;
const _setParkedRecovery    = require("./recovery_park").setParked;
const _discardParkImpl      = require("./recovery_park").discardPark;
const _discardAllParksImpl  = require("./recovery_park").discardAllParks;
// Per-skill round state for agent-mediated recovery (round count + the stagnation
// cap) plus the candidate_index nomination maps published by each digest. Every
// round is armed; there is no tier escalation. See recovery_stage.js.
const recoveryStage = require("./recovery_stage");
const PARK_TTL_MS = Number(process.env.CONXA_RECOVERY_PARK_TTL_MS) || 180000;
// RT-3: parks are keyed per skill (`${workspace_id}:${slug}`), not a single process-wide slot —
// discarding one run's park must never touch a sibling run's parked recovery window.
// Releases the park's browser lease and host lock (if it held either — see browser.js's
// getCachedBrowser/releaseCachedBrowser and host_lock.js) after tearing the park down. Without
// this, a discarded headless park's browser would stay leased forever (nothing left to release
// it), and — worse — a discarded park's host lock would stay held forever, permanently blocking
// every future run that needs the same platform.
function _discardPark(key, reason) {
  const park = _getParkedRecovery(key);
  const leaseKey = park && park.leaseKey;
  const hostRelease = park && park.hostRelease;
  return _discardParkImpl(key, reason, log).then(() => {
    if (leaseKey) releaseCachedBrowser(leaseKey);
    if (hostRelease) hostRelease();
  });
}

// ─── Multi-run registry (RT-3) ─────────────────────────────────────────────────
// Replaces the old single `activeExecution` slot — each concurrent execute_skill/
// execute_sequence call gets its own tracked run, admitted up to CONXA_MAX_CONCURRENT_RUNS.
const runRegistry = require("./run_registry");
// Per-external-host mutual exclusion (RT-3 follow-up) — two runs touching the SAME external
// platform serialize against each other; runs on different platforms stay fully parallel.
const hostLock = require("./host_lock");

// ─── 2. Playwright browser path (MUST precede any playwright require) ─────────
// Respect a caller-supplied PLAYWRIGHT_BROWSERS_PATH (e.g. dev mode where CONXA_DIR
// points to a data dir that has no chromium/ subfolder).
if (!process.env.PLAYWRIGHT_BROWSERS_PATH) {
  const primaryChromium = path.join(CONXA_DIR, "chromium");
  // The NSIS installer historically places Chromium under AppData\Local\Conxa\chromium
  // (independent of CONXA_DIR).  Fall back to that location on Windows when the
  // primary path doesn't exist — avoids "Executable doesn't exist" on existing installs.
  const winInstallerChromium = process.platform === "win32"
    ? path.join(os.homedir(), "AppData", "Local", "Conxa", "chromium")
    : null;
  // Last resort: Playwright's own default cache (where `npx playwright install` puts it), so a
  // machine that already has Chromium there doesn't fail with "Executable doesn't exist" just
  // because ~/.conxa/chromium was never created. Deliberately no auto-download here: clients that
  // borrow their own browser (Conxa Execute) never need Chromium at all.
  const playwrightDefault = process.platform === "win32" && process.env.LOCALAPPDATA
    ? path.join(process.env.LOCALAPPDATA, "ms-playwright")
    : null;
  process.env.PLAYWRIGHT_BROWSERS_PATH =
    fs.existsSync(primaryChromium) ? primaryChromium
      : (winInstallerChromium && fs.existsSync(winInstallerChromium)) ? winInstallerChromium
      : (playwrightDefault && fs.existsSync(playwrightDefault)) ? playwrightDefault
      : primaryChromium;
}
// Pass both dirs to browser.js
process.env.CONXA_DIR      = CONXA_DIR;
process.env.CONXA_DATA_DIR = CONXA_DATA_DIR;

// ─── 3. Handle CLI flags (--install-playwright, --handle-auth-callback, etc.) ──
const [,, ...cliArgs] = process.argv;
// Fast selfcheck used by the update bat script to verify the new runtime boots cleanly.
if (cliArgs.includes("--selfcheck")) {
  process.exit(0);
}

// ─── 2b. Chromium revision preflight ──────────────────────────────────────────
// If a .revision marker exists but the corresponding chromium-<rev> directory is
// missing (e.g. a second installer brought a new runtime.exe with a different
// Playwright, but the installer skip-guard left the old revision on disk), spawn
// --install-playwright in the background to self-heal before the first skill run.
// Skip this check when we're already running --install-playwright to avoid
// spawning a duplicate install process.
if (!cliArgs.includes("--install-playwright")) {
  const _chromiumBase = path.join(CONXA_DIR, "chromium");
  const _revFile = path.join(_chromiumBase, ".revision");
  if (fs.existsSync(_revFile)) {
    const _expectedRev = fs.readFileSync(_revFile, "utf8").trim();
    if (_expectedRev && !fs.existsSync(path.join(_chromiumBase, _expectedRev))) {
      const { spawn } = require("child_process");
      const _child = spawn(process.execPath, ["--install-playwright"], {
        env: { ...process.env, CONXA_DIR, PLAYWRIGHT_BROWSERS_PATH: _chromiumBase },
        stdio: "ignore",
        detached: true,
      });
      _child.unref();
      process.stderr.write(JSON.stringify({ ts: new Date().toISOString(), level: "warn",
        msg: "chromium_revision_mismatch", expected: _expectedRev, action: "reinstalling_background" }) + "\n");
    }
  }
}

if (cliArgs.includes("--install-playwright")) {
  // Extracted verbatim to cli_installer.js — runs and exits the process.
  require("./cli_installer").runInstallPlaywright(CONXA_DIR);
  return; // don't fall through into logger / MCP server setup while install is pending
}

// ─── 4. Logger ────────────────────────────────────────────────────────────────
function log(level, msg, extra = {}) {
  const line = JSON.stringify({ ts: new Date().toISOString(), level, msg, ...extra }) + "\n";
  try {
    fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
    if (fs.existsSync(LOG_FILE) && fs.statSync(LOG_FILE).size > 10 * 1024 * 1024)
      fs.renameSync(LOG_FILE, LOG_FILE + ".1");
    fs.appendFileSync(LOG_FILE, line);
  } catch (_) {}
  process.stderr.write(line);
}

log("info", "runtime_bootstrap", {
  version: RUNTIME_VERSION,
  conxa_dir: CONXA_DIR,
  conxa_data_dir: CONXA_DATA_DIR,
  skill_packs_dir: SKILL_PACKS_DIR,
  cache_dir: CACHE_DIR,
  log_file: LOG_FILE,
});

process.on("uncaughtException",  (e) => log("error", "uncaught", { error: e.message, stack: e.stack }));
process.on("unhandledRejection", (r) => log("error", "unhandled_rejection", { reason: String(r) }));

// ─── 5. Lazy requires (after env setup) ──────────────────────────────────────
let Server;
let StdioServerTransport;
let CallToolRequestSchema;
let ListToolsRequestSchema;
let skillLoader;
let sync;
let authManager;
let runPlan;
let enrichStepsWithRecovery;
let applyStepOverrides;
let appendRecoveryEvent;
let clearRetryBudget;
let checkRetryBudget;
let isAuthFailure;
let stepAssertions;
let frameScopedInventory;
let uniqueDownloadName;
let sweepOldRuns;
let extractZipOnce;
let getCachedBrowser;
let getAuthContext;
let awaitAuthPending;
let describeAuthWait;
let authAppStatus;
let releaseCachedBrowser;
let teardownExecBrowser;
let captureReAuth;
let gracefulShutdown;
let resolveGroup;
let resolveTargetHosts;
let createTracker;
let mapErrorToCode;
let drainSpill;
let closeExtraTabs;
let policyGate;

try {
  ({ Server }               = hostBridge.hostRequire("@modelcontextprotocol/sdk/server/index.js"));
  ({ StdioServerTransport } = hostBridge.hostRequire("@modelcontextprotocol/sdk/server/stdio.js"));
  ({ CallToolRequestSchema, ListToolsRequestSchema } = hostBridge.hostRequire("@modelcontextprotocol/sdk/types.js"));

  skillLoader  = require("./skill_loader");
  sync         = require("./sync");
  authManager  = require("./auth_manager");
  ({ runPlan, enrichStepsWithRecovery, applyStepOverrides, appendRecoveryEvent, clearRetryBudget, checkRetryBudget, isAuthFailure, stepAssertions, frameScopedInventory, uniqueDownloadName, sweepOldRuns, extractZipOnce } = require("./run"));
  ({ getCachedBrowser, releaseCachedBrowser, teardownExecBrowser, captureReAuth, gracefulShutdown,
     getAuthContext, awaitAuthPending, describeAuthWait, authAppStatus,
     _resolveGroup: resolveGroup } = require("./browser"));
  ({ resolveTargetHosts } = require("./target_hosts"));
  ({ createTracker, mapErrorToCode, drainSpill } = require("./tracker"));
  ({ closeExtraTabs } = require("./tabs"));
  policyGate = require("./policy_gate");
} catch (e) {
  log("error", "runtime_bootstrap_failed", { error: e.message, stack: e.stack });
  process.exit(1);
}

// ─── 6. Execution state ────────────────────────────────────────────────────────
// Per-run state lives in runRegistry (RT-3) — no process-wide "the current execution" slot.

// Tracks whether the cold-start sync is complete so execute_skill can gate on it.
const syncState = {
  startedAt:  Date.now(),
  complete:   false,
  skillsDone: false,
  appDone:    false,
};

// ─── 7. Skill index ───────────────────────────────────────────────────────────
let skillIndex = {};
try {
  skillIndex = skillLoader.loadSkillRegistryFromCache(SKILL_PACKS_DIR, CACHE_DIR);
  log("info", "skill_index_loaded", { count: Object.keys(skillIndex).length });
} catch (e) {
  log("warn", "skill_index_load_failed", { error: e.message });
}

// ─── 8. MCP server ────────────────────────────────────────────────────────────
const server = new Server(
  { name: "conxa", version: RUNTIME_VERSION },
  { capabilities: { tools: { listChanged: true } }, instructions: require("./tool_defs").SERVER_INSTRUCTIONS }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: _toolDefinitions() }));

server.setRequestHandler(CallToolRequestSchema, async (req, extra) => {
  const { name, arguments: args } = req.params;
  log("info", "tool_call", { tool: name });
  try {
    return await _handleTool(name, args || {}, extra);
  } catch (e) {
    log("error", "tool_error", { tool: name, error: e.message, stack: e.stack });
    const { summary, detail } = plainCause(e);
    return { content: [{ type: "text", text: `Internal error: ${summary} (${detail})` }], isError: true };
  }
});

// ─── 9. Runtime self-update — unified signed manifest ────────────────────────
// One Ed25519-signed manifest.json (see runtime/manifest_manager.js) replaces the old
// two-endpoint _checkHostUpdate()/_checkAppUpdate() cold-start checks. Both host and
// app updates land in their own versioned directory (conxa-runtime/<version>/,
// conxa-app/<version>/) with a `current` junction flipped by version_manager.js —
// there is no .bak/.next staging or update.bat dance anymore.
//
// The conxa_app leg now runs pre-load, in bootstrap.js, before server.js is ever
// require()'d — so a pushed app update takes effect THIS launch instead of the next
// one. Only the conxa_runtime leg still runs here: this process IS the running host
// exe, so it can never replace itself — that update can only ever take effect on the
// next cold start, same as before.
const CONXA_API = process.env.CONXA_API_URL || "https://apis.conxa.in";
const manifestManager = require("./manifest_manager");
const { shouldGate, recordLaunch } = require("./update_gate");

async function _checkForUpdates() {
  if (process.env.CONXA_SKIP_SELF_UPDATE === "1") return;
  await manifestManager.checkForUpdates({
    apiUrl: CONXA_API,
    conxaDir: CONXA_DIR,
    installId: INSTALL_ID,
    // Pull only this install's update channel — dev installs never see stable, and
    // deployed prod installs never see dev prereleases. Defaults to stable.
    channel: process.env.CONXA_UPDATE_CHANNEL || "stable",
    publicKeyB64: hostBridge.manifestPublicKey(),
    components: ["conxa_runtime"],
    // bootstrap.js's pre-load app-update check already fetched the manifest once this
    // launch (see global.__conxaManifest) — reuse it instead of fetching again.
    manifest: hostBridge.preloadedManifest(),
    log,
  });
}

// ─── 10. Connect MCP immediately ─────────────────────────────────────────────
const transport = new StdioServerTransport();
server.connect(transport);
log("info", "mcp_connected", { version: RUNTIME_VERSION, conxa_dir: CONXA_DIR,
  max_recovery_tier: MAX_RECOVERY_TIER, agent_recovery: AGENT_RECOVERY_ENABLED });

// ─── 11. Async post-connect tasks ────────────────────────────────────────────
// execute_skill awaits this promise before running so it always sees fresh data.
// The promise always resolves (failures caught internally) — never hangs.
// Host version the `current` junction points at when it is newer than the exe running this
// process — an update was activated on disk but only a restart of whatever launched us picks
// it up. Null for local dev builds (0.0.0-* never matches a release) and for a rolled-back
// `current`. Armed once after startupSync so the answer can't change mid-session.
let updateGate = { staged: null, gated: false };
function _stagedHostVersion() {
  if (IS_LOCAL_DEV_RUNTIME) return null;
  try {
    const staged = hostBridge.versionManager().currentVersion(path.join(CONXA_DIR, "conxa-runtime"));
    return staged && semver.gt(semver.coerce(staged) || "0.0.0", semver.coerce(RUNTIME_VERSION) || "0.0.0") ? staged : null;
  } catch (_) { return null; }
}
function _armUpdateGate() {
  const staged = _stagedHostVersion();
  const strikes = recordLaunch(CONXA_DATA_DIR, staged);
  const gated = shouldGate({
    running: RUNTIME_VERSION, staged, strikes,
    skip: process.env.CONXA_SKIP_UPDATE_GATE === "1", isLocalDev: IS_LOCAL_DEV_RUNTIME,
    gt: (a, b) => semver.gt(semver.coerce(a) || "0.0.0", semver.coerce(b) || "0.0.0"),
  });
  updateGate = { staged, gated };
  if (staged) log("info", gated ? "update_gate_armed" : "update_gate_disarmed", { running: RUNTIME_VERSION, staged, strikes });
}

const startupSync = (async () => {
  try {
    await Promise.all([
      sync.syncSkillPacks(SKILL_PACKS_DIR, { timeoutMs: 4000, log: (m) => log("info", m) })
        .then(()  => { syncState.skillsDone = true; })
        .catch(() => { syncState.skillsDone = true; }),

      // One manifest fetch decides both host and app updates now, instead of two
      // separate unsigned endpoint checks. App-layer activation only affects the
      // *next* cold start (this process already has server.js in its module cache),
      // so it never blocks the execution gate any more than the old check did.
      _checkForUpdates()
        .then(()  => { syncState.appDone = true; })
        .catch(e  => { log("warn", "update_check_skipped", { reason: e.message }); syncState.appDone = true; }),

      // Re-encrypt any plaintext session files left by prior keytar failures (SG-11).
      authManager.reencryptPlaintextSessions(SESSIONS_DIR, authManager.getSessionKey, log)
        .catch(e => { log("warn", "plaintext_reencryption_sweep_failed", { reason: e.message }); }),

      // PROD-18: retry any evidence batches a prior process couldn't deliver (network down,
      // process killed mid-POST) before this process's own runs add fresh traffic.
      drainSpill((lvl, msg, extra) => log(lvl, msg, extra))
        .catch(e => { log("warn", "telemetry_spill_drain_failed", { reason: e.message }); }),
    ]);
  } finally {
    try { _armUpdateGate(); } catch (e) { log("warn", "update_gate_arm_failed", { reason: e.message }); }
    syncState.complete = true;
    skillIndex = skillLoader.loadSkillRegistry(SKILL_PACKS_DIR, CACHE_DIR);
    log("info", "sync_complete", { count: Object.keys(skillIndex).length });
    server.sendToolListChanged().catch(() => {});
  }

  _phonehome().catch(() => {});
})();

// ─── 12. Graceful shutdown ────────────────────────────────────────────────────
// RT-3: discard every parked recovery page (potentially one per skill now, not just one) before
// closing browsers, so a watch-mode park's own browser/context is closed cleanly rather than
// left for process exit to abandon.
async function _shutdown() {
  await _discardAllParksImpl(log).catch(() => {});
  await gracefulShutdown();
}
process.on("SIGINT",  () => _shutdown());
process.on("SIGTERM", () => _shutdown());
// Windows callers (e.g. Build Studio's test harness) can't deliver SIGTERM — Popen.terminate()
// there is an unconditional TerminateProcess with no signal handler run. They close stdin first
// instead; react to that the same way so browser.close() still runs before the process exits.
process.stdin.on("end", () => _shutdown());

// Reads a skill's declared input fields (inputs.json, falling back to legacy input.json).
// Shared by the tool-definition builder, get_skill_inputs, and the execute_skill input gate
// so all three agree on the same declared schema.
function _readDeclaredInputs(entry) {
  const inputsPath = path.join(entry.skillDir, "inputs.json");
  const legacyPath = path.join(entry.skillDir, "input.json");
  try {
    const src = fs.existsSync(inputsPath) ? inputsPath : (fs.existsSync(legacyPath) ? legacyPath : null);
    if (src) {
      const raw = JSON.parse(fs.readFileSync(src, "utf8"));
      if (Array.isArray(raw.inputs)) return raw.inputs;
    }
  } catch (_) {}
  return [];
}

// ─── Skill-specific tool definitions (generated from loaded skill index) ─────
// One tool per installed skill so Claude can match intent directly without
// a discovery round-trip. Tool names: skill_{company}_{slug_underscored}.
function _skillToolDefinitions() {
  const tools = [];
  for (const entry of Object.values(skillIndex)) {
    const inputFields = _readDeclaredInputs(entry);

    const properties = {};
    const required = [];
    for (const f of inputFields) {
      const prop = { type: f.type || "string", description: f.description || f.name };
      // A select input's declared choices reach the agent as an enum so it can't pass an
      // out-of-range value that nothing would otherwise reject until execution (audit H-6).
      if (Array.isArray(f.enum) && f.enum.length) prop.enum = f.enum;
      // A multiselect (checkbox-group) input declares `type: "array"` with an enum'd `items`
      // instead of a top-level enum — see skill_package_builder_saved_skill.py's
      // _normalize_saved_skill_inputs. Forward it the same way so the agent knows the value must
      // be a list of these exact strings, not a single one.
      if (f.items) prop.items = f.items;
      // A date input is sent in a normal date form and converted at run time into the format the
      // target application itself accepted (handlers.js date_pick) — tell the agent which form.
      if (f.format === "date") {
        prop.format = "date";
        prop.description = `${prop.description} (date, e.g. 2026-09-25)`;
      }
      properties[f.name] = prop;
      // A field with a default is effectively optional to the calling agent — the execute
      // gate below fills it in from f.default when omitted, so don't advertise it as
      // mandatory (audit H-6). Same for a field the editor explicitly marked optional.
      const hasDefault = f.default !== undefined && f.default !== null && String(f.default).trim() !== "";
      if (!hasDefault && !f.optional) required.push(f.name);
    }

    const needsStr = required.length ? ` Needs: ${required.join(", ")}.` : "";
    tools.push({
      name: `skill_${entry.workspace_id}_${entry.slug.replace(/-/g, "_")}`,
      description: `Conxa: ${entry.manifest.name || entry.slug} on ${entry.workspace_id}. ${entry.manifest.description || ""}${needsStr} Runs one record per call; for a list/spreadsheet, call once per row.`,
      inputSchema: { type: "object", properties, required },
    });
  }
  return tools;
}

// ─── Tool definitions ─────────────────────────────────────────────────────────
// Static core-tool definitions live in tool_defs.js; skill-specific tools are
// generated per-install from the loaded skill index below.
function _toolDefinitions() {
  return [...require("./tool_defs").CORE_TOOL_DEFS, ..._skillToolDefinitions()];
}

// ─── Resolve skill from index ─────────────────────────────────────────────────
function _resolveSkill(skillSlug, workspace_id) {
  if (!skillSlug) return null;
  const normalSlug = skillSlug.replace(/-/g, "_");

  // Exact match
  if (workspace_id) {
    const entry = skillIndex[`${workspace_id}:${skillSlug}`];
    if (entry) return entry;
    // Try underscore/dash normalization
    for (const v of Object.values(skillIndex)) {
      if (v.workspace_id === workspace_id && v.slug.replace(/-/g, "_") === normalSlug) return v;
    }
    // A workspace_id was given but nothing in it matches — do NOT fall through to a slug-only
    // search across every other workspace below. Running a same-named skill from the wrong
    // workspace is worse than a clean "not found" (wrong company's pack.json, wrong sync
    // token, wrong tracking target).
    return null;
  }

  // Slug-only match across all workspaces
  for (const v of Object.values(skillIndex)) {
    if (v.slug === skillSlug || v.slug.replace(/-/g, "_") === normalSlug) return v;
  }
  return null;
}

// ─── Sync status per workspace ──────────────────────────────────────────────────
function _syncStatus(pack) {
  if (!pack.last_synced) return "unknown";
  return (Date.now() - new Date(pack.last_synced).getTime()) < 3600000 ? "current" : "stale";
}

function _trackingStatus(pack) {
  const tracking = (pack && pack.tracking) || {};
  return {
    enabled: tracking.enabled === true,
    tracking_url_present: Boolean(tracking.tracking_url),
    tracking_token_present: Boolean(tracking.tracking_token),
    company_id: tracking.company_id || "",
    workspace_id: pack?.published?.workspace_id || pack?.workspace_id || "",
    sync_endpoint: pack?.sync_endpoint || "",
  };
}



// ─── Concurrency cap message (RT-3) ────────────────────────────────────────────
// Shared by the fast pre-resolve refusal and the atomic admission check at exec-creation —
// same wording either way, and it deliberately never suggests calling cancel_execution: a
// caller past the cap didn't start the runs in the way, and cancelling a sibling's run to make
// room for its own is exactly the wrong move.
function _tooManyRunsMessage() {
  const active = runRegistry.list();
  const named = active.map((r) => `${r.skill} (run_id: ${r.run_id})`).join(", ");
  return `Too many workflows are already running (${active.length}/${runRegistry.MAX_CONCURRENT_RUNS}): ` +
    `${named}. Call get_execution_status to check progress, or wait for one to finish before starting another.`;
}

// ─── Build failure response ───────────────────────────────────────────────────
// Payload assembly lives in failure_response.js (extracted verbatim so the
// Tier 3/4 prompt text is unit-testable without loading this server). This
// wrapper binds the late-assigned module state (appendRecoveryEvent,
// stepAssertions, frameScopedInventory are set inside the SDK try block above)
// at call time. `exec` is the calling run's own registry entry (RT-3) — never
// a shared/global execution slot — so its sentVisualRefs set is scoped to
// this run alone.
async function _buildFailureResponse(page, errObj, resolvedEntry, runTracker, steps = null, exec = null) {
  const { maxRecoveryTier, agentRecoveryEnabled } = _effectiveRecoveryTier(resolvedEntry && resolvedEntry.manifest);
  return require("./failure_response").buildFailureResponse(
    page, errObj, resolvedEntry, runTracker, steps,
    {
      agentRecoveryEnabled,
      maxRecoveryTier,
      sentVisualRefs: exec && exec.sentVisualRefs,
      appendRecoveryEvent,
      stepAssertions,
      frameScopedInventory,
      // Lets _resolveVisualRef reach the synced content-addressed artifact store for the
      // recording-time reference image (see failure_response.js) — without this the
      // step's `visual_ref` can never resolve and the armed round loses its ground-truth image.
      skillPacksDir: SKILL_PACKS_DIR,
      // BUILD-26 stage (a1): lets the Studio-ceiling branch file failure evidence under this
      // run's own workspace — `exec` already carries both, no new plumbing.
      dataDir: CONXA_DATA_DIR,
      runId: exec && exec.runId,
      runStartTs: exec && exec.startedAt,
    }
  );
}

// EXEC-13: ai_review pause/resume — a sibling to the Tier 3/4 recovery machinery above, not
// part of it (see review_pause.js's header). Kept as its own module/requires rather than folded
// into failure_response.js because a review is a planned pause, not a failure, and must never
// touch recovery_stage.js's escalation state or retry_budget.js's retry-budget map.
const {
  checkReviewRetry,
  clearReviewRetry,
  validateReviewAnswer,
  buildReviewRequest,
  REVIEW_RETRY_MAX,
} = require("./review_pause");

function _buildReviewRequest(page, step, stepIndex, resolvedEntry, opts) {
  return buildReviewRequest(page, step, stepIndex, resolvedEntry, { skillPacksDir: SKILL_PACKS_DIR, ...opts });
}

// EXEC-21 (hand-over shape): the human sibling of ai_review above, same reasoning for staying
// its own module — a hand-over is a planned pause, not a failure, and must never touch
// recovery_stage.js's escalation state or retry_budget.js's map. See handover.js's header for
// why its park differs from ai_review's (host lock released, no fixed TTL, no closeExtraTabs).
const handoverMod = require("./handover");
const HANDOVER_PARK_TTL_MS = handoverMod.HANDOVER_PARK_TTL_MS;

// ─── Scheduler store (PROD-5) ─────────────────────────────────────────────────
// Lazy — only the schedule tools touch it. Inputs are encrypted at rest under a
// dedicated machine keychain entry; list_schedules surfaces metadata ONLY (never
// decrypted input values) so chat transcripts can't echo them back.
let _schedulerStoreInst = null;
function _schedulerStore() {
  if (!_schedulerStoreInst) {
    _schedulerStoreInst = require("./scheduler_store").createStore({
      dataDir: CONXA_DATA_DIR,
      getSessionKeyFn: () => authManager.getSessionKey("scheduler-v1", log),
      log,
    });
  }
  return _schedulerStoreInst;
}

// ─── Tool handler ─────────────────────────────────────────────────────────────
// ─── Detached sign-in wait ────────────────────────────────────────────────────
// A run that needs a human to sign in does not hold its execute_skill call open: it returns at once
// (so no MCP client ever sees a hung request or a timeout mid-MFA), and this task waits for the
// sign-in tabs in the background, then re-issues the SAME request. That second call finds the warm,
// already-signed-in browser session the login parked (browser.js) — same Chromium, same context —
// and executes exactly like any run, so nothing about execution changes; only who waits does.
function _joinNames(names) {
  if (names.length <= 1) return names[0] || "the application";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
function _awaitingAuthMessage(names, runId, alreadyOpen) {
  const who = _joinNames(names);
  return (alreadyOpen
      ? `Waiting for authentication to complete in the browser — finish signing in to ${who}.`
      : `Authentication required — please sign in to ${who} in the browser window that just opened.`) +
    ` Each sign-in tab closes by itself once you are signed in, and the workflow then starts on its own — do not run it again.` +
    ` Check progress with get_execution_status (run_id: ${runId}).`;
}
const _textOf = (res) => ((res && res.content) || []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
// Success is the "Done." prefix (Build Studio's Run Test reads it the same way) — err() is just text().
const _resultStatus = (t) => (/^Done\./.test(t) ? "completed" : /^Execution cancelled/.test(t) ? "cancelled" : "failed");

// -> { runId, names, joined }. `joined` when an identical request was already waiting: the caller
// is pointed at that run instead of queueing a duplicate that would fire twice on sign-in.
function _detachUntilSignedIn({ runId, toolName, args, skill, workspaceId, auth }) {
  const apps = Array.isArray(auth.apps)
    ? auth.apps.map((a) => ({ id: a.id, name: a.name, key: a.key }))
    : [{ id: auth.key, name: workspaceId, key: auth.key }];
  const names = apps.map((a) => a.name);
  const fingerprint = JSON.stringify(args, (k, v) => (k.startsWith("_") ? undefined : v));
  const dup = runRegistry.findAwaiting(fingerprint);
  if (dup) return { runId: dup.run_id, names: dup.apps.map((a) => a.app), joined: true };

  runRegistry.beginAwaitingAuth({ run_id: runId, skill, workspace_id: workspaceId, apps, statusOf: authAppStatus, fingerprint });
  log("info", "auth_detached", { run_id: runId, skill, apps: names });
  (async () => {
    try {
      const waited = await awaitAuthPending(auth, { timeoutMs: AUTH_DETACH_MAX_WAIT_MS });
      if (runRegistry.isAuthCancelled(runId)) {
        runRegistry.recordResult(runId, { skill, status: "cancelled", summary: "Cancelled while waiting for sign-in." });
      } else if (!waited.every((w) => w.outcome === "captured")) {
        // describeAuthWait names the app(s) and how to get a fresh sign-in tab.
        runRegistry.recordResult(runId, { skill, status: "failed",
          summary: `Authentication failed or was cancelled. ${describeAuthWait(waited)}` });
      } else {
        log("info", "auth_resumed", { run_id: runId, skill });
        const t = _textOf(await _handleTool(toolName, { ...args, _run_id: runId, _after_auth: true }));
        runRegistry.recordResult(runId, { skill, status: _resultStatus(t), summary: t });
      }
    } catch (e) {
      runRegistry.recordResult(runId, { skill, status: "failed", summary: `The workflow could not start: ${e.message}` });
    } finally {
      runRegistry.endAwaitingAuth(runId);
    }
  })();
  return { runId, names, joined: false };
}

async function _handleTool(name, args, extra) {
  const text = (t) => ({ content: [{ type: "text", text: t }] });
  const err  = (t) => ({ content: [{ type: "text", text: t }], isError: true });

  // ── list_skills ──────────────────────────────────────────────────────────────
  if (name === "list_skills") {
    const filterWorkspace = args.workspace_id ? String(args.workspace_id) : null;
    const skills = Object.values(skillIndex)
      .filter(s => !filterWorkspace || s.workspace_id === filterWorkspace)
      .map(s => ({
        skill:           s.slug,
        workspace_id:         s.workspace_id,
        name:            s.manifest.name || s.slug,
        description:     s.manifest.description || "",
        inputs_required: s.manifest.inputs_required || [],
        sync_status:     _syncStatus(s.pack),
        version:         s.manifest.version || "1.0.0",
      }));
    return text(JSON.stringify({ skills, total: skills.length }));
  }

  // ── get_skill_inputs ─────────────────────────────────────────────────────────
  if (name === "get_skill_inputs") {
    const entry = _resolveSkill(String(args.skill || ""), args.workspace_id ? String(args.workspace_id) : null);
    if (!entry) return err(`Skill not found: ${args.skill}. Call list_skills first.`);
    const inputsPath = path.join(entry.skillDir, "inputs.json");
    // Fall back to legacy input.json
    const legacyPath = path.join(entry.skillDir, "input.json");
    const schema = fs.existsSync(inputsPath)
      ? JSON.parse(fs.readFileSync(inputsPath, "utf8"))
      : (fs.existsSync(legacyPath) ? JSON.parse(fs.readFileSync(legacyPath, "utf8")) : {});
    return text(JSON.stringify(schema));
  }

  // ── cancel_execution ─────────────────────────────────────────────────────────
  // RT-3: with multiple runs possibly active at once, a bare call (no run_id) is only ever
  // unambiguous when exactly one run is active — cancelling "the" run is otherwise a guess that
  // could kill a sibling chat's execution instead of the caller's own. An unknown run_id is
  // reported rather than silently ignored, so a stale id from an earlier response doesn't look
  // like a successful cancel.
  if (name === "cancel_execution") {
    const requestedId = args.run_id ? String(args.run_id) : null;
    const active = runRegistry.list();

    if (requestedId) {
      const ok = runRegistry.requestCancel(requestedId);
      return text(JSON.stringify(ok
        ? { cancelled: true, run_id: requestedId }
        : { cancelled: false, reason: "no active run with that run_id", run_id: requestedId, active_runs: active }));
    }

    if (active.length === 0) return text('{"cancelled":false,"reason":"no active execution"}');
    if (active.length === 1) {
      runRegistry.requestCancel(active[0].run_id);
      return text(JSON.stringify({ cancelled: true, run_id: active[0].run_id }));
    }
    return text(JSON.stringify({
      cancelled: false,
      reason: "multiple executions are active — pass run_id to cancel a specific one",
      active_runs: active,
    }));
  }

  // ── get_execution_status ─────────────────────────────────────────────────────
  if (name === "get_execution_status") {
    if (args.run_id) {
      // One run, wherever it is: running, waiting for sign-in, or recently finished (with how it ended).
      const runId = String(args.run_id);
      return text(JSON.stringify(runRegistry.status(runId)
        || { state: "unknown", run_id: runId, reason: "no running, waiting or recently finished run with that run_id" }));
    }
    return text(JSON.stringify({
      active_runs: runRegistry.list(),
      awaiting_auth: runRegistry.listAwaitingAuth(),
      recent: runRegistry.listRecent(),
    }));
  }

  // ── authenticate ─────────────────────────────────────────────────────────────
  // Opens sign-in windows for whatever a skill needs and blocks (bounded) until the user finishes.
  // Idempotent: a second call while a window is still open just re-waits on that same window.
  if (name === "authenticate") {
    const entry = args.skill ? _resolveSkill(String(args.skill), args.workspace_id ? String(args.workspace_id) : null) : null;
    if (args.skill && !entry) return err(`Skill not found: ${args.skill}. Call list_skills first.`);
    const workspaceId = entry ? entry.workspace_id : (args.workspace_id ? String(args.workspace_id) : "");
    if (!workspaceId) return err("Pass skill (preferred) or workspace_id. Call list_skills first.");
    const waitMs = Math.min(Math.max(Number(args.wait_seconds) || 45, 1), 600) * 1000;
    const authOpts = {
      headless: false, logFn: log, authOnly: true,
      runId: `auth_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
      groupId: entry && entry.manifest && entry.manifest.group_id,
    };
    // AUTH-1: hosts the recording visited that no group app covers — never blocks, but the agent can
    // tell the user to already be signed in there instead of the run dying at that login wall.
    const _unclaimed = entry && Array.isArray(entry.manifest.unclaimed_hosts) ? entry.manifest.unclaimed_hosts : [];
    const _unclaimedNote = _unclaimed.length
      ? { unclaimed_hosts: _unclaimed, warning: `This workflow also visits ${_joinNames(_unclaimed)}, which has no sign-in set up. If it needs a login there, make sure you are already signed in — Conxa cannot check it beforehand.` }
      : {};
    const signedIn = { status: "signed_in", ..._unclaimedNote, next: "Everything needed is signed in — call execute_skill." };
    try {
      let r = await getAuthContext(workspaceId, authManager, authOpts);
      if (r.authenticated) return text(JSON.stringify(signedIn));
      if (r.refused) return err(r.message);
      const waited = await awaitAuthPending(r, { timeoutMs: waitMs });
      if (waited.every((w) => w.outcome === "captured")) {
        r = await getAuthContext(workspaceId, authManager, authOpts);
        // A captured sign-in can still carry a Prover warning (the saved session didn't check out
        // in a fresh probe) — additive field, never changes `status`, so existing callers reading
        // just that are unaffected.
        const proveWarnings = waited.map((w) => w.message).filter(Boolean);
        const warned = proveWarnings.length ? { login_warnings: proveWarnings } : {};
        // AUTH-8: a best-effort positive confirmation, kept separate from login_warnings above —
        // it is never a warning, and a miss (no name found) is silent, not an empty string here.
        const signedInAs = waited.map((w) => w.accountName).filter(Boolean);
        const named = signedInAs.length ? { signed_in_as: signedInAs } : {};
        return text(JSON.stringify(r.authenticated ? { ...signedIn, ...warned, ...named } : { status: "waiting", message: r.message }));
      }
      const failed = waited.some((w) => w.outcome === "launch_failed");
      return text(JSON.stringify({
        status: failed ? "failed" : "waiting",
        apps: waited.map((w) => ({ app: w.name, status: w.outcome === "captured" ? "signed_in" : w.outcome === "timeout" ? "waiting" : w.outcome === "launch_failed" ? "failed" : "closed" })),
        message: describeAuthWait(waited),
        next: failed ? "Fix the problem above; sign-in cannot start until it is resolved."
          : "Call authenticate again to keep waiting (raise wait_seconds if the user needs longer), or tell the user to finish signing in.",
      }));
    } catch (e) {
      return err(`Authentication failed: ${e.message}`);
    }
  }

  // ── get_runtime_status ───────────────────────────────────────────────────────
  if (name === "get_runtime_status") {
    const chromiumBase = path.join(CONXA_DIR, "chromium");
    const revFile = path.join(chromiumBase, ".revision");
    let chromiumRevision = null;
    if (fs.existsSync(revFile)) {
      chromiumRevision = fs.readFileSync(revFile, "utf8").trim() || null;
    } else if (fs.existsSync(chromiumBase)) {
      chromiumRevision = fs.readdirSync(chromiumBase).find(d => d.startsWith("chromium-")) || null;
    }

    // NOTE: the old `update_pending` field (update-pending.json) is gone — nothing
    // has written that file since the manifest-driven self-update replaced the
    // .bat/.bak update dance, so it always reported null.

    const packsMap = {};
    for (const entry of Object.values(skillIndex)) {
      const ws = entry.workspace_id;
      if (!packsMap[ws]) {
        packsMap[ws] = {
          workspace_id: ws,
          skill_pack_version: entry.pack?.skill_pack_version || "unknown",
          required_runtime: entry.pack?.required_runtime || "unknown",
          skills: [],
        };
      }
      packsMap[ws].skills.push(entry.slug);
    }

    return text(JSON.stringify({
      runtime_version: RUNTIME_VERSION,
      staged_runtime_version: updateGate.staged,
      chromium_revision: chromiumRevision,
      platform: process.platform,
      install_id: INSTALL_ID,
      conxa_dir: CONXA_DIR,
      max_recovery_tier: MAX_RECOVERY_TIER,
      agent_recovery_enabled: AGENT_RECOVERY_ENABLED,
      skill_packs: Object.values(packsMap),
    }, null, 2));
  }

  // ── create_schedule (PROD-5) ─────────────────────────────────────────────────
  // Validates slug + cron up front, stores inputs encrypted locally, and returns the
  // platforms this schedule will serialize against — so an agent can tell the user
  // that two schedules touching the same app will queue behind each other.
  if (name === "create_schedule") {
    const entry = _resolveSkill(String(args.slug || ""), args.workspace_id ? String(args.workspace_id) : null);
    if (!entry) return err(`Skill not found: ${args.slug}. Call list_skills first.`);
    let rec;
    try {
      rec = await _schedulerStore().create({
        slug: entry.slug,
        workspace_id: entry.workspace_id,
        cron: String(args.cron || ""),
        name: args.name ? String(args.name) : undefined,
        inputs: (args.inputs && typeof args.inputs === "object") ? args.inputs : {},
        catchup_grace_minutes: args.grace_minutes !== undefined ? Number(args.grace_minutes) : undefined,
      });
    } catch (e) {
      return err(`Could not create schedule: ${e.message}`);
    }
    const next = require("./cron_lite").nextAfter(rec.cron, new Date());
    await _schedulerStore().update(rec.id, { next_run_at: next ? next.toISOString() : null });
    let targetHosts = [];
    try {
      targetHosts = resolveTargetHosts([{ entry }], { resolveGroup });
    } catch (_) {}
    return text(JSON.stringify({
      created: true,
      id: rec.id,
      name: rec.name,
      skill: rec.slug,
      workspace_id: rec.workspace_id,
      cron: rec.cron,
      enabled: rec.enabled,
      next_run_at: next ? next.toISOString() : null,
      note: "Runs headlessly on this machine even when no chat app is open. Two schedules touching the same platform never run at the same time.",
      target_hosts: targetHosts,
    }, null, 2));
  }

  // ── list_schedules (PROD-5) — metadata only; input VALUES never leave the machine's store ──
  if (name === "list_schedules") {
    return text(JSON.stringify({
      schedules: _schedulerStore().listMeta(),
      hint: "Pass one of these ids to delete_schedule. Use execute_skill to run a skill right now.",
    }, null, 2));
  }

  // ── delete_schedule (PROD-5) ─────────────────────────────────────────────────
  if (name === "delete_schedule") {
    try {
      const ok = _schedulerStore().remove(String(args.id || ""));
      return text(JSON.stringify({ deleted: ok, id: String(args.id || "") }));
    } catch (e) {
      return err(`Could not delete schedule: ${e.message}`);
    }
  }

  // ── execute_skill / execute_sequence ─────────────────────────────────────────
  if (name === "execute_skill" || name === "execute_sequence") {
    let watch = args.watch !== false;
    // PROD-5: distinguish scheduled runs from chat-driven ones in logs/telemetry.
    // Only the scheduler daemon sends _trigger; anything else is chat-driven.
    const trigger = args._trigger === "scheduled" ? "scheduled" : "chat";
    const runs = name === "execute_sequence"
      ? (Array.isArray(args.skills) ? args.skills : [])
      : [{ skill: args.skill, workspace_id: args.workspace_id, inputs: args.inputs, resume_from: args.resume_from, step_overrides: args.step_overrides, review_results: args.review_results, dry_run: args.dry_run }];

    if (runs.length === 0) return err("No skills provided.");

    // Hard gate: do not execute against stale data.
    // startupSync always resolves (all failures caught internally) so this never hangs.
    // After the first call the promise is already settled — subsequent calls are instant.
    if (!syncState.complete) {
      await startupSync;
    }

    // A newer host exe is staged but this process is still the old one — refuse new runs so
    // the user restarts and actually gets it, identically in every MCP client (Execute turns
    // this same condition into its one-click dialog). Scheduled runs are exempt: their daemon
    // spawns its own exe, so a chat-host restart wouldn't help and nobody is present to act.
    if (updateGate.gated) {
      if (trigger === "scheduled") {
        log("info", "update_gate_skipped_scheduled", { staged: updateGate.staged });
      } else {
        let host = "this app";
        try { host = server.getClientVersion()?.name || host; } catch (_) {}
        return err(
          `Conxa has been updated to ${updateGate.staged}. Quit and reopen ${host} to finish — ` +
          `skills will run again as soon as it restarts.`
        );
      }
    }

    // Concurrency cap (RT-3): several runs may be genuinely active at once (separate chats, or
    // one chat firing several tool calls). Refuse honestly past the cap instead of pretending
    // only one run can ever exist — and never tell the caller to cancel a run it doesn't own.
    if (runRegistry.count() >= runRegistry.MAX_CONCURRENT_RUNS) return err(_tooManyRunsMessage());

    // Resolve all skills (fail fast)
    const resolved = [];
    let _overrideAppliedCount = 0;
    for (const run of runs) {
      let entry = _resolveSkill(String(run.skill || ""), run.workspace_id ? String(run.workspace_id) : null);
      if (!entry) return err(`Skill not found: ${run.skill}. Call list_skills.`);

      // Integrity gate — re-read manifest from disk first. Studio restages the
      // pack into the same `current` junction while this process stays alive;
      // the in-memory skillIndex checksums would otherwise be stale.
      try {
        entry = skillLoader.ensureSkillIntegrity(SKILL_PACKS_DIR, skillIndex, entry.workspace_id, entry.slug);
      } catch (integrityErr) {
        // Real mismatch (or missing files) — cloud re-sync is the last resort.
        sync.syncSkillPacks(SKILL_PACKS_DIR, { timeoutMs: 4000, log: (m) => log("info", m) })
          .then(() => { skillIndex = skillLoader.loadSkillRegistry(SKILL_PACKS_DIR, CACHE_DIR); })
          .catch(() => {});
        return err(`Skill integrity check failed: ${integrityErr.message}. A background re-sync has been triggered — please retry in a moment.`);
      }

      // Runtime compatibility
      const required = entry.manifest.required_runtime || ">=0.0.0";
      if (!IS_LOCAL_DEV_RUNTIME && !semver.satisfies(RUNTIME_VERSION, required))
        return err(`Skill ${run.skill} requires runtime ${required}, installed: ${RUNTIME_VERSION}. Please update the Conxa runtime.`);

      // Required-input gate (audit finding C2): interpolate() silently substitutes "" for a
      // missing/mis-typed input, so a required field the caller forgot would fail silently
      // deep inside a step instead of here, with a clear message. Declared defaults (H2) fill
      // in first so a default-satisfied required field is never reported missing.
      const declaredInputs = _readDeclaredInputs(entry);
      const providedInputs = (run.inputs && typeof run.inputs === "object") ? run.inputs : {};
      const effectiveInputs = {};
      for (const f of declaredInputs) {
        if (f && f.name && f.default !== undefined && f.default !== null && f.default !== "") {
          effectiveInputs[f.name] = f.default;
        }
      }
      Object.assign(effectiveInputs, providedInputs);
      const missingInputs = (entry.manifest.inputs_required || []).filter((n) => {
        const v = effectiveInputs[n];
        return v === undefined || v === null || String(v).trim() === "";
      });
      if (missingInputs.length) {
        return err(`Skill ${run.skill} is missing required input(s): ${missingInputs.join(", ")}. Call get_skill_inputs first.`);
      }
      run.inputs = effectiveInputs;

      const execPath = path.join(entry.skillDir, "execution.json");
      const recPath  = path.join(entry.skillDir, "recovery.json");
      let rawExec, rawRec;
      try {
        // The compiler always writes a bare JSON array (skill_package_builder_output.py) —
        // no object wrapper, no execution_plan key. A missing or unparseable file here used to
        // become an EMPTY step array, which then ran and reported "Done." having done nothing —
        // the single most misleading outcome this tool can produce. Fail loudly instead.
        if (!fs.existsSync(execPath)) throw new Error("execution.json is missing from this skill's files");
        rawExec = JSON.parse(fs.readFileSync(execPath, "utf8"));
        if (!Array.isArray(rawExec)) throw new Error("execution.json is not the expected step-list shape");
        rawRec = fs.existsSync(recPath) ? JSON.parse(fs.readFileSync(recPath, "utf8")) : null;
      } catch (e) {
        return err(`Skill ${run.skill}'s files are damaged (${e.message}) — sync or republish this skill.`);
      }
      const enriched = enrichStepsWithRecovery(rawExec, rawRec);
      // Apply agent-recovery selector overrides (Tier 3/4 closing edge). Only honoured when
      // agent recovery is enabled (ceiling ≥ 3) — in a deterministic Studio test (ceiling 2)
      // a stray override must not silently rewrite the pack under test.
      // candidate_index nominations resolve against the ranked digest map published by the
      // most recent Tier 3/4 failure response for this skill; an unknown/stale index is
      // reported and skipped (the step then fails into a fresh digest next round).
      // PROD-3 "Strict Mode": THIS skill's own manifest ceiling, never looser than the host's.
      const { agentRecoveryEnabled: _entryAgentEnabled } = _effectiveRecoveryTier(entry.manifest);
      const _stageKey = parkKey(entry.workspace_id || "", entry.slug || "");
      const _resolveCandidateIndex = (stepIdx, candIdx) => {
        const map = _entryAgentEnabled ? recoveryStage.getCandidateMap(_stageKey) : null;
        const hit = map && map[String(candIdx)];
        if (!hit || typeof hit.selector !== "string") {
          appendRecoveryEvent({ event: "agent_override_rejected", slug: entry.slug,
            step_index: stepIdx, reason: "stale-candidate-index" });
          return null;
        }
        return hit.selector;
      };
      const steps = _entryAgentEnabled
        ? applyStepOverrides(enriched, run.step_overrides, { resolveCandidateIndex: _resolveCandidateIndex })
        : enriched;
      const overrideCount = _entryAgentEnabled && run.step_overrides && typeof run.step_overrides === "object"
        ? Object.keys(run.step_overrides).length : 0;
      if (overrideCount) {
        appendRecoveryEvent({ event: "agent_override_applied", slug: entry.slug, count: overrideCount });
        _overrideAppliedCount += overrideCount;
      }

      // `resume_from: 0` is a legitimate resume (the failed step was the very first one) and must
      // stay distinguishable from "no resume_from given" (a fresh run) — both normalize to index 0,
      // so track explicitness separately rather than testing `resumeFrom > 0` downstream.
      const isResume = Number.isInteger(run.resume_from) && run.resume_from >= 0;
      resolved.push({
        entry,
        steps,
        inputs:     (run.inputs && typeof run.inputs === "object") ? run.inputs : {},
        resumeFrom: isResume ? run.resume_from : 0,
        isResume,
        // EXEC-13: ai_review answers keyed by step index, parallel to step_overrides above but
        // never run through applyStepOverrides — an ai_review step carries no selector for
        // applyStepOverrides to inject, only a structured answer bound into `inputs`.
        reviewResults: (run.review_results && typeof run.review_results === "object") ? run.review_results : {},
        dryRun: run.dry_run === true,
      });
    }

    // RT-9: Studio-only test guard — pause (parked) before the first step the compiler marked
    // `destructive`, resume with `_guard_release`. Read from the top-level args (never from a
    // per-run entry): customer and agent callers never set these, so for them both stay false.
    const _guardPause = args._pause_before_irreversible === true;
    const _guardRelease = args._guard_release === true;

    // EXEC-21: a handover step needs a person to actually see and click something — there is no
    // window for that in a headless run. Force one open rather than deadlocking on a banner
    // nobody can see; watch was already resolved above, before we could know what's in the pack.
    if (!watch && resolved.some((r) => Array.isArray(r.steps) && r.steps.some((s) => s && s.type === "handover"))) {
      watch = true;
      log("info", "handover_forced_watch", { skills: resolved.map((r) => r.entry.slug) });
    }

    // Retry budget check on resume
    const primary = resolved[0];
    // PROD-3 "Strict Mode": every agent-mediated recovery decision below (override/resume/park)
    // is gated on the PRIMARY skill's own effective ceiling — the only skill park/resume ever
    // applies to (both are already gated on resolved.length === 1 below).
    const { maxRecoveryTier: _effMaxTier, agentRecoveryEnabled: _effAgentEnabled } =
      _effectiveRecoveryTier(primary.entry.manifest);

    if (primary.isResume && !checkRetryBudget(primary.entry.slug, primary.resumeFrom))
      return err(`Retry budget exhausted at step ${primary.resumeFrom}. Fix the root cause in execution.json before retrying from step 0.`);

    // Acquire a run slot (RT-3: one of possibly several concurrent runs, not a single process-wide
    // lock). runId is generated here — earlier than before — because the registry keys runs by it.
    // `_run_id` (internal, like `_trigger`): a run that waited for sign-in in the background resumes
    // under the id its caller was already given, so get_execution_status keeps answering for it.
    const _runId = args._run_id ? String(args._run_id) : `r_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
    // Diagnostic phase markers (test_phase log entries) — pinpoint where a slow "Run Test" click
    // actually spends its time: host-lock wait, the two sequential Chromium launches inside
    // getCachedBrowser (headless session-validate + visible exec context), and the settle/drift
    // gate before step 0 fires. Read by conxa_compile/runtime_tool.py, which relays each one to
    // Build Studio's test log in real time. Cheap (a handful of log() calls); remove only if this
    // stops being useful, not because it "looks unused" — its consumer is a text-matching regex,
    // not a caller in this file.
    const _t0 = Date.now();
    const _phase = (phase, extra) => log("info", "test_phase", { run_id: _runId, phase, ms: Date.now() - _t0, ...(extra || {}) });
    const exec = {
      runId:           _runId,
      slug:            primary.entry.slug,
      workspace_id:    primary.entry.workspace_id,
      step:            0,
      total:           resolved.reduce((n, r) => n + r.steps.length, 0),
      startedAt:       new Date().toISOString(),
      cancelRequested: false,
      deadlineAt:      Date.now() + EXECUTION_DEADLINE_MS,
      deadlineExceeded: false,
      sentVisualRefs:  new Set(), // P5: tracks which (slug:stepIndex) visual refs were sent this execution
      waitingForHost:  null, // set while blocked in host_lock.js; see run_registry.list()
      trigger, // "scheduled" (PROD-5 daemon) | "chat" — surfaced in execute_start below
    };
    // No `await` since the pre-resolve cap check above, so this can only fail here if that check
    // ever stops being the sole gate — kept as the authoritative admission rather than trusting
    // the earlier advisory check alone.
    if (!runRegistry.begin(exec)) return err(_tooManyRunsMessage());

    // Wall-clock watchdog: trips the same cancel path as a client abort once the execution budget
    // is spent, so the run stops and returns *before* the client's request timeout fires (turning a
    // silent 4-minute hang into a fast, actionable failure). Checked at every step / recovery-stage
    // boundary; all Playwright ops are individually bounded, so it is observed within seconds of
    // expiring. Distinguished from a client cancel by the deadlineExceeded flag (see catch block).
    const _execCancelled = () => {
      if (exec.cancelRequested) return true;
      if (Date.now() >= exec.deadlineAt) {
        if (!exec.deadlineExceeded) {
          exec.deadlineExceeded = true;
          log("warn", "execution_deadline_exceeded",
            { run_id: _runId, skill: primary.entry.slug, step: exec.step, deadline_ms: EXECUTION_DEADLINE_MS });
          appendRecoveryEvent({ event: "execution_deadline_exceeded", run_id: _runId, slug: primary.entry.slug, step: exec.step });
        }
        return true;
      }
      return false;
    };

    // Honour MCP protocol cancellation. The SDK aborts `extra.signal` when the client sends
    // notifications/cancelled — which a client also does when its own request times out. Without
    // this, the runtime kept executing after the client gave up, parked a browser the client can
    // never resume, and produced a response the SDK silently drops. `runPlan`'s cancelCheck reads
    // exec.cancelRequested, so flipping it here makes both the step loop and the recovery cascade
    // yield promptly and tear down cleanly. (cancel_execution sets the same flag on this same run.)
    const _abortSignal = extra && extra.signal;
    const _onAbort = () => {
      exec.cancelRequested = true;
      log("info", "execution_cancelled_by_client", { run_id: _runId, skill: primary.entry.slug });
      appendRecoveryEvent({ event: "execution_cancelled_by_client", run_id: _runId, slug: primary.entry.slug });
    };
    if (_abortSignal) {
      if (_abortSignal.aborted) _onAbort();
      else _abortSignal.addEventListener("abort", _onAbort, { once: true });
    }

    log("info", "execute_start", {
      run_id: _runId,
      tool: name,
      run_count: resolved.length,
      skill: primary.entry.slug,
      workspace_id: primary.entry.workspace_id,
      total_steps: resolved.reduce((n, r) => n + r.steps.length, 0),
      watch,
      trigger: exec.trigger,
      max_recovery_tier: _effMaxTier,
      concurrent_runs: runRegistry.count(),
      tracking: _trackingStatus(primary.entry.pack),
    });

    // Set up lightweight tracker for this execution
    const _tracker    = createTracker(primary.entry.pack?.tracking || {}, {
      runtime_version: RUNTIME_VERSION,
      workflow_id:      primary.entry.slug,
      workflow_version: primary.entry.manifest?.version || "0",
      company_id:      primary.entry.workspace_id,
      log,
    });
    // _runId was generated earlier, at admission (runRegistry.begin), not here — see above.
    const _runTracker = _tracker.forRun(_runId, { uid: INSTALL_ID, wid: "" });
    const _wfStartAt  = Date.now();
    let   _totalRecovered = 0;
    // EXEC-36/BUILD-28(a): drift/environment-mismatch warnings collected across every skill in
    // this run (execute_sequence can chain several), surfaced on both the success and failure
    // paths below — deduped so a warning that would repeat identically per-skill (a shared
    // pack-level environment mismatch) only appears once.
    const _allWarnings = [];
    // PROD-3-DRYRUN: every destructive step skipped (resolved but never acted on) across every
    // skill in this run, so a dry-run's response can name exactly what it withheld.
    const _dryRunSkipped = [];
    if (_overrideAppliedCount) _runTracker.emit("override_applied", { n: _overrideAppliedCount });

    // Signal agent-mediated recovery retry (Tier 3/4) when resuming mid-plan.
    if (primary.isResume) {
      const hasOverride = _effAgentEnabled && primary.steps[primary.resumeFrom] && primary.steps[primary.resumeFrom]._agent_override;
      _runTracker.emit("rec_start", { si: primary.resumeFrom, l: hasOverride ? 3 : 5, sc: hasOverride ? "agent_override" : "llm_intent" });
    }
    _runTracker.emit("wf_start", {});
    // PROD-18 evidence receipt: fire the flush now so the start batch is off the wing and
    // in flight before any browser work, but don't await it here — the four early-return
    // refusal paths below (recovery/review resume refused) already hand-flush the tracker
    // on their own way out, so blocking this line would double the wait on those paths.
    // Awaited (bounded 2s, not the 5s default) right before browser work begins, at the
    // `let page = null;` seam below, which every surviving path crosses exactly once.
    const _receiptFlush = _tracker.flush({ timeoutMs: 2000 });

    // Adopt a parked failed page when the agent is resuming THIS skill with an override, so the
    // corrected selector acts on the exact DOM state the recovery request was built from. Parks
    // are keyed per `${workspace_id}:${slug}` (RT-3) — a sibling run's park lives under its own
    // key and is neither read nor discarded here.
    const _resumeOverride = _effAgentEnabled && resolved.length === 1 && primary.isResume
      && primary.steps[primary.resumeFrom] && primary.steps[primary.resumeFrom]._agent_override;
    const _parkKey = parkKey(primary.entry.workspace_id, primary.entry.slug);
    let _park = null;
    const _existingPark = _getParkedRecovery(_parkKey);
    if (_resumeOverride && _existingPark) {
      try {
        _existingPark.page.url();
        // The recovery request described the page as it stood at park time. If it has since
        // navigated or its interactive-element count shifted materially (a timer, websocket
        // push, or unrelated re-render), the agent's corrected selector would be acting on a
        // page it never actually reasoned about — discard rather than silently trust it.
        const currentFp = await capturePageFingerprint(_existingPark.page);
        const parkedFp  = _existingPark.pageFingerprint;
        const diverged = !currentFp || !parkedFp
          || currentFp.url !== parkedFp.url
          || Math.abs(currentFp.interactiveCount - parkedFp.interactiveCount) > PARK_DIVERGENCE_TOLERANCE;
        if (diverged) {
          appendRecoveryEvent({ event: "recovery_park_state_mismatch", slug: primary.entry.slug, step_index: primary.resumeFrom });
        } else {
          _park = _existingPark;
        }
      } catch (_) { _park = null; }
      if (_park) { clearTimeout(_park.timer); _setParkedRecovery(_parkKey, null); }
    }

    // EXEC-32: a resume_from that names a step with a pending recovery park, but supplies no
    // step_overrides entry for it, is not resolving anything — the caller (agent) most likely
    // forgot the override. Falling through here would discard the park below and silently run
    // this skill from resumeFrom on a FRESH page: steps before resumeFrom — including navigate —
    // are skipped by design on a resume, so the step would execute against about:blank and report
    // a misleading "Element not found" as if it were a real ground-truth failure. Only refuse when
    // a park actually exists: resume_from with no override is legitimate when there is nothing
    // parked (e.g. resuming after an auth pause on the same still-open page, below) — that must
    // keep working exactly as before.
    if (_effAgentEnabled && resolved.length === 1 && primary.isResume && !_resumeOverride
      && _existingPark && primary.steps[primary.resumeFrom]) {
      appendRecoveryEvent({ event: "recovery_resume_missing_override", slug: primary.entry.slug, step_index: primary.resumeFrom });
      _runTracker.emit("wf_fail", { dur: Date.now() - _wfStartAt, fsi: primary.resumeFrom, fc: "recovery_resume_missing_override" });
      if (_abortSignal) _abortSignal.removeEventListener("abort", _onAbort);
      runRegistry.end(_runId);
      await _receiptFlush;
      await _tracker.flush();
      _tracker.destroy();
      return err(
        `Step ${primary.resumeFrom + 1} has a pending recovery request awaiting your pick, but this call ` +
        `supplied resume_from: ${primary.resumeFrom} with no step_overrides entry for it. Call execute_skill ` +
        `again for "${primary.entry.slug}" with step_overrides: { "${primary.resumeFrom}": { "candidate_index": ` +
        `<index> } } (or a selector) to resolve it, or without resume_from to restart the skill from the beginning.`
      );
    }

    // A park still sitting under THIS skill's own key that we did not adopt (state diverged, a
    // non-resume call, or an override that didn't match) is stale for this run — discard it so it
    // can neither leak a browser nor interfere with what's about to run.
    if (_getParkedRecovery(_parkKey)) await _discardPark(_parkKey, _resumeOverride ? "replaced" : "superseded");

    // An agent override with no live, state-matching park to resume on (TTL expired, page
    // crashed, or state diverged) must never silently continue mid-plan on a fresh page — the
    // override was chosen against a DOM state that no longer exists, so applying it to a
    // blank/different page could act on the wrong element. Refuse and ask the agent to restart.
    // This exits before the try/finally below that normally releases this run's slot and flushes
    // the tracker, so both must be torn down by hand here — otherwise this run would sit in
    // runRegistry forever, counting against the concurrency cap with nothing left to release it.
    if (_resumeOverride && !_park) {
      appendRecoveryEvent({ event: "recovery_resume_refused", slug: primary.entry.slug, step_index: primary.resumeFrom });
      _runTracker.emit("wf_fail", { dur: Date.now() - _wfStartAt, fsi: primary.resumeFrom, fc: "recovery_resume_refused" });
      if (_abortSignal) _abortSignal.removeEventListener("abort", _onAbort);
      runRegistry.end(_runId);
      // Let the fired-but-unawaited receipt flush (wf_start) settle first — _flushNow's own
      // in-flight guard makes a second concurrent flush() call a silent no-op, which would
      // drop this wf_fail event rather than send it.
      await _receiptFlush;
      await _tracker.flush();
      _tracker.destroy();
      return err(
        `The recovery window for step ${primary.resumeFrom + 1} has expired or the page state has changed ` +
        `since the recovery request was issued. Call execute_skill again for "${primary.entry.slug}" without ` +
        `resume_from to restart the skill from the beginning.`
      );
    }

    // EXEC-13: resuming an ai_review pause is the review sibling of the override-adoption block
    // above — same park (same `_parkKey`, same live page), but the resumed step carries a
    // structured answer instead of a corrected selector. Unlike an override, divergence and an
    // invalid answer do NOT refuse outright: a review answer is a judgment about page STATE, so
    // if the state changed (or the answer didn't parse) the right move is a bounded re-ask
    // against the current page, not a hard refusal — see review_pause.js's header.
    //
    // Deliberately NOT gated on _effAgentEnabled: CONXA_MAX_RECOVERY_TIER / the Strict Mode
    // ceiling governs escalation into the Tier 1-4 RECOVERY cascade. An ai_review step is not
    // recovery — it's an author-placed checkpoint that must be answerable by any MCP caller,
    // including the Build Studio sandbox (tier 2, no agent), which answers it itself via the
    // cloud proxy (see handlers/workflows.py::cmd_test_workflow). The matching park-creation
    // branch below (runErr.reviewPause) was already unconditional.
    const _resumeReviewStep = resolved.length === 1 && primary.isResume
      && primary.steps[primary.resumeFrom] && primary.steps[primary.resumeFrom].type === "ai_review";
    if (_resumeReviewStep) {
      const stepDef = primary.steps[primary.resumeFrom];
      const reviewPark = _getParkedRecovery(_parkKey);
      const answerKey = String(primary.resumeFrom);
      const hasAnswer = Object.prototype.hasOwnProperty.call(primary.reviewResults, answerKey);

      if (!reviewPark) {
        // TTL expired (or the process restarted) — nothing left to reason against. Tear this run
        // down by hand, exactly like the override refusal above: we exit before the try/finally
        // that normally releases the run slot and flushes the tracker.
        appendRecoveryEvent({ event: "review_resume_refused", slug: primary.entry.slug, step_index: primary.resumeFrom });
        _runTracker.emit("wf_fail", { dur: Date.now() - _wfStartAt, fsi: primary.resumeFrom, fc: "review_resume_refused" });
        if (_abortSignal) _abortSignal.removeEventListener("abort", _onAbort);
        runRegistry.end(_runId);
        await _receiptFlush; // see recovery_resume_refused above — avoid a no-op double flush
        await _tracker.flush();
        _tracker.destroy();
        return err(
          `The review window for step ${primary.resumeFrom + 1} has expired. Call execute_skill again for ` +
          `"${primary.entry.slug}" without resume_from to restart the skill from the beginning.`
        );
      }

      const currentFp = await capturePageFingerprint(reviewPark.page).catch(() => null);
      const parkedFp  = reviewPark.pageFingerprint;
      const diverged = !currentFp || !parkedFp
        || currentFp.url !== parkedFp.url
        || Math.abs(currentFp.interactiveCount - parkedFp.interactiveCount) > PARK_DIVERGENCE_TOLERANCE;
      const validation = hasAnswer
        ? validateReviewAnswer(primary.reviewResults[answerKey], stepDef.output_schema)
        : { valid: false, errors: ["no answer supplied for this step index"] };

      if (diverged || !validation.valid) {
        const withinBudget = checkReviewRetry(primary.entry.slug, primary.resumeFrom);
        if (!withinBudget) {
          // Bounded re-ask attempts exhausted — apply the step's on_failure policy instead of
          // looping forever.
          clearReviewRetry(primary.entry.slug, primary.resumeFrom);
          const onFailure = stepDef.on_failure || "abort";
          if (onFailure === "abort") {
            await _discardPark(_parkKey, "review_retry_exhausted");
            if (_abortSignal) _abortSignal.removeEventListener("abort", _onAbort);
            runRegistry.end(_runId);
            await _receiptFlush; // see recovery_resume_refused above — avoid a no-op double flush
            await _tracker.flush();
            _tracker.destroy();
            return err(
              `AI review at step ${primary.resumeFrom + 1} did not produce a usable answer after ` +
              `${REVIEW_RETRY_MAX} attempt(s): ${validation.errors.join("; ") || "page state kept changing"}. ` +
              `Call execute_skill again without resume_from to restart the skill from the beginning.`
            );
          }
          // use_default / continue: adopt the SAME parked page so execution resumes exactly
          // where it paused — restarting on a fresh page here would break every later step's
          // assumption about where in the app the workflow already got to.
          clearTimeout(reviewPark.timer);
          _setParkedRecovery(_parkKey, null);
          _park = reviewPark;
          primary.inputs[`__ai_review_answer_${primary.resumeFrom}`] =
            onFailure === "use_default" ? stepDef.default_value : null;
          appendRecoveryEvent({ event: "review_on_failure_applied", slug: primary.entry.slug,
            step_index: primary.resumeFrom, policy: onFailure });
        } else {
          // Re-ask: keep the park alive (its TTL timer keeps running) and return a fresh review
          // request against the CURRENT page state instead of resuming execution this call.
          const reviewResp = await _buildReviewRequest(reviewPark.page, stepDef, primary.resumeFrom, primary.entry, {
            validationErrors: validation.valid ? null : validation.errors,
            pageDrifted: diverged,
          });
          if (_abortSignal) _abortSignal.removeEventListener("abort", _onAbort);
          runRegistry.end(_runId);
          await _tracker.flush();
          _tracker.destroy();
          return reviewResp;
        }
      } else {
        // Valid answer, page unchanged — adopt the park and bind the answer so run.js's
        // ai_review interception (right before executeStep) consumes it and moves straight past.
        clearTimeout(reviewPark.timer);
        _setParkedRecovery(_parkKey, null);
        _park = reviewPark;
        clearReviewRetry(primary.entry.slug, primary.resumeFrom);
        primary.inputs[`__ai_review_answer_${primary.resumeFrom}`] = primary.reviewResults[answerKey];
      }
    }

    // EXEC-21: resuming a hand-over pause — the human sibling of the ai_review resume above.
    // Two differences from review, both because a hand-over has no structured answer to
    // validate: (1) there is nothing to re-ask for, so a call naming a handover step with no
    // live park just refuses outright, same message shape as an expired review window; (2) page
    // drift is not distrusted here — a hand-over's whole point is letting a person change the
    // page, so no divergence-fingerprint check runs (compare the review resume's `diverged`
    // check above). run.js's own `revalidate()` call (inside its `handover` interception) is
    // what actually confirms the recorded tab still exists before the next step touches it.
    // The one thing THIS resume must do that review's never needs to: re-acquire the host lock,
    // which the pause-creation branch below released for the whole wait — handled further down,
    // gated on `_park.hostRelease == null`, right where the fresh-run branch first acquires it.
    const _resumeHandoverStep = resolved.length === 1 && primary.isResume
      && primary.steps[primary.resumeFrom] && primary.steps[primary.resumeFrom].type === "handover";
    if (_resumeHandoverStep) {
      const handoverPark = _getParkedRecovery(_parkKey);
      if (!handoverPark) {
        appendRecoveryEvent({ event: "handover_resume_refused", slug: primary.entry.slug, step_index: primary.resumeFrom });
        _runTracker.emit("wf_fail", { dur: Date.now() - _wfStartAt, fsi: primary.resumeFrom, fc: "handover_resume_refused" });
        if (_abortSignal) _abortSignal.removeEventListener("abort", _onAbort);
        runRegistry.end(_runId);
        await _receiptFlush;
        await _tracker.flush();
        _tracker.destroy();
        return err(
          `The hand-over window for step ${primary.resumeFrom + 1} has expired or was never opened. Call ` +
          `execute_skill again for "${primary.entry.slug}" without resume_from to restart the skill from the beginning.`
        );
      }
      clearTimeout(handoverPark.timer);
      _setParkedRecovery(_parkKey, null);
      // Whichever caller adopts the park first (a signal firing and self-resuming, or an agent
      // explicitly calling resume_from) disarms it exactly once here — the banner/file/http
      // listeners have done their job the moment either path reaches this line.
      if (handoverPark.armed) await handoverPark.armed.disarm().catch(() => {});
      _park = handoverPark;
      primary.inputs[`__handover_done_${primary.resumeFrom}`] = true;
      _runTracker.emit("handover_resumed", { si: primary.resumeFrom });
    }

    // RT-9: resuming a guard pause. Like the hand-over resume above there is no answer to
    // validate — the caller either releases the held destructive steps (adopt the parked page and
    // run on from the step) or ends the test (the park just times out). A release with no live park
    // is refused outright: restarting on a fresh page would run the irreversible step against a page
    // that is nowhere near where the workflow had got to.
    // (A release that rides along with an ai_review answer — a person's "yes" to a confirm-only
    // review also lets the steps behind it through — is handled by the review resume above, which
    // adopts the review's own park; this block is only for a park the guard itself created.)
    if (_guardRelease && !_resumeReviewStep && !_resumeHandoverStep && resolved.length === 1 && primary.isResume) {
      const guardPark = _getParkedRecovery(_parkKey);
      if (!guardPark || guardPark.guardStepIndex !== primary.resumeFrom) {
        appendRecoveryEvent({ event: "guard_resume_refused", slug: primary.entry.slug, step_index: primary.resumeFrom });
        _runTracker.emit("wf_fail", { dur: Date.now() - _wfStartAt, fsi: primary.resumeFrom, fc: "guard_resume_refused" });
        if (_abortSignal) _abortSignal.removeEventListener("abort", _onAbort);
        runRegistry.end(_runId);
        await _receiptFlush;
        await _tracker.flush();
        _tracker.destroy();
        return err(
          `The pause before step ${primary.resumeFrom + 1} has expired or was never opened. Run the test again ` +
          `to restart it from the beginning.`
        );
      }
      clearTimeout(guardPark.timer);
      _setParkedRecovery(_parkKey, null);
      _park = guardPark;
      appendRecoveryEvent({ event: "guard_released", slug: primary.entry.slug, step_index: primary.resumeFrom });
    }

    // PROD-18: every surviving path (fresh run or an adopted park) crosses here exactly
    // once, and none of the early returns above reach this line — so this is the one place
    // to settle the start receipt before any browser work begins.
    await _receiptFlush;

    let page = null;
    let _browser, _context, _protectedUrl;
    // True when _browser/_context are a view borrowed from Conxa Execute's own panel
    // (see host_browser.js) rather than a Chromium this process launched — every
    // teardown site must check this before closing anything (see teardownExecBrowser).
    let _hostOwned = false;
    // EXEC-41 Stage 2: the run id a host-owned browser was actually registered under with
    // Execute's panel (see browser_panel.js's `_runs` map, keyed by whatever runId `new_view`
    // was called with). Defaults to this call's OWN _runId — correct for a fresh run, where
    // they're the same value. A RESUMED park is the one case they diverge: _runId above is
    // freshly generated for every execute_skill call (including a resume), but the browser
    // view Execute is holding was registered under the run's ORIGINAL id — restored from
    // _park.runId below. Every teardownExecBrowser/_setParkedRecovery/runPlan call in this
    // function must use _hostRunId, never _runId, when talking to Execute or the tab registry,
    // or a resumed run's teardown/tab_open would target a runId Execute has never heard of.
    let _hostRunId = _runId;
    // Cache-lease key for _context, when it came from browser.js's headless cache (null for a
    // watch:true run, or a run that got its own uncached browser because the cache slot was
    // already leased by a concurrent run — see getCachedBrowser). Released at every teardown
    // path below except a park, which holds the lease until it's resumed or discarded (RT-3).
    let _leaseKey = null;
    // Host-lock release (RT-3 follow-up — host_lock.js). Held for the whole run, including
    // through a park, exactly like _leaseKey above: a parked page is a live, uncommitted
    // interaction with the target platform, and letting a sibling run mutate the same platform
    // while this one's fix is pending would be exactly the race this exists to prevent.
    let _hostRelease = null;
    // EXEC-14: track every tab opened during this run (populated once `_context` is known,
    // below) so closeExtraTabs (tabs.js) can close it alongside `page` on every exit path —
    // success, cancelled, session-expired, and non-parkable failure all need this, not just
    // success, so it's declared here rather than inside the try block, where the catch block
    // below couldn't see it.
    const _openedTabs = new Set();
    // Same reason as _openedTabs above: catch{} is a SIBLING block to try{}, not a nested one,
    // so a const/let declared inside try{} does not exist at all inside catch{} — referencing it
    // there throws "is not defined" (not a TDZ error) on every failure path, not just some.
    // These three are assigned inside the try block below (once _context/runtimeLog/etc. are
    // available) but must be declared out here so every cleanup call site in catch{} — cancelled,
    // session-expired, and the generic non-parkable-failure branch — can actually see them.
    let _attachPageListeners = null;
    let _trackOpenedTab = null;
    const _detachContextListeners = () => {
      if (!_context) return;
      _context.off("page", _attachPageListeners);
      _context.off("page", _trackOpenedTab);
    };
    try {
      if (_park) {
        ({ browser: _browser, context: _context, leaseKey: _leaseKey, hostRelease: _hostRelease } = _park);
        page = _park.page;
        _hostOwned = Boolean(_park.hostOwned);
        // EXEC-41 Stage 2 fix: restore the ORIGINAL run's id from the park, not this resumed
        // call's own freshly-generated _runId — see _hostRunId's declaration comment above for
        // why the two diverge here and nowhere else. _park.runId is set when the park is first
        // created (below, in the review/handover/failed branches) and survives every resume.
        _hostRunId = _park.runId || _runId;
        // The park's own listener closures (bound to the PREVIOUS call's runtimeLog/downloadQueue/
        // _openedTabs) are still attached to this same long-lived context — remove them before
        // this call attaches its own, or both calls' listeners would fire side by side.
        if (_park.attachPageListeners) _context.off("page", _park.attachPageListeners);
        if (_park.trackOpenedTab) _context.off("page", _park.trackOpenedTab);
        log("info", "recovery_park_resumed", { skill: primary.entry.slug, step_index: primary.resumeFrom });
        appendRecoveryEvent({ event: "recovery_park_resumed", slug: primary.entry.slug, step_index: primary.resumeFrom });
        _runTracker.emit("park_resumed", { si: primary.resumeFrom });

        // EXEC-21: a hand-over park released its host lock when it was created (see the
        // handoverPause branch below) so a long human-scale wait doesn't starve every sibling
        // run touching the same platform — see handover.js's header. `_hostRelease` just came
        // back null from that destructure, so re-acquire before resuming execution. The policy
        // gate does NOT re-run here, matching review's resume above: it already passed once, at
        // this run's original admission, and a resumed park continues that same run rather than
        // starting a new one.
        if (_hostRelease == null) {
          const _targetHosts = resolveTargetHosts(resolved, { resolveGroup });
          exec.waitingForHost = _targetHosts;
          const _lock = await hostLock.acquireHosts(_targetHosts, { runId: _runId, slug: primary.entry.slug }, {
            isDone: _execCancelled,
            locksDir: path.join(CONXA_DATA_DIR, "locks"),
          });
          exec.waitingForHost = null;
          if (!_lock.release) {
            // The park slot is already empty (adopted and cleared above) — discardPark has
            // nothing left to find, so close what we already destructured by hand. A hand-over
            // park is always a visible (watch:true), uncached browser (see the watch-forcing
            // check earlier in this handler), so there is no cache lease to release here.
            try { await page.close(); } catch (_) {}
            await teardownExecBrowser({ browser: _browser, context: _context, hostOwned: _hostOwned, runId: _hostRunId });
            throw Object.assign(new Error("Execution cancelled while re-acquiring a platform lock after hand-over."), {
              cancelled: true,
              hostLockWait: { host: _lock.host, blockerRunId: _lock.blocker && _lock.blocker.runId, blockerSkill: _lock.blocker && _lock.blocker.skill },
            });
          }
          _hostRelease = _lock.release;
          _phase("host_lock_reacquired");
        }
      } else {
        // Serialize against any sibling run touching the same external platform(s) before doing
        // any browser work at all (RT-3 follow-up) — a run on a different platform never waits.
        // locksDir (PROD-5) extends the same serialization ACROSS engine processes: a scheduled
        // run in the scheduler daemon's engine and this chat-driven run can never double-hit the
        // same platform. Filesystem trouble degrades to in-process-only (fail open — see
        // file_lock.js); unit tests omit locksDir and stay hermetic by design.
        const _targetHosts = resolveTargetHosts(resolved, { resolveGroup });

        // PROD-18 policy gate: refuse before any browser work — before the host lock, before
        // a page is ever opened. Only on a fresh start; a resumed park is continuing a run
        // that already passed this check when it first began, not a new admission. An
        // ordinary, ungoverned workspace (status "absent" — no policy document exists for
        // it) is completely unaffected and pays only the one KV read this already costs.
        const _policyLoad = await policyGate.loadPolicy({
          dataDir: CONXA_DATA_DIR, apiUrl: CONXA_API, workspaceId: primary.entry.workspace_id,
          trackingToken: primary.entry.pack?.tracking?.tracking_token,
          publicKeyB64: hostBridge.manifestPublicKey(),
        });
        let _policyRefusal = null;
        if (_policyLoad.status === "expired") {
          // The one bypass genuinely closed by local enforcement alone: expires_at is inside
          // the signed document, so letting the cache go stale (network blocked, machine
          // offline past the policy's own lifetime) does not silently keep a governed
          // workspace running — see policy_gate.js's module doc.
          _policyRefusal = {
            code: "policy_expired", pv: _policyLoad.doc?.policy_version,
            message: "Refused: this workspace's execution policy has expired and could not be " +
              "refreshed (offline, or the cloud is unreachable). Policy-governed skills stay " +
              "blocked until the policy can be re-verified.",
          };
        } else if (_policyLoad.status === "fresh" || _policyLoad.status === "cache") {
          const _verdict = policyGate.evaluate(_policyLoad.doc, {
            skills: resolved.map((r) => r.entry.slug), hosts: _targetHosts,
          });
          if (!_verdict.allow) {
            _runTracker.emit("policy_block", { code: _verdict.code, pv: _policyLoad.doc.policy_version, enf: Boolean(_policyLoad.doc.enforce) });
            if (_policyLoad.doc.enforce) {
              _policyRefusal = { code: _verdict.code, pv: _policyLoad.doc.policy_version, message: _verdict.message };
            } else {
              // Audit-only rollout: record what WOULD have blocked, then let the run proceed.
              log("warn", "policy_would_block", { run_id: _runId, code: _verdict.code, policy_version: _policyLoad.doc.policy_version });
            }
          }
        }
        // status "absent" — no policy document for this workspace at all. No gate.
        if (_policyRefusal) {
          appendRecoveryEvent({ event: "policy_blocked", run_id: _runId, slug: primary.entry.slug, code: _policyRefusal.code });
          _runTracker.emit("wf_fail", { dur: Date.now() - _wfStartAt, fsi: null, fc: _policyRefusal.code });
          if (_abortSignal) _abortSignal.removeEventListener("abort", _onAbort);
          runRegistry.end(_runId);
          await _tracker.flush();
          _tracker.destroy();
          return err(_policyRefusal.message);
        }

        exec.waitingForHost = _targetHosts;
        const _lock = await hostLock.acquireHosts(_targetHosts, { runId: _runId, slug: primary.entry.slug }, {
          isDone: _execCancelled, // same cancel/deadline check runPlan uses — see host_lock.js
          locksDir: path.join(CONXA_DATA_DIR, "locks"),
        });
        exec.waitingForHost = null;
        if (!_lock.release) {
          // _execCancelled() already flipped exec.deadlineExceeded (and logged/emitted it) if
          // that's why we gave up, or exec.cancelRequested was already true — either way this is
          // now indistinguishable, to the catch block below, from a cancellation/deadline hit
          // inside runPlan itself. hostLockWait carries the extra context for the message there.
          throw Object.assign(new Error("Execution cancelled while waiting for a platform lock."), {
            cancelled: true,
            hostLockWait: { host: _lock.host, blockerRunId: _lock.blocker && _lock.blocker.runId, blockerSkill: _lock.blocker && _lock.blocker.skill },
          });
        }
        _hostRelease = _lock.release;
        _phase("host_lock_acquired");

        const _getBrowser = () => getCachedBrowser(primary.entry.workspace_id, authManager, {
          headless: !watch,
          logFn: log,
          groupId: primary.entry.manifest && primary.entry.manifest.group_id,
          runId: _runId,
          noPrompt: trigger === "scheduled", // nobody is there to sign in — report it, never open a window
        });
        const _authResult = await _getBrowser();
        _phase("browser_context_ready");
        // AUTH-1: warn (never block) about hosts this skill visits that have no sign-in app. Goes out
        // as a test_phase entry — the only runtime log line Studio's Run Test relays to its test log.
        const _unclaimedHosts = [...new Set(resolved.flatMap((r) => (r.entry.manifest && r.entry.manifest.unclaimed_hosts) || []))];
        if (_unclaimedHosts.length) _phase(`no_signin_configured_for:${_unclaimedHosts.join(",")}`);
        if (_authResult.refused) {
          // Every browser session slot is leased by another run. Nothing ran and nothing was
          // opened — say so plainly; the message deliberately never suggests cancelling those runs.
          throw Object.assign(new Error(_authResult.message), { sessionsBusy: true });
        }
        if (_authResult.expiredApps) {
          // A scheduled run found a login missing/expired: no window was opened (nobody to use it).
          // The message names the app(s) and becomes the run's recorded summary for list_schedules.
          throw Object.assign(new Error(_authResult.message), { session_expired: true, authWindowOpened: false });
        }
        if (_authResult.authPending) {
          const _pendingApps = Array.isArray(_authResult.apps) ? _authResult.apps : [_authResult];
          // Detach: sign-in tabs are open, a human is needed, and that can take minutes. Nothing ran
          // yet. Not for a scheduled run (nobody is there), not when the caller opted out with
          // wait_for_auth: false, not when the launch itself failed (that message is already the real
          // answer), and not for a run that already waited once.
          if (trigger !== "scheduled" && args.wait_for_auth !== false && !args._after_auth && !_pendingApps.some((a) => a.reason === "launch_failed")) {
            _hostRelease?.(); // a human sign-in can take minutes — don't hold the platform meanwhile
            _hostRelease = null;
            const d = _detachUntilSignedIn({
              runId: _runId, toolName: name, args, skill: primary.entry.slug,
              workspaceId: primary.entry.workspace_id, auth: _authResult,
            });
            _phase("auth_awaiting_detached");
            // Pairs the wf_start above; same failure code the old "sign in, then run again" used.
            _runTracker.emit("wf_fail", { dur: Date.now() - _wfStartAt, fsi: null,
              fc: mapErrorToCode(Object.assign(new Error("sign-in required"), { session_expired: true })) });
            return {
              content: [{ type: "text", text: d.joined
                ? `${_awaitingAuthMessage(d.names, d.runId, true)} (This is the same request already waiting.)`
                : _awaitingAuthMessage(d.names, d.runId, _pendingApps.every((a) => a.reason === "already_open")) }],
              _meta: { "conxa/awaiting_auth": { run_id: d.runId, apps: d.names } },
            };
          }
          // No valid session — a login window was just opened for the user. Nothing ran yet,
          // so there's no failedAt/page to report; the outer catch below turns this into an
          // actionable "sign in, then re-run" response instead of a raw failure.
          throw Object.assign(new Error(_authResult.message),
            { session_expired: true, login_url: _authResult.loginUrl });
        }
        ({ browser: _browser, context: _context, protectedUrl: _protectedUrl, leaseKey: _leaseKey } = _authResult);
        _hostOwned = Boolean(_authResult.hostOwned);
        // A host-owned context has no context.newPage() (Electron doesn't support
        // Target.createTarget — see host_browser.js's header); _authResult.page is
        // the view Execute already created for this run.
        page = _authResult.page || await _context.newPage();
        // Packs compiled after the leading-navigate change (compiler/build.py
        // _insert_start_navigate_step) open with their own `navigate` step to the page they
        // were recorded on, so landing on _protectedUrl (the group app's landing page) first is
        // a wasted page load — and, when the two are different sites, a misleading one. Older
        // packs have no such step and still need this. The auth storage state is already loaded
        // into _context above either way, so the skill's own navigation is authenticated with
        // no re-login.
        if (_protectedUrl && primary.steps[0]?.type !== "navigate") {
          await page.goto(_protectedUrl, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => {});
        }
      }

      const runtimeLog = { consoleErrors: [], pageErrors: [], failedRequests: [] };
      // Isolated per-run workspace, not the OS Downloads folder and not CONXA_DIR (that's the
      // read-only install dir — see the header comment above). sweepOldRuns() clears stale
      // sibling run dirs before this one is created, so cleanup happens on every execution
      // regardless of how the *previous* run ended.
      const _runsBaseDir = path.join(CONXA_DATA_DIR, "runs");
      sweepOldRuns(_runsBaseDir, undefined, _runId);
      const _downloadsDir = path.join(_runsBaseDir, _runId);
      const _downloads = [];
      const _downloadSaves = [];
      const _downloadQueue = [];
      const _dialogQueue = [];

      const _takenNames = new Set();

      // Attach page diagnostic listeners — called on initial page and again after re-auth context rebuild.
      // (declared above, outside the try block, so catch{}'s cleanup calls can see it — see comment there.)
      _attachPageListeners = (pg) => {
        pg.on("console", msg => {
          if (["error", "warning"].includes(msg.type()) && runtimeLog.consoleErrors.length < 50)
            runtimeLog.consoleErrors.push({ type: msg.type(), text: msg.text() });
        });
        pg.on("pageerror",     e  => { if (runtimeLog.pageErrors.length < 20) runtimeLog.pageErrors.push(e.message); });
        pg.on("requestfailed", req => {
          if (runtimeLog.failedRequests.length < 30)
            runtimeLog.failedRequests.push({ url: req.url(), failure: req.failure()?.errorText });
        });
        pg.on("download", (download) => {
          let resolveEntry;
          const entryPromise = new Promise(resolve => { resolveEntry = resolve; });
          _downloadQueue.push(entryPromise);
          // Reserved synchronously, before the async save — see uniqueDownloadName (EXEC-11).
          const fname = uniqueDownloadName(download.suggestedFilename() || `download_${Date.now()}`, _takenNames);
          const savePromise = (async () => {
            fs.mkdirSync(_downloadsDir, { recursive: true });
            const dest = path.join(_downloadsDir, fname);
            // Under Execute the file never reaches Playwright — Execute's own browser saves it
            // (host_browser.js::saveDownload, EXEC-46).
            if (_hostOwned) await hostBrowser.saveDownload({ runId: _hostRunId, download, dest, timeoutMs: DOWNLOAD_WAIT_TIMEOUT_MS });
            else await download.saveAs(dest);
            _downloads.push(dest);
            // Extraction happens here — at download time, unconditionally — not lazily when
            // some later upload step happens to resolve to a .zip path. Keeps replay symmetric
            // with recording (session.py::_on_download does the same) and lets the compiler's
            // {{downloaded_file_N_dir}} binding point at a folder that's already real by the
            // time any step needs it. See run.js::resolveUploadPaths for the upload-side half.
            const extractedDir = fname.toLowerCase().endsWith(".zip") ? extractZipOnce(dest) : null;
            resolveEntry({ filename: fname, path: dest, extractedDir });
          })().catch(() => { resolveEntry(null); });
          _downloadSaves.push(savePromise);
        });
        // dialog_accept/dialog_dismiss steps (handlers.js) drain this — without a listener here
        // Playwright silently auto-DISMISSES any native alert/confirm/prompt, which is the
        // opposite of what a recorded "accept" step expects.
        pg.on("dialog", (dialog) => { _dialogQueue.push(dialog); });
        // A recorded "File upload" menu click opens the OS picker. Listening here suppresses
        // that native dialog so the run cannot hang in watch mode; the compiled upload step
        // then setInputFiles on the hidden <input type=file>.
        pg.on("filechooser", () => {});
      };

      _attachPageListeners(page);
      // Multi-tab: a download (or console/page error) triggered from a tab other than the
      // initial one used to be invisible entirely — this listener was only ever attached to
      // `page`. Every tab opened during this run (site-opened or Ctrl+T — see tabs.js) now gets
      // the same diagnostics/download capture the initial tab has.
      _context.on("page", _attachPageListeners);
      // Feeds _openedTabs (declared above, outside this try block, so every exit path's
      // closeExtraTabs(_openedTabs, ...) call below can see it). Never fires for `page` itself
      // (created via _context.newPage() before this listener attaches) or for a page from a
      // resumed park (opened in a prior call's closure) — only genuinely new tabs opened by
      // *this* run's steps land here. Named (not inline) so it — and _attachPageListeners — can
      // be removed with _context.off() at teardown; a cached/leased context outlives this one
      // call, and an un-removed listener would keep firing for whichever run leases it next.
      _trackOpenedTab = (pg) => { _openedTabs.add(pg); };
      _context.on("page", _trackOpenedTab);

      // Every non-parking exit path (success, cancelled, session-expired, non-parkable failure)
      // must remove this call's own listeners before returning — a cached/leased context can
      // outlive this call, and both listener functions close over this call's own
      // runtimeLog/_downloadQueue/_openedTabs, which must stop firing once this call is done. The
      // parking path deliberately skips this — it hands the closures to the park object instead
      // (see below), so the NEXT call's resume can remove them at the right time.
      // (_detachContextListeners itself is declared above, outside the try block — see comment there.)

      for (let si = 0; si < resolved.length; si++) {
        const { entry, steps, inputs, resumeFrom, dryRun, isResume } = resolved[si];
        const startAt = si === 0 ? resumeFrom : 0;
        // Only the primary skill's own resume actually resumes a call chain (see startAt above) —
        // a later skill in an execute_sequence always starts fresh at step 0.
        const isResuming = si === 0 && isResume;
        // Backs a compiled bulk-upload step's {{downloaded_files_dir}} placeholder (see
        // conxa_compile/compiler/upload_binding.py's _BindingState) — resolveUploadPaths already
        // expands a folder into every file inside it, so this run's own isolated download folder
        // just works.
        inputs.downloaded_files_dir = _downloadsDir;

        try {
          const result = await runPlan(page, steps, inputs, startAt, entry.slug, {
            // `step`/`intent`/`destructive` ride along so Build Studio's test card can name each step as it
            // starts (RT-9) — extra fields on a diagnostic log line, ignored by every other reader.
            onStep:        (i) => { exec.step = i; _phase(`step_${i}_${steps[i]?.type || "?"}_start`, { step: i, step_total: steps.length, intent: steps[i]?.intent || null, destructive: steps[i]?.destructive === true }); },
            onPhase:       _phase,
            cancelCheck:   _execCancelled,
            tracker:       _runTracker,
            downloadQueue: _downloadQueue,
            dialogQueue: _dialogQueue,
            structuralFingerprint: entry.manifest && entry.manifest.structural_fingerprint,
            environmentFingerprint: entry.manifest && entry.manifest.environment,
            watch,
            runId: _runId,
            hostOwned: _hostOwned,
            hostRunId: _hostRunId,
            dataDir: CONXA_DATA_DIR,
            dryRun,
            isResume: isResuming,
            context: _context, // EXEC-21: handover needs the browser context to arm its banner
            guard: _guardPause ? { pause: true, released: _guardRelease } : undefined, // RT-9
          });
          _totalRecovered += (result && result.recoveredSteps) ? result.recoveredSteps : 0;
          if (result && Array.isArray(result.warnings)) {
            for (const w of result.warnings) if (!_allWarnings.includes(w)) _allWarnings.push(w);
          }
          if (result && Array.isArray(result.dryRunSkipped) && result.dryRunSkipped.length) {
            _dryRunSkipped.push(...result.dryRunSkipped.map(s => ({ ...s, slug: entry.slug })));
          }
          // Times the LAST step, which onStep's start-only markers can never do on their own —
          // and localizes where post-loop teardown (below) begins.
          _phase(`steps_complete:${steps.length}`);
        } catch (runErr) {
          // Auth-failure handling: detect a login redirect and fail immediately, naming the
          // app whose session died. For an app with no learned auth definition, authentication
          // stays pre-flight only — no re-auth window opens here; the next execute_skill call's
          // normal pre-flight gate (getGroupAuthContext) re-validates it and opens a window then.
          // An app WITH a learned definition (P0: Application Authentication Recording) instead
          // gets auto-retried right below (see the session_expired handler's hasAuthDefinition
          // branch) — the confidence that let mid-run auto-login be trusted at all is exactly what
          // was missing when this was written pre-flight-only; see captureReAuth's own comment.
          const failedStep = runErr.failedAt ?? null;
          // Multi-tab: check auth on the tab the step actually failed on, not always the
          // initial tab — a login redirect in a second tab would otherwise go undetected.
          const _failedPage = runErr.failedPage || page;
          if (failedStep !== null && await isAuthFailure(_failedPage, steps)) {
            // The page that actually bounced to a login screen is the right host to resolve
            // the dead app from — for a group pack, entry.manifest.target_url is the START
            // app, which is wrong when a SIBLING app's session is the one that died (see
            // CLAUDE.md group-auth notes / captureReAuth's host-matching fallback chain).
            const loginUrl = _failedPage.url() || entry.manifest?.target_url;
            appendRecoveryEvent({ event: "auth_failure_detected", slug: entry.slug, step_index: failedStep });
            const refreshResult = await captureReAuth(entry.workspace_id, loginUrl, authManager, SESSIONS_DIR, log, {
              groupId: entry.manifest && entry.manifest.group_id,
              fallbackUrl: entry.manifest?.target_url,
            });
            // captureReAuth couldn't tie the failing page to any app this group actually
            // manages a saved session for — the login redirect isn't a tracked session dying
            // (see its comment). Don't dress up an unrelated step failure as a session expiry
            // for a made-up app; fall through to the real error below instead.
            if (refreshResult.unresolved) runErr.authHostNote = refreshResult.note;
            if (!refreshResult.unresolved) {
              throw Object.assign(
                new Error(refreshResult.message),
                {
                  session_expired: true,
                  login_url: refreshResult.loginUrl || loginUrl,
                  failedAt: failedStep,
                  fromEntry: entry,
                  // No window was opened for this failure (see captureReAuth) — the shared
                  // session_expired handler below must not tell the user "once signed in", since
                  // nothing has prompted them to sign in yet at this point.
                  authWindowOpened: false,
                  // P0: Application Authentication Recording — whether the app that died has a
                  // learned auth definition, which is what lets the session_expired handler
                  // below auto-retry this run instead of only telling the person to call
                  // execute_skill again by hand.
                  hasAuthDefinition: Boolean(refreshResult.hasAuthDefinition),
                }
              );
            }
          }
          runErr.fromEntry = entry;
          // A login wall on a site no group app covers (refreshResult.unresolved above): keep the
          // real step failure, but add where the fix is instead of leaving a bare selector error.
          if (runErr.authHostNote) runErr.message = `${runErr.message} ${runErr.authHostNote}`;
          throw runErr;
        }
      }

      // No session save on success: the context holds every group app's session merged together, and
      // dumping that back would blur per-app boundaries. Each app's own session file is written only
      // by beginInteractiveAuth's sign-in and refreshed by getGroupAuthContext's per-app pre-flight.
      const url  = page.url();
      const shot = process.env.CONXA_CAPTURE_SUCCESS_SCREENSHOT === "1"
        ? await page.screenshot({ type: "png" }).catch(() => null)
        : null;
      await Promise.allSettled(_downloadSaves);
      await page.close().catch(() => {});
      await closeExtraTabs(_openedTabs);
      _detachContextListeners();
      if (watch) {
        await teardownExecBrowser({ browser: _browser, context: _context, hostOwned: _hostOwned, runId: _hostRunId });
        _phase("browser_closed");
      }
      releaseCachedBrowser(_leaseKey); // no-op when _leaseKey is null (watch mode, or uncached)
      _hostRelease?.();

      for (const r of resolved) {
        clearRetryBudget(r.entry.slug);
        recoveryStage.clearForSlug(r.entry.slug); // reset Tier 3/4 escalation + digest maps
        appendRecoveryEvent({ event: "run_success", slug: r.entry.slug, steps_executed: r.steps.length });
      }

      _runTracker.emit("wf_ok", {
        dur: Date.now() - _wfStartAt,
        tot: resolved.reduce((n, r) => n + r.steps.length, 0),
        rec: _totalRecovered,
      });
      // Flush happens once, in `finally`, for every exit path — see there.

      log("info", "execute_success", { run_id: _runId, skill: primary.entry.slug, url });

      const downloadNote = _downloads.length
        ? `\nDownloaded files:\n${_downloads.map(p => `  ${p}`).join("\n")}`
        : "";
      const warningNote = _allWarnings.length
        ? `\n\nWarning:\n${_allWarnings.map(w => `  ${w}`).join("\n")}`
        : "";
      const dryRunNote = _dryRunSkipped.length
        ? `\n\nDry run: ${_dryRunSkipped.length} committing step(s) skipped (resolved but never acted on):\n` +
          _dryRunSkipped.map(s => `  - ${s.intent || `step ${s.index + 1}`}`).join("\n")
        : "";
      const doneVerb = _dryRunSkipped.length ? "Dry run complete" : "Done";
      const content = [{ type: "text", text: `${doneVerb}. URL: ${url}${downloadNote}${warningNote}${dryRunNote}\n(run_id: ${_runId})` }];
      if (shot) content.push({ type: "image", data: shot.toString("base64"), mimeType: "image/png" });
      return { content };

    } catch (runErr) {
      _phase(`steps_failed:${mapErrorToCode(runErr)}`);
      log("error", "execute_failed", { run_id: _runId, skill: primary.entry.slug, error: runErr.message });
      appendRecoveryEvent({ event: "terminal_failure", run_id: _runId, slug: primary.entry.slug, error: runErr.message });
      _runTracker.emit("wf_fail", {
        dur: Date.now() - _wfStartAt,
        fsi: runErr.failedAt ?? null,
        fc:  mapErrorToCode(runErr),
      });
      // Do NOT flush/destroy the tracker here — the Tier 3/4 recovery-request and
      // park-created events (below, and inside _buildFailureResponse) haven't been emitted
      // yet. Tearing the tracker down this early silently drops that telemetry. The tracker
      // is flushed once, in `finally`, after every code path in this catch block has had a
      // chance to emit.

      // Aborted runs (runPlan threw { cancelled }) come from two sources, both of which tear down
      // immediately and never park (a parked page is only useful for an agent that is still waiting):
      //   1. Deadline watchdog — we stopped *before* the client's request timeout, so the caller is
      //      still listening. Return an actionable failure naming the stalled step so the agent can
      //      verify inputs or resume with step_overrides, instead of the user seeing a silent hang.
      //   2. Client cancel (cancel_execution, or the client timed out first and sent
      //      notifications/cancelled) — the caller is gone, the SDK drops any response; just clean up.
      if (runErr.cancelled) {
        const wasDeadline = exec.deadlineExceeded;
        const stalledStep = exec.step;
        if (page) await page.close().catch(() => {});
        await closeExtraTabs(_openedTabs);
        _detachContextListeners();
        if (watch) {
          await teardownExecBrowser({ browser: _browser, context: _context, hostOwned: _hostOwned, runId: _hostRunId });
        }
        releaseCachedBrowser(_leaseKey);
        _hostRelease?.();
        // RT-3 follow-up: this run may have given up waiting for a platform host_lock.js was
        // still holding for a sibling run, rather than exceeding the deadline mid-step — say so
        // when that's what happened, so the agent knows to just retry later instead of assuming
        // its own inputs/selectors are at fault.
        const hostWaitNote = runErr.hostLockWait && runErr.hostLockWait.host
          ? ` Still waiting for ${runErr.hostLockWait.host}, in use by another run` +
            `${runErr.hostLockWait.blockerSkill ? ` (${runErr.hostLockWait.blockerSkill})` : ""}.`
          : "";
        if (wasDeadline) {
          const secs = Math.round(EXECUTION_DEADLINE_MS / 1000);
          const stepLabel = stalledStep !== null ? ` at step ${stalledStep + 1}` : "";
          const resumeHint = _effAgentEnabled && stalledStep !== null && !runErr.hostLockWait
            ? ` If a element moved, inspect the page and call execute_skill again with resume_from: ${stalledStep} and step_overrides.`
            : "";
          return err(
            `Execution stopped after exceeding the ${secs}s time budget${stepLabel}.${hostWaitNote} ` +
            `${runErr.hostLockWait ? "" : "The page never reached the expected state in time — most often the inputs don't match " +
              "what the site returned (e.g. a repository/search term with no results), so the next " +
              "element never appeared. Verify the inputs and retry."}${resumeHint} (run_id: ${_runId})`
          );
        }
        return err(`Execution cancelled.${hostWaitNote} (run_id: ${_runId})`);
      }

      // No free browser session (see browser_session.js): not a failure of the skill, and there is
      // nothing to tear down — no browser was handed out. Just release the platform lock.
      if (runErr.sessionsBusy) {
        _hostRelease?.();
        return err(`${runErr.message} (run_id: ${_runId})`);
      }

      // Session expiry is a distinct condition, not a selector/DOM failure — no screenshot,
      // no recovery payload, just a clear instruction to sign in and re-run. Handled here
      // rather than in _buildFailureResponse because the pre-run auth-pending case (session
      // expired before any step ran) never has a `page` to build a response around.
      if (runErr.session_expired) {
        if (page) await page.close().catch(() => {});
        await closeExtraTabs(_openedTabs);
        _detachContextListeners();
        if (watch) {
          await teardownExecBrowser({ browser: _browser, context: _context, hostOwned: _hostOwned, runId: _hostRunId });
        }
        releaseCachedBrowser(_leaseKey);
        _hostRelease?.();
        const failedAt = typeof runErr.failedAt === "number" ? runErr.failedAt : null;

        // P0: Application Authentication Recording — a mid-run expiry for an app WITH a learned
        // auth definition is confident enough to auto-retry instead of only telling the person to
        // call execute_skill again by hand (spec §11). Fires a fresh call under the SAME run_id
        // with resume_from set; that call runs the ordinary pre-flight gate above from scratch,
        // which detects the now-known-expired session, opens a login window, and detaches exactly
        // like any other pending sign-in already does — no new detach/registry mechanism needed.
        // Strictly gated: never for the generic detection ladder (hasAuthDefinition — the very
        // confidence gap that made this pre-flight-only in the first place, see captureReAuth's
        // comment), never without agent-enabled resume support, and capped to one retry per
        // original call (_mid_run_reauth) so a pathological repeat expiry can't loop forever.
        if (runErr.hasAuthDefinition && runErr.authWindowOpened === false && _effAgentEnabled
            && failedAt !== null && !args._mid_run_reauth) {
          const retrySlug = (runErr.fromEntry || primary.entry).slug;
          log("info", "mid_run_reauth_retry", { run_id: _runId, skill: retrySlug, step_index: failedAt });
          setImmediate(() => {
            _handleTool(name, { ...args, _run_id: _runId, resume_from: failedAt, _mid_run_reauth: true }, extra).catch(() => {});
          });
          return {
            content: [{ type: "text", text:
              `${runErr.message} Opening a sign-in window now — finish signing in and "${retrySlug}" resumes on its own ` +
              `from where it left off; you do not need to call execute_skill again. ` +
              `Check progress with get_execution_status (run_id: ${_runId}).` }],
            _meta: { "conxa/awaiting_auth": { run_id: _runId } },
          };
        }

        // A mid-run failure (authWindowOpened: false — see captureReAuth) never opened a
        // window; runErr.message already tells the caller to call execute_skill again to get
        // prompted. Only add the resume_from mechanics there, not "once signed in" — nobody's
        // been prompted to sign in yet. The pre-flight case (authWindowOpened left undefined —
        // a window WAS just opened before any step ran) keeps the original "once signed in" hint.
        const resumeHint = runErr.authWindowOpened === false
          ? (_effAgentEnabled && failedAt !== null
              ? ` Pass resume_from: ${failedAt} on that next call to continue from where this one stopped.`
              : "")
          : (_effAgentEnabled && failedAt !== null
              ? ` Once signed in, call execute_skill again for "${(runErr.fromEntry || primary.entry).slug}" with resume_from: ${failedAt}.`
              : ` Once signed in, call execute_skill again to run this skill.`);
        // `_meta` lets a programmatic caller (Build Studio's test) tell "the saved sign-in expired" from
        // a step failure without parsing prose; the text is unchanged for every other client.
        return { ...err(`${runErr.message}${resumeHint} (run_id: ${_runId})`),
          _meta: { "conxa/session_expired": { run_id: _runId, step_index: failedAt } } };
      }

      // EXEC-13: ai_review reached this step for the first time (not a resume) — this is a
      // PLANNED pause, not a failure. Park the live page exactly like the Tier 3/4 `parkable`
      // branch below (same _setParkedRecovery shape, same PARK_TTL_MS), but return a review
      // request instead of a failure response, and never touch recoveryStage's escalation state
      // or retry_budget.js's budget — see review_pause.js's header for why those stay separate.
      if (runErr.reviewPause) {
        const _reviewPage = runErr.page || page;
        const timer = setTimeout(() => { _discardPark(_parkKey, "ttl"); }, PARK_TTL_MS);
        if (timer.unref) timer.unref();
        _setParkedRecovery(_parkKey, { slug: primary.entry.slug, workspace_id: primary.entry.workspace_id,
          page: _reviewPage, context: _context, browser: _browser, hostOwned: _hostOwned, runId: _hostRunId, watch, reviewStepIndex: runErr.stepIndex, timer,
          pageFingerprint: await capturePageFingerprint(_reviewPage),
          leaseKey: _leaseKey, hostRelease: _hostRelease,
          attachPageListeners: _attachPageListeners, trackOpenedTab: _trackOpenedTab });
        await closeExtraTabs(_openedTabs, _reviewPage);
        appendRecoveryEvent({ event: "review_park_created", slug: primary.entry.slug, step_index: runErr.stepIndex, ttl_ms: PARK_TTL_MS });
        log("info", "review_park_created", { run_id: _runId, skill: primary.entry.slug, step_index: runErr.stepIndex });
        _runTracker.emit("park_created", { si: runErr.stepIndex });
        const reviewResp = await _buildReviewRequest(_reviewPage, runErr.step, runErr.stepIndex, primary.entry, {});
        if (reviewResp && Array.isArray(reviewResp.content)) {
          reviewResp.content.push({ type: "text", text: `(run_id: ${_runId})` });
        }
        return reviewResp;
      }

      // RT-9: the test guard reached a step that can't be undone. Park the live page exactly like
      // the ai_review pause above (same park shape, same TTL, same fingerprint) and tell the caller
      // what it is about to do — no recovery state, no retry budget, nothing sent yet.
      if (runErr.guardPause) {
        const _guardPage = runErr.page || page;
        const timer = setTimeout(() => { _discardPark(_parkKey, "ttl"); }, PARK_TTL_MS);
        if (timer.unref) timer.unref();
        _setParkedRecovery(_parkKey, { slug: primary.entry.slug, workspace_id: primary.entry.workspace_id,
          page: _guardPage, context: _context, browser: _browser, hostOwned: _hostOwned, runId: _hostRunId, watch, guardStepIndex: runErr.stepIndex, timer,
          pageFingerprint: await capturePageFingerprint(_guardPage),
          leaseKey: _leaseKey, hostRelease: _hostRelease,
          attachPageListeners: _attachPageListeners, trackOpenedTab: _trackOpenedTab });
        appendRecoveryEvent({ event: "guard_park_created", slug: primary.entry.slug, step_index: runErr.stepIndex, ttl_ms: PARK_TTL_MS });
        log("info", "guard_park_created", { run_id: _runId, skill: primary.entry.slug, step_index: runErr.stepIndex });
        _runTracker.emit("park_created", { si: runErr.stepIndex, kind: "guard" });
        const guardIntent = (runErr.step && (runErr.step.intent || runErr.step.type)) || `step ${runErr.stepIndex + 1}`;
        return {
          content: [{
            type: "text",
            text: `Paused before "${guardIntent}" (step ${runErr.stepIndex + 1}), which can't be undone. ` +
              `${runErr.remaining} step(s) from here on can't be undone. Call execute_skill again with resume_from = ${runErr.stepIndex} and ` +
              `_guard_release = true to run them for real, or stop here. (run_id: ${_runId})`,
          }],
          _meta: { "conxa/guard": { step_index: runErr.stepIndex, step_intent: guardIntent, remaining_irreversible: runErr.remaining, run_id: _runId } },
        };
      }

      // EXEC-21 (hand-over shape): reached this step for the first time (not a resume) and the
      // in-call wait (handover.HANDOVER_INCALL_MS) already expired without the person
      // signalling — see handover.js's header. Park the live page like ai_review above, but
      // with three deliberate differences: the host lock is RELEASED (not held) for the length
      // of the pause, since a human-scale wait would otherwise starve every sibling run
      // touching the same platform (re-acquired above, on resume, gated on hostRelease being
      // null); extra tabs are NOT closed, since the person may be mid-flow in a popup (a
      // 2FA/OAuth window, a document viewer); and the armed banner/file/http signal sources
      // run.js already set up are kept alive on the park — the moment ANY of them fires, this
      // process resumes the run ITSELF, with no further agent call needed, the same way the
      // `skill_` prefix shortcut below calls back into `_handleTool` directly.
      if (runErr.handoverPause) {
        const _handoverPage = runErr.page || page;
        if (_hostRelease) { try { _hostRelease(); } catch (_) {} }
        const timer = setTimeout(() => {
          if (runErr.armed) runErr.armed.disarm().catch(() => {});
          _discardPark(_parkKey, "ttl");
        }, HANDOVER_PARK_TTL_MS);
        if (timer.unref) timer.unref();
        _setParkedRecovery(_parkKey, { slug: primary.entry.slug, workspace_id: primary.entry.workspace_id,
          page: _handoverPage, context: _context, browser: _browser, hostOwned: _hostOwned, runId: _hostRunId, watch, handoverStepIndex: runErr.stepIndex, timer,
          pageFingerprint: await capturePageFingerprint(_handoverPage),
          leaseKey: _leaseKey, hostRelease: null,
          attachPageListeners: _attachPageListeners, trackOpenedTab: _trackOpenedTab,
          armed: runErr.armed });
        appendRecoveryEvent({ event: "handover_park_created", slug: primary.entry.slug, step_index: runErr.stepIndex, ttl_ms: HANDOVER_PARK_TTL_MS });
        log("info", "handover_park_created", { run_id: _runId, skill: primary.entry.slug, step_index: runErr.stepIndex });
        _runTracker.emit("park_created", { si: runErr.stepIndex, kind: "handover" });

        if (runErr.armed) {
          // Fire-and-forget: whenever the person signals (possibly minutes or hours from now,
          // long after this call has returned), resume the run through the exact same path an
          // agent's own resume_from call would take (_resumeHandoverStep above) — no separate
          // "internal resume" code path to keep in sync with the external one.
          runErr.armed.signal.then((via) => {
            log("info", "handover_signalled", { skill: primary.entry.slug, step_index: runErr.stepIndex, via });
            _handleTool("execute_skill", {
              skill: primary.entry.slug,
              workspace_id: primary.entry.workspace_id,
              resume_from: runErr.stepIndex,
              // The original call's inputs — dropping these meant a resumed run either failed
              // the required-input check or filled every remaining {{placeholder}} with "".
              inputs: args.inputs,
              watch: true,
              dry_run: runErr.dryRun,
            }).catch((e) => log("error", "handover_self_resume_failed", { error: e && e.message }));
          }).catch(() => {});
        }

        const handoverResp = await handoverMod.buildHandoverRequest(_handoverPage, runErr.step, runErr.stepIndex, {
          runId: _runId,
          httpPort: runErr.armed && runErr.armed.httpPort,
          token: runErr.armed && runErr.armed.token,
          cmdFile: runErr.armed && runErr.armed.cmdFile,
        });
        return handoverResp;
      }

      // Multi-tab: use the tab the failing step actually ran on, not always the initial tab —
      // the recovery request (and its DOM fingerprint) must describe the page that failed.
      const _failedPage = runErr.failedPage || page;

      // Wrapped: if assembling the recovery payload itself throws (screenshot/digest/fs error),
      // the platform lock and browser lease must still be released below rather than leaking —
      // this is best-effort evidence-building, not something that should turn into a stuck lock.
      const failResp = _failedPage
        ? await (async () => {
            try {
              return await _buildFailureResponse(_failedPage, runErr, runErr.fromEntry || primary.entry, _runTracker, resolved.length === 1 ? primary.steps : null, exec);
            } catch (buildErr) {
              log("error", "failure_response_build_failed", { run_id: _runId, error: buildErr && buildErr.message });
              const { summary, detail } = plainCause(runErr);
              return err(`Execution failed: ${summary} (${detail}) — the detailed recovery report could not be built (${plainCause(buildErr).summary}).`);
            }
          })()
        : (() => {
            // No page to build the full recovery response against (e.g. browser launch or
            // platform-lock failure before a page ever opened) — shape it the same way stepFailure
            // does rather than surfacing the raw internal error.
            const { summary, detail } = plainCause(runErr);
            return err(`Execution failed: ${summary} (${detail})`);
          })();
      if (failResp && Array.isArray(failResp.content)) {
        // EXEC-36/BUILD-28(a) — same warnings a passing run would have surfaced, so a run that
        // failed BECAUSE of a drift/environment mismatch says so instead of reading like an
        // ordinary selector/timing failure. _allWarnings covers earlier skills in a sequence;
        // runErr.warnings (set by stepFailure) covers this skill's own run up to the failure.
        const _runWarnings = _allWarnings.slice();
        if (Array.isArray(runErr.warnings)) {
          for (const w of runErr.warnings) if (!_runWarnings.includes(w)) _runWarnings.push(w);
        }
        if (_runWarnings.length) {
          failResp.content.push({ type: "text", text: `Warning:\n${_runWarnings.map(w => `  ${w}`).join("\n")}` });
        }
        // PROD-3-DRYRUN layer 5 — a destructive step may already have landed
        // (actionMayHaveTakenEffect, EXEC-24's signal) before this run died. OFFER the linked
        // compensation skill as a next step; never run it automatically — an unattended
        // compensating write after an unknown failure is a second uncontrolled action.
        const _compEntry = runErr.fromEntry || primary.entry;
        const _compSlug = runErr.actionMayHaveTakenEffect && _compEntry && _compEntry.manifest
          ? String(_compEntry.manifest.compensation_skill || "").trim() : "";
        if (_compSlug) {
          failResp.content.push({ type: "text", text:
            `This may have already taken effect before the failure. A cleanup workflow is available: ` +
            `call execute_skill with skill: "${_compSlug}" to undo it.` });
        }
        failResp.content.push({ type: "text", text: `(run_id: ${_runId})` });
      }

      // Park the live failed page for an agent-mediated (Tier 3/4) resume instead of tearing it
      // down — so the corrected selector lands on the same DOM the recovery request describes.
      // Only for single-run selector/verify failures with agent recovery enabled; auth/cancel
      // and Studio-ceiling failures are terminal and clean up normally.
      // PROD-3: a destructive-halt or entity-binding-miss failure is a deliberate fail-closed
      // stop, not exhausted recovery — never park it for an agent-mediated resume, which is what
      // would let a step_overrides candidate pick a different (possibly wrong) element or row.
      const parkable = _failedPage && _effAgentEnabled && resolved.length === 1
        && typeof runErr.failedAt === "number" && !runErr.session_expired && !runErr.cancelled
        && !runErr.destructiveHalt && !runErr.entityNotFound;
      if (parkable) {
        const timer = setTimeout(() => { _discardPark(_parkKey, "ttl"); }, PARK_TTL_MS);
        if (timer.unref) timer.unref();
        // The lease (if any — see _leaseKey above), the host lock (see _hostRelease above), and
        // this call's own page-listener closures are handed to the park, not released/detached
        // here: the park keeps the browser leased and the target platform locked (RT-3 — a
        // headless park must survive its full TTL, not the idle-cache's 90s; and a sibling run
        // must stay blocked from touching the same platform while this fix is pending) until
        // whichever call resumes or discards this park releases them.
        _setParkedRecovery(_parkKey, { slug: primary.entry.slug, workspace_id: primary.entry.workspace_id,
          page: _failedPage, context: _context, browser: _browser, hostOwned: _hostOwned, runId: _hostRunId, watch, failedAt: runErr.failedAt, timer,
          pageFingerprint: await capturePageFingerprint(_failedPage),
          leaseKey: _leaseKey, hostRelease: _hostRelease,
          attachPageListeners: _attachPageListeners, trackOpenedTab: _trackOpenedTab });
        // Only the parked page survives — any other tab this run opened (the failure wasn't
        // necessarily on the newest tab) is closed now rather than left to leak.
        await closeExtraTabs(_openedTabs, _failedPage);
        appendRecoveryEvent({ event: "recovery_park_created", slug: primary.entry.slug, step_index: runErr.failedAt, ttl_ms: PARK_TTL_MS });
        log("info", "recovery_park_created", { run_id: _runId, skill: primary.entry.slug, step_index: runErr.failedAt });
        _runTracker.emit("park_created", { si: runErr.failedAt });
      } else {
        if (page) await page.close().catch(() => {});
        if (_failedPage && _failedPage !== page) await _failedPage.close().catch(() => {});
        await closeExtraTabs(_openedTabs);
        _detachContextListeners();
        if (watch) {
          await teardownExecBrowser({ browser: _browser, context: _context, hostOwned: _hostOwned, runId: _hostRunId });
        }
        releaseCachedBrowser(_leaseKey);
        _hostRelease?.();
      }
      return failResp;

    } finally {
      if (_abortSignal) _abortSignal.removeEventListener("abort", _onAbort);
      runRegistry.end(_runId);
      // Single flush point for every exit path (success, deadline, cancel, and the
      // Tier 3/4 recovery-request/park-created path) — guarantees every event emitted
      // above (wf_ok, wf_fail, tier_escalated, park_created, park_resumed, override_applied)
      // is actually sent before the tracker's timer is torn down.
      await _tracker.flush();
      _phase("tracking_flushed");
      _tracker.destroy();
    }
  }

  // ── skill_{company}_{slug} — direct skill execution without discovery round-trip ──
  if (name.startsWith("skill_")) {
    const withoutPrefix = name.slice(6); // e.g. "render_create_a_service"
    const entry = Object.values(skillIndex).find(
      (e) => `${e.workspace_id}_${e.slug.replace(/-/g, "_")}` === withoutPrefix
    );
    if (!entry) return err(`Skill tool not found: ${name}. Call list_skills to see available skills.`);
    return _handleTool("execute_skill", {
      skill:   entry.slug,
      workspace_id: entry.workspace_id,
      inputs:  args,
      watch:   true,
    });
  }

  return err(`Unknown tool: ${name}`);
}

// MCP registration (claude_desktop_config.json, ~/.claude.json, and every other
// detected agent host) is handled by `conxa-runtime.exe register-mcp` /
// `unregister-mcp` — see bootstrap.js, mcp_register.js, mcp_hosts.js, and
// config_edit.js. The NSIS installer just invokes the subcommand; it holds no
// config-editing logic of its own.

async function _phonehome() {
  const companies = [...new Set(Object.values(skillIndex).map(s => s.workspace_id))];
  const body = JSON.stringify({
    runtime_version: RUNTIME_VERSION,
    companies,
    platform: process.platform,
    install_id: INSTALL_ID,
    // Added for the release system's Deployment view (docs/App-Flow.md) — optional
    // server-side, so an older cloud that doesn't know this field yet is unaffected.
    skill_versions: installedSkillVersions(skillIndex),
    // Per-skill sync failures (checksum mismatch, download/activation error) —
    // lets the Deployment dashboard show "failed" instead of just "pending".
    sync_errors: collectSyncErrors(SKILL_PACKS_DIR),
    // Fleet-visibility fields (Machine Registry) — all optional server-side,
    // an older cloud that doesn't know them yet just ignores the extra keys.
    hostname: (() => { try { return os.hostname(); } catch (_) { return ""; } })(),
    username: (() => { try { return os.userInfo().username; } catch (_) { return ""; } })(),
    os_release: (() => { try { return os.release(); } catch (_) { return ""; } })(),
    os_arch: (() => { try { return os.arch(); } catch (_) { return ""; } })(),
    // Feature flags only, not a tool list — see docs/TRD.md Machine Registry note.
    capabilities: {
      max_recovery_tier: String(MAX_RECOVERY_TIER),
      update_channel: process.env.CONXA_UPDATE_CHANNEL || "stable",
    },
  });
  await new Promise((resolve) => {
    const req = httpClient.request(`${CONXA_API}/api/v1/telemetry/runtime-start`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
    }, (res) => { res.resume(); resolve(); });
    req.on("error", resolve);
    req.setTimeout(5000, () => { req.destroy(); resolve(); });
    req.write(body);
    req.end();
  });
}
