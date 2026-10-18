"use strict";

// RT-9 — Studio-only test guard. `guard: { pause: true }` stops a run, parked on the live page,
// BEFORE the first step the compiler marked `destructive`; `released: true` lets every destructive
// step through. It is inert without a `guard` (every customer and MCP run), and a dry run (which
// already withholds destructive steps) never pauses.

const test = require("node:test");
const assert = require("node:assert");

const { runPlan } = require("../../app/run");

const idleContext = { on: () => {} };

function mockPage() {
  const clicks = [];
  const item = {
    evaluate: async () => ({ testid: "submit", role: "button", name: "Submit", text: "Submit" }),
    click: async () => { clicks.push("click"); },
  };
  const locator = (sel) => ({
    all: async () => (sel === '[data-testid="submit"]' ? [item] : []),
    first: () => item,
    click: async () => { clicks.push("click"); },
  });
  return {
    clicks,
    waitForLoadState: async () => {},
    context: () => idleContext,
    url: () => "https://x.test",
    locator,
  };
}

const bundle = {
  signals: [{ engine: "testid", selector: 'internal:testid=[data-testid="submit"]', durability: 0.99, orthogonality_class: "test-contract" }],
  fingerprint: { data_testid: "submit", role: "button", aria_label: "Submit" },
  stable_hash: "",
  frame_chain: [],
};
const DESTRUCTIVE = { type: "click", intent: "send_invoice", destructive: true, identity_bundle: bundle };
const NOOP = { type: "frame_enter", intent: "enter_iframe" };

test("guard: the run stops before the first destructive step, with nothing sent", async () => {
  const page = mockPage();
  await assert.rejects(
    () => runPlan(page, [NOOP, DESTRUCTIVE, DESTRUCTIVE], {}, 0, "guard-pause", { guard: { pause: true } }),
    (err) => {
      assert.strictEqual(err.guardPause, true);
      assert.strictEqual(err.stepIndex, 1);
      assert.strictEqual(err.remaining, 2); // this step and the one after it
      assert.strictEqual(err.step.intent, "send_invoice");
      assert.strictEqual(err.page, page);
      return true;
    },
  );
  assert.deepStrictEqual(page.clicks, [], "no click may happen before the guard releases");
});

test("guard: it is a planned pause, not a failure (no failedAt, so no recovery or retry budget)", async () => {
  const page = mockPage();
  try {
    await runPlan(page, [DESTRUCTIVE], {}, 0, "guard-not-failure", { guard: { pause: true } });
    assert.fail("expected a pause");
  } catch (err) {
    assert.strictEqual(err.failedAt, undefined);
    assert.strictEqual(err.cancelled, undefined);
  }
});

test("guard: once released, the destructive step is attempted for real", async () => {
  const page = mockPage();
  let paused = false;
  try {
    await runPlan(page, [DESTRUCTIVE], {}, 0, "guard-released", { guard: { pause: true, released: true }, isResume: true });
  } catch (err) {
    paused = Boolean(err.guardPause);
  }
  assert.strictEqual(paused, false, "a released guard never pauses again");
});

test("guard: without a guard nothing pauses (every customer and MCP run)", async () => {
  const page = mockPage();
  let paused = false;
  try {
    await runPlan(page, [DESTRUCTIVE], {}, 0, "no-guard", {});
  } catch (err) {
    paused = Boolean(err.guardPause);
  }
  assert.strictEqual(paused, false);
  let paused2 = false;
  try {
    await runPlan(page, [DESTRUCTIVE], {}, 0, "guard-off", { guard: { pause: false } });
  } catch (err) {
    paused2 = Boolean(err.guardPause);
  }
  assert.strictEqual(paused2, false);
});

test("guard: a dry run withholds destructive steps itself, so it never pauses", async () => {
  const page = mockPage();
  const result = await runPlan(page, [DESTRUCTIVE], {}, 0, "guard-dry", { guard: { pause: true }, dryRun: true });
  assert.deepStrictEqual(result.dryRunSkipped, [{ index: 0, intent: "send_invoice" }]);
});

test("guard: steps that can be undone never trigger it", async () => {
  const page = mockPage();
  const result = await runPlan(page, [NOOP, NOOP], {}, 0, "guard-safe", { guard: { pause: true } });
  assert.deepStrictEqual(result.dryRunSkipped, []);
});

test("guard: resuming from the paused step with a released guard skips the steps before it", async () => {
  const page = mockPage();
  const order = [];
  let paused = false;
  try {
    await runPlan(page, [NOOP, DESTRUCTIVE], {}, 1, "guard-resume", {
      guard: { pause: true, released: true }, isResume: true, onStep: (i) => order.push(i),
    });
  } catch (err) {
    paused = Boolean(err.guardPause);
  }
  assert.deepStrictEqual(order, [1]);
  assert.strictEqual(paused, false);
});
