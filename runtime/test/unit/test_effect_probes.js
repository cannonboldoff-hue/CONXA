"use strict";
// Proof of Done stage 4 (runtime half): a step's compiled `validation.effect` checklist is answered from the
// evidence notebook, and proveStep judges the step by it. Nothing here touches a real browser.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-effect-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";
process.env.CONXA_COMPLETION_START_GRACE_MS = "150";

const test = require("node:test");
const assert = require("node:assert");
const { EventEmitter } = require("node:events");

const pageScripts = require("../../app/page_scripts");
const evidence = require("../../app/evidence");
const { evaluateChecks, valueFreeWords } = require("../../app/effect_probes");
const { runPlan } = require("../../app/run");

const CLEAN = { busyCount: 0, regions: [] };
const DONE = { busyCount: 0, regions: ["1 upload complete"] };
const TRAY = { busyCount: 0, regions: ["Uploading 1 item"] };

function req(method, url, type = "xhr") {
  return { method: () => method, url: () => url, resourceType: () => type, isNavigationRequest: () => false, failure: () => ({ errorText: "net::ERR_CONNECTION_RESET" }) };
}
function resp(request, status, body) {
  const buf = body == null ? null : Buffer.from(JSON.stringify(body));
  return { status: () => status, request: () => request, headers: () => ({ "content-type": "application/json" }), body: async () => buf };
}
const settle = () => new Promise((r) => setTimeout(r, 15));

// A page that is also an event emitter, so the notebook can listen to it. `onPress` plays the app's reaction to the action.
function livePage(samples, onPress = () => {}) {
  const page = new EventEmitter();
  let calls = 0;
  const pressed = [];
  Object.assign(page, {
    pressed,
    url: () => "https://drive.test/",
    title: async () => "Drive",
    mainFrame: () => "main",
    context: () => ({ on: () => {} }),
    waitForLoadState: async () => {},
    waitForTimeout: async () => {},
    screenshot: async () => Buffer.from(""),
    keyboard: { press: async (k) => { pressed.push(k); await onPress(page); } },
    evaluate: async (script) => (script === pageScripts.completionProbe ? samples[Math.min(calls++, samples.length - 1)] : null),
  });
  return page;
}

const effect = (over = {}) => ({
  kind: "upload",
  required_rung: "completed",
  target_rung: "result_exists",
  quorum: { witnesses: 1, identity_required: false },
  checks: [
    { id: "c1", rung: "performed", witness: "target", role: "support", probe: { type: "input_has_files" } },
    { id: "c2", rung: "completed", witness: "transport", role: "support", probe: { type: "request_chain", method: "mutating", status: "2xx" } },
    { id: "c3", rung: "completed", witness: "renderer", role: "support", probe: { type: "new_text", words: ["upload", "complete"] } },
    { id: "c4", rung: "completed", witness: "renderer", role: "veto", probe: { type: "failure_signal", scope: "new_since_baseline" } },
  ],
  ...over,
});

const answers = (checks) => Object.fromEntries(checks.map((c) => [c.id, c.answer]));

// A step the way the compiler ships it: a keyboard press stands in for the click, `effect` rides on validation.
const stepWith = (eff, extra = {}) => [{ type: "keyboard_shortcut", value: "Enter", intent: "Upload the file", validation: { effect: eff }, ...extra }];
const tracker = () => { const events = []; return { events, emit: (c, d) => events.push([c, d]) }; };

test("valueFreeWords keeps words and drops digits, quotes, addresses", () => {
  assert.deepStrictEqual(valueFreeWords("1 upload complete"), ["upload", "complete"]);
  assert.deepStrictEqual(valueFreeWords('"report.pdf" was uploaded to jo@acme.com'), ["was", "uploaded"]);
});

