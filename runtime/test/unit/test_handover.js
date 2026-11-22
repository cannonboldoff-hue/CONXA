"use strict";

// EXEC-21 (hand-over shape): the human sibling of the agent_span planned pause (test_agent_span.js).
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
  const initScripts = [];
  return {
    // Like Playwright: a name already exposed on the context is refused.
    exposeBinding: async (name, fn) => { if (bindings[name]) throw new Error(`Function "${name}" has been already registered`); bindings[name] = fn; },
    addInitScript: async (s) => { initScripts.push(s); },
    pages: () => [],
    on: (evt, cb) => { if (evt === "page") pageListeners.push(cb); },
    off: (evt, cb) => { if (evt === "page") { const i = pageListeners.indexOf(cb); if (i >= 0) pageListeners.splice(i, 1); } },
    _bindings: bindings,
    _initScripts: initScripts,
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

// The banner's binding has a name that is unique per arm; tests find it by prefix.
const bindingsOf = (context) => Object.keys(context._bindings).filter((k) => k.startsWith("__conxaHandoverDone_"));
const bindingOf = (context, n = 0) => context._bindings[bindingsOf(context)[n]];

// ── arm() / disarm() ─────────────────────────────────────────────────────────

test("arm: exposes a binding and opens a loopback HTTP listener", async () => {
  const context = mockContext();
  const armed = await handover.arm(context, mockPage(), { message: "Please log in" }, "run-1");
  assert.ok(typeof bindingOf(context) === "function", "binding exposed");
  assert.ok(Number.isInteger(armed.httpPort) && armed.httpPort > 0, "loopback listener bound");
  assert.ok(armed.token && armed.token.length > 0);
  await armed.disarm();
});

test("arm: the exposed binding firing from the run's page resolves signal with \"banner\"", async () => {
  const context = mockContext();
  const page = mockPage();
  const armed = await handover.arm(context, page, { message: "m" }, "run-2");
  await bindingOf(context)({ page }, "done");
  assert.strictEqual(await armed.signal, "banner");
  await armed.disarm();
});

test("arm: two hand-overs on one context each fire their own signal from the banner", async () => {
  const context = mockContext();
  const page = mockPage();
  const first = await handover.arm(context, page, { message: "one" }, "run-a");
  await bindingOf(context, 0)({ page }, "done");
  assert.strictEqual(await first.signal, "banner");
  await first.disarm();
  const second = await handover.arm(context, page, { message: "two" }, "run-b");
  assert.strictEqual(bindingsOf(context).length, 2, "a new name per arm, no clash with the first");
  assert.strictEqual(await bindingOf(context, 1)({ page }, "ping"), true, "the second banner is live");
  await bindingOf(context, 1)({ page }, "done");
  assert.strictEqual(await second.signal, "banner", "the second signal is its own, not the first's finished one");
  await second.disarm();
});

test("banner ping: false after disarm and false for a page that is not the run's", async () => {
  const context = mockContext();
  const page = mockPage();
  const other = mockPage();
  const armed = await handover.arm(context, page, { message: "m" }, "run-ping", { tabs: { pages: new Map([["tab_0", page]]) } });
  const binding = bindingOf(context);
  assert.strictEqual(await binding({ page }, "ping"), true);
  assert.strictEqual(await binding({ page: other }, "ping"), false, "someone else's page (Studio, another run)");
  await binding({ page: other }, "done");
  const early = await Promise.race([armed.signal, new Promise((r) => setTimeout(() => r("none"), 50))]);
  assert.strictEqual(early, "none", "a click from a page that isn't the run's does nothing");
  const popup = mockPage({ opener: async () => page });
  assert.strictEqual(await binding({ page: popup }, "ping"), true, "a window the run's page opened counts");
  await armed.disarm();
  assert.strictEqual(await binding({ page }, "ping"), false, "a stale banner draws nothing");
});

test("banner script: with no <html> yet it draws on DOMContentLoaded", async () => {
  const vm = require("vm");
  const listeners = {};
  const appended = [];
  const doc = {
    documentElement: null,
    getElementById: () => null,
    addEventListener: (evt, cb) => { listeners[evt] = cb; },
    createElement: () => ({ style: {}, appendChild() {}, addEventListener() {} }),
  };
  const win = { __b: async () => true };
  const script = handover._bannerScript("hello", "__b");
  const run = vm.runInNewContext(script, { document: doc, window: win });
  await run;
  assert.ok(listeners.DOMContentLoaded, "waits for the document instead of giving up");
  doc.documentElement = { appendChild: (el) => appended.push(el) };
  await listeners.DOMContentLoaded();
  assert.strictEqual(appended.length, 1, "banner drawn once the document exists");
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
// Same idlePage/idleContext fixture shape as test_agent_span.js: a handover step has no `tab`
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
    locator: () => ({ count: async () => 2, filter: () => row, all: async () => [], evaluateAll: async () => [] }),
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

// ── the host panel's input shield comes off for the pause and goes back on ───────────────────────────────

test("arm/disarm: a host-owned run lifts the panel's input shield, then puts it back", async () => {
  const hostBrowser = require("../../app/host_browser");
  const real = hostBrowser.setInputOpen;
  const calls = [];
  hostBrowser.setInputOpen = async (a) => { calls.push(a); };
  try {
    const armed = await handover.arm(mockContext(), mockPage(), { message: "m" }, "run-call-id", { hostOwned: true, hostRunId: "host-run-id" });
    assert.deepStrictEqual(calls, [{ runId: "host-run-id", open: true }], "lifted, under the host's run id (not the call's)");
    await armed.disarm();
    assert.deepStrictEqual(calls[1], { runId: "host-run-id", open: false }, "back on when the person is done");
  } finally { hostBrowser.setInputOpen = real; }
});

test("arm: a run that is not host-owned never talks to a panel", async () => {
  const hostBrowser = require("../../app/host_browser");
  const real = hostBrowser.setInputOpen;
  let called = 0;
  hostBrowser.setInputOpen = async () => { called++; };
  try {
    const a = await handover.arm(mockContext(), mockPage(), { message: "m" }, "r1", { hostOwned: false, hostRunId: "r1" });
    await a.disarm();
    const b = await handover.arm(mockContext(), mockPage(), { message: "m" }, "r2");
    await b.disarm();
    assert.strictEqual(called, 0);
  } finally { hostBrowser.setInputOpen = real; }
});

test("host_browser.setInputOpen: posts the lift and never throws when the panel is gone", async () => {
  const hostBrowser = require("../../app/host_browser");
  const saved = process.env.CONXA_HOST_CONTROL_URL;
  delete process.env.CONXA_HOST_CONTROL_URL; // _controlPost throws: no control channel
  try { await hostBrowser.setInputOpen({ runId: "x", open: true }); } finally { if (saved !== undefined) process.env.CONXA_HOST_CONTROL_URL = saved; }
});

// ── one shared wait: resume checks, plain stops, never parked where it can't be, Stop closes it ───────────

// Captures what arm() opened so a test can prove the loopback port was closed when the run ended.
function spyArm() {
  const real = handover.arm;
  const armedList = [];
  handover.arm = async (...a) => { const r = await real(...a); armedList.push(r); return r; };
  return { armedList, restore: () => { handover.arm = real; } };
}
const portIsClosed = (port) => new Promise((resolve) => {
  const req = http.request({ host: "127.0.0.1", port, method: "POST", path: "/x" }, () => resolve(false));
  req.on("error", () => resolve(true));
  req.end();
});

test("runPlan: a resume whose resume_when is not met fails at the hand-over step and the next step never runs", async () => {
  const steps = [
    { type: "handover", message: "Please log in", resume_when: { identity_bundle: { signals: [{ engine: "css", selector: "#never" }] } }, resume_when_timeout_ms: 10 },
    { type: "handover", message: "Never reached" },
  ];
  let reachedSecond = false;
  const context = { ...idleContext, exposeBinding: async () => { reachedSecond = true; } };
  await assert.rejects(
    () => runPlan(idlePage, steps, { __handover_done_0: true }, 0, "handover-resume-unmet", { context }),
    (err) => err.handoverFailed === true && err.failedAt === 0 && !err.handoverPause && /still didn't show/.test(err.message),
  );
  assert.strictEqual(reachedSecond, false, "the next step never armed anything");
});

test("runPlan: a resume whose recorded tab is closed fails at the hand-over step in plain words", async () => {
  const closed = { ...idlePage, isClosed: () => true };
  await assert.rejects(
    () => runPlan(closed, [{ type: "handover", message: "m" }, { type: "handover", message: "n" }], { __handover_done_0: true }, 0, "handover-resume-closed", { context: idleContext }),
    (err) => err.handoverFailed === true && !/Playwright|Call log/.test(err.message) && /closed during the hand-over/.test(err.message),
  );
});

test("revalidate: a failure carries handoverFailed and a plain message", async () => {
  const tabs = createTabRegistry(mockPage({ isClosed: () => true }));
  await assert.rejects(
    () => handover.revalidate(tabs, {}, false),
    (err) => err.handoverFailed === true && /^The page the person was working on was closed/.test(err.message),
  );
});

test("runPlan: a hand-over in a for_each body that outlasts the wait stops the run (no park) and closes the port", async () => {
  const spy = spyArm();
  const steps = [{ type: "for_each", items: "names", as: "row", max_iterations: 3, on_row_error: "continue", steps: [{ type: "handover", message: "Please sign" }] }];
  try {
    await assert.rejects(
      () => runPlan(idlePage, steps, { names: "a,b" }, 0, "handover-loop", { context: idleContext }),
      (err) => err.handoverTimedOut === true && err.handoverFailed === true && !err.handoverPause && /repeated or conditional/.test(err.message),
    );
  } finally { spy.restore(); }
  assert.strictEqual(spy.armedList.length, 1, "one window only: no second row, no restart");
  assert.strictEqual(await portIsClosed(spy.armedList[0].httpPort), true);
});

test("runPlan: on_missing ask in a loop body that outlasts the wait stops the run and closes the port", async () => {
  const spy = spyArm();
  const steps = [{ type: "for_each", items: "names", as: "row", max_iterations: 3, steps: [BOUND_CLICK] }];
  try {
    await assert.rejects(
      () => runPlan(absentRecordPage(), steps, { names: "a" }, 0, "ask-loop", { context: mockContext() }),
      (err) => err.handoverTimedOut === true && !err.handoverPause,
    );
  } finally { spy.restore(); }
  assert.strictEqual(spy.armedList.length, 1);
  assert.strictEqual(await portIsClosed(spy.armedList[0].httpPort), true);
});

test("runPlan: with CONXA_HANDOVER_NO_PARK=1 a top-level hand-over stops instead of parking", async () => {
  process.env.CONXA_HANDOVER_NO_PARK = "1";
  const spy = spyArm();
  try {
    await assert.rejects(
      () => runPlan(idlePage, [{ type: "handover", message: "Please log in" }], {}, 0, "handover-nopark", { context: idleContext }),
      (err) => err.handoverTimedOut === true && !err.handoverPause && /Nobody pressed Done within/.test(err.message) && !/repeated or conditional/.test(err.message),
    );
  } finally { delete process.env.CONXA_HANDOVER_NO_PARK; spy.restore(); }
  assert.strictEqual(await portIsClosed(spy.armedList[0].httpPort), true);
});

test("runPlan: Stop during the wait ends the run as cancelled and closes the listeners", async () => {
  const realMs = handover.HANDOVER_INCALL_MS;
  handover.HANDOVER_INCALL_MS = 5000;
  const spy = spyArm();
  let stop = false;
  setTimeout(() => { stop = true; }, 150);
  try {
    await assert.rejects(
      () => runPlan(idlePage, [{ type: "handover", message: "Please log in" }], {}, 0, "handover-cancel", { context: idleContext, cancelCheck: () => stop }),
      (err) => err.cancelled === true,
    );
    await new Promise((r) => setTimeout(r, 400)); // the abandoned wait notices Stop and tidies up
  } finally { handover.HANDOVER_INCALL_MS = realMs; spy.restore(); }
  assert.strictEqual(await portIsClosed(spy.armedList[0].httpPort), true, "nothing left listening after Stop");
});

test("buildHandoverRequest: the pause text names the run id", async () => {
  const resp = await handover.buildHandoverRequest(mockPage(), { message: "m" }, 0, { runId: "run-xyz" });
  assert.match(resp.content[0].text, /get_execution_status with run_id: run-xyz/);
});
