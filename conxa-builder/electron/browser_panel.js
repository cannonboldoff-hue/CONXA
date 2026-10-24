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

// runId -> { partition, kind, viewport, scale, rect, tabs: [{ id, view, markerUrl, label, opener }],
//            activeTabId, downloads: [], finished }
const _runs = new Map();
const KINDS = new Set(["test", "record", "signin"]);
const _HIDDEN = { x: 0, y: 0, width: 0, height: 0 };

let _win = null;
let _onTabsChanged = null; // (runId, tabs, meta) => void — wired by main.js to push to the renderer
let _onShortcut = null; // (runId, action) => void — keys only the renderer can act on (focus the address bar)

function init(win, { onTabsChanged, onShortcut } = {}) {
  _win = win;
  _onTabsChanged = onTabsChanged || null;
  _onShortcut = onShortcut || null;
}

const _partitionFor = (runId) => `conxa-run-${runId}`;
const _markerUrl = (runId, tabId) => `about:blank?conxa_run=${encodeURIComponent(runId)}&conxa_tab=${encodeURIComponent(tabId)}`;
// The marker is an internal handle, never an address — it must not reach the URL field or a label.
const _isMarker = (url) => !url || url.startsWith("about:blank");
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

function _emulate(run, tab) {
  if (!run.viewport) return;
  const dbg = _debugger(tab);
  if (!dbg) return;
  dbg.sendCommand("Emulation.setDeviceMetricsOverride", {
    width: run.viewport.width, height: run.viewport.height, deviceScaleFactor: 1, mobile: false, scale: run.scale,
  }).catch((err) => console.error("browser_panel: setDeviceMetricsOverride failed", err));
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
      if (run.rect) setActiveBounds(runId, run.activeTabId, run.rect);
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
  if (tab.markerUrl) view.webContents.loadURL(tab.markerUrl).catch(() => {});
  // Both after loadURL: attaching the debugger to a never-navigated view crashes Electron.
  _emulate(run, tab);
  // The recorder sees every page in Electron (Studio's own window, other runs) through one CDP
  // context; a tab's devtools target id is how it tells which pages are its run's (`targets`).
  if (run.kind !== "test") {
    const dbg = _debugger(tab);
    tab.targetReady = dbg
      ? dbg.sendCommand("Target.getTargetInfo").then((r) => { tab.targetId = r.targetInfo.targetId; }, () => {})
      : Promise.resolve();
  }
  run.tabs.push(tab);
  if (_win) _win.contentView.addChildView(view);
  if (!background || run.activeTabId === null) run.activeTabId = tabId;
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
  if (run.rect) setActiveBounds(runId, tabId, run.rect);
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
  if (run.rect) setActiveBounds(runId, tabId, run.rect);
  else run.activeTabId = tabId;
  if (!t.view.webContents.isDestroyed()) t.view.webContents.focus();
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
    if (back && run.rect) setActiveBounds(runId, back.id, run.rect);
  }
  _emit(runId);
}

// Downloads in a run's partition never reach Playwright's download.saveAs(); Electron's own download
// manager would also pop a native Save As dialog that blocks the run on a person. So every download is
// saved silently here and the runtime claims the finished file by URL (save_download).
const _downloadDir = (runId) => path.join(os.tmpdir(), "conxa-studio-downloads", runId.replace(/[^\w.-]/g, "_"));
const _hooked = new Set();
function _hookDownloads(runId, partition) {
  if (_hooked.has(partition)) return;
  _hooked.add(partition);
  session.fromPartition(partition).on("will-download", (_e, item) => {
    const run = _runs.get(runId);
    if (!run) { item.cancel(); return; }
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
  return { width, height };
}

function newView(runId, { label, kind = "test", viewport } = {}) {
  let run = _runs.get(runId);
  if (!run) {
    if (!KINDS.has(kind)) throw new Error(`browser_panel: unknown run kind ${kind}`);
    run = {
      partition: _partitionFor(runId), kind, viewport: _viewport(viewport), scale: 1, rect: null,
      tabs: [], activeTabId: null, downloads: [], finished: false,
    };
    _runs.set(runId, run);
    _hookDownloads(runId, run.partition);
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
  if (viewport) {
    run.viewport = _viewport(viewport);
    for (const t of run.tabs) _emulate(run, t);
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
  for (const t of run.tabs) {
    try { if (_win) _win.contentView.removeChildView(t.view); } catch (_) { /* best effort */ }
    try { t.view.webContents.close(); } catch (_) { /* best effort */ }
  }
  try { await session.fromPartition(run.partition).clearStorageData(); } catch (err) {
    console.error(`browser_panel: failed to clear storage for run ${runId}`, err);
  }
  try { fs.rmSync(_downloadDir(runId), { recursive: true, force: true }); } catch (_) { /* best effort */ }
  if (_onTabsChanged) _onTabsChanged(runId, [], { finished: true, closed: true });
}

// The renderer reports where the panel's rect is, in window-local pixels. Only the active tab's view
// is shown there; every other view is parked at 0x0, never destroyed, so switching tabs is instant.
// A fixed-viewport run is letterboxed: the view keeps the viewport's aspect ratio, shrunk (never
// enlarged) to fit the rect and centred in it, and the page is drawn at that scale.
function setActiveBounds(runId, tabId, rect) {
  const run = _runs.get(runId);
  if (!run) return;
  run.rect = rect;
  for (const [id, r] of _runs) if (id !== runId) for (const t of r.tabs) t.view.setBounds(_HIDDEN);
  let shown = rect;
  if (run.viewport) {
    const { width: W, height: H } = run.viewport;
    const s = Math.min(rect.width / W, rect.height / H, 1);
    const width = Math.round(W * s), height = Math.round(H * s);
    shown = { x: rect.x + Math.floor((rect.width - width) / 2), y: rect.y + Math.floor((rect.height - height) / 2), width, height };
    // Every tab of the run shares one scale (hidden ones too), so a tab never shows at a stale size.
    if (s > 0 && Math.abs(s - run.scale) > 1e-4) {
      run.scale = s;
      for (const t of run.tabs) _emulate(run, t);
    }
  }
  for (const t of run.tabs) t.view.setBounds(t.id === tabId ? shown : _HIDDEN);
  run.activeTabId = tabId;
}

// Control-channel op "scale": how much a fixed-viewport run's pages are shrunk right now. Screencast
// frames are the view's full size with the page drawn into their top-left at this scale, and nothing
// in a frame says so — the recorder's video needs it to cut the page back out.
function scaleOf(runId) {
  const run = _runs.get(runId);
  if (!run) throw new Error(`browser_panel: no run ${runId}`);
  return { scale: run.scale };
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
function hideAll() {
  for (const run of _runs.values()) for (const t of run.tabs) t.view.setBounds(_HIDDEN);
}

function selectTab(runId, tabId) {
  return _activate(runId, tabId);
}

module.exports = { init, newView, newTab, adoptRun, personTab, closeTab, closeRun, navigate, runEnd, setActiveBounds, hideAll, selectTab, saveDownload, downloadAction, snapshot, scaleOf, targetsOf };
