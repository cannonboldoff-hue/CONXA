"use strict";
/**
 * effect_probes.js — answers the checks of a step's `validation.effect` (Proof of Done stage 4).
 *
 * The compiler wrote the checklist from what the recording showed; this module asks each question of the evidence
 * notebook (evidence.js) after the step ran and returns one answer per check: "yes" | "no" | "not_yet" | "cant_tell".
 * It only reads. A check whose probe this version cannot answer says "cant_tell", never "yes", so a pack compiled for a
 * later runtime cannot be over-trusted by an older one.
 *
 * A check with role "veto" is a failure signal: its answer "yes" means the failure WAS seen.
 */
const { classifyCompletionFailure } = require("./fault_classify");

const OK_STATUS = (s) => s >= 200 && s < 300;

/**
 * Words only, never the text: quoted spans, e-mail addresses, web addresses and anything with a digit are dropped,
 * so a customer's file name or record number never decides a check. The runtime twin of bridge.js::_valueFreeWords
 * (the recorder wrote the expected words with the same rule).
 */
function valueFreeWords(text) {
  const stripped = String(text || "")
    .replace(/"[^"]*"|(?<![A-Za-z0-9])'[^']*'(?![A-Za-z0-9])|“[^”]*”|‘[^’]*’/g, " ")
    .replace(/\S+@\S+/g, " ")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/\S*\d\S*/g, " ");
  const words = [];
  for (const w of stripped.toLowerCase().split(/[^\p{L}]+/u)) {
    if (w.length > 2 && !words.includes(w)) words.push(w);
    if (words.length >= 10) break;
  }
  return words;
}

function probeRequestChain(check, nb) {
  if (!nb) return { answer: "cant_tell" };
  if (nb.mutationFailure()) return { answer: "no" };
  const template = String((check.probe && check.probe.template) || "");
  const answered = nb.entries().some((e) => e.mutation && e.s != null && OK_STATUS(e.s)
    && !(e.shape && e.shape.error_flag) && (!template || e.t === template));
  if (answered) return { answer: "yes" };
  if (nb.pendingMutations() > 0) return { answer: "not_yet" };
  return { answer: "cant_tell" }; // nothing was sent that this notebook could see: that proves nothing either way
}

function probeFailureSignal(nb, gate, freshTexts, appFailure) {
  if (gate && gate.status === "failed") return { answer: "yes", evidence: gate.evidence };
  if (!nb) return { answer: "cant_tell" };
  const refused = nb.mutationFailure();
  if (refused) return { answer: "yes", evidence: `a request to the app failed (${refused.why})` };
  const failure = classifyCompletionFailure(freshTexts, appFailure);
  if (failure) return { answer: "yes", evidence: `the app said "${failure.evidence}"` };
  return { answer: "no" };
}

function probeNewText(check, nb) {
  if (!nb) return { answer: "cant_tell" };
  const want = ((check.probe && check.probe.words) || []).map((w) => String(w).toLowerCase()).filter(Boolean);
  if (!want.length) return { answer: "cant_tell" };
  // Everything the page said since just before the action, not only what it says now: a "complete" toast may be gone.
  for (const text of nb.seenNew()) {
    const words = valueFreeWords(text);
    if (want.every((w) => words.includes(w))) return { answer: "yes" };
  }
  return { answer: "no" };
}

function probeDownloadFile(nb) {
  if (!nb) return { answer: "cant_tell" };
  return nb.downloads() > 0 ? { answer: "yes" } : { answer: "cant_tell" };
}

/**
 * @param {object} effect  the step's validation.effect
 * @param {{notebook: object|null, gate: object|null, executed: boolean}} ctx
 * @returns {Array<{id:string, rung:string, witness:string, role:string, answer:string, evidence?:string, identity?:boolean}>}
 *   Checks a test run showed to be dead (`calibrated === false`) are left out: they no longer count toward anything.
 */
function evaluateChecks(effect, { notebook = null, gate = null, executed = true, signals = null, fresh = null } = {}) {
  const nb = notebook;
  const freshTexts = nb ? nb.seenNew() : [];
  const out = [];
  for (const check of (effect && effect.checks) || []) {
    if (!check || check.calibrated === false) continue;
    const type = check.probe && check.probe.type;
    let r;
    switch (type) {
      case "input_has_files": r = { answer: executed ? "yes" : "cant_tell" }; break;
      case "request_chain": r = probeRequestChain(check, nb); break;
      case "failure_signal": r = probeFailureSignal(nb, gate, freshTexts, signals && signals.failure); break;
      case "new_text": r = probeNewText(check, nb); break;
      case "download_file": r = probeDownloadFile(nb); break;
      case "fresh_look": r = fresh || { answer: "cant_tell" }; break; // looked at by run.js (fresh_look.js), which opens a tab
      default: r = { answer: "cant_tell" }; // value_equals / row_with_identity / url_stays / fresh_look / later_step: later
    }
    out.push({
      id: String(check.id || ""),
      rung: check.rung,
      witness: check.witness,
      role: check.role === "veto" ? "veto" : "support",
      answer: r.answer,
      ...(r.evidence ? { evidence: r.evidence } : {}),
    });
  }
  return out;
}

module.exports = { evaluateChecks, valueFreeWords };
