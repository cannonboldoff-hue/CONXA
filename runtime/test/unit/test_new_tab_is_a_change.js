"use strict";
// "The screen changes" (state_changed) on a click whose whole effect is opening a new tab: the clicked page stays
// exactly as it was, so the check used to fail every run (the-internet /windows "Click Here", WF-1 Leg D). A tab the
// page opened since the baseline is the action's effect. A click that does nothing is still not one.
// Real headless Chromium, because the popup event is what is being tested.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { verifyStep, capturePreStepSignature } = require("../../app/run");

const step = { type: "click", validation: { assertions: [{ type: "state_changed", required: true, timeout_ms: 1500 }] } };

test("a click that only opens a new tab is a change; a click that does nothing is not", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<a id="open" href="about:blank" target="_blank">Click Here</a><button id="noop">Nothing</button>`);

    let baseline = await capturePreStepSignature(page);
    await page.click("#open");
    let v = await verifyStep(page, step, {}, baseline);
    assert.strictEqual(v.pass, true, JSON.stringify(v.results));

    baseline = await capturePreStepSignature(page);
    await page.click("#noop");
    v = await verifyStep(page, step, {}, baseline);
    assert.strictEqual(v.pass, false, "an earlier tab does not count for a later step");
  } finally {
    await browser.close();
  }
});
