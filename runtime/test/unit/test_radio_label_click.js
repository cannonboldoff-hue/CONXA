"use strict";
// BUILD-111 (3): a radio drawn by its own label (Bootstrap's custom-control, demoqa's gender buttons) keeps the real input
// transparent behind the label, so a click aimed at the input failed as "<label> intercepts pointer events". The runtime
// now presses the input's own label, through the click witness; anything else in front still fails. Real headless
// Chromium, because Playwright's hit check is what is being tested.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { HANDLERS } = require("../../app/handlers");

const BOOTSTRAP = `<style>
  .cc{position:relative;z-index:1;display:inline-flex;min-height:1.5rem;padding-left:1.5rem;margin-right:1rem}
  .cc input{position:absolute;left:0;z-index:-1;width:1rem;height:1.25rem;opacity:0}
  .cc label{position:relative}
  .cc label::before{position:absolute;top:.25rem;left:-1.5rem;display:block;width:1rem;height:1rem;content:"";border:1px solid #999;border-radius:50%}
</style>`;
const BY_ID = `<div class="cc"><input type="radio" id="g1" name="gender" value="Male"><label for="g1">Male</label></div>
  <div class="cc"><input type="radio" id="g2" name="gender" value="Female"><label for="g2">Female</label></div>`;
// The input sits transparent under the label's own picture. (An input that is not rendered at all never gets this far:
// the step's visibility gate refuses it first.)
const HIDE = "position:absolute;left:0;top:0;opacity:0;z-index:-1";
const PIC = "position:relative;display:inline-block;width:80px;height:24px;background:#eee";
const WRAPPED = `<label style="position:relative;z-index:1;display:inline-block"><input type="radio" name="size" value="S" style="${HIDE}"><span style="${PIC}">Small</span></label>
  <label style="position:relative;z-index:1;display:inline-block"><input type="radio" name="size" value="L" style="${HIDE}"><span style="${PIC}">Large</span></label>`;

async function withPage(html, fn) {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(html);
    await fn(page);
  } finally {
    await browser.close();
  }
}

test("a radio hidden behind its own label (label for=id) is pressed on that label", async () => {
  await withPage(BOOTSTRAP + `<div style="height:1500px"></div>` + BY_ID, async page => {
    await HANDLERS.set_radio(page, { action: "set_radio", _explicit_selector: "#g2" }, {});
    assert.strictEqual(await page.locator("#g2").isChecked(), true);
    assert.strictEqual(await page.locator("#g1").isChecked(), false);
  });
});

test("a radio hidden inside the label that holds it is pressed on that label", async () => {
  await withPage(WRAPPED, async page => {
    await HANDLERS.set_radio(page, { action: "set_radio", _explicit_selector: 'input[value="L"]' }, {});
    assert.strictEqual(await page.locator('input[value="L"]').isChecked(), true);
  });
});

test("a plain radio is still clicked on itself", async () => {
  await withPage(`<input type="radio" id="a" name="n"><label for="a">A</label><input type="radio" id="b" name="n"><label for="b">B</label>`, async page => {
    const via = await page.locator("#b").evaluate(require("../../app/page_scripts").coveringOwnLabel);
    assert.strictEqual(via, null);
    await HANDLERS.set_radio(page, { action: "set_radio", _explicit_selector: "#b" }, {});
    assert.strictEqual(await page.locator("#b").isChecked(), true);
  });
});

test("a radio covered by something other than its own label is not routed around", async () => {
  await withPage(BOOTSTRAP + BY_ID + `<div id="banner" style="position:fixed;inset:0;z-index:9;background:#fff">Accept cookies</div>`, async page => {
    const via = await page.locator("#g2").evaluate(require("../../app/page_scripts").coveringOwnLabel);
    assert.strictEqual(via, null);
  });
});
