"use strict";
const { chromium } = require("./host_bridge").hostRequire("playwright");
const fs   = require("fs");
const path = require("path");
const hostBrowser = require("./host_browser");
const sessions = require("./browser_session");
const pageScripts = require("./page_scripts");
const { evalOn, EVAL_TIMED_OUT } = require("./page_eval");
const loginSignals = require("./login_signals");
const loginDecisionLog = require("./login_decision_log");

// How often a pending sign-in is checked, how long after success we wait for post-login JS to write
// its tokens before capturing, and how long a closed login tab may still be followed by a success
// elsewhere before it counts as abandoned. Env-overridable so tests don't sit through the defaults.
const LOGIN_POLL_MS = Number(process.env.CONXA_LOGIN_POLL_MS) || 1500;
const LOGIN_CLOSE_GRACE_MS = Number(process.env.CONXA_LOGIN_CLOSE_GRACE_MS) || 3000;
// The backup rule's agreement window ("two or more lookouts for N seconds") and the point at
// which an inconclusive sign-in gets a diagnostic log line — see login_signals.js's ladder and
// docs/artifacts/login-desk.html's "Who do we believe?" section. LOGIN_HUMAN_PROMPT_MS has no UI
// to trigger yet (Phase 2 — an "I'm done signing in" control); until then it only marks when to
// log that a wait is taking unusually long, so a stuck sign-in is visible in diagnostics well
// before the LOGIN_WAIT_MS ceiling gives up on it.
const LOGIN_BACKUP_AGREE_MS = Number(process.env.CONXA_LOGIN_BACKUP_AGREE_MS) || loginSignals.DEFAULT_BACKUP_AGREE_MS;
// AUTH-6: has a UI trigger now (Conxa Execute's per-login-tab "Done" button, and the
// `conxa-runtime login-done <key>` CLI subcommand for every other client) — see
// _checkHumanOverride below. Still only logs `login_signal_inconclusive` on its own; the human
// override is a separate, explicit signal, not something this deadline triggers by itself.
const LOGIN_HUMAN_PROMPT_MS = Number(process.env.CONXA_LOGIN_HUMAN_PROMPT_MS) || 25000;
// Timekeeper: how often the ticket signature is resampled while waiting for calm, and the hard
// cap on how long it will wait for two consecutive samples to match before saving anyway (some
// sites never go fully quiet). Mirrors settle.js's own two-consecutive-matches shape, applied to
// a different signal (ticket names/counts, not DOM shape). The budget falls back to the OLD flat
// settle delay's env var — anyone already tuning CONXA_LOGIN_SETTLE_MS down for fast tests gets
// the same effect on the new calm-wait without having to touch it.
const LOGIN_TIMEKEEPER_POLL_MS = Number(process.env.CONXA_LOGIN_TIMEKEEPER_POLL_MS) || 500;
const LOGIN_TIMEKEEPER_BUDGET_MS =
  Number(process.env.CONXA_LOGIN_TIMEKEEPER_BUDGET_MS) || Number(process.env.CONXA_LOGIN_SETTLE_MS) || 5000;

// env.js is authoritative (see server.js note): process.env is already normalized
// under the host exe; the resolve() fallback only serves standalone dev mode.
const _envInfo       = global.__conxaEnv || require("./env").resolve();
const CONXA_DIR      = process.env.CONXA_DIR || _envInfo.conxaDir;
const CONXA_DATA_DIR = process.env.CONXA_DATA_DIR || _envInfo.dataDir;
const SESSIONS_DIR = path.join(CONXA_DATA_DIR, "cache", "sessions");
// Used only by _rejectReasonForProtectedUrl (an interactive-login *capture* sanity check — did the
// window get closed on an obvious login/auth page instead of a real app page?), never by sign-in
// DETECTION — that's the signed-out baseline compare (_signedOutBaseline / isSignedInAgainstBaseline).
const LOGIN_URL_PATTERNS = [
  "login", "signin", "sign-in", "auth", "oauth", "sso",
  "session/new", "account/login", "accountchooser", "account-chooser",
];

// AUTH-6: "I'm done signing in" — a plain file-drop signal, same shape as handover.js's own resume
// mechanism (existence-only, unauthenticated — same OS user, same machine, no new exposure that
// doesn't already exist) but rooted at CONXA_DIR rather than CONXA_DATA_DIR: CONXA_DIR is what
// Conxa Execute's Electron process actually resolves and forwards (runtime_path.js + mcp_client.js),
// while CONXA_DATA_DIR is computed only inside this runtime process and never travels back to
// Execute — rooting there would leave Execute's own button unable to find the right directory.
const LOGIN_DONE_DIR = path.join(CONXA_DIR, "login-done");
function _humanOverrideFile(key) {
  return path.join(LOGIN_DONE_DIR, `${key}.cmd`);
}
// Checked once per poll tick in both wait functions, BEFORE the ladder verdict — a hit is an
// unconditional "save", bypassing judge/backup-agreement/even a currently-showing pause sign. The
// human is the authority of last resort (docs/artifacts/login-desk.html's third door into save).
// Consumes the file on the way past, same as handover.js's file-drop signal.
function _checkHumanOverride(key) {
  const file = _humanOverrideFile(key);
  try {
    if (!fs.existsSync(file)) return false;
    fs.unlinkSync(file);
    return true;
  } catch (_) {
    return false;
  }
}

// Shared context options for any Chromium instance that talks to a real target site
// (interactive login, session validation) — matches a normal desktop browser closely
// enough that bot-protected sites (Render, Google OAuth) don't reject it outright.
const STEALTH_CONTEXT_OPTIONS = {
  userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  viewport: { width: 1280, height: 720 },
  locale: "en-US",
  timezoneId: "America/New_York",
};

async function _maskAutomation(page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    Object.defineProperty(navigator, "plugins", { get: () => [1, 2, 3] });
  });
}

function _loadPack(workspace_id) {
  const packPath = path.join(CONXA_DIR, "skill-packs", workspace_id, "pack.json");
  try { return JSON.parse(fs.readFileSync(packPath, "utf8")); } catch (_) { return {}; }
}

// The navigable prefix of a success_url pattern — "https://drive.google.com/{}" is a matcher, not
// an address; goto() on it lands on /%7B%7D. Python twin: recorder/session.py::_probe_group_sessions_sync (which navigates to login_url).
// Still used to pre-navigate a freshly authenticated run to a sensible starting page (server.js) —
// NOT for sign-in detection, which no longer needs success_url at all (see _signedOutBaseline).
function _successPrefix(app) {
  return String(app.success_url || "").split("{}", 1)[0];
}

// Where to send a login tab: always the app's own login_url. (Previously this special-cased
// cross-host sign-in — accounts.google.com fronting drive.google.com — by opening the app's
// success URL instead, on the theory that the provider's own return-to parameter would land the
// user somewhere a host-based check could see. That reasoning doesn't hold up: it required a
// hardcoded list of "which hosts are third-party providers" that was necessarily incomplete and,
// for GitHub, actively wrong — GitHub IS the app here, not a provider fronting one. Detection no
// longer depends on where the user ends up: it re-probes login_url itself, before vs. after, via
// the signed-out baseline compare — so opening login_url directly works uniformly for every app.)
function _loginEntryUrl(app) {
  return app.login_url;
}

// ─── Multi-app group auth ─────────────────────────────────────────────────
// A WorkflowGroup's session files are keyed `${workspace_id}__${appId}` (see
// auth_manager.js — every save/load function already takes the key as its
// first argument, so keying is purely a call-site convention). Merging N
// per-app storageStates into one lets a single Chromium context stay signed
// in to every app the group's workflows need. Python twin:
// packages/conxa-core/conxa_core/storage/storage_state.py::merge_storage_states.
function mergeStorageStates(states) {
  const cookies = [];
  const seenCookies = new Set();
  const originsByUrl = new Map();

  for (const state of states) {
    if (!state) continue;
    for (const cookie of state.cookies || []) {
      const key = `${cookie.name}|${cookie.domain}|${cookie.path}`;
      if (seenCookies.has(key)) continue;
      seenCookies.add(key);
      cookies.push(cookie);
    }
    for (const origin of state.origins || []) {
      if (!origin.origin) continue;
      const existing = originsByUrl.get(origin.origin);
      if (!existing) {
        originsByUrl.set(origin.origin, { origin: origin.origin, localStorage: [...(origin.localStorage || [])] });
        continue;
      }
      const merged = new Map(existing.localStorage.map((item) => [item.name, item]));
      for (const item of origin.localStorage || []) merged.set(item.name, item);
      existing.localStorage = [...merged.values()];
    }
  }

  return { cookies, origins: [...originsByUrl.values()] };
}

// Does a cookie/origin `domain` (".salesforce.com" parent-scoped, or "login.salesforce.com"
// host-only) belong with `host`? Same site, or either side a dot-boundary subdomain of the other —
// bare endsWith would let "evilforce.com" match "force.com".
function _domainBelongsToHost(domain, host) {
  const d = String(domain || "").replace(/^\./, "").toLowerCase();
  const h = String(host || "").toLowerCase();
  return !!d && !!h && (d === h || h.endsWith("." + d) || d.endsWith("." + h));
}

// Slice ONE app's storageState out of a shared-context snapshot. Logins land in the same context
// the workflow runs in, so it holds every app's cookies at once; writing it whole into each app's
// file would let a stale sibling copy shadow a fresh login on the next merge (mergeStorageStates
// keeps the FIRST cookie seen per name|domain|path). Owned = domains/origins `previous` already
// had, plus anything on `hosts` (the app's login/success hosts) — the fallback is what makes an
// app's very first login attributable, when there is no previous file. Anything else is dropped —
// unless `claimedElsewhere` (the OTHER group apps' hosts) is given, in which case a cookie no app
// claims (an SSO identity provider's, say) is kept too: the old separate login window saved it, and
// it is what makes the next re-login one click. Python twin:
// packages/conxa-core/conxa_core/storage/storage_state.py::refresh_app_state (no `hosts` there).
function refreshAppState(previous, current, hosts = [], claimedElsewhere = null) {
  const ownedDomains = new Set(((previous && previous.cookies) || []).map((c) => c.domain));
  const ownedOrigins = new Set(((previous && previous.origins) || []).map((o) => o.origin));
  const on = (list, domain) => list.some((h) => _domainBelongsToHost(domain, h));
  const keep = (domain, owned) => owned || on(hosts, domain) || (claimedElsewhere !== null && !on(claimedElsewhere, domain));
  return {
    cookies: ((current && current.cookies) || []).filter((c) => keep(c.domain, ownedDomains.has(c.domain))),
    origins: ((current && current.origins) || []).filter((o) => keep(_hostOf(o.origin), ownedOrigins.has(o.origin))),
  };
}

// Resolve a workspace's WorkflowGroup from pack.json's `groups` block. null means the pack has no
// `groups` (built before Workflow Groups) or not this group — getAuthContext reports that as a
// pack that needs rebuilding; captureReAuth and target_hosts.js treat it as "no group info".
// A group app's session key — see mergeStorageStates's comment above.
const _appKey = (workspace_id, app) => `${workspace_id}__${app.id}`;

function _resolveGroup(workspace_id, groupId) {
  const pack = _loadPack(workspace_id);
  const groups = Array.isArray(pack.groups) ? pack.groups : [];
  if (groups.length === 0) return null;
  if (groupId) return groups.find((g) => g.id === groupId) || null;
  return groups[0];
}

