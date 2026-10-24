"use strict";

const test = require("node:test");
const assert = require("node:assert");

const { signalToLocator, gatherCandidates, bundleFingerprint, roleLocator, textLocator, toLocator } = require("../../app/resolve_adapter");
const { resolve } = require("../../app/resolver");

const idInterp = (s) => s;  // identity interpolate for tests

// Mock root recording which builder was invoked.
function mockRoot() {
  const calls = [];
  const mk = (kind, arg, opts) => {
    calls.push({ kind, arg, opts });
    return { kind, arg, opts };
  };
  return {
    calls,
    getByTestId: (v) => mk("testid", v),
    getByRole: (r, o) => mk("role", r, o),
    getByText: (t, o) => mk("text", t, o),
    locator: (s) => mk("locator", s),
  };
}

test("signalToLocator maps testid → literal CSS locator (preserves data-testid)", () => {
  // The testid branch must use root.locator() with the exact attribute selector, NOT getByTestId().
  // getByTestId is hard-wired to data-testid (no hyphen) and would silently miss data-test-id pages.
  const root = mockRoot();
  signalToLocator(root, { engine: "testid", selector: 'internal:testid=[data-testid="go"]' }, idInterp, {});
  assert.deepStrictEqual(root.calls[0], { kind: "locator", arg: '[data-testid="go"]', opts: undefined });
});

test("signalToLocator maps hyphenated data-test-id → literal CSS locator (regression)", () => {
  // Pages using data-test-id (with hyphen) were silently dropped before this fix because
  // getByTestId only matches data-testid. Verify the locator carries the exact attribute name.
  const root = mockRoot();
  signalToLocator(root, {
    engine: "testid",
    selector: 'internal:testid=[data-test-id="creation-button-blueprint"]',
  }, idInterp, {});
  assert.deepStrictEqual(root.calls[0], { kind: "locator", arg: '[data-test-id="creation-button-blueprint"]', opts: undefined });
  // Must NOT have called getByTestId at all.
  assert.ok(root.calls.every(c => c.kind !== "testid"), "getByTestId must not be called for hyphenated attributes");
});

test("signalToLocator maps role+name → getByRole substring (Playwright default)", () => {
  // Compile may store a shortcut-stripped prefix ("File upload") while the live accessible
  // name is still "File upload Alt+C then U". exact:true would miss; uniqueness/margin
  // handles Blueprint vs Blueprints.
  const root = mockRoot();
  signalToLocator(root, { engine: "role", selector: 'internal:role=button[name="Submit order"]' }, idInterp, {});
  assert.strictEqual(root.calls[0].kind, "role");
  assert.strictEqual(root.calls[0].arg, "button");
  assert.deepStrictEqual(root.calls[0].opts, { name: "Submit order" });
});

test("signalToLocator maps text → getByText substring", () => {
  const root = mockRoot();
  signalToLocator(root, { engine: "text_based", selector: 'internal:text="Save"' }, idInterp, {});
  assert.strictEqual(root.calls[0].kind, "text");
  assert.strictEqual(root.calls[0].arg, "Save");
  assert.deepStrictEqual(root.calls[0].opts, undefined);
});

test("signalToLocator relational falls back to base role+name (substring)", () => {
  // Playwright has no `right-of=` chain engine, so the spatial part is dropped and the durable
  // base role+name is resolved; the resolver's uniqueness gate does the sibling disambiguation
  // the spatial anchor was meant to provide.
  const root = mockRoot();
  signalToLocator(root, {
    engine: "relational",
    selector: 'internal:role=button[name="X"] >> right-of=internal:text="Y"',
  }, idInterp, {});
  assert.strictEqual(root.calls[0].kind, "role");
  assert.deepStrictEqual(root.calls[0].opts, { name: "X" });
});

test("signalToLocator xpath gets xpath= prefix", () => {
  const root = mockRoot();
  signalToLocator(root, { engine: "xpath", selector: "//div/button" }, idInterp, {});
  assert.deepStrictEqual(root.calls[0], { kind: "locator", arg: "xpath=//div/button", opts: undefined });
});

test("signalToLocator maps attr → locator() CSS", () => {
  const root = mockRoot();
  signalToLocator(root, { engine: "attr", selector: 'li[role="menuitem"][data-key="19"]' }, idInterp, {});
  assert.deepStrictEqual(root.calls[0], {
    kind: "locator",
    arg: 'li[role="menuitem"][data-key="19"]',
    opts: undefined,
  });
});

test("signalToLocator css passes through to locator()", () => {
  const root = mockRoot();
  signalToLocator(root, { engine: "css-structural", selector: "button.submit" }, idInterp, {});
  assert.deepStrictEqual(root.calls[0], { kind: "locator", arg: "button.submit", opts: undefined });
});

// Mock an element-locator returned by .all() whose evaluate() yields a fixed descriptor.
function mockElement(descriptor) {
  return { evaluate: async () => ({ ...descriptor }), _id: descriptor.testid || descriptor.name };
}