test("the checklist is answered from the notebook: request accepted, completion text seen, no failure", async () => {
  const page = livePage([CLEAN, TRAY, DONE]);
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  const r = req("POST", "https://drive.test/upload/abc123");
  page.emit("request", r);
  page.emit("response", resp(r, 200, { id: "f1" }));
  page.emit("requestfinished", r);
  await settle();
  await nb.sample(); // sees TRAY
  await nb.sample(); // sees DONE
  // "The file was chosen" comes only from the file box read back after the pick, never from the step having run.
  const got = answers(evaluateChecks(effect(), { notebook: nb, gate: { status: "done" } }));
  assert.deepStrictEqual(got, { c1: "cant_tell", c2: "yes", c3: "yes", c4: "no" });
  assert.strictEqual(answers(evaluateChecks(effect(), { notebook: nb, gate: { status: "done" }, filesChosen: { answer: "yes" } })).c1, "yes");
  nb.close();
});

test("a completion message that has already gone again still counts (a toast)", async () => {
  const page = livePage([CLEAN, DONE, CLEAN]);
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  await nb.sample(); // DONE
  await nb.sample(); // the toast has gone
  assert.strictEqual(answers(evaluateChecks(effect(), { notebook: nb, gate: { status: "done" } })).c3, "yes");
  nb.close();
});

test("nothing was sent, or the words never appeared: cant_tell and no, never yes", async () => {
  const page = livePage([CLEAN]);
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  await nb.sample();
  const got = answers(evaluateChecks(effect(), { notebook: nb, gate: { status: "done" } }));
  assert.strictEqual(got.c2, "cant_tell", "a request the notebook never saw proves nothing either way");
  assert.strictEqual(got.c3, "no");
  assert.strictEqual(got.c4, "no");
  nb.close();
});

test("a refusal, or a reply that says it failed, answers the failure veto 'yes' and the request check 'no'", async () => {
  const page = livePage([CLEAN]);
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  const r = req("POST", "https://drive.test/upload/abc123");
  page.emit("request", r);
  page.emit("response", resp(r, 200, { error: { code: "storageQuotaExceeded" } }));
  await settle();
  const got = evaluateChecks(effect(), { notebook: nb, gate: null });
  const by = Object.fromEntries(got.map((c) => [c.id, c]));
  assert.strictEqual(by.c4.answer, "yes");
  assert.match(by.c4.evidence, /the reply says the action failed/);
  assert.strictEqual(by.c2.answer, "no");
  nb.close();
});

test("a check a test run showed to be dead is left out, and an unknown probe cannot say yes", async () => {
  const page = livePage([CLEAN]);
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  const eff = effect();
  eff.checks[2].calibrated = false;
  eff.checks[2].calibration_basis = ["c2"];
  eff.checks.push({ id: "c5", rung: "result_exists", witness: "fresh_look", role: "support", probe: { type: "row_with_identity" } });
  const got = evaluateChecks(eff, { notebook: nb, gate: { status: "done" } });
  assert.ok(!got.some((c) => c.id === "c3"), "the dead check no longer counts");
  assert.strictEqual(answers(got).c5, "cant_tell");
  assert.deepStrictEqual(evaluateChecks(null), []);
  nb.close();
});

// ── through the run loop ───────────────────────────────────────────────────────────────────────

test("runPlan: an upload whose request was accepted and whose completion text appeared is proven", async () => {
  const page = livePage([CLEAN, TRAY, DONE], async (p) => {
    const r = req("POST", "https://drive.test/upload/abc123");
    p.emit("request", r); p.emit("response", resp(r, 200, { id: "f1" })); p.emit("requestfinished", r);
    await settle();
  });
  const t = tracker();
  const result = await runPlan(page, stepWith(effect()), {}, 0, "slug-effect-ok", { tracker: t });
  const s = result.proof.steps[0];
  assert.deepStrictEqual([s.verdict, s.rung, s.required, s.effect_kind], ["proven", "completed", "completed", "upload"]);
  assert.deepStrictEqual(result.proof.notes, []);
  // c1 (the file box) is not read here: this step is a press, not a file pick.
  assert.deepStrictEqual(Object.fromEntries(s.checks.filter((c) => c.id).map((c) => [c.id, c.answer])), { c1: "cant_tell", c2: "yes", c3: "yes", c4: "no" });
});

