"use strict";
// Proof of Done: the checks that need time or a second look (url_stays, row_with_identity, value_equals) and the
// deferred later_step. Every "must not pass" case is a step that did nothing, or a value too weak to mean anything.
const test = require("node:test");
const assert = require("node:assert");
const { EventEmitter } = require("node:events");

const { urlStays, rowWithIdentity, valueEquals, runLooks, wantedValue } = require("../../app/result_probes");
const { evaluateChecks } = require("../../app/effect_probes");
const { resolveLaterProofs } = require("../../app/run");

const PATTERN = "^https:\\/\\/app\\.test\\/items\\/[^/?#]+";
const urlCheck = { id: "c9", probe: { type: "url_stays", pattern: PATTERN } };

// A page whose address changes on a schedule: `moves` is [ms since start, address] in order.
function movingPage(start, moves) {
  const t0 = Date.now();
  return { url: () => { let u = start; for (const [ms, to] of moves) if (Date.now() - t0 >= ms) u = to; return u; } };
}
const nbFor = (page, before) => ({ page, urlBefore: () => before });
const fast = { maxMs: 400, settleMs: 40, pollMs: 10 };

// ── url_stays ──
test("url_stays: the page moved to the landed address and stayed: yes", async () => {
  const page = movingPage("https://app.test/form", [[30, "https://app.test/items/1042"]]);
  assert.deepStrictEqual(await urlStays(urlCheck, nbFor(page, "https://app.test/form"), fast), { answer: "yes" });
});

test("url_stays must not pass: the step began on an address the pattern already matches", async () => {
  const page = movingPage("https://app.test/items/1", []);
  assert.deepStrictEqual(await urlStays(urlCheck, nbFor(page, "https://app.test/items/1"), fast), { answer: "cant_tell" });
});

test("url_stays must not pass: the page never moved, or bounced back to the form", async () => {
  assert.deepStrictEqual(await urlStays(urlCheck, nbFor(movingPage("https://app.test/form", []), "https://app.test/form"), fast), { answer: "no" });
  const bounce = movingPage("https://app.test/form", [[20, "https://app.test/items/7"], [45, "https://app.test/form"]]);
  assert.deepStrictEqual(await urlStays(urlCheck, nbFor(bounce, "https://app.test/form"), { ...fast, settleMs: 120 }), { answer: "no" });
});

test("url_stays: an unknown before-address, a missing or broken pattern cannot say yes", async () => {
  const page = movingPage("https://app.test/items/1", []);
  assert.deepStrictEqual(await urlStays(urlCheck, nbFor(page, null), fast), { answer: "cant_tell" });
  assert.deepStrictEqual(await urlStays({ probe: { type: "url_stays", pattern: "(" } }, nbFor(page, "https://app.test/form"), fast), { answer: "cant_tell" });
  assert.deepStrictEqual(await urlStays({ probe: { type: "url_stays" } }, nbFor(page, "https://app.test/form"), fast), { answer: "cant_tell" });
});

// ── row_with_identity ──
const rowCheck = { id: "c8", probe: { type: "row_with_identity", key: "{{item_title}}", delta: 1 } };
// A page that reports `counts` for the value, call after call (the last repeats).
function countingPage(counts) {
  let n = 0;
  return { evaluate: async () => counts[Math.min(n++, counts.length - 1)] };
}
const rowOpts = { maxMs: 150, pollMs: 10 };

test("row_with_identity: the value shows up one more time than before: yes", async () => {
  const nb = { page: countingPage([1, 1, 2]), nameCountBefore: 1 };
  assert.deepStrictEqual(await rowWithIdentity(rowCheck, { notebook: nb, inputs: { item_title: "Quarterly plan" } }, rowOpts), { answer: "yes" });
});

test("row_with_identity must not pass: the count did not rise (the value was already there and nothing was added)", async () => {
  const nb = { page: countingPage([2]), nameCountBefore: 2 };
  assert.deepStrictEqual(await rowWithIdentity(rowCheck, { notebook: nb, inputs: { item_title: "Quarterly plan" } }, rowOpts), { answer: "no" });
});