// Build a mock root whose builders return a locator with .all() resolving to mock elements.
function rootWithCandidates(bySelectorKind) {
  const make = (els) => ({ all: async () => els });
  return {
    getByTestId: () => make(bySelectorKind.testid || []),
    getByRole: () => make(bySelectorKind.role || []),
    getByText: () => make(bySelectorKind.text || []),
    locator: () => make(bySelectorKind.locator || []),
  };
}

test("gatherCandidates + resolve picks the unique testid match (data-testid)", async () => {
  // signalToLocator now calls root.locator() not root.getByTestId(), so the mock root must
  // serve the winner from its `locator` bucket.
  const winner = mockElement({ testid: "go", role: "button", name: "Go", text: "Go" });
  const root = rootWithCandidates({ locator: [winner] });
  const signals = [
    { engine: "testid", selector: 'internal:testid=[data-testid="go"]', durability: 0.99 },
    { engine: "role", selector: 'internal:role=button[name="Go"]', durability: 0.95 },
  ];
  const map = await gatherCandidates([root], signals, idInterp, {});
  const fp = bundleFingerprint({ fingerprint: { data_testid: "go", role: "button", aria_label: "Go" } });
  const result = resolve(signals, fp, { queryAll: (sel) => map[sel] || [] }, {});
  assert.ok(result.node, "should resolve a node");
  assert.strictEqual(result.node._hashPayload, undefined, "hash payload stripped");
  assert.strictEqual(result.signalUsed.engine, "testid");
  assert.strictEqual(result.node._loc, winner);
});

test("gatherCandidates + resolve picks the unique hyphenated testid match (regression)", async () => {
  // Simulates the create-a-service-from-github step 2 scenario: two sibling link elements, only
  // one with data-test-id="creation-button-blueprint". Before the fix the testid signal produced
  // zero candidates (getByTestId mismatch) and the wrong sibling was clicked via score drift.
  const blueprint = mockElement({ testid: "creation-button-blueprint", role: "link", name: "Blueprint", text: "Blueprint" });
  const project   = mockElement({ testid: "creation-button-project",   role: "link", name: "Project",   text: "Project"   });
  // locator() is now called for testid signals; simulate the page returning only the matching element.
  const root = rootWithCandidates({ locator: [blueprint] });
  const signals = [
    { engine: "testid", selector: 'internal:testid=[data-test-id="creation-button-blueprint"]', durability: 0.99 },
    { engine: "role",   selector: 'internal:role=link[name="Blueprint"]',                        durability: 0.9025 },
  ];
  const map = await gatherCandidates([root], signals, idInterp, {});
  const fp = bundleFingerprint({ fingerprint: {
    data_testid: "creation-button-blueprint", role: "link",
    aria_label: "", inner_text: "Blueprint",
  }});
  const result = resolve(signals, fp, { queryAll: (sel) => map[sel] || [] }, {});
  assert.ok(result.node, "should resolve blueprint, not project");
  assert.strictEqual(result.node._loc, blueprint, "must pick blueprint, not the project sibling");
  assert.strictEqual(result.signalUsed.engine, "testid", "must resolve via testid signal, not fall through to recovery");
  // project sibling must not appear among candidates (locator returned only blueprint)
  const allCandidates = Object.values(map).flat();
  assert.ok(!allCandidates.some(c => c._loc === project), "project sibling must not appear in candidate map");
});

test("a11y recovery shape: same-name candidates are disambiguated by fingerprint, not .first()", async () => {
  // Fix #1 guarantee: a11y recovery resolves through the pure matcher, so even when two elements
  // share the recovery name ("Blueprint"), the one matching the recorded fingerprint (testid) wins
  // the uniqueness gate — the decoy is never blindly clicked. This is the resolver-level invariant
  // behind recoverWithA11y's synthetic role/text bundle.
  const real  = mockElement({ testid: "creation-button-blueprint", role: "link", name: "Blueprint", text: "Blueprint" });
  const decoy = mockElement({ testid: "",                          role: "link", name: "Blueprint", text: "Blueprint" });
  const root = rootWithCandidates({ role: [real, decoy] });
  const signals = [{ engine: "role", selector: 'internal:role=link[name="Blueprint"]', durability: 0.9 }];
  const map = await gatherCandidates([root], signals, idInterp, {});
  const fp = bundleFingerprint({ fingerprint: { data_testid: "creation-button-blueprint", role: "link", inner_text: "Blueprint" } });
  const result = resolve(signals, fp, { queryAll: (sel) => map[sel] || [] }, {});
  assert.ok(result.node, "should resolve a winner");
  assert.strictEqual(result.node._loc, real, "must pick the fingerprint-matching element, not the decoy");
});

