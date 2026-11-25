"use strict";
// E2E16-14. BUILD-110 (1): just before a form is sent, every box the run typed for it must still hold what was typed;
// a box that changed stops the run and is never typed again, and a secret box's value never leaves the page.
// BUILD-110 (2): a box the compiler linked to an earlier box ("the same value as the first box") is filled from the
// same run input. BUILD-106 (1): the page's own OK/Cancel box that no step answers is always put to the MCP client,
// never pressed by the runtime; never asked in a dry, loop, destructive or scheduled run.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-typed-native-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");

const pageScripts = require("../../app/page_scripts");
const { runPlan, noteTypedBox, checkTypedBoxes, dismissNativeQuestions } = require("../../app/run");
const { buildForkRequest } = require("../../app/fork_pause");

// ─── BUILD-110 (1): the page functions ─────────────────────────────────────────────────────

function box(value, extra = {}) {
  return { tagName: "INPUT", value, ...extra };
}

test("typedBoxHolds: same when the box shows the typed text or what it showed right after typing", () => {
  const b = box("(555) 123-4567");
  assert.strictEqual(pageScripts.noteTypedBox(b), true);
  assert.strictEqual(pageScripts.typedBoxHolds(b, "5551234567"), "same", "a box that formats as you type is not a change");
  assert.strictEqual(pageScripts.typedBoxHolds(box("  Acme  Corp "), "Acme Corp"), "same", "spaces aside");
});

test("typedBoxHolds: different when the box changed after typing; null when it is not a box", () => {
  const b = box("Acme Corp");
  pageScripts.noteTypedBox(b);
  b.value = "Acme CorpA";
  assert.strictEqual(pageScripts.typedBoxHolds(b, "Acme Corp"), "different");
  b.value = "";
  assert.strictEqual(pageScripts.typedBoxHolds(b, "Acme Corp"), "different", "a cleared box changed");
  assert.strictEqual(pageScripts.typedBoxHolds({ tagName: "DIV" }, "x"), null);
});

test("typedBoxHolds answers only same/different, never the box's value", () => {
  const secret = box("hunter2-typo", { type: "password" });
  const said = pageScripts.typedBoxHolds(secret, "hunter2");
  assert.ok(said === "different" && !String(said).includes("hunter"));
});

// ─── BUILD-110 (1): the run's check before the send ────────────────────────────────────────

// A fake element finder: each step's box is one fake input whose live value the test sets.
function fakeFinder(boxes) {
  return async (_page, step) => ({ evaluate: async (fn, arg) => fn(boxes[step.source_step_key], arg) });
}
const typedStep = (key, value, label) => ({
  type: "type", value, source_step_key: key,
  identity_bundle: { fingerprint: { label_text: label } },
});
const sendStep = (keys) => ({ type: "click", intent: "Send", handler_hints: { recheck_typed: keys } });
function runState(inputs = {}) {
  const events = [];
  return { events, ctx: { typedRechecks: new Set(["k1", "k2"]), tracker: { emit: (e, f) => events.push({ e, ...f }) }, slug: "s" },
    state: { inputs, warnings: [] } };
}

test("a box that still holds what was typed lets the send go ahead", async () => {
  const boxes = { k1: box("") };
  const finder = async (_p, step) => { boxes.k1.value = "Ada"; return fakeFinder(boxes)(_p, step); };
  const { ctx, state } = runState({ name: "Ada" });
  const s1 = typedStep("k1", "{{name}}", "Name");
  await noteTypedBox(ctx, state, s1, null, finder);
  await checkTypedBoxes(ctx, state, sendStep(["k1"]), 1, null, fakeFinder(boxes));
});

