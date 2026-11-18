"use strict";
// Completion gate: "the action ran" is not "the action finished". Unit cases for the wait itself plus
// an end-to-end pass through runPlan (a stand-in upload that fails, and one that finishes).

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-completion-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";
process.env.CONXA_COMPLETION_START_GRACE_MS = "150"; // keep the "no sign" cases quick

const test = require("node:test");
const assert = require("node:assert");

const pageScripts = require("../../app/page_scripts");
const pageStatus = require("../../app/page_status");
const { waitForCompletion, classifySample, completionMessage, completionWarning } = require("../../app/completion");
const { classifyCompletionFailure, classifyFault } = require("../../app/fault_classify");
const { runPlan } = require("../../app/run");

const CLEAN = { busyCount: 0, regions: [] };
const TRAY = { busyCount: 0, regions: ["Uploading 1 item JENKINS_HOME.gitignore"] };
const DONE_TRAY = { busyCount: 0, regions: ["1 upload complete"] };
const FAILED_TRAY = { busyCount: 0, regions: ["Upload failed"] };

// A page whose completionProbe returns a scripted sequence (the last entry repeats) and whose
// dialogProbe returns `dialog`. `.on` captures the handlers page_status.attach registers.
function mockPage(samples, { dialog = null } = {}) {
  const handlers = {};
  let calls = 0;
  const page = {
    evaluate: async (script) => {
      if (script === pageScripts.dialogProbe) return dialog;
      const s = samples[Math.min(calls, samples.length - 1)];
      calls++;
      return s;
    },
    on: (ev, fn) => { handlers[ev] = fn; },
    mainFrame: () => "main",
  };
  pageStatus.attach(page);
  return { page, handlers };
}

function req(method, url = "https://drive.test/upload?id=1", type = "xhr") {
  return { method: () => method, url: () => url, resourceType: () => type, failure: () => ({ errorText: "net::ERR_CONNECTION_RESET" }) };
}
function resp(request, status) {
  return { status: () => status, request: () => request, frame: () => "other" };
}
const opts = (over) => ({ baseline: CLEAN, sinceMs: 0, mode: "full", budgetMs: 1500, pollMs: 10, quietMs: 30, startGraceMs: 120, ...over });
// Drive's real upload tray: three buttons, none of them a question.
const DRIVE_TRAY = { title: "Uploading 1 item", text: "report.pdf 42%", hasFields: false, controls: [{ name: "Minimize" }, { name: "Close" }, { name: "Cancel all" }] };
const REPLACE_POPUP = { title: "Replace file?", text: "", hasFields: false, controls: [{ name: "Replace" }, { name: "Keep both" }] };

test("classifySample counts only progress that is new since the baseline", () => {
  assert.strictEqual(classifySample(TRAY, CLEAN).busy, true);
  assert.strictEqual(classifySample(TRAY, TRAY).busy, false, "a tray already there before the action is not this step's");
  assert.strictEqual(classifySample({ busyCount: 2, regions: [] }, { busyCount: 1, regions: [] }).busy, true);
  assert.strictEqual(classifySample({ busyCount: 1, regions: [] }, { busyCount: 1, regions: [] }).busy, false);
  assert.strictEqual(classifySample(DONE_TRAY, CLEAN).busy, false);
});

test("a quiet page is done almost at once for a short wait", async () => {
  const { page } = mockPage([CLEAN]);
  const v = await waitForCompletion(page, opts({ mode: "short" }));
  assert.strictEqual(v.status, "done");
  assert.ok(v.waitedMs < 500, `waited ${v.waitedMs}ms`);
});