const authCache = require("./auth_cache")(SESSIONS_DIR);
const { AUTH_VALIDATION_TTL_MS, readValidationCache: _readValidationCache, writeValidationCache: _writeValidationCache, readLanding: _readLanding } = authCache;

function _mtimeOrZero(p) {
  try { return fs.statSync(p).mtimeMs; } catch (_) { return 0; }
}

// Stores only the origin — a navigation hint only needs "which site", not a specific path that
// risks going stale as the app's own routes change. Refuses to learn anything still login-shaped
// (loginSignals.looksLikeLoginAnswer) — that's never "the app", it's a hop through it.
function _writeLanding(key, url) {
  if (!url || _rejectReasonForProtectedUrl(url)) return;
  try {
    if (loginSignals.looksLikeLoginAnswer({ url, hasPasswordBox: false })) return;
    authCache.writeLanding(key, `${new URL(url).origin}/`);
  } catch (_) {}
}
// success_url (explicit) beats the learned landing address (observed) beats login_url (the only
// thing guaranteed to exist) — a navigation hint only, see the comment above. Sign-in detection
// never calls this.
function _protectedUrlOf(app, key) {
  return app.success_url || _readLanding(key) || app.login_url;
}

// Resolve a session for `key` (a bare workspace_id, or `${workspace_id}__${appId}` for a
// group app) WITHOUT launching a browser, preferring whichever of the encrypted/raw files
// was written most recently — not always the encrypted file. A raw file newer than the
// encrypted one means something other than this runtime process wrote a fresher session
// since the encrypted file was last produced (Build Studio's _stage_runtime_auth
// re-staging a freshly re-authenticated group app, or a save that fell back to
// saveRawSession) — reading the encrypted file unconditionally in that case serves a
// stale, already-expired session until the next process restart runs
// auth_manager.reencryptPlaintextSessions. A winning raw file is promoted into the
// encrypted one on the spot (same steps the startup sweep runs), so this is a one-time
// cost paid only by the first read after a fresh raw write, not every call. Returns the
// session's file path too so callers can key mtime-sensitive caches on it.
async function _loadSessionForKey(key, authManager, logFn) {
  const encPath = path.join(SESSIONS_DIR, `${key}_state.json`);
  const rawPath = path.join(SESSIONS_DIR, `${key}_raw_state.json`);
  const encMtime = _mtimeOrZero(encPath);
  const rawMtime = _mtimeOrZero(rawPath);

  if (rawMtime > 0 && rawMtime > encMtime) {
    let stored = null;
    try { stored = JSON.parse(fs.readFileSync(rawPath, "utf8")); } catch (_) {}
    if (stored) {
      if (authManager) {
        try {
          const token = await authManager.getSessionKey(key, logFn);
          if (authManager.saveEncryptedSession(key, stored, token, SESSIONS_DIR, logFn)) {
            fs.unlinkSync(rawPath);
            return { stored, sessionPath: encPath };
          }
        } catch (_) {}
      }
      return { stored, sessionPath: rawPath };
    }
  }

  if (encMtime > 0 && authManager) {
    try {
      const token = await authManager.getSessionKey(key, logFn);
      if (token) {
        const stored = authManager.loadDecryptedSession(key, token, SESSIONS_DIR);
        if (stored) return { stored, sessionPath: encPath };
      }
    } catch (_) {}
  }

  if (rawMtime > 0) {
    try {
      const stored = JSON.parse(fs.readFileSync(rawPath, "utf8"));
      return { stored, sessionPath: rawPath };
    } catch (_) {}
  }

  return { stored: null, sessionPath: null };
}

// Thin per-app wrapper over _loadSessionForKey — kept as its own name since
// getGroupAuthContext's caller comments and tests refer to "loading a group app session".
async function _loadGroupAppSession(workspace_id, app, authManager, logFn) {
  return _loadSessionForKey(_appKey(workspace_id, app), authManager, logFn);
}

/** Group-aware auth resolution. A group is the unit of sign-in: a workflow in it runs only when
 * EVERY app in the group is authenticated — there is no per-skill subset (the manifest's old
 * `required_apps` was removed; a caller's stray `opts.requiredAppIds` is ignored). The merged
 * context is seeded from every app's stored session, so a workflow that moves between apps
 * mid-run arrives signed in to each.
 *
 * Cost model: every app pays a live network validation (one shared headless browser for the
 * whole batch) unless its session validated within CONXA_AUTH_VALIDATION_TTL_MS (default 10 min, see
 * _readValidationCache), which skips that app's check.
 *
 * Any missing/expired app opens its own login window — ALL of them at once, not one at a time,
 * so a run with N broken apps costs the user one interruption instead of N. Each app is keyed
 * `${workspace_id}__${app.id}` in _pendingAuth, so the parallel opens never collide. */
async function getGroupAuthContext(workspace_id, group, authManager, opts = {}) {
  const headless = opts.headless !== false;
  const logFn = opts.logFn;
  const key = _sessionKey(workspace_id, opts, headless);

  // opts.authOnly: the `authenticate` tool only wants to know whether sign-in is complete (opening
  // login tabs for whatever is missing) — never a run context, so every site that would build
  // one returns { authenticated: true } instead.
  if (opts.authOnly && group.apps.length === 0) return { authenticated: true };
  if (group.apps.length === 0) {
    return _openSessionResult(key, undefined, headless, opts, { protectedUrl: "", sessionSource: "group-no-apps" });
  }

  // Load every app's stored session first (no browser), then decide who pays a
  // live network check (see the TTL-cache helpers above).
  const _groupAuthT0 = Date.now();
  if (logFn) logFn("info", "test_phase", { phase: `group_auth_validate_start:${group.apps.map((a) => a.name).join(",")}`, ms: 0 });
  const loaded = await Promise.all(group.apps.map((app) => _loadGroupAppSession(workspace_id, app, authManager, logFn)));
  const results = [];
  const batch = [];
  group.apps.forEach((app, i) => {
    const { stored, sessionPath } = loaded[i];
    if (!stored) {
      results.push({ app, stored: null, valid: false });
      return;
    }
    const key = _appKey(workspace_id, app);
    // A cookieless saved session never skips the live check: before AUTH-21 Execute saved sign-ins
    // with 0 cookies yet stamped them valid, and the run then met a signed-out site mid-flow.
    const hasCookies = Array.isArray(stored.cookies) && stored.cookies.length > 0;
    if (hasCookies && sessionPath && _readValidationCache(key, sessionPath)) {
      results.push({ app, stored, sessionPath, valid: true, fromCache: true });
      return;
    }
    // navUrl is always login_url — detection re-probes the sign-in address itself (see
    // _isAuthenticated/_signedOutBaseline). app: what login_signals.js::classifySession reads —
    // the learned auth definition (P0: Application Authentication Recording), if any, which then
    // decides alone, and success_url, which counts as proof when the landing page matches it.
    batch.push({
      key, stored, navUrl: app.login_url, sessionPath, label: app.name,
      app: { success_url: app.success_url, auth_definition: app.auth_definition },
    });
    results.push({ app, stored, sessionPath, valid: null, _batchIndex: batch.length - 1 });
  });
  // The `authenticate` tool with nothing to probe and nothing missing: report success without
  // building any browser at all.
  const missingStored = results.some((r) => !r.stored);
  if (opts.authOnly && batch.length === 0 && !missingStored) return { authenticated: true };

  // ONE Chromium for the whole session (see browser_session.js). Every stored session is seeded
  // into it; an app that still needs a live check is probed in a throwaway tab of it; an
  // app that is missing or expired gets a sign-in tab in it. So the browser a login lands in is the
  // browser the workflow runs in — nothing is re-seeded from disk and nothing is re-validated.
  // Newest session file first: mergeStorageStates keeps the FIRST cookie per name|domain|path, so a
  // stale sibling's copy of a shared cookie (an SSO identity provider's) must not beat a fresh login.
  const seed = mergeStorageStates(results.filter((r) => r.stored)
    .sort((a, b) => _mtimeOrZero(b.sessionPath) - _mtimeOrZero(a.sessionPath))
    .map((r) => r.stored));
  let opened;
  try {
    opened = await _openSession(key, seed, headless, opts);
  } catch (e) {
    // Chromium itself won't start (missing / mid-install). If an app needed the browser for
    // a check or a sign-in, report it the way a failed login-window launch always was — a
    // `launch_failed` sign-in result callers surface at once instead of waiting on a window that
    // never opened. With nothing to check it is a plain run-context failure: propagate.
    const needy = results.filter((r) => !r.valid);
    if (needy.length === 0) throw e;
    return {
      authPending: true,
      loginUrl: needy[0].app.login_url,
      message: e.message,
      apps: needy.map((r) => ({
        id: r.app.id, name: r.app.name, loginUrl: r.app.login_url,
        authPending: true, key: _appKey(workspace_id, r.app), reason: "launch_failed", launchFailed: true, message: e.message,
      })),
    };
  }
  if (opened.refused) return opened;
  const { session, id } = opened;
  const protectedUrlOf = () => {
    const app = group.apps[0];
    return app ? _protectedUrlOf(app, _appKey(workspace_id, app)) : "";
  };
  // A warm session of this key that another call parked in the meantime already passed pre-flight.
  if (opened.reused) return _sessionResult(session, id, { sessionSource: "group" });

  try {
    if (batch.length > 0) {
      const outcomes = await _probeInSession(session, batch);
      for (const r of results) {
        if (r.valid !== null) continue;
        const entry = batch[r._batchIndex];
        r.valid = outcomes.get(entry.key);
        if (r.valid && entry.sessionPath) _writeValidationCache(entry.key, entry.sessionPath);
        delete r._batchIndex;
      }
    }
    const validatedCount = results.filter((r) => r.valid).length;
    if (logFn) logFn("info", "test_phase", {
      phase: `group_auth_validate_done:${validatedCount}/${results.length}_valid`,
      ms: Date.now() - _groupAuthT0,
      network_checked: batch.length,
      ttl_cached: results.filter((r) => r.fromCache).length,
    });
    const missingRequired = results.filter((r) => !r.valid);

    if (missingRequired.length === 0) {
      if (opts.authOnly) {
        // Nothing to run — park the warm, signed-in browser for the next call (an Execute view is
        // released outright: it belongs to that one run).
        sessions.release(id, { closeNow: Boolean(session.hostOwned) });
        return { authenticated: true };
      }
      return _sessionResult(session, id, { protectedUrl: protectedUrlOf(), sessionSource: "group" });
    }

    const names = missingRequired.map((r) => r.app.name);
    const plural = missingRequired.length === 1;
    const message =
      `This workflow belongs to the ${group.name} group and requires authentication to ` +
      `${group.apps.length} application${group.apps.length === 1 ? "" : "s"}. Sign in to ` +
      `${names.join(", ")} in the window${plural ? "" : "s"} that just opened, then run the skill again.`;
    const alreadyOpenMessage =
      `Sign-in for ${names.join(", ")} is already waiting in ${plural ? "an open window" : "open windows"} ` +
      `— finish signing in there, then run the skill again.`;

    // An unattended (scheduled) run has nobody to sign in: opening a login window on a machine no one
    // is sitting at only strands it. Report which app needs a person instead, and close the session.
    if (opts.noPrompt) {
      sessions.release(id, { closeNow: true });
      return {
        authPending: false,
        expiredApps: names,
        message: `${names.join(", ")} ${plural ? "is" : "are"} not signed in — the saved sign-in is missing or has expired — and a scheduled run has nobody to sign in. ` +
          `Sign in on this machine (run the skill once, or use the authenticate tool); it will run on schedule again after that.`,
      };
    }

    // A headless browser cannot show a login page, so an unattended run that needs a human still
    // opens its own visible window (the one case that costs a second Chromium) and this session —
    // useless once the login lands somewhere else — is closed rather than kept warm.
    const inSession = !headless;
    if (!inSession) sessions.release(id, { closeNow: true });

    // While sign-in is pending the session stays leased (it holds the login tabs). The last app to
    // settle parks it warm — the caller's next getCachedBrowser reuses it, already signed in — or
    // closes it if anything went wrong or a login ended up in someone else's window.
    const hold = { remaining: missingRequired.length, clean: true };
    const settleOne = () => {
      if (--hold.remaining > 0 || !inSession) return;
      sessions.release(id, { closeNow: !hold.clean });
    };
    const hostsOf = (a) => [_hostOf(a.login_url), _hostOf(a.success_url)].filter(Boolean);
    const pendings = await Promise.all(missingRequired.map((r) =>
      beginInteractiveAuth(_appKey(workspace_id, r.app), _loginEntryUrl(r.app), {
        label: r.app.name,
        storedState: r.stored,
        protectedUrl: _protectedUrlOf(r.app, _appKey(workspace_id, r.app)),
        authManager,
        sessionsDir: SESSIONS_DIR,
        logFn,
        runId: opts.runId,
        authDefinition: r.app.auth_definition,
        ...(inSession ? {
          session,
          hosts: hostsOf(r.app),
          claimedElsewhere: group.apps.filter((a) => a.id !== r.app.id).flatMap(hostsOf),
          onSettled: (h) => { if (h.outcome !== "captured") hold.clean = false; settleOne(); },
        } : {}),
      })
    ));
    if (inSession) {
      // Apps whose login is not ours to settle (already open in another session's window, or it
      // never launched) count down here instead; the session is then not worth keeping.
      for (const p of pendings) {
        if (p.reason === "already_open" || p.reason === "launch_failed" || !p.authPending) { hold.clean = false; settleOne(); }
      }
    }
    const allPending = pendings.every((p) => p.authPending);
    // A real launch failure (chromium missing/mid-install, etc.) on any one app must win
    // over the generic "windows just opened" message — it's the actionable diagnosis.
    const failed = pendings.find((p) => p.launchFailed);
    return {
      authPending: allPending,
      loginUrl: pendings[0] && pendings[0].loginUrl,
      message: failed ? failed.message
        : (allPending ? (pendings.every((p) => p.reason === "already_open") ? alreadyOpenMessage : message)
          : pendings.find((p) => !p.authPending)?.message),
      apps: missingRequired.map((r, i) => ({ id: r.app.id, name: r.app.name, loginUrl: r.app.login_url, ...pendings[i] })),
    };
  } catch (e) {
    sessions.release(id, { closeNow: true });
    throw e;
  }
}

