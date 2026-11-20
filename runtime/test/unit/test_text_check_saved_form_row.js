"use strict";
// WF-11 twin: "Add record" copies the form's boxes into a new table row and clears the boxes. The person's proof words
// ("Cara") were in a box before the click and in the row after it, so the count that includes boxes stayed the same and
// the last step was "couldn't confirm" although the row was there. The page's own text gained the words: that is a
// witness, since a click that did nothing would leave them only in the box.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { capturePreStepSignature, evaluateAssertion } = require("../../app/assertions");

const FORM = `<input id=f value="Cara"><button id=add>Add record</button><table><tbody id=rows></tbody></table>
<script>document.getElementById("add").onclick = () => {
  document.getElementById("rows").insertAdjacentHTML("beforeend", "<tr><td>" + document.getElementById("f").value + "</td></tr>");
  document.getElementById("f").value = "";
};</script>`;

for (const strict of [false, true]) {
  test(`words moved from a box into a saved row prove the click${strict ? " (a bound record value)" : ""}`, async () => {
    const browser = await chromium.launch({ headless: true });
    try {
      const page = await browser.newPage();
      await page.setContent(FORM);
      const check = { type: "text_present", target: "Cara", required: true };
      const step = strict ? { _bound_values: ["Cara"] } : null;
      const baseline = await capturePreStepSignature(page, ["Cara"]);
      assert.deepStrictEqual([baseline.counts.Cara, baseline.pageCounts.Cara], [1, 0], "in the box only");

      const idle = await evaluateAssertion([page], page, check, {}, baseline, 0, step);
      assert.strictEqual(idle.answer, "cant_tell", "nothing clicked: the words in the box prove nothing");

      await page.click("#add");
      const r = await evaluateAssertion([page], page, check, {}, baseline, 0, step);
      assert.deepStrictEqual([r.answer, r.fresh], ["yes", true], "the row is new on the page");
    } finally {
      await browser.close();
    }
  });
}
