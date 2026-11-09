"use strict";
// "Go and look" (Proof of Done stage 5): open the same page again in a separate tab and see whether an uploaded file's
// name is on it, and more times than before. Read-only, never on the step's own tab, and it cleans up after itself.
const test = require("node:test");
const assert = require("node:assert");
const { EventEmitter } = require("node:events");

const pageScripts = require("../../app/page_scripts");
const evidence = require("../../app/evidence");
const { freshLook } = require("../../app/fresh_look");
const { evaluateChecks } = require("../../app/effect_probes");

// A page whose context can open one more tab. `counts` is what that tab reports, call after call (the last repeats).
function setup({ counts = [3], url = "https://drive.test/folder", newPageError = null } = {}) {
  const log = { goto: [], closed: false, front: 0, evaluated: [] };
  let calls = 0;
  const tab = {
    goto: async (u) => { log.goto.push(u); },
    evaluate: async (script, arg) => { log.evaluated.push([script === pageScripts.countTextOccurrences, arg]); return counts[Math.min(calls++, counts.length - 1)]; },
    close: async () => { log.closed = true; },
  };
  const page = new EventEmitter();
  Object.assign(page, {
    url: () => url,
    bringToFront: async () => { log.front++; },
    context: () => ({ newPage: async () => { if (newPageError) throw new Error(newPageError); return tab; } }),
  });
  const registry = { pendingPages: [tab] }; // the context's "page" event queued the new tab here
  return { page, tab, registry, log };
}
const look = (s, over = {}) => freshLook({ page: s.page, registry: s.registry, fileName: "report.pdf", countBefore: 1, maxMs: 120, pollMs: 10, ...over });

test("the file name appears more often than before: yes, and the tab is read-only and cleaned up", async () => {
  const s = setup({ counts: [1, 1, 2] });
  const r = await look(s);
  assert.deepStrictEqual(r, { answer: "yes" });
  assert.deepStrictEqual(s.log.goto, ["https://drive.test/folder"], "it loads the address the step is already on, nothing else");
  assert.ok(s.log.evaluated.every(([isCount, arg]) => isCount && arg === "report.pdf"), "it only counts the name; it never clicks or types");
  assert.strictEqual(s.log.closed, true);
  assert.deepStrictEqual(s.registry.pendingPages, [], "the run can never mistake it for a tab the site opened");
  assert.strictEqual(s.log.front, 1, "the step's own tab gets the foreground back");
});

test("a file that was already there before the upload proves nothing", async () => {
  const s = setup({ counts: [1] });
  assert.deepStrictEqual(await look(s), { answer: "no" });
  assert.strictEqual(s.log.closed, true);
});

test("a name that never shows up within the time allowed is 'no'", async () => {
  const s = setup({ counts: [0] });
  assert.deepStrictEqual(await look(s, { countBefore: 0 }), { answer: "no" });
});

test("where a tab cannot be opened, or the name or address is unusable, the answer is 'cant_tell' and nothing is opened", async () => {
  const blocked = setup({ newPageError: "Electron has no Target.createTarget" });
  assert.deepStrictEqual(await look(blocked), { answer: "cant_tell" });
  const noName = setup();
  assert.deepStrictEqual(await look(noName, { fileName: null }), { answer: "cant_tell" });
  assert.deepStrictEqual(await look(noName, { countBefore: null }), { answer: "cant_tell" });
  assert.strictEqual(noName.log.goto.length, 0);
  const blank = setup({ url: "about:blank" });
  assert.deepStrictEqual(await look(blank), { answer: "cant_tell" });
  assert.strictEqual(blank.log.goto.length, 0);
});

test("a page that fails to load is 'cant_tell', and the tab is still closed", async () => {
  const s = setup();
  s.tab.goto = async () => { throw new Error("net::ERR_NAME_NOT_RESOLVED"); };
  assert.deepStrictEqual(await look(s), { answer: "cant_tell" });
  assert.strictEqual(s.log.closed, true);
});

test("countTextOccurrences counts a name case-insensitively in the visible text", () => {
  global.document = { body: { innerText: "Report.pdf  report.PDF  other.txt" } };
  try {
    assert.strictEqual(pageScripts.countTextOccurrences("report.pdf"), 2);
    assert.strictEqual(pageScripts.countTextOccurrences("nope.pdf"), 0);
    assert.strictEqual(pageScripts.countTextOccurrences(""), 0);
    assert.doesNotThrow(() => new Function(`return (${pageScripts.countTextOccurrences.toString()})`)(), "re-parsable in the page");
  } finally {
    delete global.document;
  }
});

test("the notebook counts the name on the page BEFORE the action", async () => {
  const page = new EventEmitter();
  Object.assign(page, {
    url: () => "https://drive.test/folder",
    mainFrame: () => "main",
    evaluate: async (script, arg) => (script === pageScripts.countTextOccurrences && arg === "report.pdf" ? 2 : { busyCount: 0, regions: [] }),
  });
  const nb = await evidence.open({ page, pagesOf: () => [page], fileName: "report.pdf" });
  assert.strictEqual(nb.nameCountBefore, 2);
  nb.close();
  const none = await evidence.open({ page, pagesOf: () => [page] });
  assert.strictEqual(none.nameCountBefore, null, "no name, no count");
  none.close();
});

test("the fresh answer reaches the checklist, and without one the check cannot say yes", () => {
  const effect = { checks: [{ id: "c1", rung: "result_exists", witness: "fresh_look", role: "support", probe: { type: "fresh_look", expect: "file_name" } }] };
  assert.strictEqual(evaluateChecks(effect, { fresh: { answer: "yes" } })[0].answer, "yes");
  assert.strictEqual(evaluateChecks(effect, {})[0].answer, "cant_tell");
  effect.checks[0].calibrated = false;
  assert.deepStrictEqual(evaluateChecks(effect, { fresh: { answer: "yes" } }), [], "a check shown to be dead no longer counts");
});
