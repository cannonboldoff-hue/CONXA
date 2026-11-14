"use strict";
/**
 * host_browser.js — the one seam where the runtime borrows a browser view an MCP
 * client already owns, instead of launching its own Chromium.
 *
 * Today the only such client is Conxa Execute: it renders skill runs inside its own
 * window (a "cowork" panel) rather than letting a separate Chromium window pop up.
 * Execute passes two things as environment variables when it spawns the runtime
 * (see conxa-execute/app/electron/mcp_client.js):
 *   - CONXA_HOST_BROWSER_CDP   the Chrome DevTools Protocol endpoint of its own
 *                              Electron process (chromium.connectOverCDP target)
 *   - CONXA_HOST_CONTROL_URL   a loopback HTTP control channel Execute runs, used to
 *                              ask it to create a WebContentsView (new_view/new_tab, with an
 *                              optional tab label) and destroy one (close_view — a single tab,
 *                              e.g. a finished login) or a whole run's worth (run_end)
 *
 * Every other client (Claude Desktop, the scheduler, the Build Studio sandbox) never
 * sets these, so endpoint() returns null and browser.js's existing chromium.launch()
 * path is exercised exactly as before — this module is inert for them.
 *
 * Electron's CDP target does NOT support Target.createBrowserContext or
 * Target.createTarget (confirmed by a Stage-0 spike against Electron 42 / Playwright
 * 1.59) — there is exactly one browser context (browser.contexts()[0]), and a new
 * page can only come from Execute itself creating a WebContentsView. That shapes
 * every function below: acquire() takes the existing context and asks Execute for a
 * page rather than creating one, and newTab() is a control-channel round trip, not a
 * context.newPage() call.
 */

const httpClient = require("./http_client");
const { hostRequire } = require("./host_bridge");
const { evalOn } = require("./page_eval");
const pageScripts = require("./page_scripts");
const chromium = hostRequire("playwright").chromium;

function endpoint() {
  const v = process.env.CONXA_HOST_BROWSER_CDP;
  return v && v.trim() ? v.trim() : null;
}

function controlUrl() {
  const v = process.env.CONXA_HOST_CONTROL_URL;
  return v && v.trim() ? v.trim() : null;
}

// POST {op, ...body} to Execute's control channel and return the parsed JSON reply.
// Loopback-only, short timeout — a wedged Execute must fail fast into the caller's
// fallback rather than hang the run.
async function _controlPost(op, body, timeoutMs = 5000) {
  const url = controlUrl();
  if (!url) throw new Error("host_browser: CONXA_HOST_CONTROL_URL not set");
  const { body: raw } = await httpClient.postForBuffer(url, {
    json: { op, ...body },
    timeoutMs,
  });
  return JSON.parse(raw.toString("utf8"));
}

// A download in a host-owned run is saved by Execute, not Playwright: the run's views live in their
// own Electron partition, whose download manager never writes where download.saveAs() looks
// (confirmed on real Electron — ENOENT). Execute hands the finished file over instead (EXEC-46).
async function saveDownload({ runId, download, dest, timeoutMs }) {
  await _controlPost("save_download", { runId, url: download.url(), dest, timeoutMs }, timeoutMs + 5000);
}

// Cookies of the storage a PAGE actually uses, in Playwright's cookie shape. Execute gives each
// run its own in-memory Electron partition (browser_panel.js), but Playwright's one CDP context
// is Electron's DEFAULT session: context.cookies() reads it (always empty for a run) and
// context.addCookies() writes into it (never seen by a run's views) — confirmed against real
// Electron (AUTH-21). The Network domain of a CDP session on the page itself goes through that
// page's own partition, so both directions go through here.
async function pageCookies(page) {
  const cdp = await page.context().newCDPSession(page);
  try {
    const { cookies } = await cdp.send("Network.getAllCookies");
    return cookies.map((c) => ({
      name: c.name, value: c.value, domain: c.domain, path: c.path,
      expires: c.session ? -1 : c.expires, httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite || "Lax",
    }));
  } finally { await cdp.detach().catch(() => {}); }
}

async function setPageCookies(page, cookies) {
  const cdp = await page.context().newCDPSession(page);
  try {
    await cdp.send("Network.setCookies", {
      cookies: cookies.map(({ expires, ...c }) => (typeof expires === "number" && expires > 0 ? { ...c, expires } : c)),
    });
  } finally { await cdp.detach().catch(() => {}); }
}

