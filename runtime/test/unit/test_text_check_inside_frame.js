"use strict";
// WF-2 Segment D: a click inside a frame shows its result there (a tab's panel in a demo frame). The "was it on the page
// before?" count read the top document only, so the text was 0 before and 0 after, never "new", and the step could
// never be proven. Counted across the page's frames, the same way before and after.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { capturePreStepSignature, evaluateAssertion } = require("../../app/assertions");

test("what a visible text box holds is on the screen; a password or file picker is not (WF-2 Segment E)", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<input id=d><input id=p type=password value="11/15/2026x"><p>Date:</p>`);
    const words = "11/15/2026";
    const baseline = await capturePreStepSignature(page, [words]);
    assert.strictEqual(baseline.counts[words], 0, "the password box never counts");
    await page.fill("#d", words);
    const r = await evaluateAssertion([page], page, { type: "text_present", target: words, required: true }, {}, baseline);
    assert.strictEqual(r.ok, true, "the date in the box is found");
    assert.strictEqual(r.fresh, true, "and it was not there before the step");
  } finally {
    await browser.close();
  }
});

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

// WF-8 8.13: a heading already on the page "proved" the robot-check click. A text check is a witness only if it would
// have said "no" had the action done nothing: counted before and more of it now.
test("words already on the page, or with no count before, never prove the step (can't tell, never a failure)", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<h1>Sample Form with ReCAPTCHA</h1><button id=b>Go</button>`);
    const words = "Sample Form with ReCAPTCHA";
    const check = { type: "text_present", target: words, required: true };
    const baseline = await capturePreStepSignature(page, [words]);
    assert.strictEqual(baseline.counts[words], 1);
    const same = await evaluateAssertion([page], page, check, {}, baseline);
    assert.deepStrictEqual([same.ok, same.answer, !!same.fresh], [true, "cant_tell", false], "already there, no more of it");

    const blind = await evaluateAssertion([page], page, check, {}, null);
    assert.deepStrictEqual([blind.ok, blind.answer], [true, "cant_tell"], "no count before the step");
    const uncounted = await evaluateAssertion([page], page, check, {}, { ...baseline, counts: {} });
    assert.strictEqual(uncounted.answer, "cant_tell", "the count could not be read");

    await page.evaluate(() => { document.body.insertAdjacentHTML("beforeend", "<p>Sample Form with ReCAPTCHA</p>"); });
    const more = await evaluateAssertion([page], page, check, {}, baseline);
    assert.deepStrictEqual([more.answer, more.fresh], ["yes", true], "more of it now: the action brought it up");
  } finally {
    await browser.close();
  }
});
