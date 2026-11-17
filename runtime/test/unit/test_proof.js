"use strict";
// proof.js: the fixed rules that turn check answers into a verdict and a next move. Pure, no page.
const test = require("node:test");
const assert = require("node:assert");
const proof = require("../../app/proof");

const sup = (rung, witness, answer, extra = {}) => ({ rung, witness, role: "support", answer, ...extra });
const veto = (answer, extra = {}) => ({ rung: "completed", witness: "renderer", role: "veto", answer, ...extra });

test("judge: a yes at the required rung from one witness is proven", () => {
  const r = proof.judge([sup("accepted", "renderer", "yes")], { requiredRung: "accepted" });
  assert.deepStrictEqual([r.verdict, r.rung], ["proven", "accepted"]);
});

test("judge: a higher rung proves the lower ones", () => {
  const r = proof.judge([sup("completed", "renderer", "yes")], { requiredRung: "accepted" });
  assert.strictEqual(r.verdict, "proven");
});

test("judge: a lower rung does not prove a higher requirement; it can only be couldnt_confirm", () => {
  const r = proof.judge([sup("performed", "target", "yes"), sup("accepted", "renderer", "yes")], { requiredRung: "completed" });
  assert.deepStrictEqual([r.verdict, r.rung], ["couldnt_confirm", "accepted"]);
});

test("judge: cant_tell and no are never success", () => {
  for (const answer of ["cant_tell", "no"]) {
    assert.strictEqual(proof.judge([sup("accepted", "renderer", answer)], { requiredRung: "accepted" }).verdict, "couldnt_confirm", answer);
  }
  assert.strictEqual(proof.judge([], {}).verdict, "couldnt_confirm", "no checks at all proves nothing");
});

test("judge: a veto beats any amount of good news; nothing-happened needs positive proof", () => {
  const checks = [sup("completed", "renderer", "yes"), sup("result_exists", "fresh_look", "yes"), veto("yes", { evidence: "Upload failed" })];
  const a = proof.judge(checks, { requiredRung: "completed" });
  assert.deepStrictEqual([a.verdict, a.reason], ["failed_may_have_happened", "Upload failed"]);
  assert.strictEqual(proof.judge(checks, { requiredRung: "completed", nothingHappened: true }).verdict, "failed_nothing_happened");
  assert.notStrictEqual(proof.judge([sup("completed", "renderer", "yes"), veto("no")], { requiredRung: "completed" }).verdict, "failed_may_have_happened", "a veto answered no is not a failure");
});

test("judge: quorum counts distinct witnesses, not checks", () => {
  const two = { requiredRung: "result_exists", quorum: { witnesses: 2 } };
  const sameWitness = [sup("result_exists", "renderer", "yes"), sup("result_exists", "renderer", "yes")];
  assert.strictEqual(proof.judge(sameWitness, two).verdict, "couldnt_confirm", "two checks on one screen are one witness");
  const separate = [sup("result_exists", "renderer", "yes"), sup("completed", "transport", "yes")];
  assert.strictEqual(proof.judge(separate, two).verdict, "couldnt_confirm", "the transport check is below the required rung");
  const both = [sup("result_exists", "renderer", "yes"), sup("result_exists", "fresh_look", "yes")];
  assert.strictEqual(proof.judge(both, two).verdict, "proven");
});

test("judge: identity is required when asked, so 'a file appeared' is not 'this file'", () => {
  const o = { requiredRung: "result_exists", quorum: { witnesses: 1, identityRequired: true } };
  assert.strictEqual(proof.judge([sup("result_exists", "renderer", "yes")], o).verdict, "couldnt_confirm");
  assert.strictEqual(proof.judge([sup("result_exists", "renderer", "yes", { identity: true })], o).verdict, "proven");
});

test("judge: not_yet is still_working; blocked and partly_done follow the fixed order", () => {
  assert.strictEqual(proof.judge([sup("accepted", "renderer", "not_yet")], { requiredRung: "accepted" }).verdict, "still_working");
  assert.strictEqual(proof.judge([], { progressing: true }).verdict, "still_working");
  assert.strictEqual(proof.judge([sup("accepted", "renderer", "yes")], { blocked: true }).verdict, "blocked", "a question in the way comes first");
  assert.strictEqual(proof.judge([], { items: ["proven", "couldnt_confirm"] }).verdict, "partly_done");
  assert.strictEqual(proof.judge([], { items: ["couldnt_confirm", "couldnt_confirm"] }).verdict, "couldnt_confirm");
});

