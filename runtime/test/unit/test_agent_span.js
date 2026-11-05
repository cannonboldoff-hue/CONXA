"use strict";

// Engine v2 C4: the agent_span step. The agent can only nominate one numbered element the runtime listed;
// these tests pin what the runtime refuses (and that a refusal costs no budget), where the span fails
// closed (dry run, scheduled run, loop body, a popup that needs a decision, leaving the site) and that a
// span whose goal already holds does nothing. A fake page stands in for Playwright, as test_ai_review.js does.

const test = require("node:test");
const assert = require("node:assert");

const span = require("../../app/agent_span");
const pageScripts = require("../../app/page_scripts");
const { runPlan } = require("../../app/run");

const STEP = {
  type: "agent_span", goal: "Open the invoice for {{invoice}}", allowed: ["click", "fill", "select", "scroll"],
  until: { type: "text_present", target: "Invoice {{invoice}}", required: true },
  budget: { actions: 3, seconds: 60 }, on_budget: "fail",
};

const CANDS = [
  { i: 0, tag: "a", role: "link", type: "", name: "Invoice INV-7", host: "app.test", disabled: false, value: "" },
  { i: 1, tag: "button", role: "button", type: "", name: "Delete invoice", host: "", disabled: false, value: "" },
  { i: 2, tag: "a", role: "link", type: "", name: "Help centre", host: "other.test", disabled: false, value: "" },
  { i: 3, tag: "input", role: "text", type: "text", name: "Search", host: "", disabled: false, value: "" },
  { i: 4, tag: "input", role: "password", type: "password", name: "Password", host: "", disabled: false, value: "" },
  { i: 5, tag: "button", role: "button", type: "", name: "Next", host: "", disabled: true, value: "" },
  { i: 6, tag: "select", role: "select", type: "", name: "Status", host: "", disabled: false, value: "" },
];

// A page that knows which text is showing and what a click does. `dialog` is what the popup probe returns.
function fakePage({ url = "https://app.test/list", texts = [], onClick = () => {}, dialog = null, count = 1 } = {}) {
  const log = [];
  const page = {
    log, texts: new Set(texts), _url: url,
    url: () => page._url,
    isClosed: () => false,
    evaluate: async (fn) => {
      if (fn === span.gatherCandidates) return CANDS;
      if (fn === pageScripts.dialogProbe) return dialog;
      return { textLen: 100, interactiveCount: 7 };       // preStepSignature
    },
    frames: () => [{
      locator: (sel) => ({ count: async () => (page.texts.has(JSON.parse(sel.replace(/^text=/, ""))) ? 1 : 0) }),
    }],
    locator: (sel) => ({
      count: async () => count,
      click: async () => { log.push(["click", sel]); onClick(page, sel); },
      fill: async (v) => { log.push(["fill", sel, v]); },
      selectOption: async (o) => { log.push(["select", sel, o]); },
    }),
    mouse: { wheel: async (x, y) => { log.push(["wheel", y]); } },
    waitForLoadState: async () => {},
    screenshot: async () => Buffer.from("x"),
  };
  return page;
}

async function started(page, step = STEP, inputs = { invoice: "INV-7" }) {
  const state = await span.begin(page, step, inputs);
  await span.refreshCandidates(page, state);
  return { state, inputs };
}

// ── begin ───────────────────────────────────────────────────────────────────────────────────────

test("begin fills the goal from the run's inputs, keeps only allowed kinds and clamps the budget", async () => {
  const s = await span.begin(fakePage(), { ...STEP, allowed: ["click", "navigate", "scroll"], budget: { actions: 99, seconds: 5000 } }, { invoice: "INV-7" });
  assert.strictEqual(s.goal, "Open the invoice for INV-7");
  assert.deepStrictEqual(s.allowed, ["click", "scroll"]);
  assert.deepStrictEqual(s.budget, { actions: 15, seconds: 600 });
  assert.strictEqual(s.host, "app.test");
});

// ── what is refused ─────────────────────────────────────────────────────────────────────────────

test("a nomination is refused for: an action the step does not allow, a number not in the list, a disabled element", async () => {
  const { state } = await started(fakePage(), { ...STEP, allowed: ["click"] });
  const why = (a) => span.refusalReason(state, a, "app.test");
  assert.match(why({ kind: "fill", candidate_index: 3, value: "x" }), /not an action this step allows/);
  assert.match(why({ kind: "click", candidate_index: 99 }), /not in the element list/);
  assert.match(why({ kind: "click", candidate_index: -1 }), /not in the element list/);
  assert.match(why({ kind: "click", candidate_index: "0" }), /not in the element list/);
  assert.match(why({ kind: "click", candidate_index: 5 }), /disabled/);
  assert.strictEqual(why({ kind: "click", candidate_index: 0 }), "");
  assert.match(why(null), /no action/);
});

test("a button that looks irreversible and a link to another site are never pressed", async () => {
  const { state } = await started(fakePage());
  assert.match(span.refusalReason(state, { kind: "click", candidate_index: 1 }, "app.test"), /cannot be undone/);
  assert.match(span.refusalReason(state, { kind: "click", candidate_index: 2 }, "app.test"), /another site \(other\.test\)/);
});

