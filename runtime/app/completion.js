"use strict";
/**
 * completion.js — "the action ran" is not "the action finished".
 *
 * A click that starts an upload, a download, a submit or a background job returns the moment the
 * browser has dispatched it. The page then keeps working (a progress tray, a spinner, a request still
 * in flight) and may end in failure. verifyStep only proves the page reacted, which a progress tray
 * already satisfies, so without this gate a run is reported "done" while the work is still running or
 * after it failed.
 *
 * This gate runs AFTER a step passed its own check and BEFORE the run moves on. It only reads the
 * page; it never acts, never answers a popup and never retries (the action already happened, so a
 * retry could do it twice). Only what is NEW since just before the action counts, so a permanent
 * spinner or an old error banner never fails a run.
 *
 *   short  every other step that can start something: wait (bounded) for visible progress to clear,
 *          then carry on. It never fails the step: the next step's own checks take over.
 *   full   an upload, or the last step that acts in the plan: also wait for data-changing requests
 *          to finish and fail the step when the app says it did not work. Silence only counts as
 *          "done" once the app has shown a first sign of taking the work (a busy indicator, new status
 *          text, a data-changing request, a new address); with no sign inside startGraceMs the answer
 *          is "no_sign", never "done".
 *
 * Verdicts:
 *   "done"          progress cleared and nothing failed.
 *   "failed"        the app said it did not work (full mode).
 *   "timed_out"     a full wait ran out of its own budget with progress still showing.
 *   "still_working" the work was still going when the run's time limit or the budget (short) was reached.
 *   "no_sign"       a full wait saw nothing that says the step started: it cannot say it finished.
 *   "cant_tell"     the page could not be read (closed, hung): not evidence of anything.
 *   "handoff"       a decision popup / native dialog is up: not this gate's to decide, so the caller
 *                   carries on and the popup machinery owns it.
 */
const pageScripts = require("./page_scripts");
const pageStatus = require("./page_status");
const { evalOn, EVAL_TIMED_OUT } = require("./page_eval");
const { classifyCompletionFailure } = require("./fault_classify");
const fork = require("./fork");

// What an app shows while it is still working, in a status region. One definition, shared with fork.js
// so a progress tray is the same thing to the popup rules and to this gate.
const { IN_PROGRESS_RE } = fork;

// How long a full wait lets the app show a first sign of taking the work before it says "no_sign".
const DEFAULT_START_GRACE_MS = 3000;
// A wait stops this long before the run's own time limit so the answer still reaches the caller.
const DEADLINE_MARGIN_MS = 20000;

function newTexts(sample, baseline) {
  const before = new Set((baseline && baseline.regions) || []);
  return ((sample && sample.regions) || []).filter((t) => !before.has(t));
}

/** Pure. What one page sample says, measured against the sample taken before the action. */
function classifySample(sample, baseline) {
  const fresh = newTexts(sample, baseline);
  const busyText = fresh.find((t) => IN_PROGRESS_RE.test(t)) || null;
  const busy = (sample.busyCount || 0) > ((baseline && baseline.busyCount) || 0) || !!busyText;
  const failure = classifyCompletionFailure(fresh);
  return { busy, busyText, failure };
}

function requestFailureEvidence(f) {
  return `a request to the app failed (${f.why})`;
}

function pageUrl(page) {
  try { return typeof page.url === "function" ? page.url() : null; } catch (_) { return null; }
}

/**
 * @param {object} page
 * @param {{baseline: {busyCount:number, regions:string[], url?:string}, sinceMs: number,
 *   mode?: "short"|"full", budgetMs: number, pendingBudgetMs?: number, pollMs?: number, quietMs?: number,
 *   startGraceMs?: number, deadlineAt?: number, deadlineMarginMs?: number, cancelCheck?: () => boolean,
 *   dialogQueue?: any[]}} o
 * @returns {Promise<{status: "done"|"failed"|"timed_out"|"still_working"|"no_sign"|"cant_tell"|"handoff",
 *   evidence?: string, reason?: string, waitedMs: number, warning?: string, kind?: string}>}
 */
