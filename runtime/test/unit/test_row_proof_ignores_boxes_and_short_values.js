"use strict";
// WF-3 Leg A (demoqa Web Tables, 2026-11-16): a page-only form "saved" a row. The proof that it worked is that the
// entry now shows up on the page one more time than before. It failed for two reasons: the count included what the form's
// boxes held (the value was in a box before Save and in the new row after: no change), and the nearest typed value
// ("QA") was too short to mean anything, while the same entry's salary ("1001") would have done.
const test = require("node:test");
const assert = require("node:assert");
const pageScripts = require("../../app/page_scripts");
const { wantedIdentity } = require("../../app/result_probes");

test("the first candidate value long enough to mean something is used", () => {
  const probe = { key: "{{department}}", keys: ["{{department}}", "{{salary}}", "{{email}}"] };
  assert.strictEqual(wantedIdentity(probe, { department: "QA", salary: "1001", email: "e1@acme.io" }), "1001");
  assert.strictEqual(wantedIdentity({ key: "{{t}}" }, { t: "Quarterly report" }), "Quarterly report");
  assert.strictEqual(wantedIdentity({ key: "{{t}}" }, { t: "QA" }), null);
});

test("the before/after count reads the page's text, never what a box holds", () => {
  const prev = global.document;
  global.document = { body: { innerText: "Name Salary\nCierra 10000" },
    querySelectorAll: () => [{ type: "text", offsetParent: {}, value: "1001" }] };
  try {
    assert.strictEqual(pageScripts.countPageText("1001"), 0, "only in the form's box");
    assert.strictEqual(pageScripts.countTextOccurrences("1001"), 1, "a plain 'you see it' check still reads boxes");
  } finally {
    global.document = prev;
  }
});
