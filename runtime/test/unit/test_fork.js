"use strict";

// Outcome forks: fork.js (pure state matching + policy) and run.js::runForkStep (observe the page,
// apply the policy, run the chosen option through the real executeOneStep). Option steps here are
// plain `navigate` steps: the mock page records which URL it was sent to, which tells us exactly
// which option ran without simulating the click/GATE chain (test_for_each.js uses the same idea).

const test = require("node:test");
const assert = require("node:assert");

const fork = require("../../app/fork");
const { runPlan } = require("../../app/run");

const idleContext = { on: () => {} };

// ─── fixtures ──────────────────────────────────────────────────────────────────────────────

const EXISTS_PROBE = {
  title: "Upload options",
  text: "report.pdf already exists in this location.",
  controls: [
    { name: "Replace", text: "Replace" },
    { name: "Keep both", text: "Keep both" },
    { name: "Cancel", text: "Cancel" },
  ],
};

function nav(url, extra = {}) { return { type: "navigate", url, ...extra }; }

function existsState(policy, extra = {}) {
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
    ...extra,
  };
}

function forkStep(states, extra = {}) {
  return { type: "outcome_fork", timeout_ms: 1500, unknown_policy: "ask_human", states, ...extra };
}

// `probe` is what dialogProbe sees; any navigation (an option running) closes it unless
// `stayOpen`. gotos records where the run went.
function mockPage({ probe = null, stayOpen = false, gotoThrows = false } = {}) {
  const page = {
    gotos: [],
    probe,
    waitForLoadState: async () => {},
    context: () => idleContext,
    url: () => "https://x.test",
    evaluate: async () => page.probe,
    goto: async (url) => {
      if (gotoThrows) throw new Error("net::ERR_FAILED");
      page.gotos.push(url);
      if (!stayOpen) page.probe = null;
    },
  };
  return page;
}

function recordingTracker() {
  const events = [];
  return { events, emit: (e, f) => events.push({ e, ...f }) };
}

async function run(page, steps, inputs = {}, opts = {}) {
  const tracker = recordingTracker();
  const result = await runPlan(page, steps, inputs, 0, "fork-test", { tracker, ...opts });
  return { result, tracker };
}

const rejectsWith = (re) => (err) => { assert.match(String(err.message), re); return true; };

// ─── fork.js: matchState ───────────────────────────────────────────────────────────────────

test("matchState: no dialog open means the clean state", () => {
  assert.deepStrictEqual(fork.matchState(null, [existsState({ mode: "stop" })]), { kind: "clean" });
});

test("matchState: recognises the dialog by title and button labels, ignoring the file name in the message", () => {
  const state = existsState({ mode: "stop" });
  const probe = { ...EXISTS_PROBE, text: "q3-forecast.xlsx already exists in this location." };
  const seen = fork.matchState(probe, [state]);
  assert.strictEqual(seen.kind, "state");
  assert.strictEqual(seen.state.id, "exists");
});

test("matchState: a missing button means unknown, not a loose match", () => {
  const probe = { ...EXISTS_PROBE, controls: [{ name: "Replace" }, { name: "Cancel" }] };
  assert.strictEqual(fork.matchState(probe, [existsState({ mode: "stop" })]).kind, "unknown");
});

test("matchState: a different title with the same buttons is unknown", () => {
  const probe = { ...EXISTS_PROBE, title: "Discard changes?" };
  assert.strictEqual(fork.matchState(probe, [existsState({ mode: "stop" })]).kind, "unknown");
});

test("matchState: the exact button set beats a contained subset", () => {
  const small = { id: "small", signature: { title: "Upload options", button_labels: ["Replace", "Cancel"] }, options: [], policy: { mode: "stop" } };
  const full = existsState({ mode: "stop" });
  const seen = fork.matchState(EXISTS_PROBE, [small, full]);
  assert.strictEqual(seen.state.id, "exists");
});

test("matchState: two equally good states are unknown, never first-wins", () => {
  const a = existsState({ mode: "stop" }, { id: "a" });
  const b = existsState({ mode: "stop" }, { id: "b" });
  assert.strictEqual(fork.matchState(EXISTS_PROBE, [a, b]).kind, "unknown");
});

// ─── fork.js: decide ───────────────────────────────────────────────────────────────────────

test("decide: auto runs a neutral option", () => {
  const d = fork.decide(existsState({ mode: "auto", option_id: "keep_both" }), {});
  assert.strictEqual(d.do, "run");
  assert.strictEqual(d.option.id, "keep_both");
});

