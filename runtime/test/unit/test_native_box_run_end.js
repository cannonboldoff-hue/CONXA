"use strict";
// BUILD-117 (4): in Build Studio's test pane (a host panel) the run's page outlives the run. An OK/Cancel box nobody may
// answer (no one to ask, a step that can't be undone, a dry or scheduled run) used to stay open after the run had
// failed, and pressing OK there later still ran the page's action. The run now presses Cancel before it ends; only a
// pause that is asking about the box keeps it open. And the failure says what happened, not "didn't respond in time".

const test = require("node:test");
const assert = require("node:assert");
const { runPlan } = require("../../app/run");
const { plainCause } = require("../../app/errors");

function fakeDialog(type, message) {
  const d = { type: () => type, message: () => message, defaultValue: () => "", accepted: 0, dismissed: 0 };
  d.accept = async () => { d.accepted++; };
  d.dismiss = async () => { d.dismissed++; };
  return d;
}

// Opening "https://x.test/a" makes the page raise `dialog` (a confirm() no step answers).
function nativePage(dialogQueue, dialog) {
  return {
    waitForLoadState: async () => {},
    context: () => ({ on: () => {} }),
    url: () => "https://x.test",
    evaluate: async () => null,
    once: () => {},
    off: () => {},
    screenshot: async () => { throw new Error("no picture while the box is open"); },
    goto: async (url) => { if (url.endsWith("/a")) dialogQueue.push(dialog); },
  };
}
const nav = (url, extra = {}) => ({ type: "navigate", url, ...extra });

async function settle(steps, extra, q, d) {
  return runPlan(nativePage(q, d), steps, {}, 0, "native-box-end", { tracker: { emit: () => {} }, dialogQueue: q, ...extra })
    .then((r) => ({ r }), (e) => ({ e }));
}

test("a box nobody may answer is pressed Cancel before the run ends, never OK", async () => {
  const cases = [{ answerPopups: false }, { answerPopups: true, dryRun: true }, { answerPopups: true, scheduled: true }];
  for (const extra of cases) {
    const q = [];
    const d = fakeDialog("confirm", "Delete Grace Hopper?");
    const out = await settle([nav("https://x.test/a"), nav("https://x.test/b")], extra, q, d);
    assert.ok(!(out.e && out.e.forkPause), JSON.stringify(extra));
    assert.deepStrictEqual([d.accepted, d.dismissed, q.length], [0, 1, 0], JSON.stringify(extra));
  }
  const q = [];
  const d = fakeDialog("confirm", "Delete Grace Hopper?");
  await settle([nav("https://x.test/a", { destructive: true }), nav("https://x.test/b")], { answerPopups: true }, q, d);
  assert.deepStrictEqual([d.accepted, d.dismissed], [0, 1], "a step that can't be undone is never asked, and never left open");
});

test("the pause that asks about the box keeps it open for the answer", async () => {
  const q = [];
  const d = fakeDialog("confirm", "Open the details of Grace Hopper?");
  const out = await settle([nav("https://x.test/a"), nav("https://x.test/b")], { answerPopups: true }, q, d);
  assert.strictEqual(out.e && out.e.forkPause, true);
  assert.deepStrictEqual([d.accepted, d.dismissed, q.length], [0, 0, 1]);
});

test("a step stuck behind the box says so in plain words", () => {
  const err = Object.assign(new Error("locator.click: Timeout 2500ms exceeded."),
    { dialogPending: true, dialogPendingType: "confirm", dialogPendingMessage: "Delete Grace Hopper?" });
  assert.match(plainCause(err).summary, /^The page asked "Delete Grace Hopper\?" in its own OK\/Cancel box\. .*Cancel was pressed/);
  // An alert is not a question: its own fault path speaks for it.
  const alert = Object.assign(new Error("locator.click: Timeout 2500ms exceeded."), { dialogPending: true, dialogPendingType: "alert" });
  assert.match(plainCause(alert).summary, /didn't respond in time/);
});