test("a box that changed after typing stops the run before the send, plainly, and is never typed again", async () => {
  const boxes = { k1: box("Acme Corp") };
  const { ctx, state, events } = runState({ company: "Acme Corp" });
  await noteTypedBox(ctx, state, typedStep("k1", "{{company}}", "Company"), null, fakeFinder(boxes));
  boxes.k1.value = "Acme CorpA"; // a stray key
  await assert.rejects(checkTypedBoxes(ctx, state, sendStep(["k1"]), 2, null, fakeFinder(boxes)), (err) => {
    assert.match(err.message, /The box no longer holds what was typed \(the "Company" box\)\. The form was not sent/);
    assert.strictEqual(err.fault, true);
    assert.strictEqual(err.faultKind, "box_changed");
    assert.ok(!err.message.includes("Acme"), "the value is never printed");
    return true;
  });
  assert.ok(events.some((e) => e.e === "step_fail" && e.fc === "typed_box_changed"));
  assert.strictEqual(boxes.k1.value, "Acme CorpA", "nothing was retyped");
});

test("a box this run did not type, or one that cannot be found, is 'can't tell' and never stops the send", async () => {
  const { ctx, state } = runState({});
  await checkTypedBoxes(ctx, state, sendStep(["k1"]), 1, null, fakeFinder({})); // never typed
  await noteTypedBox(ctx, state, typedStep("k2", "x", "X"), null, async () => { throw new Error("gone"); });
  await checkTypedBoxes(ctx, state, sendStep(["k2"]), 1, null, async () => { throw new Error("gone"); });
});

test("a step the compiler did not list is not noted (no cost on workflows without the mark)", async () => {
  const { ctx, state } = runState({});
  let asked = false;
  await noteTypedBox(ctx, state, typedStep("other", "x", "X"), null, async () => { asked = true; return null; });
  assert.strictEqual(asked, false);
  assert.ok(!state.typedBoxes);
});

// ─── BUILD-110 (2): the second box reuses the first box's run input ────────────────────────

test("a pasted box linked to the first box types the first box's value", async () => {
  const { ctx, state } = runState({ email: "ada@x.io" });
  const boxes = { k1: box("ada@x.io"), k2: box("ada@x.io") };
  await noteTypedBox(ctx, state, typedStep("k1", "{{email}}", "Email"), null, fakeFinder(boxes));
  await noteTypedBox(ctx, state, typedStep("k2", "{{email}}", "Confirm email"), null, fakeFinder(boxes));
  assert.strictEqual(state.typedBoxes.get("k2").typed, state.typedBoxes.get("k1").typed);
  assert.strictEqual(state.typedBoxes.get("k2").typed, "ada@x.io");
});

// ─── BUILD-106 (1): the page's own OK/Cancel box ───────────────────────────────────────────

function fakeDialog(type, message) {
  const d = { type: () => type, message: () => message, defaultValue: () => "", accepted: 0, dismissed: 0 };
  d.accept = async () => { d.accepted++; };
  d.dismiss = async () => { d.dismissed++; };
  return d;
}

// Opening "https://x.test/a" makes the page raise `dialog` (a confirm() no step answers).
function nativePage(dialogQueue, dialog) {
  const page = {
    gotos: [],
    waitForLoadState: async () => {},
    context: () => ({ on: () => {} }),
    url: () => "https://x.test",
    evaluate: async () => null,
    once: () => {},
    off: () => {},
    screenshot: async () => { throw new Error("no picture while the box is open"); },
    goto: async (url) => { page.gotos.push(url); if (url.endsWith("/a")) dialogQueue.push(dialog); },
  };
  return page;
}
const nav = (url, extra = {}) => ({ type: "navigate", url, ...extra });
const PLAN = () => [nav("https://x.test/a", { intent: "open_order" }), nav("https://x.test/b")];

async function run(steps, inputs, extra, dialogQueue, dialog) {
  const page = nativePage(dialogQueue, dialog);
  const tracker = { emit: () => {} };
  const result = await runPlan(page, steps, inputs, 0, "native-box", { tracker, dialogQueue, ...extra });
  return { page, result };
}

