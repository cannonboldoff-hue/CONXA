"use strict";
/**
 * browser_panel.js — Execute's side of the in-app browser panel. Covers skill
 * runs, logins, and recovery hand-over (EXEC-41 stage 1 + stage 2).
 *
 * Owns one WebContentsView per open tab, grouped by run. The runtime never creates
 * these directly — Electron's CDP target doesn't support Target.createTarget
 * (confirmed by the Stage-0 spike in this change), so every new page is created HERE,
 * in the process that actually owns the window, and the runtime is told where to find
 * it via a marker URL (see host_browser.js on the runtime side).
 *
 * Each run gets its own partition (`conxa-run-<runId>`, no `persist:` prefix — an
 * in-memory, per-run Electron session) so nothing about one run's cookies/storage can
 * leak into another's, and cleanup on run_end is just "stop referencing these views."
 */
const { session, WebContentsView } = require("electron");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { resolveRuntimeCommand } = require("./runtime_path");

// runId -> { partition, tabs: [{ id, view, markerUrl }], activeTabId }
const _runs = new Map();

// runId -> chat (session) id that started the run. Runs started outside a chat (the form) have
// no entry and stay visible in every chat.
const _chatOf = new Map();

function tagRun(runId, chatId) {
  if (runId && chatId) _chatOf.set(runId, chatId);
}

function chatOf(runId) {
  return _chatOf.get(runId) ?? null;
}

const _HIDDEN = { x: 0, y: 0, width: 0, height: 0 };

let _win = null; // the BaseWindow/BrowserWindow whose contentView hosts every run's views
let _onTabsChanged = null; // (runId, tabs) => void — wired by main.js to push to the renderer

function init(win, { onTabsChanged } = {}) {
  _win = win;
  _onTabsChanged = onTabsChanged || null;
}

function _partitionFor(runId) {
  return `conxa-run-${runId}`;
}

function _markerUrl(runId, tabId) {
  return `about:blank?conxa_run=${encodeURIComponent(runId)}&conxa_tab=${encodeURIComponent(tabId)}`;
}

// The marker URL is an internal handle the runtime uses to find a view, not an address —
// it must never reach the URL field or a tab label.
function _isMarker(url) {
  return !url || url.startsWith("about:blank");
}

function _hostOf(url) {
  try { return new URL(url).hostname; } catch (_) { return ""; }
}

function _describeTab(run, t) {
  const wc = t.view.webContents;
  const url = wc.isDestroyed() ? "" : wc.getURL();
  const title = wc.isDestroyed() ? "" : wc.getTitle();
  const shown = _isMarker(url) ? "" : url;
  return {
    id: t.id,
    active: t.id === run.activeTabId,
    label: t.label || title || _hostOf(shown) || "New tab",
    url: shown,
    canGoBack: !wc.isDestroyed() && wc.navigationHistory.canGoBack(),
    canGoForward: !wc.isDestroyed() && wc.navigationHistory.canGoForward(),
    loading: !wc.isDestroyed() && wc.isLoading(),
    // AUTH-6: a UI hint only — the raw key (below) never reaches the renderer, just whether
    // this tab is an actual sign-in tab worth showing an "I'm done signing in" action on.
    isLogin: Boolean(t.loginKey),
  };
}

// `meta.focus` tells the renderer to select this run even if another one already holds the
// selection — set only for a login, which is blocking a run on the user.
function _emitTabsChanged(runId, meta) {
  const run = _runs.get(runId);
  if (!run || !_onTabsChanged) return;
  _onTabsChanged(runId, run.tabs.map((t) => _describeTab(run, t)), meta);
}

// Fixed-viewport runs: the page always sees viewport.width x height at DPR 1, drawn at `run.scale`
// (see setActiveBounds), so a site lays out as it did when recorded however big the panel is. Same CDP
// call as Build Studio's panel (conxa-builder/electron/browser_panel.js), issued through Electron's
// debugger so it survives navigation.
function _debugger(tab) {
  if (tab.view.webContents.isDestroyed()) return null;
  const dbg = tab.view.webContents.debugger;
  try {
    if (!dbg.isAttached()) dbg.attach("1.3");
    return dbg;
  } catch (err) {
    console.error("browser_panel: debugger attach failed", err);
    return null;
  }
}

