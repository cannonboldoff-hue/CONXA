"use strict";
/**
 * browser_panel.js — Build Studio's side of the embedded test browser (BUILD-42).
 *
 * The same seam Conxa Execute uses (conxa-execute/app/electron/browser_panel.js, EXEC-41): the
 * runtime's test run borrows a native WebContentsView instead of opening a separate Chromium
 * window, attaching with Playwright's connectOverCDP (runtime/app/host_browser.js). This file owns
 * the views. Electron's CDP target can't create pages or contexts, so every page is created HERE and
 * the runtime finds it by a marker URL.
 *
 * Differences from Execute's, all on purpose:
 *  - A run has a `kind`: "test" (the runtime's test run), "record" or "signin" (the recorder, which
 *    builds its own per-tab video from a CDP screencast and its own storage state — see
 *    docs/archive/spikes/in-app-recording-2026-10.md). A run created with a fixed `viewport` keeps
 *    the page at exactly that size at DPR 1 whatever the panel's size, scaled down to fit; the
 *    recorder asks `scale` to read its video frames.
 *  - A test run that ENDS does not close its view. The mockup keeps the final page on screen so the
 *    person can check the result themselves ("Stays on the final page so you can check the result");
 *    run_end marks it finished and the person (or the next test) closes it. Record and sign-in runs
 *    are closed by the backend (close_run) when they finish.
 *  - Nothing here is chat-scoped.
 *
 * Each run gets its own in-memory partition (`conxa-run-<runId>`), so one run's cookies can't reach
 * another's and closing is just "stop referencing these views and wipe the partition".
 */
const { session, WebContentsView } = require("electron");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

// runId -> { partition, kind, viewport, height, scale, rect, tabs: [{ id, view, markerUrl, label, opener }],
//            activeTabId, downloads: [], finished }
// `height` is the page's live CSS height: viewport.height, except a viewport with fit "width" lets it
// follow the pane (the page stays viewport.width wide, so its layout never changes).
const _runs = new Map();
const KINDS = new Set(["test", "record", "signin"]);
const _HIDDEN = { x: 0, y: 0, width: 0, height: 0 };
// A page lays out at its view's size (zoomed), so a view shrunk to 0x0 is a 0-wide page, and a view born at
// 0x0 stays one until something resizes it. Nothing a run drives is ever shrunk: a view the panel is not
// showing (another tab, a dialog over the pane, a folded pane, a minimized window) is hidden with setVisible
// and keeps its bounds, so the page keeps the size it was recorded at whether or not anyone can see it
// (probe, Electron 42: a hidden view stays 1280x720, a 0x0 one reads 0x0). Only the shield, which nothing
// drives, is still parked at _HIDDEN.
let _paneHidden = false; // the renderer's pane is covered, folded or gone; cleared by its next real rect
let _lastRect = null; // the last real pane rect, so a run's first view is placed without waiting for a report
const _validRect = (r) => !!r && r.width > 0 && r.height > 0;
const _hideView = (view) => view.setVisible(false);
// The page height a fit-to-width pane may ask for. runtime/app/page_geometry.js mirrors MIN_H
// (a pytest keeps them equal).
const MIN_H = 480, MAX_H = 1440;

let _win = null;
let _onTabsChanged = null; // (runId, tabs, meta) => void — wired by main.js to push to the renderer
let _onShortcut = null; // (runId, action) => void — keys only the renderer can act on (focus the address bar)

function init(win, { onTabsChanged, onShortcut } = {}) {
  _win = win;
  _onTabsChanged = onTabsChanged || null;
  _onShortcut = onShortcut || null;
}

const _partitionFor = (runId) => `conxa-run-${runId}`;
// Test runs share one on-disk partition so its HTTP cache survives from test to test: a fresh in-memory
// partition per run re-downloads every asset over cold connections, which is what made tests crawl on a
// flaky network. Cookies and site storage are wiped before and after every run (prepareTestRun, closeRun);
// only the cache is kept. Record and sign-in runs keep per-run partitions (their cookie capture must see
// nothing else), as does a test run that overlaps another live one on the shared partition.
const SHARED_PARTITION = "persist:conxa-test-cache";
const _sharedInUse = () => [..._runs.values()].some((r) => r.partition === SHARED_PARTITION);
const _markerUrl = (runId, tabId) => `about:blank?conxa_run=${encodeURIComponent(runId)}&conxa_tab=${encodeURIComponent(tabId)}`;
// The marker is an internal handle, never an address — it must not reach the URL field or a label.
const _isMarker = (url) => !url || url.startsWith("about:blank");
// Guided tour (BUILD-79): a note drawn inside the page. Allowed ONLY in sign-in runs (they install no
// recorder, so nothing is captured) and ONLY on the Conxa website host. Never in record or test runs.
let _tourHint = null; // { target, text } | null
const _tourScript = require("fs").readFileSync(path.join(__dirname, "tour_hint.js"), "utf8");
function _tourHost() {
  try { return new URL(process.env.CONXA_WEB_ORIGIN || "https://www.conxa.in").host; } catch (_) { return ""; }
}
function _tourAllowed(run, wc) {
  if (!run || run.kind !== "signin" || !_tourHint) return false;
  try { return new URL(wc.getURL()).host === _tourHost(); } catch (_) { return false; }
}
function _injectTourHint(run, tab) {
  const wc = tab.view.webContents;
  if (wc.isDestroyed() || !_tourAllowed(run, wc)) return;
  const arg = JSON.stringify({ target: String(_tourHint.target || ""), text: String(_tourHint.text || "") });
  wc.executeJavaScriptInIsolatedWorld(2001, [{ code: `${_tourScript}
;__conxaTourHint(${arg});` }]).catch(() => {});
}
function setTourHint(hint) {
  _tourHint = hint && hint.text ? { target: hint.target, text: hint.text } : null;
  for (const run of _runs.values()) for (const tab of run.tabs) {
    const wc = tab.view.webContents;
    if (wc.isDestroyed()) continue;
    if (_tourHint) _injectTourHint(run, tab);
    else if (run.kind === "signin") wc.executeJavaScriptInIsolatedWorld(2001, [{ code: "window.__conxaTourHintClear && window.__conxaTourHintClear();" }]).catch(() => {});
  }
}
function _hostOf(url) {
  try { return new URL(url).hostname; } catch (_) { return ""; }
}

