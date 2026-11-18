"use strict";
// Phase 0 of "evidence must be linked to the action". Each case reproduces one of the three wrong verdicts from the
// GitHub -> Google Drive test runs on a static page (no site involved), measured in real headless Chromium:
//   (c) a popover mounted OUTSIDE a fixed window-sized app shell read as a page-blocking backdrop (blocks: true)
//   (b) a finished upload tray whose progress bar stays on screen kept the completion gate "busy" forever
//   (a) a hover card (non-modal dialog opened by a pointer, not by the step) read as a question for the person
// These tests describe the CORRECT behaviour; they fail on the code that produced the bad verdicts.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const pageScripts = require("../../app/page_scripts");
const fork = require("../../app/fork");

const SHELL = `<div id="shell" style="position:fixed;inset:0;overflow:auto"><p>${"The whole application lives in this shell. ".repeat(60)}</p>
  <div id="row" style="margin:40px;width:300px;height:40px;background:#eee">Sass.gitignore</div></div>`;

// A hover card mounted on <body>, outside the shell (Drive mounts its popovers this way).
const HOVER_CARD = `<div role="dialog" aria-label="Sass.gitignore" style="position:fixed;left:40px;top:120px;width:360px;height:260px;background:#fff">
  <b>Sass.gitignore</b><button aria-label="Add to starred">Add to starred</button><button>Sass.gitignore</button></div>`;

async function withPage(html, fn) {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
    await page.setContent(`<!doctype html><html><body style="margin:0;font:14px sans-serif">${html}</body></html>`);
    return await fn(page);
  } finally {
    await browser.close();
  }
}

test("(c) a popover mounted outside a window-sized app shell does not block the page", async () => {
  await withPage(SHELL + HOVER_CARD, async (page) => {
    const probe = await page.evaluate(pageScripts.dialogProbe);
    assert.ok(probe, "the card is seen");
    assert.strictEqual(probe.blocks, false, `blocks_reason=${probe.blocks_reason}`);
  });
});

test("(c) a real backdrop outside the shell still blocks", async () => {
  await withPage(SHELL + `<div style="position:fixed;inset:0;background:rgba(0,0,0,.5)"></div>${HOVER_CARD}`, async (page) => {
    const probe = await page.evaluate(pageScripts.dialogProbe);
    assert.strictEqual(probe.blocks, true);
  });
});

test("(c) dialogProbe names the branch that decided `blocks`", async () => {
  await withPage(SHELL + HOVER_CARD, async (page) => {
    const probe = await page.evaluate(pageScripts.dialogProbe);
    assert.strictEqual(typeof probe.blocks_reason, "string");
  });
});

test("(a) with blocks measured false, the hover card is not a blocking decision", async () => {
  await withPage(SHELL + HOVER_CARD, async (page) => {
    const probe = await page.evaluate(pageScripts.dialogProbe);
    assert.strictEqual(fork.isBlockingDecision(probe), false);
  });
});

// ── (b) motion: a bar that only stays on screen is not work ─────────────────────────────────────────────────
const pageStatus = require("../../app/page_status");
const { waitForCompletion } = require("../../app/completion");

const gateOpts = (baseline, over) => ({ baseline, sinceMs: 0, mode: "full", budgetMs: 6000, pendingBudgetMs: 6000, pollMs: 25, quietMs: 60, startGraceMs: 400, ...over });

// Drive's upload tray: a dialog that says "1 upload complete" and keeps a per-file progress bar (data-progress=100).
const FINISHED_TRAY = `<div role="dialog" aria-label="Uploading 1 item" style="position:fixed;right:16px;bottom:16px;width:300px;height:90px;background:#fff">
  <b>1 upload complete</b><div role="progressbar" data-progress="100" aria-valuemin="0" aria-valuemax="100" style="width:50px;height:6px;background:#38f"></div></div>`;

async function withGatePage(fn) {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
    pageStatus.attach(page);
    await page.setContent(`<!doctype html><html><body><main><p>The page.</p></main></body></html>`);
    const baseline = { ...(await page.evaluate(pageScripts.completionProbe)), url: page.url() };
    return await fn(page, baseline);
  } finally {
    await browser.close();
  }
}

