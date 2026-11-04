"use strict";

const test = require("node:test");
const assert = require("node:assert");

const { CLASS, classifyException, remedyFor, buildRepairEvent } = require("../../app/recovery");
const { a11yRecoveryName, gateLocator } = require("../../app/run");

test("a11y recovery name prefers the element's own accessible name over label_text", () => {
  // Regression: the render 'create-a-service-from-github' blueprint link had its label_text
  // mis-captured as a sibling's text ("Project"). Recovery must use inner_text ("Blueprint"),
  // not label_text, or `role=link[name="Project"]` would click creation-button-project.
  assert.strictEqual(
    a11yRecoveryName({ role: "link", aria_label: "", name: "", inner_text: "Blueprint", label_text: "Project" }),
    "Blueprint",
  );
});

test("a11y recovery name falls back to label_text for label-only form controls", () => {
  // A textbox whose accessible name comes from its <label> (inner_text empty) must still resolve.
  assert.strictEqual(
    a11yRecoveryName({ role: "textbox", aria_label: "", name: "", inner_text: "", label_text: "Search repositories" }),
    "Search repositories",
  );
});

test("a11y recovery name prefers aria_label first", () => {
  assert.strictEqual(
    a11yRecoveryName({ aria_label: "Close dialog", inner_text: "X", label_text: "ignore" }),
    "Close dialog",
  );
});

test("a11y recovery name derives from placeholder for label-less inputs", () => {
  // Regression (audit H-3): the compiler names a placeholder-only search box from its
  // placeholder text (identity_bundle.py's aria_label||name||inner_text||placeholder||
  // label_text). Without placeholder here, recovery saw an empty name and bailed immediately.
  assert.strictEqual(
    a11yRecoveryName({ role: "combobox", aria_label: "", name: "", inner_text: "", placeholder: "Search", label_text: "" }),
    "Search",
  );
});

test("a11y recovery name prefers placeholder over label_text", () => {
  assert.strictEqual(
    a11yRecoveryName({ inner_text: "", placeholder: "Search", label_text: "Nav context" }),
    "Search",
  );
});

// Mirrors identity_bundle.py's _NAME_FROM_CONTENT_ROLES gate: a combobox/select never gets its
// accessible name from its own inner text (a concatenated <option> list isn't a name any browser
// or Playwright computes), so recovery must not fabricate one either — the compiler refuses to
// emit a role signal for this element in the first place, and recovery agreeing is what makes
// compile-time and recovery-time naming consistent (the invariant the function's own docstring
// states). Regression: react-datepicker's year <select> had no name, and its concatenated
// <option> text ("1900 1901 1902 ... 1915") was being used as one, producing a selector that
// could never match — this is the runtime half of that same bug.
test("a11y recovery name refuses to fabricate a name from a nameless combobox's option-list inner_text", () => {
  assert.strictEqual(
    a11yRecoveryName({
      tag: "select", role: "combobox",
      aria_label: "", name: "", alt: "", title: "", placeholder: "", label_text: "",
      inner_text: "1900 1901 1902 1903 1904 1905 1906 1907 1908 1909 1910",
    }),
    "",
  );
});

test("a11y recovery name still fabricates from inner_text for a name-from-content role (button)", () => {
  assert.strictEqual(
    a11yRecoveryName({ tag: "button", role: "button", aria_label: "", inner_text: "Submit" }),
    "Submit",
  );
});

test("a11y recovery name uses inner_text up to 80 chars and none beyond (mirrors identity_bundle.py)", () => {
  assert.strictEqual(a11yRecoveryName({ tag: "button", role: "button", inner_text: "x".repeat(80) }), "x".repeat(80));
  assert.strictEqual(a11yRecoveryName({ tag: "button", role: "button", inner_text: "x".repeat(81) }), "");
});

test("a11y recovery name strips a trailing keyboard-shortcut hint", () => {
  assert.strictEqual(
    a11yRecoveryName({
      role: "menuitem",
      aria_label: "",
      inner_text: "File upload Alt+C then U",
    }),
    "File upload",
  );
});

// `name` is the HTML form-field attribute, never an ARIA accessible-name source
// (identity_bundle.py's _accessible_name never includes it) — a radio/checkbox GROUP's `name`
// is the shared group key every sibling carries, so using it here would recover on whichever
// group member happens to match first, not the one actually recorded.
test("a11y recovery name does not fall back to the HTML `name` attribute", () => {
  assert.strictEqual(
    a11yRecoveryName({ role: "radio", aria_label: "", name: "gender", inner_text: "", label_text: "" }),
    "",
  );
});

test("classifies stale/detached error", () => {
  assert.strictEqual(classifyException(new Error("Element is not attached to the DOM")), CLASS.STALE);
});

test("classifies intercepted error", () => {
  assert.strictEqual(classifyException(new Error("<div> intercepts pointer events")), CLASS.INTERCEPTED);
});