async function _persistSession(workspace_id, state, authManager, sessionsDir, logFn) {
  if (authManager) {
    try {
      const sessionKey = await authManager.getSessionKey(workspace_id, logFn);
      const encrypted = authManager.saveEncryptedSession(workspace_id, state, sessionKey, sessionsDir, logFn);
      if (!encrypted) authManager.saveRawSession(workspace_id, state, sessionsDir, logFn);
    } catch (_) {
      authManager.saveRawSession(workspace_id, state, sessionsDir, logFn);
    }
  } else {
    fs.mkdirSync(sessionsDir, { recursive: true });
    fs.writeFileSync(path.join(sessionsDir, `${workspace_id}_raw_state.json`), JSON.stringify(state, null, 2), { mode: 0o600 });
  }
}

// The signed-in state of a login context, in storageState shape. A host-owned (Conxa Execute)
// context can't use context.storageState(): for every origin visited by a page that has since
// closed (the judge's probe tabs, an identity provider's redirect hops) Playwright opens a hidden
// page to read its localStorage, and Electron's CDP can't create one (host_browser.js header) —
// it threw, the sign-in was never saved, and the run never started (AUTH-20). And context.cookies()
// reads Electron's default session, not the run's own partition, so it came back empty (AUTH-21).
// So: cookies through a CDP session on one of this login's own pages (hostBrowser.pageCookies),
// localStorage from those same pages — never context.pages(), which also holds other runs' views
// and Execute's own UI.
// ponytail: localStorage only from this login's still-open pages — an app that keeps its login in
// localStorage of an origin whose page already closed loses it; open a background tab per origin
// (hostBrowser.openTab) if one ever needs that.
async function _captureState(context, hostOwned, pages = []) {
  if (!hostOwned) return context.storageState();
  const live = pages.filter((p) => { try { return !p.isClosed(); } catch (_) { return false; } });
  if (!live.length) throw new Error("no open page of this sign-in left to read it from");
  const origins = new Map();
  for (const p of live) {
    try {
      const o = await evalOn(p, pageScripts.originStorage, undefined, 2000);
      if (o && o !== EVAL_TIMED_OUT && o.origin && o.origin !== "null" && o.localStorage.length) origins.set(o.origin, o);
    } catch (_) {}
  }
  return { cookies: await hostBrowser.pageCookies(live[0]), origins: [...origins.values()] };
}

// ─── Browser sessions (registry in browser_session.js) ─────────────────────────
// EVERY live Chromium — headless or visible — is a registered session, so the 5-instance cap holds
// for all of them. A session is a LEASE (RT-3): a run holds it `busy` for the whole execution (and
// through a parked recovery); the idle timer only starts once it is released; two concurrent runs
// never share one live context (tabs.js's popup registry and the per-run download listener both
// assume exactly one run per context), so a call that finds its session leased gets its own.
//
// A parked session skips pre-flight on reuse — it only proves the browser is still alive, not that
// the logins are, a real staleness window (authentication is pre-flight-only, so an expiry inside
// it surfaces mid-run instead). Kept short rather than re-validating on every hit, which would add
// a probe's worth of latency to the common nothing-expired case. A visible run's session is not
// parked: server.js closes its browser at teardown and release() then drops the dead entry at once.

// A session is one Chromium keyed by what it is authenticated for — the group, whose every app it
// is signed in to. A host-owned (Execute) session also carries its run id — its views live under
// that id in Execute's panel, so another run must not inherit them.
function _sessionKey(workspace_id, opts, headless) {
  const host = !headless && opts.runId && hostBrowser.endpoint() ? `::host:${opts.runId}` : "";
  return `${workspace_id}::${opts.groupId || ""}::${headless ? "h" : "w"}${host}`;
}

// Registers a new Chromium (or Execute view) for `key`, seeded with `stored`. Reuses a parked one if
// there is one; refuses (-> { refused, message }) when every slot is leased. The session object
// carries its own close(), which is the ONE teardown seam (teardownExecBrowser).
function _openSession(key, stored, headless, opts = {}) {
  return sessions.acquire(key, {
    create: async () => {
      const built = await _buildExecContext(stored, headless, opts);
      const session = { ...built, hostRunId: built.hostOwned ? opts.runId : undefined };
      session.close = () => teardownExecBrowser({
        browser: session.browser, context: session.context, hostOwned: session.hostOwned, runId: session.hostRunId,
      });
      return session;
    },
  });
}

// What every caller of getCachedBrowser/getAuthContext gets for a ready session. `leaseKey` is the
// registry id server.js hands back to releaseCachedBrowser.
function _sessionResult(session, id, extra = {}) {
  if (extra.protectedUrl !== undefined) session.protectedUrl = extra.protectedUrl;
  return {
    browser: session.browser, context: session.context, page: session.page,
    hostOwned: session.hostOwned, hostRunId: session.hostRunId,
    protectedUrl: session.protectedUrl || "", leaseKey: id, ...extra,
  };
}
async function _openSessionResult(key, stored, headless, opts, extra) {
  const r = await _openSession(key, stored, headless, opts);
  return r.refused ? r : _sessionResult(r.session, r.id, extra);
}

// A throwaway or sign-in tab inside a session. A launched context makes its own page; Execute's
// Electron target can't (see host_browser.js), so it asks Execute for a labelled view instead and
// keeps the tab id, which is what lets just this one view be closed later.
async function _newSessionPage(session, { label, focus, loginKey } = {}) {
  if (session.hostOwned) {
    const { page, tabId } = await hostBrowser.openTab({ context: session.context, runId: session.hostRunId, label, focus, loginKey });
    return { page, hostTabId: tabId };
  }
  return { page: await session.context.newPage() };
}
async function _closeSessionPage(session, { page, hostTabId }) {
  try { if (!page.isClosed()) await page.close(); } catch (_) {}
  if (session.hostOwned && hostTabId) await hostBrowser.release({ runId: session.hostRunId, tabId: hostTabId });
}

// Live-check stored sessions in throwaway tabs of the session's own context — no separate browser.
// Each entry: { key, navUrl, label, app }. A failure on one entry marks just that entry invalid.
// _isAuthenticated owns the navigation (it attaches its response listener first).
async function _probeInSession(session, entries) {
  const outcomes = new Map();
  await Promise.all(entries.map(async (entry) => {
    let tab;
    try {
      tab = await _newSessionPage(session, { label: entry.label, focus: false });
      outcomes.set(entry.key, await _isAuthenticated(session, tab.page, entry.navUrl, entry.app));
    } catch (_) {
      outcomes.set(entry.key, false);
    } finally {
      if (tab) await _closeSessionPage(session, tab);
    }
  }));
  return outcomes;
}

async function getCachedBrowser(workspace_id, authManager, opts = {}) {
  const headless = opts.headless !== false; // default true
  // A parked session of this key skips pre-flight entirely — it only proves the browser is still
  // alive, not that the logins are, which is a real staleness window kept short by the registry's
  // idle timeout (authentication is meant to be pre-flight-only).
  const hit = sessions.reuse(_sessionKey(workspace_id, opts, headless));
  if (hit) return { ..._sessionResult(hit.session, hit.id), cached: true };
  const result = await getAuthContext(workspace_id, authManager, {
    headless, logFn: opts.logFn, groupId: opts.groupId, runId: opts.runId, noPrompt: opts.noPrompt,
  });
  // authPending / refused mean no browser was handed out — nothing to lease.
  return { ...result, cached: false, leaseKey: result.leaseKey || null };
}

// Ends a run's lease on a session (no-op for a null leaseKey). Re-arms the idle timer from this
// moment, not from when the lease was acquired, so a long execution is never reaped mid-run.
function releaseCachedBrowser(leaseKey, opts) {
  if (!leaseKey) return;
  sessions.release(leaseKey, opts);
}

// ─── Session management ───────────────────────────────────────────────────────

function _isBlankUrl(url) {
  const value = String(url || "").trim().toLowerCase();
  return !value || value === "about:blank" || value === "chrome://newtab/";
}

function _rejectReasonForProtectedUrl(url) {
  const value = String(url || "").trim();
  if (_isBlankUrl(value)) {
    return "No authenticated page URL was captured. Log in, navigate to the page where workflows should start, then close Chromium.";
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch (_) {
    return "The captured protected URL is not valid.";
  }
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
    return "The captured protected URL must be an http or https page.";
  }
  const lowered = value.toLowerCase();
  if (LOGIN_URL_PATTERNS.some(marker => lowered.includes(marker))) {
    return "The final page still looks like a login/auth page. Navigate to the authenticated app page, then close Chromium.";
  }
  return "";
}

