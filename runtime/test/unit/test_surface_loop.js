"use strict";
// EXEC-49 P4: (1) a popup the run keeps answering is a loop, not progress; (2) a popup that opens
// after the fork's watch window is still the fork's popup for the next two steps of the same list.
// Option steps are plain `navigate` steps so the mock page's goto log shows exactly what ran.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-surface-loop-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");

const { runPlan, countSurface } = require("../../app/run");

const POPUP = {
  title: "Upload options",
  text: "report.pdf already exists in this location.",
  controls: [{ name: "Replace", text: "Replace" }, { name: "Keep both", text: "Keep both" }, { name: "Cancel", text: "Cancel" }],
};

const nav = (url) => ({ type: "navigate", url });

function existsState(policy) {
  return {
    id: "exists",
    kind: "dialog",
    signature: { title: "Upload options", button_labels: ["Replace", "Keep both", "Cancel"], message_tokens: ["already", "exists", "location"] },
    options: [
      { id: "replace", label: "Replace", role: "consequential", steps: [nav("https://x.test/replace")] },
      { id: "keep_both", label: "Keep both", role: "neutral", steps: [nav("https://x.test/keep")] },
      { id: "cancel", label: "Cancel", role: "dismiss", steps: [nav("https://x.test/cancel")] },
    ],
    policy,
  };
}

const forkStep = (policy) => ({ type: "outcome_fork", timeout_ms: 1500, unknown_policy: "continue", states: [existsState(policy)] });

// `opens(url)` says whether landing on that url (re)opens the popup. Any other navigation closes it.
function mockPage({ probe = null, opens = () => false } = {}) {
  const page = {
    gotos: [],
    probe,
    waitForLoadState: async () => {},
    context: () => ({ on: () => {} }),
    url: () => "https://x.test",
    evaluate: async () => page.probe,
    goto: async (url) => {
      page.gotos.push(url);
      page.probe = opens(url) ? POPUP : null;
    },
  };
  return page;
}

async function run(page, steps, opts = {}) {
  const events = [];
  const tracker = { emit: (e, f) => { events.push({ e, ...f }); if (opts.onEmit) opts.onEmit(e, f); } };
  await runPlan(page, steps, {}, 0, "surface-loop", { tracker });
  return events;
}

// ─── loop guard ────────────────────────────────────────────────────────────────────────────

test("a popup answered more than twice in one run stops the run as a loop", async () => {
  const page = mockPage({ probe: POPUP, opens: (u) => u.endsWith("/open") });
  const f = forkStep({ mode: "auto", option_id: "keep_both", author_approved: true });
  const steps = [f, nav("https://x.test/open"), f, nav("https://x.test/open"), f, nav("https://x.test/open")];
  await assert.rejects(run(page, steps), (err) => {
    assert.strictEqual(err.forkDecisionNeeded, true);
    assert.match(err.message, /keeps coming back/);
    assert.strictEqual(err.failedAt, 4);
    return true;
  });
  assert.deepStrictEqual(page.gotos.filter((u) => u.endsWith("/keep")), ["https://x.test/keep", "https://x.test/keep"]);
});

test("countSurface: the same popup on different loop rows or in different workflows is not a loop", () => {
  const ctx = { tracker: { emit: () => {} }, slug: "s", callStack: ["a"] };
  const state = { warnings: [] };
  for (let row = 0; row < 6; row++) {
    state.loopCursor = { rowId: `row-${row}` };
    countSurface(ctx, state, { type: "click" }, 0, "exists", "Saved");
  }
  state.loopCursor = { rowId: "row-0" };
  countSurface(ctx, state, { type: "click" }, 0, "exists", "Saved"); // 2nd time on this row: still fine
  assert.throws(() => countSurface(ctx, state, { type: "click" }, 0, "exists", "Saved"), (err) => err.forkDecisionNeeded === true);
  // same popup, same row, but inside a called workflow: counted on its own
  assert.doesNotThrow(() => countSurface({ ...ctx, callStack: ["a", "b"] }, state, { type: "click" }, 0, "exists", "Saved"));
});