function _emulate(run, tab) {
  if (!run.viewport) return;
  const dbg = _debugger(tab);
  if (!dbg) return;
  return dbg.sendCommand("Emulation.setDeviceMetricsOverride", {
    width: run.viewport.width, height: run.viewport.height, deviceScaleFactor: 1, mobile: false, scale: run.scale,
  }).catch((err) => console.error("browser_panel: setDeviceMetricsOverride failed", err));
}

// The page's real size, read back, and put right if it drifted. Chromium ignores a repeat of the override
// it already holds, so a page whose size another CDP client rewrote (Playwright's clipped screenshot
// does) is only fixed by clearing first. This panel is the ONLY place that sets or clears the size;
// the runtime measures and asks (control op "ensure_geometry"), it never writes it
// (conxa-cloud/tests/test_page_geometry_single_owner.py keeps that true).
async function _ensureGeometry(run, tab) {
  if (!run.viewport) return { ok: true, live: null };
  const dbg = _debugger(tab);
  if (!dbg) return { ok: false, live: null };
  const read = async () => {
    try {
      const r = await dbg.sendCommand("Runtime.evaluate", { expression: "[innerWidth, innerHeight, devicePixelRatio]", returnByValue: true });
      const v = r && r.result && r.result.value;
      return Array.isArray(v) ? v : null;
    } catch (_) { return null; }
  };
  const fits = (v) => !!v && v[0] === run.viewport.width && v[1] === run.viewport.height && Math.abs(v[2] - 1) < 0.01;
  let live = await read();
  if (fits(live)) return { ok: true, live };
  try { await dbg.sendCommand("Emulation.clearDeviceMetricsOverride"); } catch (_) { /* the set below still runs */ }
  await _emulate(run, tab);
  live = await read();
  return { ok: fits(live), live };
}

// Control-channel op "ensure_geometry": every tab of the run, not just the shown one, because a step
// runs on the tab it was recorded on and that tab may be hidden.
async function ensureGeometry(runId) {
  const run = _runs.get(runId);
  if (!run) throw new Error(`browser_panel: no run ${runId}`);
  const results = await Promise.all(run.tabs.map((t) => _ensureGeometry(run, t)));
  return { ok: results.every((r) => r.ok), tabs: results.length };
}

function _viewport(v) {
  if (!v) return null;
  const width = Math.round(Number(v.width)), height = Math.round(Number(v.height));
  if (!(width > 0 && height > 0)) throw new Error("viewport needs a positive width and height");
  return { width, height };
}

// While the runtime drives a run, the person's mouse must not reach the page: a hover opens a menu the recording never
// saw, and a stray click would be taken for a step. A transparent view sits on top of the page and takes the native
// input, while the runtime's CDP input still goes straight to the page beneath it (same as Build Studio's panel).
// A sign-in tab, and any popup it opens, is the person's own and is never covered, and the shield comes off for a
// hand-over. Both are the runtime's call (control op "input"): the shield exists only once a runtime that knows how to
// lift it has asked for it, so an older runtime never meets a shield it cannot lift. It never takes keyboard focus
// (the person may be typing in the chat beside it), and says nothing on the page.
// The marker keeps the runtime from mistaking this page for a tab the run opened (runtime/app/tabs.js::isShieldPage).
const SHIELD_URL = "about:blank?conxa_shield=1";
const SHIELD_JS = `(() => {
  document.documentElement.style.background = "transparent";
  document.body.style.cssText = "margin:0;background:transparent;cursor:not-allowed;overflow:hidden";
})();`;

function _addShield(run) {
  if (run.shield || !_win) return;
  const shield = new WebContentsView({ webPreferences: { sandbox: true } });
  if (typeof shield.setBackgroundColor === "function") shield.setBackgroundColor("#00000000");
  shield.setBounds(_HIDDEN);
  _win.contentView.addChildView(shield);
  shield.webContents.loadURL(SHIELD_URL).then(() => shield.webContents.executeJavaScript(SHIELD_JS)).catch(() => {});
  run.shield = shield;
}