test("an unrecorded OK/Cancel box is put to the MCP client with its own words; nothing is pressed", async () => {
  const q = [];
  const d = fakeDialog("confirm", "Delete order 42?");
  await assert.rejects(run(PLAN(), {}, { answerPopups: true }, q, d), (err) => {
    assert.strictEqual(err.forkPause, true);
    assert.strictEqual(err.stepIndex, 0);
    assert.strictEqual(err.info.native, true);
    assert.strictEqual(err.info.acted, true, "the action already ran: the answer re-checks it, never repeats it");
    assert.strictEqual(err.info.text, "Delete order 42?", "the words are passed on as they are");
    assert.deepStrictEqual(err.info.choices.map((c) => c.id), ["OK", "Cancel", "__stop__"]);
    return true;
  });
  assert.strictEqual(d.accepted + d.dismissed, 0, "the runtime never presses either button itself");
  assert.strictEqual(q.length, 1, "the box stays open for the answer");
});

test("an OK/Cancel box is never asked about in a dry, destructive or scheduled run, or with nobody to ask", async () => {
  for (const extra of [{ answerPopups: false }, { answerPopups: true, dryRun: true }, { answerPopups: true, scheduled: true }]) {
    const q = [];
    const d = fakeDialog("confirm", "Sure?");
    await run(PLAN(), {}, extra, q, d).then(() => {}, (err) => assert.notStrictEqual(err.forkPause, true));
    assert.strictEqual(d.accepted, 0, JSON.stringify(extra));
  }
  const q = [];
  const d = fakeDialog("confirm", "Sure?");
  const steps = PLAN();
  steps[0].destructive = true;
  await run(steps, {}, { answerPopups: true }, q, d).then(() => {}, (err) => assert.notStrictEqual(err.forkPause, true));
  assert.strictEqual(d.accepted, 0);
});

test("an OK/Cancel box inside a loop is never asked about", async () => {
  const q = [];
  const d = fakeDialog("confirm", "Sure?");
  const loop = { type: "for_each", items: "rows", as: "r", max_iterations: 2, steps: [nav("https://x.test/a")] };
  await run([loop], { rows: "a" }, { answerPopups: true }, q, d).then(() => {}, (err) => assert.notStrictEqual(err.forkPause, true));
  assert.strictEqual(d.accepted, 0);
});

test("a box the recording answered (the next step is its answer) is not asked about", async () => {
  const q = [];
  const d = fakeDialog("confirm", "Sure?");
  const steps = [nav("https://x.test/a"), { type: "dialog_accept", value: JSON.stringify({ type: "confirm", message: "Sure?" }) }];
  await run(steps, {}, { answerPopups: true }, q, d);
  assert.strictEqual(d.accepted, 1, "the recorded answer, not the runtime's");
});

test("resume with OK: the box is accepted, the step is not repeated, the run goes on", async () => {
  const d = fakeDialog("confirm", "Delete order 42?");
  const q = [d]; // the box carried over from the paused call (server.js hands the park's queue on)
  const { page, result } = await run(PLAN(), { __fork_choice_0: "OK", __fork_agent_0: 1, __fork_acted_0: 1 }, { answerPopups: true }, q, fakeDialog("confirm", "x"));
  assert.strictEqual(d.accepted, 1);
  assert.strictEqual(d.dismissed, 0);
  assert.deepStrictEqual(page.gotos, ["https://x.test/b"], "the step that raised the box was not run again");
  assert.ok(result.warnings.some((w) => /OK\/Cancel box was answered with "OK" by the assistant/.test(w)));
});

test("resume with Cancel dismisses the box; Stop presses Cancel and ends the run", async () => {
  const d = fakeDialog("confirm", "Sure?");
  await run(PLAN(), { __fork_choice_0: "Cancel", __fork_agent_0: 1, __fork_acted_0: 1 }, { answerPopups: true }, [d], d);
  assert.deepStrictEqual([d.accepted, d.dismissed], [0, 1]);

  const s = fakeDialog("confirm", "Sure?");
  const q = [s];
  await assert.rejects(run(PLAN(), { __fork_choice_0: "__stop__", __fork_agent_0: 1, __fork_acted_0: 1 }, { answerPopups: true }, q, s),
    (err) => err.forkDecisionNeeded === true);
  assert.deepStrictEqual([s.accepted, s.dismissed], [0, 1], "never left open, never OK'd");
  assert.strictEqual(q.length, 0);
});