test("classifies disabled error", () => {
  assert.strictEqual(classifyException(new Error("element is not enabled")), CLASS.NOT_ENABLED);
});

test("classifies verify-fail via flag", () => {
  const err = Object.assign(new Error("Verification failed: url_pattern"), { verifyFail: true });
  assert.strictEqual(classifyException(err), CLASS.VERIFY_FAIL);
});

test("plain locator timeout → timeout-element (skip straight to L2)", () => {
  assert.strictEqual(classifyException(new Error("Timeout 700ms exceeded waiting for locator")), CLASS.TIMEOUT_ELEMENT);
});

test("timeout carrying a navigation signature → timeout-navigation", () => {
  assert.strictEqual(
    classifyException(new Error("Timeout 700ms exceeded waiting for navigation")),
    CLASS.TIMEOUT_NAVIGATION,
  );
  assert.strictEqual(
    classifyException(new Error("Timeout 700ms exceeded waiting for locator: execution context was destroyed")),
    CLASS.TIMEOUT_NAVIGATION,
  );
});

test("unknown error → unknown", () => {
  assert.strictEqual(classifyException(new Error("some other thing")), CLASS.UNKNOWN);
});

test("remedyFor maps classes to remedies", () => {
  assert.strictEqual(remedyFor(CLASS.STALE), "re-resolve");
  assert.strictEqual(remedyFor(CLASS.INTERCEPTED), "dismiss-overlay");
  assert.strictEqual(remedyFor(CLASS.OUT_OF_BOUNDS), "scroll-into-view");
  assert.strictEqual(remedyFor(CLASS.NOT_STABLE), "wait-stable");
  assert.strictEqual(remedyFor(CLASS.NOT_ENABLED), "wait-enabled");
  assert.strictEqual(remedyFor(CLASS.TIMEOUT_NAVIGATION), "wait-navigation");
  assert.strictEqual(remedyFor(CLASS.TIMEOUT_ELEMENT), "descend-layer2");
  assert.strictEqual(remedyFor(CLASS.VERIFY_FAIL), "descend-layer2");
});

test("buildRepairEvent carries structured drift fields", () => {
  const step = { identity_bundle: { stable_hash: "abc", compat_fingerprint: "fp1" } };
  const evt = buildRepairEvent(step, 3, { tier: "L2", method: "a11y", klass: CLASS.STALE, score: 0.91234, margin: 0.2 });
  assert.strictEqual(evt.step_id, 3);
  assert.strictEqual(evt.tier, "L2");
  assert.strictEqual(evt.method, "a11y");
  assert.strictEqual(evt.stable_hash, "abc");
  assert.strictEqual(evt.app_version_fingerprint, "fp1");
  assert.strictEqual(evt.score, 0.912);
  assert.strictEqual(evt.drift_hint, "re-resolve");
});

test("buildRepairEvent tolerates missing identity_bundle", () => {
  const evt = buildRepairEvent({ type: "click" }, 0, {});
  assert.strictEqual(evt.stable_hash, "");
  assert.strictEqual(evt.tier, "L2");
});

test("gateLocator lets a detach error during the disabled-check escape instead of swallowing it", async () => {
  let calls = 0;
  const loc = {
    waitFor: async () => {},
    evaluate: async () => {
      calls += 1;
      if (calls === 1) return true; // RAF-stable check: element reports stable
      throw new Error("Element is not attached to the DOM"); // disabled-check: detached mid-flight
    },
  };
  await assert.rejects(() => gateLocator(loc, { confidence: 0.9 }), /not attached/);
});

test("gateLocator still swallows unrelated evaluate glitches on the disabled-check (best-effort)", async () => {
  let calls = 0;
  const loc = {
    waitFor: async () => {},
    evaluate: async () => {
      calls += 1;
      if (calls === 1) return true;
      throw new Error("some unrelated evaluate glitch");
    },
  };
  await assert.doesNotReject(() => gateLocator(loc, { confidence: 0.9 }));
});

test("gateLocator waits for attached, not visible, on a hidden file input", async () => {
  const states = [];
  const loc = {
    waitFor: async ({ state }) => { states.push(state); },
    evaluate: async () => { throw new Error("raf/disabled must not run for file inputs"); },
  };
  await gateLocator(loc, {
    type: "upload",
    identity_bundle: { fingerprint: { input_type: "file" } },
  });
  assert.deepStrictEqual(states, ["attached"]);
});

test("gateLocator waits for attached on a click that targeted a file input", async () => {
  const states = [];
  const loc = {
    waitFor: async ({ state }) => { states.push(state); },
    evaluate: async () => { throw new Error("raf/disabled must not run for file inputs"); },
  };
  await gateLocator(loc, {
    type: "click",
    identity_bundle: { fingerprint: { input_type: "file" } },
  });
  assert.deepStrictEqual(states, ["attached"]);
});
