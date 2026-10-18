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
 *  - Only tests use it. Recording and sign-in keep their own Chromium: the recorder needs a context
 *    created with record_video_dir (compile reads event screenshots from that video) and
 *    storageState(), neither of which exists on an attached Electron context — see TRD §6.7.
 *  - A run that ENDS does not close its view. The mockup keeps the final page on screen so the person
 *    can check the result themselves ("Stays on the final page so you can check the result");
 *    run_end marks it finished and the person (or the next run) closes it.
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

// runId -> { partition, tabs: [{ id, view, markerUrl, label }], activeTabId, downloads: [], finished }
const _runs = new Map();
const _HIDDEN = { x: 0, y: 0, width: 0, height: 0 };

let _win = null;
let _onTabsChanged = null; // (runId, tabs, meta) => void — wired by main.js to push to the renderer

function init(win, { onTabsChanged } = {}) {
  _win = win;
  _onTabsChanged = onTabsChanged || null;
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
  };
}

function _emit(runId, meta) {
  const run = _runs.get(runId);
  if (!run || !_onTabsChanged) return;
  _onTabsChanged(runId, run.tabs.map((t) => _describeTab(run, t)), { finished: run.finished, ...(meta || {}) });
}

function _createTab(run, runId, label, background = false) {
  const tabId = crypto.randomBytes(4).toString("hex");
  const view = new WebContentsView({ webPreferences: { partition: run.partition, sandbox: true } });
  // A site-opened popup or new tab becomes a tab in the panel's footer, never a stray window.
  view.webContents.setWindowOpenHandler(({ url }) => {
    const newId = _createTab(run, runId);
    const nv = run.tabs.find((t) => t.id === newId).view;
    if (url && url !== "about:blank") nv.webContents.loadURL(url).catch(() => {});
    return { action: "deny" };
  });
  for (const ev of ["page-title-updated", "did-navigate", "did-navigate-in-page", "did-start-loading", "did-stop-loading"]) {
    view.webContents.on(ev, () => _emit(runId));
  }
  view.setBounds(_HIDDEN); // parked until the renderer reports a rect
  view.webContents.loadURL(_markerUrl(runId, tabId)).catch(() => {});
  run.tabs.push({ id: tabId, view, markerUrl: _markerUrl(runId, tabId), label: label || null });
  if (_win) _win.contentView.addChildView(view);
  if (!background || run.activeTabId === null) run.activeTabId = tabId;
  _emit(runId);
  return tabId;
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
    const file = path.join(dir, String(run.downloads.length + 1));
    item.setSavePath(file);
    run.downloads.push({
      url: item.getURL(),
      done: new Promise((resolve) => item.once("done", (_ev, state) => resolve(state === "completed" ? file : null))),
    });
  });
}

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

// A new test starting replaces a finished one still sitting on screen.
async function _dropFinishedRuns(exceptRunId) {
  for (const [id, r] of _runs) if (id !== exceptRunId && r.finished) await closeRun(id);
}

function newView(runId, { label } = {}) {
  let run = _runs.get(runId);
  if (!run) {
    run = { partition: _partitionFor(runId), tabs: [], activeTabId: null, downloads: [], finished: false };
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
  if (run.activeTabId === tabId) run.activeTabId = run.tabs[Math.min(idx, run.tabs.length - 1)].id;
  _emit(runId);
}

function _normalizeUrl(raw) {
  const v = String(raw || "").trim();
  if (!v) return "";
  return /^[a-z][a-z0-9+.-]*:/i.test(v) ? v : `https://${v}`;
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

// Control-channel op "run_end": the runtime is done with this run. The view STAYS (the person checks
// the result in the final page); it is only marked finished so the panel can say so.
async function runEnd(runId) {
  const run = _runs.get(runId);
  if (!run) return;
  run.finished = true;
  _emit(runId);
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
function setActiveBounds(runId, tabId, rect) {
  const run = _runs.get(runId);
  if (!run) return;
  for (const [id, r] of _runs) if (id !== runId) for (const t of r.tabs) t.view.setBounds(_HIDDEN);
  for (const t of run.tabs) t.view.setBounds(t.id === tabId ? rect : _HIDDEN);
  run.activeTabId = tabId;
}

// A native view floats above the page, so nothing in the app can draw over it. Hiding parks every view
// (a dialog is open, or the person folded the panel); showing re-reports bounds via setActiveBounds.
function hideAll() {
  for (const run of _runs.values()) for (const t of run.tabs) t.view.setBounds(_HIDDEN);
}

function selectTab(runId, tabId) {
  const run = _runs.get(runId);
  if (!run || !run.tabs.some((t) => t.id === tabId)) return false;
  run.activeTabId = tabId;
  _emit(runId);
  return true;
}

module.exports = { init, newView, newTab, closeTab, closeRun, navigate, runEnd, setActiveBounds, hideAll, selectTab, saveDownload };
