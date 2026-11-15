"use strict";
// "You see these words on the screen" means contained in the page's text, any letter case, the same meaning as the
// before-count. A required text check whose words were absent before the step and are there after it proves the work
// finished (the demoqa text-box form: "Asha Rao" appears inside "Name:Asha Rao", with no network request at all).
// Real headless Chromium, because Playwright's text matching is what is being tested.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { verifyStep, capturePreStepSignature } = require("../../app/run");
const proof = require("../../app/proof");

const step = (words) => ({ type: "click", validation: { assertions: [{ type: "text_present", target: words, required: true, timeout_ms: 1500 }] } });

test("a typed check is found inside a longer line, and new text proves the work finished", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<form><input id="n"><button type="button">Submit</button></form><div id="output"></div><template><p>Asha Rao</p></template>`);
    const s = step("asha rao");
    const baseline = await capturePreStepSignature(page, ["asha rao"]);
    await page.evaluate(() => { document.getElementById("output").innerHTML = "<p>Name:Asha Rao</p>"; });
    const v = await verifyStep(page, s, {}, baseline);
    assert.strictEqual(v.pass, true);
    const [r] = v.results;
    assert.strictEqual(r.answer, "yes");
    assert.strictEqual(r.fresh, true);
    assert.strictEqual(proof.assertionRung(r), "completed");
    const j = proof.judge([{ rung: proof.assertionRung(r), witness: "renderer", answer: r.answer }], { requiredRung: "completed" });
    assert.strictEqual(j.verdict, "proven");
  } finally {
    await browser.close();
  }
});

test("words already on the page, or only hidden in it, never prove the step", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<h1>Welcome Asha Rao</h1><div hidden><p>Order saved</p></div>`);
    for (const words of ["Asha Rao", "Order saved"]) {
      const s = step(words);
      const baseline = await capturePreStepSignature(page, [words]);
      const v = await verifyStep(page, s, {}, baseline);
      const [r] = v.results;
      assert.notStrictEqual(r.fresh, true, words);
      assert.strictEqual(proof.assertionRung(r), "accepted", words);
    }
    // An advisory check (the compiler's own) never proves completion, even when new.
    assert.strictEqual(proof.assertionRung({ type: "text_present", required: false, fresh: true }), "accepted");
  } finally {
    await browser.close();
  }
});
