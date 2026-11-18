"use strict";
// WF-7 7.swap: Carol's row was replaced by "Carol Ann"'s. The row binding "Carol" matched "Carol Ann" (Playwright's
// hasText is a substring), Carol Ann's email was saved, and the check "Saved Carol" passed on "Saved Carol Ann".
// A bound row is now matched only by a whole value (a cell, a field, or the row's own text), and a check whose words
// end in a bound value must have those words end a line. Real headless Chromium, because Playwright's matching is what
// is being tested.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { verifyStep } = require("../../app/run");
const { entityRoots } = require("../../app/resolution");

const table = (rows) => `<table><tbody>${rows.map(([n, e]) => `<tr><td>${n}</td><td><input value="${e}"></td><td><button>Edit</button></td></tr>`).join("")}</tbody></table>`;
const BOUND = { entity_binding: { container_selector: "tbody > tr", identifier: "Carol" } };

test("the bound row is the one that shows the value whole, never a longer look-alike", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(table([["Alice", "a@x"], ["Carol Ann", "ca@x"]]));
    const swapped = await entityRoots([page], BOUND, {});
    assert.strictEqual(swapped.length, 0);
    assert.strictEqual(swapped.absent, true);

    await page.setContent(table([["Carol Ann", "ca@x"], ["  carol ", "c@x"]]));
    const both = await entityRoots([page], BOUND, {});
    assert.strictEqual(both.length, 1);
    assert.strictEqual(await both[0].locator("input").inputValue(), "c@x");

    // a field's value counts as the cell it sits in
    const byField = await entityRoots([page], { entity_binding: { container_selector: "tbody > tr", identifier: "ca@x" } }, {});
    assert.strictEqual(byField.length, 1);

    // a for_each row is named by its own whole text
    await page.setContent(`<ul><li>Bob</li><li>Carol</li><li>Carol Ann</li></ul>`);
    const row = await entityRoots([page], { entity_binding: { container_selector: "ul > li", identifier: "Carol" } }, {});
    assert.strictEqual(row.length, 1);
  } finally {
    await browser.close();
  }
});

test("a check ending in the bound value does not pass on a longer look-alike", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const check = { type: "click", _bound_values: ["Carol"],
      validation: { assertions: [{ type: "text_present", target: "Saved Carol", required: true, timeout_ms: 800 }] } };
    await page.setContent(`<p id="status">Saved Carol Ann</p>`);
    assert.strictEqual((await verifyStep(page, check, {})).pass, false);
    await page.setContent(`<p id="status">Saved Carol.</p>`);
    assert.strictEqual((await verifyStep(page, check, {})).pass, true);
    // without a bound value the words are still found inside a longer line, as before
    await page.setContent(`<p id="status">Saved Carol Ann</p>`);
    assert.strictEqual((await verifyStep(page, { ...check, _bound_values: [] }, {})).pass, true);
  } finally {
    await browser.close();
  }
});
