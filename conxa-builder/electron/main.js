"use strict";
/**
 * Build Studio Electron main process.
 *
 * Spawns the PyInstaller backend (or `python backend.py` in dev) and bridges
 * `ipcMain.handle('python:cmd', ...)` to its stdin/stdout JSON-RPC. Streaming
 * `{type:"event"}` lines are forwarded to the focused renderer. The backend is
 * restarted up to 3 times on unexpected exit before surfacing a fatal dialog.
 */

const { app, BrowserWindow, ipcMain, dialog, shell, Menu, protocol, net } = require("electron");
const { autoUpdater } = require("electron-updater");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const readline = require("readline");
const { pathToFileURL } = require("url");
const { Bridge } = require("./bridge");
const conxaEnv = require("./env");
const nodeNet = require("net");
const browserPanel = require("./browser_panel");
const browserControl = require("./browser_control");

// Local skill assets (screenshots, frames) are streamed from disk through this
// scheme instead of being base64-inlined into every RPC response — see FIX.md.
// Must be registered before app is ready.
protocol.registerSchemesAsPrivileged([
  { scheme: "conxa-asset", privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true } },
]);

// electron-updater config — manual-only, no auto-download or auto-install.
autoUpdater.autoDownload = false;
autoUpdater.autoInstallOnAppQuit = false;
autoUpdater.logger = null; // silence default console noise in production

const IS_DEV = !app.isPackaged;
const MAX_BACKEND_RESTARTS = 3;

// Preload runs in a separate context where process.defaultApp is not reliable
// for our node-launched dev wrapper. Pass the main-process packaging state
// explicitly so the renderer can skip packaged-only bootstrap in dev.
process.env.CONXA_ELECTRON_IS_PACKAGED = app.isPackaged ? "1" : "0";

// BUILD-42: tests run in a browser panel inside this window instead of a separate Chromium. The runtime
// attaches to this process over CDP (Playwright connectOverCDP), so Electron has to expose a debugging
// port — on loopback only, and a random free one. --remote-debugging-port needs a concrete number
// before app ready; the control channel started in whenReady is what hands the runtime views. The port
// reaches every webContents in this app, not just the panel's, which is the price of the seam (Conxa
// Execute pays the same one); recording and sign-in do not use it.
function _freePort() {
  return new Promise((resolve, reject) => {
    const srv = nodeNet.createServer();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}
let _CDP_PORT = null;
const _cdpPortReady = _freePort()
  .then((port) => {
    _CDP_PORT = port;
    app.commandLine.appendSwitch("remote-debugging-port", String(port));
    app.commandLine.appendSwitch("remote-debugging-address", "127.0.0.1");
  })
  .catch(() => { _CDP_PORT = null; }); // no port → no panel; tests fall back to their own window

// Enforce single instance so second-instance fires (required for Windows deep-link handling).
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
  process.exit(0);
}

// Windows: app already running — focus it and forward the deep-link URL.
app.on("second-instance", (_event, argv) => {
  const url = argv.find((a) => a.startsWith("conxa-studio://"));
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
    if (url) mainWindow.webContents.send("deep-link", url);
  }
});

// macOS: OS fires this when the app is already running and a conxa-studio:// link is opened.
app.on("open-url", (event, url) => {
  event.preventDefault();
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("deep-link", url);
  }
});
const APP_ICON_PATH = path.join(__dirname, "build", "icon.png");

let mainWindow = null;
let backend = null;
let backendRestarts = 0;
let bridge = null;

// The engine's state as the renderer sees it: "running" | "restarting" | "stopped". "stopped" means the
// automatic restarts ran out; the person restarts it from the notice (engine:restart). Workflows are
// files on disk, so nothing is lost either way.
let engineState = "running";
const _engineTail = []; // last backend stderr lines, for "Copy diagnostics"
function setEngineState(next) {
  engineState = next;
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("engine:status", { state: next });
}

// Forward electron-updater events to the renderer's update:status channel.
// Registered once on first update:check so mainWindow is guaranteed to exist.
let updateListenersRegistered = false;
function sendUpdateStatus(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("update:status", payload);
  }
}
function ensureUpdateListeners() {
  if (updateListenersRegistered) return;
  updateListenersRegistered = true;
  autoUpdater.on("download-progress", (info) => {
    sendUpdateStatus({
      phase: "download-progress",
      percent: info.percent,
      bytesPerSecond: info.bytesPerSecond,
      transferred: info.transferred,
      total: info.total,
    });
  });
  autoUpdater.on("update-downloaded", () => sendUpdateStatus({ phase: "downloaded" }));
  autoUpdater.on("error", (err) => sendUpdateStatus({ phase: "error", message: err.message }));
}
// Minimal semver greater-than without an extra dependency.
function semverGt(a, b) {
  const pa = a.replace(/^v/, "").split(".").map(Number);
  const pb = b.replace(/^v/, "").split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) > (pb[i] || 0)) return true;
    if ((pa[i] || 0) < (pb[i] || 0)) return false;
  }
  return false;
}

