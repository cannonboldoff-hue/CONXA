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
// How long bars that each show a fixed value may stay unchanged before the wait calls them a picture, not work
// (EXEC-61: GitHub draws its language and pulse charts as role=progressbar). A full wait gives them the stall the
// compiler writes for a step that is not long (effects.py, 20 s). An upload passes 0: it keeps its own rules.
const STATIC_BAR_MS = 3000;
const FULL_STATIC_BAR_MS = 20000;

// Every visible indicator reports a value (aria-valuenow, data-progress, a <progress> value): a bar, never a spinner.
function allDeterminate(sample) {
  const n = sample.busyCount || 0;
  const vals = String(sample.bars || "").split("|");
  return n > 0 && vals.length === n && vals.every(Boolean);
}

function newTexts(sample, baseline) {
  const before = new Set((baseline && baseline.regions) || []);
  return ((sample && sample.regions) || []).filter((t) => !before.has(t));
}

// Only ever read against the app's LEARNED progress words, never the defaults. A recording can learn a word that also
// sits in the app's result line ("upload" in "1 upload complete"), because words are kept without their phrase; a
// text that says the work is finished is then never work in flight. The English defaults still decide on their own.
const FINISHED_RE = /\b(complete|completed|done|finished|uploaded|saved|sent|success|successful|succeeded)\b/i;

/** Pure. What one page sample says, measured against the sample taken before the action. */
function classifySample(sample, baseline, signals = null) {
  const fresh = newTexts(sample, baseline);
  // The app's own words (app_knowledge.js::signalsFor) count beside the English defaults.
  // A text that carries the app's own success wording is a result, not work in flight, even when it also shares a
  // word with the app's progress list (an older recording could learn "complete" as progress).
  const busyText = fresh.find((t) => IN_PROGRESS_RE.test(t)
    || (signals && signals.progress && signals.progress.test(t) && !FINISHED_RE.test(t) && !(signals.success && signals.success.test(t)))) || null;
  const busy = (sample.busyCount || 0) > ((baseline && baseline.busyCount) || 0) || !!busyText;
  const failure = classifyCompletionFailure(fresh, signals && signals.failure);
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
 *   dialogQueue?: any[], stallMs?: number, staticBarMs?: number, notebook?: object, signals?: {progress: RegExp|null, failure: RegExp|null}}} o
 *   `staticBarMs`: bars that each show a fixed number and stay unchanged this long are a picture, not work (0 = off). `stallMs`:
 *   an indicator that reads the same for this long is on screen, not working (see `still` on a "done" verdict). `signals` are the app's own
 *   words (app_knowledge.js::signalsFor). `notebook` (evidence.js) widens what is read from the one page
 *   to every tab and visible frame the run owns; without it the wait reads `page` alone, as before.
 * @returns {Promise<{status: "done"|"failed"|"timed_out"|"still_working"|"no_sign"|"cant_tell"|"handoff",
 *   evidence?: string, reason?: string, waitedMs: number, warning?: string, kind?: string}>}
 */
