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

// WF-13 F6: a short result ("Name:person", under the 20-character text tolerance, no new element, same address) is
// still a change when a required check saw new words; words that were already there prove nothing, so the
// page-change check still decides.
test("new words under a required check also satisfy 'the page changed'; old words do not", async () => {
  const browser = await chromium.launch({ headless: true });
  const both = (words) => ({ type: "click", validation: { assertions: [
    { type: "text_present", target: words, required: true, timeout_ms: 800 },
    { type: "state_changed", required: true, timeout_ms: 500 },
  ] } });
  try {
    const page = await browser.newPage();
    await page.setContent(`<form><input id="n" value="person"><button type="button">Submit</button></form><p id="output"></p><p>Name:old</p>`);
    let baseline = await capturePreStepSignature(page, ["Name:person"]);
    await page.evaluate(() => { document.getElementById("output").textContent = "Name:person"; });
    let v = await verifyStep(page, both("Name:person"), {}, baseline);
    assert.strictEqual(v.pass, true);
    assert.deepStrictEqual(v.results.map((r) => [r.type, r.answer]), [["text_present", "yes"], ["state_changed", "yes"]]);
    assert.strictEqual(v.results[1].proven_by, "fresh_check");

    // Same page again: "Name:person" is already there, Submit did nothing visible -> the page-change check fails.
    baseline = await capturePreStepSignature(page, ["Name:person"]);
    v = await verifyStep(page, both("Name:person"), {}, baseline);
    assert.strictEqual(v.pass, false);
    assert.strictEqual(v.channel, "state_changed");
    assert.notStrictEqual(v.results[0].fresh, true);
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

// BUILD-108 (6): a control's label that newly appeared is a check that passed, but not the result itself.
test("a button's label that appeared is not proof the work finished; a result's words are", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<div id="out"></div>`);
    const mk = (source) => ({ type: "click", validation: { assertions: [{ type: "text_present", target: "Edit", required: true, timeout_ms: 1500, source }] } });
    const baseline = await capturePreStepSignature(page, ["Edit"]);
    await page.evaluate(() => { document.getElementById("out").innerHTML = "<p>Edit</p>"; });
    for (const [source, rung] of [["control", "accepted"], ["result", "completed"]]) {
      const v = await verifyStep(page, mk(source), {}, baseline);
      assert.strictEqual(v.results[0].answer, "yes", source);
      assert.strictEqual(proof.assertionRung(v.results[0]), rung, source);
    }
  } finally {
    await browser.close();
  }
});