test("decide: auto on a consequential option without author approval pauses (fails closed)", () => {
  const d = fork.decide(existsState({ mode: "auto", option_id: "replace" }), {});
  assert.strictEqual(d.do, "pause");
  assert.strictEqual(d.reason, "consequential_unapproved");
});

test("decide: auto on a consequential option runs once the author approved it", () => {
  const d = fork.decide(existsState({ mode: "auto", option_id: "replace", author_approved: true }), {});
  assert.strictEqual(d.do, "run");
});

test("decide: by_input maps the caller's value to an option, loosely on case", () => {
  const state = existsState({ mode: "by_input", input: "on_duplicate" });
  const d = fork.decide(state, { on_duplicate: "KEEP BOTH" });
  assert.strictEqual(d.option.id, "keep_both");
  assert.strictEqual(d.source, "input");
});

test("decide: by_input with an unknown value is a bad-input error listing the valid choices", () => {
  const state = existsState({ mode: "by_input", input: "on_duplicate" });
  assert.throws(() => fork.decide(state, { on_duplicate: "yolo" }), (e) => e.badInput && /Keep both/.test(e.message));
});

test("decide: by_input defaults to asking a person", () => {
  const state = existsState({ mode: "by_input", input: "on_duplicate" });
  assert.strictEqual(fork.decide(state, {}).do, "pause");
  assert.strictEqual(fork.decide(state, { on_duplicate: "ask" }).do, "pause");
});

test("decide: a pause degrades to a stop inside a loop, where a person can't be asked", () => {
  const d = fork.decide(existsState({ mode: "ask_human" }), {}, { loopDepth: 1 });
  assert.strictEqual(d.do, "stop");
});

test("decide: stop mode carries its message", () => {
  const d = fork.decide(existsState({ mode: "stop", message: "Duplicate file" }), {});
  assert.deepStrictEqual(d, { do: "stop", message: "Duplicate file" });
});

test("decideUnknown: pauses by default and stops when the fork says so", () => {
  assert.strictEqual(fork.decideUnknown({ unknown_policy: "ask_human" }, EXISTS_PROBE).do, "pause");
  assert.strictEqual(fork.decideUnknown({ unknown_policy: "stop" }, EXISTS_PROBE).do, "stop");
});

test("clampTimeout keeps the wait between 1.5s and 8s", () => {
  assert.strictEqual(fork.clampTimeout(10), 1500);
  assert.strictEqual(fork.clampTimeout(99999), 8000);
  assert.strictEqual(fork.clampTimeout(undefined), fork.DEFAULT_TIMEOUT_MS);
});

// ─── runForkStep ───────────────────────────────────────────────────────────────────────────

test("clean path: no dialog appears, so no option runs and the run carries on", async () => {
  const page = mockPage({ probe: null });
  const { tracker } = await run(page, [forkStep([existsState({ mode: "auto", option_id: "keep_both" })]), nav("https://x.test/next")]);
  assert.deepStrictEqual(page.gotos, ["https://x.test/next"]);
  assert.ok(tracker.events.some((e) => e.e === "fork_clean"));
});

test("known dialog + auto policy: the chosen option runs and the dialog is confirmed closed", async () => {
  const page = mockPage({ probe: EXISTS_PROBE });
  const { tracker } = await run(page, [forkStep([existsState({ mode: "auto", option_id: "keep_both" })])]);
  assert.deepStrictEqual(page.gotos, ["https://x.test/keep"]);
  const decided = tracker.events.find((e) => e.e === "fork_decided");
  assert.strictEqual(decided.opt, "keep_both");
  assert.strictEqual(decided.src, "policy");
});

test("by_input: the caller's value picks the option", async () => {
  const page = mockPage({ probe: EXISTS_PROBE });
  await run(page, [forkStep([existsState({ mode: "by_input", input: "on_duplicate" })])], { on_duplicate: "Replace" });
  assert.deepStrictEqual(page.gotos, ["https://x.test/replace"]);
});

