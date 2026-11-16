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