// ─── Signed-out baseline ───────────────────────────────────────────────────────────────────────
// What a login URL genuinely looks like with NO cookies at all — fetched live, from a throwaway
// incognito context, not guessed from a hostname/redirect/IdP list. This is what replaced host
// matching for deciding "is this session signed in": compare a probe taken WITH the session's
// cookies against this baseline (login_signals.js::isSignedInAgainstBaseline). A login page's
// shape rarely changes, and this runtime process stays up across many execute_skill calls, so the
// fetch is cached per entryUrl for AUTH_VALIDATION_TTL_MS. ponytail: process-memory cache only,
// not disk-persisted like _authValidationCachePath — a runtime restart just re-pays one fetch per
// app; upgrade to a disk cache if cold starts prove costly.
const _baselineCache = new Map(); // entryUrl -> { snapshot, ts }
async function _signedOutBaseline(session, entryUrl) {
  // Execute's shared CDP browser can't create a second isolated context (Target.createBrowserContext
  // is unsupported — see _proveSession's identical host-owned limitation), so there is no way to
  // fetch a truly cookie-less snapshot there. isSignedInAgainstBaseline treats a null baseline as
  // an accepted, weaker fallback (not-login-shaped is all it can tell).
  if (session.hostOwned) return null;
  const hit = _baselineCache.get(entryUrl);
  if (hit && Date.now() - hit.ts < AUTH_VALIDATION_TTL_MS) return hit.snapshot;
  let ctx;
  try {
    ctx = await session.browser.newContext(STEALTH_CONTEXT_OPTIONS); // no storageState = genuinely signed out
    const page = await ctx.newPage();
    await page.goto(entryUrl, { waitUntil: "domcontentloaded", timeout: 15000 });
    const hasPasswordBox = await evalOn(page, pageScripts.passwordBoxProbe, undefined, 1200);
    const snapshot = { url: page.url(), hasPasswordBox: hasPasswordBox === true };
    _baselineCache.set(entryUrl, { snapshot, ts: Date.now() });
    return snapshot;
  } catch (_) {
    return null;
  } finally {
    if (ctx) await ctx.close().catch(() => {});
  }
}

// ── Learned auth definitions (P0: Application Authentication Recording) ────────────────────────
// The Playwright-touching half of a login_signals.js::evaluateAuthDefinition observation — mirrors
// conxa_compile/auth_learning.py's _navigate_and_observe_sync exactly (same three-context contrast
// idea, see that module's docstring), reading only what evaluate() consumes: no cookie values, no
// response bodies, no query string/fragment retained anywhere. Owns its own navigation to
// definition.probe_url so the response listener is attached before anything loads — a caller that
// already navigated `page` elsewhere gets overridden, which in practice is a no-op since
// probe_url is always the app's login_url, the same address every existing call site already
// used as entryUrl.
async function _gatherDefinitionObservation(page, probeUrl) {
  const responses = [];
  const onResponse = (resp) => {
    try {
      if (responses.length >= 30) return;
      const rt = resp.request().resourceType();
      if (rt !== "xhr" && rt !== "fetch") return;
      const u = new URL(resp.url());
      responses.push({ method: resp.request().method(), path: u.pathname || "/", status: resp.status() });
    } catch (_) {}
  };
  page.on("response", onResponse);
  try {
    await page.goto(probeUrl, { waitUntil: "domcontentloaded", timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1500).catch(() => {}); // let SPA XHRs fired on load settle
  } finally {
    try { page.off("response", onResponse); } catch (_) {}
  }

  let passwordBox = false;
  try { passwordBox = (await evalOn(page, pageScripts.passwordBoxProbe, undefined, 1200)) === true; } catch (_) {}
  let otpLike = false;
  try {
    const pause = await evalOn(page, pageScripts.pauseSignProbe, undefined, 1200);
    if (pause !== EVAL_TIMED_OUT) otpLike = loginSignals.looksPaused(pause);
  } catch (_) {}
  let markers = [];
  try {
    const m = await evalOn(page, pageScripts.authDefinitionMarkersProbe, undefined, 1200);
    if (m !== EVAL_TIMED_OUT && Array.isArray(m)) markers = m;
  } catch (_) {}
  let cookieNames = [];
  // The page's own cookie jar — under Execute, context.cookies() reads the wrong one (AUTH-21).
  try { cookieNames = (await hostBrowser.pageCookies(page)).map((c) => c.name); } catch (_) {}

  return {
    final_url: page.url(),
    password_box: passwordBox,
    otp_like: otpLike,
    markers,
    responses,
    cookie_names: cookieNames,
  };
}

async function _evaluateAgainstDefinition(page, definition) {
  const observation = await _gatherDefinitionObservation(page, definition.probe_url || definition.probeUrl);
  return loginSignals.evaluateAuthDefinition(definition, observation);
}

// The definition-aware judge: same throwaway-tab-of-the-shared-context shape as
// _snapshotLoginEntry, but returns a full evaluateAuthDefinition verdict instead of a raw
// snapshot — _waitForSessionLogin's ladder branch maps "yes"/"no"/"unsure" itself.
async function _probeAuthDefinitionVerdict(session, definition) {
  let tab;
  try {
    tab = await _newSessionPage(session, { label: "verify", focus: false });
    return await _evaluateAgainstDefinition(tab.page, definition);
  } catch (_) {
    return { verdict: "unsure", classes: [] };
  } finally {
    if (tab) await _closeSessionPage(session, tab);
  }
}

// A page that still looks like a sign-in page may be mid client-side redirect: re-read it every
// 200 ms for up to 3 s. Updates `observation` in place; the markers are re-read too, since the ones
// gathered on the login page would otherwise mark the page it redirected to as signed out.
// Python twin: recorder/session.py::_settle_off_login.
async function _settleOffLogin(page, observation) {
  const deadline = Date.now() + 3000;
  while (loginSignals.looksLikeSessionLogin(observation) && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 200));
    let passwordBox = false;
    try { passwordBox = (await evalOn(page, pageScripts.passwordBoxProbe, undefined, 1200)) === true; } catch (_) {}
    let markers = [];
    try {
      const m = await evalOn(page, pageScripts.authDefinitionMarkersProbe, undefined, 1200);
      if (m !== EVAL_TIMED_OUT && Array.isArray(m)) markers = m;
    } catch (_) {}
    observation.final_url = page.url();
    observation.password_box = passwordBox;
    observation.markers = markers;
  }
}

// Is this app's saved session still signed in? Navigates `page` to the app's login address and
// applies login_signals.js::classifySession, the one rule Build Studio's Check now / Check all /
// recording gate share (recorder/session.py::_probe_group_sessions_sync). `app`: { success_url,
// auth_definition }. Only a positive "ready" counts; "expired" and "unverified" are both false.
async function _isAuthenticated(session, page, entryUrl, app = {}) {
  try {
    const definition = app.auth_definition;
    const observation = await _gatherDefinitionObservation(
      page, (definition && (definition.probe_url || definition.probeUrl)) || entryUrl);
    // A learned rule decides alone; the settle loop and the baseline are the generic path's.
    if (!definition) await _settleOffLogin(page, observation);
    const baseline = definition ? null : await _signedOutBaseline(session, entryUrl);
    const state = loginSignals.classifySession(observation, baseline, app, { noBaselineOk: Boolean(session.hostOwned) });
    return state === "ready";
  } catch (_) {
    return false;
  }
}

// ── Multi-signal login-completion detection ─────────────────────────────────────────────────
// See login_signals.js's header for the vocabulary. This section is the Playwright-touching
// half (signal gathering); login_signals.js is the pure decision half. Shared by both
// _waitForSessionLogin and _waitForInteractiveAuth below so the two never grow independently
// drifting copies of the same rule.

// Judge: re-ask the login entry URL in the background, in a throwaway tab of the SAME context —
// once before sign-in even starts (the "before" snapshot) and again whenever a lookout fires
// afterwards — and hand both snapshots to login_signals.js::judgeFromSnapshots for the actual
// before/after compare (docs/artifacts/login-desk.html "Ask for the login page again"). The "after"
// call is gated by the caller to run only when a lookout has just fired — never on a timer — so a
// bot-protected site's login page isn't hit any more than the person's own navigation already hits
// it. Returns `{ url, hasPasswordBox }`, or null if the probe itself failed (network hiccup,
// timeout) — the pure layer treats that as "can't tell" either way.
async function _snapshotLoginEntry(session, entryUrl) {
  let tab;
  try {
    tab = await _newSessionPage(session, { label: "verify", focus: false });
    await tab.page.goto(entryUrl, { waitUntil: "domcontentloaded", timeout: 15000 });
    const hasPasswordBox = await evalOn(tab.page, pageScripts.passwordBoxProbe, undefined, 1200);
    return { url: tab.page.url(), hasPasswordBox: hasPasswordBox === true };
  } catch (_) {
    return null;
  } finally {
    if (tab) await _closeSessionPage(session, tab);
  }
}

// The judge's actual verdict: judgeFromSnapshots' relative before/after compare, falling back to
// the ABSOLUTE signed-out baseline compare (_signedOutBaseline / isSignedInAgainstBaseline) when
// before/after read the same address — the case a fast sign-in (no visible URL change on the login
// entry itself between the pre-window snapshot and this ask) leaves genuinely inconclusive by the
// relative compare alone. The baseline fallback only ever resolves "yes", never "no": a wrong or
// stale cached baseline must not actively BLOCK the backup-agreement rule (ladderVerdict's
// judge==="no" branch waits outright), it should just leave the ladder to decide some other way.
async function _judgeVerdict(session, entryUrl, beforeSnapshot, after) {
  const relative = loginSignals.judgeFromSnapshots(beforeSnapshot, after);
  if (relative !== null) return relative;
  if (!after) return null;
  const baseline = await _signedOutBaseline(session, entryUrl);
  return loginSignals.isSignedInAgainstBaseline(baseline, after) ? "yes" : null;
}