test("runPlan: the app answering OK with a reply that says the upload failed stops the run as a failure, not 'couldn't confirm'", async () => {
  const page = livePage([CLEAN, TRAY, TRAY, TRAY], async (p) => {
    const r = req("POST", "https://drive.test/upload/abc123");
    p.emit("request", r); p.emit("response", resp(r, 200, { error: { code: "storageQuotaExceeded" } })); p.emit("requestfinished", r);
    await settle();
  });
  await assert.rejects(
    runPlan(page, stepWith(effect()), {}, 0, "slug-effect-veto", { tracker: tracker() }),
    (err) => {
      assert.strictEqual(err.fault, true);
      assert.strictEqual(err.couldntConfirm, undefined, "a failure the app showed is not 'couldn't confirm'");
      assert.strictEqual(err.completionGate, true);
      assert.match(err.message, /the reply says the action failed/);
      return true;
    },
  );
  assert.deepStrictEqual(page.pressed, ["Enter"], "never repeated");
});

test("runPlan: a calibrated quorum of two separate witnesses that is not met ends the run as 'couldn't confirm'", async () => {
  // Only the screen said anything (the gate sees the tray clear); no request was visible, so the transport witness is silent.
  const page = livePage([CLEAN, TRAY, DONE]);
  const eff = effect({ quorum: { witnesses: 2, identity_required: false } });
  eff.checks[2].calibrated = false; // the 'words' check is dead, so the screen is only the gate's single witness
  eff.checks[2].calibration_basis = ["c2"]; // shown dead by a run that really proved the step
  await assert.rejects(
    runPlan(page, stepWith(eff, { destructive: true }), {}, 0, "slug-effect-quorum", { tracker: tracker() }),
    (err) => err.couldntConfirm === true,
  );
});

test("runPlan: with the same quorum met by the transport witness the step is proven", async () => {
  const page = livePage([CLEAN, TRAY, DONE], async (p) => {
    const r = req("PUT", "https://drive.test/upload/zzz999");
    p.emit("request", r); p.emit("response", resp(r, 200, null)); p.emit("requestfinished", r);
    await settle();
  });
  const eff = effect({ quorum: { witnesses: 2, identity_required: false } });
  eff.checks[0].calibrated = true; eff.checks[0].calibration_basis = ["c2"]; // a proven test run raised the quorum
  const result = await runPlan(page, stepWith(eff, { destructive: true }), {}, 0, "slug-effect-quorum-ok", { tracker: tracker() });
  assert.strictEqual(result.proof.steps[0].verdict, "proven");
});

test("runPlan: a step with no checklist behaves exactly as it did in stage 2", async () => {
  const page = livePage([CLEAN, TRAY, DONE]);
  const result = await runPlan(page, [{ type: "keyboard_shortcut", value: "Enter", intent: "Upload the file" }], {}, 0, "slug-effect-none", { tracker: tracker() });
  const s = result.proof.steps[0];
  assert.deepStrictEqual([s.verdict, s.effect_kind], ["proven", undefined]);
});

// ── a witness must be able to say no ───────────────────────────────────────────────────────────

test("a request to an endpoint the run already saw talking is background noise: it proves nothing and starts nothing", async () => {
  const page = livePage([CLEAN]);
  const nb = await evidence.open({ page, pagesOf: () => [page], ambientKeys: new Set(["POST https://drive.test/log"]) });
  const noise = req("POST", "https://drive.test/log");
  page.emit("request", noise); page.emit("response", resp(noise, 200, null)); page.emit("requestfinished", noise);
  await settle();
  assert.strictEqual(nb.mutationStarted(), false, "background traffic is no sign that the action took the work");
  assert.strictEqual(nb.sawStart(), false);
  assert.strictEqual(answers(evaluateChecks(effect(), { notebook: nb, gate: null })).c2, "cant_tell");
  const own = req("POST", "https://drive.test/upload/abc123");
  page.emit("request", own); page.emit("response", resp(own, 200, { id: "f1" })); page.emit("requestfinished", own);
  await settle();
  assert.strictEqual(answers(evaluateChecks(effect(), { notebook: nb, gate: null })).c2, "yes", "a request the action itself caused still counts");
  nb.close();
});

