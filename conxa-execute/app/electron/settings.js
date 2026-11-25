"use strict";
const fs = require("fs");
const path = require("path");
const { safeStorage } = require("electron");

function settingsPath() {
  return path.join(require("./account").dir(), "byo-settings.bin");
}

function loadSettings() {
  const p = settingsPath();
  if (!fs.existsSync(p)) return { activeWorkspaceId: "", runPermission: "ask" };
  try {
    const buf = fs.readFileSync(p);
    if (!safeStorage.isEncryptionAvailable()) {
      return { activeWorkspaceId: "", runPermission: "ask", error: "OS encryption is not available; settings were not loaded." };
    }
    const json = JSON.parse(safeStorage.decryptString(buf));
    return {
      activeWorkspaceId: String(json.activeWorkspaceId || ""),
      runPermission: json.runPermission === "auto" ? "auto" : "ask",
    };
  } catch (e) {
    return { activeWorkspaceId: "", runPermission: "ask", error: e.message };
  }
}

function saveSettings({ activeWorkspaceId, runPermission }) {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("OS encryption is not available; settings were not saved.");
  }
  const prev = loadSettings();
  const next = {
    activeWorkspaceId: String(activeWorkspaceId || prev.activeWorkspaceId || "").trim(),
    runPermission: runPermission === "auto" || (runPermission === undefined && prev.runPermission === "auto") ? "auto" : "ask",
  };
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), safeStorage.encryptString(JSON.stringify(next)));
  return next;
}

function publicSettings() {
  const s = loadSettings();
  return { activeWorkspaceId: s.activeWorkspaceId, runPermission: s.runPermission, error: s.error || null };
}

module.exports = { loadSettings, saveSettings, publicSettings };
