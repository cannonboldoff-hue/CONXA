"use strict";

// Unit tests for the conditional/branch primitives (EXEC-1): if_present, try_dismiss,
// wait_for_one_of. Disable GATE so mocks don't need to simulate stability checks —
// mirrors integration_agent_recovery.js's env-var-before-require setup.
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");

const { executeStep, probePresent } = require("../../app/run");

// Minimal mock page: `present` maps selector -> boolean (drives .count()); `clickThrows` maps
// selector -> an error message thrown by that selector's first().click(). No browser needed —
// branch handlers only ever call .locator(sel).count()/.first().click() and page.keyboard.press.
function mockPage({ present = {}, clickThrows = {} } = {}) {
  const clicks = [];
  const escapes = [];
  return {
    locator: (sel) => ({
      count: async () => (present[sel] ? 1 : 0),
      first: () => ({
        click: async () => {
          if (clickThrows[sel]) throw new Error(clickThrows[sel]);
          clicks.push(sel);
        },
      }),
    }),
    keyboard: { press: async (key) => { escapes.push(key); } },
    _clicks: clicks,
    _escapes: escapes,
  };
}

// A page whose one selector of interest only reports present after `revealAfterCalls` polls —
// proves wait_for_one_of polls its options instead of sampling once (same pattern as
// test_verify.js's mockRevealAfterCallsPage).
function mockRevealPage(selector, revealAfterCalls) {
  let calls = 0;
  const clicks = [];
  return {
    locator: (sel) => ({
      count: async () => {
        if (sel !== selector) return 0;
        calls++;
        return calls > revealAfterCalls ? 1 : 0;
      },
      first: () => ({ click: async () => { clicks.push(sel); } }),
    }),
    keyboard: { press: async () => {} },
    _clicks: clicks,
  };
}

// ─── probePresent ──────────────────────────────────────────────────────────────

test("probePresent returns true when the selector resolves to at least one element", async () => {
  const page = mockPage({ present: { "#a": true } });
  assert.strictEqual(await probePresent(page, { selector: "#a" }, {}, 10), true);
});

test("probePresent returns false when the selector never resolves", async () => {
  const page = mockPage({ present: {} });
  assert.strictEqual(await probePresent(page, { selector: "#a" }, {}, 10), false);
});

test("probePresent returns false when there is no selector or identity_bundle at all", async () => {
  const page = mockPage({});
  assert.strictEqual(await probePresent(page, {}, {}, 10), false);
});

// ─── if_present ─────────────────────────────────────────────────────────────────

test("if_present runs the branch body when the probe selector is present", async () => {
  const page = mockPage({ present: { ".cookie-banner": true } });
  const step = {
    type: "if_present", selector: ".cookie-banner", timeout_ms: 10,
    steps: [{ type: "click", selector: "#accept-cookies" }],
  };
  await executeStep(page, step, {});
  assert.deepStrictEqual(page._clicks, ["#accept-cookies"]);
});

test("if_present skips the branch body when the probe selector is absent", async () => {
  const page = mockPage({ present: {} });
  const step = {
    type: "if_present", selector: ".cookie-banner", timeout_ms: 10,
    steps: [{ type: "click", selector: "#accept-cookies" }],
  };
  await executeStep(page, step, {});
  assert.deepStrictEqual(page._clicks, []);
});

test("if_present never throws even when the nested step fails", async () => {
  const page = mockPage({
    present: { ".cookie-banner": true },
    clickThrows: { "#accept-cookies": "boom" },
  });
  const step = {
    type: "if_present", selector: ".cookie-banner", timeout_ms: 10,
    steps: [{ type: "click", selector: "#accept-cookies" }],
  };
  await assert.doesNotReject(executeStep(page, step, {}));
});

// ─── try_dismiss ────────────────────────────────────────────────────────────────

test("try_dismiss clicks the first present candidate and stops", async () => {
  const page = mockPage({ present: { "#accept-cookies": false, ".cookie .accept": true } });
  const step = {
    type: "try_dismiss", timeout_ms: 10,
    candidates: ["#accept-cookies", ".cookie .accept", "[aria-label='Close']"],
  };
  await executeStep(page, step, {});
  assert.deepStrictEqual(page._clicks, [".cookie .accept"]);
  assert.deepStrictEqual(page._escapes, []);
});

test("try_dismiss swallows total absence and falls back to Escape", async () => {
  const page = mockPage({ present: {} });
  const step = { type: "try_dismiss", timeout_ms: 10, candidates: ["#accept-cookies"] };
  await assert.doesNotReject(executeStep(page, step, {}));
  assert.deepStrictEqual(page._escapes, ["Escape"]);
});

