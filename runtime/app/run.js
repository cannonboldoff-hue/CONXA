"use strict";
// run.js — run-plan ORCHESTRATION and the stable public barrel for the run engine.
//
// The engine was decomposed along its natural seams (behavior-preserving); the
// one-way dependency direction is:
//   run_plan (this file)
//     → cascade.js        (Tier 1/2 recovery ladder + remedies)
//     → handlers.js       (action dispatch table, branch primitives, executeStep)
//     → assertions.js     (post-condition VERIFY family)
//     → locators.js       (withLocator modes, selector derivation, action helpers)
//     → resolution.js     (frame roots, identity-bundle resolution, GATE, override gate)
//       → interpolate.js · step_utils.js · run_config.js · recovery_log.js
//         uploads.js · retry_budget.js (leaves)
//
// Everything below remains exported from here unchanged — server.js and the
// test suite import through this barrel.

const { mapErrorToCode } = require("./tracker");
const { plainCause } = require("./errors");
const { classifyException, remedyFor, buildRepairEvent } = require("./recovery");
const { detectPreExecDrift } = require("./drift");
const { compareEnvironment } = require("./env_match");
const pageScripts = require("./page_scripts");
const { evalOn, EVAL_TIMED_OUT } = require("./page_eval");
const { createTabRegistry, resolveStepPage, stepInheritsPage } = require("./tabs");

const { appendRecoveryEvent } = require("./recovery_log");
const { interpolate } = require("./interpolate");
const {
  PAGE_LOAD_TIMEOUT_MS,
  RUN_RETENTION_MS,
  SETTLE_RETRY_ENABLED,
  SETTLE_BUDGET_MS,
  SETTLE_POLL_MS,
  MAX_LOOP_ITERATIONS,
} = require("./run_config");
const { waitForSettle } = require("./settle");
const { checkRetryBudget: _checkRetryBudget, clearRetryBudget } = require("./retry_budget");
const {
  rootCandidates,
  frameScopedInventory,
  captureEarlyDomSnapshot,
  gateLocator,
  validateOverrideSelector,
  resolveStep,
  enumerateRows,
  splitListInput,
} = require("./resolution");
const {
  stepAssertions,
  capturePreStepSignature,
  verifyStep,
  hasRequiredAssertion,
  needsStateChangedBaseline,
} = require("./assertions");
const {
  baseSelector,
} = require("./locators");
const {
  NOOP_STEP_TYPES,
  probePresent,
  executeStep,
  enrichStepsWithRecovery,
  applyStepOverrides,
  answerDialog,
} = require("./handlers");
const {
  recoverWithSelector,
  a11yRecoveryName,
  layer1Ladder,
  recoverStep,
  createActionGuard,
  maybeCapturePreStep,
} = require("./cascade");
const {
  resolveUploadPaths,
  extractZipOnce,
  uniqueDownloadName,
} = require("./uploads");
const handover = require("./handover");
const { dismissAgentNominated } = require("./dismiss_patterns");
const learnedDismissals = require("./learned_dismissals");

// checkRetryBudget is exported for server.js's budget enforcement; kept under its
// own name to make the "budget is checked there, not here" split explicit.
const checkRetryBudget = _checkRetryBudget;

// Step types that may trigger a real page navigation and need waitForLoadState after them.
// tab_open/tab_switch/popup are included so the first real step after a tab-boundary marker
// gets a load wait too — the marker itself is a no-op, but the tab it names may still be
// mid-navigation (e.g. a target=_blank popup that opens at about:blank).
const NAVIGATION_STEP_TYPES = new Set([
  "navigate", "browser_back", "browser_forward", "click", "dblclick", "right_click",
  "keyboard_shortcut",
  "if_present", "try_dismiss", "wait_for_one_of",
  "tab_open", "tab_switch", "popup",
]);

// Only wait for page load when the previous step could have triggered navigation.
async function waitForPageLoad(page, prevType) {
  if (!prevType || !NAVIGATION_STEP_TYPES.has(prevType)) return;

  await page.waitForLoadState("domcontentloaded", { timeout: PAGE_LOAD_TIMEOUT_MS }).catch(() => {});
  if (process.env.CONXA_WAIT_NETWORKIDLE === "1") {
    // networkidle never fires on analytics-heavy sites, so it never inherits the full
    // page-load budget — capped independently of how high PAGE_LOAD_TIMEOUT_MS is set.
    await page.waitForLoadState("networkidle", { timeout: Math.min(PAGE_LOAD_TIMEOUT_MS, 8000) }).catch(() => {});
  }
}

// Nothing else ever deletes a finished run's workspace (W-7), so files accumulate under
// {CONXA_DATA_DIR}/runs/ forever. Sweep it at the start of every execution — rather than
// hooking success/failure/cancel/park separately — so cleanup runs no matter how the
// *previous* run ended. Never touches the run currently starting up.
function sweepOldRuns(runsBaseDir, maxAgeMs = RUN_RETENTION_MS, excludeRunId = null) {
  let entries;
  try { entries = require("fs").readdirSync(runsBaseDir, { withFileTypes: true }); }
  catch (_) { return; }
  const cutoff = Date.now() - maxAgeMs;
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name === excludeRunId) continue;
    const dir = require("path").join(runsBaseDir, entry.name);
    try {
      if (require("fs").statSync(dir).mtimeMs < cutoff) {
        require("fs").rmSync(dir, { recursive: true, force: true });
      }
    } catch (_) { /* retried on next run */ }
  }
}

