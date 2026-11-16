"use strict";
// Electron (Build Studio's test pane, Conxa Execute) never raises window.prompt(): no dialog event, the call does
// nothing (spike 2026-11-16). In a host-owned run the page is armed with the recorded answer instead, and the
// prompt's dialog step must then finish at once, not wait the whole dialog timeout for an event that never comes.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const pageScripts = require("../../app/page_scripts");
const { HANDLERS, recordedDialogType, promptAnswer } = require("../../app/handlers");

const promptStep = { type: "dialog_accept", value: JSON.stringify({ type: "prompt", message: "I am a JS prompt", value: "{{dialog_answer}}" }) };

test("a recorded prompt step names its kind and its answer, inputs filled in", () => {
  assert.strictEqual(recordedDialogType(promptStep), "prompt");
  assert.strictEqual(promptAnswer(promptStep, { dialog_answer: "Conxa prompt" }), "Conxa prompt");
  assert.strictEqual(recordedDialogType({ type: "dialog_accept", value: "not json" }), "");
});

test("an armed prompt answers once with the recorded text, and its dialog step does not wait", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<button id="p" onclick="r.textContent='You entered: '+prompt('I am a JS prompt','')">p</button><p id="r"></p>`);
    await page.evaluate(pageScripts.armPrompt, "Conxa prompt");
    await page.click("#p");
    assert.strictEqual(await page.textContent("#r"), "You entered: Conxa prompt");

    const t0 = Date.now();
    await HANDLERS.dialog_accept(page, promptStep, { dialog_answer: "Conxa prompt" }, { dialogQueue: [] });
    assert.ok(Date.now() - t0 < 5000, "answered in the page: nothing to wait for");
    assert.strictEqual(await page.evaluate(pageScripts.takePromptAnswered), false, "the answer is counted once");
  } finally {
    await browser.close();
  }
});