// One tick's lookout read across every currently-live candidate page owned by this login. Mutates
// `state` in place (firedAt for the backup rule, sawPasswordBox so "gone" can only fire after a
// box was actually seen, ticketBaseline for the newTickets lookout) since this runs inside a
// setInterval tick, where threading a return value back out is more awkward than the mutation it
// would produce anyway. Returns { paused } — the one signal every caller needs synchronously,
// right now, to gate saving.
// `ownPages` are pages we KNOW belong to this one login (the login tab and pages it opened — see
// both callers below); password-box, pause-sign and the ticket signature all read ONLY these,
// never a sibling app's own tabs sharing the same launched context (the "cold start, N apps" case).
async function _sampleLookouts(state, { ownPages, context }, nowMs) {
  let anyPasswordBox = false;
  let anyPaused = false;
  let anyLanded = false;
  let anyOnSignIn = false;
  const addresses = [];
  for (const p of ownPages) {
    try { addresses.push(loginSignals.addressOf(p.url())); } catch (_) {}
    let hasPw = false;
    try {
      const probe = await evalOn(p, pageScripts.passwordBoxProbe, undefined, 1200);
      hasPw = probe === true;
      if (hasPw) anyPasswordBox = true;
    } catch (_) {}
    try {
      const pauseProbe = await evalOn(p, pageScripts.pauseSignProbe, undefined, 1200);
      if (pauseProbe !== EVAL_TIMED_OUT && loginSignals.looksPaused(pauseProbe)) anyPaused = true;
    } catch (_) {}
    // Landed: this page's OWN current state doesn't look like a login answer any more — an
    // instant, LEVEL check (unlike passwordGone/newTickets below, which need to have observed a
    // PRIOR state to detect a change), so a fast multi-step flow (password -> OTP -> app) that
    // races past the poll interval before any earlier state gets sampled still gets caught the
    // first tick that lands on the finished page. This is the generic, host-list-free replacement
    // for the old "journey" lookout's per-tick absolute check.
    try {
      const url = p.url();
      if (!loginSignals.looksLikeLoginAnswer({ url, hasPasswordBox: hasPw })) anyLanded = true;
      else {
        anyOnSignIn = true;
        // Mid-flow = this sign-in tab has moved on from its first screen (next step, password box
        // shown or gone) and is still on the sign-in. Latched: once the person has started, only
        // leaving the sign-in pages clears it. An untouched tab stays probe-able, so a sign-in
        // finished in some other tab is still caught by the judge.
        const sig = `${loginSignals.addressOf(url)}|${hasPw}`;
        if (!url.startsWith("about:")) {
          if (!state.startSig.has(p)) state.startSig.set(p, sig);
          else if (state.startSig.get(p) !== sig) state.progressed = true;
        }
      }
    } catch (_) {}
  }
  if (anyLanded && state.firedAt.landed === undefined) state.firedAt.landed = nowMs;

  // "Password box gone" only ever fires once a box was actually observed first — a login whose
  // FIRST screen has no password field (email-first, then password on the next screen) must not
  // fire this the instant it loads just because there is no box yet.
  if (state.sawPasswordBox && !anyPasswordBox && state.firedAt.passwordGone === undefined) {
    state.firedAt.passwordGone = nowMs;
  }
  if (anyPasswordBox) state.sawPasswordBox = true;

  // New tickets: the context's cookie jar / storage keys have changed shape since BEFORE the login
  // window opened — a fully generic backup signal (no host/URL reasoning at all), replacing the
  // old "journey" lookout's hardcoded identity-provider host list. state.ticketBaseline is seeded
  // by the caller (beginInteractiveAuth) from a snapshot taken before the window opens, same
  // timing as beforeSnapshot for the judge — NOT lazily from this function's first sample, which
  // would race a sign-in finished (in another tab) faster than one poll interval and silently
  // absorb it as "already there". A session with no pre-window snapshot available (the
  // standalone-browser fallback, no context yet) falls back to establishing it from the first
  // sample instead.
  const ticketsNow = await _ticketSignatureNow(context, ownPages[ownPages.length - 1]);
  if (!state.ticketBaseline) {
    state.ticketBaseline = ticketsNow;
  } else if (state.firedAt.newTickets === undefined && loginSignals.ticketsChanged(state.ticketBaseline, ticketsNow)) {
    state.firedAt.newTickets = nowMs;
  }

  return { paused: anyPaused, onSignIn: anyOnSignIn && state.progressed, address: addresses.join(" ") };
}

// Tickets signature for ONE representative page (whichever live candidate is passed in) plus the
// context-wide cookie jar — cookies are context-scoped in Playwright, so reading them once per
// tick from the context itself (rather than per-page) is both cheaper and unambiguous.
async function _ticketSignatureNow(context, page) {
  let cookieNames = [];
  try { cookieNames = (await context.cookies()).map((c) => c.name); } catch (_) {}
  let storageKeyCounts = {};
  if (page) {
    try {
      const counts = await evalOn(page, pageScripts.storageKeyCounts, undefined, 1200);
      if (counts !== EVAL_TIMED_OUT && counts) storageKeyCounts = counts;
    } catch (_) {}
  }
  return loginSignals.ticketSignature({ cookieNames, storageKeyCounts });
}

// Timekeeper: wait for the ticket signature to stop changing before saving — replaces a flat
// settle delay with "two consecutive samples match, or a budget runs out", the same shape
// settle.js already uses for a different signal (DOM shape, not ticket names/counts).
async function _waitForTicketsCalm(context, getRepresentativePage, {
  budgetMs = LOGIN_TIMEKEEPER_BUDGET_MS, pollMs = LOGIN_TIMEKEEPER_POLL_MS,
} = {}) {
  const deadline = Date.now() + budgetMs;
  // Deliberately does NOT take a baseline sample before the loop starts. The artifact's own "late
  // pass" walkthrough shows the FIRST timekeeper check after a ticket fires still reporting "still
  // changing" even though nothing has fired since — two SEPARATE post-decision polls must agree,
  // not "whatever was true the instant we decided to save" vs. the first poll. Without this, a
  // write that lands between the decision and the first poll (the exact "late pass" case) can
  // pass unnoticed: the pre-loop baseline and the first sample would both already include it,
  // reading as "unchanged" from tick one.
  let prev = null;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, Math.min(pollMs, Math.max(0, deadline - Date.now()))));
    const sig = await _ticketSignatureNow(context, getRepresentativePage());
    if (prev && loginSignals.sameTickets(prev, sig)) return;
    prev = sig;
  }
}

// Prover: verify the just-captured session actually authenticates before reporting success. A
// launched (non-host-owned) browser gets a true isolated re-test — a brand new context seeded
// with nothing but the captured `state` — which is what catches a site that keeps part of its
// login in page-memory that was never written to a cookie or localStorage (so it was never really
// captured at all). A host-owned (Conxa Execute) session can't do that: Electron's CDP target
// implements neither Target.createBrowserContext nor Target.createTarget (see host_browser.js),
// so there is exactly one context for every run in the panel. That branch re-tests in a fresh TAB
// of the same shared context instead — weaker (it still shares the context's other cookies) but
// still catches the same page-memory-only failure a same-context re-test can reach. A real,
// accepted gap for host-owned logins — see docs/TRD.md §4.7's CDP-context limitation.
// ponytail: reload-in-shared-context for host-owned, not a true isolated context — upgrade if
// Electron ever exposes Target.createBrowserContext.
async function _proveSession(entryUrl, { hostOwned, context, hostRunId, browser, state, definition }) {
  if (!entryUrl) return true;
  if (hostOwned) {
    let tab;
    try {
      const opened = await hostBrowser.openTab({ context, runId: hostRunId, label: "verify", focus: false });
      tab = opened;
      // hostOwned:true short-circuits _signedOutBaseline to null (no isolated context available —
      // see its own comment); _isAuthenticated falls back to its weaker, accepted best-effort check.
      // A definition, when present, sidesteps that gap entirely — it needs no isolated baseline.
      return await _isAuthenticated({ hostOwned: true }, tab.page, entryUrl, { auth_definition: definition });
    } catch (_) {
      return false;
    } finally {
      if (tab) {
        try { if (!tab.page.isClosed()) await tab.page.close(); } catch (_) {}
        if (tab.tabId) await hostBrowser.release({ runId: hostRunId, tabId: tab.tabId }).catch(() => {});
      }
    }
  }
  let freshCtx = null;
  try {
    freshCtx = await browser.newContext({ storageState: state });
    const page = await freshCtx.newPage();
    return await _isAuthenticated({ hostOwned: false, browser }, page, entryUrl, { auth_definition: definition });
  } catch (_) {
    return false;
  } finally {
    if (freshCtx) await freshCtx.close().catch(() => {});
  }
}

// AUTH-8 — "Signed in as ___": a best-effort, never-blocking positive confirmation. A miss (no
// plausible marker found, or the probe itself fails) is silent — this never becomes a warning,
// unlike the Prover above. Run once, on the page that actually landed the sign-in.
async function _probeAccountName(page) {
  if (!page) return "";
  try {
    const name = await evalOn(page, pageScripts.accountNameProbe, undefined, 1500);
    return name && name !== EVAL_TIMED_OUT ? name : "";
  } catch (_) {
    return "";
  }
}