test("judge: the same checks always give the same verdict (no scores, no order effects)", () => {
  const checks = [sup("accepted", "renderer", "yes"), sup("completed", "transport", "yes"), veto("no")];
  const forward = proof.judge(checks, { requiredRung: "completed" });
  const backward = proof.judge([...checks].reverse(), { requiredRung: "completed" });
  assert.deepStrictEqual(forward, backward);
});

test("nextMove: every verdict x situation cell of the design table", () => {
  const safe = { idempotent: true, attended: true };
  const unsafe = { idempotent: false, attended: true };
  const nobody = { idempotent: false, attended: false };
  const m = (v, c) => proof.nextMove(v, c).do;

  for (const c of [safe, unsafe, nobody]) assert.strictEqual(m("proven", c), "continue");
  for (const c of [safe, unsafe, nobody]) assert.strictEqual(m("still_working", c), "wait");
  for (const c of [safe, unsafe]) assert.strictEqual(m("failed_nothing_happened", c), "recover");
  assert.strictEqual(proof.nextMove("failed_nothing_happened", nobody).ai, false, "recovery without AI when nobody is there");
  assert.strictEqual(proof.nextMove("failed_nothing_happened", unsafe).ai, true);

  assert.strictEqual(m("failed_may_have_happened", safe), "retry");
  assert.strictEqual(m("failed_may_have_happened", unsafe), "look", "go and look first, never retry blind");
  assert.strictEqual(m("failed_may_have_happened", nobody), "fail");

  assert.strictEqual(m("blocked", safe), "popup");
  assert.strictEqual(m("blocked", nobody), "fail");

  assert.strictEqual(m("partly_done", safe), "redo_unproven");
  assert.strictEqual(m("partly_done", unsafe), "stop_report");
  assert.strictEqual(m("partly_done", nobody), "stop_report");

  assert.deepStrictEqual(proof.nextMove("couldnt_confirm", safe), { do: "continue", note: true });
  assert.strictEqual(m("couldnt_confirm", unsafe), "fail", "nobody is asked unless the author said so");
  assert.strictEqual(proof.nextMove("couldnt_confirm", { ...unsafe, onUnverified: "handover" }).do, "handover");
  assert.strictEqual(m("couldnt_confirm", { ...safe, attended: false }), "fail");
  assert.strictEqual(m("nonsense", safe), "fail", "an unknown verdict fails closed");
});

test("requiredRungFor: older steps ask for what their author had", () => {
  const plain = { type: "click" };
  assert.strictEqual(proof.requiredRungFor(plain), "performed");
  assert.strictEqual(proof.requiredRungFor(plain, { hasRequiredAssertion: true }), "accepted");
  assert.strictEqual(proof.requiredRungFor(plain, { full: true, hasRequiredAssertion: true }), "completed");
  assert.strictEqual(proof.requiredRungFor({ validation: { effect: { required_rung: "result_exists" } } }, { full: true }), "result_exists");
  assert.strictEqual(proof.requiredRungFor({ validation: { effect: { required_rung: "bogus" } } }), "performed");
});

test("BUILD-103: the rules follow the effect kind, not the step type", () => {
  const withKind = (type, kind) => ({ type, validation: { effect: { kind } } });
  // A file pick the page keeps for a later press: picking again is harmless, and it is not the upload.
  assert.strictEqual(proof.effectKind(withKind("upload", "choose_files")), "choose_files");
  assert.strictEqual(proof.safeToRepeat(withKind("upload", "choose_files")), true);
  // The press that sends those files is the upload: never done twice.
  assert.strictEqual(proof.effectKind(withKind("click", "upload")), "upload");
  assert.strictEqual(proof.safeToRepeat(withKind("click", "upload")), false);
  // A pack built before effects: its upload steps are uploads.
  assert.strictEqual(proof.effectKind({ type: "upload" }), "upload");
  const { isNonIdempotent } = require("../../app/step_utils");
  assert.strictEqual(isNonIdempotent(withKind("upload", "choose_files")), false);
  assert.strictEqual(isNonIdempotent({ type: "upload" }), true);
});

test("safeToRepeat: uploads and destructive steps are not", () => {
  assert.strictEqual(proof.safeToRepeat({ type: "upload" }), false);
  assert.strictEqual(proof.safeToRepeat({ type: "click", destructive: true }), false);
  assert.strictEqual(proof.safeToRepeat({ type: "click" }), true);
});