test("try_dismiss honors fallback_escape:false", async () => {
  const page = mockPage({ present: {} });
  const step = {
    type: "try_dismiss", timeout_ms: 10, candidates: ["#accept-cookies"], fallback_escape: false,
  };
  await executeStep(page, step, {});
  assert.deepStrictEqual(page._escapes, []);
});

test("try_dismiss tries the next candidate if clicking a present one throws", async () => {
  const page = mockPage({ present: { "#a": true, "#b": true }, clickThrows: { "#a": "boom" } });
  const step = { type: "try_dismiss", timeout_ms: 10, candidates: ["#a", "#b"] };
  await executeStep(page, step, {});
  assert.deepStrictEqual(page._clicks, ["#b"]);
});

// ─── wait_for_one_of ────────────────────────────────────────────────────────────

test("wait_for_one_of polls until an option appears, then runs its steps", async () => {
  const page = mockRevealPage("#mfa-code", 2);
  const step = {
    type: "wait_for_one_of", timeout_ms: 2000,
    options: [
      { selector: "#mfa-code", steps: [{ type: "click", selector: "#mfa-submit" }] },
      { selector: "#dashboard" },
    ],
  };
  await executeStep(page, step, {});
  assert.deepStrictEqual(page._clicks, ["#mfa-submit"]);
});

test("wait_for_one_of throws when required and no option appears before timeout", async () => {
  const page = mockPage({ present: {} });
  const step = {
    type: "wait_for_one_of", timeout_ms: 50, required: true, options: [{ selector: "#never" }],
  };
  await assert.rejects(executeStep(page, step, {}), /none of the candidate selectors appeared/);
});

test("wait_for_one_of no-ops when not required and no option appears before timeout", async () => {
  const page = mockPage({ present: {} });
  const step = { type: "wait_for_one_of", timeout_ms: 50, options: [{ selector: "#never" }] };
  await assert.doesNotReject(executeStep(page, step, {}));
});

// ─── variant branch (flow: true) ───────────────────────────────────────────────
// "Record another way from here": each option is a whole way the rest of the workflow can go. The
// chosen body runs as real steps, so a failure inside it fails the run (a cookie-banner branch
// would have swallowed it).

const { runPlan } = require("../../app/run");
const { HANDLERS } = require("../../app/handlers");

function flowPage(present) {
  return {
    ...mockPage({ present }),
    waitForLoadState: async () => {},
    context: () => ({ on: () => {} }),
    url: () => "https://x.test",
  };
}

function flowBranch(extra = {}) {
  return {
    type: "wait_for_one_of", flow: true, timeout_ms: 50,
    options: [
      { selector: "#path-a", steps: [{ type: "ran", intent: "a1" }, { type: "ran", intent: "a2" }] },
      { selector: "#path-b", steps: [{ type: "ran", intent: "b1" }] },
    ],
    ...extra,
  };
}

test("variant branch: runs the body of the option whose probe is on the page, and only that one", async () => {
  const ran = [];
  HANDLERS.ran = async (_p, step) => { ran.push(step.intent); };
  try {
    await runPlan(flowPage({ "#path-b": true }), [flowBranch()], {}, 0, "vb-b");
    assert.deepStrictEqual(ran, ["b1"]);
    ran.length = 0;
    await runPlan(flowPage({ "#path-a": true, "#path-b": true }), [flowBranch()], {}, 0, "vb-ab");
    assert.deepStrictEqual(ran, ["a1", "a2"]);                       // the first matching option wins
  } finally {
    delete HANDLERS.ran;
  }
});

test("variant branch: nothing on the page matching any way fails the run instead of passing silently", async () => {
  await assert.rejects(
    () => runPlan(flowPage({}), [flowBranch()], {}, 0, "vb-none"),
    (err) => /None of the known ways/.test(err.message),
  );
});

test("variant branch: a failing step inside the chosen body fails the run (it is not swallowed)", async () => {
  HANDLERS.boom = async () => { throw Object.assign(new Error("boom"), { badInput: true }); };
  try {
    const branch = flowBranch({ options: [{ selector: "#path-a", steps: [{ type: "boom", intent: "x" }] }] });
    await assert.rejects(() => runPlan(flowPage({ "#path-a": true }), [branch], {}, 0, "vb-fail"), /boom/);
  } finally {
    delete HANDLERS.boom;
  }
});

test("a branch without flow: true still swallows body errors (cookie banners must never fail a run)", async () => {
  HANDLERS.boom = async () => { throw new Error("boom"); };
  try {
    const banner = { type: "wait_for_one_of", timeout_ms: 50, options: [{ selector: "#path-a", steps: [{ type: "boom", intent: "x" }] }] };
    await runPlan(flowPage({ "#path-a": true }), [banner], {}, 0, "vb-banner");
  } finally {
    delete HANDLERS.boom;
  }
});
