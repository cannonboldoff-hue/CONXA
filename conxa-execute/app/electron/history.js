"use strict";
const fs = require("fs");
const path = require("path");

function historyPath() {
  return path.join(require("./account").dir(), "run-history.json");
}

function loadHistory() {
  const p = historyPath();
  let raw;
  try {
    raw = fs.readFileSync(p, "utf8");
  } catch {
    return []; // no history file yet — nothing to recover
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    // The file exists but isn't valid JSON — don't silently start clean, or
    // the very next pushHistory() overwrites it and the run history is gone
    // for good. Move it aside so a person could still recover it by hand.
    console.error("history: run-history.json is corrupt, moving it aside", err);
    try {
      fs.renameSync(p, `${p}.corrupt-${Date.now()}`);
    } catch (_) {}
    return [];
  }
}

function pushHistory(entry) {
  const list = loadHistory();
  list.unshift(entry);
  fs.mkdirSync(path.dirname(historyPath()), { recursive: true });
  fs.writeFileSync(historyPath(), JSON.stringify(list.slice(0, 50), null, 2));
  return list.slice(0, 50);
}

function deleteHistoryEntry(at) {
  const list = loadHistory().filter((e) => e.at !== at);
  fs.mkdirSync(path.dirname(historyPath()), { recursive: true });
  fs.writeFileSync(historyPath(), JSON.stringify(list, null, 2));
  return list;
}

function updateRun(runId, patch) {
  const list = loadHistory();
  const entry = list.find((e) => e.run_id === runId);
  if (!entry) return list;
  Object.assign(entry, patch);
  fs.writeFileSync(historyPath(), JSON.stringify(list, null, 2));
  return list;
}

module.exports = { loadHistory, pushHistory, deleteHistoryEntry, updateRun };
