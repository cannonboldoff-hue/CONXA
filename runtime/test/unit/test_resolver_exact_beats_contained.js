"use strict";
// WF-2 Segment F: an autocomplete list shows "Java" and "JavaScript". Locators match contained-in text on purpose
// (c320dba), so the scorer must tell them apart: the same words beat words merely contained in a longer name, the
// recorded element agrees with its own fingerprint, and a look-alike with only the longer name is never clicked.

const test = require("node:test");
const assert = require("node:assert");
const { resolve, scoreCandidate, DEFAULT_CONFIDENCE_THRESHOLD } = require("../../app/resolver");

// What the compiler recorded for the "Java" suggestion (li.ui-menu-item).
const FP = { tag: "li", role: "li", inner_text: "Java", anchor_phrases: ["ul#ui-id-1"] };
const java = { role: "", name: "Java", text: "Java", anchorNeighbors: ["Java"] };
const javascript = { role: "", name: "JavaScript", text: "JavaScript", anchorNeighbors: ["JavaScript"] };
const signals = [{ selector: 'internal:text="Java"', durability: 0.85 }];
const rootOf = (nodes) => ({ queryAll: () => nodes });

test("the recorded element agrees with its own fingerprint", () => {
  assert.ok(scoreCandidate(java, FP) >= DEFAULT_CONFIDENCE_THRESHOLD);
});

test("the same words beat words contained in a longer name", () => {
  const r = resolve(signals, FP, rootOf([java, javascript]));
  assert.strictEqual(r.node, java);
  assert.ok(r.margin >= 0.15);
});

test("a look-alike with only the longer name is refused, not clicked", () => {
  const r = resolve(signals, FP, rootOf([javascript]));
  assert.ok(!r.node, "JavaScript must never stand in for Java");
});

test("a keyboard-shortcut tail on the live name still matches exactly (c320dba)", () => {
  const fp = { tag: "button", role: "button", aria_label: "File upload" };
  assert.ok(scoreCandidate({ role: "button", name: "File upload Alt+C then U", text: "" }, fp) >= 0.99);
});

// EXEC-67: GitHub's tab link was recorded as "Pull requests 58 (58)" (a count and its screen-reader echo); the next
// run shows another count. The real fingerprint of the shipped wf3-github-tour-40 pack, one css signal on the href.
const TAB_FP = { tag: "a", role: "link", inner_text: "Pull requests 58 (58)", label_text: "Issues 1.1k (1.1k)",
  anchor_phrases: ["Global navigation menu"] };
const tab = (text) => ({ role: "link", name: text, text, anchorNeighbors: [] });
const tabSignals = [{ engine: "css-structural", selector: 'a[href="/cli/cli/pulls"]', durability: 0.3 }];

test("a tab whose live count changed is still the recorded tab (EXEC-67)", () => {
  const live = tab("Pull requests 61 (61)");
  const r = resolve(tabSignals, TAB_FP, rootOf([live]));
  assert.strictEqual(r.node, live);
  assert.ok(scoreCandidate(tab("Pull requests"), TAB_FP) >= 0.99, "the count gone altogether is the same name");
});

test("a changed count never makes two tabs a tie it was not before", () => {
  const r = resolve(tabSignals, TAB_FP, rootOf([tab("Pull requests 61 (61)"), tab("Issues 1.2k (1.2k)")]));
  assert.strictEqual(r.node && r.node.name, "Pull requests 61 (61)");
  assert.ok(!resolve(tabSignals, TAB_FP, rootOf([tab("Pull requests 1 (1)"), tab("Pull requests 2 (2)")])).node);
});

test("a lone trailing number is not a count: Page 3 never stands in for Page 2", () => {
  const fp = { tag: "a", role: "link", inner_text: "Page 2" };
  assert.ok(!resolve(tabSignals, fp, rootOf([tab("Page 3")])).node);
});

// WF-2 Segment J: two identical "Toggle Me" widgets; only the section they sit in (its last heading) differs.
const TWIN_FP = { tag: "summary", role: "button", inner_text: "Toggle Me", section_heading: "Details" };
const twin = (heading) => ({ role: "button", name: "Toggle Me", text: "Toggle Me", anchorNeighbors: [], sectionHeading: heading });
const twinSignals = [{ selector: 'internal:role=button[name="Toggle Me"]', durability: 0.9 }];

test("identical twins in different sections are told apart by their section", () => {
  const first = twin("Details");
  const r = resolve(twinSignals, TWIN_FP, rootOf([twin("Customizing the Summary Icon"), first]));
  assert.strictEqual(r.node, first);
  assert.ok(r.margin >= 0.15);
});

test("identical twins in the same section are still refused", () => {
  const r = resolve(twinSignals, TWIN_FP, rootOf([twin("Details"), twin("Details")]));
  assert.ok(!r.node && r.ambiguous);
});

test("a single match whose section heading changed still resolves", () => {
  const r = resolve(twinSignals, TWIN_FP, rootOf([twin("Renamed section")]));
  assert.ok(r.node);
});

test("a pack recorded before section headings existed scores as before", () => {
  const { section_heading, ...old } = TWIN_FP;
  assert.strictEqual(scoreCandidate(twin("Details"), old), scoreCandidate(twin("Anything"), old));
});