test("a full wait that never sees the work start says no_sign, not done", async () => {
  const { page } = mockPage([CLEAN]);
  const v = await waitForCompletion(page, opts({ startGraceMs: 80 }));
  assert.strictEqual(v.status, "no_sign");
  assert.match(completionWarning(v), /couldn't confirm this step finished/);
});

test("a tray that opens late is still waited out, not called done", async () => {
  // ~20 quiet polls, then the tray, then it clears: silence before the first sign proves nothing.
  const { page } = mockPage([...Array(20).fill(CLEAN), TRAY, TRAY, TRAY, DONE_TRAY], { dialog: DRIVE_TRAY });
  const v = await waitForCompletion(page, opts({ startGraceMs: 2000 }));
  assert.strictEqual(v.status, "done");
  assert.ok(v.waitedMs >= 150, `returned before the tray appeared: ${v.waitedMs}ms`);
});

test("a data-changing request that starts and finishes counts as the first sign", async () => {
  const { page, handlers } = mockPage([CLEAN]);
  const r = req("POST");
  handlers.request(r);
  handlers.requestfinished(r);
  const v = await waitForCompletion(page, opts());
  assert.strictEqual(v.status, "done");
});

test("Drive's real upload tray (Minimize / Close / Cancel all) is waited out, never handed off", async () => {
  const { page } = mockPage([TRAY, TRAY, TRAY, DONE_TRAY], { dialog: DRIVE_TRAY });
  const v = await waitForCompletion(page, opts());
  assert.strictEqual(v.status, "done");
  assert.ok(v.waitedMs >= 30, `handed off at once: ${v.waitedMs}ms`);
});

test("a deadline stops the wait short with still_working, so the answer still reaches the caller", async () => {
  const { page } = mockPage([TRAY]);
  const v = await waitForCompletion(page, opts({ budgetMs: 5000, deadlineAt: Date.now() + 120, deadlineMarginMs: 60 }));
  assert.strictEqual(v.status, "still_working");
  assert.strictEqual(v.reason, "deadline");
  assert.match(completionMessage(v), /ran out of time/);
});

test("a cancelled run stops the wait at once", async () => {
  const { page } = mockPage([TRAY]);
  const v = await waitForCompletion(page, opts({ cancelCheck: () => true }));
  assert.strictEqual(v.status, "still_working");
  assert.strictEqual(v.reason, "cancelled");
});

test("a question left open when the work ended is handed off, not passed", async () => {
  const { page } = mockPage([DONE_TRAY], { dialog: REPLACE_POPUP });
  const v = await waitForCompletion(page, opts());
  assert.strictEqual(v.status, "handoff");
  assert.strictEqual(v.reason, "decision_popup");
});

test("a panel that leaves the page usable never stops the wait; it is reported for the step's own proof", async () => {
  const tray = { title: "1 upload complete", text: "", hasFields: false, blocks: false,
    controls: [{ name: "Minimize" }, { name: "Close" }, { name: "Show file location" }] };
  const { page } = mockPage([DONE_TRAY], { dialog: tray });
  const v = await waitForCompletion(page, opts());
  assert.strictEqual(v.status, "done");
  assert.strictEqual(v.leftOpen.title, "1 upload complete");
  // The same buttons with the page covered (or not measured) are still a question: nothing makes it less careful.
  for (const blocks of [true, null, undefined]) {
    const r = await waitForCompletion(mockPage([DONE_TRAY], { dialog: { ...tray, blocks } }).page, opts());
    assert.strictEqual(r.status, "handoff", `blocks=${blocks}`);
  }
});

test("a result text is not work in flight, even if a recording learned one of its words as progress", async () => {
  // The real Drive recording: words kept without their phrase, nothing learned as success.
  const signals = { progress: new RegExp(String.raw`(cancel|starting|upload|uploading)`, "i"), success: null, failure: null };
  const v = await waitForCompletion(mockPage([DONE_TRAY]).page, opts({ signals }));
  assert.strictEqual(v.status, "done");
  assert.strictEqual(classifySample({ busyCount: 0, regions: ["Uploading 1 item"] }, CLEAN, signals).busy, true, "real progress still counts");
});

test("progress that clears is done, and only after it cleared", async () => {
  const { page } = mockPage([TRAY, TRAY, TRAY, DONE_TRAY]);
  const v = await waitForCompletion(page, opts());
  assert.strictEqual(v.status, "done");
  assert.ok(v.waitedMs >= 30);
});

test("a new failure message fails a full wait, even right after progress", async () => {
  const { page } = mockPage([TRAY, TRAY, FAILED_TRAY]);
  const v = await waitForCompletion(page, opts());
  assert.strictEqual(v.status, "failed");
  assert.match(completionMessage(v), /This step didn't finish: the app said "Upload failed"\./);
});

test("a short wait never fails the step on text", async () => {
  const { page } = mockPage([FAILED_TRAY]);
  const v = await waitForCompletion(page, opts({ mode: "short" }));
  assert.strictEqual(v.status, "done");
});

test("an error banner that was already on the page is ignored (and is no sign the step started)", async () => {
  const { page } = mockPage([FAILED_TRAY]);
  const v = await waitForCompletion(page, opts({ baseline: FAILED_TRAY }));
  assert.strictEqual(v.status, "no_sign", "not failed by an old banner, and not called done on silence");
  const short = await waitForCompletion(mockPage([FAILED_TRAY]).page, opts({ baseline: FAILED_TRAY, mode: "short" }));
  assert.strictEqual(short.status, "done");
});

test("progress that never clears times out a full wait and lets a short wait carry on", async () => {
  const full = await waitForCompletion(mockPage([TRAY]).page, opts({ budgetMs: 120 }));
  assert.strictEqual(full.status, "timed_out");
  assert.match(completionMessage(full), /still in progress after/);
  const short = await waitForCompletion(mockPage([TRAY]).page, opts({ mode: "short", budgetMs: 120 }));
  assert.strictEqual(short.status, "still_working", "carries on, but never says it finished");
  assert.strictEqual(short.reason, "budget");
});

test("a refused data-changing request fails a full wait; a later success on the same endpoint clears it", async () => {
  const a = mockPage([CLEAN]);
  const r = req("POST");
  a.handlers.request(r);
  a.handlers.response(resp(r, 413));
  a.handlers.requestfinished(r);
  const v = await waitForCompletion(a.page, opts());
  assert.strictEqual(v.status, "failed");
  assert.match(v.evidence, /status 413/);

  const b = mockPage([CLEAN]);
  const r1 = req("POST");
  b.handlers.request(r1);
  b.handlers.response(resp(r1, 500));
  b.handlers.requestfinished(r1);
  const r2 = req("POST");
  b.handlers.request(r2);
  b.handlers.response(resp(r2, 200));
  b.handlers.requestfinished(r2);
  assert.strictEqual((await waitForCompletion(b.page, opts())).status, "done");
});

test("a data-changing request still open holds a full wait; once it finishes the step is done", async () => {
  const { page, handlers } = mockPage([CLEAN]);
  const r = req("PUT");
  handlers.request(r);
  setTimeout(() => handlers.requestfinished(r), 120);
  const v = await waitForCompletion(page, opts({ budgetMs: 2000 }));
  assert.strictEqual(v.status, "done");
  assert.ok(v.waitedMs >= 100, `finished too early: ${v.waitedMs}ms`);
});

test("a request that stays open with nothing on screen is done with a warning after the grace time", async () => {
  const { page, handlers } = mockPage([CLEAN]);
  handlers.request(req("POST"));
  const v = await waitForCompletion(page, opts({ pendingBudgetMs: 80, budgetMs: 2000 }));
  assert.strictEqual(v.status, "done");
  assert.match(v.warning, /still sending data/);
});

test("a short wait ignores open requests", async () => {
  const { page, handlers } = mockPage([CLEAN]);
  handlers.request(req("POST"));
  const v = await waitForCompletion(page, opts({ mode: "short" }));
  assert.strictEqual(v.status, "done");
  assert.strictEqual(v.warning, undefined);
});

test("a request that started before the action is not this step's", async () => {
  const { page, handlers } = mockPage([CLEAN]);
  handlers.request(req("POST"));
  const v = await waitForCompletion(page, opts({ sinceMs: Date.now() + 60_000 }));
  assert.strictEqual(v.status, "no_sign", "an earlier request is not a sign this step started anything");
});

test("a native dialog or a decision popup is handed off, never waited out or answered", async () => {
  const native = await waitForCompletion(mockPage([TRAY]).page, opts({ dialogQueue: [{}] }));
  assert.strictEqual(native.status, "handoff");
  const dialog = { title: "Replace file?", text: "", hasFields: false, controls: [{ name: "Replace" }, { name: "Keep both" }] };
  const popup = await waitForCompletion(mockPage([TRAY], { dialog }).page, opts());
  assert.strictEqual(popup.status, "handoff");
});

test("a page that cannot answer is never a failure, and never a success either", async () => {
  const page = { evaluate: async () => { throw new Error("closed"); }, on: () => {} };
  const v = await waitForCompletion(page, opts());
  assert.strictEqual(v.status, "cant_tell");
  assert.strictEqual(v.reason, "probe_unavailable");
  assert.match(completionWarning(v), /couldn't check whether this step finished/);
});

test("page_status only tracks data-changing xhr/fetch requests", () => {
  const { page, handlers } = mockPage([CLEAN]);
  handlers.request(req("GET"));
  handlers.request(req("POST", "https://x.test/a.png", "image"));
  assert.strictEqual(pageStatus.pendingMutations(page, 0), 0);
  const r = req("DELETE");
  handlers.request(r);
  assert.strictEqual(pageStatus.pendingMutations(page, 0), 1);
  handlers.requestfailed(r);
  assert.strictEqual(pageStatus.pendingMutations(page, 0), 0);
  assert.ok(pageStatus.mutationFailure(page, 0), "a reset connection is a failure");
  const aborted = req("POST");
  const q = mockPage([CLEAN]);
  q.handlers.request(aborted);
  aborted.failure = () => ({ errorText: "net::ERR_ABORTED" });
  q.handlers.requestfailed(aborted);
  assert.strictEqual(pageStatus.mutationFailure(q.page, 0), null, "an aborted request is not a failure");
});

test("classifyCompletionFailure is narrow: real failures only", () => {
  assert.strictEqual(classifyCompletionFailure(["Upload failed"]).kind, "action_failed");
  assert.strictEqual(classifyCompletionFailure(["Couldn't send your message"]).kind, "action_failed");
  assert.strictEqual(classifyCompletionFailure(["Too many requests"]).kind, "rate_limited");
  for (const benign of ["1 upload complete", "No results found", "Imported 5 rows, 0 failed", "Name is required", "No errors"]) {
    assert.strictEqual(classifyCompletionFailure([benign]), null, benign);
  }
});

test("the failure-path classifier is unchanged by the completion rules", () => {
  assert.strictEqual(classifyFault({ alerts: ["Upload failed"], mayHaveActed: true }), null);
  assert.strictEqual(classifyFault({ alerts: ["Page not found"], mayHaveActed: true }).kind, "not_found");
});

test("completionProbe is a self-contained function the page can parse on its own", () => {
  assert.strictEqual(typeof pageScripts.completionProbe, "function");
  assert.doesNotThrow(() => new Function(`return (${pageScripts.completionProbe.toString()})`)());
});

// ─── end to end through runPlan ────────────────────────────────────────────────────────────

function runPage(samples, { dialog = null } = {}) {
  let calls = 0;
  const pressed = [];
  return {
    pressed,
    url: () => "https://drive.test/",
    title: async () => "Drive",
    on: () => {},
    mainFrame: () => "main",
    context: () => ({ on: () => {} }),
    waitForLoadState: async () => {},
    waitForTimeout: async () => {},
    screenshot: async () => Buffer.from(""),
    keyboard: { press: async (k) => { pressed.push(k); } },
    evaluate: async (script) => {
      if (script === pageScripts.dialogProbe) return dialog;
      if (script !== pageScripts.completionProbe) return null;
      // call 0 is the baseline taken before the action; the rest are the gate's samples
      const s = samples[Math.min(calls, samples.length - 1)];
      calls++;
      return s;
    },
  };
}
const ACT = [{ type: "keyboard_shortcut", value: "Enter", intent: "Upload the file" }];

test("runPlan: an upload that ends in 'Upload failed' fails the run, with no recovery and no second press", async () => {
  const events = [];
  const page = runPage([CLEAN, TRAY, FAILED_TRAY]);
  const tracker = { emit: (code, data) => events.push([code, data]) };
  await assert.rejects(
    runPlan(page, ACT, {}, 0, "slug-completion-fail", { tracker }),
    (err) => {
      assert.strictEqual(err.fault, true);
      assert.strictEqual(err.faultKind, "action_failed");
      assert.match(err.message, /This step didn't finish: the app said "Upload failed"/);
      return true;
    },
  );
  assert.deepStrictEqual(page.pressed, ["Enter"], "the action ran exactly once");
  assert.ok(events.some(([c, d]) => c === "step_fail" && d.fc === "completion_failed"));
  assert.ok(!events.some(([c]) => c === "repair_event" || c === "tier_ok"), "not recovered, not reported ok");
});

test("runPlan: the run is not reported done until the progress has cleared", async () => {
  const events = [];
  const page = runPage([CLEAN, TRAY, TRAY, DONE_TRAY]);
  const tracker = { emit: (code, data) => events.push([code, data]) };
  await runPlan(page, ACT, {}, 0, "slug-completion-ok", { tracker });
  const done = events.find(([c]) => c === "completion");
  assert.deepStrictEqual([done[1].mode, done[1].st], ["full", "done"]);
  assert.ok(done[1].waited >= 500, `returned before the tray cleared: ${done[1].waited}ms`);
  assert.ok(events.findIndex(([c]) => c === "completion") < events.findIndex(([c]) => c === "tier_ok"), "gate runs before the step is reported ok");
});

test("runPlan: a popup left open by the final step stops the run instead of reporting done", async () => {
  const events = [];
  const page = runPage([CLEAN, DONE_TRAY], { dialog: REPLACE_POPUP });
  const tracker = { emit: (code, data) => events.push([code, data]) };
  await assert.rejects(
    runPlan(page, ACT, {}, 0, "slug-completion-popup", { tracker }),
    (err) => {
      assert.strictEqual(err.forkDecisionNeeded, true);
      assert.strictEqual(err.completionGate, true);
      assert.match(err.message, /Needs a decision: Replace file\?/);
      return true;
    },
  );
  assert.deepStrictEqual(page.pressed, ["Enter"], "the popup was not answered and the action did not run twice");
  assert.ok(!events.some(([c]) => c === "tier_ok"), "not reported ok");
});

test("runPlan: a decision-looking panel that leaves the page usable does not fail a step that finished", async () => {
  const tray = { title: "1 upload complete", text: "", hasFields: false, blocks: false,
    controls: [{ name: "Minimize" }, { name: "Close" }, { name: "Show file location" }] };
  const events = [];
  const page = runPage([CLEAN, DONE_TRAY], { dialog: tray });
  const tracker = { emit: (code, data) => events.push([code, data]) };
  const result = await runPlan(page, ACT, {}, 0, "slug-completion-leftopen", { tracker });
  assert.ok(events.some(([c]) => c === "tier_ok"), "the step is reported ok");
  assert.ok(events.some(([c]) => c === "left_open"), "the panel is recorded, not ignored");
  assert.match(result.warnings.join(" "), /A panel \("1 upload complete".*still open.*does not block the page/);
  assert.strictEqual(result.proof.steps[0].left_open.title, "1 upload complete");
});

test("runPlan: Drive's upload tray after the final step is waited out and the run is done", async () => {
  const events = [];
  const page = runPage([CLEAN, TRAY, TRAY, DONE_TRAY], { dialog: DRIVE_TRAY });
  const tracker = { emit: (code, data) => events.push([code, data]) };
  await runPlan(page, ACT, {}, 0, "slug-completion-drive", { tracker });
  const done = events.find(([c]) => c === "completion");
  assert.deepStrictEqual([done[1].mode, done[1].st], ["full", "done"]);
  assert.ok(done[1].waited >= 500, `returned before the tray cleared: ${done[1].waited}ms`);
});

test("runPlan: a low-risk final step with no sign of starting finishes with a plain note, never as a plain pass", async () => {
  const events = [];
  const page = runPage([CLEAN, CLEAN]);
  const tracker = { emit: (code, data) => events.push([code, data]) };
  const result = await runPlan(page, ACT, {}, 0, "slug-completion-nosign", { tracker });
  assert.deepStrictEqual(result.proof.notes, [`I couldn't confirm that "Upload the file" finished.`]);
  assert.deepStrictEqual([result.proof.steps[0].verdict, result.proof.steps[0].required], ["couldnt_confirm", "completed"]);
  assert.strictEqual(events.find(([c]) => c === "completion")[1].st, "no_sign");
  assert.strictEqual(events.find(([c]) => c === "proof")[1].v, "couldnt_confirm");
});

test("runPlan: a step that cannot be undone and shows no sign of finishing stops the run as 'couldn't confirm'", async () => {
  const events = [];
  const page = runPage([CLEAN, CLEAN]);
  const tracker = { emit: (code, data) => events.push([code, data]) };
  const risky = [{ type: "keyboard_shortcut", value: "Enter", intent: "Send the invoice", destructive: true }];
  await assert.rejects(
    runPlan(page, risky, {}, 0, "slug-completion-risky", { tracker }),
    (err) => {
      assert.strictEqual(err.couldntConfirm, true);
      assert.strictEqual(err.completionGate, true);
      assert.match(err.message, /I couldn't confirm that "Send the invoice" finished/);
      assert.strictEqual(err.proof.steps[0].verdict, "couldnt_confirm");
      return true;
    },
  );
  assert.deepStrictEqual(page.pressed, ["Enter"], "never repeated");
  assert.ok(events.some(([c, d]) => c === "step_fail" && d.fc === "couldnt_confirm"));
  assert.ok(!events.some(([c]) => c === "tier_ok"), "not reported ok");
});

test("runPlan: a run nobody is watching ends on 'couldn't confirm' even for a low-risk step", async () => {
  const page = runPage([CLEAN, CLEAN]);
  const tracker = { emit: () => {} };
  await assert.rejects(runPlan(page, ACT, {}, 0, "slug-completion-sched", { tracker, scheduled: true }), (e) => e.couldntConfirm === true);
});

test("runPlan: an upload that finishes is proven at 'completed'", async () => {
  const page = runPage([CLEAN, TRAY, DONE_TRAY]);
  const tracker = { emit: () => {} };
  const result = await runPlan(page, ACT, {}, 0, "slug-completion-proven", { tracker });
  assert.deepStrictEqual(result.proof.notes, []);
  assert.deepStrictEqual([result.proof.steps[0].verdict, result.proof.steps[0].rung], ["proven", "completed"]);
});

// ─── WF-6: false success ───────────────────────────────────────────────────────────────────

function fakeDialog(type, message) {
  const d = { type: () => type, message: () => message, accepted: 0 };
  d.accept = async () => { d.accepted++; };
  return d;
}
const SAVE = [{ type: "keyboard_shortcut", value: "Enter", intent: "Save the greeting",
  validation: { assertions: [{ type: "text_present", target: "Thanks,", required: true }] } }];

test("runPlan: an alert the final step raises ends the run as a fault quoting it, never a pass", async () => {
  const events = [];
  const dialogQueue = [];
  const alert = fakeDialog("alert", "Session expired. Please try again.");
  const page = runPage([CLEAN, CLEAN]);
  page.keyboard.press = async (k) => { page.pressed.push(k); dialogQueue.push(alert); };
  const tracker = { emit: (code, data) => events.push([code, data]) };
  await assert.rejects(
    runPlan(page, SAVE, {}, 0, "slug-wf6-alert", { tracker, dialogQueue }),
    (err) => {
      assert.strictEqual(err.fault, true);
      assert.strictEqual(err.faultKind, "auth_expired");
      assert.strictEqual(err.completionGate, true);
      assert.match(err.message, /Your sign-in to the app ended: "Session expired\. Please try again\."/);
      return true;
    },
  );
  assert.deepStrictEqual(page.pressed, ["Enter"], "never repeated");
  assert.strictEqual(alert.accepted, 1);
  assert.ok(!events.some(([c]) => c === "tier_ok" || c === "repair_event"), "not reported ok, not recovered");
});

test("runPlan: an alert that opens while the final step's result is awaited still ends the run", async () => {
  const dialogQueue = [];
  const page = runPage([CLEAN, TRAY, TRAY, TRAY]);
  page.keyboard.press = async (k) => { page.pressed.push(k); setTimeout(() => dialogQueue.push(fakeDialog("alert", "Could not save")), 60); };
  await assert.rejects(
    runPlan(page, ACT, {}, 0, "slug-wf6-late-alert", { tracker: { emit: () => {} }, dialogQueue }),
    (err) => err.fault === true && /The app showed a message: "Could not save"/.test(err.message),
  );
  assert.deepStrictEqual(page.pressed, ["Enter"]);
});

test("runPlan: a final step whose checks could not be read (a question left open) is never a plain pass", async () => {
  const dialogQueue = [];
  const confirm = fakeDialog("confirm", "Leave this page?");
  const page = runPage([CLEAN, CLEAN]);
  page.keyboard.press = async (k) => { page.pressed.push(k); dialogQueue.push(confirm); };
  const result = await runPlan(page, SAVE, {}, 0, "slug-wf6-still", { tracker: { emit: () => {} }, dialogQueue });
  assert.strictEqual(result.proof.steps[0].verdict, "couldnt_confirm");
  assert.deepStrictEqual(result.proof.notes, [`I couldn't confirm that "Save the greeting" finished.`]);
  assert.strictEqual(confirm.accepted, 0, "a question is never answered on anyone's behalf");
});

test("runPlan: a step that sent data but showed no result is never done again", async () => {
  const events = [];
  const handlers = {};
  const page = runPage([CLEAN, CLEAN]);
  page.on = (ev, fn) => { (handlers[ev] = handlers[ev] || []).push(fn); };
  const post = { method: () => "POST", url: () => "https://drive.test/api/greet", resourceType: () => "fetch", isNavigationRequest: () => false };
  page.keyboard.press = async (k) => { page.pressed.push(k); for (const fn of handlers.request || []) fn(post); };
  const tracker = { emit: (code, data) => events.push([code, data]) };
  await assert.rejects(
    runPlan(page, SAVE, {}, 0, "slug-wf6-sent", { tracker }),
    (err) => {
      assert.strictEqual(err.recoveryHaltReason, "action-sent-request");
      assert.strictEqual(err.actionMayHaveTakenEffect, true);
      assert.match(err.message, /sent its data to the app.*not tried again/);
      return true;
    },
  );
  assert.deepStrictEqual(page.pressed, ["Enter"], "pressed exactly once");
  assert.ok(events.some(([c, d]) => c === "step_fail" && d.fc === "sent_no_result"));
  assert.ok(!events.some(([c]) => c === "repair_event" || c === "tier_ok"));
});
