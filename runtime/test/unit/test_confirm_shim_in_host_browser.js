"use strict";
// BUILD-117 (4).1: Electron's own OK/Cancel box stays on screen after a late answer. In a host-owned run the page's
// confirm() is refused at once (Cancel) and noted, so the person is asked in Studio's card and the step runs again
// with their answer armed. Never a native box on screen.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const pageScripts = require("../../app/page_scripts");

const html = `<button id="c" onclick="r.textContent=confirm('Delete it?')?'deleted':'kept'">c</button><p id="r"></p>`;

test("the shim refuses a confirm, names it, and an armed answer is used once", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    let native = 0;
    page.on("dialog", (d) => { native++; d.dismiss().catch(() => {}); });
    await page.setContent(html);

    await page.evaluate(pageScripts.armBoxShim);
    await page.click("#c");
    assert.strictEqual(await page.textContent("#r"), "kept", "refused: the page got Cancel");
    assert.deepStrictEqual(await page.evaluate(pageScripts.takeBoxes), ["Delete it?"]);
    assert.deepStrictEqual(await page.evaluate(pageScripts.takeBoxes), [], "taken once");

    await page.evaluate(pageScripts.armBoxShim, true);
    await page.click("#c");
    assert.strictEqual(await page.textContent("#r"), "deleted", "the armed OK was used");
    assert.deepStrictEqual(await page.evaluate(pageScripts.takeBoxes), [], "an armed answer is no question");
    await page.click("#c");
    assert.strictEqual(await page.textContent("#r"), "kept", "and only once");
    assert.strictEqual(native, 0, "no native box was ever raised");

    await page.evaluate(pageScripts.disarmBoxShim);
    await page.click("#c");
    assert.strictEqual(native, 1, "disarmed: the page's own confirm is back");
  } finally {
    await browser.close();
  }
});
