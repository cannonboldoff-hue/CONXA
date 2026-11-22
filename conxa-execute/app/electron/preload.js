"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("conxaExecute", {
  runtimeStatus: () => ipcRenderer.invoke("runtime:status"),
  listSkills: () => ipcRenderer.invoke("skills:list"),
  getInputs: (payload) => ipcRenderer.invoke("skills:inputs", payload),
  execute: (payload) => ipcRenderer.invoke("skills:execute", payload),
  history: () => ipcRenderer.invoke("history:list"),
  deleteHistory: (payload) => ipcRenderer.invoke("history:delete", payload),
  getSettings: () => ipcRenderer.invoke("settings:get"),
  saveSettings: (payload) => ipcRenderer.invoke("settings:save", payload),
  update: {
    check: () => ipcRenderer.invoke("update:check"),
    install: () => ipcRenderer.invoke("update:install"),
    getVersion: () => ipcRenderer.invoke("app:version"),
    onStatus: (cb) => {
      const listener = (_e, payload) => cb(payload);
      ipcRenderer.on("update:status", listener);
      return () => ipcRenderer.removeListener("update:status", listener);
    },
  },
  chatSend: (payload) => ipcRenderer.invoke("chat:send", payload),
  onChatDelta: (cb) => {
    const listener = (_e, payload) => cb(payload);
    ipcRenderer.on("chat:delta", listener);
    return () => ipcRenderer.removeListener("chat:delta", listener);
  },
  onConfirmRun: (cb) => {
    const listener = (_e, payload) => cb(payload);
    ipcRenderer.on("chat:confirm-run", listener);
    return () => ipcRenderer.removeListener("chat:confirm-run", listener);
  },
  onSessionUpdated: (cb) => {
    const listener = (_e, payload) => cb(payload);
    ipcRenderer.on("chat:session-updated", listener);
    return () => ipcRenderer.removeListener("chat:session-updated", listener);
  },
  confirmRunReply: (payload) => ipcRenderer.invoke("chat:confirm-run-reply", payload),
  listSessions: () => ipcRenderer.invoke("sessions:list"),
  createSession: () => ipcRenderer.invoke("sessions:create"),
  loadSession: (payload) => ipcRenderer.invoke("sessions:load", payload),
  deleteSession: (payload) => ipcRenderer.invoke("sessions:delete", payload),
  renameSession: (payload) => ipcRenderer.invoke("sessions:rename", payload),
  searchSessions: (payload) => ipcRenderer.invoke("sessions:search", payload),
  captureScreen: () => ipcRenderer.invoke("screen:capture"),
  authLogin: () => ipcRenderer.invoke("auth:login"),
  authCancel: () => ipcRenderer.invoke("auth:cancel"),
  authLogout: () => ipcRenderer.invoke("auth:logout"),
  authStatus: () => ipcRenderer.invoke("auth:status"),
  getContexts: () => ipcRenderer.invoke("account:contexts"),
  setContext: (payload) => ipcRenderer.invoke("account:set-context", payload),
  openExternal: (payload) => ipcRenderer.invoke("shell:openExternal", payload),
  panel: {
    setBounds: (payload) => ipcRenderer.invoke("panel:bounds", payload),
    setChat: (payload) => ipcRenderer.invoke("panel:set-chat", payload),
    selectTab: (payload) => ipcRenderer.invoke("panel:select-tab", payload),
    closeTab: (payload) => ipcRenderer.invoke("panel:close-tab", payload),
    navigate: (payload) => ipcRenderer.invoke("panel:navigate", payload),
    newTab: (payload) => ipcRenderer.invoke("panel:new-tab", payload),
    loginDone: (payload) => ipcRenderer.invoke("panel:login-done", payload),
    hideAll: () => ipcRenderer.invoke("panel:hide-all"),
    capture: () => ipcRenderer.invoke("panel:capture"),
    onTabsChanged: (cb) => {
      const listener = (_e, payload) => cb(payload);
      ipcRenderer.on("panel:tabs", listener);
      return () => ipcRenderer.removeListener("panel:tabs", listener);
    },
  },
  windowControls: {
    minimize: () => ipcRenderer.invoke("window:minimize"),
    toggleMaximize: () => ipcRenderer.invoke("window:toggle-maximize"),
    close: () => ipcRenderer.invoke("window:close"),
    isMaximized: () => ipcRenderer.invoke("window:is-maximized"),
    onMaximizeChange: (cb) => {
      const listener = (_e, maximized) => cb(Boolean(maximized));
      ipcRenderer.on("window:maximized", listener);
      return () => ipcRenderer.removeListener("window:maximized", listener);
    },
  },
});