test("by_input: a value that matches no option fails the run before anything is clicked", async () => {
  const page = mockPage({ probe: EXISTS_PROBE });
  await assert.rejects(
    () => run(page, [forkStep([existsState({ mode: "by_input", input: "on_duplicate" })])], { on_duplicate: "yolo" }),
    rejectsWith(/isn't a valid choice/),
  );
  assert.deepStrictEqual(page.gotos, []);
});

const pausesWith = (check) => (err) => {
  assert.strictEqual(err.forkPause, true, `expected a fork pause, got: ${err.message}`);
  check(err);
  return true;
};

test("consequential auto without approval pauses for a person and clicks nothing", async () => {
  const page = mockPage({ probe: EXISTS_PROBE });
  await assert.rejects(
    () => run(page, [forkStep([existsState({ mode: "auto", option_id: "replace" })])]),
    pausesWith((err) => {
      assert.strictEqual(err.stepIndex, 0);
      assert.strictEqual(err.info.reason, "consequential_unapproved");
      assert.strictEqual(err.info.suggested, "replace");
      assert.deepStrictEqual(err.info.choices.map((c) => c.id), ["replace", "keep_both", "cancel", "__stop__"]);
    }),
  );
  assert.deepStrictEqual(page.gotos, []);
});

test("ask_human pauses with the state's own options and never chooses for the person", async () => {
  const page = mockPage({ probe: EXISTS_PROBE });
  await assert.rejects(
    () => run(page, [forkStep([existsState({ mode: "ask_human" })])]),
    pausesWith((err) => assert.strictEqual(err.info.unknown, false)),
  );
  assert.deepStrictEqual(page.gotos, []);
});

test("an unrecognised dialog pauses offering only the buttons actually on screen", async () => {
  const page = mockPage({ probe: { title: "Discard changes?", text: "Unsaved.", controls: [{ name: "Discard" }, { name: "Keep editing" }] } });
  await assert.rejects(
    () => run(page, [forkStep([existsState({ mode: "auto", option_id: "keep_both" })])]),
    pausesWith((err) => {
      assert.strictEqual(err.info.unknown, true);
      assert.deepStrictEqual(err.info.choices.map((c) => c.id), ["Discard", "Keep editing", "__stop__"]);
    }),
  );
  assert.deepStrictEqual(page.gotos, []);
});

test("a person's answer runs that option on the re-observed dialog", async () => {
  const page = mockPage({ probe: EXISTS_PROBE });
  const { tracker } = await run(page, [forkStep([existsState({ mode: "ask_human" })])], { __fork_choice_0: "keep_both" });
  assert.deepStrictEqual(page.gotos, ["https://x.test/keep"]);
  assert.strictEqual(tracker.events.find((e) => e.e === "fork_decided").src, "human");
});

test("a person's answer that is not one of the state's options fails closed", async () => {
  const page = mockPage({ probe: EXISTS_PROBE });
  await assert.rejects(
    () => run(page, [forkStep([existsState({ mode: "ask_human" })])], { __fork_choice_0: "nuke_everything" }),
    rejectsWith(/not one of the choices/),
  );
  assert.deepStrictEqual(page.gotos, []);
});

test("choosing stop ends the run without touching the page", async () => {
  const page = mockPage({ probe: EXISTS_PROBE });
  await assert.rejects(
    () => run(page, [forkStep([existsState({ mode: "ask_human" })])], { __fork_choice_0: "__stop__" }),
    rejectsWith(/Stopped at the popup/),
  );
  assert.deepStrictEqual(page.gotos, []);
});

test("if the person already dealt with the popup in the page, the run just carries on", async () => {
  const page = mockPage({ probe: null });
  const { tracker } = await run(page, [forkStep([existsState({ mode: "ask_human" })]), nav("https://x.test/next")], { __fork_choice_0: "keep_both" });
  assert.deepStrictEqual(page.gotos, ["https://x.test/next"]);
  assert.strictEqual(tracker.events.find((e) => e.e === "fork_decided").mode, "handled_in_page");
});

test("an unrecognised popup answered by label clicks that exact visible button", async () => {
  const clicked = [];
  const probe = { title: "Discard changes?", text: "Unsaved.", controls: [{ name: "Discard" }, { name: "Keep editing" }] };
  const page = mockPage({ probe });
  const dialogs = {
    last: () => ({ getByRole: (role, opts) => ({ first: () => ({ click: async () => { clicked.push([role, opts.name, opts.exact]); page.probe = null; } }) }) }),
  };
  page.locator = () => dialogs;
  await run(page, [forkStep([existsState({ mode: "auto", option_id: "keep_both" })])], { __fork_choice_0: "Keep editing" });
  assert.deepStrictEqual(clicked, [["button", "Keep editing", true]]);
});

test("an unrecognised popup answered with a label that is not on screen fails closed", async () => {
  const probe = { title: "Discard changes?", text: "", controls: [{ name: "Discard" }] };
  const page = mockPage({ probe });
  await assert.rejects(
    () => run(page, [forkStep([existsState({ mode: "ask_human" })])], { __fork_choice_0: "Delete everything" }),
    rejectsWith(/no longer one of its buttons/),
  );
});

test("inside a loop a pause degrades to a stop (a person cannot be asked mid-iteration)", async () => {
  const page = mockPage({ probe: EXISTS_PROBE });
  const loop = { type: "for_each", items: "files", as: "f", max_iterations: 2, steps: [forkStep([existsState({ mode: "ask_human" })])] };
  await assert.rejects(() => run(page, [loop], { files: "a,b" }), (err) => { assert.notStrictEqual(err.forkPause, true); return true; });
});

test("an option step that really fails fails the run (not swallowed like a best-effort branch)", async () => {
  const page = mockPage({ probe: EXISTS_PROBE, gotoThrows: true });
  await assert.rejects(() => run(page, [forkStep([existsState({ mode: "auto", option_id: "keep_both" })])]));
});

test("a dialog still open after the option ran fails the run", async () => {
  const page = mockPage({ probe: EXISTS_PROBE, stayOpen: true });
  await assert.rejects(
    () => run(page, [forkStep([existsState({ mode: "auto", option_id: "keep_both" })])]),
    rejectsWith(/still open/),
  );
});

test("native dialog: matched from the dialog queue and answered by the option's dialog step", async () => {
  const answered = [];
  const dialog = {
    type: () => "confirm",
    message: () => "Replace existing file?",
    accept: async () => { answered.push("accept"); queue.length = 0; },
    dismiss: async () => { answered.push("dismiss"); queue.length = 0; },
  };
  const queue = [dialog];
  const state = {
    id: "native",
    kind: "native_dialog",
    signature: { title: "confirm", button_labels: ["OK", "Cancel"] },
    options: [{ id: "cancel", label: "Cancel", role: "dismiss", steps: [{ type: "dialog_dismiss" }] }],
    policy: { mode: "auto", option_id: "cancel" },
  };
  const page = mockPage();
  await run(page, [forkStep([state])], {}, { dialogQueue: queue });
  assert.deepStrictEqual(answered, ["dismiss"]);
});

test("guard: a destructive option pauses at the fork's own index before it runs", async () => {
  const state = existsState({ mode: "auto", option_id: "replace", author_approved: true });
  state.options[0].steps = [nav("https://x.test/replace", { destructive: true })];
  const page = mockPage({ probe: EXISTS_PROBE });
  await assert.rejects(
    () => run(page, [forkStep([state])], {}, { guard: { pause: true, released: false } }),
    (err) => { assert.strictEqual(err.guardPause, true); assert.strictEqual(err.stepIndex, 0); return true; },
  );
  assert.deepStrictEqual(page.gotos, []);
});

test("an older runtime's unknown-step failure path is not used: outcome_fork is a known step type", async () => {
  const { executeStep } = require("../../app/run");
  await executeStep(mockPage(), { type: "outcome_fork", states: [] }, {});
});

// ─── fork_pause.js ─────────────────────────────────────────────────────────────────────────

const { validateForkAnswer, buildForkRequest } = require("../../app/fork_pause");

test("validateForkAnswer accepts {choice} or a bare id, only from the offered list", () => {
  const ids = ["replace", "keep_both", "__stop__"];
  assert.strictEqual(validateForkAnswer({ choice: "keep_both" }, ids).choice, "keep_both");
  assert.strictEqual(validateForkAnswer("replace", ids).valid, true);
  assert.strictEqual(validateForkAnswer({ choice: "delete_all" }, ids).valid, false);
  assert.strictEqual(validateForkAnswer({}, ids).valid, false);
  assert.strictEqual(validateForkAnswer(null, ids).valid, false);
});

test("buildForkRequest tells the agent to ask the user and marks the pause as human-only", async () => {
  const info = {
    title: "Upload options", text: "report.pdf already exists.", reason: "consequential_unapproved", suggested: "replace", unknown: false,
    choices: [{ id: "replace", label: "Replace", role: "consequential" }, { id: "keep_both", label: "Keep both", role: "neutral" }, { id: "__stop__", label: "Stop the run", role: "stop" }],
  };
  const resp = await buildForkRequest({ url: () => "https://drive.test", screenshot: async () => null }, 3, info, {});
  const text = resp.content[0].text;
  assert.match(text, /ASK THE USER/);
  assert.match(text, /resume_from: 3/);
  assert.match(text, /replace: Replace \(changes or removes data\)/);
  const meta = resp._meta["conxa/fork"];
  assert.strictEqual(meta.requires_human, true);
  assert.strictEqual(meta.step_index, 3);
  assert.deepStrictEqual(meta.choices.map((c) => c.id), ["replace", "keep_both", "__stop__"]);
});