test("row_with_identity must not pass: a short, common or unresolved value says nothing", async () => {
  const nb = { page: countingPage([9]), nameCountBefore: 0 };
  for (const inputs of [{ item_title: "ab" }, { item_title: "" }, {}]) {
    assert.deepStrictEqual(await rowWithIdentity(rowCheck, { notebook: nb, inputs }, rowOpts), { answer: "cant_tell" }, JSON.stringify(inputs));
  }
  const noBaseline = { page: countingPage([9]), nameCountBefore: null };
  assert.deepStrictEqual(await rowWithIdentity(rowCheck, { notebook: noBaseline, inputs: { item_title: "Quarterly plan" } }, rowOpts), { answer: "cant_tell" });
});

// A delete on one row (WF-3 Leg B, a cart kept in the browser): the row's own text shows one time fewer.
test("row_with_identity for a delete: yes only when the row's text shows fewer times than before", async () => {
  const gone = { id: "c9", probe: { type: "row_with_identity", key: "Sauce Labs Onesie", delta: -1 } };
  assert.deepStrictEqual(await rowWithIdentity(gone, { notebook: { page: countingPage([1, 0]), nameCountBefore: 1 }, inputs: {} }, rowOpts), { answer: "yes" });
  assert.deepStrictEqual(await rowWithIdentity(gone, { notebook: { page: countingPage([1]), nameCountBefore: 1 }, inputs: {} }, rowOpts), { answer: "no" });
  // Not on the page before: its absence afterwards proves nothing.
  assert.deepStrictEqual(await rowWithIdentity(gone, { notebook: { page: countingPage([0]), nameCountBefore: 0 }, inputs: {} }, rowOpts), { answer: "no" });
});

// ── value_equals ──
const valueCheck = { id: "c7", probe: { type: "value_equals", field_step: "k-fill", value: "{{item_title}}" } };
const fillStep = { source_step_key: "k-fill", type: "type" };
function pageWithFreshTab(fieldValue, { fieldType = "text", resolves = true } = {}) {
  const log = { closed: false };
  const loc = { getAttribute: async () => fieldType, evaluate: async (fn) => fn({ tagName: "INPUT", value: fieldValue }) };
  const tab = { goto: async () => {}, close: async () => { log.closed = true; } };
  const page = new EventEmitter();
  Object.assign(page, { url: () => "https://app.test/items/new", bringToFront: async () => {}, context: () => ({ newPage: async () => tab }) });
  const resolve = async () => { if (!resolves) throw new Error("Element not found"); return loc; };
  return { page, resolve, log };
}
const valOpts = { maxMs: 120, pollMs: 10 };
const ctxFor = (s, inputs) => ({ notebook: { page: s.page }, steps: [fillStep], inputs, registry: { pendingPages: [] }, resolve: s.resolve });

test("value_equals: the field still holds the value on a freshly opened page: yes, and the tab is closed", async () => {
  const s = pageWithFreshTab("  Quarterly   plan ");
  assert.deepStrictEqual(await valueEquals(valueCheck, ctxFor(s, { item_title: "quarterly plan" }), valOpts), { answer: "yes" });
  assert.strictEqual(s.log.closed, true);
});

test("value_equals must not pass: the field holds something else, or cannot be read", async () => {
  assert.deepStrictEqual(await valueEquals(valueCheck, ctxFor(pageWithFreshTab("Old title"), { item_title: "Quarterly plan" }), valOpts), { answer: "no" });
  assert.deepStrictEqual(await valueEquals(valueCheck, ctxFor(pageWithFreshTab("x", { resolves: false }), { item_title: "Quarterly plan" }), valOpts), { answer: "cant_tell" });
});

test("value_equals never reads a password field, and ignores a value too short to mean anything", async () => {
  assert.deepStrictEqual(await valueEquals(valueCheck, ctxFor(pageWithFreshTab("Quarterly plan", { fieldType: "password" }), { item_title: "Quarterly plan" }), valOpts), { answer: "cant_tell" });
  assert.deepStrictEqual(await valueEquals(valueCheck, ctxFor(pageWithFreshTab("ab"), { item_title: "ab" }), valOpts), { answer: "cant_tell" });
  const noStep = { ...ctxFor(pageWithFreshTab("Quarterly plan"), { item_title: "Quarterly plan" }), steps: [] };
  assert.deepStrictEqual(await valueEquals(valueCheck, noStep, valOpts), { answer: "cant_tell" }, "the earlier step was deleted");
});

