"use strict";

// PROD-3-DRYRUN — `dry_run` runs every step except the final committing (destructive) action.
// That step still RESOLVES (proving its target is findable) but is never dispatched or verified.

const test = require("node:test");
const assert = require("node:assert");

const { runPlan } = require("../../app/run");

// Same minimal tab-registry fixture test_agent_span.js uses for a step with no `.tab` block.
const idleContext = { on: () => {} };

// A page that resolves exactly one testid-signal target — mirrors test_resolver.js's known-good
// single-match case (testid "submit" descriptor against a matching fingerprint), routed through
// the real gatherCandidates/resolveSignals path instead of calling resolve() directly.
function mockResolvablePage({ findsTarget = true } = {}) {
  const clicks = [];
  const item = {
    evaluate: async () => ({ testid: "submit", role: "button", name: "Submit", text: "Submit" }),
  };
  return {
    clicks,
    waitForLoadState: async () => {},
    context: () => idleContext,
    url: () => "https://x.test",
    locator: (sel) => ({
      all: async () => (findsTarget && sel === '[data-testid="submit"]' ? [item] : []),
    }),
  };
}

const DESTRUCTIVE_STEP = {
  type: "click",
  intent: "delete_invoice",
  destructive: true,
  identity_bundle: {
    signals: [{ engine: "testid", selector: 'internal:testid=[data-testid="submit"]', durability: 0.99, orthogonality_class: "test-contract" }],
    fingerprint: { data_testid: "submit", role: "button", aria_label: "Submit" },
    stable_hash: "",
    frame_chain: [],
  },
};

test("dry_run: a destructive step resolves but is never dispatched, and is reported skipped", async () => {
  const page = mockResolvablePage();
  const result = await runPlan(page, [DESTRUCTIVE_STEP], {}, 0, "dry-run-skip", { dryRun: true });
  assert.deepStrictEqual(result.dryRunSkipped, [{ index: 0, intent: "delete_invoice" }]);
  assert.strictEqual(result.recoveredSteps, 0);
});

test("dry_run: a destructive step whose target can't be resolved still fails closed", async () => {
  const page = mockResolvablePage({ findsTarget: false });
  await assert.rejects(
    () => runPlan(page, [DESTRUCTIVE_STEP], {}, 0, "dry-run-miss", { dryRun: true }),
    (err) => {
      assert.strictEqual(err.failedAt, 0);
      return true;
    }
  );
});

test("dry_run: a non-destructive (no-op) step is not intercepted by the dry-run branch", async () => {
  const page = mockResolvablePage();
  const step = { type: "frame_enter", intent: "enter_iframe" };
  const result = await runPlan(page, [step], {}, 0, "dry-run-nonop", { dryRun: true });
  assert.deepStrictEqual(result.dryRunSkipped, []);
});

test("without dry_run, the destructive-skip branch never triggers (dryRunSkipped stays empty for a no-op step)", async () => {
  const page = mockResolvablePage();
  const step = { type: "frame_enter", intent: "enter_iframe" };
  const result = await runPlan(page, [step], {}, 0, "no-dry-run");
  assert.deepStrictEqual(result.dryRunSkipped, []);
});
