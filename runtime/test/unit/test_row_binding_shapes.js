"use strict";
// E2E16-8: the two row-binding shapes the compiler now writes, run against real headless Chromium.
// - BUILD-102 (6): OrangeHRM's suggestion list (markup as recorded, WF-4 Leg A). The compiled binding is
//   `[role="listbox"] > [role="option"]`, match "contains", self_target; inert until the person's yes.
// - BUILD-108 (3): the same record in two panels. `tbody > tr` sees it twice and refuses; the panel-scoped selector
//   the compiler writes finds the one row.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { entityRoots, resolveStep } = require("../../app/resolution");

const option = (name) => `<div data-v-da59eaf4="" role="option" class="oxd-autocomplete-option"><span>${name}</span><!----></div>`;
const orangeList = (names) => `<div class="oxd-autocomplete-wrapper"><input class="oxd-input">` +
  `<div data-v-3ebc98ba="" role="listbox" class="oxd-autocomplete-dropdown --positon-bottom">${names.map(option).join("")}</div></div>`;
const PICK = (confirmed) => ({
  entity_binding: { container_selector: '[role="listbox"] > [role="option"]', identifier: "{{employee_name}}", source: "input",
    match: "contains", self_target: true, confirmed },
  identity_bundle: { signals: [{ selector: 'internal:text="Amelia  Brown"i', durability: 0.6 }], fingerprint: {}, frame_chain: [] },
});

test("an OrangeHRM suggestion: a yes picks the one option that shows the typed name, and only then", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(orangeList(["John  Smith", "Amelia  Brown", "John Doe"]));
    const picked = await resolveStep(page, PICK(true), { employee_name: "amelia" });
    assert.strictEqual((await picked.innerText()).replace(/\s+/g, " "), "Amelia Brown");
    // two options show it, or none: it stops instead of guessing
    await assert.rejects(resolveStep(page, PICK(true), { employee_name: "john" }), (e) => e.entityNotFound === true);
    await assert.rejects(resolveStep(page, PICK(true), { employee_name: "zed" }), (e) => e.entityNotFound === true);
    // before the yes the binding does nothing: the page is searched as recorded
    const roots = [page];
    assert.strictEqual(await entityRoots(roots, PICK(false), { employee_name: "john" }), roots);
  } finally {
    await browser.close();
  }
});

test("the same record in two panels: the panel-scoped list finds the one row, the plain one refuses", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const row = (n) => `<tr><td>${n}</td><td><button>Edit</button></td></tr>`;
    await page.setContent(`<section id="results"><table><tbody>${row("Alice")}${row("Carol")}</tbody></table></section>` +
      `<section id="recent"><table><tbody>${row("Carol")}</tbody></table></section>`);
    const plain = await entityRoots([page], { entity_binding: { container_selector: "tbody > tr", identifier: "Carol" } }, {});
    assert.strictEqual(plain.length, 0);
    assert.strictEqual(plain.several, true);
    const scoped = await entityRoots([page], { entity_binding: { container_selector: "#results tbody > tr", identifier: "Carol" } }, {});
    assert.strictEqual(scoped.length, 1);
  } finally {
    await browser.close();
  }
});
