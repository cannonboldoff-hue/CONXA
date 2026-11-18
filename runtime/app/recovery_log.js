"use strict";
// Recovery log — the append-only JSONL audit of every cascade event, extracted
// verbatim from run.js. Rotates at 10 MB, never throws (a logging failure must
// not break an in-flight recovery).
const fs   = require("fs");
const path = require("path");

const CONXA_DIR = process.env.CONXA_DIR || require("./host_bridge").env().conxaDir;
const RECOVERY_LOG = path.join(CONXA_DIR, "logs", "recovery.log");
const RECOVERY_LOG_MAX = 10 * 1024 * 1024;

function appendRecoveryEvent(event) {
  try {
    fs.mkdirSync(path.dirname(RECOVERY_LOG), { recursive: true });
    if (fs.existsSync(RECOVERY_LOG) && fs.statSync(RECOVERY_LOG).size > RECOVERY_LOG_MAX) {
      fs.renameSync(RECOVERY_LOG, `${RECOVERY_LOG}.1`);
    }
    fs.appendFileSync(RECOVERY_LOG, `${require("./redact").scrubLine(JSON.stringify({ ts: new Date().toISOString(), ...event }))}\n`);
  } catch (_) {}
}

// BUILD-26 stage (a1) — the copilot's recovery-trail evidence. Events carry no run_id (most
// cascade call sites only know slug/step_index), so this filters by slug plus a ts floor instead
// of threading run_id through ~25 call sites for one reader. Caps at 200 matches, newest last.
function readRecentEvents(slug, sinceTs) {
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
      out.push(rec);
    }
    return out.slice(-200);
  } catch (_) {
    return [];
  }
}

module.exports = { CONXA_DIR, RECOVERY_LOG, RECOVERY_LOG_MAX, appendRecoveryEvent, readRecentEvents };
