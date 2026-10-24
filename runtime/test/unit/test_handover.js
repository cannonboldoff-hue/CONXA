"use strict";

// EXEC-21 (hand-over shape): the human sibling of ai_review's planned pause (test_ai_review.js).
// Two things are under test here, same split as that file:
//   • handover.js — arm()/disarm() (the three signal sources: banner binding, loopback HTTP,
//     file drop), revalidate(), and buildHandoverRequest().
//   • run.js's runPlan interception — a fresh arrival races the armed signal against
//     HANDOVER_INCALL_MS and throws a distinct `handoverPause` signal once it expires; a resume
//     that already bound `__handover_done_<i>` consumes it and continues.
//
// CONXA_HANDOVER_INCALL_MS and CONXA_DATA_DIR are set BEFORE requiring the modules under test —
// both are read once at module load (handover.js's RESUME_DIR, run.js's re-export of the
// constant), so setting them later would silently no-op.
process.env.CONXA_HANDOVER_INCALL_MS = "30";
const os = require("os");
const path = require("path");
const fs = require("fs");
const tmpDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-handover-test-"));
process.env.CONXA_DATA_DIR = tmpDataDir;

const test   = require("node:test");
const assert = require("node:assert");
const http   = require("http");

const handover = require("../../app/handover");
const { runPlan } = require("../../app/run");
const { createTabRegistry } = require("../../app/tabs");

test.after?.(() => {
  fs.rmSync(tmpDataDir, { recursive: true, force: true });
});

// ── mock browser context/page ────────────────────────────────────────────────

function mockContext() {
  const pageListeners = [];
  const bindings = {};
  return {
    exposeBinding: async (name, fn) => { bindings[name] = fn; },
    addInitScript: async () => {},
    pages: () => [],
    on: (evt, cb) => { if (evt === "page") pageListeners.push(cb); },
    off: (evt, cb) => { if (evt === "page") { const i = pageListeners.indexOf(cb); if (i >= 0) pageListeners.splice(i, 1); } },
    _bindings: bindings,
    _pageListeners: pageListeners,
  };
}

function mockPage(overrides = {}) {
  return {
    url: () => "https://x.example/2fa",
    evaluate: async () => {},
    screenshot: async () => Buffer.from("shot"),
    isClosed: () => false,
    waitForLoadState: async () => {},
    context: () => ({ on: () => {} }),
    ...overrides,
  };
}

// ── arm() / disarm() ─────────────────────────────────────────────────────────

test("arm: exposes a binding and opens a loopback HTTP listener", async () => {
  const context = mockContext();
  const armed = await handover.arm(context, mockPage(), { message: "Please log in" }, "run-1");
  assert.ok(typeof context._bindings.__conxaHandoverDone === "function", "binding exposed");
  assert.ok(Number.isInteger(armed.httpPort) && armed.httpPort > 0, "loopback listener bound");
  assert.ok(armed.token && armed.token.length > 0);
  await armed.disarm();
});

test("arm: the exposed binding firing resolves signal with \"banner\"", async () => {
  const context = mockContext();
  const armed = await handover.arm(context, mockPage(), { message: "m" }, "run-2");
  context._bindings.__conxaHandoverDone();
  assert.strictEqual(await armed.signal, "banner");
  await armed.disarm();
});

test("arm: a POST to the loopback listener with the right token resolves signal with \"http\"", async () => {
  const context = mockContext();
  const armed = await handover.arm(context, mockPage(), { message: "m" }, "run-3");
  const req = http.request({ host: "127.0.0.1", port: armed.httpPort, method: "POST", path: `/resume/run-3?token=${armed.token}` });
  req.end();
  assert.strictEqual(await armed.signal, "http");
  await armed.disarm();
});

test("arm: a POST with the wrong token does not resolve signal", async () => {
  const context = mockContext();
  const armed = await handover.arm(context, mockPage(), { message: "m" }, "run-4");
  await new Promise((resolve) => {
    const req = http.request(
      { host: "127.0.0.1", port: armed.httpPort, method: "POST", path: "/resume/run-4?token=wrong" },
      (res) => { res.resume(); res.on("end", resolve); }
    );
    req.end();
  });
  const winner = await Promise.race([armed.signal, new Promise((r) => setTimeout(() => r("timeout"), 200))]);
  assert.strictEqual(winner, "timeout", "wrong token must not resume");
  await armed.disarm();
});

test("arm: dropping the resume file resolves signal with \"file\"", async () => {
  const context = mockContext();
  const armed = await handover.arm(context, mockPage(), { message: "m" }, "run-file");
  fs.writeFileSync(armed.cmdFile, "resume");
  const winner = await Promise.race([armed.signal, new Promise((r) => setTimeout(() => r("timeout"), 3000))]);
  assert.strictEqual(winner, "file");
  await armed.disarm();
});

test("disarm: closes the loopback listener so a later request fails to connect", async () => {
  const context = mockContext();
  const armed = await handover.arm(context, mockPage(), { message: "m" }, "run-5");
  const port = armed.httpPort;
  await armed.disarm();
  await assert.rejects(() => new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, method: "POST", path: "/resume/run-5" }, resolve);
    req.on("error", reject);
    req.end();
  }));
});

// ── revalidate() ──────────────────────────────────────────────────────────────

test("revalidate: returns the resolved page when the recorded tab still exists", async () => {
  const page = mockPage();
  const tabs = createTabRegistry(page);
  const result = await handover.revalidate(tabs, {}, false);
  assert.strictEqual(result, page);
});