// BUILD-117 (4): after OK the step's own check could never count, because the resume re-checked it with no "before".
test("the pause keeps the page as it was before the step, and the resume re-checks the step against it", async () => {
  const checked = () => [nav("https://x.test/a", { validation: { assertions: [{ type: "state_changed", required: true }] } }), nav("https://x.test/b")];
  const sigPage = (q, d, sig) => {
    const page = nativePage(q, d);
    page.evaluate = async (fn) => (fn === pageScripts.preStepSignature ? sig : null);
    return page;
  };
  const q = [];
  const before = { textLen: 10, interactiveCount: 2 };
  await assert.rejects(
    runPlan(sigPage(q, fakeDialog("confirm", "Open the details?"), before), checked(), {}, 0, "native-box",
      { tracker: { emit: () => {} }, dialogQueue: q, answerPopups: true }),
    (err) => err.forkPause === true && err.stateBaseline && err.stateBaseline.textLen === 10 && !("stateBaseline" in err.info),
  );
  const cantTell = async (extra) => {
    const d = fakeDialog("confirm", "Open the details?");
    const events = [];
    await runPlan(sigPage([d], fakeDialog("confirm", "x"), { textLen: 80, interactiveCount: 3 }), checked(),
      { __fork_choice_0: "OK", __fork_agent_0: 1, __fork_acted_0: 1, ...extra }, 0, "native-box",
      { tracker: { emit: (e, p) => events.push([e, p]) }, dialogQueue: [d], answerPopups: true });
    return events.find(([e, p]) => e === "verify_result" && p.si === 0)[1].ct;
  };
  assert.strictEqual(await cantTell({ __fork_baseline_0: { url: "https://x.test", ...before } }), 0, "measured against the page before");
  assert.strictEqual(await cantTell({}), 1, "no before: can't tell, as it was");
});

test("a popup Studio put to the person is reported as answered by a person, under the same rules", async () => {
  const d = fakeDialog("confirm", "Sure?");
  const { result } = await run(PLAN(), { __fork_choice_0: "OK", __fork_agent_0: 1, __fork_acted_0: 1, __fork_person_0: 1 },
    { answerPopups: true }, [d], fakeDialog("confirm", "x"));
  assert.strictEqual(d.accepted, 1);
  assert.ok(result.warnings.some((w) => /answered with "OK" by a person/.test(w)));
});

test("dismissNativeQuestions presses Cancel on open OK/Cancel boxes only, never an alert, never OK", async () => {
  const c = fakeDialog("confirm", "a");
  const p = fakeDialog("prompt", "b");
  const a = fakeDialog("alert", "c");
  const q = [c, a, p];
  await dismissNativeQuestions(q);
  assert.deepStrictEqual([c.dismissed, p.dismissed, a.dismissed, c.accepted + p.accepted], [1, 1, 0, 0]);
  assert.deepStrictEqual(q, [a]);
});

