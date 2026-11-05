"use strict";
// EXEC-49 P5: an UNRECORDED question popup on a run whose caller can reason is put to that caller
// (the MCP client), never answered by the runtime itself. Only the popup's own visible buttons are
// offered; any popup with an irreversible-looking button, a dry run, a loop or an option body stops
// instead. An agent's answer is checked again at click time. A verdict only ever ends a run.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-agent-popup-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");

const fork = require("../../app/fork");
const { runPlan, askAboutBlockingPopup } = require("../../app/run");
const { buildForkRequest, validateForkAnswer } = require("../../app/fork_pause");
const { applyStepOverrides } = require("../../app/handlers");

const QUESTION = {
  title: "Open in the app?",
  text: "Continue in the desktop app.",
  controls: [{ name: "Open app", text: "Open app" }, { name: "Stay in browser", text: "Stay in browser" }],
};
const REPLACE = {
  title: "Upload options",
  text: "report.pdf already exists.",
  controls: [{ name: "Replace", text: "Replace" }, { name: "Keep both", text: "Keep both" }, { name: "Cancel", text: "Cancel" }],
};
const DELETE = {
  title: "Remove file",
  text: "Remove it for good?",
  controls: [{ name: "Delete", text: "Delete" }, { name: "Cancel", text: "Cancel" }],
};

const forkOf = () => ({
  type: "outcome_fork", timeout_ms: 1500, unknown_policy: "continue",
  states: [{
    id: "other", kind: "dialog",
    signature: { title: "Something else", button_labels: ["Yes", "No"], message_tokens: [] },
    options: [{ id: "yes", label: "Yes", role: "neutral", steps: [] }, { id: "no", label: "No", role: "dismiss", steps: [] }],
    policy: { mode: "continue" },
  }],
});
const nav = (url) => ({ type: "navigate", url });

function mockPage(probe) {
  const page = {
    gotos: [], clicked: [], probe,
    waitForLoadState: async () => {},
    context: () => ({ on: () => {} }),
    url: () => "https://x.test",
    evaluate: async () => page.probe,
    goto: async (url) => { page.gotos.push(url); },
    locator: () => ({
      last: () => ({
        getByRole: (role, opts) => ({
          first: () => ({ click: async () => { page.clicked.push(opts.name); page.probe = null; } }),
        }),
      }),
    }),
  };
  return page;
}

async function run(page, steps, inputs = {}, extra = {}) {
  const events = [];
  const tracker = { emit: (e, f) => events.push({ e, ...f }) };
  const result = await runPlan(page, steps, inputs, 0, "agent-popup", { tracker, ...extra });
  return { result, events };
}

// ─── fork.agentPopupChoices ────────────────────────────────────────────────────────────────

test("agentPopupChoices offers only the visible buttons plus stop", () => {
  const { choices, blockedBy } = fork.agentPopupChoices(QUESTION);
  assert.strictEqual(blockedBy, null);
  assert.deepStrictEqual(choices.map((c) => c.id), ["Open app", "Stay in browser", "__stop__"]);
});

test("agentPopupChoices offers nothing when any button looks irreversible", () => {
  const { choices, blockedBy } = fork.agentPopupChoices(DELETE);
  assert.strictEqual(blockedBy, "Delete");
  assert.deepStrictEqual(choices, []);
});

// ─── entry (a): an unrecorded popup at a fork ──────────────────────────────────────────────

test("an unrecorded question popup at a fork is put to the caller, with only its own buttons", async () => {
  const page = mockPage(QUESTION);
  await assert.rejects(run(page, [forkOf(), nav("https://x.test/next")], {}, { answerPopups: true }), (err) => {
    assert.strictEqual(err.forkPause, true);
    assert.strictEqual(err.info.agent, true);
    assert.strictEqual(err.stepIndex, 0);
    assert.deepStrictEqual(err.info.choices.map((c) => c.id), ["Open app", "Stay in browser", "__stop__"]);
    return true;
  });
  assert.deepStrictEqual(page.clicked, [], "nothing is clicked before the caller answers");
});

test("without a caller that can reason, the popup is left alone as before", async () => {
  const page = mockPage(QUESTION);
  const { result } = await run(page, [forkOf(), nav("https://x.test/next")], {}, { answerPopups: false });
  assert.deepStrictEqual(page.gotos, ["https://x.test/next"]);
  assert.ok(result.warnings.some((w) => /left alone/.test(w)));
});