test("revalidate: throws handoverRevalidationFailed when the page was closed", async () => {
  const page = mockPage({ isClosed: () => true });
  const tabs = createTabRegistry(page);
  await assert.rejects(
    () => handover.revalidate(tabs, {}, false),
    (err) => err.handoverRevalidationFailed === true
  );
});

// ── buildHandoverRequest ─────────────────────────────────────────────────────

test("buildHandoverRequest: includes the message and a current screenshot", async () => {
  const step = { message: "Please complete the 2FA challenge, then click Done." };
  const resp = await handover.buildHandoverRequest(mockPage(), step, 3, { runId: "r1" });
  const header = resp.content[0].text;
  assert.match(header, /paused at step 4/);
  assert.match(header, /2FA challenge/);
  assert.strictEqual(resp.content.filter((c) => c.type === "image").length, 1);
  assert.strictEqual(resp._meta["conxa/handover"].step_index, 3);
  assert.strictEqual(resp._meta["conxa/handover"].run_id, "r1");
});

// ── run.js's runPlan interception ────────────────────────────────────────────
// Same idlePage/idleContext fixture shape as test_ai_review.js: a handover step has no `tab`
// block, so resolveStepPage takes the tab_0 shortcut and never touches real Playwright.
const idleContext = {
  on: () => {}, off: () => {},
  exposeBinding: async () => {}, addInitScript: async () => {}, pages: () => [],
};
const idlePage = {
  waitForLoadState: async () => {}, context: () => idleContext,
  evaluate: async () => {}, screenshot: async () => Buffer.from("s"),
  isClosed: () => false, url: () => "https://x.example",
};

test("runPlan: a fresh handover step throws handoverPause once the in-call wait expires", async () => {
  const steps = [{ type: "handover", message: "Please log in" }];
  let caught;
  await assert.rejects(
    () => runPlan(idlePage, steps, {}, 0, "handover-fresh", { context: idleContext }),
    (err) => {
      caught = err;
      assert.strictEqual(err.handoverPause, true);
      assert.strictEqual(err.stepIndex, 0);
      assert.strictEqual(err.step, steps[0]);
      assert.ok(err.armed, "the still-armed signal sources are handed to the caller to park");
      return true;
    }
  );
  await caught.armed.disarm(); // this test never parks — release what arm() opened
});

test("runPlan: no browser context threaded through fails loud instead of skipping the gate", async () => {
  const steps = [{ type: "handover", message: "Please log in" }];
  await assert.rejects(() => runPlan(idlePage, steps, {}, 0, "handover-no-ctx", {}));
});

test("runPlan: a resume with __handover_done_<i> bound consumes it and continues past the step", async () => {
  const steps = [
    { type: "handover", message: "Please log in" },
    { type: "handover", message: "Never reached" },
  ];
  const inputs = { __handover_done_0: true };
  // Step 1 has no bound marker, so it re-throws its own pause once the in-call wait expires —
  // proves step 0 was actually consumed and execution moved on, not stuck re-pausing on step 0.
  let caught;
  await assert.rejects(
    () => runPlan(idlePage, steps, inputs, 0, "handover-resume", { context: idleContext }),
    (err) => { caught = err; return err.handoverPause === true && err.stepIndex === 1; }
  );
  assert.strictEqual(inputs.__handover_done_0, undefined, "consumed marker is removed");
  await caught.armed.disarm();
});

// on_missing: "ask" -- a record that is provably not on the page pauses like a hand-over (same park,
// same resume), and a resume that already bound `__handover_done_<i>` tries the step again.
function absentRecordPage() {
  const row = { count: async () => 0, first: () => ({}) };
  return mockPage({
    waitForTimeout: async () => {},
    locator: () => ({ count: async () => 2, filter: () => row, all: async () => [] }),
  });
}
const BOUND_CLICK = {
  type: "click", intent: "open_invoice", on_missing: "ask",
  identity_bundle: { signals: [{ engine: "testid", selector: 'internal:testid=[data-testid="open"]', durability: 0.99 }], fingerprint: { data_testid: "open" }, frame_chain: [] },
  entity_binding: { container_selector: "table tr", identifier: "Invoice #9", source: "literal", confirmed: true },
};

test("on_missing ask: a provably absent record pauses as a hand-over instead of failing", async () => {
  const context = mockContext();
  await assert.rejects(
    () => runPlan(absentRecordPage(), [BOUND_CLICK], {}, 0, "ask-missing", { context }),
    (err) => {
      assert.strictEqual(err.handoverPause, true);
      assert.strictEqual(err.stepIndex, 0);
      assert.match(err.step.message, /isn't on the page/);
      err.armed.disarm();
      return true;
    },
  );
});

test("on_missing ask: a resume that says the person dealt with it tries the step again (and asks again if still absent)", async () => {
  const context = mockContext();
  await assert.rejects(
    () => runPlan(absentRecordPage(), [BOUND_CLICK], { __handover_done_0: true }, 0, "ask-missing-2", { context, isResume: true }),
    (err) => { err.armed && err.armed.disarm(); return err.handoverPause === true; },
  );
});

test("on_missing ask without a browser context still fails closed", async () => {
  await assert.rejects(
    () => runPlan(absentRecordPage(), [BOUND_CLICK], {}, 0, "ask-missing-3", {}),
    (err) => err.handoverPause !== true && /Bound record could not be uniquely located/.test(String(err.message)),
  );
});
