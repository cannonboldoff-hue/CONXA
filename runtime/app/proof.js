"use strict";
/**
 * proof.js — what a step has actually proven, by fixed rules (no AI, no scores).
 *
 * One word, "passed", used to cover four very different claims. A step is now judged on a ladder:
 *
 *   performed < accepted < completed < result_exists
 *   (we did it) (the app took it) (the work finished) (the right thing is really there)
 *
 * Each check answers one of four things: "yes", "no", "not_yet", "cant_tell". "I couldn't tell" is
 * its own answer and never counts as success. A check has a `witness` (a separate source of evidence:
 * target | renderer | transport | filesystem | fresh_look | later_step) so two checks that read the same
 * screen are one witness, not two. A check with role "veto" is a failure signal: its answer "yes" means
 * the failure WAS seen, and it beats any amount of good news.
 *
 * This module only decides. It never reads a page, never acts, never retries. run.js feeds it the
 * answers it collected and acts on `nextMove`.
 */

const ANSWERS = Object.freeze(["yes", "no", "not_yet", "cant_tell"]);
const RUNGS = Object.freeze(["performed", "accepted", "completed", "result_exists"]);
const WITNESSES = Object.freeze(["target", "renderer", "transport", "filesystem", "fresh_look", "later_step"]);
const VERDICTS = Object.freeze([
  "proven", "still_working", "failed_nothing_happened", "failed_may_have_happened", "blocked",
  "partly_done", "couldnt_confirm",
]);
const OUTCOMES = Object.freeze(["done", "submitted", "done_please_check", "partly_done", "couldnt_confirm", "failed"]);

const rungIndex = (r) => RUNGS.indexOf(r);

/**
 * @param {Array<{rung:string, witness?:string, role?:"support"|"veto", answer:string, identity?:boolean, evidence?:string,
 *   observed?:boolean}>} checks  `observed`: the recording itself saw this result.
 * @param {{requiredRung?:string, quorum?:{witnesses?:number, identityRequired?:boolean}, blocked?:boolean,
 *   nothingHappened?:boolean, progressing?:boolean, items?:string[]}} [o]
 * @returns {{verdict:string, rung:string|null, reason?:string}} `rung` is the highest rung a supporting check proved.
 */
function judge(checks, o = {}) {
  const list = Array.isArray(checks) ? checks : [];
  const need = Math.max(0, rungIndex(RUNGS.includes(o.requiredRung) ? o.requiredRung : "accepted"));
  const support = list.filter((c) => c && c.role !== "veto");
  const yes = support.filter((c) => c.answer === "yes" && rungIndex(c.rung) >= 0);
  let rung = null;
  for (const c of yes) if (rung === null || rungIndex(c.rung) > rungIndex(rung)) rung = c.rung;

  // 1. Something is in the way that only a person can answer.
  if (o.blocked) return { verdict: "blocked", rung };
  // 2. A failure signal beats any amount of good news. "Nothing happened" needs positive proof of it:
  //    nothing sent, nothing changed. Without that the action may have landed, so it is never repeated.
  const veto = list.find((c) => c && c.role === "veto" && c.answer === "yes");
  if (veto) {
    return { verdict: o.nothingHappened ? "failed_nothing_happened" : "failed_may_have_happened", rung, reason: veto.evidence };
  }
  // 3. Enough separate witnesses at (or above) the required rung. A higher rung proves the lower ones.
  //    The recording is the expected outcome: when every result it saw ("upload complete") is absent, the weaker
  //    witnesses (a request was answered, the run ended quietly) no longer prove the work. Only a witness that
  //    found the result itself (`result_exists`) can. "I couldn't tell" about those results is not absence.
  const seen = support.filter((c) => c.observed);
  const absent = seen.length > 0 && seen.every((c) => c.answer === "no");
  const bar = absent ? Math.max(need, rungIndex("result_exists")) : need;
  const counting = yes.filter((c) => rungIndex(c.rung) >= bar);
  const q = o.quorum || {};
  if (new Set(counting.map((c) => c.witness || "renderer")).size >= Math.max(1, q.witnesses || 1)
      && (!q.identityRequired || counting.some((c) => c.identity))) {
    return { verdict: "proven", rung };
  }
  // 4. The app is clearly still working.
  if (o.progressing || support.some((c) => c.answer === "not_yet")) return { verdict: "still_working", rung };
  // 5. Some items of a batch are proven and some are not.
  if (Array.isArray(o.items) && o.items.some((v) => v === "proven") && o.items.some((v) => v !== "proven")) {
    return { verdict: "partly_done", rung };
  }
  // 6. Nothing left to ask. Never success.
  return { verdict: "couldnt_confirm", rung };
}