function _dropShield(run) {
  const shield = run.shield;
  if (!shield) return;
  run.shield = null;
  try { if (_win) _win.contentView.removeChildView(shield); } catch (_) {}
  try { shield.webContents.close(); } catch (_) {}
}

const _shieldCovers = (run, tab) => !!(run.shield && !run.handedOver && tab && !tab.human);

// Sized to the page it covers and raised above every tab (adding a child again moves it to the top).
function _layoutShield(run, tab, shown) {
  if (!run.shield) return;
  if (!_shieldCovers(run, tab) || !shown) { run.shield.setBounds(_HIDDEN); return; }
  run.shield.setBounds(shown);
  try { if (_win) _win.contentView.addChildView(run.shield); } catch (_) {}
}

// Control-channel op "input": `open: false` puts the shield over the run's page (the runtime asks once at run
// start, and again when a hand-over ends); `open: true` takes it off, because a hand-over is the one time a person
// acts in the page.
function setInputOpen(runId, open) {
  const run = _runs.get(runId);
  if (!run) return { ok: false };
  const wasOpen = run.handedOver === true;
  run.handedOver = open === true;
  if (!run.handedOver) _addShield(run);
  const tab = run.tabs.find((t) => t.id === run.activeTabId);
  _layoutShield(run, tab, run.shownRect);
  if (run.handedOver) { if (tab && !tab.view.webContents.isDestroyed()) tab.view.webContents.focus(); }
  // The person just used the page; typing goes back to the app, not to the page the shield now covers again.
  else if (wasOpen && _win && _win.webContents && !_win.webContents.isDestroyed()) _win.webContents.focus();
  return { ok: true };
}

// Create a view, wire its popup handler, and register it as a tab on `run`. The new tab
// becomes the active one and the renderer is told — this is the only place that happens, so a
// window.open() popup (an OAuth "Sign in with Google" leg) is shown exactly like a tab the
// runtime asked for, instead of sitting at 0x0 unannounced.
function _createTab(run, runId, label, meta, loginKey, background = false, human = false) {
  const tabId = crypto.randomBytes(4).toString("hex");
  const view = new WebContentsView({
    webPreferences: { partition: run.partition, sandbox: true },
  });
  view.webContents.setWindowOpenHandler(({ url }) => {
    const opener = run.tabs.find((t) => t.view === view);
    const newTabId = _createTab(run, runId, undefined, undefined, undefined, false, Boolean(opener && opener.human)); // an OAuth leg of a sign-in stays the person's
    const newView = run.tabs.find((t) => t.id === newTabId).view;
    // A real popup navigates to its actual destination itself (target=_blank); loading it
    // here too would race that navigation, so only seed it when the handler actually carries
    // a concrete URL (Electron passes "" for some window.open() calls with no url arg).
    if (url && url !== "about:blank") newView.webContents.loadURL(url).catch(() => {});
    return { action: "deny" }; // denied at the OS level; we already created our own view for it
  });
  // Keep the renderer's tab strip (title, URL, back/forward, spinner) in step with the page.
  for (const ev of ["page-title-updated", "did-navigate", "did-navigate-in-page", "did-start-loading", "did-stop-loading"]) {
    view.webContents.on(ev, () => _emitTabsChanged(runId));
  }
  view.setBounds({ x: 0, y: 0, width: 0, height: 0 }); // hidden until the renderer reports a rect
  view.webContents.loadURL(_markerUrl(runId, tabId)).catch(() => {});
  _emulate(run, { view }); // after loadURL: attaching the debugger to a never-navigated view crashes Electron
  // A navigation (marker page -> real site, a new render process) can drop the override, leaving the
  // page at full size inside a shrunk view. Check (and put right) each time a page is ready.
  for (const ev of ["dom-ready", "did-finish-load"]) view.webContents.on(ev, () => { _ensureGeometry(run, { view }).catch(() => {}); });
  run.tabs.push({ id: tabId, view, markerUrl: _markerUrl(runId, tabId), label: label || null, loginKey: loginKey || null, human: Boolean(loginKey) || human });
  if (_win) _win.contentView.addChildView(view);
  // A background tab (a judge/prover probe, focus:false) must not steal the shown view from the
  // sign-in the user is typing into — it stays parked at 0x0 until closed.
  if (!background || run.activeTabId === null) run.activeTabId = tabId;
  _emitTabsChanged(runId, meta);
  return tabId;
}

