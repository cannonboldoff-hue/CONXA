"use strict";
// BUILD-106 (2), owner decision 2026-11-21: a "still working" step before the last one hands over to the next step (by
// design: a popup the next step answers, work the next step waits on). It keeps that verdict, and the run says "please
// check" for it ONLY when no later step's own check confirmed anything. A later step proven by a real check (more than
// "it ran") keeps the run "Done", so recorded accept-the-popup flows whose later steps confirm still pass.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-handover-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";
process.env.CONXA_COMPLETION_START_GRACE_MS = "150";
process.env.CONXA_COMPLETION_SHORT_MS = "300";

const test = require("node:test");
const assert = require("node:assert");
const { EventEmitter } = require("node:events");

const pageScripts = require("../../app/page_scripts");
const proof = require("../../app/proof");
const { runPlan, resolveLaterProofs } = require("../../app/run");

const CLEAN = { busyCount: 0, regions: [] };
const BUSY = { busyCount: 0, regions: ["Uploading 1 item"] };
const DONE = { busyCount: 0, regions: ["1 upload complete"] };

// What the page shows follows the keys pressed: before the first press nothing, after it the import is running, and
// after the second press `afterSecond`.
function livePage(afterSecond) {
  const page = new EventEmitter();
  const pressed = [];
  Object.assign(page, {
    pressed,
    url: () => "https://app.test/",
    title: async () => "App",
    mainFrame: () => "main",
    context: () => ({ on: () => {} }),
    waitForLoadState: async () => {},
    waitForTimeout: async () => {},
    screenshot: async () => Buffer.from(""),
    keyboard: { press: async (k) => { pressed.push(k); } },
    evaluate: async (script) => (script === pageScripts.completionProbe ? [CLEAN, BUSY, afterSecond][Math.min(pressed.length, 2)] : null),
  });
  return page;
}
const STEPS = () => [
  // Its work must finish (a long job); the short wait before a later step ends while it still shows progress.
  { type: "keyboard_shortcut", value: "Enter", intent: "Start the import", source_step_key: "k#1",
    validation: { effect: { kind: "long_job", required_rung: "completed", checks: [] } } },
  { type: "keyboard_shortcut", value: "Enter", intent: "Open the import report", source_step_key: "k#2" },
];
const tracker = () => ({ emit: () => {} });
const NOTE = proof.unconfirmedNote(STEPS()[0], 0);

test("a later step whose own check confirms keeps the run done, with no note for the handed-over step", async () => {
  const result = await runPlan(livePage(DONE), STEPS(), {}, 0, "slug-handover-ok", { tracker: tracker() });
  assert.deepStrictEqual(result.proof.steps.map((s) => s.verdict), ["still_working", "proven"]);
  assert.deepStrictEqual(result.proof.notes, []);
  assert.strictEqual(proof.runOutcome({ notes: result.proof.notes.length }), "done");
});

test("nothing later confirms: the handed-over step keeps its verdict and the run says please check", async () => {
  const result = await runPlan(livePage(CLEAN), STEPS(), {}, 0, "slug-handover-unconfirmed", { tracker: tracker() });
  assert.strictEqual(result.proof.steps[0].verdict, "still_working");
  assert.notStrictEqual(result.proof.steps[1].verdict, "proven");
  assert.ok(result.proof.notes.includes(NOTE), JSON.stringify(result.proof.notes));
  assert.strictEqual(proof.runOutcome({ notes: result.proof.notes.length }), "done_please_check");
});

// The rule alone, on the proof record: "it ran" never confirms; a later check that said yes does.
const entry = (verdict, checks) => ({ verdict, checks });
const ran = { rung: "performed", witness: "target", answer: "yes" };
const waiting = (later) => ({ proof: { steps: [entry("still_working", [ran]), later], notes: [], later: [], waits: [{ at: 0, note: NOTE }] } });

test("a later step proven only by having run does not confirm the handed-over step", () => {
  const state = waiting(entry("proven", [ran, { rung: "accepted", witness: "renderer", answer: "cant_tell" }]));
  resolveLaterProofs(state, new Set());
  assert.deepStrictEqual(state.proof.notes, [NOTE]);
  assert.deepStrictEqual(state.proof.waits, []);
});

test("a later accept-the-popup step proven by its own check confirms the handed-over step", () => {
  const state = waiting(entry("proven", [ran, { rung: "accepted", witness: "renderer", answer: "yes" }]));
  resolveLaterProofs(state, new Set());
  assert.deepStrictEqual(state.proof.notes, []);
});

test("a failure signal seen later is never a confirmation", () => {
  const state = waiting(entry("proven", [ran, { rung: "completed", witness: "renderer", role: "veto", answer: "yes" }]));
  resolveLaterProofs(state, new Set());
  assert.deepStrictEqual(state.proof.notes, [NOTE]);
});