// opts.runId, when set, is Execute's cue to borrow its own browser view instead of
// launching one (see host_browser.js). Only reachable when headless is false — a
// borrowed browser is always visible, by definition of what Execute uses it for —
// and only when CONXA_HOST_BROWSER_CDP is actually set, so every other client
// (Claude Desktop, the scheduler, the Build Studio sandbox) never even attempts it.
// A failure at any step (connect, view creation, seeding) falls through to the
// normal launch below and logs why — a run must never hard-fail over Execute's
// panel being unavailable.
async function _buildExecContext(stored, headless = false, opts = {}) {
  if (!headless && opts.runId && hostBrowser.endpoint()) {
    try {
      return await hostBrowser.acquire({ runId: opts.runId, storageState: stored, label: opts.label });
    } catch (e) {
      if (opts.logFn) opts.logFn("warn", "host_browser_fallback", { run_id: opts.runId, error: e.message });
    }
  }
  const browser = await chromium.launch({
    headless,
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const context = await browser.newContext({ storageState: stored, acceptDownloads: true });
  return { browser, context };
}

// Launch the headed login window and navigate to targetUrl. Kept separate from
// _waitForInteractiveAuth() so beginInteractiveAuth() can await just this fast part —
// a chromium.launch()/goto() failure surfaces to the caller immediately instead of
// being swallowed by a detached background task (see beginInteractiveAuth).
async function _openInteractiveAuthWindow(workspace_id, targetUrl, opts = {}) {
  const { storedState, runId, logFn, label, session } = opts;
  // In-session (the normal case): a sign-in is just another tab of the session's own context, so
  // the cookies it earns are already where the workflow will run. One trade-off, the same one the
  // host branch below documents: the context was built for the RUN, so it does not carry
  // STEALTH_CONTEXT_OPTIONS (a fixed UA/locale/timezone would change the environment the run
  // reports against its recording). A visible Chromium's own UA is not a headless tell, and
  // _maskAutomation still applies per page.
  if (session) {
    const tab = await _newSessionPage(session, { label, focus: true, loginKey: workspace_id });
    try {
      await _maskAutomation(tab.page);
      await tab.page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
    } catch (e) {
      await _closeSessionPage(session, tab);
      throw e;
    }
    return { session, loginPage: tab.page, hostTabId: tab.hostTabId };
  }
  // EXEC-41 Stage 2: a login window is just another headed browser, so it gets the exact
  // same host branch as a skill run's own context (see _buildExecContext) — Execute's panel,
  // not a separate OS window, when Execute is the client. One real, accepted trade-off: the
  // launch path below applies STEALTH_CONTEXT_OPTIONS (a desktop-Chrome UA, viewport, locale,
  // timezone) via browser.newContext(), which the host branch cannot do — Electron's CDP
  // target has no Target.createBrowserContext (Stage-0 spike), so this reuses Execute's own
  // single context as-is. A bot-protection screen that specifically distrusts Electron's own
  // UA string may reject a host-owned login page more often than the launched one; this is
  // caught the same way any login failure is — the retry in beginInteractiveAuth, and a
  // connect failure here falling straight through to the launch path below.
  if (runId && hostBrowser.endpoint()) {
    try {
      const { browser: loginBrowser, context: loginCtx, page: loginPage, hostTabId } =
        await hostBrowser.acquire({ runId, storageState: storedState, label, focus: true, loginKey: workspace_id });
      await _maskAutomation(loginPage);
      await loginPage.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
      return { loginBrowser, loginCtx, loginPage, hostOwned: true, hostRunId: runId, hostTabId };
    } catch (e) {
      if (logFn) logFn("warn", "host_browser_fallback", { run_id: runId, phase: "login", error: e.message });
    }
  }
  const loginBrowser = await chromium.launch({
    headless: false,
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const loginCtx = await loginBrowser.newContext({
    ...STEALTH_CONTEXT_OPTIONS,
    acceptDownloads: true,
    // Seed with the last known session (even if expired) so a partially-stale session
    // (e.g. one cookie refreshed, others still good) can skip straight past steps the
    // site would otherwise re-prompt for, instead of forcing a full fresh login.
    ...(storedState ? { storageState: storedState } : {}),
  });
  const loginPage = await loginCtx.newPage();
  // Mask Playwright detection at the JS level — prevents "browser not secure" errors
  await _maskAutomation(loginPage);
  await loginPage.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  return { loginBrowser, loginCtx, loginPage };
}

// How long a login window may sit open before it is given up on.
const LOGIN_WAIT_MS = 10 * 60 * 1000;

// The poll tick both login waits share: human override → already signed in → lookouts → judge →
// ladder. Resolves to the reason to save the session, or null to keep waiting. `ownPages()` is
// only this login's own tabs (never a sibling app's — see _sampleLookouts); `askJudge()` is the
// caller's before/after re-probe, asked once a NEW lookout has fired or this login's tabs moved to
// another page, never while a previous ask is in flight, a pause sign is up, or the person is
// part-way through the sign-in. The backup rule only counts once the judge has answered for the
// page they are on now (ladderVerdict's judgeSettled). With a learned `authDefinition` the probe REPLACES both
// the baseline judge and the backup-agreement rule: only a confident "yes" saves.
function _loginDecider({ key, beforeSnapshot, ticketBaseline, authDefinition, context, ownPages, askJudge }) {
  const lookoutState = { firedAt: {}, sawPasswordBox: false, ticketBaseline, startSig: new Map(), progressed: false };
  let judgeVerdict = null; // "yes" | "no" | null ("can't tell" — see login_signals.js)
  let judging = false;
  let judgedAtFiredCount = 0;
  let judgedAtAddress = "";
  let answeredAtAddress = null; // the own-tab address the judge's latest answer is about
  return async function decide() {
    // AUTH-6: the human said so — an unconditional save, bypassing everything below.
    if (_checkHumanOverride(key)) return "human_override";
    if (loginSignals.alreadySignedIn(beforeSnapshot)) return "already_signed_in";

    const nowMs = Date.now();
    const { paused, onSignIn, address } = await _sampleLookouts(lookoutState, { ownPages: ownPages(), context }, nowMs);
    const firedCount = Object.keys(lookoutState.firedAt).length;
    if (loginSignals.shouldAskJudge({ firedCount, judgedAtFiredCount, judgeVerdict, judging, paused, onSignIn, address, judgedAtAddress })) {
      judging = true;
      judgedAtFiredCount = firedCount;
      judgedAtAddress = address;
      askJudge()
        .then((v) => { judgeVerdict = v; })
        .catch(() => {})
        .finally(() => { judging = false; answeredAtAddress = judgedAtAddress; });
    }
    if (authDefinition) return !paused && !onSignIn && judgeVerdict === "yes" ? "auth_definition_yes" : null;
    const verdict = loginSignals.ladderVerdict({
      paused, onSignIn, judge: judgeVerdict, judgeSettled: !judging && answeredAtAddress === address,
      firedAt: lookoutState.firedAt, nowMs, agreeMs: LOGIN_BACKUP_AGREE_MS,
    });
    return verdict.action === "save" ? verdict.reason : null;
  };
}

// Poll + hard deadline + the "taking unusually long" diagnostic; the returned function stops all three.
function _loginTimers({ key, logFn, waitMs, onTick, onTimeout }) {
  const humanPrompt = setTimeout(() => {
    if (logFn) logFn("info", "login_signal_inconclusive", { key, waitedMs: LOGIN_HUMAN_PROMPT_MS });
  }, LOGIN_HUMAN_PROMPT_MS);
  const poll = setInterval(onTick, LOGIN_POLL_MS);
  const deadline = setTimeout(onTimeout, waitMs);
  return () => { clearInterval(poll); clearTimeout(deadline); clearTimeout(humanPrompt); };
}

// Wait for the user to finish signing in (or close the window) and return the
// captured session. Runs in the background — see beginInteractiveAuth.
async function _waitForInteractiveAuth(workspace_id, opened, opts = {}) {
  if (opened.session) return _waitForSessionLogin(workspace_id, opened, opts);
  const { waitMs = LOGIN_WAIT_MS, entryUrl = "", logFn, beforeSnapshot = null, ticketBaseline = null } = opts;
  const { loginBrowser, loginCtx, loginPage, hostOwned, hostRunId, hostTabId } = opened;
  let lastUrl = "";
  let lastState = null;
  let proveOk = true;
  let accountName = "";
  let _autoCloseScheduled = false;
  let _finalizing = false; // guards two overlapping _captureIfAuthenticated runs (interval + framenavigated)

  // Shared close point for both the auto-close-on-success timer below and the final
  // fallback close — mirrors teardownExecBrowser's reasoning exactly: loginBrowser.close()
  // on a CDP connection only ever disconnects (never kills Execute's browser process), but
  // a host-owned login still needs Execute told to actually destroy the view, or it leaks
  // until Execute's own idle cleanup reclaims it.
  const _closeLoginBrowser = async () => {
    try { if (loginBrowser.isConnected()) await loginBrowser.close(); } catch (_) {}
    if (hostOwned && hostRunId) await hostBrowser.release({ runId: hostRunId, tabId: hostTabId });
  };

  let decisionReason = null; // AUTH-7: which ladder rung (or human_override) decided a capture
  const _waitStartMs = Date.now();

  const _liveTabPages = () => {
    try { return loginCtx.pages().filter((p) => { try { return !p.isClosed(); } catch (_) { return false; } }); }
    catch (_) { return [loginPage]; }
  };
  // This login's context is dedicated to it alone (never shared with a sibling app's login — see
  // _waitForSessionLogin's comment on why that distinction matters). If the "before" snapshot never
  // resolved, the judge stays null and the backup-agreement rule decides instead.
  const decide = _loginDecider({
    key: workspace_id, beforeSnapshot, ticketBaseline, context: loginCtx, ownPages: _liveTabPages,
    askJudge: () => _snapshotLoginEntry({ hostOwned, context: loginCtx, hostRunId }, entryUrl)
      .then((after) => _judgeVerdict({ hostOwned, browser: loginBrowser }, entryUrl, beforeSnapshot, after)),
  });

  // Runs the multi-signal ladder (see login_signals.js) across every open tab of this login
  // context, then Timekeeper + capture + Prover once it says save. Gated by _finalizing so the
  // periodic interval and the framenavigated-driven calls below can never overlap each other —
  // signal gathering is real async work now (DOM probes), not the instant boolean check this
  // replaces, so overlap would otherwise be easy to hit. On first successful capture, schedule
  // auto-close after 1.5 s so the user does not need to close the window manually; if they close
  // it first, `disconnected` resolves the outer promise and the already-captured state is used.
  const _captureIfAuthenticated = async () => {
    if (_autoCloseScheduled || _finalizing || lastState) return;
    _finalizing = true;
    try {
      const reason = await decide();
      if (!reason) return;
      decisionReason = reason;

      const landedPage = () => {
        const live = _liveTabPages();
        return live[live.length - 1] || loginPage;
      };
      await _waitForTicketsCalm(loginCtx, landedPage);
      const landed = landedPage();
      try { if (landed) lastUrl = landed.url(); } catch (_) {}
      try { lastState = await _captureState(loginCtx, hostOwned, [loginPage]); } catch (e) {
        if (logFn) logFn("warn", "login_capture_failed", { key: workspace_id, error: e.message });
        return;
      }
      if (lastState) {
        proveOk = await _proveSession(entryUrl,
          { hostOwned, context: loginCtx, hostRunId, browser: loginBrowser, state: lastState });
        if (logFn && !proveOk) logFn("warn", "login_prove_failed", { key: workspace_id });
        accountName = await _probeAccountName(landed);
        _autoCloseScheduled = true;
        setTimeout(() => { _closeLoginBrowser().catch(() => {}); }, 1500);
      }
    } finally {
      _finalizing = false;
    }
  };

  const rememberPage = async (page) => {
    if (!page) return;
    try {
      if (page.isClosed()) return;
      const url = page.url();
      if (!_isBlankUrl(url)) lastUrl = url;
    } catch (_) {}
  };

  const attachPage = (page) => {
    rememberPage(page).catch(() => {});
    page.on("framenavigated", (frame) => {
      try {
        if (!frame.parentFrame()) {
          rememberPage(page)
            .then(() => _captureIfAuthenticated())
            .catch(() => {});
        }
      } catch (_) {}
    });
    // Capture right before the page closes — covers the case where the user closes
    // the browser immediately after landing on the authenticated page, before the
    // next framenavigated capture has a chance to run.
    page.on("close", () => { _captureIfAuthenticated().catch(() => {}); });
  };

  loginCtx.on("page", attachPage);
  attachPage(loginPage);

  // Wait for the user to close all browser windows (event-driven).
  // Also run a periodic re-capture every 1.5 s for apps that write session tokens
  // into localStorage/sessionStorage via client JS after the page has loaded —
  // framenavigated fires before that JS runs, so the one-shot capture misses them.
  // Bounded: a host-owned login shares a CDP connection with Execute's whole process, so
  // "disconnected" never fires when the person just walks away from the panel — without a
  // deadline that login stays pending forever and _pendingAuth turns every later run into
  // "a login window is already open".
  let timedOut = false;
  await new Promise((resolve) => {
    const stop = _loginTimers({
      key: workspace_id, logFn, waitMs,
      onTick: () => {
        _captureIfAuthenticated().catch(() => {});
        // A host-owned login shares Execute's CDP connection, so closing just this tab never fires
        // "disconnected" — notice it here instead of waiting out the 10-minute deadline.
        // ponytail: gives up if the user closes the ORIGINAL page after signing in via an OAuth
        // popup; beginInteractiveAuth's one-shot reopen covers that.
        if (hostOwned && loginPage.isClosed()) { stop(); resolve(); }
      },
      onTimeout: () => { timedOut = true; stop(); resolve(); },
    });
    loginBrowser.on("disconnected", () => { stop(); resolve(); });
  });

  // Last-resort fallback: try once more in case context is still accessible.
  if (lastState === null) {
    try { lastState = await _captureState(loginCtx, hostOwned, [loginPage]); } catch (_) {}
  }

  await _closeLoginBrowser();
  const _waitedMs = Date.now() - _waitStartMs;

  if (timedOut && !lastState) {
    loginDecisionLog.record(workspace_id, "timeout", _waitedMs);
    throw Object.assign(
      new Error(`The login window for ${workspace_id} timed out before sign-in finished. Run the skill again to get a new one.`),
      { loginTimedOut: true },
    );
  }

  const rejectReason = _rejectReasonForProtectedUrl(lastUrl);
  if (rejectReason) { loginDecisionLog.record(workspace_id, "rejected_url", _waitedMs); throw new Error(rejectReason); }
  if (!lastState) {
    loginDecisionLog.record(workspace_id, "abandoned", _waitedMs);
    throw new Error(`Authentication session was not captured for ${workspace_id}. Please try again.`);
  }
  loginDecisionLog.record(workspace_id, decisionReason, _waitedMs);
  return { state: lastState, protectedUrl: lastUrl, proveOk, accountName };
}

// _waitForInteractiveAuth for a sign-in TAB inside a session (see _openInteractiveAuthWindow).
// Detection is a poll over EVERY page of the shared context rather than events on one window: it
// is what makes "finished in a different tab", an OAuth popup, a multi-hop SSO redirect and a login
// page that closes itself all look the same — the judge re-probes entryUrl itself (in a throwaway
// tab, see _snapshotLoginEntry) rather than looking for a page that "arrived" somewhere specific.
// Only the login tab and pages it opened (page.opener() chain) are closed afterwards; another
// app's concurrent login tab, and tabs the user opened themselves, are never touched.
// Resolves { state, protectedUrl } — `state` is already sliced to this app (refreshAppState).
async function _waitForSessionLogin(key, opened, opts = {}) {
  const { session, loginPage, hostTabId } = opened;
  const {
    waitMs = LOGIN_WAIT_MS, storedState, hosts = [], claimedElsewhere = null, entryUrl = "", logFn,
    beforeSnapshot = null, ticketBaseline = null, authDefinition = null,
  } = opts;
  const ctx = session.context;

  const mine = new Set([loginPage]);
  const adopt = async (page) => {
    try { const from = await page.opener(); if (from && mine.has(from)) mine.add(page); } catch (_) {}
  };
  ctx.on("page", adopt);

  const isClosed = (p) => { try { return p.isClosed(); } catch (_) { return true; } };
  // Only the login tab and pages it opened — see _sampleLookouts's own comment on why the
  // password-box/pause-sign/ticket signals must never read the shared context's OTHER pages: a
  // sibling app's login sharing this same launched context (multiple apps missing at once) would
  // otherwise get credited for this app's own arrival. A sign-in finished in some OTHER tab (one
  // the user opened themselves, not one of `mine`) is still caught — not by scanning for it here,
  // but because the judge re-probes entryUrl itself in its own throwaway tab of the shared context
  // (_snapshotLoginEntry), which doesn't care which tab the user actually used.
  const myLivePages = () => [...mine].filter((p) => !isClosed(p));

  const decide = _loginDecider({
    key, beforeSnapshot, ticketBaseline, authDefinition, context: ctx, ownPages: myLivePages,
    askJudge: () => (authDefinition
      ? _probeAuthDefinitionVerdict(session, authDefinition).then((r) => (r.verdict === "no" ? "no" : r.verdict === "yes" ? "yes" : null))
      : _snapshotLoginEntry(session, entryUrl).then((after) => _judgeVerdict(session, entryUrl, beforeSnapshot, after))),
  });

  let outcome = null;
  let decisionReason = null; // AUTH-7: which ladder rung (or human_override) decided a "reached" outcome
  const _waitStartMs = Date.now();
  await new Promise((resolve) => {
    let closedAt = 0;
    let ticking = false;
    const finish = (o) => {
      if (outcome) return;
      outcome = o;
      stop();
      try { session.browser.off("disconnected", onGone); } catch (_) {}
      resolve();
    };
    const onGone = () => finish("gone");
    const stop = _loginTimers({
      key, logFn, waitMs,
      onTimeout: () => finish("timeout"),
      onTick: () => {
        if (outcome || ticking) return; // signal gathering is real async work — never overlap two ticks
        ticking = true;
        (async () => {
          decisionReason = await decide();
          if (decisionReason) return finish("reached");
          // With a learned auth definition a closed tab never ends the wait early — only a "yes" or
          // the timeout. Otherwise: the login tab is closed and nothing else got there — but a page
          // that closes itself right after a redirect can beat the poll, so give a success elsewhere
          // a moment to show up.
          if (!authDefinition && isClosed(loginPage)) {
            if (!closedAt) closedAt = Date.now();
            else if (Date.now() - closedAt >= LOGIN_CLOSE_GRACE_MS) finish("closed");
          }
        })().catch(() => {}).finally(() => { ticking = false; });
      },
    });
    // A launched session's window/browser being closed; an Execute view closing is caught by the
    // login tab's own isClosed() above (the CDP connection is Execute's whole process).
    try { session.browser.on("disconnected", onGone); } catch (_) {}
  });
  loginDecisionLog.record(key, outcome === "reached" ? decisionReason : outcome, Date.now() - _waitStartMs);

  let state = null;
  let landedUrl = "";
  let proveOk = true;
  let accountName = "";
  if (outcome === "reached") {
    // The timekeeper's representative page: the most recently active page owned by this login
    // (never a sibling app's tab — see _sampleLookouts's ownPages comment for why that distinction
    // matters throughout this function). The judge, not page selection, is what decided sign-in.
    const landedPage = () => myLivePages()[myLivePages().length - 1] || loginPage;
    await _waitForTicketsCalm(ctx, landedPage);
    const page = landedPage();
    landedUrl = page ? page.url() : "";
    try { state = await _captureState(ctx, session.hostOwned, myLivePages()); } catch (e) {
      if (logFn) logFn("warn", "login_capture_failed", { key, error: e.message });
    }
    if (state) {
      proveOk = await _proveSession(entryUrl,
        { hostOwned: session.hostOwned, context: ctx, hostRunId: session.hostRunId, browser: session.browser, state, definition: authDefinition });
      if (logFn && !proveOk) logFn("warn", "login_prove_failed", { key });
      accountName = await _probeAccountName(page);
    }
  }

  try { ctx.off("page", adopt); } catch (_) {}
  for (const p of mine) {
    if (p === loginPage) await _closeSessionPage(session, { page: p, hostTabId });
    else { try { if (!isClosed(p)) await p.close(); } catch (_) {} }
  }

  if (outcome === "reached") {
    if (!state) throw Object.assign(new Error(`Authentication session was not captured for ${key}. Please try again.`), { captureFailed: true });
    return { state: refreshAppState(storedState, state, hosts, claimedElsewhere), protectedUrl: landedUrl, proveOk, accountName };
  }
  if (outcome === "timeout") {
    throw Object.assign(
      new Error(`The login window for ${key} timed out before sign-in finished. Run the skill again to get a new one.`),
      { loginTimedOut: true },
    );
  }
  throw new Error(outcome === "gone"
    ? `The browser was closed before sign-in finished for ${key}.`
    : `The sign-in tab for ${key} was closed before sign-in finished.`);
}

// Per-workspace handle for an in-flight (or just-finished) interactive login window, so a
// second execute_skill call while the window is open doesn't spawn a second one.
const _pendingAuth = new Map();

// Open a headed Chromium window for the user to log in. The launch itself (the fast,
// synchronous part) is awaited here, so a genuine failure — chromium missing/mid-install,
// launch permission error, bad navigation — is returned to the caller as the real error
// instead of the misleading "window opened" message. Once the window is open, waiting
// for the user to actually sign in happens in the background; the window disconnecting
// with no session captured (user closed it before signing in) reopens once, then gives
// up — the next call to this function starts a fresh attempt.
async function beginInteractiveAuth(workspace_id, targetUrl, opts = {}) {
  const { storedState, protectedUrl, authManager, sessionsDir, logFn, runId, label, session, hosts, claimedElsewhere, onSettled, authDefinition } = opts;

  const existing = _pendingAuth.get(workspace_id);
  if (existing && existing.status === "pending") {
    return { authPending: true, loginUrl: targetUrl, key: workspace_id, reason: "already_open",
      message: `A login window for ${label || workspace_id} is already open. Sign in there.` };
  }
  const reopened = existing && existing.status === "done" && existing.outcome === "abandoned";

  // The "before" snapshot (docs/artifacts/login-desk.html "Ask for the login page again"), taken
  // in a throwaway tab BEFORE the visible login window opens — so it can never race the person's
  // own sign-in and misread an already-fresh session as "before". Only possible for the in-session
  // case: the launched-browser fallback (an unattended run with no session yet) has no context to
  // borrow a throwaway tab from at this point, so it skips the snapshot and the judge stays null,
  // leaving the backup-agreement rule (password-box-gone + new-tickets) to decide alone.
  // ponytail: no before-snapshot for that fallback — add one (its own short-lived browser) if that
  // path turns out to need the judge's precision too.
  const beforeSnapshot = session ? await _snapshotLoginEntry(session, targetUrl).catch(() => null) : null;
  // Same reasoning for the newTickets lookout's own baseline: captured HERE, before the window
  // opens, not lazily on the wait loop's first poll tick. A sign-in finished in some OTHER tab
  // (elsewhere in the shared context) can complete faster than one poll interval — the ticket
  // baseline must predate that possibility or it silently absorbs the change as "already there".
  const ticketBaseline = session ? await _ticketSignatureNow(session.context, null).catch(() => null) : null;

  let opened;
  try {
    opened = await _openInteractiveAuthWindow(workspace_id, targetUrl, { storedState, runId, logFn, label, session });
  } catch (e) {
    // authPending stays true so the existing "gate on auth" handling in callers still
    // fires (they only branch on this flag) — only the message differs, carrying the
    // real failure instead of a claim that a window opened.
    return { authPending: true, loginUrl: targetUrl, key: workspace_id, reason: "launch_failed", message: e.message, launchFailed: true };
  }

  const handle = { status: "pending", outcome: null };
  _pendingAuth.set(workspace_id, handle);

  // Callers (the execute_skill gate, the `authenticate` tool) await this via awaitInteractiveAuth.
  // The body never rejects — every failure lands in handle.outcome/message below.
  // Runs before `settled` resolves, so whoever awaits the outcome sees the session already released.
  const settle = () => { try { if (onSettled) onSettled(handle); } catch (_) {} };
  handle.settled = (async () => {
    let lastErr = null;
    let currentlyOpen = opened;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const { state, protectedUrl: landedUrl, proveOk, accountName } = await _waitForInteractiveAuth(workspace_id, currentlyOpen,
          { protectedUrl, storedState, hosts, claimedElsewhere, entryUrl: targetUrl, logFn, beforeSnapshot, ticketBaseline, authDefinition });
        try { await _persistSession(workspace_id, state, authManager, sessionsDir, logFn); }
        catch (e) { throw Object.assign(e, { captureFailed: true }); }
        // docs/artifacts/login-desk.html "Follow the journey": remember where THIS sign-in actually
        // landed, so a later run (or the judge's own snapshot) has a real address to check even
        // when the app was configured with no success_url at all.
        _writeLanding(workspace_id, landedUrl);
        // Just signed in inside the very context the run will use — nothing left to prove, so stamp
        // the validation cache instead of making the next pre-flight probe it again.
        if (session) {
          try {
            const { sessionPath } = await _loadSessionForKey(workspace_id, authManager, logFn);
            if (sessionPath) _writeValidationCache(workspace_id, sessionPath);
          } catch (_) {}
        }
        handle.status = "done";
        handle.outcome = "captured";
        // The Prover (see _proveSession) already ran inside the wait — a failure here doesn't
        // block the save (docs/artifacts/login-desk.html's own "Login lost on save" scenario
        // still saves), it just means the customer is told now instead of on their next run.
        if (proveOk === false) {
          handle.message = `Signed in to ${label || workspace_id}, but a fresh check couldn't confirm the saved sign-in actually works — ` +
            `it may fail next time you run this skill. Try signing in again if it does.`;
        }
        // AUTH-8: best-effort, positive-only — never overrides a prove-failure warning above, and
        // a miss (accountName === "") sets nothing rather than an empty string.
        if (accountName) handle.accountName = accountName;
        settle();
        return;
      } catch (e) {
        lastErr = e;
        if (logFn) logFn("warn", "login_attempt_failed", { key: workspace_id, attempt, error: e.message });
        // Reopen a fresh window for the retry — the previous one is already closed
        // (disconnected is what got us here). A timeout is different: nobody is there, so
        // reopening would just put another unattended window up. So is a failure AFTER the person
        // signed in (capturing or saving it): a fresh sign-in tab would just ask them again for
        // something they already did — the AUTH-20 "signed in, then asked to sign in again" loop.
        if (e.loginTimedOut || e.captureFailed) break;
        if (attempt === 0) {
          try {
            currentlyOpen = await _openInteractiveAuthWindow(workspace_id, targetUrl, { storedState, runId, logFn, label, session });
          } catch (e2) {
            lastErr = e2;
            break;
          }
        }
      }
    }
    handle.status = "done";
    handle.outcome = "abandoned";
    handle.message = lastErr && lastErr.message;
    settle();
  })();

  const message = reopened
    ? `The previous login window for ${workspace_id} was closed before signing in. Opened a new one — sign in there, then re-run the skill.`
    : `Opened a browser window for the user to log in to ${workspace_id}. Sign in there — the window closes on its own once you land on the app.`;
  return { authPending: true, loginUrl: targetUrl, key: workspace_id, reason: reopened ? "reopened" : "opened", message };
}

// Await the outcome of an in-flight interactive login started by beginInteractiveAuth.
// Resolves to { outcome: "captured" | "abandoned" | "timeout" | "none", message? } and never throws.
// A "timeout" here only means THIS caller stopped waiting — the login window stays open (its own
// LOGIN_WAIT_MS deadline is untouched) so the user can still finish and a later call picks it up.
async function awaitInteractiveAuth(key, { timeoutMs = 180000 } = {}) {
  const h = _pendingAuth.get(key);
  if (!h) return { outcome: "none" };
  if (h.status === "done") return { outcome: h.outcome, message: h.message, accountName: h.accountName };
  let timer;
  const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve({ outcome: "timeout" }), timeoutMs); });
  const settled = h.settled.then(() => ({ outcome: h.outcome, message: h.message, accountName: h.accountName }));
  try { return await Promise.race([settled, timeout]); } finally { clearTimeout(timer); }
}

