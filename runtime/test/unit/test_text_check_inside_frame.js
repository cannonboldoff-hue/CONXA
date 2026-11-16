"use strict";
// WF-2 Segment D: a click inside a frame shows its result there (a tab's panel in a demo frame). The "was it on the page
// before?" count read the top document only, so the text was 0 before and 0 after, never "new", and the step could
// never be proven. Counted across the page's frames, the same way before and after.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { capturePreStepSignature, evaluateAssertion } = require("../../app/assertions");

test("text that appears inside a frame after the step is new, and counts as proof", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<h1>Top</h1><iframe srcdoc="<div id=p style='display:none'>Mauris eleifend est</div>"></iframe>`);
    const frame = page.frames().find((f) => f.parentFrame());
    await frame.waitForSelector("#p", { state: "attached" });
    const words = "Mauris eleifend est";
    const baseline = await capturePreStepSignature(page, [words]);
    assert.strictEqual(baseline.counts[words], 0, "hidden before the step");

    await frame.evaluate(() => { document.getElementById("p").style.display = "block"; });
    const r = await evaluateAssertion([page.frameLocator("iframe")], page, { type: "text_present", target: words, required: true }, {}, baseline);
    assert.strictEqual(r.ok, true);
    assert.strictEqual(r.fresh, true, "new since the step, though it is inside a frame");
  } finally {
    await browser.close();
  }
});
