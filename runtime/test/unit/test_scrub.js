"use strict";

// scrub.js must agree with conxa_compile/app_graph/scrub.py (and the route rule in
// navigation_effects.py) on every case in test/fixtures/scrub_cases.json -- the Python side runs
// the same file (conxa-cloud/tests/test_app_graph.py), so the two cannot drift apart.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const scrub = require("../../app/scrub");

const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "fixtures", "scrub_cases.json"), "utf8"));
const FN = { text: scrub.scrubText, label: scrub.scrubLabel, tokens: (s) => scrub.scrubTokens(s), route: scrub.routeTemplate };

test("every shared case gives the same answer as the Python scrubber", () => {
  assert.ok(cases.length > 20);
  for (const c of cases) {
    assert.deepStrictEqual(FN[c.fn](c.in), c.out, `${c.fn}(${JSON.stringify(c.in)})`);
  }
});

test("nothing a person typed or a page showed survives", () => {
  const dirty = 'Replace "q3-report.pdf" for jane@acme.com at https://x.test/a?b=1 order 12345 now';
  const clean = scrub.scrubText(dirty);
  for (const leaked of ["q3", "jane", "acme", "x.test", "12345", "pdf"]) assert.ok(!clean.includes(leaked), leaked);
});
