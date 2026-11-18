"use strict";

// Tier 3/4 override-validation gate (run.js:validateOverrideSelector) — extends the
// "resolver never blindly picks candidate[0]" invariant to the agent-recovery closing edge.
// Mocks Playwright's page.locator(selector).all() so no real browser is needed; scoring itself
// reuses the real resolver.js/resolve_adapter.js (not mocked).

const test = require("node:test");
const assert = require("node:assert");

const { validateOverrideSelector } = require("../../app/run");

// Mock page: page.locator(selector).all() resolves to one "locator" per descriptor, whose
// .evaluate() returns that descriptor directly (bypassing real DOM extraction — the extraction
// logic itself, resolve_adapter.js's _extractDescriptor, is exercised in real browser tests).
function mockPage(descriptorsBySelector) {
  return {
    locator(selector) {
      const descriptors = descriptorsBySelector[selector] || [];
      return {
        async all() {
          return descriptors.map(d => ({ evaluate: async () => ({ ...d }) }));
        },
      };
    },
  };
}

test("validateOverrideSelector: no match is invalid", async () => {
  const page = mockPage({});
  const step = { _explicit_selector: "#missing", identity_bundle: {} };
  const result = await validateOverrideSelector(page, step, {});
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.reason, "no-match");
});

test("validateOverrideSelector: a single match is accepted even with an impoverished fingerprint", async () => {
  const page = mockPage({ "#go": [{ role: "button", name: "", text: "", testid: "" }] });
  const step = { _explicit_selector: "#go", identity_bundle: { fingerprint: {} } };
  const result = await validateOverrideSelector(page, step, {});
  assert.strictEqual(result.valid, true);
  assert.ok(result.loc);
});

test("validateOverrideSelector: multi-match resolves the clear winner via fingerprint scoring", async () => {
  const page = mockPage({
    ".btn": [
      { role: "button", name: "Cancel", text: "Cancel", testid: "" },
      { role: "button", name: "Submit order", text: "Submit order", testid: "go" },
    ],
  });
  const step = {
    _explicit_selector: ".btn",
    identity_bundle: { fingerprint: { role: "button", data_testid: "go", aria_label: "Submit order" } },
  };
  const result = await validateOverrideSelector(page, step, {});
  assert.strictEqual(result.valid, true);
  const winner = await result.loc.evaluate();
  assert.strictEqual(winner.testid, "go");
});

test("validateOverrideSelector: a genuine tie (no signal clears the margin) is rejected as ambiguous", async () => {
  const page = mockPage({
    "button": [
      { role: "button", name: "Submit Order", text: "Submit Order", testid: "" },
      { role: "button", name: "Also Button", text: "Also Button", testid: "" },
    ],
  });
  const step = {
    _explicit_selector: "button",
    identity_bundle: { fingerprint: { role: "button", aria_label: "Totally Different Button", data_testid: "vanished" } },
  };
  const result = await validateOverrideSelector(page, step, {});
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.reason, "ambiguous");
  assert.strictEqual(result.candidates.length, 2);
});

test("validateOverrideSelector: missing/blank selector is invalid without querying the page", async () => {
  const page = mockPage({});
  const result = await validateOverrideSelector(page, { _explicit_selector: "", identity_bundle: {} }, {});
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.reason, "missing-selector");
});

// WF-7: the agent-mediated stage (Tier B) may not do what the resolver refuses. A selector narrow enough to match one
// Edit button (">> nth=2", a row path, a numbered candidate) still only names a position when the recording cannot
// tell that button from four others; and a step bound to a row is fixed inside that row or not at all.
const EDIT = { role: "button", name: "Edit", text: "Edit", testid: "" };
const EDIT_STEP = {
  identity_bundle: {
    signals: [{ engine: "css-structural", selector: "button.edit", durability: 0.3 }],
    fingerprint: { role: "button", inner_text: "Edit" },
  },
};

test("validateOverrideSelector: one of several identical row buttons is refused even when the selector matches one", async () => {
  const page = mockPage({ "button.edit >> nth=2": [EDIT], "button.edit": [EDIT, EDIT, EDIT, EDIT, EDIT] });
  const result = await validateOverrideSelector(page, { ...EDIT_STEP, _explicit_selector: "button.edit >> nth=2" }, {});
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.reason, "lookalike");
  assert.deepStrictEqual(result.candidates, []);
  // the only Edit on the page is no choice at all
  const lone = mockPage({ "button.edit >> nth=0": [EDIT], "button.edit": [EDIT] });
  assert.strictEqual((await validateOverrideSelector(lone, { ...EDIT_STEP, _explicit_selector: "button.edit >> nth=0" }, {})).valid, true);
});

function rowsPage(rowTexts) {
  // rows are "name | Edit" (cells); the real page script decides which rows show the value whole, and a row's own
  // .locator() finds its Edit button.
  const fakeEl = (t) => ({ innerText: t.replace(/ \| /g, "\t"), querySelectorAll: () => t.split(" | ").map(c => ({ tagName: "TD", innerText: c })) });
  return {
    locator(selector) {
      if (selector !== "tbody > tr") return { async all() { return []; } };
      return {
        async count() { return rowTexts.length; },
        async evaluateAll(fn, arg) { return fn(rowTexts.map(fakeEl), arg); },
        nth() { return { locator: () => ({ async all() { return [{ evaluate: async () => ({ ...EDIT }) }]; } }) }; },
      };
    },
  };
}

test("validateOverrideSelector: a bound row that is missing or shown twice refuses any agent selector", async () => {
  const bound = { ...EDIT_STEP, _explicit_selector: "button.edit",
    entity_binding: { container_selector: "tbody > tr", identifier: "{{search_by_name}}" } };
  const missing = await validateOverrideSelector(rowsPage(["Eve | Edit"]), bound, { search_by_name: "zzzqqq" });
  assert.deepStrictEqual([missing.valid, missing.reason], [false, "entity-not-found"]);
  const twice = await validateOverrideSelector(rowsPage(["Carol | Edit", "Carol | Edit"]), bound, { search_by_name: "Carol" });
  assert.deepStrictEqual([twice.valid, twice.reason], [false, "entity-not-found"]);
  const found = await validateOverrideSelector(rowsPage(["Bob | Edit", "Carol Ann | Edit", "Carol | Edit"]), bound, { search_by_name: "Carol" });
  assert.strictEqual(found.valid, true);                                  // inside the one bound row: allowed
});

test("validateOverrideSelector: a longer look-alike row (Carol Ann) is not the bound row (Carol)", async () => {
  const bound = { ...EDIT_STEP, _explicit_selector: "button.edit",
    entity_binding: { container_selector: "tbody > tr", identifier: "Carol" } };
  const swapped = await validateOverrideSelector(rowsPage(["Alice | Edit", "Carol Ann | Edit"]), bound, {});
  assert.deepStrictEqual([swapped.valid, swapped.reason], [false, "entity-not-found"]);
});