function stepFailure(step, stepIndex, cause, preShot, warnings) {
  // Shaped once here (the single funnel every step failure passes through) rather than at each
  // of the 5 places downstream that interpolate err.message into a response — see errors.js.
  // rawMessage keeps the original text (with Playwright's "Call log:" trace) for evidence/logs;
  // err.message itself is now "<plain summary> (<technical detail>)", never the raw dump.
  const rawMessage = cause && cause.message ? cause.message : String(cause);
  const { summary, detail } = plainCause(cause instanceof Error ? cause : new Error(rawMessage));
  const err = new Error(`Step ${stepIndex + 1} (${step.type}) failed: ${summary} (${detail})`);
  err.rawMessage = rawMessage;
  err.failedAt = stepIndex;
  err.failedStep = step;
  err.preShot = preShot;
  // EXEC-36/BUILD-28(a) — plain-language drift/environment-mismatch warnings accumulated over
  // the run so far, so a run that fails BECAUSE of one says so instead of reporting an ordinary
  // selector/timing failure. See server.js/failure_response.js for where these surface.
  if (Array.isArray(warnings) && warnings.length) err.warnings = warnings.slice();
  // `cause` (primaryErr) carries fields the caller needs but that this wrapper Error previously
  // dropped — earlyDomSnapshot silently never reached _buildFailureResponse, so its "prefer the
  // failure-moment snapshot" comment was dead code; verifyResults/override-validation details
  // were similarly lost.
  if (cause) {
    if (Array.isArray(cause.earlyDomSnapshot)) err.earlyDomSnapshot = cause.earlyDomSnapshot;
    if (cause.verifyFail) {
      err.verifyFail = true;
      err.verifyResults = cause.verifyResults;
    }
    if (cause.overrideValidationFailed) {
      err.overrideValidationFailed = true;
      err.overrideReason = cause.overrideReason;
      err.overrideCandidates = cause.overrideCandidates;
    }
    // EXEC-24: the recovery cascade refused to act again because an earlier attempt may
    // already have taken effect. The agent tier needs to know that before it reasons about a
    // page whose state it cannot otherwise account for; a destructive step fails closed on it.
    if (cause.actionMayHaveTakenEffect) err.actionMayHaveTakenEffect = true;
    if (cause.recoveryHaltReason) err.recoveryHaltReason = cause.recoveryHaltReason;
    if (cause.destructiveHalt) err.destructiveHalt = true;
    if (cause.frameNotFound) err.frameNotFound = true;
    if (cause.entityNotFound) err.entityNotFound = true;
    if (cause.tabNotFound) err.tabNotFound = true;
    if (cause.failedPage) err.failedPage = cause.failedPage;
    // EXEC-30: Tier A's known-pattern ladder saw an INTERCEPTED failure and cleared nothing —
    // tell the agent tier this looks like an unrecognized overlay, not a plain missing element.
    if (cause.unknownOverlay) err.unknownOverlay = true;
  }
  return err;
}