// Wait for every login window an authPending result opened (a group result carries one entry per
// missing app in `apps`; a single-session result is its own entry). Skips entries that never got a
// window (launch_failed) — there is nothing to wait for. Resolves to
// [{ key, id?, name?, outcome, message? }], one per entry, in order.
async function awaitAuthPending(result, { timeoutMs } = {}) {
  const entries = Array.isArray(result.apps) ? result.apps : [result];
  return Promise.all(entries.map(async (e) => ({
    key: e.key, id: e.id, name: e.name || e.key,
    ...(e.reason === "launch_failed"
      ? { outcome: "launch_failed", message: e.message }
      : await awaitInteractiveAuth(e.key, { timeoutMs })),
  })));
}

// One plain word for a login's state, for get_execution_status: waiting | signed_in | closed | failed.
// No handle at all means the sign-in tab never opened.
function authAppStatus(key) {
  const h = _pendingAuth.get(key);
  if (!h) return "failed";
  if (h.status !== "done") return "waiting";
  return h.outcome === "captured" ? "signed_in" : "closed";
}

// One honest sentence for whatever is still unsigned after awaitAuthPending — names the real state
// (window still open vs closed vs never launched) instead of claiming a window "just opened".
function describeAuthWait(waited) {
  const pending = waited.filter((w) => w.outcome !== "captured");
  if (pending.length === 0) return "";
  const launch = pending.find((w) => w.outcome === "launch_failed");
  if (launch) return launch.message;
  const names = pending.map((w) => w.name).join(", ");
  const closed = pending.filter((w) => w.outcome === "abandoned" || w.outcome === "none");
  if (closed.length === pending.length) {
    return `The sign-in window for ${names} was closed before sign-in finished. Call authenticate (or run the skill again) to get a new one.`;
  }
  return `Still waiting for sign-in to ${names} — the window is still open. Finish signing in there, then call authenticate or run the skill again.`;
}