test("gatherCandidates computes a stableHash from the payload", async () => {
  const el = { evaluate: async () => ({ role: "button", name: "X", text: "X", testid: "", _hashPayload: "button||X" }) };
  const root = rootWithCandidates({ role: [el] });
  const signals = [{ engine: "role", selector: 'internal:role=button[name="X"]', durability: 0.95 }];
  const map = await gatherCandidates([root], signals, idInterp, {});
  const d = map[signals[0].selector][0];
  assert.strictEqual(typeof d.stableHash, "string");
  assert.strictEqual(d.stableHash.length, 64);  // sha256 hex
  assert.strictEqual(d._hashPayload, undefined);
});

test("bundleFingerprint folds stable_hash into the fingerprint", () => {
  const fp = bundleFingerprint({ fingerprint: { role: "button" }, stable_hash: "abc" });
  assert.strictEqual(fp.role, "button");
  assert.strictEqual(fp.stable_hash, "abc");
});

// signal_to_display() (compiler) strips the "internal:" prefix for the editor UI, and that
// display string ends up in target.primary_selector / the compiled step's top-level
// "selector" field. Any runtime path handed that string must resolve it the same tolerant
// way as the internal:-prefixed form — never fall through to Playwright's exact-match
// selector engine just because the prefix is missing.
test("roleLocator matches with or without the internal: prefix", () => {
  const root = mockRoot();
  roleLocator(root, 'role=menuitem[name="File upload"]');
  assert.strictEqual(root.calls[0].kind, "role");
  assert.strictEqual(root.calls[0].arg, "menuitem");
  assert.deepStrictEqual(root.calls[0].opts, { name: "File upload" });

  const root2 = mockRoot();
  roleLocator(root2, 'internal:role=menuitem[name="File upload"]');
  assert.deepStrictEqual(root2.calls[0], root.calls[0]);
});

test("textLocator matches with or without the internal: prefix", () => {
  const root = mockRoot();
  textLocator(root, 'text="File upload"');
  assert.strictEqual(root.calls[0].kind, "text");
  assert.strictEqual(root.calls[0].arg, "File upload");

  const root2 = mockRoot();
  textLocator(root2, 'internal:text="File upload"');
  assert.deepStrictEqual(root2.calls[0], root.calls[0]);
});

test("toLocator routes role/text strings through the tolerant matcher, everything else through locator()", () => {
  const roleRoot = mockRoot();
  toLocator(roleRoot, 'role=menuitem[name="File upload"]');
  assert.strictEqual(roleRoot.calls[0].kind, "role");

  const textRoot = mockRoot();
  toLocator(textRoot, 'text="Save"');
  assert.strictEqual(textRoot.calls[0].kind, "text");

  const cssRoot = mockRoot();
  toLocator(cssRoot, '[data-key="19"]');
  assert.deepStrictEqual(cssRoot.calls[0], { kind: "locator", arg: '[data-key="19"]', opts: undefined });
});

// BUILD-49: a clicked item that changes each run keeps `{{input}}` in its text signals and in the
// fingerprint's text fields, so scoring compares each candidate with THIS run's text.
const { interpolate: realInterp } = require("../../app/interpolate");

function textNode(text) {
  return { role: "link", name: text, text, testid: "", stableHash: "recorded-hash", anchorNeighbors: [] };
}

const templatedBundle = {
  stable_hash: "recorded-hash",
  fingerprint: { role: "link", tag: "a", inner_text: "{{file}}", aria_label: "{{file}}" },
  signals: [{ engine: "role", selector: 'internal:role=link[name="{{file}}"]', durability: 0.9 }],
};

test("bundleFingerprint fills a templated text field from the run and drops the recorded hash", () => {
  const fp = bundleFingerprint(templatedBundle, realInterp, { file: "LICENSE" });
  assert.strictEqual(fp.inner_text, "LICENSE");
  assert.strictEqual(fp.aria_label, "LICENSE");
  assert.strictEqual(fp.stable_hash, "");
  // an untemplated bundle is untouched, even when an interpolator is supplied
  assert.strictEqual(bundleFingerprint({ stable_hash: "h", fingerprint: { inner_text: "Save" } }, realInterp, {}).stable_hash, "h");
});

test("a templated item resolves to this run's label, and the uniqueness margin still decides among near matches", () => {
  const fp = bundleFingerprint(templatedBundle, realInterp, { file: "LICENSE" });
  const unique = { queryAll: () => [textNode("LICENSE")] };
  assert.ok(resolve(templatedBundle.signals, fp, unique, {}).node);
  // the RECORDED label would have scored 0 against this run's fingerprint
  assert.strictEqual(resolve(templatedBundle.signals, fp, { queryAll: () => [textNode("Android.gitignore")] }, {}).node, undefined);
  // two near-identical candidates (LICENSE vs LICENSE-2) are not blindly picked: margin gate applies
  const both = { queryAll: () => [textNode("LICENSE"), textNode("LICENSE")] };
  assert.strictEqual(resolve(templatedBundle.signals, fp, both, {}).ambiguous, true);
});