// Executes ONE step against the given `steps` array at index `i`, mutating `state` in place
// (recoveredSteps/hasExecutedStep/prevStepType/prevPage/warnings/dryRunSkipped) and throwing on
// an unrecoverable failure — exactly the per-iteration body the top-level step loop used to
// inline. Extracted so EXEC-38's `for_each` can run its body steps through the SAME machinery
// (tab resolution, dry-run skip, GATE/VERIFY, settle-retry, the full recovery cascade,
// cancellation) instead of the best-effort, no-recovery `runBranchBody` other branch primitives
// use — a loop body doing real work needs the real thing.
//
// `steps`/`i` are explicit parameters (not closed over from an outer `steps`) because a
// for_each body is a DIFFERENT array from the top-level plan; `ctx` carries everything that is
// genuinely constant for the whole run (tabs, watch, dryRun, slug, runCtx, the queues, and the
// tracker/cancelCheck/onStep/onPhase callbacks server.js supplied to runPlan).
async function executeOneStep(ctx, state, steps, i) {
  if (ctx.cancelCheck && ctx.cancelCheck()) {
    throw Object.assign(new Error("Execution cancelled"), { cancelled: true });
  }

  const step = steps[i];
  if (ctx.onStep) ctx.onStep(i);

  // Resolve which live page this step runs on. Never falls back to the previous step's page
  // on a miss (see resolveStepPage) — a same-looking element on the wrong tab is worse than
  // a clean failure here. Exception: a tab_open/tab_switch/popup marker that carries no `tab`
  // block names no tab at all (a recorder mis-stamp, e.g. a popup event attributed to the
  // page that was active when the event drained rather than the page that fired it) — treating
  // that as "go to tab_0" bounces execution back to wherever it started. Since these steps are
  // no-ops (see NOOP marker handlers below) and every real step still resolves its own tab
  // independently, simply staying on the current page is always safe here.
  let page;
  if (state.hasExecutedStep && state.prevPage && stepInheritsPage(step)) {
    page = state.prevPage;
  } else {
    try {
      // prevPage gates tabs.js's settle-on-page-switch: a step that moves execution to a
      // different page (including back to the initial page) gets a load wait and, under
      // watch mode, a bringToFront — without it the return leg of A→B→A replays invisibly
      // against a background tab.
      page = await resolveStepPage(ctx.tabs, step, { watch: ctx.watch, loadTimeoutMs: PAGE_LOAD_TIMEOUT_MS, prevPage: state.prevPage, onPhase: ctx.onPhase });
    } catch (tabErr) {
      ctx.tracker.emit("step_fail", { si: i, fc: "tab_not_found" });
      throw stepFailure(step, i, tabErr, null, state.warnings);
    }
  }

  if (state.hasExecutedStep && page === state.prevPage) await waitForPageLoad(page, state.prevStepType);

  // RT-9 — Studio-only test guard. A caller that passes `_pause_before_irreversible` asks the run to
  // stop, parked on the live page, BEFORE the first step the compiler marked `destructive` (send,
  // submit, pay, delete, publish). It resumes only when the caller says so (`_guard_release`), which
  // releases every later destructive step in the run at once. Like ai_review it is a planned pause,
  // not a failure and not recovery: it never touches recovery_stage.js or retry_budget.js, and with
  // no `guard` on ctx (every customer run, every MCP caller) this block is inert. A dry run already
  // withholds destructive steps, so the guard has nothing to do there.
  if (ctx.guard && ctx.guard.pause && !ctx.guard.released && !ctx.dryRun && step.destructive === true) {
    if (state.loopDepth > 0) {
      // A pause inside a for_each body would lose the loop's row cursor (the same reason a handover
      // can't live there), so fail closed rather than run an irreversible step unguarded.
      ctx.tracker.emit("step_fail", { si: i, fc: "guard_inside_loop" });
      throw stepFailure(step, i, new Error(
        "Test stopped before a repeated step that can't be undone: the guard can't pause inside a loop. " +
        "Turn the guard off to run it, or test it somewhere safe."
      ), null, state.warnings);
    }
    const remaining = steps.slice(i).filter((s) => s && s.destructive === true).length;
    throw Object.assign(new Error("guard_pause"), { guardPause: true, stepIndex: i, step, page, remaining });
  }

  // EXEC-13: ai_review is a planned pause (author-placed reasoning checkpoint), not a page
  // action and not a Tier 1-4 recovery candidate — it carries no selector/identity_bundle, so
  // it must never reach executeStep/HANDLERS or recoverStep. server.js validates a resumed
  // review answer against the step's output_schema and binds it under `__ai_review_answer_<i>`
  // in `inputs` *before* calling runPlan (see the `_resumeReviewStep` block); this interception
  // just consumes it and moves on. A fresh arrival (no bound answer yet) throws a distinct,
  // non-recovery signal that server.js's catch block special-cases (like `cancelled`/
  // `session_expired`) to park the page and return a review request instead of a failure.
  if (step.type === "ai_review") {
    const answerKey = `__ai_review_answer_${i}`;
    if (Object.prototype.hasOwnProperty.call(state.inputs, answerKey)) {
      const outputName = step.output_name || `ai_review_output_${i}`;
      state.inputs[outputName] = state.inputs[answerKey];
      delete state.inputs[answerKey];
      state.hasExecutedStep = true;
      state.prevStepType = step.type;
      state.prevPage = page;
      return;
    }
    throw Object.assign(new Error("ai_review_pause"), { reviewPause: true, stepIndex: i, step, page });
  }

  // EXEC-21 (hand-over shape): a step the recording deliberately left for a PERSON, not the
  // agent — 2FA, a CAPTCHA, an e-signature. Like ai_review, this is a planned pause: no
  // selector, no identity_bundle, never reaches executeStep/HANDLERS or the recovery cascade.
  // Two speeds (see handover.js's header): most hand-overs finish inside HANDOVER_INCALL_MS and
  // never leave this function — the person clicks the in-page banner and execution just
  // continues in this same call. If they haven't by then, this throws a distinct non-recovery
  // signal (mirroring ai_review_pause) that server.js parks the page on and returns a pause
  // response for; the SAME armed banner/file/http signals stay live on the parked page, so the
  // eventual resume is driven by the person's own click, not by another MCP call polling in.
  // A resumed call binds `__handover_done_<i>` into inputs (server.js's resume path) after
  // re-validating the page — this interception just consumes that marker and moves on.
  if (step.type === "handover") {
    const doneKey = `__handover_done_${i}`;
    if (Object.prototype.hasOwnProperty.call(state.inputs, doneKey)) {
      delete state.inputs[doneKey];
      state.hasExecutedStep = true;
      state.prevStepType = step.type;
      state.prevPage = page;
      return;
    }
    if (!ctx.context) {
      // No browser context threaded through (e.g. a unit test harness that calls runPlan
      // directly) — fail loud rather than silently skipping the human gate.
      throw stepFailure(step, i, new Error("handover: no browser context available to arm"), null, state.warnings);
    }
    const armed = await handover.arm(ctx.context, page, step, ctx.runCtx && ctx.runCtx.runId);
    const winner = await Promise.race([
      armed.signal,
      new Promise((resolve) => setTimeout(() => resolve(null), handover.HANDOVER_INCALL_MS)),
    ]);
    if (winner) {
      await armed.disarm();
      const revalidated = await handover.revalidate(ctx.tabs, step, ctx.watch).catch((e) => {
        ctx.tracker.emit("step_fail", { si: i, fc: "handover_revalidation_failed" });
        throw stepFailure(step, i, e, null, state.warnings);
      });
      ctx.tracker.emit("handover_resumed", { si: i, via: winner, inCall: true });
      state.hasExecutedStep = true;
      state.prevStepType = step.type;
      state.prevPage = revalidated;
      return;
    }
    // In-call wait expired — hand the still-armed signal sources to server.js so it can park
    // the page and let the eventual resume drive itself; disarm() is NOT called here, the park
    // owns armed's lifetime until it resumes or discards.
    throw Object.assign(new Error("handover_pause"), { handoverPause: true, stepIndex: i, step, page, armed, dryRun: ctx.dryRun });
  }

  // EXEC-38 — "for each row, do steps A-C". Runs its body through THIS SAME function (recovery,
  // GATE/VERIFY, tab resolution, dry-run's destructive-skip — all of it, for free), which is
  // exactly why executeOneStep was extracted out of a flat loop in the first place. See
  // runForEachStep below.
  if (step.type === "for_each") {
    await runForEachStep(ctx, state, step, page, i);
    state.hasExecutedStep = true;
    state.prevStepType = step.type;
    state.prevPage = page;
    return;
  }

  // PROD-3-DRYRUN — the one committing action a dry run withholds. Still RESOLVES the step
  // (proves the target is actually findable, the same signal a real run would need) but never
  // dispatches the handler or verifies a post-condition that couldn't have happened. Kept in
  // the loop rather than threaded through every handler — the smallest diff that can never
  // half-execute an action by accident. A resolve failure is a real failure (fail closed: a
  // dry run that can't even find its committing step's target is not a clean preview).
  if (ctx.dryRun && step.destructive === true) {
    let resolveErr = null;
    try {
      await resolveStep(page, step, state.inputs);
    } catch (e) {
      resolveErr = e;
    }
    if (resolveErr) {
      resolveErr.earlyDomSnapshot = await captureEarlyDomSnapshot(page, step, state.inputs);
      resolveErr.failedPage = page;
      ctx.tracker.emit("step_fail", { si: i, fc: mapErrorToCode(resolveErr) });
      throw stepFailure(step, i, resolveErr, null, state.warnings);
    }
    state.dryRunSkipped.push({ index: i, intent: step.intent || step.type });
    ctx.tracker.emit("dry_run_skip", { si: i });
    state.hasExecutedStep = true;
    state.prevStepType = step.type;
    state.prevPage = page;
    return;
  }

  // EXEC-30 — agent-nominated overlay dismissal (Tier B closing edge). applyStepOverrides
  // (handlers.js) stamped this step with _dismiss_selector/_dismiss_escape when the previous
  // failure response's step_overrides carried a `dismiss` entry for this index. Runs exactly
  // once — the override only ever lands on the resumed index — BEFORE the step's own action,
  // never in place of it: whether or not the dismissal actually clicked something, the
  // recorded step still runs immediately after.
  if (step._dismiss_selector || step._dismiss_escape) {
    if (step._dismiss_escape) {
      await page.keyboard.press("Escape").catch(() => {});
      appendRecoveryEvent({ event: "tierb_overlay_dismissed", slug: ctx.slug, step_index: i, method: "escape", source: "agent" });
      ctx.tracker.emit("overlay_dismissed", { si: i, src: "agent" });
    } else {
      const result = await dismissAgentNominated(page, step._dismiss_selector).catch(err => ({ ok: false, reason: "error", message: err && err.message }));
      if (result.ok) {
        learnedDismissals.record(page.url(), result.selector);
        appendRecoveryEvent({ event: "tierb_overlay_dismissed", slug: ctx.slug, step_index: i,
          selector: result.selector, label: result.label, source: "agent" });
        ctx.tracker.emit("overlay_dismissed", { si: i, src: "agent" });
      } else {
        appendRecoveryEvent({ event: "overlay_dismiss_rejected", slug: ctx.slug, step_index: i, reason: result.reason });
        ctx.tracker.emit("overlay_dismiss_rejected", { si: i, why: result.reason });
        // Ride along on the step object (already carried forward as err.failedStep on a
        // later failure) so the next failure response can explain the refusal — no new
        // plumbing through stepFailure needed.
        step._dismiss_rejected = result.reason;
      }
    }
    await page.waitForTimeout(150).catch(() => {}); // let the DOM settle before the real attempt
  }

  const preShot = await maybeCapturePreStep(page, step);
  const primarySelector = baseSelector(step, state.inputs);
  // Pre-action baseline for the state_changed assertion (only captured when the step actually
  // carries one — cheap, but no reason to pay it on every step).
  const stateBaseline = needsStateChangedBaseline(step) ? await capturePreStepSignature(page) : null;

  // EXEC-29 — a native dialog blocks the very Playwright call that opens it: click()/fill()/
  // etc. do not resolve until the dialog is answered (this is Chromium/CDP behavior, not
  // something Playwright can be told to skip). Waiting for the FOLLOWING dialog_accept/
  // dialog_dismiss step to drain ctx.dialogQueue — which is what a pack compiled before this
  // fix does, and which HANDLERS["dialog_accept"] still supports below for exactly that
  // reason — therefore deadlocks: that step can never run because this one's action promise
  // never returns. Arming the answer here, one step ahead of dispatch, means Chromium's
  // dialog resolves the instant it opens and THIS step's own action returns normally. Safe to
  // race with the old drain path (answerDialog tolerates being called twice on one dialog —
  // see its comment in handlers.js).
  const nextStep = steps[i + 1];
  let unarmDialog = null;
  if (nextStep && (nextStep.type === "dialog_accept" || nextStep.type === "dialog_dismiss")) {
    const onDialog = (dialog) => { answerDialog(dialog, nextStep, state.inputs).catch(() => {}); };
    page.once("dialog", onDialog);
    unarmDialog = () => { try { page.off("dialog", onDialog); } catch (_) {} };
  }

  let primaryErr = null;
  try {
    await executeStep(page, step, state.inputs, { downloadQueue: ctx.downloadQueue, dialogQueue: ctx.dialogQueue });
    // Phase 8: independent post-condition verification.
    const verdict = await verifyStep(page, step, state.inputs, stateBaseline, ctx.dialogQueue);
    // Fleet-visible audit: one event per step that actually carries assertions, pass or fail,
    // so advisory-assertion decay shows up as a drift signal before it becomes a hard failure.
    if (verdict.results.length) {
      ctx.tracker.emit("verify_result", {
        si: i,
        ok: verdict.pass,
        n: verdict.results.length,
        advFail: verdict.results.filter(r => !r.ok && !r.required).length,
      });
    }
    if (!verdict.pass) {
      ctx.tracker.emit("verify_fail", { si: i, ch: verdict.channel });
      throw Object.assign(new Error(`Verification failed: ${verdict.channel}`), {
        verifyFail: true,
        verifyResults: verdict.results,
      });
    }
    // BUILD-30: set by resolution.js::maybeScrollForVirtualization when this step only
    // resolved after a scroll pass — a distinct success reason so BUILD-28's drift dashboard
    // stops classifying a virtualized row as drift; it isn't a page redesign, it's a scroll
    // position.
    ctx.tracker.emit("tier_ok", { si: i, tier: "tier1_compiled", virtualScroll: !!step._used_virtual_scroll });
    state.hasExecutedStep = true;
    state.prevStepType = step.type;
    state.prevPage = page;
    return;
  } catch (err) {
    primaryErr = err;
    primaryErr.earlyDomSnapshot = await captureEarlyDomSnapshot(page, step, state.inputs);
    primaryErr.failedPage = page;
  } finally {
    // A dialog that never opened (this click didn't actually trigger one — drift, a wrong
    // element, whatever) must not stay armed into recovery or a later step: it would wrongly
    // auto-answer the NEXT real dialog this run happens to hit, with an answer meant for a
    // different one. `.once` already self-removes after firing; this is only load-bearing
    // for the case where it never fires.
    if (unarmDialog) unarmDialog();
  }

  // Same reasoning as the auth check below: the caller supplied input the page cannot accept
  // (a folder of 20 files for a single-file upload control). No amount of re-finding the
  // element fixes that, and letting it reach Tier 3+ would spend LLM tokens on a mistake the
  // error message already explains. Fail straight through with that message intact.
  if (primaryErr && primaryErr.badInput) {
    ctx.tracker.emit("step_fail", { si: i, fc: "bad_input" });
    throw stepFailure(step, i, primaryErr, preShot, state.warnings);
  }

  // PROD-3 — a bound step whose record can't be uniquely located isn't a selector problem the
  // cascade can fix: every stage re-resolves through the same identity_bundle + entity_binding,
  // so it would just fail the same way seven more times. Fail straight through rather than
  // burning the cascade (or worse, letting some stage relax scoping and act on the wrong row).
  if (primaryErr && primaryErr.entityNotFound) {
    ctx.tracker.emit("step_fail", { si: i, fc: "entity_not_found" });
    throw stepFailure(step, i, primaryErr, preShot, state.warnings);
  }

  // A login redirect is an auth condition, not a selector/DOM problem the T1/T2 cascade can
  // fix — running it anyway just burns ~10s against a login page before server.js's own
  // isAuthFailure check (which triggers the re-auth window) gets a turn. Skip straight to
  // stepFailure so that check runs immediately.
  if (await isAuthFailure(page, steps)) {
    ctx.tracker.emit("step_fail", { si: i, fc: "auth_failure" });
    throw stepFailure(step, i, primaryErr, preShot, state.warnings);
  }

  // EXEC-37 — settle-detection fallback, before the recovery cascade. A currently-passing run
  // never reaches this: only a step that already failed gets one more chance, once the page
  // visibly stops changing (no DOM/text churn, no visible busy indicator). Two shapes, matching
  // what already happened:
  //   - the action itself may have never dispatched (verifyFail unset AND the withLocator seam
  //     never marked mayHaveActed): safe to retry the whole action + verify once settled.
  //   - the action may already have landed — either its post-condition just didn't hold yet
  //     (verifyFail) or the dispatch itself may have fired before throwing (mayHaveActed, same
  //     signal cascade.js's own guard uses at EXEC-24): re-clicking risks a double-submit on a
  //     non-idempotent step (step_utils.js::isNonIdempotent) — so only re-verify, never re-act.
  const mayHaveActed = !!(primaryErr.verifyFail || primaryErr.mayHaveActed);
  if (SETTLE_RETRY_ENABLED) {
    const settle = await waitForSettle(page, { budgetMs: SETTLE_BUDGET_MS, pollMs: SETTLE_POLL_MS }).catch(() => null);
    if (settle && settle.waitedMs > SETTLE_POLL_MS) {
      if (mayHaveActed) {
        ctx.tracker.emit("settle_retry", { si: i, waited: settle.waitedMs, phase: "verify", ok: settle.settled });
        const verdict2 = await verifyStep(page, step, state.inputs, stateBaseline, ctx.dialogQueue).catch(() => null);
        if (verdict2 && verdict2.pass) {
          ctx.tracker.emit("tier_ok", { si: i, tier: "tier1_compiled_settled" });
          state.hasExecutedStep = true;
          state.prevStepType = step.type;
          state.prevPage = page;
          return;
        }
      } else {
        ctx.tracker.emit("settle_retry", { si: i, waited: settle.waitedMs, phase: "action", ok: settle.settled });
        try {
          await executeStep(page, step, state.inputs, { downloadQueue: ctx.downloadQueue, dialogQueue: ctx.dialogQueue });
          const verdict2 = await verifyStep(page, step, state.inputs, stateBaseline, ctx.dialogQueue);
          if (verdict2.pass) {
            ctx.tracker.emit("tier_ok", { si: i, tier: "tier1_compiled_settled" });
            state.hasExecutedStep = true;
            state.prevStepType = step.type;
            state.prevPage = page;
            return;
          }
          primaryErr = Object.assign(new Error(`Verification failed after settle: ${verdict2.channel}`), {
            verifyFail: true,
            verifyResults: verdict2.results,
          });
        } catch (retryErr) {
          primaryErr = retryErr;
          primaryErr.earlyDomSnapshot = await captureEarlyDomSnapshot(page, step, state.inputs);
          primaryErr.failedPage = page;
        }
      }
    }
  }

  // EXEC-24 — seed the guard with what we already know about the primary attempt. Two ways
  // the step's action may already have run: it threw from inside the action callback
  // (`mayHaveActed`, set at the withLocator seam), or it completed and only its post-condition
  // failed (`verifyFail`) — that one definitely acted. `stateBaseline` is the page as it was
  // before this step touched anything, which is what the guard compares against.
  const guard = createActionGuard(step, {
    acted: !!(primaryErr && (primaryErr.mayHaveActed || primaryErr.verifyFail)),
    signature: stateBaseline,
  });

  const recovered = await recoverStep(page, step, state.inputs, ctx.slug, i, primarySelector, ctx.tracker, primaryErr, ctx.cancelCheck, stateBaseline, guard, ctx.dialogQueue, ctx.runCtx);
  if (!recovered) {
    if (guard.blocked) {
      primaryErr.recoveryHaltReason = guard.blocked;
      if (guard.blocked !== "destructive-no-guess") primaryErr.actionMayHaveTakenEffect = true;
    }
    // PROD-3: an irreversible step recovery could not heal is never handed to the agent for a
    // candidate override, whichever stage it stopped at — a step_overrides pick is
    // re-identification by another name, the one thing a destructive step must never do. This
    // used to be implied by cascade.js halting before Layer 2 and setting `destructive-no-guess`
    // on the guard; now that the same-element Layer 2 stages run for destructive steps too, the
    // rule is stated here instead of inferred from where the cascade stopped.
    if (step.destructive === true) primaryErr.destructiveHalt = true;
    ctx.tracker.emit("step_fail", { si: i, fc: mapErrorToCode(primaryErr) });
    throw stepFailure(step, i, primaryErr, preShot, state.warnings);
  }

  // Phase 9: emit a structured drift signal for the fleet flywheel (admin-gated; never
  // mutates the local pack). `recovered` carries the winning tier/method when available.
  const klass = classifyException(primaryErr);
  ctx.tracker.emit("repair_event", buildRepairEvent(step, i, {
    tier: recovered && recovered.tier ? recovered.tier : "L2",
    method: recovered && recovered.method ? recovered.method : "",
    klass,
    driftHint: remedyFor(klass),
  }));

  state.recoveredSteps++;
  state.hasExecutedStep = true;
  state.prevStepType = step.type;
  state.prevPage = page;
}