// ── plumbing ──
test("wantedValue: needs 4+ characters and a fully resolved value", () => {
  assert.strictEqual(wantedValue("{{t}}", { t: "Plan A" }), "Plan A");
  assert.strictEqual(wantedValue("{{t}}", { t: "abc" }), null);
  assert.strictEqual(wantedValue("{{t}}", {}), null);
});

test("runLooks: skips dead checks and answers only the look probes", async () => {
  const dead = { ...urlCheck, id: "c1", calibrated: false, calibration_basis: ["c2"] };
  const page = movingPage("https://app.test/form", [[10, "https://app.test/items/3"]]);
  const effect = { checks: [dead, { id: "c2", probe: { type: "new_text", words: ["saved"] } }] };
  assert.deepStrictEqual(await runLooks(effect, { notebook: nbFor(page, "https://app.test/form") }), {});
  assert.deepStrictEqual(await runLooks(null, {}), {});
  assert.deepStrictEqual(await runLooks(effect, { notebook: null }), {});
});

test("evaluateChecks: a look is used only when it was run; later_step is never not_yet and never yes there", () => {
  const eff = { checks: [urlCheck, rowCheck, valueCheck, { id: "c6", rung: "completed", witness: "later_step", probe: { type: "later_step", step: "k2" } }] };
  const none = Object.fromEntries(evaluateChecks(eff, { notebook: null }).map((c) => [c.id, c.answer]));
  assert.deepStrictEqual(none, { c9: "cant_tell", c8: "cant_tell", c7: "cant_tell", c6: "cant_tell" });
  const withLooks = Object.fromEntries(evaluateChecks(eff, { notebook: null, looks: { c9: { answer: "yes" }, c6: { answer: "yes" } } }).map((c) => [c.id, c.answer]));
  assert.strictEqual(withLooks.c9, "yes");
  assert.strictEqual(withLooks.c6, "cant_tell", "later_step ignores looks: only run.js resolves it, after the later step ran");
});

// ── later_step ──
const NOTE = "I could not confirm that the download finished.";
// What proveStep keeps for a step it could not confirm: the live checks, the rung it needed and its quorum.
function pendingState(quorum = { witnesses: 2 }) {
  const checks = [
    { rung: "performed", witness: "target", role: "support", answer: "yes" },
    { id: "c0", rung: "completed", witness: "renderer", role: "support", answer: "yes" },
    { id: "c1", rung: "completed", witness: "later_step", role: "support", answer: "cant_tell" },
  ];
  return {
    proof: {
      steps: [{ index: 0, verdict: "couldnt_confirm", checks: checks.map((c) => ({ ...c })) }],
      notes: [NOTE],
      later: [{ at: 0, key: "k-upload", checkId: "c1", note: NOTE, checks, required: "completed", quorum }],
    },
  };
}

test("later_step: when the later step ran and passed, the earlier step is judged again and proven, and its note is withdrawn", () => {
  const state = pendingState();
  resolveLaterProofs(state, new Set(["k-upload"]));
  assert.strictEqual(state.proof.steps[0].verdict, "proven");
  assert.strictEqual(state.proof.steps[0].checks.find((c) => c.id === "c1").answer, "yes");
  assert.deepStrictEqual(state.proof.notes, []);
  assert.deepStrictEqual(state.proof.later, []);
});

test("later_step must not pass: the later step never ran (skipped, deleted or the run ended first)", () => {
  const state = pendingState();
  resolveLaterProofs(state, new Set(["some-other-step"]));
  assert.strictEqual(state.proof.steps[0].verdict, "couldnt_confirm");
  assert.strictEqual(state.proof.notes.length, 1);
  assert.strictEqual(state.proof.later.length, 1);
});

test("later_step must not pass: the later step ran but the step's own quorum is still not met", () => {
  const state = pendingState({ witnesses: 3 });
  resolveLaterProofs(state, new Set(["k-upload"]));
  assert.strictEqual(state.proof.steps[0].verdict, "couldnt_confirm");
  assert.strictEqual(state.proof.notes.length, 1, "the note stays: the run still could not confirm it");
  assert.strictEqual(state.proof.steps[0].checks.find((c) => c.id === "c1").answer, "cant_tell");
});