test("a popup with an irreversible-looking button is never put to the caller: the run stops", async () => {
  const page = mockPage(DELETE);
  await assert.rejects(run(page, [forkOf()], {}, { answerPopups: true }), (err) => {
    assert.strictEqual(err.forkDecisionNeeded, true);
    assert.notStrictEqual(err.forkPause, true);
    assert.match(err.message, /can't be undone \("Delete"\)/);
    return true;
  });
});

test("a recorded fork's own unknown_policy: stop is respected (the caller is not asked)", async () => {
  const page = mockPage(QUESTION);
  const f = { ...forkOf(), unknown_policy: "stop" };
  await assert.rejects(run(page, [f], {}, { answerPopups: true }), (err) => err.forkDecisionNeeded === true && err.forkPause !== true);
});

test("a dry run and a loop body stop instead of asking", async () => {
  await assert.rejects(run(mockPage(QUESTION), [forkOf()], {}, { answerPopups: true, dryRun: true }), (err) => err.forkDecisionNeeded === true);
  const loop = { type: "for_each", items: "files", as: "f", max_iterations: 2, steps: [forkOf()] };
  await assert.rejects(run(mockPage(QUESTION), [loop], { files: "a,b" }, { answerPopups: true }), (err) => err.forkPause !== true);
});

test("a recorded popup (a state the fork lists) is not put to the caller", async () => {
  const page = mockPage({ title: "Something else", text: "", controls: [{ name: "Yes" }, { name: "No" }] });
  const { events } = await run(page, [forkOf(), nav("https://x.test/next")], {}, { answerPopups: true });
  assert.ok(!events.some((e) => e.e === "fork_paused"));
  assert.deepStrictEqual(page.gotos, ["https://x.test/next"]);
});

// ─── entry (b): a plain step blocked by a question popup ───────────────────────────────────

test("a failed step with a question popup in the way asks the caller, and remembers the step had acted", async () => {
  const page = mockPage(QUESTION);
  const ctx = { answerPopups: true, tracker: { emit: () => {} }, slug: "s" };
  await assert.rejects(
    askAboutBlockingPopup(ctx, { warnings: [] }, { type: "click", intent: "click_save" }, page, 3, { verifyFail: true }),
    (err) => err.forkPause === true && err.stepIndex === 3 && err.info.acted === true && err.info.stepIntent === "click_save",
  );
});

test("askAboutBlockingPopup does nothing for a destructive step, a quiet run, or a clean page", async () => {
  const ctx = { answerPopups: true, tracker: { emit: () => {} }, slug: "s" };
  await askAboutBlockingPopup(ctx, { warnings: [] }, { type: "click", destructive: true }, mockPage(QUESTION), 0, {});
  await askAboutBlockingPopup({ ...ctx, answerPopups: false }, { warnings: [] }, { type: "click" }, mockPage(QUESTION), 0, {});
  await askAboutBlockingPopup(ctx, { warnings: [] }, { type: "click" }, mockPage(null), 0, {});
});

// ─── the answer ────────────────────────────────────────────────────────────────────────────

test("resume at a fork: the caller's button is clicked, then the run goes on", async () => {
  const page = mockPage(QUESTION);
  await run(page, [forkOf(), nav("https://x.test/next")], { __fork_choice_0: "Stay in browser", __fork_agent_0: 1 }, { answerPopups: true });
  assert.deepStrictEqual(page.clicked, ["Stay in browser"]);
  assert.deepStrictEqual(page.gotos, ["https://x.test/next"]);
});

test("resume at a plain step whose action had not run: the popup is answered, then the step runs", async () => {
  const page = mockPage(QUESTION);
  const { result } = await run(page, [nav("https://x.test/a")], { __fork_choice_0: "Open app", __fork_agent_0: 1 }, { answerPopups: true });
  assert.deepStrictEqual(page.clicked, ["Open app"]);
  assert.deepStrictEqual(page.gotos, ["https://x.test/a"]);
  assert.ok(result.warnings.some((w) => /answered with "Open app" by the assistant/.test(w)));
});

test("resume at a plain step whose action HAD run: the popup is answered and the step is NOT run again", async () => {
  const page = mockPage(QUESTION);
  await run(page, [nav("https://x.test/a")], { __fork_choice_0: "Open app", __fork_agent_0: 1, __fork_acted_0: 1 }, { answerPopups: true });
  assert.deepStrictEqual(page.clicked, ["Open app"]);
  assert.deepStrictEqual(page.gotos, [], "the navigation was not repeated");
});

test("an agent's choice of an irreversible button is refused even if the popup changed since it was offered", async () => {
  const page = mockPage(DELETE);
  await assert.rejects(
    run(page, [nav("https://x.test/a")], { __fork_choice_0: "Delete", __fork_agent_0: 1 }, { answerPopups: true }),
    (err) => err.forkDecisionNeeded === true && /can't be undone/.test(err.message),
  );
  assert.deepStrictEqual(page.clicked, []);
});

test("an answer naming a button that is no longer on screen fails closed; stop ends the run", async () => {
  await assert.rejects(
    run(mockPage(QUESTION), [nav("https://x.test/a")], { __fork_choice_0: "Maybe later", __fork_agent_0: 1 }, { answerPopups: true }),
    (err) => err.badInput === true,
  );
  await assert.rejects(
    run(mockPage(QUESTION), [nav("https://x.test/a")], { __fork_choice_0: "__stop__", __fork_agent_0: 1 }, { answerPopups: true }),
    (err) => err.forkDecisionNeeded === true,
  );
});

test("if the popup is already gone when the answer arrives, the run just goes on", async () => {
  const page = mockPage(null);
  await run(page, [nav("https://x.test/a")], { __fork_choice_0: "Open app", __fork_agent_0: 1 }, { answerPopups: true });
  assert.deepStrictEqual(page.clicked, []);
  assert.deepStrictEqual(page.gotos, ["https://x.test/a"]);
});

// ─── the request text ──────────────────────────────────────────────────────────────────────

test("buildForkRequest (agent): not human-only, names the goal, tells the caller to ask the user when unsure", async () => {
  const info = { title: "Open in the app?", text: "Continue.", reason: "agent_decides", agent: true, acted: true, stepIntent: "click_save", choices: fork.agentPopupChoices(QUESTION).choices };
  const resp = await buildForkRequest({ url: () => "https://x.test", screenshot: async () => null }, 2, info, { workflow: "Save report", goal: "Save the weekly report" });
  const text = resp.content[0].text;
  assert.match(text, /Workflow: Save report -- Save the weekly report/);
  assert.match(text, /Step: click_save/);
  assert.match(text, /ASK THE USER in the chat before answering/);
  assert.match(text, /re-checked, not repeated/);
  assert.strictEqual(resp._meta["conxa/fork"].requires_human, false);
  assert.strictEqual(resp._meta["conxa/fork"].agent, true);
  assert.strictEqual(validateForkAnswer({ choice: "Stay in browser" }, info.choices.map((c) => c.id)).valid, true);
  assert.strictEqual(validateForkAnswer({ choice: "Delete" }, info.choices.map((c) => c.id)).valid, false);
});

test("buildForkRequest (human pause) is unchanged: still human-only", async () => {
  const resp = await buildForkRequest({ url: () => "https://x.test", screenshot: async () => null }, 1, { title: "T", choices: [{ id: "a", label: "A", role: "neutral" }] }, {});
  assert.strictEqual(resp._meta["conxa/fork"].requires_human, true);
});

// ─── G8: the verdict ───────────────────────────────────────────────────────────────────────

test("applyStepOverrides turns a verdict into a marker and ignores unknown kinds", () => {
  const steps = [{ type: "click" }, { type: "click" }];
  const out = applyStepOverrides(steps, { 0: { verdict: { kind: "fault", why: "  The app\nsays no access  " } }, 1: { verdict: { kind: "obstruction", why: "x" } } });
  assert.deepStrictEqual(out[0]._agent_verdict, { kind: "fault", why: "The app says no access" });
  assert.strictEqual(out[0]._agent_override, true);
  assert.strictEqual(out[1]._agent_verdict, undefined);
});

test("a verdict ends the run with the agent's reason and never acts on the page", async () => {
  const page = mockPage(null);
  const step = { ...nav("https://x.test/a"), _agent_verdict: { kind: "fault", why: "The app says the account is locked" } };
  await assert.rejects(run(page, [step]), (err) => err.fault === true && err.faultKind === "fault" && /account is locked/.test(err.message));
  assert.deepStrictEqual(page.gotos, []);
  const decision = { ...nav("https://x.test/a"), _agent_verdict: { kind: "decision", why: "It needs the user" } };
  await assert.rejects(run(mockPage(null), [decision]), (err) => err.forkDecisionNeeded === true);
});
