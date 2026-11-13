"use strict";

const test = require("node:test");
const assert = require("node:assert");

const { verifyStep, capturePreStepSignature } = require("../../app/run");
const { pollPositive, pollNegative } = require("../../app/assertions");

// Minimal mock page + locator for VERIFY unit testing (no browser).
function mockPage(url, counts = {}, values = {}) {
  return {
    url: () => url,
    locator: (sel) => ({
      first: () => ({
        waitFor: async () => { if ((counts[sel] || 0) === 0) throw new Error("not attached"); },
        inputValue: async () => values[sel] ?? "",
      }),
      count: async () => counts[sel] || 0,
    }),
  };
}

test("no assertions → pass", async () => {
  const page = mockPage("https://app.example.com/done");
  const r = await verifyStep(page, { type: "click" }, {});
  assert.strictEqual(r.pass, true);
});

test("url_pattern assertion passes when url matches", async () => {
  const page = mockPage("https://app.example.com/orders/123/confirmed");
  const step = { type: "click", validation: { assertions: [
    { type: "url_pattern", target: "/orders/.*/confirmed", required: true },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true);
});

test("url_pattern assertion fails when url does not match", { timeout: 70000 }, async () => {
  const page = mockPage("https://app.example.com/orders/cart");
  const step = { type: "click", validation: { assertions: [
    { type: "url_pattern", target: "/orders/.*/confirmed", required: true, timeout_ms: 50 },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, false);
  assert.strictEqual(r.channel, "url_pattern");
});

test("hash url_pattern assertion honours the short compiler timeout", async () => {
  const page = mockPage("https://app.example.com/page");
  const t0 = Date.now();
  const step = { type: "click", validation: { assertions: [
    { type: "url_pattern", target: "#detail$", required: true, timeout_ms: 50 },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, false);
  assert.ok(Date.now() - t0 < 500, "same-document hash checks should not inherit the page-load budget");
});

// COMPILE-3: url_changed = "the URL differs from before the action" (compiler target = before-URL).
test("url_changed passes when the page navigated away from the pre-action URL", async () => {
  const page = mockPage("https://app.example.com/success/42");
  const step = { type: "click", validation: { assertions: [
    { type: "url_changed", target: "https://app.example.com/form", required: true },
  ] } };
  const r = await verifyStep(page, step, {}, { url: "https://app.example.com/form" });
  assert.strictEqual(r.pass, true);
});

test("url_changed fails when the page stayed put", { timeout: 70000 }, async () => {
  const page = mockPage("https://app.example.com/form");
  const step = { type: "click", validation: { assertions: [
    { type: "url_changed", target: "https://app.example.com/form", required: true, timeout_ms: 50 },
  ] } };
  const r = await verifyStep(page, step, {}, { url: "https://app.example.com/form" });
  assert.strictEqual(r.pass, false);
  assert.strictEqual(r.channel, "url_changed");
});

test("url_changed prefers the live baseline over the recorded target", async () => {
  // Recorded on a different host than the replay; only the live baseline is meaningful.
  const page = mockPage("https://prod.example.com/success");
  const step = { type: "click", validation: { assertions: [
    { type: "url_changed", target: "https://staging.example.com/form", required: true },
  ] } };
  const r = await verifyStep(page, step, {}, { url: "https://prod.example.com/form" });
  assert.strictEqual(r.pass, true);
});

test("url_changed falls back to the recorded target when no baseline was captured", async () => {
  const page = mockPage("https://app.example.com/success");
  const step = { type: "click", validation: { assertions: [
    { type: "url_changed", target: "https://app.example.com/form", required: true },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true);
});

test("url_changed steps capture a pre-action baseline", () => {
  const { needsStateChangedBaseline } = require("../../app/assertions");
  assert.strictEqual(needsStateChangedBaseline({ type: "click", validation: { assertions: [{ type: "url_changed", target: "" }] } }), true);
});

test("selector_present passes when element attached", async () => {
  const page = mockPage("https://x.test", { ".success-banner": 1 });
  const step = { type: "click", validation: { assertions: [
    { type: "selector_present", target: ".success-banner", required: true },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true);
});

test("selector_absent fails when element still present", async () => {
  const page = mockPage("https://x.test", { ".spinner": 2 });
  const step = { type: "click", validation: { assertions: [
    { type: "selector_absent", target: ".spinner", required: true, timeout_ms: 50 },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, false);
});

test("selector_absent passes once the element is gone and stays gone through the stabilization window", async () => {
  const page = mockPage("https://x.test", { ".spinner": 0 });
  const step = { type: "click", validation: { assertions: [
    { type: "selector_absent", target: ".spinner", required: true },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true);
});

test("advisory (required=false) assertion failure does not fail the step", async () => {
  const page = mockPage("https://x.test", {});
  const step = { type: "click", validation: { assertions: [
    { type: "selector_present", target: ".maybe", required: false },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true);
});

test("interpolates input vars into assertion target", async () => {
  const page = mockPage("https://app.example.com/u/alice");
  const step = { type: "click", validation: { assertions: [
    { type: "url_pattern", target: "/u/{{user}}", required: true },
  ] } };
  const r = await verifyStep(page, step, { user: "alice" });
  assert.strictEqual(r.pass, true);
});

// ─── value_equals ────────────────────────────────────────────────────────────

test("value_equals passes on an exact match", async () => {
  const page = mockPage("https://x.test", {}, { "#email": "alice@example.com" });
  const step = { type: "fill", validation: { assertions: [
    { type: "value_equals", target: "#email", expected: "alice@example.com", required: true },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true);
});

test("value_equals passes on a normalized match (whitespace/case)", async () => {
  const page = mockPage("https://x.test", {}, { "#name": "  John  Smith " });
  const step = { type: "fill", validation: { assertions: [
    { type: "value_equals", target: "#name", expected: "john smith", required: true },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true);
});

test("value_equals passes via contains fallback (masked/formatted field)", async () => {
  const page = mockPage("https://x.test", {}, { "#phone": "(555) 123-4567" });
  const step = { type: "fill", validation: { assertions: [
    { type: "value_equals", target: "#phone", expected: "555", required: true },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true);
});

test("value_equals fails when the field holds an unrelated value", async () => {
  const page = mockPage("https://x.test", {}, { "#email": "" });
  const step = { type: "fill", validation: { assertions: [
    { type: "value_equals", target: "#email", expected: "alice@example.com", required: true, timeout_ms: 50 },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, false);
  assert.strictEqual(r.channel, "value_equals");
});

test("value_equals interpolates the expected value from inputs", async () => {
  const page = mockPage("https://x.test", {}, { "#email": "bob@example.com" });
  const step = { type: "fill", validation: { assertions: [
    { type: "value_equals", target: "#email", expected: "{{email}}", required: true },
  ] } };
  const r = await verifyStep(page, step, { email: "bob@example.com" });
  assert.strictEqual(r.pass, true);
});

// ─── state_changed ───────────────────────────────────────────────────────────

function mockStatePage({ url = "https://x.test", textLen = 100, interactiveCount = 5 } = {}) {
  return {
    url: () => url,
    evaluate: async (_fn, _sel) => ({ textLen, interactiveCount }),
    locator: () => ({ first: () => ({}) }),
  };
}

test("state_changed passes when the URL differs from the baseline", async () => {
  const page = mockStatePage({ url: "https://x.test/after" });
  const baseline = { url: "https://x.test/before", textLen: 100, interactiveCount: 5 };
  const step = { type: "click", validation: { assertions: [{ type: "state_changed", required: true }] } };
  const r = await verifyStep(page, step, {}, baseline);
  assert.strictEqual(r.pass, true);
});

test("state_changed passes when the interactive-element count differs", async () => {
  const page = mockStatePage({ interactiveCount: 6 });
  const baseline = { url: "https://x.test", textLen: 100, interactiveCount: 5 };
  const step = { type: "click", validation: { assertions: [{ type: "state_changed", required: true }] } };
  const r = await verifyStep(page, step, {}, baseline);
  assert.strictEqual(r.pass, true);
});

test("state_changed passes when the body text length shifts beyond tolerance", async () => {
  const page = mockStatePage({ textLen: 200 });
  const baseline = { url: "https://x.test", textLen: 100, interactiveCount: 5 };
  const step = { type: "click", validation: { assertions: [{ type: "state_changed", required: true }] } };
  const r = await verifyStep(page, step, {}, baseline);
  assert.strictEqual(r.pass, true);
});

test("state_changed fails when nothing on the page changed (the no-op guard)", async () => {
  const page = mockStatePage({ url: "https://x.test", textLen: 100, interactiveCount: 5 });
  const baseline = { url: "https://x.test", textLen: 100, interactiveCount: 5 };
  const step = { type: "click", validation: { assertions: [{ type: "state_changed", required: true, timeout_ms: 50 }] } };
  const r = await verifyStep(page, step, {}, baseline);
  assert.strictEqual(r.pass, false);
  assert.strictEqual(r.channel, "state_changed");
});

test("state_changed tolerates small incidental text-length drift (timestamp noise)", async () => {
  const page = mockStatePage({ textLen: 105 }); // within STATE_CHANGED_TEXT_LEN_TOLERANCE (20)
  const baseline = { url: "https://x.test", textLen: 100, interactiveCount: 5 };
  const step = { type: "click", validation: { assertions: [{ type: "state_changed", required: true, timeout_ms: 50 }] } };
  const r = await verifyStep(page, step, {}, baseline);
  assert.strictEqual(r.pass, false);
});

test("state_changed passes trivially when no baseline was captured", async () => {
  const page = mockStatePage();
  const step = { type: "click", validation: { assertions: [{ type: "state_changed", required: true }] } };
  const r = await verifyStep(page, step, {}, null);
  assert.strictEqual(r.pass, true);
});

test("capturePreStepSignature returns url/textLen/interactiveCount from the page", async () => {
  const page = mockStatePage({ url: "https://x.test/a", textLen: 42, interactiveCount: 3 });
  const sig = await capturePreStepSignature(page);
  assert.deepStrictEqual(sig, { url: "https://x.test/a", textLen: 42, interactiveCount: 3 });
});

// ─── web-first polling (Phase A) ──────────────────────────────────────────────

// A page whose element "appears" only after a few polls — proves verifyStep retries the
// predicate instead of sampling it once. A pre-poll rewrite would fail this immediately.
function mockRevealAfterCallsPage(url, { revealAfterCalls = 2 } = {}) {
  let calls = 0;
  return {
    url: () => url,
    locator: () => ({
      count: async () => {
        calls++;
        return calls > revealAfterCalls ? 1 : 0;
      },
    }),
  };
}

test("text_present polls until the element appears instead of failing on the first check", async () => {
  const page = mockRevealAfterCallsPage("https://x.test", { revealAfterCalls: 2 });
  const step = { type: "click", validation: { assertions: [
    { type: "text_present", target: "Order confirmed", required: true, timeout_ms: 2000 },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true);
});

test("text_present still fails once the timeout elapses if the element never appears", async () => {
  const page = mockRevealAfterCallsPage("https://x.test", { revealAfterCalls: 9999 });
  const step = { type: "click", validation: { assertions: [
    { type: "text_present", target: "Order confirmed", required: true, timeout_ms: 50 },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, false);
});

// A page that flickers: looks absent on the first check, reappears on the stabilization
// recheck, then goes absent for good. Proves pollNegative doesn't false-pass on a flicker.
function mockFlickerAbsentPage(url) {
  let calls = 0;
  return {
    url: () => url,
    locator: () => ({
      count: async () => {
        calls++;
        if (calls === 2) return 1; // stabilization recheck catches the flicker
        return 0;
      },
    }),
  };
}

test("selector_absent does not false-pass on a flicker (reappears during the stabilization window)", async () => {
  const page = mockFlickerAbsentPage("https://x.test");
  const step = { type: "click", validation: { assertions: [
    { type: "selector_absent", target: ".spinner", required: true, timeout_ms: 3000 },
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, true); // eventually stabilizes absent — but only after the flicker resolves
});

// ─── full assertion audit (Phase A) ───────────────────────────────────────────

test("verifyStep evaluates every assertion and returns a full audit, not just the first failure", async () => {
  const page = mockPage("https://app.example.com/orders/cart", { ".confirm-banner": 0 });
  const step = { type: "click", validation: { assertions: [
    { type: "url_pattern", target: "/orders/cart", required: true },           // passes
    { type: "url_pattern", target: "/orders/.*/confirmed", required: true, timeout_ms: 50 }, // fails (required)
    { type: "selector_present", target: ".confirm-banner", required: false, timeout_ms: 20 }, // fails (advisory)
  ] } };
  const r = await verifyStep(page, step, {});
  assert.strictEqual(r.pass, false);
  assert.strictEqual(r.channel, "url_pattern");
  assert.strictEqual(r.results.length, 3);
  assert.deepStrictEqual(r.results.map(x => x.ok), [true, false, false]);
  assert.deepStrictEqual(r.results.map(x => x.required), [true, true, false]);
  for (const result of r.results) {
    assert.ok(typeof result.elapsed_ms === "number");
  }
});

test("no-assertions verdict carries an empty results array", async () => {
  const page = mockPage("https://app.example.com/done");
  const r = await verifyStep(page, { type: "click" }, {});
  assert.deepStrictEqual(r.results, []);
});

// ─── native JS dialog (alert/confirm/prompt) blocks verification ─────────────
//
// A click that opens a native dialog blocks the page's renderer thread until the *next* step
// (dialog_accept/dialog_dismiss) resolves it. Before this fix, a required assertion on that
// click step (e.g. state_changed) would poll page.evaluate/locator.count forever against the
// blocked renderer, so the dialog_accept step was never reached and the dialog never closed —
// a real deadlock (FIX.md, 2026-08-26 replay-stuck-on-alert entry).

test("verifyStep skips verification entirely while a dialog is pending, even with a required assertion", async () => {
  let touched = false;
  const page = {
    url: () => { touched = true; return "https://x.test"; },
    locator: () => { touched = true; return { count: async () => 0 }; },
    evaluate: async () => { touched = true; return { textLen: 0, interactiveCount: 0 }; },
  };
  const step = { type: "click", validation: { assertions: [
    { type: "state_changed", required: true, timeout_ms: 50 },
  ] } };
  const dialogQueue = [{ type: "alert" }]; // a dialog already fired synchronously off the click
  const r = await verifyStep(page, step, {}, null, dialogQueue);
  assert.strictEqual(r.pass, true);
  assert.strictEqual(r.channel, "dialog_pending");
  assert.strictEqual(touched, false, "verifyStep must not touch the page while a dialog is open");
});

test("verifyStep verifies normally when the dialog queue is empty", async () => {
  const page = mockPage("https://x.test", { ".confirm-banner": 1 });
  const step = { type: "click", validation: { assertions: [
    { type: "selector_present", target: ".confirm-banner", required: true },
  ] } };
  const r = await verifyStep(page, step, {}, null, []);
  assert.strictEqual(r.pass, true);
});

// ─── pollPositive/pollNegative honor their own timeout against a hung check ──
//
// Both used to `await checkFn()` unconditionally and only check the deadline afterward, so a
// checkFn that never resolves (e.g. a Playwright call against a renderer blocked by an open
// dialog) hung the poll — and the whole run — forever. They must now give up at the deadline
// even when checkFn itself never settles.

test("pollPositive gives up at the deadline instead of hanging forever on a stuck check", async () => {
  const started = Date.now();
  const result = await pollPositive(() => new Promise(() => {}), 150);
  assert.strictEqual(result, false);
  assert.ok(Date.now() - started < 2000, "must not hang past the deadline");
});

test("pollNegative gives up at the deadline instead of hanging forever on a stuck check", async () => {
  const started = Date.now();
  const result = await pollNegative(() => new Promise(() => {}), 150);
  assert.strictEqual(result, false);
  assert.ok(Date.now() - started < 2000, "must not hang past the deadline");
});

test("pollPositive still resolves true promptly when the check simply succeeds", async () => {
  const result = await pollPositive(() => Promise.resolve(true), 1000);
  assert.strictEqual(result, true);
});

// ── Proof of Done: every check says yes / no / not_yet / cant_tell ─────────────────────────────────

test("answers: a check that held is yes, one that did not is no", async () => {
  const page = mockPage("https://app.example.com/orders/123/confirmed");
  const ok = await verifyStep(page, { type: "click", validation: { assertions: [{ type: "url_pattern", target: "/confirmed", required: true }] } }, {});
  assert.strictEqual(ok.answer, "yes");
  assert.strictEqual(ok.results[0].answer, "yes");
});

test("answers: nothing to check, or a baseline that could not be taken, is cant_tell, never yes", async () => {
  const page = mockPage("https://app.example.com/x");
  const none = await verifyStep(page, { type: "click" }, {});
  assert.deepStrictEqual([none.pass, none.answer], [true, "cant_tell"], "still passes as it always did");
  const noBaseline = await verifyStep(page, { type: "click", validation: { assertions: [{ type: "state_changed", required: true }] } }, {}, null);
  assert.deepStrictEqual([noBaseline.pass, noBaseline.results[0].answer, noBaseline.answer], [true, "cant_tell", "cant_tell"]);
});

test("answers: a native dialog in the way is not_yet", async () => {
  const page = mockPage("https://app.example.com/x");
  const r = await verifyStep(page, { type: "click", validation: { assertions: [{ type: "url_changed", required: true }] } }, {}, null, [{}]);
  assert.deepStrictEqual([r.pass, r.answer], [true, "not_yet"]);
});

test("answers: one real yes outweighs a check that could not look, and a required no is a no", async () => {
  const page = mockPage("https://app.example.com/orders/1");
  const mixed = await verifyStep(page, { type: "click", validation: { assertions: [
    { type: "url_pattern", target: "/orders/", required: true },
    { type: "state_changed", required: false },
  ] } }, {}, null);
  assert.deepStrictEqual(mixed.results.map((r) => r.answer), ["yes", "cant_tell"]);
  assert.strictEqual(mixed.answer, "yes");
});

test("a failed click does not burn the page-load wait: advisory checks use their own short timeout, and a re-check is capped", async () => {
  const page = mockPage("https://github.com/github/gitignore");
  const step = { type: "click", validation: { assertions: [
    { type: "url_changed", target: "https://github.com/github/gitignore", timeout_ms: 8000, required: false },
    { type: "url_pattern", target: "^https://github\.com/x/blob", timeout_ms: 8000, required: false },
  ] } };
  const t0 = Date.now();
  const r = await verifyStep(page, step, {}, null, null);
  assert.strictEqual(r.pass, true, "advisory only: never fails the step");
  assert.ok(Date.now() - t0 < 20000, "two advisory URL checks wait their own 8s, not 60s each");
  const required = { type: "click", validation: { assertions: [{ type: "url_changed", target: "https://github.com/github/gitignore", timeout_ms: 8000, required: true }] } };
  const t1 = Date.now();
  const r2 = await verifyStep(page, required, {}, null, null, { capMs: 300 });
  assert.strictEqual(r2.pass, false);
  assert.ok(Date.now() - t1 < 3000, "a capped re-check returns quickly");
});
