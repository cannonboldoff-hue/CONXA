"use strict";
// Proof of Done stage 5: a step whose work is still running when the call runs out of time is PARKED, not failed,
// and the next call RE-CHECKS it. Nothing is clicked or uploaded again on the way back.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-pause-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";
process.env.CONXA_COMPLETION_START_GRACE_MS = "150";

const test = require("node:test");
const assert = require("node:assert");
const { EventEmitter } = require("node:events");

const pageScripts = require("../../app/page_scripts");
const { runPlan } = require("../../app/run");

const CLEAN = { busyCount: 0, regions: [] };
const TRAY = { busyCount: 0, regions: ["Uploading 1 item"] };
const DONE = { busyCount: 0, regions: ["1 upload complete"] };
const FAILED = { busyCount: 0, regions: ["Upload failed"] };

function livePage(samples) {
  const page = new EventEmitter();
  let calls = 0;
  const pressed = [];
  Object.assign(page, {
    pressed,
    url: () => "https://drive.test/",
    title: async () => "Drive",
    mainFrame: () => "main",
    context: () => ({ on: () => {} }),
    waitForLoadState: async () => {},
    waitForTimeout: async () => {},
    screenshot: async () => Buffer.from(""),
    keyboard: { press: async (k) => { pressed.push(k); } },
    evaluate: async (script) => (script === pageScripts.completionProbe ? samples[Math.min(calls++, samples.length - 1)] : null),
  });
  return page;
}
const STEP = () => [{ type: "keyboard_shortcut", value: "Enter", intent: "Upload the file", source_step_key: "k#1" }];
const tracker = () => { const events = []; return { events, emit: (c, d) => events.push([c, d]) }; };
const soon = () => Date.now() + 100; // the run's own limit is near, so the wait stops short of it at once

test("a wait that is still going when the run runs out of time parks the step instead of failing it", async () => {
  const page = livePage([CLEAN, TRAY]);
  const t = tracker();
  await assert.rejects(
    runPlan(page, STEP(), {}, 0, "slug-park", { tracker: t, deadlineAt: soon() }),
    (err) => {
      assert.strictEqual(err.proofPause, true);
      assert.strictEqual(err.completionGate, true, "never recovered into a re-run");
      assert.strictEqual(err.stepIndex, 0);
      assert.match(err.evidence, /Uploading/);
      assert.deepStrictEqual(err.recheck.regions, [], "what the page showed BEFORE the action, to measure the re-check against");
      assert.strictEqual(err.proof.steps[0].verdict, "still_working");
      assert.strictEqual(err.proof.steps[0].source_step_key, "k#1");
      return true;
    },
  );
  assert.deepStrictEqual(page.pressed, ["Enter"], "the action ran exactly once");
  assert.ok(!t.events.some(([c]) => c === "tier_ok"), "not reported ok");
  assert.ok(!t.events.some(([c, d]) => c === "step_fail"), "a parked wait is not a failed step");
});

test("the next call re-checks without doing the step again, and a finished upload is proven", async () => {
  const first = livePage([CLEAN, TRAY]);
  let baseline;
  await assert.rejects(runPlan(first, STEP(), {}, 0, "slug-park-ok", { tracker: tracker(), deadlineAt: soon() }), (e) => { baseline = e.recheck; return true; });

  const later = livePage([DONE]);
  const result = await runPlan(later, STEP(), { __proof_recheck_0: JSON.stringify(baseline) }, 0, "slug-park-ok", { tracker: tracker() });
  assert.deepStrictEqual(later.pressed, [], "nothing was pressed again");
  assert.deepStrictEqual([result.proof.steps[0].verdict, result.proof.steps[0].rung], ["proven", "completed"]);
  assert.deepStrictEqual(result.proof.notes, []);
});

test("a re-check that finds the app says it failed ends the run as a failure, still without a re-run", async () => {
  const later = livePage([FAILED]);
  await assert.rejects(
    runPlan(later, STEP(), { __proof_recheck_0: JSON.stringify({ busyCount: 0, regions: [] }) }, 0, "slug-park-fail", { tracker: tracker() }),
    (err) => {
      assert.strictEqual(err.fault, true);
      assert.match(err.message, /This step didn't finish: the app said "Upload failed"/);
      return true;
    },
  );
  assert.deepStrictEqual(later.pressed, []);
});

test("a re-check that finds it still going parks it again, with the same baseline", async () => {
  const baseline = { busyCount: 0, regions: [] };
  const later = livePage([TRAY]);
  await assert.rejects(
    runPlan(later, STEP(), { __proof_recheck_0: JSON.stringify(baseline) }, 0, "slug-park-again", { tracker: tracker(), deadlineAt: soon() }),
    (err) => err.proofPause === true && err.recheck.busyCount === 0 && Array.isArray(err.recheck.regions),
  );
  assert.deepStrictEqual(later.pressed, []);
});