// EXEC-38 — "for each row matching X, do steps A-C." Snapshots the row list ONCE (see
// enumerateRows), then runs the body through executeOneStep per row — the same tab resolution,
// GATE/VERIFY, settle-retry, recovery cascade, and dry-run destructive-skip every top-level step
// gets, not the best-effort/no-recovery path other branch primitives (if_present/try_dismiss/
// wait_for_one_of) use, because a loop body doing real work needs the real thing. Body steps
// carry an `entity_binding.identifier` of `{{<as>_id}}` (author-set), so the existing entityRoots
// narrowing — "exactly one match or fail closed" — does all the per-row scoping; there is no new
// resolution logic here beyond the enumeration itself.
//
// Two mutually exclusive row sources: `rows.container_selector` (a live DOM scan via
// enumerateRows) or `items` (a named runtime input, split on commas via splitListInput) — a
// loop over "the rows on this page" vs. a loop over "the files/values the caller named". The
// compiler/patch_gate enforce exactly one is set; everything past enumeration (cap, save/
// restore, on_row_error, dry-run, recovery, telemetry) is identical for both.
//
// ponytail: onStep/telemetry `si` values inside the loop body are body-LOCAL indices (0..N),
// not composite paths into the outer step list — a body step's progress/telemetry can collide
// in number with a top-level step's. Upgrade to composite indices ("5.2.1") only if a real
// caller needs to disambiguate; for a first cut this is cosmetic, not a correctness issue.
async function runForEachStep(ctx, state, step, page, i) {
  const spec = (step.rows && typeof step.rows === "object") ? step.rows : {};
  const asName = String(step.as || "row").trim() || "row";
  const bodySteps = Array.isArray(step.steps) ? step.steps : [];

  // "A loop step without a cap fails at load" — refuse rather than guess a default. An
  // unbounded loop combined with an irreversible action is exactly how automation software
  // damages a customer's database (PROD-3-DRYRUN's own rationale, restated for iteration).
  const declaredCap = Number(step.max_iterations);
  if (!Number.isInteger(declaredCap) || declaredCap <= 0) {
    const capErr = Object.assign(
      new Error("for_each step has no max_iterations — refusing to run an uncapped loop"),
      { badInput: true },
    );
    ctx.tracker.emit("step_fail", { si: i, fc: "bad_input" });
    throw stepFailure(step, i, capErr, null, state.warnings);
  }
  const cap = Math.min(declaredCap, MAX_LOOP_ITERATIONS);
  const onRowError = step.on_row_error === "continue" ? "continue" : "stop";

  // EXEC-38 items source: a loop driven by a named runtime input (comma-separated text) instead
  // of a DOM row scan — no live page to enumerate, so no container/entity-binding scoping
  // applies to the body (entityRoots already no-ops when a body step carries none).
  const rowIds = step.items
    ? splitListInput(state.inputs[step.items], cap)
    : await enumerateRows(page, spec, cap);
  ctx.tracker.emit("for_each_start", { si: i, total: rowIds.length, cap });

  // Save/restore around the WHOLE loop, not per-iteration — every iteration sets these fresh
  // before its body runs, so there is nothing to restore in between; only a later top-level step
  // (or an outer for_each, if one ever wraps another) must not see loop-local state leak out.
  const idKey = `${asName}_id`;
  const idxKey = `${asName}_index`;
  const hadId = Object.prototype.hasOwnProperty.call(state.inputs, idKey);
  const hadIdx = Object.prototype.hasOwnProperty.call(state.inputs, idxKey);
  const prevId = state.inputs[idKey];
  const prevIdx = state.inputs[idxKey];

  let processed = 0;
  let failed = 0;
  state.loopDepth = (state.loopDepth || 0) + 1; // RT-9: the guard can't pause inside a loop body
  try {
    for (let idx = 0; idx < rowIds.length; idx++) {
      state.inputs[idKey] = rowIds[idx];
      state.inputs[idxKey] = idx;
      try {
        for (let j = 0; j < bodySteps.length; j++) {
          await executeOneStep(ctx, state, bodySteps, j);
        }
        processed++;
      } catch (bodyErr) {
        // Cancellation and an ai_review/handover pause inside the body are never row-level
        // failures — they must always propagate immediately regardless of on_row_error. (Note:
        // patch_gate.py refuses to compile a handover step inside a for_each body in the first
        // place — unwinding this loop on a pause loses the iteration cursor, so resuming would
        // restart the loop from row 0 rather than continuing the interrupted row. This branch
        // only matters if an older/hand-authored pack manages to carry one anyway.)
        if (bodyErr && (bodyErr.cancelled || bodyErr.reviewPause || bodyErr.handoverPause)) throw bodyErr;
        failed++;
        ctx.tracker.emit("for_each_row_fail", { si: i, row_index: idx, continued: onRowError === "continue" });
        if (onRowError !== "continue") throw bodyErr;
      }
    }
  } finally {
    state.loopDepth -= 1;
    if (hadId) state.inputs[idKey] = prevId; else delete state.inputs[idKey];
    if (hadIdx) state.inputs[idxKey] = prevIdx; else delete state.inputs[idxKey];
  }

  // Reached only when the loop did NOT throw out of the try above — either every row succeeded,
  // or on_row_error absorbed some failures ("stop" mode already exited via the propagated throw
  // before this line runs). `processed`/`failed` were already tracker-only telemetry
  // (for_each_done below) that never reached the run's own result — a "continue" loop with rows
  // skipped, or an empty items list, could complete and report plain success with nothing (or
  // less than everything) actually done. Pushing into state.warnings surfaces it in the same
  // "Warning:\n..." block the final result text already appends (server.js's _allWarnings).
  if (rowIds.length === 0) {
    state.warnings.push(`Loop at step ${i + 1} found no items to process — "${step.items || "rows"}" was empty.`);
  } else if (failed > 0) {
    state.warnings.push(`Loop at step ${i + 1}: ${failed} of ${rowIds.length} item(s) failed and were skipped (on_row_error: continue).`);
  }

  ctx.tracker.emit("for_each_done", { si: i, processed, failed, total: rowIds.length });
}