function _describeTab(run, t) {
  const wc = t.view.webContents;
  const gone = wc.isDestroyed();
  const url = gone ? "" : wc.getURL();
  const shown = _isMarker(url) ? "" : url;
  return {
    id: t.id,
    active: t.id === run.activeTabId,
    label: t.label || (gone ? "" : wc.getTitle()) || _hostOf(shown) || "New tab",
    url: shown,
    canGoBack: !gone && wc.navigationHistory.canGoBack(),
    canGoForward: !gone && wc.navigationHistory.canGoForward(),
    loading: !gone && wc.isLoading(),
    opener: t.opener,
    // "site" = the page opened it (link, window.open); "person" = the + button, Ctrl+T or a menu.
    openedBy: t.openedBy || (t.opener ? "site" : "initial"),
    favicon: t.favicon || null,
  };
}

function _emit(runId, meta) {
  const run = _runs.get(runId);
  if (!run || !_onTabsChanged) return;
  _onTabsChanged(runId, run.tabs.map((t) => _describeTab(run, t)), {
    finished: run.finished, kind: run.kind, downloads: run.downloads.map(_describeDownload), ...(meta || {}),
  });
}

// Fixed-viewport runs: the page always sees viewport.width x height at DPR 1, drawn at `scale`.
// This is the same CDP call Playwright makes for its own fixed viewport, issued through Electron's
// debugger so it survives navigation (webContents.enableDeviceEmulation was lost on the first one).
// Playwright attaching over CDP alongside this debugger session was verified in the spike.
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

// How a run's page is shrunk to fit the pane. A record run is driven by the person's own mouse, which
// Chromium maps through the override's `scale`. Every other run is driven by Playwright over CDP, and
// CDP Input events are NOT mapped through `scale` (a click lands at 1/scale of where it was aimed), while
// a device scale factor shrinks the render but not the layout (the page stays full size and is clipped).
// So those runs use the view's zoom factor: the page lays out at pane/zoom = viewport.width CSS pixels,
// is painted shrunk, and CDP input lands exactly (spike, Electron 42). The pane's size is the only
// input: no metrics override is set at all, so `innerHeight` follows the pane by itself.
function _setZoom(tab, z) {
  try { if (!tab.view.webContents.isDestroyed()) tab.view.webContents.setZoomFactor(z); } catch (err) {
    console.error("browser_panel: setZoomFactor failed", err);
  }
}