// Strip "studio-v" or "v" prefix from manifest version strings → bare semver.
function stripVersion(v) {
  return v.replace(/^studio-v/i, "").replace(/^v/, "");
}

// ─── Backend lifecycle ────────────────────────────────────────────────────────

function backendCommand() {
  if (IS_DEV) {
    const defaultPy = process.platform === "win32" ? "python" : "python3";
    const venvPy = process.env.VIRTUAL_ENV
      ? path.join(process.env.VIRTUAL_ENV, process.platform === "win32" ? "Scripts\\python.exe" : "bin/python")
      : defaultPy;
    const py = process.env.CONXA_PYTHON || venvPy;
    const script = path.join(__dirname, "..", "python", "backend.py");
    return { cmd: py, args: [script] };
  }
  // Packaged: PyInstaller --onedir backend placed via electron-builder extraFiles.
  const exe = process.platform === "win32" ? "backend.exe" : "backend";
  return { cmd: path.join(process.resourcesPath, "backend", exe), args: [] };
}

function startBackend() {
  const { cmd, args } = backendCommand();
  backend = spawn(cmd, args, {
    stdio: ["pipe", "pipe", "pipe"], // all pipes; windowsHide suppresses the console window
    windowsHide: true,
    env: {
      ...process.env,
      SKILL_ALLOW_NO_PROVIDERS: "1",
      // conxa_core's Settings cross-checks environment=prod against SKILL_AUTH_REQUIRED
      // (see _require_env_auth_consistency) — a safeguard meant for the cloud backend's
      // Clerk-JWT gate. Studio's local Python process never sets SKILL_AUTH_REQUIRED (it
      // authenticates via PKCE in auth_service.py, not that flag), so with the prod
      // default from env.js below every packaged install would fail this check on
      // startup. Bypass it here — it doesn't apply to Studio.
      SKILL_ALLOW_ENV_MISMATCH: "1",
      // Dev/prod cloud + auth + Studio-state config, resolved by env.js. Defaults
      // to prod (the shipped Studio publishes to the production cloud); an explicit
      // CONXA_ENV=dev (from the launcher) selects the isolated dev lane. Any single
      // value can still be overridden by setting its env var in the shell.
      ...conxaEnv.backendEnv(),
      // CONXA_CLERK_CLIENT_SECRET is optional: auth_service uses PKCE (public client)
      // so the secret is not required for the token exchange. If Clerk is configured
      // as a confidential client, set this env var in the shell before `npm run dev`,
      // or set it as a GitHub Actions secret and pass it to the build step.
      // Never commit a default value here.
      ...(process.env.CONXA_CLERK_CLIENT_SECRET
        ? { CONXA_CLERK_CLIENT_SECRET: process.env.CONXA_CLERK_CLIENT_SECRET }
        : {}),
      // In dev, add source paths so Python can import conxa_core and conxa_compile
      // without requiring a full pip install. In packaged mode these directories
      // don't exist on disk and the frozen bundle has everything it needs — omit them.
      PYTHONPATH: (IS_DEV ? [
        path.join(__dirname, "..", "..", "packages", "conxa-core"),
        path.join(__dirname, "..", "python"),
        process.env.PYTHONPATH || "",
      ] : [process.env.PYTHONPATH || ""]).filter(Boolean).join(path.delimiter),
    },
  });

  bridge = new Bridge(
    (line) => backend.stdin.write(line),
    (event) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("python:event", event);
      }
    }
  );

  const rl = readline.createInterface({ input: backend.stdout });
  rl.on("line", (line) => bridge.handleLine(line));

  const rlErr = readline.createInterface({ input: backend.stderr });
  rlErr.on("line", (line) => {
    console.error("[backend]", line);
    _engineTail.push(line);
    if (_engineTail.length > 60) _engineTail.shift();
  });

  // A start that stays up for a minute is healthy: forget earlier crashes so three unrelated ones
  // over a week don't add up to "could not be restarted".
  const healthy = setTimeout(() => { backendRestarts = 0; }, 60000);
  if (healthy.unref) healthy.unref();
  setEngineState("running");

  backend.on("exit", (code) => {
    clearTimeout(healthy);
    if (bridge) bridge.rejectAll(`backend exited (code ${code})`);

    if (backendRestarts < MAX_BACKEND_RESTARTS && !app.isQuitting) {
      backendRestarts += 1;
      setEngineState("restarting");
      startBackend();
    } else if (!app.isQuitting) {
      setEngineState("stopped");
      if (!mainWindow || mainWindow.isDestroyed()) {
        dialog.showErrorBox(
          "Conxa Build Studio",
          "The backend stopped unexpectedly and could not be restarted. " +
            "Please restart the app. Logs are in the app data directory."
        );
      }
    }
  });
}