test("(b) a finished tray whose progress bar stays on screen is settled, and is not counted as a yes", async () => {
  await withGatePage(async (page, baseline) => {
    await page.evaluate((html) => { setTimeout(() => document.body.insertAdjacentHTML("beforeend", html), 80); }, FINISHED_TRAY);
    const v = await waitForCompletion(page, gateOpts(baseline, { stallMs: 700 }));
    assert.strictEqual(v.status, "done", JSON.stringify(v));
    assert.strictEqual(v.still, true, "quiet is not proof: the caller must count it as cant_tell");
    assert.match(v.busyWhat, /progressbar/);
    assert.ok(v.waitedMs < 4000, `waited ${v.waitedMs}ms`);
  });
});

test("(b) without a stall window the same page is waited out to the budget (today's behaviour, unchanged)", async () => {
  await withGatePage(async (page, baseline) => {
    await page.evaluate((html) => { setTimeout(() => document.body.insertAdjacentHTML("beforeend", html), 80); }, FINISHED_TRAY);
    const v = await waitForCompletion(page, gateOpts(baseline, { budgetMs: 900 }));
    assert.strictEqual(v.status, "timed_out");
  });
});

test("(b) a bar that really moves, then leaves, is a yes (the work was seen to end)", async () => {
  await withGatePage(async (page, baseline) => {
    await page.evaluate(() => {
      document.body.insertAdjacentHTML("beforeend", '<div role="progressbar" data-progress="0" aria-valuemin="0" aria-valuemax="100" style="width:50px;height:6px"></div>');
      let n = 0;
      const t = setInterval(() => {
        const bar = document.querySelector('[role="progressbar"]');
        n += 20;
        if (n >= 100) { clearInterval(t); bar.remove(); } else bar.setAttribute("data-progress", String(n));
      }, 150);
    });
    const v = await waitForCompletion(page, gateOpts(baseline, { stallMs: 700 }));
    assert.strictEqual(v.status, "done", JSON.stringify(v));
    assert.notStrictEqual(v.still, true);
  });
});

test("(b) a bar that moves slowly is not stalled: every change restarts the stall window", async () => {
  await withGatePage(async (page, baseline) => {
    await page.evaluate(() => {
      document.body.insertAdjacentHTML("beforeend", '<div role="progressbar" data-progress="0" aria-valuemin="0" aria-valuemax="100" style="width:50px;height:6px"></div>');
      let n = 0;
      const t = setInterval(() => {
        const bar = document.querySelector('[role="progressbar"]');
        n += 10;
        if (n >= 100) { clearInterval(t); bar.remove(); } else bar.setAttribute("data-progress", String(n));
      }, 300); // a change every 300ms for 3s, with a 700ms stall window
    });
    const v = await waitForCompletion(page, gateOpts(baseline, { stallMs: 700 }));
    assert.strictEqual(v.status, "done", JSON.stringify(v));
    assert.notStrictEqual(v.still, true, "it moved the whole time");
    assert.ok(v.waitedMs >= 2500, `ended early after ${v.waitedMs}ms`);
  });
});

// ── (a) park-and-recheck: a surface that a resting pointer holds open goes away when the pointer does ──────────
process.env.CONXA_DATA_DIR = process.env.CONXA_DATA_DIR || require("node:os").tmpdir();
const { parkAndRecheck } = require("../../app/run");

// A card that covers the page (so it "blocks") but exists only while the pointer is over #row. Hover-owned.
// The row stays above the card's backdrop (as a real hover card leaves its item reachable), so the pointer keeps resting on it.
const HOVER_BLOCKER = `<div id="row" style="position:relative;z-index:10;margin:40px;width:300px;height:40px;background:#eee">Sass.gitignore</div>
  <script>
    const row = document.getElementById("row");
    const open = () => { if (document.getElementById("card")) return;
      document.body.insertAdjacentHTML("beforeend", '<div id="card" style="position:fixed;inset:0;z-index:5;background:rgba(0,0,0,.2)"><div role="dialog" aria-label="Sass.gitignore" style="position:fixed;left:30%;top:30%;width:40%;height:30%;background:#fff"><b>Sass.gitignore</b><button>Add to starred</button><button>Open</button></div></div>'); };
    row.addEventListener("mouseenter", open);
    document.addEventListener("mousemove", (e) => { const c = document.getElementById("card"); if (c && !e.target.closest("#card, #row")) { /* stays: only a leave of #row closes it */ } });
    row.addEventListener("mouseleave", () => { const c = document.getElementById("card"); if (c) c.remove(); });
  </script>`;

// A real question that no pointer has anything to do with.
const REAL_QUESTION = `<div style="position:fixed;inset:0;background:rgba(0,0,0,.5)"></div>
  <div role="dialog" aria-label="Replace file?" style="position:fixed;left:30%;top:30%;width:40%;height:30%;background:#fff"><b>Replace file?</b><button>Replace</button><button>Keep both</button></div>`;