// Downloads. A run's views live in their own partition, so Electron's download manager handles
// every file — Playwright's download.saveAs() never gets it (it looks in its own artifacts dir),
// and with no handler Electron pops its native Save As dialog, which blocks the run on a human
// (EXEC-46). So every run download is saved here silently, and the runtime claims the finished
// file by URL through the save_download op (saveDownload below).
function _downloadDir(runId) {
  return path.join(os.tmpdir(), "conxa-execute-downloads", runId.replace(/[^\w.-]/g, "_"));
}
const _hookedPartitions = new Set(); // Electron keeps one session per partition name for the app's life
function _hookDownloads(runId, partition) {
  if (_hookedPartitions.has(partition)) return;
  _hookedPartitions.add(partition);
  session.fromPartition(partition).on("will-download", (_e, item) => {
    const run = _runs.get(runId);
    if (!run) { item.cancel(); return; }
    const dir = _downloadDir(runId);
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, String(run.downloads.length + 1));
    item.setSavePath(file); // a set path is what stops Electron from asking the user
    run.downloads.push({
      url: item.getURL(),
      done: new Promise((resolve) => item.once("done", (_ev, state) => resolve(state === "completed" ? file : null))),
    });
  });
}

// Control-channel op: "save_download" — hand the finished file for `url` (the first not yet
// claimed, so a loop downloading the same URL twice gets both) to the runtime at `dest`.
async function saveDownload(runId, url, dest, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  let entry;
  for (;;) {
    const run = _runs.get(runId);
    entry = run && run.downloads.find((d) => !d.claimed && d.url === url);
    if (entry || Date.now() > deadline) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  if (!entry) throw new Error(`no download of ${url} in run ${runId}`);
  entry.claimed = true;
  const file = await entry.done;
  if (!file) throw new Error(`download of ${url} did not complete`);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(file, dest);
  fs.rmSync(file, { force: true });
  return { ok: true };
}

// Control-channel op: "new_view" — first tab of a run. Returns the marker URL the
// runtime waits for (host_browser.js::_findPageByMarker). `loginKey` (AUTH-6) is set only for an
// actual sign-in tab, never a judge/prover probe tab — see browser_control.js's op doc.
function newView(runId, { label, focus, loginKey, viewport } = {}) {
  let run = _runs.get(runId);
  if (!run) {
    run = { partition: _partitionFor(runId), tabs: [], activeTabId: null, downloads: [], viewport: _viewport(viewport), scale: 1 };
    _runs.set(runId, run);
    _hookDownloads(runId, run.partition);
  }
  const tabId = _createTab(run, runId, label, focus ? { focus: true } : undefined, loginKey);
  return { markerUrl: run.tabs.find((t) => t.id === tabId).markerUrl, tabId };
}

// Control-channel op: "new_tab" — a tab_open step's second (or later) tab, or a sign-in / probe tab
// of a browser session, in a run that already has a panel open. `focus` is set for a sign-in.
function newTab(runId, { label, focus, loginKey } = {}) {
  const run = _runs.get(runId);
  if (!run) throw new Error(`browser_panel: no run ${runId} to add a tab to`);
  const tabId = _createTab(run, runId, label, focus ? { focus: true } : undefined, loginKey, focus === false);
  return { markerUrl: run.tabs.find((t) => t.id === tabId).markerUrl, tabId };
}

// AUTH-6: the renderer's "I'm done signing in" button, wired through panel:login-done in main.js.
// Looks up the tab's own loginKey (never trusting the renderer to know or send it — see
// _describeTab's isLogin comment) and writes the same file-drop signal the CLI subcommand and
// browser.js's _checkHumanOverride agree on: <CONXA_DIR>/login-done/<key>.cmd, existence-only,
// same accepted local-trust model as handover.js's own resume file. A no-op for any tab that
// isn't a real sign-in tab.
function _conxaDir() {
  if (process.env.CONXA_DIR) return process.env.CONXA_DIR;
  const cmd = resolveRuntimeCommand();
  if (cmd && cmd.conxaDir) return cmd.conxaDir;
  return path.join(os.homedir(), ".conxa");
}
function loginDone(runId, tabId) {
  const run = _runs.get(runId);
  const tab = run && run.tabs.find((t) => t.id === tabId);
  if (!tab || !tab.loginKey) return { ok: false };
  try {
    const dir = path.join(_conxaDir(), "login-done");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, `${tab.loginKey}.cmd`), "done");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Close ONE view. A login finishing (or the user hitting ×) must not take its sibling
// logins down with it — that was the bug run_end's run-wide teardown caused. The last tab
// out falls through to runEnd so destroy + partition wipe + empty emit stay in one place.
async function closeTab(runId, tabId) {
  const run = _runs.get(runId);
  if (!run) return;
  const idx = run.tabs.findIndex((t) => t.id === tabId);
  if (idx === -1) return;
  if (run.tabs.length === 1) return runEnd(runId);
  const [gone] = run.tabs.splice(idx, 1);
  try { if (_win) _win.contentView.removeChildView(gone.view); } catch (_) {}
  try { gone.view.webContents.close(); } catch (_) {}
  if (run.activeTabId === tabId) {
    // Promote the neighbour; the renderer re-reports bounds for it on the next emit.
    run.activeTabId = run.tabs[Math.min(idx, run.tabs.length - 1)].id;
  }
  _emitTabsChanged(runId);
}

// Address-bar behaviour for a typed string: a bare host gets https://, anything with a
// scheme is left alone. No search fallback — this is an automation browser.
function _normalizeUrl(raw) {
  const v = String(raw || "").trim();
  if (!v) return "";
  if (/^[a-z][a-z0-9+.-]*:/i.test(v)) return v;
  return `https://${v}`;
}

function navigate(runId, tabId, { action, url } = {}) {
  const run = _runs.get(runId);
  const t = run && run.tabs.find((x) => x.id === tabId);
  if (!t) return false;
  const wc = t.view.webContents;
  switch (action) {
    case "back": wc.navigationHistory.goBack(); break;
    case "forward": wc.navigationHistory.goForward(); break;
    case "reload": wc.reload(); break;
    case "stop": wc.stop(); break;
    case "load": {
      const target = _normalizeUrl(url);
      if (!target) return false;
      wc.loadURL(target).catch(() => {});
      break;
    }
    default: return false;
  }
  return true;
}

// Control-channel op: "run_end" — destroy every view for this run and wipe its
// partition's storage. Best-effort by design (see host_browser.js::release) — a run
// whose end notification never arrives just sits until the caller retries or the app
// restarts; nothing else depends on this firing promptly.
async function runEnd(runId) {
  const run = _runs.get(runId);
  if (!run) return;
  _runs.delete(runId);
  _chatOf.delete(runId);
  _dropShield(run);
  for (const t of run.tabs) {
    try { if (_win) _win.contentView.removeChildView(t.view); } catch (_) {}
    try { t.view.webContents.close(); } catch (_) {}
  }
  try {
    await session.fromPartition(run.partition).clearStorageData();
  } catch (err) {
    // Not fatal to the run (already ended) but leaves this run's cookies/storage
    // sitting in the partition indefinitely — worth knowing about, not swallowing.
    console.error(`browser_panel: failed to clear storage for run ${runId}`, err);
  }
  try { fs.rmSync(_downloadDir(runId), { recursive: true, force: true }); } catch (_) {}
  if (_onTabsChanged) _onTabsChanged(runId, []);
}

// Renderer reports where the (non-tab-strip) panel rect is, in window-local pixels.
// Only the active tab's view gets shown there; every other tab in every run is
// parked off-screen (0x0) rather than destroyed, so switching tabs is instant.
function setActiveBounds(runId, tabId, rect) {
  const run = _runs.get(runId);
  if (!run) return;
  for (const [id, r] of _runs) {
    if (id === runId) continue;
    for (const t of r.tabs) t.view.setBounds(_HIDDEN); // a native view never goes away on its own
    if (r.shield) r.shield.setBounds(_HIDDEN);
    r.shownRect = null;
  }
  let shown = rect;
  if (run.viewport) {
    // Letterboxed: the view keeps the viewport's aspect ratio, shrunk (never enlarged) to fit the rect
    // and centred; every tab of the run shares one scale so none shows at a stale size.
    const { width: W, height: H } = run.viewport;
    const s = Math.min(rect.width / W, rect.height / H, 1);
    const width = Math.round(W * s), height = Math.round(H * s);
    shown = { x: rect.x + Math.floor((rect.width - width) / 2), y: rect.y + Math.floor((rect.height - height) / 2), width, height };
    if (s > 0 && Math.abs(s - run.scale) > 1e-4) {
      run.scale = s;
      for (const t of run.tabs) _emulate(run, t);
    }
  }
  for (const t of run.tabs) {
    t.view.setBounds(t.id === tabId ? shown : _HIDDEN);
  }
  run.activeTabId = tabId;
  run.shownRect = shown;
  _layoutShield(run, run.tabs.find((t) => t.id === tabId), shown);
  _shown = { runId, tabId };
}

// The last tab placed on screen — what "Capture the browser panel" grabs.
let _shown = null;

// The user hid the panel: park every view. Showing it again re-reports bounds via setActiveBounds.
function hideAll() {
  for (const run of _runs.values()) {
    for (const t of run.tabs) t.view.setBounds(_HIDDEN);
    if (run.shield) run.shield.setBounds(_HIDDEN);
    run.shownRect = null;
  }
  _shown = null;
}

async function captureVisible() {
  const run = _shown && _runs.get(_shown.runId);
  const tab = run && run.tabs.find((t) => t.id === _shown.tabId);
  if (!tab) throw Object.assign(new Error("Nothing is open in the browser panel."), { code: "panel_empty" });
  const img = await tab.view.webContents.capturePage();
  return `data:image/jpeg;base64,${img.toJPEG(85).toString("base64")}`;
}

// Control-channel op "capture": a still of a run's tab, as base64 JPEG/PNG. The runtime's own screenshot is a CDP
// call that only answers while the view is being drawn, so it hangs on a hidden tab, a chat the user left or a
// minimized window; capturePage with stayHidden/stayAwake answers in all of those (probe, Electron 42).
async function capture(runId, tabId, { type = "jpeg", quality = 80 } = {}) {
  const run = _runs.get(runId);
  const tab = run && (run.tabs.find((t) => t.id === tabId) || run.tabs.find((t) => t.id === run.activeTabId));
  if (!tab || tab.view.webContents.isDestroyed()) throw new Error(`browser_panel: no tab to capture in run ${runId}`);
  const img = await tab.view.webContents.capturePage(undefined, { stayHidden: true, stayAwake: true });
  const buf = type === "png" ? img.toPNG() : img.toJPEG(Math.max(1, Math.min(100, Math.round(Number(quality) || 80))));
  return { data: buf.toString("base64") };
}

// The user moved to another chat: park every view that belongs to a different chat at 0x0. The
// views stay alive (the run keeps going); coming back re-places them via setActiveBounds.
function setVisibleChat(chatId) {
  for (const [runId, run] of _runs) {
    const owner = _chatOf.get(runId);
    if (owner && owner !== chatId) {
      for (const t of run.tabs) t.view.setBounds(_HIDDEN);
      if (run.shield) run.shield.setBounds(_HIDDEN);
      run.shownRect = null;
      if (_shown && _shown.runId === runId) _shown = null;
    }
  }
}

module.exports = { init, tagRun, chatOf, newView, newTab, closeTab, navigate, runEnd, setActiveBounds, setVisibleChat, hideAll, captureVisible, capture, loginDone, saveDownload, ensureGeometry, setInputOpen };