test("fill only goes into a plain text field, with a short text value", async () => {
  const { state } = await started(fakePage());
  const why = (a) => span.refusalReason(state, a, "app.test");
  assert.strictEqual(why({ kind: "fill", candidate_index: 3, value: "INV-7" }), "");
  assert.match(why({ kind: "fill", candidate_index: 4, value: "secret" }), /not a plain text field/);
  assert.match(why({ kind: "fill", candidate_index: 0, value: "x" }), /not a text field/);
  assert.match(why({ kind: "fill", candidate_index: 3, value: "x".repeat(201) }), /at most 200/);
  assert.match(why({ kind: "fill", candidate_index: 3 }), /at most 200/);
  assert.match(why({ kind: "select", candidate_index: 3, value: "Open" }), /not a drop-down/);
  assert.match(why({ kind: "select", candidate_index: 6 }), /option to pick/);
  assert.strictEqual(why({ kind: "select", candidate_index: 6, value: "Open" }), "");
});

test("a refused nomination does nothing and costs no budget; the fourth refusal ends the span", async () => {
  const page = fakePage();
  const { state, inputs } = await started(page);
  for (let n = 1; n <= span.REFUSAL_LIMIT; n++) {
    const r = await span.applyRound(page, state, { kind: "click", candidate_index: 1 }, inputs);
    assert.strictEqual(r.kind, "refused");
    assert.strictEqual(state.used, 0);
  }
  const last = await span.applyRound(page, state, { kind: "click", candidate_index: 1 }, inputs);
  assert.deepStrictEqual([last.kind, last.code], ["failed", "refused_too_often"]);
  assert.deepStrictEqual(page.log, [], "nothing was clicked");
});

// ── a round ─────────────────────────────────────────────────────────────────────────────────────

test("an accepted click costs one action and the span is done when the author's check holds", async () => {
  const page = fakePage({ onClick: (p) => p.texts.add("Invoice INV-7") });
  const { state, inputs } = await started(page);
  const r = await span.applyRound(page, state, { kind: "click", candidate_index: 0 }, inputs);
  assert.strictEqual(r.kind, "done");
  assert.strictEqual(state.used, 1);
  assert.deepStrictEqual(page.log, [["click", '[data-conxa-span="0"]']]);
});

test("an action that does not reach the goal asks again; fill, select and scroll are carried out", async () => {
  const page = fakePage();
  const { state, inputs } = await started(page);
  assert.strictEqual((await span.applyRound(page, state, { kind: "fill", candidate_index: 3, value: "INV-7" }, inputs)).kind, "continue");
  assert.strictEqual((await span.applyRound(page, state, { kind: "select", candidate_index: 6, value: "Open" }, inputs)).kind, "continue");
  assert.strictEqual((await span.applyRound(page, state, { kind: "scroll", value: "up" }, inputs)).code, "exhausted");   // third action = the budget
  assert.deepStrictEqual(page.log.map((l) => l[0]), ["fill", "select", "wheel"]);
  assert.strictEqual(page.log[2][1], -600);
});

test("running out of actions ends the span with a typed reason", async () => {
  const page = fakePage();
  const { state, inputs } = await started(page);
  let r;
  for (let n = 0; n < 3; n++) r = await span.applyRound(page, state, { kind: "scroll" }, inputs);
  assert.deepStrictEqual([r.kind, r.code], ["failed", "exhausted"]);
});

test("running out of time ends the span too", async () => {
  const page = fakePage();
  const { state, inputs } = await started(page);
  state.startedAt -= 61_000;
  const r = await span.applyRound(page, state, { kind: "scroll" }, inputs);
  assert.deepStrictEqual([r.kind, r.code], ["failed", "exhausted"]);
});

test("the agent giving up ends the span without touching the page", async () => {
  const page = fakePage();
  const { state, inputs } = await started(page);
  const r = await span.applyRound(page, state, { status: "give_up", why: "no such invoice" }, inputs);
  assert.deepStrictEqual([r.kind, r.code], ["failed", "gave_up"]);
  assert.match(r.message, /no such invoice/);
  assert.deepStrictEqual(page.log, []);
});

test("a popup that asks for a decision stops the span and is never answered", async () => {
  const dialog = { title: "File exists", text: "Replace it?", controls: [{ name: "Replace" }, { name: "Keep both" }], hasFields: false };
  const page = fakePage({ dialog });
  const { state, inputs } = await started(page);
  const r = await span.applyRound(page, state, { kind: "click", candidate_index: 0 }, inputs);
  assert.deepStrictEqual([r.kind, r.code], ["failed", "decision_dialog"]);
  assert.deepStrictEqual(page.log, []);
});

test("a click that takes the page to another site ends the span", async () => {
  const page = fakePage({ onClick: (p) => { p._url = "https://evil.test/x"; } });
  const { state, inputs } = await started(page);
  const r = await span.applyRound(page, state, { kind: "click", candidate_index: 0 }, inputs);
  assert.deepStrictEqual([r.kind, r.code], ["failed", "left_host"]);
});