function callBackend(type, payload) {
  if (!backend || backend.killed || !bridge) {
    return Promise.reject(new Error("backend_not_running"));
  }
  return bridge.call(type, payload);
}

// ─── IPC surface for the renderer ──────────────────────────────────────────────

ipcMain.handle("python:cmd", async (_e, { type, payload }) => {
  try {
    const result = await callBackend(type, payload);
    return { ok: true, result };
  } catch (err) {
    return { ok: false, code: err.code || "error", message: err.message, trace: err.trace };
  }
});

ipcMain.handle("open-external", (_e, url) => shell.openExternal(url));

ipcMain.handle("engine:status", () => ({ state: engineState }));
ipcMain.handle("engine:diagnostics", () => `Engine state: ${engineState}\n${_engineTail.join("\n")}`);
ipcMain.handle("engine:restart", () => {
  if (engineState !== "stopped") return false;
  backendRestarts = 0;
  setEngineState("restarting");
  startBackend();
  return true;
});

// ─── Embedded test browser (BUILD-42) ──────────────────────────────────────────
// Bounds are the panel region's window-local rect from the renderer; a native view floats above the DOM,
// so the renderer also hides it whenever a dialog is open (panel:hide).
const _num = (v) => Math.max(0, Math.round(Number(v) || 0));
ipcMain.handle("panel:bounds", (_e, { runId, tabId, rect }) => {
  if (!runId || !tabId || !rect) return false;
  browserPanel.setActiveBounds(runId, tabId, { x: _num(rect.x), y: _num(rect.y), width: _num(rect.width), height: _num(rect.height) });
  return true;
});
ipcMain.handle("panel:hide", () => { browserPanel.hideAll(); return true; });
ipcMain.handle("panel:select-tab", (_e, { runId, tabId }) => browserPanel.selectTab(runId, tabId));
ipcMain.handle("panel:close-tab", async (_e, { runId, tabId }) => { await browserPanel.closeTab(runId, tabId); return true; });
ipcMain.handle("panel:close-run", async (_e, { runId }) => { await browserPanel.closeRun(runId); return true; });
ipcMain.handle("panel:navigate", (_e, { runId, tabId, action, url }) => browserPanel.navigate(runId, tabId, { action, url }));
ipcMain.handle("panel:available", () => Boolean(process.env.CONXA_HOST_BROWSER_CDP && process.env.CONXA_HOST_CONTROL_URL));

ipcMain.handle("dialog:save-installer", async (_e, srcPath) => {
  const { canceled, filePath } = await dialog.showSaveDialog({
    defaultPath: path.basename(srcPath),
    filters: [{ name: "Installer", extensions: ["exe"] }],
  });
  if (canceled || !filePath) return { ok: false };
  fs.copyFileSync(srcPath, filePath);
  return { ok: true, filePath };
});

ipcMain.handle("dialog:pick-file", async (event, opts) => {
  const { filters, defaultPath, multiple } = opts ?? {};
  // Surface the Studio window above whatever else has focus (e.g. the recording browser
  // window) before showing the dialog — see the file-picker-request flow in
  // RecordWorkflowDialog.tsx.
  const win = windowFromEvent(event);
  if (win) {
    // show() alone leaves a minimized window minimized on Windows — restore first.
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    // The recording browser is a separate top-level window from another process —
    // focus() alone doesn't force us above it in z-order on Windows. Pin on top for
    // just the dialog's lifetime so the picker isn't stuck behind the recorder.
    win.setAlwaysOnTop(true);
  }
  let resolvedDefaultPath = defaultPath;
  if (resolvedDefaultPath) {
    try {
      const stat = fs.statSync(resolvedDefaultPath);
      if (!stat.isDirectory() && !stat.isFile()) {
        resolvedDefaultPath = undefined;
      }
    } catch {
      resolvedDefaultPath = undefined;
    }
  }
  const dialogOpts = {
    properties: multiple ? ["openFile", "multiSelections"] : ["openFile"],
    ...(Array.isArray(filters) && filters.length ? { filters } : {}),
    ...(resolvedDefaultPath ? { defaultPath: resolvedDefaultPath } : {}),
  };
  try {
    // Attach the dialog to the Studio window (modal) so it opens centered on top of it
    // instead of floating behind other windows.
    const { canceled, filePaths } = await dialog.showOpenDialog(win, dialogOpts);
    return canceled ? null : filePaths;
  } finally {
    win?.setAlwaysOnTop(false);
  }
});