async function waitForCompletion(page, o) {
  const { baseline, sinceMs, budgetMs, dialogQueue, deadlineAt, cancelCheck } = o;
  const full = o.mode === "full";
  const pollMs = o.pollMs ?? 250;
  const quietMs = o.quietMs ?? 500;
  const startGraceMs = o.startGraceMs ?? DEFAULT_START_GRACE_MS;
  const deadlineMarginMs = o.deadlineMarginMs ?? DEADLINE_MARGIN_MS;
  const pendingBudgetMs = o.pendingBudgetMs ?? budgetMs;
  const start = Date.now();
  const out = (status, extra = {}) => ({ status, waitedMs: Date.now() - start, ...extra });
  const baseUrl = baseline && baseline.url;
  let quietSince = null;
  let pendingSince = null;
  let sawPendingGrace = false;
  let started = false;

  for (;;) {
    if (cancelCheck && cancelCheck()) return out("still_working", { reason: "cancelled" });
    if (dialogQueue && dialogQueue.length) return out("handoff", { reason: "native_dialog" });

    let sample;
    try {
      sample = await evalOn(page, pageScripts.completionProbe, undefined, 2000);
    } catch (_) { return out("cant_tell", { reason: "probe_unavailable" }); }
    // A page that cannot answer (closed, hung) is not evidence of anything: never fail a run on it,
    // and never call it a success either.
    if (!sample || sample === EVAL_TIMED_OUT) return out("cant_tell", { reason: "probe_unavailable" });

    const c = classifySample(sample, baseline);
    const urlNow = baseUrl ? pageUrl(page) : null;
    // A first sign the app took the work. Silence before this proves nothing: a tray can open late.
    if (c.busy || newTexts(sample, baseline).length || (urlNow && urlNow !== baseUrl)
        || (full && pageStatus.mutationStarted(page, sinceMs))) started = true;

    if (full) {
      if (c.failure) return out("failed", { evidence: `the app said "${c.failure.evidence}"`, kind: c.failure.kind });
      const refused = pageStatus.mutationFailure(page, sinceMs);
      if (refused) return out("failed", { evidence: requestFailureEvidence(refused), kind: "action_failed" });
    }

    let pending = false;
    if (full && !c.busy && pageStatus.pendingMutations(page, sinceMs) > 0) {
      pendingSince = pendingSince ?? Date.now();
      if (Date.now() - pendingSince <= pendingBudgetMs) pending = true;
      else sawPendingGrace = true;
    } else {
      pendingSince = null;
    }

    if (c.busy) {
      // A decision popup would hold the page "busy" forever; it is not ours to wait out or answer.
      const dlg = await evalOn(page, pageScripts.dialogProbe, undefined, 2000).catch(() => null);
      if (dlg && dlg !== EVAL_TIMED_OUT && fork.isDecisionDialog(dlg)) return out("handoff", { reason: "decision_popup" });
    }

    if (!c.busy && !pending) {
      if (full && !started) {
        if (Date.now() - start >= startGraceMs) return out("no_sign");
      } else {
        quietSince = quietSince ?? Date.now();
        if (Date.now() - quietSince >= quietMs) {
          // The step that ended the run may have left a question behind (a "Replace or keep both?").
          // Nothing after it will look, so this is the last chance to say so.
          if (full) {
            const dlg = await evalOn(page, pageScripts.dialogProbe, undefined, 2000).catch(() => null);
            if (dlg && dlg !== EVAL_TIMED_OUT && fork.isDecisionDialog(dlg)) return out("handoff", { reason: "decision_popup" });
          }
          return out("done", sawPendingGrace
            ? { warning: "The app was still sending data in the background when this step ended." }
            : {});
        }
      }
    } else {
      quietSince = null;
    }

    const now = Date.now();
    const evidence = c.busyText || "it still showed progress";
    // Stop before the run's own time limit: the caller must still be listening when the answer arrives.
    if (deadlineAt && (c.busy || pending) && now >= deadlineAt - deadlineMarginMs) {
      return out("still_working", { reason: "deadline", evidence });
    }
    if (now - start >= budgetMs) {
      return full ? out("timed_out", { evidence }) : out("still_working", { reason: "budget", evidence });
    }
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
}

/** One probe, taken just before the action, that later samples are measured against. Never throws. */
async function captureCompletionBaseline(page) {
  try {
    const s = await evalOn(page, pageScripts.completionProbe, undefined, 2000);
    return s && s !== EVAL_TIMED_OUT ? { ...s, url: pageUrl(page) } : null;
  } catch (_) { return null; }
}

function waitedPhrase(ms) {
  const s = Math.round(ms / 1000);
  return s >= 60 ? `${Math.round(s / 60)} minute${Math.round(s / 60) === 1 ? "" : "s"}` : `${s} seconds`;
}

/** Plain-language reason for a failed, timed-out or out-of-time verdict. */
function completionMessage(v) {
  if (v.status === "failed") return `This step didn't finish: ${v.evidence}.`;
  if (v.status === "still_working") {
    return "This step was still in progress when the run ran out of time, so it stopped instead of reporting success. "
      + "The work may still finish on its own.";
  }
  return `This step was still in progress after ${waitedPhrase(v.waitedMs)}, so the run stopped instead of reporting success.`;
}

/** Plain-language note for a verdict that is neither a pass nor a failure, or null when there is none. */
function completionWarning(v) {
  if (v.status === "no_sign") return "I couldn't confirm this step finished: nothing showed that it had started.";
  if (v.status === "cant_tell") return "I couldn't check whether this step finished, because the page didn't answer.";
  if (v.status === "still_working") return "This step was still working when the run moved on.";
  return null;
}

module.exports = {
  waitForCompletion,
  captureCompletionBaseline,
  classifySample,
  completionMessage,
  completionWarning,
  IN_PROGRESS_RE,
  DEFAULT_START_GRACE_MS,
  DEADLINE_MARGIN_MS,
};