test("the run's background endpoints are learned from the pages, and an action's own first request is not among them", async () => {
  const page = livePage([CLEAN]);
  const watcher = evidence.ambientWatcher({ pagesOf: () => [page] });
  watcher.refresh();
  watcher.refresh(); // one listener per page, however often it is refreshed
  const r = req("POST", "https://drive.test/log?token=abc");
  page.emit("request", r);
  page.emit("request", req("GET", "https://drive.test/list"));
  assert.deepStrictEqual([...watcher.keys], ["POST https://drive.test/log"]);
});

test("runPlan: an upload whose only 'accepted' request was background traffic is not proven", async () => {
  const page = livePage([CLEAN, TRAY, CLEAN, CLEAN, CLEAN], async (p) => {
    const r = req("POST", "https://drive.test/log");
    p.emit("request", r); p.emit("response", resp(r, 200, null)); p.emit("requestfinished", r);
    await settle();
  });
  // The page was already calling /log before the action (an earlier step), so it is background noise.
  const step = stepWith(effect(), { destructive: true })[0];
  const ctxPrime = { type: "keyboard_shortcut", value: "Shift", intent: "Look around" };
  const ping = () => { const r = req("POST", "https://drive.test/log"); page.emit("request", r); };
  const origPress = page.keyboard.press;
  page.keyboard.press = async (k) => { if (k === "Shift") { ping(); return; } return origPress(k); };
  await assert.rejects(
    runPlan(page, [ctxPrime, step], {}, 0, "slug-effect-background", { tracker: tracker() }),
    (err) => err.couldntConfirm === true || err.completionGate === true,
  );
});

// ── the picker's look probes, through the run loop ─────────────────────────────────────────────

function savePage({ works }) {
  let moved = false;
  const page = livePage([CLEAN, CLEAN, CLEAN], async (p) => {
    if (!works) return;
    moved = true;
    const r = req("POST", "https://drive.test/api/items");
    p.emit("request", r); p.emit("response", resp(r, 200, { id: "i1" })); p.emit("requestfinished", r);
    await settle();
  });
  const base = page.evaluate;
  page.evaluate = async (script, arg) => (script === pageScripts.countPageText ? (moved ? 2 : 1) : base(script, arg));
  page.url = () => (moved ? "https://drive.test/item/9" : "https://drive.test/form");
  return page;
}
const saveEffect = () => ({
  kind: "save", required_rung: "completed", target_rung: "completed", quorum: { witnesses: 1, identity_required: false },
  checks: [
    { id: "c1", rung: "completed", witness: "transport", role: "support", probe: { type: "request_chain", method: "mutating", status: "2xx" } },
    { id: "c2", rung: "accepted", witness: "renderer", role: "support", probe: { type: "url_stays", pattern: "^https:\/\/drive\.test\/item\/[^/?#]+" } },
    { id: "c3", rung: "result_exists", witness: "renderer", role: "support", probe: { type: "row_with_identity", key: "{{item_title}}", delta: 1 } },
    { id: "c4", rung: "completed", witness: "renderer", role: "veto", probe: { type: "failure_signal", scope: "new_since_baseline" } },
  ],
});
const saveStep = () => stepWith(saveEffect(), { destructive: true, intent: "Save the item" });

test("runPlan: url_stays and row_with_identity are looked at after the action and shown in the step's proof", async () => {
  const result = await runPlan(savePage({ works: true }), saveStep(), { item_title: "Quarterly plan" }, 0, "slug-look-ok", { tracker: tracker() });
  const s = result.proof.steps[0];
  assert.strictEqual(s.verdict, "proven");
  assert.deepStrictEqual(Object.fromEntries(s.checks.filter((c) => c.id).map((c) => [c.id, c.answer])), { c1: "yes", c2: "yes", c3: "yes", c4: "no" });
});