function _emulate(run, tab) {
  if (!run.viewport) return;
  if (run.kind !== "record") { _setZoom(tab, run.scale); return; }
  _setZoom(tab, 1); // a test handed to the recorder (adoptRun) must not shrink twice
  const dbg = _debugger(tab);
  if (!dbg) return;
  return dbg.sendCommand("Emulation.setDeviceMetricsOverride", {
    width: run.viewport.width, height: run.height, deviceScaleFactor: 1, mobile: false, scale: run.scale,
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
  // A record page is at exact size and DPR 1. A zoomed (test) page's DPR is the screen's DPR times the zoom,
  // and its layout size is the pane divided by the zoom, so it is off by a pixel from rounding.
  const fits = (v) => !!v && (run.kind === "record"
    ? v[0] === run.viewport.width && v[1] === run.height && Math.abs(v[2] - 1) < 0.01
    : Math.abs(v[0] - run.viewport.width) <= 1 && Math.abs(v[1] - run.height) <= 2);
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
  const check = () => Promise.all(run.tabs.map((t) => _ensureGeometry(run, t)));
  let results = await check();
  if (!results.every((r) => r.ok) && run.rect && run.activeTabId) {
    // A view left without its size (the pane's rect never reached it) is only put right by laying the
    // run out again from the rect the panel last had; re-setting the zoom cannot size a 0x0 view.
    _layout(run, run.activeTabId, run.rect);
    results = await check();
  }
  return { ok: results.every((r) => r.ok), tabs: results.length };
}

// Control-channel op "capture": a still of a run's tab as JPEG/PNG bytes (base64). The runtime's own
// screenshot is a CDP call that only answers when the view is being drawn, so it hangs on a hidden tab, a
// covered pane or a minimized window (probe, Electron 42); capturePage with stayHidden/stayAwake draws
// the page for the capture and answers in every one of those states. It sees the view at its on-screen
// scale, so the runtime asks for it only after its own capture did not answer.
async function capture(runId, tabId, { type = "jpeg", quality = 80 } = {}) {
  const run = _runs.get(runId);
  const tab = run && (run.tabs.find((t) => t.id === tabId) || run.tabs.find((t) => t.id === run.activeTabId));
  if (!tab || tab.view.webContents.isDestroyed()) throw new Error(`browser_panel: no tab to capture in run ${runId}`);
  const img = await tab.view.webContents.capturePage(undefined, { stayHidden: true, stayAwake: true });
  const buf = type === "png" ? img.toPNG() : img.toJPEG(Math.max(1, Math.min(100, Math.round(Number(quality) || 80))));
  return { data: buf.toString("base64") };
}

// While the runtime drives a test run, the person's mouse and keyboard must not reach the page: a pointer resting on a
// file name opens a hover card the run never made, and a stray click would be taken for a step. Electron's
// `before-mouse-event` cannot separate the person from the runtime (spike, Electron 42: it fires for CDP input, and
// blocking it blocks the run's own clicks). So a transparent view sits on top of the page and takes the native input,
// while the runtime's CDP input still goes straight to the page beneath it. The person's own tabs are never covered,
// and the shield is lifted when the run ends, is adopted by a recorder or is closed.
// The marker keeps the runtime from mistaking this page for a tab the run opened (runtime/app/tabs.js::isShieldPage).
const SHIELD_URL = "about:blank?conxa_shield=1";
const SHIELD_JS = `(() => {
  document.documentElement.style.background = "transparent";
  document.body.style.cssText = "margin:0;background:transparent;cursor:not-allowed;overflow:hidden";
  const h = document.createElement("div");
  h.textContent = "Hands off while the test runs. It will tell you when it needs you.";
  h.style.cssText = "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);display:none;padding:8px 14px;border-radius:8px;background:rgba(30,30,30,.9);color:#fff;font:13px system-ui,sans-serif";
  document.body.appendChild(h);
  let t;
  addEventListener("mousedown", () => { h.style.display = "block"; clearTimeout(t); t = setTimeout(() => { h.style.display = "none"; }, 2500); });
})();`;

function _addShield(run) {
  if (run.kind !== "test" || run.shield || !_win) return;
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
  try { if (_win) _win.contentView.removeChildView(shield); } catch (_) { /* best effort */ }
  try { shield.webContents.close(); } catch (_) { /* best effort */ }
}

const _shieldCovers = (run, tab) => !!(run.shield && !run.finished && !run.handedOver && tab && tab.openedBy !== "person");

// Sized to the page it covers, raised above every tab (adding a child again moves it to the top), and given the
// keyboard focus so typing does not reach the page either.
function _layoutShield(run, tab, shown) {
  if (!run.shield) return;
  if (!_shieldCovers(run, tab) || !shown) { run.shield.setBounds(_HIDDEN); return; }
  run.shield.setBounds(shown);
  try { if (_win) _win.contentView.addChildView(run.shield); } catch (_) { /* best effort */ }
  try { if (!run.shield.webContents.isDestroyed()) run.shield.webContents.focus(); } catch (_) { /* best effort */ }
}

// Wires a view (ours, or one Chromium created for a popup) into the run as a tab.
function _addTab(run, runId, view, { label, background = false, marker = true, opener = null, openedBy = null } = {}) {
  const tabId = crypto.randomBytes(4).toString("hex");
  const tab = { id: tabId, view, markerUrl: marker ? _markerUrl(runId, tabId) : null, label: label || null, opener, openedBy };
  // A site-opened popup or new tab becomes a tab in the panel, never a stray window. Chromium creates
  // its webContents (createWindow), so window.opener and Playwright's page.opener() keep working;
  // denying and loading the URL into a view of our own crashed Electron in the spike.
  view.webContents.setWindowOpenHandler(() => ({
    action: "allow",
    createWindow: (options) => {
      const nv = new WebContentsView({ webContents: options.webContents, webPreferences: options.webPreferences });
      _addTab(run, runId, nv, { marker: false, opener: tabId });
      return nv.webContents;
    },
  }));
  for (const ev of ["page-title-updated", "did-navigate", "did-navigate-in-page", "did-start-loading", "did-stop-loading"]) {
    view.webContents.on(ev, () => _emit(runId));
  }
  view.webContents.on("did-start-navigation", (_e, _url, isInPlace, isMainFrame) => {
    if (isMainFrame && !isInPlace) tab.favicon = null;
  });
  view.webContents.on("page-favicon-updated", (_e, favicons) => {
    tab.favicon = (favicons || []).find((u) => /^(https?|data):/.test(u)) || null;
    _emit(runId);
  });
  // A native view gets the keyboard while the page has focus, so the browser's own shortcuts are
  // caught here, before the page sees them (and so before a recording could record them as typing).
  view.webContents.on("before-input-event", (e, input) => {
    if (_shortcut(runId, tabId, input)) e.preventDefault();
  });
  view.webContents.on("context-menu", (_e, params) => _contextMenu(runId, tabId, params));
  // The page closed itself (window.close() — a sign-in popup finishing): drop its tab.
  view.webContents.once("destroyed", () => _forgetTab(runId, tabId));
  view.setBounds(_HIDDEN); // parked until the renderer reports a rect
  // Attaching the debugger to a never-navigated view crashes Electron (the whole app, no message). Our own tabs
  // load their marker first. A site-opened tab arrives here from inside Chromium's createWindow, before its first
  // page has started, so its debugger work waits for that page (a recording died on every "open in a new tab" link).
  const wire = () => {
    _emulate(run, tab);
    // The recorder sees every page in Electron (Studio's own window, other runs) through one CDP
    // context; a tab's devtools target id is how it tells which pages are its run's (`targets`).
    if (run.kind === "test") return Promise.resolve();
    const dbg = _debugger(tab);
    return dbg ? dbg.sendCommand("Target.getTargetInfo").then((r) => { tab.targetId = r.targetInfo.targetId; }, () => {}) : Promise.resolve();
  };
  if (tab.markerUrl) {
    view.webContents.loadURL(tab.markerUrl).catch(() => {});
    tab.targetReady = wire();
  } else {
    tab.targetReady = new Promise((resolve) => {
      let wired = false;
      const once = () => { if (wired || view.webContents.isDestroyed()) return; wired = true; wire().then(resolve, resolve); };
      for (const ev of ["did-navigate", "dom-ready", "did-finish-load"]) view.webContents.once(ev, () => setImmediate(once));
      // `targets` waits on every tab: a popup that never loads a page (or closes first) must not hold the recorder. It
      // is then left out of the run's targets until it loads (wired late, it still gets its size and id).
      setTimeout(resolve, 5000);
    });
  }
  // A navigation (the blank marker page -> a real site, which swaps the render process) can drop the
  // override, leaving the page at full size inside a shrunk view: clipped on the right and bottom.
  // So the size is checked (and put right) each time a page is ready, not only when the tab is created.
  for (const ev of ["dom-ready", "did-finish-load"]) {
    view.webContents.on(ev, () => { _ensureGeometry(run, tab).catch(() => {}); if (ev === "dom-ready") _injectTourHint(run, tab); });
  }
  run.tabs.push(tab);
  if (_win) _win.contentView.addChildView(view);
  if (!background || run.activeTabId === null) run.activeTabId = tabId;
  // Placed now, from what the panel already knows, never left at 0x0 until the renderer reports: a step
  // that opens a tab reads its size right away, and the renderer may be slow (or hidden) to answer.
  const rect = run.rect || (run.kind === "test" ? _lastRect : null);
  if (rect) { run.rect = rect; _layout(run, run.activeTabId, rect); }
  _emit(runId);
  return tabId;
}

function _createTab(run, runId, label, background = false, openedBy = null) {
  const view = new WebContentsView({ webPreferences: { partition: run.partition, sandbox: true } });
  return _addTab(run, runId, view, { label, background, openedBy });
}

// A tab the PERSON opens (the + button, Ctrl+T, "Open link in new tab"), as in any browser: it comes
// to the front, blank or on `url`. It is a run tab like any other — the recorder sees it as a tab
// opened by the user (no opener), exactly as Ctrl+T in the old separate window.
function personTab(runId, { url } = {}) {
  const run = _runs.get(runId);
  if (!run) throw new Error(`browser_panel: no run ${runId} to add a tab to`);
  const tabId = _createTab(run, runId, null, false, "person");
  if (url) {
    const tab = run.tabs.find((t) => t.id === tabId);
    // After the marker has loaded, so the recorder still finds the tab by it.
    tab.view.webContents.once("did-stop-loading", () => navigate(runId, tabId, { action: "load", url }));
  }
  return { tabId };
}

function _activate(runId, tabId) {
  const run = _runs.get(runId);
  const t = run && run.tabs.find((x) => x.id === tabId);
  if (!t) return false;
  if (run.rect) _layout(run, tabId, run.rect);
  else run.activeTabId = tabId;
  if (!_shieldCovers(run, t) && !t.view.webContents.isDestroyed()) t.view.webContents.focus();
  _emit(runId);
  return true;
}

// Chrome's tab and page keys. Returns true when handled (the page never sees the key).
function _shortcut(runId, tabId, input) {
  if (input.type !== "keyDown") return false;
  const run = _runs.get(runId);
  if (!run) return false;
  const key = String(input.key || "");
  const lower = key.toLowerCase();
  const mod = input.control || input.meta;
  const idx = run.tabs.findIndex((t) => t.id === tabId);
  const cycle = (d) => _activate(runId, run.tabs[(idx + d + run.tabs.length) % run.tabs.length].id);
  if (mod && !input.shift && lower === "t") {
    personTab(runId);
    if (_onShortcut) _onShortcut(runId, "focus-address");
    return true;
  }
  if (mod && lower === "w") { void closeTab(runId, tabId); return true; }
  if ((mod && lower === "l") || key === "F6" || (input.alt && lower === "d")) {
    if (_onShortcut) _onShortcut(runId, "focus-address");
    return true;
  }
  if ((mod && lower === "r") || key === "F5") {
    navigate(runId, tabId, { action: input.shift ? "hard-reload" : "reload" });
    return true;
  }
  if (input.alt && key === "ArrowLeft") { navigate(runId, tabId, { action: "back" }); return true; }
  if (input.alt && key === "ArrowRight") { navigate(runId, tabId, { action: "forward" }); return true; }
  if (mod && (key === "Tab" || key === "PageDown" || key === "PageUp")) {
    cycle(key === "PageUp" || (key === "Tab" && input.shift) ? -1 : 1);
    return true;
  }
  if (mod && /^[1-9]$/.test(key)) {
    const n = Number(key);
    const target = n === 9 ? run.tabs[run.tabs.length - 1] : run.tabs[n - 1];
    if (target) _activate(runId, target.id);
    return true;
  }
  return false;
}

// Right-click, as in Chrome — a native view has no menu of its own. `Menu` is looked up here so the
// panel's unit tests (which stub electron) don't need it.
function _contextMenu(runId, tabId, params) {
  const run = _runs.get(runId);
  const t = run && run.tabs.find((x) => x.id === tabId);
  if (!t || t.view.webContents.isDestroyed()) return;
  const { Menu, clipboard } = require("electron");
  const wc = t.view.webContents;
  const flags = params.editFlags || {};
  const items = [];
  if (params.linkURL) {
    items.push(
      { label: "Open link in new tab", click: () => personTab(runId, { url: params.linkURL }) },
      { label: "Copy link address", click: () => clipboard.writeText(params.linkURL) },
    );
  }
  if (params.isEditable) {
    if (items.length) items.push({ type: "separator" });
    items.push(
      { label: "Undo", role: "undo", enabled: flags.canUndo },
      { label: "Redo", role: "redo", enabled: flags.canRedo },
      { type: "separator" },
      { label: "Cut", role: "cut", enabled: flags.canCut },
      { label: "Copy", role: "copy", enabled: flags.canCopy },
      { label: "Paste", role: "paste", enabled: flags.canPaste },
      { label: "Select all", role: "selectAll" },
    );
  } else if (params.selectionText) {
    if (items.length) items.push({ type: "separator" });
    items.push({ label: "Copy", role: "copy" });
  } else if (!params.linkURL) {
    items.push(
      { label: "Back", enabled: wc.navigationHistory.canGoBack(), click: () => navigate(runId, tabId, { action: "back" }) },
      { label: "Forward", enabled: wc.navigationHistory.canGoForward(), click: () => navigate(runId, tabId, { action: "forward" }) },
      { label: "Reload", click: () => navigate(runId, tabId, { action: "reload" }) },
    );
  }
  if (items.length) Menu.buildFromTemplate(items).popup({ window: _win || undefined });
}

function _forgetTab(runId, tabId) {
  const run = _runs.get(runId);
  if (!run) return;
  const idx = run.tabs.findIndex((t) => t.id === tabId);
  if (idx === -1) return;
  const [gone] = run.tabs.splice(idx, 1);
  try { if (_win) _win.contentView.removeChildView(gone.view); } catch (_) { /* best effort */ }
  if (run.tabs.length === 0) { void closeRun(runId); return; }
  if (run.activeTabId === tabId) {
    const back = run.tabs.find((t) => t.id === gone.opener) || run.tabs[Math.min(idx, run.tabs.length - 1)];
    run.activeTabId = back ? back.id : null;
    if (back && run.rect) _layout(run, back.id, run.rect);
  }
  _emit(runId);
}

// Downloads in a run's partition never reach Playwright's download.saveAs(); Electron's own download
// manager would also pop a native Save As dialog that blocks the run on a person. So every download is
// saved silently here and the runtime claims the finished file by URL (save_download).
const _downloadDir = (runId) => path.join(os.tmpdir(), "conxa-studio-downloads", runId.replace(/[^\w.-]/g, "_"));
const _hooked = new Set();
function _hookDownloads(partition) {
  if (_hooked.has(partition)) return;
  _hooked.add(partition);
  session.fromPartition(partition).on("will-download", (_e, item) => {
    // A shared partition outlives runs, so the owner is looked up per download, not captured once.
    const owner = [..._runs].find(([, r]) => r.partition === partition);
    if (!owner) { item.cancel(); return; }
    const [runId, run] = owner;
    const dir = _downloadDir(runId);
    fs.mkdirSync(dir, { recursive: true });
    // A counter, not downloads.length: "Clear list" shrinks the array and ids/files must stay unique.
    const seq = (run.downloadSeq = (run.downloadSeq || 0) + 1);
    const file = path.join(dir, String(seq));
    item.setSavePath(file);
    const entry = {
      id: String(seq),
      url: item.getURL(),
      filename: item.getFilename(),
      host: _hostOf(item.getURL()),
      totalBytes: item.getTotalBytes(),
      receivedBytes: 0,
      // progressing | completed | cancelled | interrupted
      state: "progressing",
      path: file,
      item,
    };
    let lastEmit = 0;
    item.on("updated", (_ev, state) => {
      entry.receivedBytes = item.getReceivedBytes();
      entry.totalBytes = item.getTotalBytes();
      if (state === "interrupted") entry.state = "interrupted";
      const now = Date.now();
      if (now - lastEmit >= 250) { lastEmit = now; _emit(runId); }
    });
    entry.done = new Promise((resolve) => item.once("done", (_ev, state) => {
      entry.receivedBytes = item.getReceivedBytes();
      entry.totalBytes = item.getTotalBytes() || entry.totalBytes;
      entry.state = state === "completed" ? "completed" : state === "cancelled" ? "cancelled" : "interrupted";
      _emit(runId);
      resolve(state === "completed" ? file : null);
    }));
    run.downloads.push(entry);
    _emit(runId);
  });
}

const _describeDownload = (d) => ({
  id: d.id, filename: d.filename, host: d.host, state: d.state,
  receivedBytes: d.receivedBytes, totalBytes: d.totalBytes,
});

// A download failure carries its kind in the message ("cancelled: ...") because browser_control only
// relays e.message to the recorder/runtime, which turn it back into a reason.
const _fail = (code, msg) => Object.assign(new Error(`${code}: ${msg}`), { code });

async function saveDownload(runId, url, dest, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  let entry;
  for (;;) {
    const run = _runs.get(runId);
    entry = run && run.downloads.find((d) => !d.claimed && d.url === url);
    if (entry || Date.now() > deadline) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  if (!entry) throw _fail("timeout", `no download of ${url} in run ${runId}`);
  entry.claimed = true;
  // The slow-download budget covers the transfer too, not only the wait for it to start.
  const file = await Promise.race([
    entry.done,
    new Promise((r) => setTimeout(() => r("timeout"), Math.max(1000, timeoutMs))),
  ]);
  if (file === "timeout") {
    try { entry.item.cancel(); } catch (_) { /* already gone */ }
    throw _fail("timeout", `download of ${url} did not finish in ${timeoutMs}ms`);
  }
  if (!file) throw _fail(entry.state === "cancelled" ? "cancelled" : "interrupted", `download of ${url} did not complete`);
  const size = fs.statSync(file).size;
  if (size === 0 || (entry.totalBytes > 0 && size !== entry.totalBytes)) {
    throw _fail("empty", `download of ${url} is ${size} bytes${entry.totalBytes > 0 ? ` (expected ${entry.totalBytes})` : ""}`);
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(file, dest);
  fs.rmSync(file, { force: true });
  entry.path = dest;
  return { ok: true, size };
}

// A still of the active tab. The page is a native view that always draws above the renderer, so the
// Downloads popover can only float over it if the view steps aside and this stands in for it.
async function snapshot(runId, tabId) {
  const run = _runs.get(runId);
  const tab = run && run.tabs.find((t) => t.id === tabId);
  if (!tab || tab.view.webContents.isDestroyed()) return null;
  try { return (await tab.view.webContents.capturePage()).toDataURL(); } catch (_) { return null; }
}

// Chrome's download row actions. cancel/retry act on the live item; show/open on the saved file.
function downloadAction(runId, id, action) {
  const run = _runs.get(runId);
  if (run && action === "clear") { run.downloads = run.downloads.filter((x) => x.state === "progressing"); _emit(runId); return true; }
  const d = run && run.downloads.find((x) => x.id === id);
  if (!d) return false;
  const { shell } = require("electron");
  switch (action) {
    case "cancel": if (d.state === "progressing") d.item.cancel(); return true;
    case "retry": {
      const tab = run.tabs.find((t) => t.id === run.activeTabId) || run.tabs[0];
      if (tab && !tab.view.webContents.isDestroyed()) tab.view.webContents.downloadURL(d.url);
      return true;
    }
    case "show": if (d.state === "completed" && fs.existsSync(d.path)) shell.showItemInFolder(d.path); return true;
    case "open": if (d.state === "completed" && fs.existsSync(d.path)) void shell.openPath(d.path); return true;
    default: return false;
  }
}

// A new run starting replaces a finished test still sitting on screen. Record and sign-in runs are
// never dropped here — their backend closes them once it has saved what it needs.
async function _dropFinishedRuns(exceptRunId) {
  for (const [id, r] of _runs) if (id !== exceptRunId && r.finished && r.kind === "test") await closeRun(id);
}

function _viewport(v) {
  if (!v) return null;
  const width = Math.round(Number(v.width)), height = Math.round(Number(v.height));
  if (!(width > 0 && height > 0)) throw new Error("viewport needs a positive width and height");
  return v.fit === "width" ? { width, height, fit: "width" } : { width, height };
}

// Control-channel op "new_view" for a test run calls this first (and awaits it): drop the finished test
// still on screen and wipe whatever its cookies left in the shared partition (or a previous app
// session left on disk), so the new run starts signed out with a warm cache.
async function prepareTestRun() {
  await _dropFinishedRuns(null);
  if (_sharedInUse()) return;
  try { await session.fromPartition(SHARED_PARTITION).clearStorageData(); } catch (err) {
    console.error("browser_panel: failed to clear shared test partition", err);
  }
}

function newView(runId, { label, kind = "test", viewport } = {}) {
  let run = _runs.get(runId);
  if (!run) {
    if (!KINDS.has(kind)) throw new Error(`browser_panel: unknown run kind ${kind}`);
    run = {
      partition: kind === "test" && !_sharedInUse() ? SHARED_PARTITION : _partitionFor(runId), kind, viewport: _viewport(viewport), scale: 1, rect: null,
      tabs: [], activeTabId: null, downloads: [], finished: false,
    };
    run.height = run.viewport ? run.viewport.height : null;
    _runs.set(runId, run);
    _addShield(run);
    _hookDownloads(run.partition);
    void _dropFinishedRuns(runId);
  }
  const tabId = _createTab(run, runId, label);
  return { markerUrl: run.tabs.find((t) => t.id === tabId).markerUrl, tabId };
}

function newTab(runId, { label, focus } = {}) {
  const run = _runs.get(runId);
  if (!run) throw new Error(`browser_panel: no run ${runId} to add a tab to`);
  const tabId = _createTab(run, runId, label, focus === false);
  return { markerUrl: run.tabs.find((t) => t.id === tabId).markerUrl, tabId };
}

async function closeTab(runId, tabId) {
  const run = _runs.get(runId);
  if (!run) return;
  const idx = run.tabs.findIndex((t) => t.id === tabId);
  if (idx === -1) return;
  if (run.tabs.length === 1) return closeRun(runId);
  const [gone] = run.tabs.splice(idx, 1);
  try { if (_win) _win.contentView.removeChildView(gone.view); } catch (_) { /* best effort */ }
  try { gone.view.webContents.close(); } catch (_) { /* best effort */ }
  if (run.activeTabId !== tabId) { _emit(runId); return; }
  // As in a browser: the tab to the right takes its place (or the one that opened it, for a popup).
  const next = run.tabs.find((t) => t.id === gone.opener) || run.tabs[Math.min(idx, run.tabs.length - 1)];
  _activate(runId, next.id);
}

// What the address bar does with what was typed, as in Chrome: a URL loads as is, a bare host gets
// https:// (http:// for localhost and IP addresses), anything else is a search.
function _normalizeUrl(raw) {
  const v = String(raw || "").trim();
  if (!v) return "";
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(v) || /^(about|data|file):/i.test(v)) return v;
  if (!/\s/.test(v)) {
    if (/^(localhost|\d{1,3}(\.\d{1,3}){3})(:\d+)?([/?#]|$)/i.test(v)) return `http://${v}`;
    if (/^[^/\s?#]+\.[a-z]{2,}(:\d+)?([/?#]|$)/i.test(v)) return `https://${v}`;
  }
  return `https://www.google.com/search?q=${encodeURIComponent(v)}`;
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
    case "hard-reload": wc.reloadIgnoringCache(); break;
    case "stop": wc.stop(); break;
    case "load": {
      const target = _normalizeUrl(url);
      if (!target) return false;
      wc.loadURL(target).catch(() => {});
      wc.focus(); // after typing an address the keyboard goes back to the page, as in a browser
      break;
    }
    default: return false;
  }
  return true;
}

// Control-channel op "run_end": the runtime is done with this run. The view STAYS (the person checks
// the result in the final page); it is only marked finished so the panel can say so.
async function runEnd(runId) {
  const run = _runs.get(runId);
  if (!run) return;
  run.finished = true;
  _dropShield(run); // the person checks the final page: it is theirs again
  _emit(runId);
}

// Control-channel op "adopt_view": hand a test run that is parked mid-workflow to a recorder. Same views
// and same partition, so the signed-in session and the page state the replay built up carry over; the run
// becomes a record run (never dropped as a "finished test") at the recorder's fixed viewport. Awaits each
// tab's devtools target id, which test tabs never looked up: the recorder finds its pages by them.
async function adoptRun(runId, { kind = "record", viewport } = {}) {
  const run = _runs.get(runId);
  if (!run) throw new Error(`browser_panel: no run ${runId} to adopt`);
  if (!KINDS.has(kind) || kind === "test") throw new Error(`browser_panel: cannot adopt a run as ${kind}`);
  run.kind = kind;
  run.finished = false;
  _dropShield(run); // the recorder is driven by the person's own mouse
  if (viewport) {
    run.viewport = _viewport(viewport);
    run.height = run.viewport.height;
    for (const t of run.tabs) _emulate(run, t);
    // The renderer only reports a rect when it changes, and an adopt changes none: lay the pages out
    // for the new viewport from the rect it last reported.
    if (run.rect && run.activeTabId) _layout(run, run.activeTabId, run.rect);
  }
  await Promise.all(run.tabs.map((t) => {
    const dbg = _debugger(t);
    t.targetReady = dbg
      ? dbg.sendCommand("Target.getTargetInfo").then((r) => { t.targetId = r.targetInfo.targetId; }, () => {})
      : Promise.resolve();
    return t.targetReady;
  }));
  _emit(runId);
  const active = run.tabs.find((t) => t.id === run.activeTabId) || run.tabs[0];
  if (!active) throw new Error(`browser_panel: run ${runId} has no open tab to adopt`);
  return { tabId: active.id, targetId: active.targetId || null };
}

// The person closed the browser (or a newer run replaced it): destroy the views, wipe the partition.
async function closeRun(runId) {
  const run = _runs.get(runId);
  if (!run) return;
  _runs.delete(runId);
  _dropShield(run);
  // Chromium keeps a page's zoom per site in the partition, which test runs share: put it back to 1.
  for (const t of run.tabs) _setZoom(t, 1);
  for (const t of run.tabs) {
    try { if (_win) _win.contentView.removeChildView(t.view); } catch (_) { /* best effort */ }
    try { t.view.webContents.close(); } catch (_) { /* best effort */ }
  }
  // The shared partition is wiped only when no other run is still on it.
  try { if (run.partition !== SHARED_PARTITION || !_sharedInUse()) await session.fromPartition(run.partition).clearStorageData(); } catch (err) {
    console.error(`browser_panel: failed to clear storage for run ${runId}`, err);
  }
  try { fs.rmSync(_downloadDir(runId), { recursive: true, force: true }); } catch (_) { /* best effort */ }
  if (_onTabsChanged) _onTabsChanged(runId, [], { finished: true, closed: true });
}

// The renderer reports where the panel's rect is, in window-local pixels. Only the active tab's view
// is shown there; every other view is parked at 0x0, never destroyed, so switching tabs is instant.
// A fixed-viewport run is letterboxed: the view keeps the viewport's aspect ratio, shrunk (never
// enlarged) to fit the rect and centred in it, and the page is drawn at that scale.
// A viewport with fit "width" fills the rect instead: the page stays viewport.width CSS pixels wide,
// is drawn at rect.width / viewport.width, and its height is whatever then fills the rect (kept
// between MIN_H and MAX_H, so a short or tall pane gets a centred or top-anchored page).
function _fitBox(run, rect) {
  const { width: W } = run.viewport;
  let s = rect.width / W;
  let H = Math.round(rect.height / s);
  if (H < MIN_H) {
    H = MIN_H;
    s = rect.height / MIN_H;
    const width = Math.round(W * s);
    return { s, H, shown: { x: rect.x + Math.floor((rect.width - width) / 2), y: rect.y, width, height: rect.height } };
  }
  if (H > MAX_H) {
    H = MAX_H;
    return { s, H, shown: { x: rect.x, y: rect.y, width: rect.width, height: Math.round(MAX_H * s) } };
  }
  return { s, H, shown: rect };
}

// The renderer reports the pane's rect. A zero rect (a folded pane, a minimized window) is "nothing to show
// here", not "make the page zero wide": the views are hidden and every size stays as it was.
function setActiveBounds(runId, tabId, rect) {
  const run = _runs.get(runId);
  if (!run) return;
  if (!_validRect(rect)) { hideAll(); return; }
  _paneHidden = false;
  _lastRect = rect;
  run.rect = rect;
  for (const [id, r] of _runs) if (id !== runId) _hideRun(r);
  _layout(run, tabId, rect);
}

// Sizes every tab of the run for `rect` and shows `tabId` there. A tab that is not shown keeps the same
// bounds (hidden, never shrunk), so switching tabs, or a hidden pane, never changes what a page measures.
function _layout(run, tabId, rect) {
  let shown = rect;
  if (run.viewport) {
    let s, H = run.height;
    if (run.viewport.fit === "width") {
      ({ s, H, shown } = _fitBox(run, rect));
    } else {
      const { width: W, height: VH } = run.viewport;
      s = Math.min(rect.width / W, rect.height / VH, 1);
      const width = Math.round(W * s), height = Math.round(VH * s);
      shown = { x: rect.x + Math.floor((rect.width - width) / 2), y: rect.y + Math.floor((rect.height - height) / 2), width, height };
    }
    // Every tab of the run shares one scale and height (hidden ones too), so a tab never shows at a stale size.
    if (s > 0 && (Math.abs(s - run.scale) > 1e-4 || H !== run.height)) {
      run.scale = s;
      run.height = H;
      for (const t of run.tabs) _emulate(run, t);
    }
  }
  for (const t of run.tabs) {
    t.view.setBounds(shown);
    if (t.id === tabId && !_paneHidden) t.view.setVisible(true); else _hideView(t.view);
  }
  run.activeTabId = tabId;
  run.shownRect = _paneHidden ? null : shown;
  _layoutShield(run, run.tabs.find((t) => t.id === tabId), run.shownRect);
}

// Control-channel op "input": a hand-over is the one time a person is meant to act in the run's page, so the
// runtime asks for the shield to come off while it waits, and to go back on when the person is done.
function setInputOpen(runId, open) {
  const run = _runs.get(runId);
  if (!run) return { ok: false };
  run.handedOver = open === true;
  const tab = run.tabs.find((t) => t.id === run.activeTabId);
  _layoutShield(run, tab, run.shownRect);
  if (run.handedOver && tab && !tab.view.webContents.isDestroyed()) tab.view.webContents.focus();
  return { ok: true };
}

// Control-channel op "scale": how much a fixed-viewport run's pages are drawn at right now, and the
// page's CSS size. Screencast frames are the view's full size with the page drawn into their top-left
// at this scale, and nothing in a frame says so — the recorder's video needs it to cut the page back out.
function scaleOf(runId) {
  const run = _runs.get(runId);
  if (!run) throw new Error(`browser_panel: no run ${runId}`);
  return { scale: run.scale, width: run.viewport ? run.viewport.width : null, height: run.height };
}

// Control-channel op "targets": the devtools target ids of a record/sign-in run's tabs. Every tab
// is added synchronously when Chromium creates it, before any CDP client can see its page, so once
// the pending id lookups settle, a page whose target is not listed belongs to someone else.
async function targetsOf(runId) {
  const run = _runs.get(runId);
  if (!run) throw new Error(`browser_panel: no run ${runId}`);
  await Promise.all(run.tabs.map((t) => t.targetReady));
  return { targets: run.tabs.filter((t) => t.targetId).map((t) => ({ tabId: t.id, targetId: t.targetId, opener: t.opener })) };
}

// A native view floats above the page, so nothing in the app can draw over it. Hiding parks every view
// (a dialog is open, or the person folded the panel); showing re-reports bounds via setActiveBounds.
function _hideRun(run) {
  for (const t of run.tabs) _hideView(t.view);
  if (run.shield) run.shield.setBounds(_HIDDEN);
  run.shownRect = null;
}

function hideAll() {
  _paneHidden = true;
  for (const run of _runs.values()) _hideRun(run);
}

function selectTab(runId, tabId) {
  return _activate(runId, tabId);
}

module.exports = { setTourHint, _tourAllowed, init, prepareTestRun, newView, newTab, adoptRun, personTab, closeTab, closeRun, navigate, runEnd, setActiveBounds, setInputOpen, hideAll, selectTab, saveDownload, downloadAction, snapshot, scaleOf, targetsOf, ensureGeometry, capture };