function windowFromEvent(event) {
  return BrowserWindow.fromWebContents(event.sender);
}

// Surfaces the Studio window above the recording browser window (a separate top-level window
// from another process, so plain focus() doesn't reliably win the z-order on Windows) when a
// js_dialog_request modal opens — see RecordWorkflowDialog.tsx. `raise:false` on modal close
// lets the recording browser come back to the front instead of pinning the Studio forever.
ipcMain.handle("window:raise", (event, raise) => {
  const win = windowFromEvent(event);
  if (!win) return;
  if (raise) {
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    win.setAlwaysOnTop(true);
  } else {
    win.setAlwaysOnTop(false);
  }
});

ipcMain.handle("window:minimize", (event) => {
  windowFromEvent(event)?.minimize();
});

ipcMain.handle("window:toggle-maximize", (event) => {
  const win = windowFromEvent(event);
  if (!win) return false;
  if (win.isMaximized()) {
    win.unmaximize();
  } else {
    win.maximize();
  }
  return win.isMaximized();
});

ipcMain.handle("window:close", (event) => {
  windowFromEvent(event)?.close();
});

ipcMain.handle("window:is-maximized", (event) => Boolean(windowFromEvent(event)?.isMaximized()));

ipcMain.handle("app:version", () => app.getVersion());

// Checks for a newer version by fetching the Cloud studio-manifest, then using
// electron-updater's generic provider to read latest.yml from the same release directory.
// Fail-open: any error returns { available: false, error } so startup is never bricked.
// In dev, checkForUpdates() returns null (not packaged) — short-circuit early to be explicit.
ipcMain.handle("update:check", async () => {
  const currentVersion = app.getVersion();
  if (process.env.CONXA_FORCE_UPDATE_SCREEN === "1") {
    return { available: true, currentVersion, latestVersion: "99.0.0" };
  }
  if (IS_DEV) {
    return { available: false, currentVersion };
  }
  const CLOUD_API = conxaEnv.resolve().cloudApi;
  try {
    const res = await fetch(`${CLOUD_API}/api/v1/updates/studio-manifest`, {
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) {
      return { available: false, currentVersion, error: `Manifest fetch failed: HTTP ${res.status}` };
    }
    const manifest = await res.json();
    const winUrl = manifest.win_url || "";
    if (!winUrl) {
      return { available: false, currentVersion, error: "Manifest has no win_url" };
    }
    // Point electron-updater at the cloud-served latest.yml so the path: field is
    // always in sync with CONXA_STUDIO_WIN_URL. The files[].url in that response
    // is the full GitHub URL, so the actual .exe downloads from GitHub directly.
    ensureUpdateListeners();
    autoUpdater.setFeedURL({ provider: "generic", url: `${CLOUD_API}/api/v1/updates/studio/`, useMultipleRangeRequest: false });
    const r = await autoUpdater.checkForUpdates();
    if (r === null) {
      // Not packaged — should not reach here due to IS_DEV guard above; defensive.
      return { available: false, currentVersion };
    }
    const latestVersion = stripVersion(r.updateInfo.version || "");
    const available = semverGt(latestVersion, currentVersion);
    return { available, currentVersion, latestVersion };
  } catch (err) {
    return { available: false, currentVersion, error: err.message };
  }
});

// Triggers the differential (blockmap) download via electron-updater. Progress and
// completion events flow through the listeners registered in ensureUpdateListeners().
// electron-updater verifies integrity automatically against the sha512 in latest.yml.
ipcMain.handle("update:start", async () => {
  try {
    await autoUpdater.downloadUpdate();
  } catch (err) {
    sendUpdateStatus({ phase: "error", message: err.message });
  }
});

// Launches the downloaded installer silently then relaunches the app.
// NsisUpdater.quitAndInstall(isSilent=true, isForceRunAfter=true) builds the proven
// --updated /S --force-run NSIS args, equivalent to what was hand-coded before.
ipcMain.handle("update:install", () => {
  autoUpdater.quitAndInstall(true /* isSilent */, true /* isForceRunAfter */);
});

// ─── Window ─────────────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#1a1a1a",
    icon: APP_ICON_PATH,
    title: "Conxa Build Studio",
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (IS_DEV && process.env.CONXA_RENDERER_URL) {
    mainWindow.loadURL(process.env.CONXA_RENDERER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, "renderer", "dist", "index.html"));
  }

  // The application menu is suppressed (Menu.setApplicationMenu(null)), which also
  // strips the default F12 / Ctrl+Shift+I DevTools accelerators. Re-register them in dev.
  if (IS_DEV) {
    mainWindow.webContents.on("before-input-event", (_event, input) => {
      const isToggle =
        input.key === "F12" ||
        (input.control && input.shift && input.key.toLowerCase() === "i");
      if (isToggle) mainWindow.webContents.toggleDevTools();
    });
  }

  const sendMaximizeState = () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.webContents.send("window:maximized", mainWindow.isMaximized());
  };
  mainWindow.on("maximize", sendMaximizeState);
  browserPanel.init(mainWindow, {
    onTabsChanged: (runId, tabs, meta) => {
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("panel:tabs", { runId, tabs, ...meta });
    },
  });
  mainWindow.on("unmaximize", sendMaximizeState);
}

