"use strict";

const test = require("node:test");
const assert = require("node:assert");

const {
  detectPreExecDrift,
  assessDrift,
  bestLandmarkScore,
} = require("../../app/drift");

// ── bestLandmarkScore (pure) ────────────────────────────────────────────────

test("bestLandmarkScore: matching descriptor scores high", () => {
  const lm = { intent: "click submit", data_testid: "submit", inner_text: "Submit" };
  const descriptors = [{ testid: "submit", name: "Submit", text: "Submit" }];
  assert.ok(bestLandmarkScore(lm, descriptors) >= 0.9);
});

test("bestLandmarkScore: no candidates scores zero", () => {
  const lm = { intent: "click submit", data_testid: "submit", inner_text: "Submit" };
  assert.strictEqual(bestLandmarkScore(lm, []), 0);
});

test("bestLandmarkScore: contradicting testid scores low", () => {
  const lm = { data_testid: "submit", inner_text: "Submit" };
  const descriptors = [{ testid: "cancel", name: "Cancel", text: "Cancel" }];
  assert.ok(bestLandmarkScore(lm, descriptors) < 0.5);
});

// ── assessDrift (pure aggregation) ──────────────────────────────────────────

test("assessDrift: all landmarks present → no drift", () => {
  const landmarks = [{ intent: "a" }, { intent: "b" }, { intent: "c" }];
  const r = assessDrift(landmarks, [0.9, 0.8, 1.0]);
  assert.strictEqual(r.drift, false);
  assert.strictEqual(r.missing, 0);
});

test("assessDrift: majority missing → drift", () => {
  const landmarks = [{ intent: "a" }, { intent: "b" }, { intent: "c" }];
  const r = assessDrift(landmarks, [0.9, 0.1, 0.0]);
  assert.strictEqual(r.drift, true);
  assert.strictEqual(r.missing, 2);
  assert.deepStrictEqual(r.missingIntents, ["b", "c"]);
});

test("assessDrift: exactly at ratio threshold trips drift once at least two landmarks are missing", () => {
  const landmarks = [{ intent: "a" }, { intent: "b" }, { intent: "c" }, { intent: "d" }];
  const r = assessDrift(landmarks, [0.9, 0.8, 0.1, 0.1]); // 2/4 == 0.5 threshold
  assert.strictEqual(r.drift, true);
});

test("assessDrift: a single missing landmark is never drift, however few landmarks there are", () => {
  assert.strictEqual(assessDrift([{ intent: "a" }, { intent: "b" }], [0.9, 0.1]).drift, false);
  assert.strictEqual(assessDrift([{ intent: "a" }], [0.0]).drift, false);
});

test("assessDrift: empty landmarks → no drift", () => {
  const r = assessDrift([], []);
  assert.strictEqual(r.drift, false);
  assert.strictEqual(r.total, 0);
});

// ── detectPreExecDrift (page-driven, with a fake page) ──────────────────────

// Fake Playwright page whose locators yield preset descriptors. Each descriptor
// is returned directly from item.evaluate() (bypassing the real browser extractor).
function fakePage(byLocator, byText, byRole) {
  const handles = (arr) => (arr || []).map((d) => ({ evaluate: async () => d }));
  return {
    url: () => "https://example.test/app",
    locator: (sel) => ({ all: async () => handles(byLocator[sel]) }),
    getByText: (txt) => ({ all: async () => handles((byText || {})[txt]) }),
    getByRole: (role) => ({ all: async () => handles((byRole || {})[role]) }),
  };
}

test("detectPreExecDrift: landmarks present → no drift", async () => {
  const fp = {
    landmarks: [
      { intent: "open menu", data_testid: "menu", inner_text: "Menu" },
      { intent: "click submit", data_testid: "submit", inner_text: "Submit" },
    ],
    landmark_count: 2,
  };
  const page = fakePage(
    {
      '[data-testid="menu"], [data-test-id="menu"]': [{ testid: "menu", name: "Menu", text: "Menu" }],
      '[data-testid="submit"], [data-test-id="submit"]': [{ testid: "submit", name: "Submit", text: "Submit" }],
    },
    {},
  );
  const r = await detectPreExecDrift(page, fp);
  assert.strictEqual(r.drift, false);
  assert.strictEqual(r.missing, 0);
});

test("detectPreExecDrift: landmarks vanished → drift", async () => {
  const fp = {
    landmarks: [
      { intent: "open menu", data_testid: "menu", inner_text: "Menu" },
      { intent: "click submit", data_testid: "submit", inner_text: "Submit" },
    ],
    landmark_count: 2,
  };
  // Redesigned page: none of the recorded testids or texts resolve.
  const page = fakePage({}, {});
  const r = await detectPreExecDrift(page, fp);
  assert.strictEqual(r.drift, true);
  assert.strictEqual(r.missing, 2);
  assert.strictEqual(r.total, 2);
});

test("detectPreExecDrift: no landmarks → no drift, no throw", async () => {
  const r = await detectPreExecDrift(fakePage({}, {}), { landmarks: [] });
  assert.strictEqual(r.drift, false);
});

test("detectPreExecDrift: falls back to text when testid missing", async () => {
  const fp = {
    landmarks: [{ intent: "click cta", inner_text: "Get started" }],
    landmark_count: 1,
  };
  const page = fakePage({}, { "Get started": [{ name: "Get started", text: "Get started" }] });
  const r = await detectPreExecDrift(page, fp);
  assert.strictEqual(r.drift, false);
});

test("detectPreExecDrift: primary_selector role= string resolves via getByRole, not exact-match locator()", async () => {
  // primary_selector carries signal_to_display()'s unprefixed "role=…[name=\"…\"]" form.
  // Landmark gathering must route it through toLocator (tolerant getByRole), not
  // page.locator() directly — which would apply Playwright's exact-match role engine and
  // never find an element whose live accessible name has extra text (e.g. a keyboard-
  // accelerator hint) beyond what was compiled.
  const fp = {
    landmarks: [{
      intent: "open file upload",
      primary_selector: 'role=menuitem[name="File upload"]',
      inner_text: "File upload",
    }],
    landmark_count: 1,
  };
  const page = fakePage(
    {}, // no plain-locator match — proves it did NOT fall through to page.locator()
    {},
    { menuitem: [{ role: "menuitem", name: "File upload", text: "File upload" }] },
  );
  const r = await detectPreExecDrift(page, fp);
  assert.strictEqual(r.drift, false);
  assert.strictEqual(r.missing, 0);
});