async function getAuthContext(workspace_id, authManager, opts = {}) {
  const headless = opts.headless !== false; // default true
  const logFn = opts.logFn;

  // Every pack Build Studio produces carries a `groups` block and every skill belongs to one (the
  // build refuses otherwise), so sign-in is always resolved per group app. A group with no apps is
  // valid — getGroupAuthContext runs it with no gate. There is no single-session fallback (AUTH-2):
  // a pack whose group can't be resolved was built before Workflow Groups or lost its group.
  const group = _resolveGroup(workspace_id, opts.groupId);
  if (!group) {
    throw new Error(
      `No sign-in group found for ${workspace_id}${opts.groupId ? ` (group ${opts.groupId})` : ""} — this skill pack ` +
      `predates Workflow Groups or its group was removed. Rebuild the pack in Build Studio and publish it again.`);
  }
  return getGroupAuthContext(workspace_id, group, authManager, {
    headless, logFn, runId: opts.runId, authOnly: opts.authOnly, noPrompt: opts.noPrompt,
  });
}

// Resolve which app died from a mid-execution auth failure and describe it clearly —
// but do NOT open a re-auth window here. Authentication is pre-flight only (see
// getGroupAuthContext above): a run that hits an auth failure mid-execution always fails
// immediately, full stop, and re-authentication happens on the user's NEXT attempt, via
// the normal pre-flight gate at the top of the next execute_skill call — which re-validates
// this exact app and opens its login window then. Opening a window right here, at the
// moment of failure (the previous behavior), interrupted an already-dead run instead of
// waiting for a fresh attempt, which is what made it a mid-run auto-login rather than a
// pre-flight one. Called from server.js on auth failure. Group-aware: picks the app whose
// success_url/login_url host matches the failing page's own URL (server.js passes the page
// that actually bounced to a login screen, not the manifest's start-app URL — a group
// pack's target_url is the app the workflow STARTS in, which is the wrong app when a
// sibling dies mid-run). Falls back to opts.fallbackUrl's host (manifest.target_url) if the
// primary hint matches nothing — e.g. an OAuth hop landed on a third-party host — before
// giving up to the legacy group.apps[0] guess. Falls back to the non-group single-session
// behavior when there's no group at all.
// Strips a leading "www." — used only for ATTRIBUTION (which configured app does this page
// belong to, for error messages and cookie-ownership splitting: captureReAuth, refreshAppState's
// `hosts`/`claimedElsewhere`), never for sign-in DETECTION any more (see _signedOutBaseline /
// isSignedInAgainstBaseline). A redirect between a bare and www.-prefixed hostname shouldn't make
// an app unrecognizable for either purpose.
function _hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch (_) { return ""; }
}

async function captureReAuth(workspace_id, loginUrl, authManager, sessionsDir, logFn, opts = {}) {
  // authManager/sessionsDir are accepted (not used) to keep this function's call sites and
  // existing tests stable — they were only ever needed for the window-opening/session-persist
  // path this function no longer takes.
  const group = _resolveGroup(workspace_id, opts.groupId);
  if (group && group.apps && group.apps.length > 0) {
    const byHost = (host) => group.apps.find((a) => _hostOf(a.success_url || a.login_url) === host);
    let matchedBy = "failing-page-host";
    let app = byHost(_hostOf(loginUrl));
    if (!app && opts.fallbackUrl) {
      matchedBy = "manifest-fallback-host";
      app = byHost(_hostOf(opts.fallbackUrl));
    }
    if (!app) {
      // Neither the failing page's own host nor the manifest's fallback host matches any
      // app this group actually manages a saved session for. That means the redirect isn't
      // a tracked app's session dying — it's something else (most commonly a workflow's own
      // recorded login step failing on bad credentials against a site that was never
      // registered as a group app in the first place). Guessing group.apps[0] here used to
      // name a real, uninvolved app (e.g. "Render") in the user-facing error for a failure
      // that had nothing to do with it — worse than no guess. Report unresolved and let the
      // caller fall back to the plain step-failure message instead of fabricating a culprit.
      if (logFn) logFn("info", "reauth_app_unresolved", { workspace_id, loginUrl, fallbackUrl: opts.fallbackUrl || null });
      // `note` is for the person: name the site and where to fix it, without claiming a session died.
      const host = _hostOf(loginUrl);
      return {
        authPending: false, unresolved: true, loginUrl,
        note: `The page is at a sign-in screen on ${host || "a site"}, which has no sign-in set up in the ${group.name} group. ` +
          `If this workflow needs you signed in there, add it as an app in that group in Build Studio, then run again.`,
      };
    }
    if (logFn) logFn("info", "reauth_app_resolved", { workspace_id, appId: app.id, matchedBy });
    authCache.clearValidation(_appKey(workspace_id, app));
    return {
      authPending: false,
      loginUrl: app.login_url,
      // hasAuthDefinition: whether server.js should AUTO-retry this run (see its session_expired
      // handler) instead of only telling the person to call execute_skill again by hand — see
      // server.js's own comment on why that's safe now, but only for an app whose sign-in is
      // detected via a learned definition (P0: Application Authentication Recording), never the
      // generic ladder: the whole reason mid-run auto-login was deliberately left out before was
      // that generic detection wasn't confident enough to trust unattended.
      hasAuthDefinition: Boolean(app.auth_definition),
      message: `${app.name}'s saved sign-in expired mid-run. Call execute_skill again — ` +
        `you'll be prompted to sign back in to ${app.name}, then can resume from where this left off.`,
    };
  }
  return {
    authPending: false,
    loginUrl,
    message: `The saved sign-in for ${workspace_id} expired mid-run. Call execute_skill again — ` +
      `you'll be prompted to sign back in, then can resume from where this left off.`,
  };
}

// The one place a watch-mode run's browser/context gets torn down. Replaces four
// near-identical `if (watch) { await _context.close(); await _browser.close(); }`
// blocks that used to live at each of server.js's teardown sites — a launched
// browser really is closed there, but a host-owned one (Execute's panel) is only
// ever disconnected: closing it would tear down Execute's whole browser process,
// panel and all, out from under the user. Doing this in one shared function is the
// point — a call site that forgot the `hostOwned` check would silently kill
// Execute's browser mid-session, and the fix belongs where every teardown path
// routes through, not patched into each one by hand.
async function teardownExecBrowser({ browser, context, hostOwned, runId }) {
  if (!browser) return;
  if (hostOwned) {
    await browser.close().catch(() => {}); // CDP connection: disconnect only
    if (runId) await hostBrowser.release({ runId }); // tell Execute to destroy the view
    return;
  }
  if (context) await context.close().catch(() => {});
  await browser.close().catch(() => {});
}

async function gracefulShutdown() {
  await sessions.closeAll();
  process.exit(0);
}

module.exports = {
  getCachedBrowser,
  releaseCachedBrowser,
  teardownExecBrowser,
  getAuthContext,
  getGroupAuthContext,
  captureReAuth,
  beginInteractiveAuth,
  awaitInteractiveAuth,
  awaitAuthPending,
  describeAuthWait,
  authAppStatus,
  _pendingAuth,
  gracefulShutdown,
  mergeStorageStates,
  refreshAppState,
  _rejectReasonForProtectedUrl,
  _loginEntryUrl,
  _successPrefix,
  _probeInSession,
  _isAuthenticated,
  _signedOutBaseline,
  _resolveGroup,
  _loadGroupAppSession,
  _loadSessionForKey,
  _readValidationCache,
  _writeValidationCache,
  _readLanding,
  _writeLanding,
  _protectedUrlOf,
  _buildExecContext,
  _sessionKey,
  _openInteractiveAuthWindow,
  _waitForInteractiveAuth,
  AUTH_VALIDATION_TTL_MS,
  _checkHumanOverride,
  _humanOverrideFile,
  LOGIN_DONE_DIR,
};
