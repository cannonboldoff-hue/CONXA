"use strict";
// PROD-3 (EXEC-24 P5) — the line for a destructive step is RE-IDENTIFICATION, not tier.
//
// A `step.destructive === true` step may retry the SAME element under different timing or a
// narrower scope, but must never be re-resolved to a different one. So it runs Layer 1 AND the
// same-element half of Layer 2 (transient retry, re-hover, dialog scope — all of which re-try
// `primarySelector` itself), while `recoverWithA11y` — the one stage that re-probes by accessible
// name and can therefore land on a different node — stays blocked.
//
// This replaced a blanket halt before all of Layer 2. That halt was correct when Layer 2 still
// contained a fallback-selector walk and a fuzzy text match (both picked a different element by
// text affinity and clicked `.first()`); once those were deleted, blocking the remaining
// same-selector stages while still allowing L1's identical same-selector retry was an accident of
// where the gate sat, not a safety property.

const test = require("node:test");
const assert = require("node:assert");

const { recoverStep } = require("../../app/cascade");

function fakeTracker() {
  const events = [];
  return { events, emit(name, fields) { events.push({ name, fields }); } };
}

const has = (tracker, name, why) =>
  tracker.events.some(e => e.name === name && (why === undefined || e.fields.why === why));

// The cascade calls `page.waitForTimeout(250)` on its way into the transient stage and nothing
// before it touches `page` outside a try/catch — so a page that ONLY implements waitForTimeout
// both lets the cascade proceed and records that the same-element half of L2 was actually
// reached. Every dispatching stage below throws against it and is swallowed by
// recoverWithSelector's own catch, which is exactly the "stage ran and did not heal" path.
function fakePage() {
  const calls = { waitForTimeout: 0 };
  return { calls, waitForTimeout: async () => { calls.waitForTimeout++; } };
}

// primaryErr.verifyFail makes layer1Ladder's remedy resolve to "descend-layer2", which returns
// false immediately WITHOUT touching `page` — so Layer 1 never healing is guaranteed, and every
// assertion below is about what happens after it.
const VERIFY_FAIL_ERR = { verifyFail: true };

test("destructive step SKIPS the a11y re-probe — the one stage that re-identifies", async () => {
  const page = fakePage();
  const step = { destructive: true, type: "click", handler_hints: {} };
  const tracker = fakeTracker();

  const result = await recoverStep(page, step, {}, "test-slug", 0, "sel", tracker, VERIFY_FAIL_ERR, null, null, null);

  assert.strictEqual(result, false);
  assert.ok(has(tracker, "rec_skip", "destructive"),
    "expected rec_skip/destructive — emitted only in the branch that skips recoverWithA11y");
});

test("destructive step still REACHES the same-element half of Layer 2", async () => {
  const page = fakePage();
  const step = { destructive: true, type: "click", handler_hints: {} };
  const tracker = fakeTracker();

  await recoverStep(page, step, {}, "test-slug", 0, "sel", tracker, VERIFY_FAIL_ERR, null, null, null);

  assert.strictEqual(page.calls.waitForTimeout, 1,
    "the transient stage's 250ms wait must run — proves the cascade no longer halts before L2");
});

test("a destructive step can now actually HEAL at Layer 2 without re-identifying", async () => {
  // `tab_open` has a genuine no-op handler (handlers.js), so executeStep succeeds without a real
  // browser — the cheapest way to make the transient stage's re-dispatch of the SAME selector
  // succeed. The step type is irrelevant to the gating logic under test.
  const page = fakePage();
  const step = { destructive: true, type: "tab_open", handler_hints: {} };
  const tracker = fakeTracker();

  const result = await recoverStep(page, step, {}, "test-slug", 0, "sel", tracker, VERIFY_FAIL_ERR, null, null, null);

  assert.deepStrictEqual(result, { tier: "L2", method: "transient" });
  assert.ok(!has(tracker, "rec_halt"), "a healed step must not report a halt");
});

test("an unhealed destructive step still reports a deliberate halt (stays out of the agent park)", async () => {
  const page = fakePage();
  const step = { destructive: true, type: "click", handler_hints: {} };
  const tracker = fakeTracker();

  const result = await recoverStep(page, step, {}, "test-slug", 0, "sel", tracker, VERIFY_FAIL_ERR, null, null, null);

  assert.strictEqual(result, false);
  assert.ok(has(tracker, "rec_halt", "destructive"),
    "expected rec_halt/destructive — server.js's `parkable` check depends on run.js seeing this step as a destructive halt");
});

test("the skip does NOT set guard.blocked — that would abort the stages this change allows", async () => {
  const page = fakePage();
  const step = { destructive: true, type: "click", handler_hints: {} };
  const guard = { guarded: true, acted: false, signature: null, blocked: null };

  await recoverStep(page, step, {}, "test-slug", 0, "sel", fakeTracker(), VERIFY_FAIL_ERR, null, null, guard);

  assert.strictEqual(guard.blocked, null,
    "skipping a11y must not look like a guard stop, or stopped() would short-circuit L2");
});

test("control: a NON-destructive step runs the a11y stage (proves the skip above is real)", async () => {
  const page = fakePage();
  const step = { destructive: false, type: "click", handler_hints: {} };
  const tracker = fakeTracker();

  const result = await recoverStep(page, step, {}, "test-slug", 0, "sel", tracker, VERIFY_FAIL_ERR, null, null, null);

  assert.strictEqual(result, false);
  assert.ok(!has(tracker, "rec_skip"), "a non-destructive step must never skip re-identification");
  assert.ok(!has(tracker, "rec_halt"), "a non-destructive step must never report a destructive halt");
});

// WF-9: a "send" step whose action went out but whose check failed was clicked again 7 times. A step that cannot
// be undone is never dispatched a second time on the strength of a failed check.
test("a destructive step that already acted and failed its check is blocked, not clicked again", async () => {
  const { createActionGuard, guardDecision } = require("../../app/cascade");
  const page = {
    url: () => "https://x.test/start",
    locator: () => ({ first: () => ({ waitFor: async () => { throw new Error("not attached"); } }), count: async () => 0 }),
    waitForTimeout: async () => {},
  };
  const check = { validation: { assertions: [{ type: "selector_present", target: ".sent", required: true, timeout_ms: 20 }] } };
  const send = { type: "click", destructive: true, ...check };
  const guard = createActionGuard(send, { acted: true });
  assert.strictEqual(await guardDecision(page, send, {}, null, guard), "blocked");
  assert.strictEqual(guard.blocked, "action-may-have-taken-effect");

  // A step that can be undone keeps the old behaviour: a failed check means retry.
  const plain = { type: "click", ...check };
  assert.strictEqual(await guardDecision(page, plain, {}, null, createActionGuard(plain, { acted: true })), "go");
});