async function runPlan(startPage, steps, inputs, startFrom, slug, { onStep, onPhase, cancelCheck, tracker, downloadQueue, dialogQueue, structuralFingerprint, environmentFingerprint, watch, runId, hostOwned, hostRunId, dataDir, dryRun, isResume, context, guard } = {}) {
  // BUILD-26 stage (f): threaded into recoverStep -> cascade.js's dismiss-overlay remedy, which
  // is the one place the runtime captures an unexpected overlay's identity even on a run that
  // ultimately passes. Optional — omitted (e.g. a Studio caller that predates this) simply
  // means no capture happens, same as today.
  const runCtx = (runId && dataDir) ? { runId, dataDir } : null;
  const t = tracker || { emit: () => {} };
  // A fresh run starts with a clean budget. The success path also clears it, but a *failed* run
  // used to leave its attempt counts behind in this long-lived process, so the next FRESH run of
  // the same skill started already exhausted and recovery never engaged (EXEC-12). A resume,
  // though, is exactly the call server.js's checkRetryBudget() gates on — clearing it here too
  // meant that check could never actually exhaust (each resume wiped the count runPlan itself
  // was about to add to), so a resume must NOT clear it.
  if (!isResume) clearRetryBudget(slug);
  // Run-wide state threaded through every step by executeOneStep (below) — including a for_each
  // body step (EXEC-38 reuses this same function), so tab-inheritance/settle continuity works
  // identically inside a loop body as at the top level. Mutated in place, never reassigned.
  const state = {
    // The flat input-variable namespace steps read {{placeholders}} from and some steps
    // (ai_review, download_observed) write into — shared and mutated across the whole run,
    // for_each body iterations included, exactly as it always was as a bare closure variable.
    inputs,
    recoveredSteps: 0,
    hasExecutedStep: false,
    prevStepType: null,
    prevPage: null,
    // EXEC-36/BUILD-28(a) — plain-language warnings surfaced to the caller regardless of whether
    // the run ultimately passes or fails (see stepFailure above and the success-path return
    // below). Populated by the pre-execution drift gate and the environment-mismatch check, both
    // advisory-only: neither ever blocks execution.
    warnings: [],
    // PROD-3-DRYRUN: every destructive step this run resolved-but-skipped, so the caller can
    // name exactly what a dry run withheld.
    dryRunSkipped: [],
  };

  // Multi-tab: each step declares which tab it runs on (step.tab — see tabs.js). The registry
  // binds tab_0 to startPage and starts listening for new pages immediately, before any step
  // runs, so a tab opened by an early step is queued even if a later step is the first to ask
  // for it.
  // hostRunId (not runId — see host_browser.js/server.js's _hostRunId comment) is what a
  // tab_open step's Execute-panel lookup must correlate against: on a resumed hand-over/
  // review park, runId is freshly generated per call (the caller-facing resume identity
  // runCtx above needs), but the browser view Execute is holding was registered under the
  // run's ORIGINAL id. They're the same value for every fresh (non-resumed) run.
  const tabs = createTabRegistry(startPage, { hostOwned, runId: hostRunId ?? runId });

  // Settle the page before the first step so step 0 doesn't fire against a still-hydrating SPA.
  // Uses the same timeout constant as navigation waits; best-effort (catch swallowed).
  await startPage.waitForLoadState("domcontentloaded", { timeout: PAGE_LOAD_TIMEOUT_MS }).catch(() => {});

  // Pre-execution drift gate (advisory only). On a fresh run, check whether the
  // pack's recorded structural landmarks are still present. If most have vanished
  // the target app was likely redesigned — emit a signal for the fleet dashboard.
  // This NEVER blocks: execution proceeds and per-step recovery still applies.
  if (startFrom === 0 && structuralFingerprint && Array.isArray(structuralFingerprint.landmarks) && structuralFingerprint.landmarks.length) {
    try {
      const verdict = await detectPreExecDrift(startPage, structuralFingerprint);
      if (verdict.drift) {
        t.emit("drift_detected", {
          total: verdict.total,
          missing: verdict.missing,
          drift_ratio: Number(verdict.driftRatio.toFixed(3)),
          missing_intents: (verdict.missingIntents || []).slice(0, 5),
          url: (() => { try { return startPage.url(); } catch (_) { return ""; } })(),
        });
        // BUILD-28(a): drift_detected used to be telemetry-only — reached the fleet dashboard
        // and stopped there, never the user running the skill. Route it through the same
        // warnings channel EXEC-36 adds below so it actually reaches execute_skill's response.
        state.warnings.push(
          `This app may have been redesigned since this workflow was recorded — ` +
          `${verdict.missing}/${verdict.total} expected landmarks are missing on the live page.`
        );
      }
    } catch (_) { /* advisory gate never affects execution */ }
  }

  // EXEC-36 — pre-execution environment-mismatch check (advisory only, same shape as the drift
  // gate above). Compares the recorded environment against the live browser's; a material
  // difference (language, UTC offset, or a viewport crossing a responsive breakpoint) turns a
  // baffling mid-run failure into an obvious, self-explanatory one instead of reading like
  // ordinary drift/breakage.
  if (startFrom === 0 && environmentFingerprint && Object.keys(environmentFingerprint).length) {
    try {
      const live = await evalOn(startPage, pageScripts.environmentSignature);
      if (live !== EVAL_TIMED_OUT) {
        const mismatches = compareEnvironment(environmentFingerprint, live);
        if (mismatches.length) {
          t.emit("env_mismatch", {
            fields: mismatches.map((m) => m.field),
            locale_rec: environmentFingerprint.locale || "",
            locale_live: live.locale || "",
            vw_rec: (environmentFingerprint.viewport && environmentFingerprint.viewport.w) || null,
            vw_live: (live.viewport && live.viewport.w) || null,
            tz_delta_min: (typeof environmentFingerprint.utc_offset_minutes === "number" && typeof live.utc_offset_minutes === "number")
              ? live.utc_offset_minutes - environmentFingerprint.utc_offset_minutes
              : null,
          });
          for (const m of mismatches) state.warnings.push(m.message);
        }
      }
    } catch (_) { /* advisory gate never affects execution */ }
  }

  // ctx carries everything genuinely constant for the whole run — passed to every
  // executeOneStep call (top-level steps, and — once EXEC-38's for_each executor calls it too —
  // a loop body's steps, reusing the exact same tab-resolution/GATE/VERIFY/recovery machinery
  // rather than the best-effort, no-recovery path other branch primitives use).
  const ctx = {
    onStep, onPhase, cancelCheck, tracker: t, downloadQueue, dialogQueue,
    tabs, watch, dryRun, slug, runCtx, context, // context: EXEC-21 handover needs it to arm its banner
    guard, // RT-9: Studio-only pause before the first step that can't be undone (see executeOneStep)
  };

  for (let i = startFrom; i < steps.length; i++) {
    await executeOneStep(ctx, state, steps, i);
  }

  return { recoveredSteps: state.recoveredSteps, warnings: state.warnings, dryRunSkipped: state.dryRunSkipped };
}