test("an element that vanished since the list was sent is refused as out of date, not clicked", async () => {
  const page = fakePage({ count: 0 });
  const { state, inputs } = await started(page);
  const r = await span.applyRound(page, state, { kind: "click", candidate_index: 0 }, inputs);
  assert.strictEqual(r.kind, "refused");
  assert.match(r.reason, /no longer on the page/);
  assert.strictEqual(state.used, 0);
});

// ── the author's check ──────────────────────────────────────────────────────────────────────────

test("untilHolds: text, address and state checks", async () => {
  const page = fakePage({ texts: ["Saved"], url: "https://app.test/done" });
  const base = { url: "https://app.test/list", textLen: 100, interactiveCount: 7 };
  assert.strictEqual(await span.untilHolds(page, { type: "text_present", target: "Saved" }, {}, base), true);
  assert.strictEqual(await span.untilHolds(page, { type: "text_present", target: "Nope" }, {}, base), false);
  assert.strictEqual(await span.untilHolds(page, { type: "text_absent", target: "Nope" }, {}, base), true);
  assert.strictEqual(await span.untilHolds(page, { type: "text_absent", target: "Saved" }, {}, base), false);
  assert.strictEqual(await span.untilHolds(page, { type: "url_pattern", target: "/done$" }, {}, base), true);
  assert.strictEqual(await span.untilHolds(page, { type: "url_changed" }, {}, base), true);
  assert.strictEqual(await span.untilHolds(page, { type: "state_changed" }, {}, base), true);        // the url differs
  assert.strictEqual(await span.untilHolds(page, { type: "selector_present", target: "#x" }, {}, base), false, "a selector check is not a span check");
  assert.strictEqual(await span.untilHolds(page, null, {}, base), false);
  assert.strictEqual(await span.untilHolds(page, { type: "text_present", target: "" }, {}, base), false);
});

test("the request names the goal, the budget and the numbered elements, and says page text is data", async () => {
  const page = fakePage();
  const { state } = await started(page);
  const resp = await span.buildSpanRequest(page, STEP, 4, state, { refused: "element 1 looks like it cannot be undone" });
  const text = resp.content.filter((c) => c.type === "text").map((c) => c.text).join("\n");
  assert.match(text, /step 5 for an agent span/);
  assert.match(text, /Goal: Open the invoice for INV-7/);
  assert.match(text, /3 actions, 60 s/);
  assert.match(text, /refused and cost nothing: element 1/);
  assert.match(text, /untrusted page data/);
  assert.match(text, /span_actions: \{ "4"/);
  assert.strictEqual(resp._meta["conxa/agent_span"].candidates.length, CANDS.length);
  assert.strictEqual(resp._meta["conxa/agent_span"].step_index, 4);
});

// ── run.js: where the step fails closed ────────────────────────────────────────────────────────

function runnerPage(texts = []) {
  const page = fakePage({ texts });
  page.waitForLoadState = async () => {};
  page.context = () => ({ on: () => {} });
  return page;
}

test("runPlan: a fresh span throws a spanPause carrying its state, never a normal failure", async () => {
  const steps = [{ ...STEP }];
  await assert.rejects(
    () => runPlan(runnerPage(), steps, { invoice: "INV-7" }, 0, "span-fresh"),
    (err) => err.spanPause === true && err.stepIndex === 0 && err.span.goal === "Open the invoice for INV-7"
      && err.span.used === 0 && err.step === steps[0]
  );
});

test("runPlan: a span whose goal already holds does nothing and moves on", async () => {
  const page = runnerPage(["Invoice INV-7"]);
  await runPlan(page, [{ ...STEP }], { invoice: "INV-7" }, 0, "span-already");
  assert.deepStrictEqual(page.log, []);
});

test("runPlan: a resume that bound the done marker consumes it and continues", async () => {
  const inputs = { invoice: "INV-7", __agent_span_done_0: true };
  const steps = [{ ...STEP }, { ...STEP, goal: "second" }];
  await assert.rejects(() => runPlan(runnerPage(), steps, inputs, 0, "span-resume"), (err) => err.spanPause === true && err.stepIndex === 1);
  assert.strictEqual(inputs.__agent_span_done_0, undefined);
});

test("runPlan: a scheduled run (no agent) fails closed instead of pausing for one", async () => {
  await assert.rejects(
    () => runPlan(runnerPage(), [{ ...STEP }], { invoice: "INV-7" }, 0, "span-scheduled", { agentSpans: false }),
    (err) => !err.spanPause && /needs an AI agent/.test(err.message)
  );
});

test("runPlan: a dry run withholds the span and says so", async () => {
  const result = await runPlan(runnerPage(), [{ ...STEP }], { invoice: "INV-7" }, 0, "span-dry", { dryRun: true });
  assert.deepStrictEqual(result.dryRunSkipped.map((s) => s.index), [0]);
});
