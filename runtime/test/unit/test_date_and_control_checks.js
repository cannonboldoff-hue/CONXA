"use strict";
// E2E16-11 (owner decisions 2026-11-21).
// BUILD-111 (6): a text check whose words are one whole date finds that date however the page writes it; nothing else
// is loosened, and a date that reads two ways is only found written the same way round.
// W-E: a button's or link's label that appeared (source "control") is never the compiler's proof of a step.
// Real headless Chromium for the page parts: the page's own text is what is being read.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { parseDate, countDates } = require("../../app/assertions");
const { verifyStep, capturePreStepSignature } = require("../../app/run");
const proof = require("../../app/proof");

test("every common way of writing one date reads as the same date", () => {
  const want = parseDate("21 Nov 2026");
  assert.deepStrictEqual(want, { y: 2026, m: 11, d: 21 });
  for (const t of ["11/21/2026", "2026-11-21", "Nov 21, 2026", "November 21, 2026", "21 november 2026", "21.11.2026",
    "21-Nov-2026", "21st Nov 2026", "Nov. 21 2026", "2026/11/21", "  21  NOV  2026 "]) {
    assert.deepStrictEqual(parseDate(t), want, t);
  }
});

test("only words that are, as a whole, one complete date parse; anything else keeps the exact match", () => {
  for (const t of ["Order 21 Nov", "Delivered on 21 Nov 2026", "Nov 2026", "21/11/26", "31/02/2026", "13/13/2026",
    "21 Novembre 2026", "21. November 2026x", "Thank you for your order!", "", null]) {
    assert.strictEqual(parseDate(t), null, String(t));
  }
});

test("a numeric date that reads two ways only matches the same two numbers in the same order", () => {
  const amb = parseDate("03/04/2026");
  assert.strictEqual(amb.ambiguous, true);
  assert.strictEqual(countDates(["Due 03/04/2026 and 3-4-2026"], amb), 2);
  assert.strictEqual(countDates(["2026-03-04", "4 Mar 2026", "04/03/2026", "Mar 4, 2026"], amb), 0);
  // The other way round too: a clear date is never found in a page date that reads two ways.
  assert.strictEqual(countDates(["03/04/2026"], parseDate("4 Mar 2026")), 0);
  assert.strictEqual(countDates(["2026-03-04", "March 4, 2026"], parseDate("4 Mar 2026")), 2);
  // Equal numbers read the same either way.
  assert.strictEqual(countDates(["2026-05-05"], parseDate("05/05/2026")), 1);
});

test("a date inside a longer number or word is not that date", () => {
  const want = parseDate("1/11/2026");
  assert.strictEqual(countDates(["21/11/2026", "ref 11/11/20261"], want), 0);
  assert.strictEqual(countDates(["Saturday, November 21, 2026 at 10:00", "2026-11-21T10:00"], parseDate("21 Nov 2026")), 2);
});

const dateStep = (words, timeout = 1500) => ({ type: "click", validation: { assertions: [{ type: "text_present", target: words, required: true, timeout_ms: timeout }] } });

test("a date check passes on a page that writes the date another way, and only new ones prove the step", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<p>Created 2026-11-20</p><div id="out"></div>`);
    let baseline = await capturePreStepSignature(page, ["21 Nov 2026"]);
    await page.evaluate(() => { document.getElementById("out").textContent = "Delivery: 11/21/2026"; });
    let v = await verifyStep(page, dateStep("21 Nov 2026"), {}, baseline);
    assert.strictEqual(v.pass, true);
    assert.strictEqual(v.results[0].answer, "yes");
    assert.strictEqual(v.results[0].fresh, true);
    assert.strictEqual(proof.assertionRung(v.results[0]), "completed");

    // Already on the page before the step: found, but it proves nothing.
    baseline = await capturePreStepSignature(page, ["Nov 21, 2026"]);
    v = await verifyStep(page, dateStep("Nov 21, 2026"), {}, baseline);
    assert.strictEqual(v.pass, true);
    assert.notStrictEqual(v.results[0].fresh, true);

    // Another date, or a date that reads two ways written differently: not found.
    await page.setContent(`<p>2026-03-04</p><p>11/22/2026</p>`);
    for (const words of ["21 Nov 2026", "03/04/2026"]) {
      v = await verifyStep(page, dateStep(words, 200), {}, await capturePreStepSignature(page, [words]));
      assert.strictEqual(v.pass, false, words);
    }

    // A date a box holds counts, as it does for any words (a filled date box).
    await page.setContent(`<input value="">`);
    baseline = await capturePreStepSignature(page, ["2026-11-21"]);
    await page.fill("input", "21/11/2026");
    v = await verifyStep(page, dateStep("2026-11-21"), {}, baseline);
    assert.strictEqual(v.pass, true);
  } finally {
    await browser.close();
  }
});

test("words that are not a date keep the exact match", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<p>Order 2026-11-21 saved</p>`);
    const v = await verifyStep(page, dateStep("Order 21 Nov 2026 saved", 200), {}, await capturePreStepSignature(page, ["Order 21 Nov 2026 saved"]));
    assert.strictEqual(v.pass, false);
  } finally {
    await browser.close();
  }
});

test("W-E: a control's label the compiler offered never proves the step; one a person ticked is a passed check", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<div id="out"></div>`);
    const mk = (required) => ({ type: "click", validation: { assertions: [{ type: "text_present", target: "Edit", required, timeout_ms: 1500, source: "control" }] } });
    const baseline = await capturePreStepSignature(page, ["Edit"]);
    await page.evaluate(() => { document.getElementById("out").innerHTML = "<button>Edit</button>"; });
    let v = await verifyStep(page, mk(false), {}, baseline);
    assert.strictEqual(v.pass, true, "never a new failure");
    assert.strictEqual(v.results[0].answer, "cant_tell");
    assert.strictEqual(v.answer, "cant_tell");
    // With nothing else to prove it, a step that needs the app to have taken it is "couldn't confirm", never a pass.
    const checks = [{ rung: "performed", witness: "target", answer: "yes" }, { rung: proof.assertionRung(v.results[0]), witness: "renderer", answer: v.results[0].answer }];
    assert.strictEqual(proof.judge(checks, { requiredRung: "accepted" }).verdict, "couldnt_confirm");

    v = await verifyStep(page, mk(true), {}, baseline);
    assert.strictEqual(v.results[0].answer, "yes");
    assert.strictEqual(proof.assertionRung(v.results[0]), "accepted");
  } finally {
    await browser.close();
  }
});