// Auth-failure detection — login redirect or session-expired page heuristics. Deliberately
// broader/unanchored than browser.js's LOGIN_PATH_RE (which answers a different question —
// "has login-completion happened yet" — see its comment); don't merge them.
const AUTH_FAILURE_URL_RE = /\/(login|signin|sign-in|auth|logout|session-expired)(\/|$|\?)/i;
const AUTH_FAILURE_TITLE_RE = /sign\s*in|log\s*in|session\s*expired|authentication\s*required/i;

// A skill can legitimately record a login page as an ordinary step — a demo app's /login form
// the workflow itself fills in. The heuristics above can't tell that apart from a session that
// died, and getting it wrong is expensive twice over: the step loses its whole Tier 1-4 recovery
// cascade (run.js short-circuits straight to stepFailure), and the user is told their saved
// sign-in expired for an app the failing step never touched. A URL the recording deliberately
// navigated to is not a redirect, so it is not a session expiry.
function _isRecordedUrl(steps, url) {
  if (!Array.isArray(steps) || !url) return false;
  const bare = (u) => String(u).split("#")[0].split("?")[0].replace(/\/$/, "");
  const live = bare(url);
  return steps.some((s) => s && s.type === "navigate" && s.url && bare(s.url) === live);
}

async function isAuthFailure(page, steps) {
  const url = page.url();
  if (_isRecordedUrl(steps, url)) return false;
  if (AUTH_FAILURE_URL_RE.test(url)) return true;
  try {
    const title = await page.title();
    if (AUTH_FAILURE_TITLE_RE.test(title)) return true;
  } catch (_) {}
  return false;
}

module.exports = {
  NOOP_STEP_TYPES,
  NAVIGATION_STEP_TYPES,
  appendRecoveryEvent,
  interpolate,
  resolveUploadPaths,
  extractZipOnce,
  enrichStepsWithRecovery,
  applyStepOverrides,
  executeStep,
  uniqueDownloadName,
  sweepOldRuns,
  runPlan,
  checkRetryBudget,
  clearRetryBudget,
  mapErrorToCode,
  isAuthFailure,
  verifyStep,
  gateLocator,
  a11yRecoveryName,
  capturePreStepSignature,
  hasRequiredAssertion,
  needsStateChangedBaseline,
  recoverWithSelector,
  recoverStep,
  layer1Ladder,
  probePresent,
  validateOverrideSelector,
  stepAssertions,
  rootCandidates,
  frameScopedInventory,
};