// ─── App lifecycle ──────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);

  if (process.platform === "win32") {
    app.setAppUserModelId("ai.conxa.build-studio");
  }
  // Custom scheme for the OAuth callback (registered at install on Windows).
  if (!app.isDefaultProtocolClient("conxa-studio")) {
    app.setAsDefaultProtocolClient("conxa-studio");
  }

  // Streams conxa-asset://local/<relative-path> from <studioHome>/data on demand —
  // the renderer counterpart to assets.py's asset_url(). Rejects traversal outside
  // the data dir the same way resolve_skill_asset() does on the Python side.
  const assetsRoot = path.resolve(path.join(conxaEnv.resolve().studioHome, "data"));
  protocol.handle("conxa-asset", (request) => {
    const url = new URL(request.url);
    const relPath = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    if (!relPath || relPath.includes("..")) {
      return new Response("Forbidden", { status: 403 });
    }
    const target = path.resolve(assetsRoot, relPath);
    if (target !== assetsRoot && !target.startsWith(assetsRoot + path.sep)) {
      return new Response("Forbidden", { status: 403 });
    }
    return net.fetch(pathToFileURL(target).toString());
  });

  // The runtime is spawned by the Python backend, which inherits this process's environment, so these two
  // are all it needs to find the panel (see host_browser.js). Set before the backend starts. If anything
  // below fails the variables stay unset and the runtime launches its own Chromium exactly as before.
  await _cdpPortReady;
  if (_CDP_PORT) {
    try {
      const { url } = await browserControl.start();
      process.env.CONXA_HOST_BROWSER_CDP = `http://127.0.0.1:${_CDP_PORT}`;
      process.env.CONXA_HOST_CONTROL_URL = url;
    } catch (err) {
      console.error("browser panel control channel failed to start; tests will open their own window", err);
    }
  }

  startBackend();
  createWindow();

  // Windows cold-launch: deep link URL is passed as a CLI arg when the app starts fresh.
  const coldUrl = process.argv.find((a) => a.startsWith("conxa-studio://"));
  if (coldUrl) {
    mainWindow.webContents.once("did-finish-load", () => {
      mainWindow.webContents.send("deep-link", coldUrl);
    });
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", (event) => {
  // Re-entrant: the app.quit() calls below re-emit before-quit. `backend.killed` is
  // only set by an explicit .kill(), so after a graceful exit it stays false forever —
  // without this latch the handler would preventDefault() on every pass and the app
  // could never quit (zombie process holding the single-instance lock).
  if (app.isQuitting) return;
  app.isQuitting = true;
  if (backend && !backend.killed) {
    // Give the backend a chance to run its own shutdown (joins any open
    // recording's Playwright thread) instead of force-killing it — closing
    // stdin makes its stdin-read loop hit EOF and exit on its own.
    event.preventDefault();
    const forceKill = setTimeout(() => {
      if (backend && !backend.killed) backend.kill();
      app.quit();
    }, 3000);
    backend.once("exit", () => {
      clearTimeout(forceKill);
      app.quit();
    });
    backend.stdin.end();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

module.exports = { callBackend };
