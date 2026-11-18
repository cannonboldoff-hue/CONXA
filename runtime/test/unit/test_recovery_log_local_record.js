"use strict";
// RT-16/17: recovery.log is the full local record of a run; the cloud tracker gets only a small subset.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-reclog-"));
process.env.CONXA_DIR = dir;
const log = require("../../app/recovery_log");

function lines() {
  return fs.readFileSync(log.RECOVERY_LOG, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

test("a line written while a step is in scope carries the run and step", () => {
  log.enterStepScope({ run_id: "r1", slug: "wf", step_index: 3, step_key: "abc#0" });
  log.appendRecoveryEvent({ event: "resolve", outcome: "found", score: 0.9 });
  const rec = lines().pop();
  assert.equal(rec.run_id, "r1");
  assert.equal(rec.step_key, "abc#0");
  assert.equal(rec.outcome, "found");
});

test("a field the event sets itself wins over the scope", () => {
  log.enterStepScope({ run_id: "r1", slug: "wf", step_index: 3 });
  log.appendRecoveryEvent({ event: "x", step_index: 9 });
  assert.equal(lines().pop().step_index, 9);
});

test("rotation keeps three older files, then drops the oldest", () => {
  const big = "x".repeat(1024);
  for (let round = 0; round < 5; round++) {
    fs.writeFileSync(log.RECOVERY_LOG, `${JSON.stringify({ round, pad: big })}\n`.repeat(1));
    fs.truncateSync(log.RECOVERY_LOG, log.RECOVERY_LOG_MAX + 1);
    log.appendRecoveryEvent({ event: "after_rotation", round });
  }
  for (const n of [1, 2, 3]) assert.ok(fs.existsSync(`${log.RECOVERY_LOG}.${n}`), `.${n} exists`);
  assert.ok(!fs.existsSync(`${log.RECOVERY_LOG}.4`), "never a fourth");
});

test("detail that stays on this computer never appears in a tracker.emit call", () => {
  const localOnly = /\b(checks|tied|signals|candidates)\s*[:,}]/;
  const appDir = path.join(__dirname, "..", "..", "app");
  for (const f of fs.readdirSync(appDir).filter((n) => n.endsWith(".js"))) {
    const src = fs.readFileSync(path.join(appDir, f), "utf8");
    for (const m of src.matchAll(/tracker\.emit\(\s*"[a-z_]+"\s*,\s*\{([^}]*)\}/g)) {
      assert.ok(!localOnly.test(m[1]), `${f}: tracker.emit carries local-only detail: ${m[0]}`);
    }
  }
});