// Seed a Playwright storageState into the run's view — the manual equivalent of what
// chromium.launch()'s browser.newContext({ storageState }) does in one call, split into its two
// parts because Electron's context can't be created fresh, only reused:
//   - cookies: through the page's own CDP session (setPageCookies — not context.addCookies(),
//     which lands in Electron's default session instead of the run's partition).
//   - localStorage: Playwright has no CDP call for this; the only way is to visit
//     each origin and set it from the page itself, same as Playwright's own
//     storageState-loading code does internally when it owns the context.
async function _seedStorageState(context, page, storageState) {
  if (!storageState) return;
  if (Array.isArray(storageState.cookies) && storageState.cookies.length) {
    await setPageCookies(page, storageState.cookies);
  }
  const origins = Array.isArray(storageState.origins) ? storageState.origins : [];
  for (const o of origins) {
    if (!o.localStorage || !o.localStorage.length) continue;
    try {
      await page.goto(o.origin, { waitUntil: "domcontentloaded", timeout: 15000 });
      // Routed through page_eval.js's evalOn (EXEC-34) rather than a bare page.evaluate() —
      // this is a CDP round-trip like any other and gets no timeout of its own.
      await evalOn(page, pageScripts.seedLocalStorage, o.localStorage);
    } catch (_) {
      // A single origin failing to seed (site down, blocked) must not fail the whole
      // run — cookies (the common case) are already in; this only loses that one
      // origin's localStorage, same risk profile as a stale cache entry.
    }
  }
}

// Borrow Execute's browser for one run. Returns the same shape browser.js's
// _buildExecContext() returns ({ browser, context }), plus `page` (the view Execute
// already created — callers must use this instead of context.newPage(), which
// Electron doesn't support) and `hostOwned: true` so teardownExecBrowser() knows to
// disconnect rather than close.
async function acquire({ runId, storageState, label, focus, loginKey, viewport }) {
  const cdp = endpoint();
  if (!cdp) throw new Error("host_browser: CONXA_HOST_BROWSER_CDP not set");
  // `viewport` fixes the page size no matter how big the host's panel is (the panel letterboxes it).
  const { markerUrl, tabId } = await _controlPost("new_view", { runId, label, focus, loginKey, viewport });
  const browser = await chromium.connectOverCDP(cdp);
  const context = browser.contexts()[0];
  if (!context) {
    await browser.close().catch(() => {});
    throw new Error("host_browser: no browser context on connect");
  }
  const page = await _findPageByMarker(context, markerUrl, runId);
  await _seedStorageState(context, page, storageState);
  return { browser, context, page, hostOwned: true, hostTabId: tabId };
}

// Execute's control channel responds with the about:blank-with-marker URL it loaded
// into the new view (?conxa_run=<runId>) so this side can pick the right page out of
// a context that may hold other runs' tabs too — context.pages() has no other way to
// tell them apart.
async function _findPageByMarker(context, markerUrl, runId) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const found = context.pages().find((p) => p.url() === markerUrl);
    if (found) return found;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`host_browser: view for run ${runId} did not appear (marker ${markerUrl})`);
}

// tab_open steps need a second page in the same run. context.newPage() is
// unsupported against Electron, so this is a control-channel round trip identical
// in shape to acquire()'s first step, just without re-seeding storageState (the new
// tab shares the run's existing context/cookies already).
async function openTab({ context, runId, label, focus, loginKey }) {
  const { markerUrl, tabId } = await _controlPost("new_tab", { runId, label, focus, loginKey });
  return { page: await _findPageByMarker(context, markerUrl, runId), tabId };
}
// The tabId is what a caller needs to close just this one view later (see release below) — a
// sign-in / probe tab inside a session, as opposed to a `tab_open` step's tab, which lives to run end.
async function newTab(args) {
  return (await openTab(args)).page;
}

// Ask the host panel — the one owner of the page size — to measure the run's page and put it back at
// the recorded size if it drifted (page_geometry.js's checkGeometry calls this).
function ensureGeometry({ runId }) {
  return _controlPost("ensure_geometry", { runId });
}

// A hand-over is the one time a person acts in the run's page. The panel keeps a transparent shield over the
// run's page the rest of the time (so a stray click or hover is never taken for a step); this takes it off
// (open) or puts it back. Best-effort: a failed lift leaves the person on the resume file/http signals.
function setInputOpen({ runId, open }) {
  return _controlPost("input", { runId, open: open === true }).catch(() => {});
}

// Tell Execute a view (or the whole run) is done. With `tabId` only that one view goes — a
// login that finished must not take its sibling logins' views down with it (a group pack opens
// one login per missing app, all under the same runId). Without it the run is over and Execute
// destroys every view plus the partition.
// The CDP `browser` handle itself is only ever disconnected (see
// teardownExecBrowser in browser.js) — closing it would tear down Execute's whole
// browser process out from under the app.
function release({ runId, tabId }) {
  const req = tabId ? _controlPost("close_view", { runId, tabId }) : _controlPost("run_end", { runId });
  return req.catch(() => {});
  // ponytail: best-effort — a failed run_end leaks one view until Execute's own
  // idle cleanup (or app restart) reclaims it; never worth failing a run over.
}

module.exports = { endpoint, controlUrl, acquire, newTab, openTab, release, ensureGeometry, setInputOpen, pageCookies, setPageCookies, saveDownload };