async function waitForCompletion(page, o) {
  const { baseline, sinceMs, budgetMs, dialogQueue, deadlineAt, cancelCheck } = o;
  const nb = o.notebook || null;
  const full = o.mode === "full";
  const pollMs = o.pollMs ?? 250;
  const quietMs = o.quietMs ?? 500;
  const startGraceMs = o.startGraceMs ?? DEFAULT_START_GRACE_MS;
  const deadlineMarginMs = o.deadlineMarginMs ?? DEADLINE_MARGIN_MS;
  const pendingBudgetMs = o.pendingBudgetMs ?? budgetMs;
  const staticBarMs = o.staticBarMs ?? (full ? FULL_STATIC_BAR_MS : STATIC_BAR_MS);
  const start = Date.now();
  const out = (status, extra = {}) => ({ status, waitedMs: Date.now() - start, ...extra });
  const baseUrl = baseline && baseline.url;
  let quietSince = null;
  let pendingSince = null;
  let sawPendingGrace = false;
  let started = false;
  let lastSig = null;
  let lastChangeAt = Date.now();
  let sawBusy = false;
  let sawOpenRequest = false;

  for (;;) {
    if (cancelCheck && cancelCheck()) return out("still_working", { reason: "cancelled" });
    if (dialogQueue && dialogQueue.length) return out("handoff", { reason: "native_dialog" });

    let sample;
    try {
      sample = nb ? await nb.sample() : await evalOn(page, pageScripts.completionProbe, undefined, 2000);
    } catch (_) { return out("cant_tell", { reason: "probe_unavailable" }); }
    // A page that cannot answer (closed, hung) is not evidence of anything: never fail a run on it,
    // and never call it a success either.
    if (!sample || sample === EVAL_TIMED_OUT) return out("cant_tell", { reason: "probe_unavailable" });

    const c = classifySample(sample, baseline, o.signals || null);
    // Motion: a progress indicator that has shown the same thing (same count, same values, same status text) for the
    // whole stall window is on screen, not working. A finished tray's bar or a hover card's thumbnail spinner stays
    // visible forever; waiting for it to leave never ends. Work that is really in flight is held by its open request.
    const sig = `${sample.busyCount || 0}|${sample.bars || ""}|${(sample.regions || []).join("\n")}`;
    if (sig !== lastSig) { lastSig = sig; lastChangeAt = Date.now(); }
    // Bars that only show a number and never move are let go as well; a spinner or progress words still hold the
    // wait, and a full wait is still held by any open request. `still` keeps it a cant_tell, never a yes.
    const staticBar = staticBarMs > 0 && c.busy && !c.busyText && allDeterminate(sample) && Date.now() - lastChangeAt >= staticBarMs;
    const stalled = !!(o.stallMs && c.busy && Date.now() - lastChangeAt >= o.stallMs) || staticBar;
    if (c.busy) sawBusy = true;
    const urlNow = baseUrl ? pageUrl(page) : null;
    // A first sign the app took the work. Silence before this proves nothing: a tray can open late.
    if (c.busy || newTexts(sample, baseline).length || (urlNow && urlNow !== baseUrl)
        || (nb && nb.sawStart())
        || (full && (nb ? nb.mutationStarted() : pageStatus.mutationStarted(page, sinceMs)))) started = true;

    if (full) {
      if (c.failure) return out("failed", { evidence: `the app said "${c.failure.evidence}"`, kind: c.failure.kind });
      const refused = nb ? nb.mutationFailure() : pageStatus.mutationFailure(page, sinceMs);
      if (refused) return out("failed", { evidence: requestFailureEvidence(refused), kind: "action_failed" });
    }

    const busyNow = c.busy && !stalled;
    let pending = false;
    if (full && !busyNow && (nb ? nb.pendingMutations() : pageStatus.pendingMutations(page, sinceMs)) > 0) {
      pendingSince = pendingSince ?? Date.now();
      if (Date.now() - pendingSince <= pendingBudgetMs) { pending = true; sawOpenRequest = true; }
      else sawPendingGrace = true;
    } else {
      pendingSince = null;
    }

    if (c.busy) {
      // A decision popup would hold the page "busy" forever; it is not ours to wait out or answer.
      const dlg = await evalOn(page, pageScripts.dialogProbe, undefined, 2000).catch(() => null);
      if (dlg && dlg !== EVAL_TIMED_OUT && fork.isBlockingDecision(dlg)) return out("handoff", { reason: "decision_popup" });
    }

    if (!busyNow && !pending) {
      if (full && !started) {
        if (Date.now() - start >= startGraceMs) return out("no_sign");
      } else {
        quietSince = quietSince ?? Date.now();
        if (Date.now() - quietSince >= quietMs) {
          // The step that ended the run may have left a question behind (a "Replace or keep both?").
          // Nothing after it will look, so this is the last chance to say so.
          // Only a surface that blocks the page stops the run here. One that leaves the page usable is reported
          // (`leftOpen`) and the step's own proof decides, so a finished-work panel can never fail a step that worked.
          let leftOpen = null;
          if (full) {
            const dlg = await evalOn(page, pageScripts.dialogProbe, undefined, 2000).catch(() => null);
            if (dlg && dlg !== EVAL_TIMED_OUT && fork.isBlockingDecision(dlg)) return out("handoff", { reason: "decision_popup" });
            if (dlg && dlg !== EVAL_TIMED_OUT && fork.isDecisionDialog(dlg)) leftOpen = dlg;
          }
          // Did the work visibly END (an indicator that was showing has gone, or a request that was open has closed)?
          // A step that is only quiet, with an indicator that never moved, is not shown to have finished: `still` tells
          // the caller to count this as "couldn't tell", never as a yes.
          const seenEnd = sawOpenRequest || (sawBusy && !c.busy);
          return out("done", {
            ...(c.busy && stalled && !seenEnd ? { still: true, busyWhat: sample.busyWhat || "" } : {}),
            ...(leftOpen ? { leftOpen } : {}),
            ...(sawPendingGrace ? { warning: "The app was still sending data in the background when this step ended." } : {}),
          });
        }
      }
    } else {
      quietSince = null;
    }

    const now = Date.now();
    const evidence = c.busyText || "it still showed progress";
    // Stop before the run's own time limit: the caller must still be listening when the answer arrives.
    if (deadlineAt && (c.busy || pending) && now >= deadlineAt - deadlineMarginMs) {
      // `open`: the data-changing requests still open, by endpoint only, so a step that never settles can be told apart
      // (a request that really is in flight, a page's own background call, or progress words still on screen).
      return out("still_working", { reason: "deadline", evidence, busyWhat: sample.busyWhat || "", open: nb && nb.openMutations ? nb.openMutations() : [] });
    }
    if (now - start >= budgetMs) {
      return full ? out("timed_out", { evidence, busyWhat: sample.busyWhat || "" }) : out("still_working", { reason: "budget", evidence, busyWhat: sample.busyWhat || "" });
    }
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
}

/** One probe, taken just before the action, that later samples are measured against. Never throws.
 *  With a notebook the probe was already taken (across every tab and visible frame) when it opened. */
async function captureCompletionBaseline(page, notebook = null) {
  try {
    if (notebook) return notebook.baseline ? { ...notebook.baseline, url: pageUrl(page) } : null;
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