test("runPlan must not pass: a save that did nothing leaves the looks at 'no' and the step unconfirmed", async () => {
  await assert.rejects(
    runPlan(savePage({ works: false }), saveStep(), { item_title: "Quarterly plan" }, 0, "slug-look-nothing", { tracker: tracker() }),
    (err) => err.couldntConfirm === true && err.proof.steps[0].checks.filter((c) => c.id === "c2" || c.id === "c3").every((c) => c.answer === "no"),
  );
});

test("runPlan: a value too short to look for is 'cant_tell', so the look cannot prove anything", async () => {
  const result = await runPlan(savePage({ works: true }), saveStep(), { item_title: "ab" }, 0, "slug-look-short", { tracker: tracker() });
  const row = result.proof.steps[0].checks.find((c) => c.id === "c3");
  assert.strictEqual(row.answer, "cant_tell");
});

// ── later_step, through the run loop ──────────────────────────────────────────────────────────

function downloadThenUpload(laterKey) {
  const eff = {
    kind: "download", required_rung: "completed", target_rung: "completed", quorum: { witnesses: 2, identity_required: false },
    checks: [
      // calibrated by an earlier proven run, so the quorum of two counts; the page shows one witness (the gate), no request is seen
      { id: "c1", rung: "completed", witness: "transport", role: "support", calibrated: true, calibration_basis: ["c1"], probe: { type: "request_chain", method: "mutating", status: "2xx" } },
      { id: "c2", rung: "completed", witness: "later_step", role: "support", probe: { type: "later_step", step: laterKey } },
    ],
  };
  return [
    { type: "keyboard_shortcut", value: "Enter", intent: "Download the report", source_step_key: "k-download", validation: { effect: eff } },
    { type: "keyboard_shortcut", value: "Tab", intent: "Upload it again", source_step_key: "k-upload" },
  ];
}

test("runPlan: a download nothing could confirm is proven afterwards by the later step that needs its file", async () => {
  const result = await runPlan(livePage([CLEAN, CLEAN, TRAY, DONE]), downloadThenUpload("k-upload"), {}, 0, "slug-later-ok", { tracker: tracker() });
  const s = result.proof.steps[0];
  assert.strictEqual(s.verdict, "proven", "judged again with the later step's yes: the gate's witness and the later step make the two it needs");
  assert.strictEqual(s.checks.find((c) => c.id === "c2").answer, "yes");
  assert.ok(!result.proof.notes.some((n) => /Download the report/.test(n)), "the download's own 'couldn't confirm' note is withdrawn");
});

test("runPlan must not pass: when the named later step does not exist, the download stays unconfirmed and the run says so", async () => {
  const result = await runPlan(livePage([CLEAN, CLEAN, TRAY, DONE]), downloadThenUpload("k-deleted"), {}, 0, "slug-later-gone", { tracker: tracker() });
  assert.strictEqual(result.proof.steps[0].verdict, "couldnt_confirm");
  assert.ok(result.proof.notes.some((n) => /Download the report/.test(n)), "the run still says it could not confirm the download");
});

test("BUILD-103: a press that sends files picked earlier is gated like an upload, even when it is not the last action", async () => {
  const page = livePage([CLEAN, DONE], async (p) => {
    const r = req("POST", "https://drive.test/upload/abc123");
    p.emit("request", r); p.emit("response", resp(r, 200, { id: "f1" })); p.emit("requestfinished", r);
    await settle();
  });
  const t = tracker();
  const plan = [...stepWith(effect()), { type: "keyboard_shortcut", value: "Tab", intent: "Move on" }];
  await runPlan(page, plan, {}, 0, "slug-press-upload", { tracker: t });
  const gate = t.events.find(([c, d]) => c === "completion" && d.si === 0);
  assert.ok(gate, "the press was gated");
  assert.strictEqual(gate[1].mode, "full", "an upload's full wait, not the short one");
});