test("countSurface ignores quoted names and numbers in the title when counting", () => {
  const ctx = { tracker: { emit: () => {} }, slug: "s", callStack: ["a"] };
  const state = { warnings: [] };
  countSurface(ctx, state, { type: "click" }, 0, "unknown", "Replace 'a.pdf'?");
  countSurface(ctx, state, { type: "click" }, 0, "unknown", "Replace 'b.pdf'?");
  assert.throws(() => countSurface(ctx, state, { type: "click" }, 0, "unknown", "Replace 'c.pdf'?"));
});

// ─── late popups ───────────────────────────────────────────────────────────────────────────

test("a known popup that opens just after the fork's watch window is handled before the next step", async () => {
  const page = mockPage();
  const steps = [forkStep({ mode: "auto", option_id: "keep_both", author_approved: true }), nav("https://x.test/next")];
  // The fork finds nothing (clean); the popup opens right after, before step 1 runs.
  await run(page, steps, { onEmit: (e) => { if (e === "fork_clean") page.probe = POPUP; } });
  assert.deepStrictEqual(page.gotos, ["https://x.test/keep", "https://x.test/next"]);
});

test("a late popup follows the same policy: a consequential option nobody approved stops the run", async () => {
  const page = mockPage();
  const steps = [forkStep({ mode: "auto", option_id: "replace", author_approved: false }), nav("https://x.test/next")];
  await assert.rejects(
    run(page, steps, { onEmit: (e) => { if (e === "fork_clean") page.probe = POPUP; } }),
    (err) => err.forkDecisionNeeded === true && err.failedAt === 1,
  );
  assert.deepStrictEqual(page.gotos, [], "nothing was replaced");
});

test("a late popup whose policy would ask a person stops instead (the resume path is the fork's own)", async () => {
  const page = mockPage();
  const steps = [forkStep({ mode: "ask_human", reviewed: true }), nav("https://x.test/next")];
  await assert.rejects(
    run(page, steps, { onEmit: (e) => { if (e === "fork_clean") page.probe = POPUP; } }),
    (err) => err.forkDecisionNeeded === true && /opened after the step/.test(err.message),
  );
});

test("arming lasts two steps: a popup that opens later is left alone as before", async () => {
  const page = mockPage({ opens: (u) => u.endsWith("/b") });
  const steps = [
    forkStep({ mode: "auto", option_id: "keep_both", author_approved: true }),
    nav("https://x.test/a"), nav("https://x.test/b"), nav("https://x.test/c"),
  ];
  await run(page, steps);
  assert.deepStrictEqual(page.gotos, ["https://x.test/a", "https://x.test/b", "https://x.test/c"]);
});

test("an unknown popup after a clean fork is not acted on (only the fork's own states are)", async () => {
  const page = mockPage();
  const stranger = { title: "Something else", text: "", controls: [{ name: "Do it" }, { name: "Not that" }] };
  const steps = [forkStep({ mode: "auto", option_id: "keep_both", author_approved: true }), nav("https://x.test/next")];
  await run(page, steps, { onEmit: (e) => { if (e === "fork_clean") page.probe = stranger; } });
  assert.deepStrictEqual(page.gotos, ["https://x.test/next"]);
});

test("a dry run never arms a late handler", async () => {
  const page = mockPage();
  const events = [];
  const tracker = { emit: (e, f) => { events.push(e); if (e === "fork_clean") page.probe = POPUP; } };
  await runPlan(page, [forkStep({ mode: "auto", option_id: "keep_both", author_approved: true }), nav("https://x.test/next")], {}, 0, "surface-dry", { tracker, dryRun: true });
  assert.deepStrictEqual(page.gotos, ["https://x.test/next"]);
});