/**
 * What to do about a verdict. Mirrors the "next move" table of the Proof of Done design:
 * a step is repeated only with proof that the first try did nothing, and nobody is asked unless the
 * author said so (`onUnverified: "handover"`).
 * @param {string} verdict
 * @param {{idempotent?:boolean, attended?:boolean, onUnverified?:string}} [c]
 *   idempotent = safe to do twice; attended = a person can still be reached (false for a scheduled run).
 */
function nextMove(verdict, c = {}) {
  const { idempotent = false, attended = true, onUnverified = "fail" } = c;
  switch (verdict) {
    case "proven": return { do: "continue" };
    case "still_working": return { do: "wait" };
    case "failed_nothing_happened": return { do: "recover", ai: attended };
    case "failed_may_have_happened":
      if (!attended) return { do: "fail" };
      return idempotent ? { do: "retry", times: 1 } : { do: "look" };
    case "blocked": return attended ? { do: "popup" } : { do: "fail" };
    case "partly_done": return idempotent && attended ? { do: "redo_unproven" } : { do: "stop_report" };
    case "couldnt_confirm":
      if (!attended) return { do: "fail" };
      if (idempotent) return { do: "continue", note: true };
      return onUnverified === "handover" ? { do: "handover" } : { do: "fail" };
    default: return { do: "fail" };
  }
}

/**
 * The kind of work a step does: its effect's kind. A pack built before effects existed has none; its upload steps are
 * uploads. A file pick the page keeps until a later press is "choose_files", and that press is the "upload".
 */
function effectKind(step) {
  const e = step && step.validation && step.validation.effect;
  if (e && e.kind) return String(e.kind);
  return step && step.type === "upload" ? "upload" : null;
}

/** Steps that cannot safely be done twice. Pre-effect (published) workflows have no consequence label. */
function safeToRepeat(step) {
  return !(step && (step.destructive === true || effectKind(step) === "upload"));
}

/**
 * The rung a step must reach to count as proven. A step with an `effect` block names its own; an older
 * step asks for what its author had: the gate's "completed" where the gate waits for the work to finish
 * (an upload, the last acting step), "accepted" where a required assertion exists, otherwise just that
 * the action ran.
 */
function requiredRungFor(step, { full = false, hasRequiredAssertion = false } = {}) {
  const declared = step && step.validation && step.validation.effect && step.validation.effect.required_rung;
  if (RUNGS.includes(declared)) return declared;
  if (full) return "completed";
  return hasRequiredAssertion ? "accepted" : "performed";
}

/**
 * The rung one of a step's own checks (validation.assertions) proves. Words the author said mean "it worked" (only a
 * person makes a text check required; the compiler writes them advisory), absent before the action and there now
 * (`fresh`, assertions.js): that finds the result itself, so it proves the work finished. Any other check says only
 * that the app took the action.
 */
function assertionRung(result) {
  return result && result.type === "text_present" && result.required && result.fresh ? "completed" : "accepted";
}

/** One run's outcome from what its steps showed. */
function runOutcome({ stopped = false, partly = false, parked = false, notes = 0 } = {}) {
  if (stopped) return "couldnt_confirm";
  if (partly) return "partly_done";
  if (parked) return "submitted";
  return notes > 0 ? "done_please_check" : "done";
}

// The first words of an execute_skill result. Parsers read the structured `outcome` first and these
// as the fallback, so keep them fixed.
const LEAD = Object.freeze({
  done: "Done.",
  submitted: "Submitted.",
  done_please_check: "Done, please check.",
  partly_done: "Partly done.",
  couldnt_confirm: "Couldn't confirm.",
});

function leadWord(outcome) {
  return LEAD[outcome] || LEAD.done;
}

/** The outcome a result's text starts with, or null for anything else (a failure message). */
function outcomeFromText(text) {
  const t = String(text || "").trimStart();
  for (const o of ["done_please_check", "submitted", "partly_done", "couldnt_confirm", "done"]) {
    if (t.startsWith(LEAD[o])) return o;
  }
  return null;
}

/** Plain-language note for a step that finished without the proof it needed. */
function unconfirmedNote(step, index) {
  // Only words a person would say: the compiler's own description, else an intent that is already a sentence.
  // A step type or a snake_case intent token ("try_dismiss") is an internal name and never shown.
  const intent = step && typeof step.intent === "string" ? step.intent.trim() : "";
  const words = (step && typeof step.label === "string" && step.label.trim()) || (/\s/.test(intent) && !intent.includes("_") ? intent : "");
  const what = words ? `"${words}"` : `step ${index + 1}`;
  return `I couldn't confirm that ${what} finished.`;
}

module.exports = {
  ANSWERS, RUNGS, WITNESSES, VERDICTS, OUTCOMES,
  judge, nextMove, effectKind, safeToRepeat, requiredRungFor, assertionRung, runOutcome,
  leadWord, outcomeFromText, unconfirmedNote,
};