test("(a) a blocking surface held open by the resting pointer is gone after the pointer is parked", async () => {
  await withPage(HOVER_BLOCKER, async (page) => {
    await page.hover("#row"); // the pointer rests on the row; the card opens
    await page.waitForSelector("#card");
    const before = await page.evaluate(pageScripts.dialogProbe);
    assert.strictEqual(fork.isBlockingDecision(before), true, "it looks like a blocking question while hovered");
    const after = await parkAndRecheck(page, null, before);
    assert.strictEqual(after, null, "the card closed once the pointer left");
  });
});

test("(a) a real question is still there after the pointer is parked", async () => {
  await withPage(REAL_QUESTION, async (page) => {
    const before = await page.evaluate(pageScripts.dialogProbe);
    const after = await parkAndRecheck(page, null, before);
    assert.ok(after && fork.isBlockingDecision(after), "a real question stays, so the run still stops on it");
  });
});

test("(a) a native dialog is never excused by parking", async () => {
  const native = { native: true, title: "confirm", text: "Sure?", controls: [{ name: "OK" }, { name: "Cancel" }] };
  assert.strictEqual(await parkAndRecheck({}, null, native), native);
});

// ── contrast at run time: a text the page already showed cannot prove the action ───────────────────────────────
const { evaluateAssertion, capturePreStepSignature, watchedTexts } = require("../../app/assertions");

const LIST = `<ul><li>Sass.gitignore</li><li>Ada.gitignore</li></ul>`;
const BOUND = { type: "text_present", target: "{{file}}", required: true, timeout_ms: 600 };

test("contrast: a bound text that was already on the page, with nothing new, answers cant_tell (and still passes)", async () => {
  await withPage(LIST, async (page) => {
    const base = await capturePreStepSignature(page, ["Ada.gitignore"]);
    assert.strictEqual(base.counts["Ada.gitignore"], 1);
    const r = await evaluateAssertion([page], page, BOUND, { file: "Ada.gitignore" }, base);
    assert.strictEqual(r.ok, true);
    assert.strictEqual(r.answer, "cant_tell");
  });
});

test("contrast: the same text appearing once more is a yes", async () => {
  await withPage(LIST, async (page) => {
    const base = await capturePreStepSignature(page, ["Ada.gitignore"]);
    await page.evaluate(() => document.querySelector("ul").insertAdjacentHTML("beforeend", "<li>Ada.gitignore</li>"));
    const r = await evaluateAssertion([page], page, BOUND, { file: "Ada.gitignore" }, base);
    assert.strictEqual(r.answer, "yes");
  });
});

test("contrast: a text that was not there before and is now is a yes; one still missing is a no", async () => {
  await withPage(LIST, async (page) => {
    const base = await capturePreStepSignature(page, ["New.gitignore"]);
    await page.evaluate(() => document.querySelector("ul").insertAdjacentHTML("beforeend", "<li>New.gitignore</li>"));
    assert.strictEqual((await evaluateAssertion([page], page, BOUND, { file: "New.gitignore" }, base)).answer, "yes");
    assert.strictEqual((await evaluateAssertion([page], page, BOUND, { file: "Nope.gitignore" }, base)).answer, "no");
  });
});

test("a text check with no count from before the step passes but proves nothing; every text check is watched", async () => {
  await withPage(LIST, async (page) => {
    const step = { validation: { assertions: [{ type: "text_present", target: "download" }, BOUND, { type: "selector_present", target: "#x" }] } };
    assert.deepStrictEqual(watchedTexts(step, { file: "X" }), ["download", "X"]);
    // WF-8 8.13: it would have said "yes" had the action done nothing, so it is no witness (CLAUDE.md).
    const r = await evaluateAssertion([page], page, { type: "text_present", target: "Ada.gitignore", timeout_ms: 600 }, {}, null);
    assert.deepStrictEqual([r.ok, r.answer], [true, "cant_tell"]);
  });
});

test("contrast: the clicked control's own label (a plain, unbound text) cannot prove the click", async () => {
  await withPage(`<button>Download</button><p>Some page.</p>`, async (page) => {
    const base = await capturePreStepSignature(page, ["Download"]);
    const r = await evaluateAssertion([page], page, { type: "text_present", target: "Download", required: false, timeout_ms: 600 }, {}, base);
    assert.strictEqual(r.answer, "cant_tell");
  });
});