test("runOutcome and the result's first words", () => {
  assert.strictEqual(proof.runOutcome({}), "done");
  assert.strictEqual(proof.runOutcome({ notes: 2 }), "done_please_check");
  assert.strictEqual(proof.runOutcome({ parked: true, notes: 1 }), "submitted");
  assert.strictEqual(proof.runOutcome({ partly: true, parked: true }), "partly_done");
  assert.strictEqual(proof.runOutcome({ stopped: true, partly: true }), "couldnt_confirm", "a stop outranks everything");
  for (const o of ["done", "submitted", "done_please_check", "partly_done", "couldnt_confirm"]) {
    assert.strictEqual(proof.outcomeFromText(`${proof.leadWord(o)} URL: x`), o, o);
  }
  assert.strictEqual(proof.outcomeFromText("Execution cancelled."), null);
  assert.strictEqual(proof.outcomeFromText("Step 3 (click) failed"), null);
  assert.notStrictEqual(proof.outcomeFromText("Done, please check. x"), "done", "'Done, please check.' is not plain 'Done.'");
});

test("unconfirmedNote speaks in plain words", () => {
  assert.strictEqual(proof.unconfirmedNote({ intent: "Upload the report" }, 4), `I couldn't confirm that "Upload the report" finished.`);
  assert.strictEqual(proof.unconfirmedNote({}, 4), "I couldn't confirm that step 5 finished.");
  // Internal names never reach a person: a step type or a snake_case intent falls back to the step number.
  assert.strictEqual(proof.unconfirmedNote({ type: "try_dismiss", intent: "try_dismiss" }, 6), "I couldn't confirm that step 7 finished.");
  assert.strictEqual(proof.unconfirmedNote({ type: "click", intent: "activate_control_buy_now" }, 1), "I couldn't confirm that step 2 finished.");
  assert.strictEqual(proof.unconfirmedNote({ type: "click", intent: "activate_control_buy_now", label: "Click Buy Now" }, 1),
    `I couldn't confirm that "Click Buy Now" finished.`);
});

// ── the recording is the expected outcome ──────────────────────────────────────────────────────────
// A real false pass (a Drive upload that never happened, 2026-11-13): the app's background request was answered and the
// run ended quietly, but the "upload complete" the recording saw never appeared and the fresh look could not tell.
// This shape of evidence must never be "proven". Keep these cases failing if a gate is ever loosened again.
const fixture = require("../fixtures/proof_must_not_pass.json");

for (const c of fixture.cases) {
  test(`judge must not prove: ${c.name}`, () => {
    const r = proof.judge(c.checks, c.options);
    assert.notStrictEqual(r.verdict, "proven");
    assert.strictEqual(r.verdict, c.verdict);
  });
}

test("judge: when the recording's results are absent, a witness that found the result itself still proves the step", () => {
  const r = proof.judge([
    sup("completed", "transport", "yes"),
    sup("completed", "renderer", "no", { observed: true }),
    sup("result_exists", "fresh_look", "yes"),
  ], { requiredRung: "completed" });
  assert.strictEqual(r.verdict, "proven");
});

test("judge: one of the recording's results appearing is enough, the other may be a stray word", () => {
  const r = proof.judge([
    sup("completed", "renderer", "no", { observed: true }),
    sup("completed", "renderer", "yes", { observed: true }),
  ], { requiredRung: "completed" });
  assert.strictEqual(r.verdict, "proven");
});

test("judge: not being able to tell about the recording's results is not the same as them being absent", () => {
  const r = proof.judge([sup("completed", "transport", "yes"), sup("completed", "renderer", "cant_tell", { observed: true })], { requiredRung: "completed" });
  assert.strictEqual(r.verdict, "proven");
});

// A check the proof picker adds can only add: whatever it answers, a step that was proven without it stays proven, and a
// step that was not proven is never made "still working" by it (the new probes never say "not_yet").
test("judge: an added check answering no, cant_tell or not_yet never changes a proven verdict", () => {
  const base = [sup("completed", "transport", "yes")];
  for (const answer of ["no", "cant_tell", "not_yet", "yes"]) {
    for (const witness of ["renderer", "fresh_look", "later_step"]) {
      const r = proof.judge([...base, sup("completed", witness, answer)], { requiredRung: "completed" });
      assert.strictEqual(r.verdict, "proven", `${witness}:${answer}`);
    }
  }
});

test("judge: added checks that cannot look never turn 'couldn't confirm' into 'still working'", () => {
  const base = [sup("performed", "target", "yes")];
  for (const answer of ["no", "cant_tell"]) {
    const r = proof.judge([...base, sup("result_exists", "renderer", answer), sup("completed", "later_step", answer)], { requiredRung: "completed" });
    assert.strictEqual(r.verdict, "couldnt_confirm", answer);
  }
});
