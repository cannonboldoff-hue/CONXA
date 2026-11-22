"use strict";
// BUILD-117 (3): a "read a value" step bound to the row a run input names. Its only signal is the recorded absolute
// XPath (/html[1]/.../tr[1]/td[2]); read from inside the bound row it found nothing, so the step missed (and then fell
// back to the recorded value's text, which only the recorded row shows). The step and the page are the live pass's own
// (docs/testing/evidence/LIVE-PASS-2026-11-22/site/list.html, lp-delete-customer step 7), run in real Chromium.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { resolveStep } = require("../../app/resolution");
const { inputRefs } = require("../../app/failure_response");

const rows = (list) => list.map(([n, b]) =>
  `<tr class="customer-row"><td class="name">${n}</td><td class="balance">${b}</td><td><button class="info">Show details</button> <button class="del">Delete</button></td></tr>`).join("");
// The search puts the searched customer first, as list.html does.
const PAGE = (list) => `<h1>Customers</h1><form><label>Find customer <input id="q"></label></form><div id="msg"></div>` +
  `<table><thead><tr><th>Name</th><th>Balance</th><th></th></tr></thead><tbody id="rows">${rows(list)}</tbody></table>`;

const readBalance = (xpath = "/html[1]/body[1]/table[1]/tbody[1]/tr[1]/td[2]") => ({
  type: "extract", selector: "text=\"$12.50\"", output_name: "customers", extract: { source: "text" },
  identity_bundle: {
    signals: [{ engine: "xpath", selector: xpath, durability: 0.01, orthogonality_class: "structural", unique_at_compile: true }],
    fingerprint: { role: "td", tag: "td", inner_text: "", css_class_tokens: ["balance"], anchor_phrases: ["Customers"], section_heading: "Customers" },
    frame_chain: [],
  },
  entity_binding: { container_selector: "#rows > tr.customer-row", identifier: "{{find_customer}}", source: "input", confirmed: false, match: "whole", position: 0 },
});

test("the bound row's own cell is read for any customer, not only the recorded one", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(PAGE([["Ada Lovelace", "$40.00"], ["Grace Hopper", "$12.50"], ["Alan Turing", "$7.25"]]));
    const ada = await resolveStep(page, readBalance(), { find_customer: "Ada Lovelace" });
    assert.strictEqual(await ada.innerText(), "$40.00");
    // Grace now sits on the second row: the recorded place (tr[1]) is not where her row is, the row binding is.
    const grace = await resolveStep(page, readBalance(), { find_customer: "Grace Hopper" });
    assert.strictEqual(await grace.innerText(), "$12.50");
    // A customer who is not listed still stops the run: the binding fails closed, it never reads another row.
    await assert.rejects(resolveStep(page, readBalance(), { find_customer: "Nobody" }), (e) => e.entityNotFound === true);
  } finally {
    await browser.close();
  }
});

test("a path that does not pass through the row is left as it was, and a miss inside a found row clears the input", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(PAGE([["Ada Lovelace", "$40.00"]]));
    // The recorded path ends at the page's heading, outside any row: nothing in the row is guessed from it.
    const step = readBalance("/html[1]/body[1]/h1[1]");
    const err = await resolveStep(page, step, { find_customer: "Ada Lovelace" }).then(() => null, (e) => e);
    assert.ok(err && err.resolveMiss, "still a miss");
    assert.strictEqual(err.rowFound, true, "Ada's row was on the page");
    // Studio is told which inputs the step read; the one that named the found row is not among them.
    const refs = inputRefs({ ...err, failedStep: { ...step, value: "{{note}}" } }, { find_customer: "Ada Lovelace", note: "x" });
    assert.deepStrictEqual(refs.map((r) => r.id), ["note"]);
    assert.deepStrictEqual(inputRefs({ failedStep: step }, { find_customer: "Ada" }).map((r) => r.id), ["find_customer"]);
  } finally {
    await browser.close();
  }
});

test("without a row binding the absolute path is read from the page, as before", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(PAGE([["Ada Lovelace", "$40.00"], ["Grace Hopper", "$12.50"]]));
    const { entity_binding: _, ...plain } = readBalance();
    assert.strictEqual(await (await resolveStep(page, plain, {})).innerText(), "$40.00");
  } finally {
    await browser.close();
  }
});
