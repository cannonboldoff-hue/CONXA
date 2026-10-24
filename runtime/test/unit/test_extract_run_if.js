"use strict";
// extract steps, outputs and run_if (gap analysis G4 / G5.2). Pure pieces only; the real server path is
// covered by test/e2e/golden_path.js.

const test = require("node:test");
const assert = require("node:assert");
const { runIfDecision, collectOutputs } = require("../../app/run");
const { readExtractValue } = require("../../app/handlers");

const loc = (over = {}) => ({
  innerText: async () => "  Order   #A-1042  shipped ",
  inputValue: async () => "  typed value ",
  getAttribute: async (name) => ({ href: "/orders/1042", "data-id": null })[name] ?? null,
  ...over,
});

test("run_if: not_empty runs only when the caller supplied something", () => {
  const step = { run_if: { input: "note", op: "not_empty" } };
  assert.deepStrictEqual(runIfDecision(step, { note: "hi" }), { skip: false, input: "note" });
  assert.strictEqual(runIfDecision(step, {}).skip, true);
  assert.strictEqual(runIfDecision(step, { note: "   " }).skip, true);
  assert.strictEqual(runIfDecision(step, { note: null }).skip, true);
});

test("run_if: equals and not_equals compare trimmed text", () => {
  assert.strictEqual(runIfDecision({ run_if: { input: "mode", op: "equals", value: "fast" } }, { mode: " fast " }).skip, false);
  assert.strictEqual(runIfDecision({ run_if: { input: "mode", op: "equals", value: "fast" } }, { mode: "slow" }).skip, true);
  assert.strictEqual(runIfDecision({ run_if: { input: "mode", op: "equals", value: "fast" } }, {}).skip, true);
  assert.strictEqual(runIfDecision({ run_if: { input: "mode", op: "not_equals", value: "fast" } }, {}).skip, false);
});

test("run_if: a missing or malformed condition never removes a step", () => {
  assert.strictEqual(runIfDecision({}, {}), null);
  assert.strictEqual(runIfDecision({ run_if: "yes" }, {}), null);
  assert.strictEqual(runIfDecision({ run_if: { op: "equals" } }, {}), null);                // no input named
  assert.strictEqual(runIfDecision({ run_if: { input: "x", op: "teleport" } }, {}), null);  // unknown operator
});

test("extract reads text, input values and attributes, collapsing whitespace", async () => {
  assert.strictEqual(await readExtractValue(loc(), { source: "text" }), "Order #A-1042 shipped");
  assert.strictEqual(await readExtractValue(loc(), {}), "Order #A-1042 shipped");
  assert.strictEqual(await readExtractValue(loc(), { source: "value" }), "typed value");
  assert.strictEqual(await readExtractValue(loc(), { source: "attribute", attribute: "href" }), "/orders/1042");
});

test("extract keeps the first capture group, or the whole match, of a pattern", async () => {
  assert.strictEqual(await readExtractValue(loc(), { pattern: "#([A-Z]-\\d+)" }), "A-1042");
  assert.strictEqual(await readExtractValue(loc(), { pattern: "\\d+" }), "1042");
});

test("extract fails clearly, and without a retry, when there is nothing usable", async () => {
  await assert.rejects(readExtractValue(loc(), { pattern: "refund" }), (e) => e.badInput === true && /does not match/.test(e.message));
  await assert.rejects(readExtractValue(loc(), { pattern: "(" }), (e) => e.badInput === true && /not a valid expression/.test(e.message));
  await assert.rejects(readExtractValue(loc({ innerText: async () => "  " }), {}), (e) => e.badInput === true && /no value/.test(e.message));
  await assert.rejects(readExtractValue(loc(), { source: "attribute", attribute: "data-id" }), (e) => e.badInput === true);
});

test("a pattern may use a run input", async () => {
  assert.strictEqual(await readExtractValue(loc(), { pattern: "({{prefix}}-\\d+)" }, { prefix: "A" }), "A-1042");
});

test("collectOutputs reports every extract step's value, nested bodies included, and nothing unproduced", () => {
  const steps = [
    { type: "extract", output_name: "ticket_id" },
    { type: "for_each", for_each: { steps: [{ type: "extract", output_name: "row_total" }] } },
    { type: "if_present", steps: [{ type: "extract", output_name: "never_ran" }] },
    { type: "click" },
  ];
  assert.deepStrictEqual(collectOutputs(steps, { ticket_id: "T-9", row_total: "42", unrelated: "x" }), { ticket_id: "T-9", row_total: "42" });
  assert.deepStrictEqual(collectOutputs([{ type: "click" }], { a: 1 }), {});
});
