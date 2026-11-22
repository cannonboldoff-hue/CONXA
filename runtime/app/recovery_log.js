"use strict";
// Recovery log — the append-only JSONL record of a run, kept on this computer only (RT-16/17).
// It is the full local record: every line carries run_id / slug / step when a run is in scope, so a
// run can be rebuilt from it alone. The cloud tracker (tracker.js) gets a small subset and never
// reads this file. Rotates at 10 MB over three files, never throws (a logging failure must not
// break an in-flight recovery).
const fs   = require("fs");
const path = require("path");
const { AsyncLocalStorage } = require("async_hooks");

const CONXA_DIR = process.env.CONXA_DIR || require("./host_bridge").env().conxaDir;
const RECOVERY_LOG = path.join(CONXA_DIR, "logs", "recovery.log");
const RECOVERY_LOG_MAX = 10 * 1024 * 1024;
const RECOVERY_LOG_KEEP = 3; // recovery.log.1 .. .3

// Who a line belongs to. Set once per step by run.js (enterStepScope); every append made while that
// step runs, however deep in the call chain, picks it up. A run's own fields win over the scope.
const scope = new AsyncLocalStorage();

function enterStepScope(fields) {
  const prev = scope.getStore() || {};
  scope.enterWith({ ...prev, ...fields });
}

function rotate() {
  if (!fs.existsSync(RECOVERY_LOG) || fs.statSync(RECOVERY_LOG).size <= RECOVERY_LOG_MAX) return;
  for (let n = RECOVERY_LOG_KEEP; n >= 2; n--) {
    const from = `${RECOVERY_LOG}.${n - 1}`;
    if (fs.existsSync(from)) fs.renameSync(from, `${RECOVERY_LOG}.${n}`);
  }
  fs.renameSync(RECOVERY_LOG, `${RECOVERY_LOG}.1`);
}

function appendRecoveryEvent(event) {
  try {
    fs.mkdirSync(path.dirname(RECOVERY_LOG), { recursive: true });
    rotate();
    const line = { ts: new Date().toISOString(), ...(scope.getStore() || {}), ...event };
    fs.appendFileSync(RECOVERY_LOG, `${require("./redact").scrubLine(JSON.stringify(line))}\n`);
  } catch (_) {}
}

// BUILD-26 stage (a1) — the copilot's recovery-trail evidence. Filters by slug plus a ts floor.
// Caps at 200 matches, newest last. `keep` (optional) narrows the lines before the cap.
function readRecentEvents(slug, sinceTs, keep = null) {
  try {
    if (!fs.existsSync(RECOVERY_LOG)) return [];
    const floor = typeof sinceTs === "number" ? sinceTs : Date.parse(sinceTs || "") || 0;
    const out = [];
    for (const line of fs.readFileSync(RECOVERY_LOG, "utf8").split("\n")) {
      if (!line.trim()) continue;
      let rec;
      try { rec = JSON.parse(line); } catch (_) { continue; }
      if (slug && rec.slug !== slug) continue;
      const t = Date.parse(rec.ts || "");
      if (Number.isFinite(t) && t < floor) continue;
      if (keep && !keep(rec)) continue;
      out.push(rec);
    }
    return out.slice(-200);
  } catch (_) {
    return [];
  }
}

module.exports = { CONXA_DIR, RECOVERY_LOG, RECOVERY_LOG_MAX, RECOVERY_LOG_KEEP, appendRecoveryEvent, enterStepScope, readRecentEvents };
