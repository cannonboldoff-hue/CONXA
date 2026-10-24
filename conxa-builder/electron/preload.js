"use strict";
/**
 * Preload bridge. Exposes a minimal, typed surface to the renderer over the
 * context-isolated boundary — no direct Node or ipcRenderer access leaks.
 */

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("conxa", {
  /** True when running from the packaged installer; false in `npm run dev`. */
  isPackaged: process.env.CONXA_ELECTRON_IS_PACKAGED === "1",

  /** Invoke a backend command. Resolves to { ok, result } | { ok:false, code, message }. */
  cmd: (type, payload) => ipcRenderer.invoke("python:cmd", { type, payload }),

  /** Subscribe to streaming backend events. Returns an unsubscribe fn. */
  onEvent: (handler) => {
    const listener = (_e, msg) => handler(msg);
    ipcRenderer.on("python:event", listener);
    return () => ipcRenderer.removeListener("python:event", listener);
  },

  openExternal: (url) => ipcRenderer.invoke("open-external", url),

  pickFile: (opts) => ipcRenderer.invoke("dialog:pick-file", opts),

  /** Surface (or un-pin) the Studio window above the recording browser window. */
  raiseWindow: (raise) => ipcRenderer.invoke("window:raise", raise),

  saveInstaller: (srcPath) => ipcRenderer.invoke("dialog:save-installer", srcPath),

  windowControls: {
    minimize: () => ipcRenderer.invoke("window:minimize"),
    toggleMaximize: () => ipcRenderer.invoke("window:toggle-maximize"),
    close: () => ipcRenderer.invoke("window:close"),
    isMaximized: () => ipcRenderer.invoke("window:is-maximized"),
    onMaximizeChange: (handler) => {
      const listener = (_e, isMaximized) => handler(isMaximized);
      ipcRenderer.on("window:maximized", listener);
      return () => ipcRenderer.removeListener("window:maximized", listener);
    },
  },

  /** Subscribe to deep-link URLs sent from the main process. Returns an unsubscribe fn. */
  onDeepLink: (handler) => {
    const listener = (_e, url) => handler(url);
    ipcRenderer.on("deep-link", listener);
    return () => ipcRenderer.removeListener("deep-link", listener);
  },

  /** The local engine (Python backend): its state, a manual restart, and text to paste into a bug report. */
  engine: {
    status: () => ipcRenderer.invoke("engine:status"),
    restart: () => ipcRenderer.invoke("engine:restart"),
    diagnostics: () => ipcRenderer.invoke("engine:diagnostics"),
    onStatus: (handler) => {
      const listener = (_e, msg) => handler(msg);
      ipcRenderer.on("engine:status", listener);
      return () => ipcRenderer.removeListener("engine:status", listener);
    },
  },

  /** Embedded test browser (BUILD-42). `available` is false when the debugging port couldn't be opened. */
  panel: {
    available: () => ipcRenderer.invoke("panel:available"),
    setBounds: (payload) => ipcRenderer.invoke("panel:bounds", payload),
    hide: () => ipcRenderer.invoke("panel:hide"),
    selectTab: (payload) => ipcRenderer.invoke("panel:select-tab", payload),
    newTab: (payload) => ipcRenderer.invoke("panel:new-tab", payload),
    closeTab: (payload) => ipcRenderer.invoke("panel:close-tab", payload),
    closeRun: (payload) => ipcRenderer.invoke("panel:close-run", payload),
    navigate: (payload) => ipcRenderer.invoke("panel:navigate", payload),
    downloadAction: (payload) => ipcRenderer.invoke("panel:download-action", payload),
    onTabs: (handler) => {
      const listener = (_e, msg) => handler(msg);
      ipcRenderer.on("panel:tabs", listener);
      return () => ipcRenderer.removeListener("panel:tabs", listener);
    },
    /** A browser key pressed inside the page that the renderer acts on: { runId, action: "focus-address" }. */
    onShortcut: (handler) => {
      const listener = (_e, msg) => handler(msg);
      ipcRenderer.on("panel:shortcut", listener);
      return () => ipcRenderer.removeListener("panel:shortcut", listener);
    },
  },

  update: {
    check: () => ipcRenderer.invoke("update:check"),
    start: () => ipcRenderer.invoke("update:start"),
    install: () => ipcRenderer.invoke("update:install"),
    getVersion: () => ipcRenderer.invoke("app:version"),
    onStatus: (handler) => {
      const listener = (_e, msg) => handler(msg);
      ipcRenderer.on("update:status", listener);
      return () => ipcRenderer.removeListener("update:status", listener);
    },
  },
});