test("the request for an OK/Cancel box shows its words plainly, takes no picture and says what a timeout does", async () => {
  let shot = false;
  const page = { url: () => "https://x.test", screenshot: async () => { shot = true; return null; } };
  const info = { title: "confirm", text: "Delete order 42?", reason: "agent_decides", agent: true, native: true, acted: true,
    choices: [{ id: "OK", label: "OK" }, { id: "Cancel", label: "Cancel" }, { id: "__stop__", label: "Stop the run" }] };
  const resp = await buildForkRequest(page, 0, info, {});
  const text = resp.content[0].text;
  assert.match(text, /the page's own OK\/Cancel box\. Its words, as the page wrote them:\nDelete order 42\?/);
  assert.match(text, /Cancel is pressed and the run ends as "couldn't confirm"/);
  assert.strictEqual(shot, false);
  assert.strictEqual(resp.content.length, 1);
  assert.strictEqual(resp._meta["conxa/fork"].native, true);
});

// ─── BUILD-117 (4).1: in a host-owned browser the page's confirm() is refused in the page, no native box ever opens ───

// Opening "https://x.test/a" makes the page's shim refuse a confirm() ("Delete order 42?"); frame.evaluate runs the page scripts.
function shimPage() {
  const seen = { armed: [], boxes: [], answers: [] };
  const frame = {
    evaluate: async (fn, arg) => {
      if (fn === pageScripts.armBoxShim) { seen.armed.push(true); if (arg !== undefined) seen.answers.push(arg); return true; }
      if (fn === pageScripts.disarmBoxShim) { seen.armed.push(false); return true; }
      if (fn === pageScripts.takeBoxes) return seen.boxes.splice(0);
      return null;
    },
  };
  const page = nativePage([], null);
  page.frames = () => [frame];
  page.goto = async (url) => { page.gotos.push(url); if (url.endsWith("/a") && !seen.answers.length) seen.boxes.push("Delete order 42?"); };
  return { page, seen };
}
const runShim = (page, inputs, extra) => runPlan(page, PLAN(), inputs, 0, "native-box", { tracker: { emit: () => {} }, dialogQueue: [], hostOwned: true, answerPopups: true, ...extra });

test("host-owned: a confirm() refused in the page is put to the MCP client with its own words", async () => {
  const { page } = shimPage();
  await assert.rejects(runShim(page, {}), (err) => {
    assert.strictEqual(err.forkPause, true);
    assert.strictEqual(err.info.shim, true);
    assert.strictEqual(err.info.native, true);
    assert.strictEqual(err.info.text, "Delete order 42?");
    assert.deepStrictEqual(err.info.choices.map((c) => c.id), ["OK", "Cancel", "__stop__"]);
    return true;
  });
});

test("host-owned: resume with OK runs the step again with OK armed; Cancel re-checks it and never runs it again", async () => {
  const ok = shimPage();
  await runShim(ok.page, { __fork_choice_0: "OK", __fork_agent_0: 1, __fork_acted_0: 1, __fork_shim_0: 1 });
  assert.deepStrictEqual(ok.seen.answers, [true], "OK armed once");
  assert.deepStrictEqual(ok.page.gotos, ["https://x.test/a", "https://x.test/b"], "the step ran again, with the answer in place");

  const no = shimPage();
  await runShim(no.page, { __fork_choice_0: "Cancel", __fork_agent_0: 1, __fork_acted_0: 1, __fork_shim_0: 1 }).catch(() => {});
  assert.deepStrictEqual(no.seen.answers, [], "no answer armed");
  assert.ok(!no.page.gotos.includes("https://x.test/a"), "the step was not run again");
});

test("host-owned: no shim where nobody may be asked, or where the recording answers the box", async () => {
  const dry = shimPage();
  await runShim(dry.page, {}, { dryRun: true }).catch(() => {});
  assert.deepStrictEqual(dry.seen.armed.filter(Boolean), [], "a dry run keeps the browser's own box");
  const rec = shimPage();
  const steps = [nav("https://x.test/a"), { type: "dialog_accept", value: JSON.stringify({ type: "confirm", message: "Sure?" }) }];
  const q = [fakeDialog("confirm", "Sure?")]; // the box is already open: the recorded step answers it at once
  await runPlan(rec.page, steps, {}, 0, "native-box", { tracker: { emit: () => {} }, dialogQueue: q, hostOwned: true, answerPopups: true }).catch(() => {});
  assert.deepStrictEqual(rec.seen.armed.filter(Boolean), [], "a recorded box is answered by the browser, not the shim");
});
