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
const { evalOn, countOn, EVAL_TIMED_OUT } = require("./page_eval");
const { SIGN_IN_PAGE_RE, SSO_HOP_RE, pathOf } = require("./login_paths");
const { createTabRegistry, resolveStepPage, stepInheritsPage } = require("./tabs");

const { appendRecoveryEvent } = require("./recovery_log");
const { interpolate } = require("./interpolate");
const { MAX_CALL_DEPTH } = require("./call_workflow");
const fork = require("./fork");
const pageStatus = require("./page_status");
const { classifyFault, faultMessage } = require("./fault_classify");
const appKnowledge = require("./app_knowledge");
const {
  PAGE_LOAD_TIMEOUT_MS,
  RUN_RETENTION_MS,
  SETTLE_RETRY_ENABLED,
  SETTLE_BUDGET_MS,
  RECHECK_CAP_MS,
  SETTLE_POLL_MS,
  MAX_LOOP_ITERATIONS,
  COMPLETION_GATE_ENABLED,
  COMPLETION_BUDGET_MS,
  COMPLETION_SHORT_MS,
  NETWORK_GRACE_MS,
  COMPLETION_START_GRACE_MS,
} = require("./run_config");
const { waitForSettle } = require("./settle");
const { waitForCompletion, captureCompletionBaseline, completionMessage } = require("./completion");
const proof = require("./proof");
const evidence = require("./evidence");
const effectProbes = require("./effect_probes");
const { freshLook } = require("./fresh_look");
const { runLooks, wantedValue } = require("./result_probes");
const { isNonIdempotent } = require("./step_utils");
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
  pollNegative,
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
const { checkGeometry } = require("./page_geometry");
const {
  resolveUploadPaths,
  extractZipOnce,
  uniqueDownloadName,
} = require("./uploads");
const handover = require("./handover");
const agentSpan = require("./agent_span");
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
  "if_present", "try_dismiss", "wait_for_one_of", "outcome_fork",
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

const PAGE_SIZE_MESSAGE = "The test browser is not showing the page at its normal size, so a click could land in the wrong place and this step could not be trusted. Run the test again.";

function stepFailure(step, stepIndex, cause, preShot, warnings) {
  // Shaped once here (the single funnel every step failure passes through) rather than at each
  // of the 5 places downstream that interpolate err.message into a response — see errors.js.
  // rawMessage keeps the original text (with Playwright's "Call log:" trace) for evidence/logs;
  // err.message itself is now "<plain summary> (<technical detail>)", never the raw dump.
  const rawMessage = cause && cause.message ? cause.message : String(cause);
  const { summary, detail } = plainCause(cause instanceof Error ? cause : new Error(rawMessage));
  const err = new Error(`Step ${stepIndex + 1} (${step.type}) failed: ${summary}${detail ? ` (${detail})` : ""}`);
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
    // Dropped here until now, so the class of a find-failure was lost before it reached the evidence.
    if (cause.ambiguous) err.ambiguous = true;
    if (cause.resolveMiss) err.resolveMiss = true;
    if (Array.isArray(cause.signalCounts)) err.signalCounts = cause.signalCounts;
    if (cause.entityNotFound) err.entityNotFound = true;
    if (cause.entityAbsent) err.entityAbsent = true;
    if (cause.tabNotFound) err.tabNotFound = true;
    if (cause.failedPage) err.failedPage = cause.failedPage;
    // EXEC-30: Tier A's known-pattern ladder saw an INTERCEPTED failure and cleared nothing —
    // tell the agent tier this looks like an unrecognized overlay, not a plain missing element.
    if (cause.unknownOverlay) err.unknownOverlay = true;
    // Deliberate stops, not exhausted recovery: without these flags the wrapper looked like an
    // ordinary selector failure, so server.js parked it and handed it to an armed Tier B round.
    if (cause.forkDecisionNeeded) err.forkDecisionNeeded = true;
    if (cause.badInput) err.badInput = true;
    if (cause.fault) {
      err.fault = true;
      err.faultKind = cause.faultKind;
    }
    if (cause.decisionDialog) err.decisionDialog = cause.decisionDialog;
    if (cause.leftOpenSurface) err.leftOpenSurface = cause.leftOpenSurface;
  }
  return err;
}

// EXEC-49 — is this failed step's page showing the app's own refusal? Null when it is not, when a
// popup is asking the person to choose (that is a decision, never a fault), when the step is a fork
// (it handles its own popups), or when a native dialog has the renderer blocked.
async function detectFault(ctx, page, step, { mayHaveActed, sinceMs }) {
  if (step.type === "outcome_fork") return null;
  if (ctx.dialogQueue && ctx.dialogQueue.length) return null;
  const dlg = await evalOn(page, pageScripts.dialogProbe).catch(() => null);
  if (dlg && dlg !== EVAL_TIMED_OUT && fork.isDecisionDialog(dlg)) return null;
  const probe = await evalOn(page, pageScripts.faultProbe).catch(() => null);
  const p = probe && probe !== EVAL_TIMED_OUT ? probe : {};
  const seen = pageStatus.recent(page, sinceMs);
  return classifyFault({
    alerts: [...(p.alerts || []), ...(p.invalid || [])],
    offline: !!p.offline,
    status: seen ? seen.status : null,
    mayHaveActed,
  });
}

// Completion gate (completion.js): "the action ran" is not "the action finished". Called once a step
// has passed its own check, before the run moves on. An upload and the plan's last acting step wait
// for the work to really finish and fail when the app says it did not; every other acting step only
// waits (bounded) for visible progress to clear. The action already happened, so a failure here never
// enters recovery: re-running it could upload or submit twice (callers rethrow `completionGate`).
//
// Returns what the gate found (so proveStep can count it as a check), or null when it did not run.
// "no_sign", "cant_tell" and "still_working" are not failures here: proveStep turns them into a
// verdict, so they are never a silent pass and never a false failure.
async function confirmCompleted(ctx, state, step, i, page, completion, preShot) {
  try {
    return await confirmCompletedInner(ctx, state, step, i, page, completion, preShot);
  } finally {
    // The verdict is in (or the step is failing): stop listening, whatever the outcome.
    if (completion.notebook) closeNotebook(ctx, completion.notebook);
  }
}

// The one file an upload step sends, by name, or null (not an upload, several files, or no readable path). Only the
// name is ever used, and only to look for it on a page.
function uploadFileName(step, inputs) {
  if (!step || step.type !== "upload") return null;
  try {
    const paths = resolveUploadPaths(interpolate(String(step.value ?? ""), inputs));
    return paths.length === 1 ? require("node:path").basename(paths[0]) : null;
  } catch (_) {
    return null;
  }
}

// The value a step's row_with_identity check wants to find on the page more often than before, or null. Counted on the
// page before the action (the notebook's `nameCountBefore`), so only the same needle can be looked for afterwards.
function identityNeedle(step, inputs) {
  const effect = step && step.validation && step.validation.effect;
  const check = effect && Array.isArray(effect.checks) && effect.checks.find((c) => c && c.probe
    && c.probe.type === "row_with_identity" && !(c.calibrated === false && effectProbes.hasBasis(c)));
  return check ? wantedValue(check.probe.key, inputs) : null;
}

// How the app this page belongs to words progress, success and failure (learned from its recordings, shipped in
// app_knowledge.json). Empty when nothing is known: the English defaults then decide alone.
function appSignals(ctx, page) {
  try {
    return appKnowledge.signalsFor(ctx.appKnowledge, appKnowledge.hostOf(typeof page.url === "function" ? page.url() : ""));
  } catch (_) {
    return { progress: null, success: null, failure: null };
  }
}

// The pages this run owns (never the whole browser context: a borrowed host browser also holds other
// runs' views, and another run's refusal must not fail this one).
function runPages(ctx) {
  const t = ctx.tabs;
  if (!t) return [];
  return [...new Set([...(t.pages ? t.pages.values() : []), ...(t.bound || []), ...(t.pendingPages || [])])];
}

// Called at the start of every step: the run's background endpoints are learned from the steps that came before.
function noteBackground(ctx) {
  try {
    if (!ctx.ambient) ctx.ambient = evidence.ambientWatcher({ pagesOf: () => runPages(ctx) });
    ctx.ambient.refresh();
  } catch (_) { /* evidence, never a reason to fail a step */ }
}

async function openNotebook(ctx, page, baseline = null, fileName = null) {
  closeNotebook(ctx);
  try {
    ctx.notebook = await evidence.open({
      page, pagesOf: () => runPages(ctx), ...(baseline ? { baseline } : {}), ...(fileName ? { fileName } : {}),
      ...(ctx.ambient ? { ambientKeys: ctx.ambient.keys } : {}),
    });
    return ctx.notebook;
  } catch (_) {
    ctx.notebook = null; // the notebook is evidence, never a reason to fail a step: the gate reads the page alone
    return null;
  }
}

function closeNotebook(ctx, only = null) {
  const nb = only || ctx.notebook;
  if (!nb) return;
  try { nb.close(); } catch (_) { /* already closed */ }
  if (ctx.notebook === nb) ctx.notebook = null;
}

async function confirmCompletedInner(ctx, state, step, i, page, completion, preShot) {
  const full = step.type === "upload" || step === ctx.lastActStep;
  const mode = full ? "full" : "short";
  const warn = (text) => { if (text && !state.warnings.includes(text)) state.warnings.push(text); };
  if (!completion.baseline) {
    // The page could not be read before the action, so nothing after it can be measured.
    if (!completion.attempted) return null;
    ctx.tracker.emit("completion", { si: i, mode, st: "cant_tell", waited: 0 });
    return { status: "cant_tell", reason: "no_baseline" };
  }
  const upload = step.type === "upload";
  const v = await waitForCompletion(page, {
    baseline: completion.baseline,
    sinceMs: completion.sinceMs,
    mode,
    budgetMs: full ? COMPLETION_BUDGET_MS : COMPLETION_SHORT_MS,
    pendingBudgetMs: upload ? COMPLETION_BUDGET_MS : NETWORK_GRACE_MS,
    startGraceMs: COMPLETION_START_GRACE_MS,
    notebook: completion.notebook || null,
    signals: appSignals(ctx, page),
    deadlineAt: ctx.deadlineAt,
    cancelCheck: ctx.cancelCheck,
    dialogQueue: ctx.dialogQueue,
  });
  ctx.tracker.emit("completion", { si: i, mode, st: v.status, waited: v.waitedMs });
  warn(v.warning);
  if (v.status === "handoff") {
    // A popup left open by the plan's final step has no next step to answer it: put it to the same
    // rules as any other unrecorded popup. Anywhere else the next step's own popup handling owns it.
    if (v.reason === "decision_popup" && step === ctx.lastStep) await popupLeftBehind(ctx, state, step, i, page, preShot);
    return v;
  }
  if (v.reason === "cancelled") {
    // Cancelled (or out of time) while waiting: stop here, not "Done". Same error the step loop throws.
    throw Object.assign(new Error("Execution cancelled"), { cancelled: true, completionGate: true });
  }
  const deadlineHit = v.status === "still_working" && v.reason === "deadline";
  if (v.status !== "failed" && v.status !== "timed_out" && !(deadlineHit && full)) return v;
  if (deadlineHit && full && !state.loopDepth && !state.forkDepth && !ctx.dryRun) {
    // The work is still going and this call is out of time. Pause instead of failing: the run ends "Submitted.", the
    // page stays parked, and a later call checks on it without doing anything again (a re-check, never a re-run).
    // Inside a loop or a conditional there is no cursor to resume from, so those still fail below.
    state.proof.steps.push({
      index: i,
      ...(step.source_step_key ? { source_step_key: step.source_step_key } : {}),
      intent: step.intent || null, verdict: "still_working", rung: null, required: "completed", checks: [],
    });
    ctx.tracker.emit("proof", { si: i, rung: null, v: "still_working", w: [] });
    throw Object.assign(new Error("proof_pause"), {
      proofPause: true, completionGate: true, stepIndex: i, step, page,
      evidence: v.evidence,
      recheck: completion.baseline,
      proof: { steps: state.proof.steps.slice(), notes: state.proof.notes.slice() },
    });
  }
  ctx.tracker.emit("step_fail", { si: i, fc: v.status === "failed" ? "completion_failed" : "completion_timeout" });
  const cause = Object.assign(new Error(completionMessage(v)), {
    fault: true,
    faultKind: v.kind || "unfinished",
    actionMayHaveTakenEffect: true,
  });
  throw Object.assign(stepFailure(step, i, cause, preShot, state.warnings), { completionGate: true });
}

// ── Proof of Done ──────────────────────────────────────────────────────────────────────────────────
// What this step has actually proven, by the fixed rules in proof.js. The step already ran and passed
// its own check; this decides whether the run may call it proven. It only reads answers already
// collected (VERIFY, the completion gate): it never touches the page, never retries, never asks.

// One verify_result per step that carries assertions, on every path that verifies, pass or fail, so
// assertion decay is visible as a drift signal. `ct` counts checks that could not look.
function emitVerifyResult(ctx, i, verdict) {
  if (!verdict || !Array.isArray(verdict.results) || !verdict.results.length) return;
  ctx.tracker.emit("verify_result", {
    si: i,
    ok: verdict.pass,
    n: verdict.results.length,
    advFail: verdict.results.filter((r) => !r.ok && !r.required).length,
    ct: verdict.results.filter((r) => r.answer === "cant_tell").length,
  });
}

// What the completion gate found, as one check's answer. A popup the next step owns is "not yet", never
// a failure and never a pass.
function gateAnswer(gate) {
  if (!gate) return null;
  switch (gate.status) {
    case "done": return "yes";
    case "no_sign": case "cant_tell": return "cant_tell";
    case "still_working": case "handoff": return "not_yet";
    default: return null;
  }
}

// A decision-looking panel that did not block the page and was still open when the plan's last acting step ended.
// Returns what to record about it, or null when the recording already saw it (its words are the step's own
// "the app says ..." witness): that one is a result, not a surprise. Never decides anything and never clicks.
function leftOpenRecord(effect, open) {
  if (!open) return null;
  const said = String([open.title, open.text].join(" ")).toLowerCase();
  const recorded = new Set();
  if (effect) {
    for (const c of effect.checks || []) if (c && c.probe && Array.isArray(c.probe.words)) c.probe.words.forEach((w) => recorded.add(String(w).toLowerCase()));
    const w = effect.words || {};
    for (const k of ["success", "progress"]) (w[k] || []).forEach((x) => recorded.add(String(x).toLowerCase()));
  }
  if ([...recorded].some((w) => w.length >= 4 && said.includes(w))) return null;
  return { title: open.title || "", buttons: fork.probeLabelsRaw(open).slice(0, 8), blocks: open.blocks === false ? false : null };
}

async function proveStep(ctx, state, step, i, { verify = null, gate = null, recovered = false, preShot = null, notebook = null } = {}) {
  const hasReq = hasRequiredAssertion(step);
  const full = (step.type === "upload" || step === ctx.lastActStep) && !!gate;
  // The step's own checklist (compiled from the recording) applies only when the gate ran: with the completion
  // gate off, or on a step it never gated, nothing here may be stricter than before.
  const effect = gate && step.validation && step.validation.effect && Array.isArray(step.validation.effect.checks)
    ? step.validation.effect : null;
  const checks = [{ rung: "performed", witness: "target", role: "support", answer: "yes" }];
  if (verify && Array.isArray(verify.results) && verify.results.length) {
    for (const r of verify.results) {
      checks.push({ rung: "accepted", witness: r.type === "value_equals" ? "target" : "renderer", role: "support", answer: r.answer || (r.ok ? "yes" : "no") });
    }
  } else if (verify) {
    checks.push({ rung: "accepted", witness: "renderer", role: "support", answer: verify.answer || "cant_tell" });
  } else if (recovered) {
    // Recovery re-verifies a required assertion before it reports success; without one nothing was read.
    checks.push({ rung: "accepted", witness: "renderer", role: "support", answer: hasReq ? "yes" : "cant_tell" });
  }
  const g = gateAnswer(gate);
  if (g) checks.push({ rung: "completed", witness: "renderer", role: "support", answer: g });
  // Go and look: the one witness that asks the app afresh. It opens the same page in a separate tab and reads it, so it
  // runs only when the step carries that check and the check has not been shown to be dead on this site.
  let fresh = null;
  if (effect && notebook && effect.checks.some((c) => c && !(c.calibrated === false && effectProbes.hasBasis(c)) && c.probe && c.probe.type === "fresh_look")) {
    fresh = await freshLook({ page: notebook.page, registry: ctx.tabs, fileName: uploadFileName(step, state.inputs), countBefore: notebook.nameCountBefore });
    ctx.tracker.emit("fresh_look", { si: i, a: fresh.answer });
  }
  // The checks that need time or a second page (url_stays, row_with_identity, value_equals), looked at only when asked for.
  const looks = effect && notebook
    ? await runLooks(effect, { notebook, steps: ctx.steps, inputs: state.inputs, registry: ctx.tabs, resolve: resolveStep })
    : null;
  if (effect) checks.push(...effectProbes.evaluateChecks(effect, { notebook, gate, executed: true, fresh, looks, signals: appSignals(ctx, state.prevPage || ctx.tabs && ctx.tabs.pages && ctx.tabs.pages.get("tab_0")) }));

  const required = proof.requiredRungFor(step, { full, hasRequiredAssertion: hasReq });
  // A raised quorum only counts when a proven test run raised it (a check carries its `calibration_basis`).
  const quorum = effect && effect.quorum
    ? { witnesses: effect.checks.some(effectProbes.hasBasis) ? effect.quorum.witnesses : 1, identityRequired: !!effect.quorum.identity_required }
    : undefined;
  const j = proof.judge(checks, { requiredRung: required, quorum });
  const onUnverified = step.validation && step.validation.effect && step.validation.effect.on
    && String(step.validation.effect.on.unverified || "").startsWith("handover") ? "handover" : "fail";
  const move = proof.nextMove(j.verdict, { idempotent: proof.safeToRepeat(step), attended: !ctx.scheduled, onUnverified });

  const leftOpen = leftOpenRecord(effect, gate && gate.leftOpen);
  if (leftOpen) {
    ctx.tracker.emit("left_open", { si: i, v: j.verdict });
    if (move.do === "continue") {
      state.warnings.push(`A panel ("${leftOpen.title || "untitled"}"${leftOpen.buttons.length ? `, buttons: ${leftOpen.buttons.join(", ")}` : ""}) `
        + "was still open when the run ended. It does not block the page and the step's result was confirmed, so the run was not stopped. Check it if it looks like a question.");
    }
  }
  state.proof.steps.push({
    index: i,
    ...(step.source_step_key ? { source_step_key: step.source_step_key } : {}),
    intent: step.intent || null,
    verdict: j.verdict,
    rung: j.rung,
    required,
    ...(leftOpen ? { left_open: leftOpen } : {}),
    ...(effect ? { effect_kind: effect.kind } : {}),
    checks: checks.map(({ id, rung, witness, role, answer }) => ({ ...(id ? { id } : {}), rung, witness, ...(role === "veto" ? { role } : {}), answer })),
  });
  ctx.tracker.emit("proof", {
    si: i, rung: j.rung, v: j.verdict,
    w: [...new Set(checks.filter((c) => c.answer === "yes").map((c) => c.witness))],
  });

  if (move.do === "continue") {
    if (move.note) {
      const note = proof.unconfirmedNote(step, i);
      state.proof.notes.push(note);
      // A later step that needs what this one made can still prove it once it has run (resolveLaterProofs).
      const later = effect && effect.checks.find((c) => c && c.id && c.probe && c.probe.type === "later_step" && c.probe.step
        && !(c.calibrated === false && effectProbes.hasBasis(c)));
      if (later) state.proof.later.push({ at: state.proof.steps.length - 1, key: String(later.probe.step), checkId: later.id, note, checks, required, quorum });
    }
    return;
  }
  if (move.do === "wait") return; // the next step's own checks (or a popup it owns) take over
  if (String(j.verdict).startsWith("failed_")) {
    // A failure signal in the step's own checklist (an error or refusal the app showed, a reply that said no): an
    // ordinary failure, not "couldn't confirm". The action already happened, so it never enters recovery.
    ctx.tracker.emit("step_fail", { si: i, fc: "proof_failed" });
    const cause = Object.assign(new Error(`This step didn't finish: ${j.reason || "the app reported a problem"}.`), {
      fault: true, faultKind: "action_failed", actionMayHaveTakenEffect: true,
    });
    throw Object.assign(stepFailure(step, i, cause, preShot, state.warnings), { completionGate: true });
  }
  // Anything else fails closed. Today only "couldn't confirm" on a step that is not safe to do twice
  // (an upload, a destructive step) or on a run nobody is watching gets here.
  state.proof.stopped = { index: i };
  ctx.tracker.emit("step_fail", { si: i, fc: "couldnt_confirm" });
  const cause = Object.assign(
    new Error(`${proof.unconfirmedNote(step, i)} It ran, but nothing confirmed it finished, so the run stopped instead of reporting success. `
      + "Check it before running this again."),
    { fault: true, faultKind: "unconfirmed", actionMayHaveTakenEffect: true },
  );
  throw Object.assign(stepFailure(step, i, cause, preShot, state.warnings), {
    completionGate: true,
    couldntConfirm: true,
    proof: { steps: state.proof.steps.slice(), notes: state.proof.notes.slice() },
  });
}

// A step the run could not confirm (and may safely repeat) can still be proven afterwards by a later step that only works
// if it succeeded (a download whose file a later step uploads). Called once the plan has run to its end: `ran` holds the
// keys of the steps that really executed and passed (never a step a dry run or an input condition skipped). The step is
// judged again by the same rules with that one answer changed to "yes" (so its quorum and the recording's expected
// results still apply); only when that makes it proven is the "I couldn't confirm" note withdrawn.
function resolveLaterProofs(state, ran) {
  const kept = [];
  for (const p of state.proof.later) {
    const entry = state.proof.steps[p.at];
    const check = p.checks && p.checks.find((c) => c.id === p.checkId);
    if (!entry || !check || !ran.has(p.key)) { kept.push(p); continue; }
    check.answer = "yes";
    const j = proof.judge(p.checks, { requiredRung: p.required, quorum: p.quorum });
    if (j.verdict !== "proven") { check.answer = "cant_tell"; continue; }
    const row = entry.checks.find((c) => c.id === p.checkId);
    if (row) row.answer = "yes";
    entry.verdict = j.verdict;
    entry.rung = j.rung;
    const at = state.proof.notes.indexOf(p.note);
    if (at >= 0) state.proof.notes.splice(at, 1);
  }
  state.proof.later = kept;
}

// The plan's final step ended with a popup that asks a question and nothing is left to answer it.
// Never answered on anyone's behalf: it goes through the same rules as an unrecorded popup anywhere
// else (put to the MCP client when allowed, otherwise the run stops), and the run is not "Done".
async function popupLeftBehind(ctx, state, step, i, page, preShot) {
  const blocking = await observeDialog(page, ctx);
  if (!blocking || !fork.isBlockingDecision(blocking)) return;
  try {
    await askAboutBlockingPopup(ctx, state, step, page, i, { verifyFail: true });
  } catch (e) {
    if (e && typeof e === "object") e.completionGate = true; // the action already ran: never recover into a re-run
    throw e;
  }
  const title = blocking.title || "a popup";
  ctx.tracker.emit("step_fail", { si: i, fc: "fork_decision_needed" });
  const cause = Object.assign(
    new Error(`Needs a decision: ${title}. The last step ended with a popup asking a question that this workflow doesn't answer, `
      + "so the run stopped instead of reporting success. Tell Conxa in the chat how this popup should be handled."),
    {
      forkDecisionNeeded: true, actionMayHaveTakenEffect: true,
      leftOpenSurface: { title: blocking.title || "", buttons: fork.probeLabelsRaw(blocking).slice(0, 8), blocks: blocking.blocks === false ? false : (blocking.blocks === true ? true : null) },
    },
  );
  throw Object.assign(stepFailure(step, i, cause, preShot, state.warnings), { completionGate: true });
}

// Stop clicks must land now, not after the current step's waits finish: poll the cancel flag while a
// step runs and reject the moment it trips. The abandoned step is left to die when the run's
// browser is torn down; its late rejection is swallowed so it can't surface as an unhandled error.
function raceCancel(stepPromise, cancelCheck) {
  if (!cancelCheck) return stepPromise;
  stepPromise.catch(() => {});
  let timer;
  const cancelled = new Promise((_, reject) => {
    timer = setInterval(() => {
      if (cancelCheck()) reject(Object.assign(new Error("Execution cancelled"), { cancelled: true }));
    }, 100);
  });
  return Promise.race([stepPromise, cancelled]).finally(() => clearInterval(timer));
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
// The one entry for running a step (top level, loop body, fork option, branch body, called workflow).
// It owns two facts every caller used to have to remember:
//  - WHERE the step is: state.stepPath is the chain of indices from the plan's own list down to this step
//    ([9, 0] = the first step inside step 9's chosen option), so progress and failures can name the
//    top-level step they belong to instead of a body-local number that collides with one.
//  - WHICH PAGE it ran on: any error leaving the step carries that page (`failedPage`), so evidence,
//    sign-in checks and recovery describe the tab that failed, not whichever tab the run started on.
async function executeOneStep(ctx, state, steps, i) {
  const frame = { page: null };
  if (!state.stepPath) state.stepPath = [];
  state.stepPath.push(i);
  try {
    return await runStepFrame(ctx, state, steps, i, frame);
  } catch (e) {
    if (e && typeof e === "object" && !e.failedPage && frame.page && !(typeof frame.page.isClosed === "function" && frame.page.isClosed())) {
      e.failedPage = frame.page;
    }
    throw e;
  } finally {
    state.stepPath.pop();
  }
}

async function runStepFrame(ctx, state, steps, i, frame) {
  if (ctx.cancelCheck && ctx.cancelCheck()) {
    throw Object.assign(new Error("Execution cancelled"), { cancelled: true });
  }

  const step = steps[i];
  const stepStartedAt = Date.now(); // a refusal the page reported before this is not this step's fault
  // run_if: a step that runs only when an input says so ("skip this field when it's empty"). It reads
  // the run's inputs and nothing else -- never the page -- so whether a step ran is decided by what the
  // caller supplied, and is reported rather than silent.
  const gate = runIfDecision(step, state.inputs);
  if (gate && gate.skip) {
    state.skippedByInput.push({ index: i, intent: step.intent || "", input: gate.input });
    if (ctx.onPhase) ctx.onPhase(`step_${i}_skipped_by_input`, { step: i, input: gate.input });
    return;
  }
  if (ctx.onStep) ctx.onStep(i, { path: state.stepPath.slice(), step });

  // EXEC-49 P5 (G8): the agent looked at the page and said this is not a moved element. Honour it by
  // ending the run with its reason: a verdict can only make a run stop sooner, never act further.
  if (step._agent_verdict) {
    const v = step._agent_verdict;
    const why = v.why ? `: ${v.why}` : "";
    appendRecoveryEvent({ event: "agent_verdict", slug: ctx.slug, step_index: i, kind: v.kind });
    ctx.tracker.emit("step_fail", { si: i, fc: `agent_verdict_${v.kind}` });
    const cause = v.kind === "decision"
      ? Object.assign(new Error(`The assistant stopped the run at a decision nobody authorised${why}`), { forkDecisionNeeded: true })
      : Object.assign(new Error(`The assistant stopped the run: the app reported a problem${why}`), { fault: true, faultKind: v.kind });
    throw stepFailure(step, i, cause, null, state.warnings);
  }

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

  frame.page = page;
  if (state.hasExecutedStep && page === state.prevPage) await waitForPageLoad(page, state.prevStepType);

  // RT-9 — Studio-only test guard. A caller that passes `_pause_before_irreversible` asks the run to
  // stop, parked on the live page, BEFORE the first step the compiler marked `destructive` (send,
  // submit, pay, delete, publish). It resumes only when the caller says so (`_guard_release`), which
  // releases every later destructive step in the run at once. Like agent_span it is a planned pause,
  // not a failure and not recovery: it never touches recovery_stage.js or retry_budget.js, and with
  // no `guard` on ctx (every customer run, every MCP caller) this block is inert. A dry run already
  // withholds destructive steps, so the guard has nothing to do there.
  if (ctx.guard && ctx.guard.pause && !ctx.guard.released && !ctx.dryRun && step.destructive === true) {
    if (state.loopDepth === 1 && !state.forkDepth && state.loopCursor) {
      // Inside a for_each body the pause carries the loop's row cursor, so the resume continues AT
      // this row and step instead of restarting the loop. `stepIndex` is the loop's own index: that is
      // what the caller resumes from.
      const remaining = steps.slice(i).filter((s) => s && s.destructive === true).length;
      throw Object.assign(new Error("guard_pause"), {
        guardPause: true, stepIndex: state.loopCursor.stepIndex, step, page, remaining,
        loop: { ...state.loopCursor, bodyIndex: i },
      });
    }
    if (state.loopDepth > 0 || state.forkDepth > 0) {
      // A nested loop or an outcome-fork option has no single row cursor to resume from, so fail
      // closed rather than run an irreversible step unguarded (the fork checks its chosen option
      // BEFORE running it, so reaching here means a destructive step nested deeper than it looked).
      ctx.tracker.emit("step_fail", { si: i, fc: "guard_inside_loop" });
      throw stepFailure(step, i, new Error(
        "Test stopped before a repeated or conditional step that can't be undone: the guard can't pause inside a loop or a popup choice. " +
        "Turn the guard off to run it, or test it somewhere safe."
      ), null, state.warnings);
    }
    const remaining = steps.slice(i).filter((s) => s && s.destructive === true).length;
    throw Object.assign(new Error("guard_pause"), { guardPause: true, stepIndex: i, step, page, remaining });
  }

  // Studio-only: stop, parked on the live page, BEFORE a named step. Used to replay a workflow up to
  // step N-1 and hand the page to the recorder for re-recording from N (a segment). Same park as the
  // guard above, same rule about loops and popup choices (no row cursor to resume from).
  if (ctx.stopBefore !== undefined && ctx.stopBefore === i && !ctx.dryRun) {
    if (state.loopDepth > 0 || state.forkDepth > 0) {
      ctx.tracker.emit("step_fail", { si: i, fc: "guard_inside_loop" });
      throw stepFailure(step, i, new Error("Can't stop inside a repeated or conditional step: pick the step that starts the loop instead."), null, state.warnings);
    }
    throw Object.assign(new Error("guard_pause"), { guardPause: true, stepIndex: i, step, page, remaining: 0, stopBefore: true });
  }

  // Proof of Done: this step was parked while its work was still running, and the caller has come back to check.
  // It is a RE-CHECK, never a re-run: nothing is clicked or uploaded again. The page is measured against what it
  // showed before the action; the verdict (done, failed, or still working and parked again) follows the same rules.
  const recheckKey = `__proof_recheck_${i}`;
  if (Object.prototype.hasOwnProperty.call(state.inputs, recheckKey)) {
    let baseline = null;
    try { baseline = JSON.parse(String(state.inputs[recheckKey])); } catch (_) { baseline = null; }
    delete state.inputs[recheckKey];
    const notebook = baseline ? await openNotebook(ctx, page, baseline) : null;
    const completion = { baseline: baseline || null, sinceMs: Date.now(), attempted: true, notebook };
    const gateV = await confirmCompleted(ctx, state, step, i, page, completion, null);
    await proveStep(ctx, state, step, i, { gate: gateV, notebook });
    ctx.tracker.emit("tier_ok", { si: i, tier: "tier1_rechecked" });
    state.hasExecutedStep = true;
    state.prevStepType = step.type;
    state.prevPage = page;
    return;
  }

  // EXEC-49 P5: server.js bound an answer for a popup that blocked THIS (non-fork) step on the previous call.
  if (step.type !== "outcome_fork" && Object.prototype.hasOwnProperty.call(state.inputs, `__fork_choice_${i}`)) {
    if (await answerBlockingPopup(ctx, state, step, page, i)) return;
  }

  // EXEC-21 (hand-over shape): a step the recording deliberately left for a PERSON, not the
  // agent — 2FA, a CAPTCHA, an e-signature. Like agent_span, this is a planned pause: no
  // selector, no identity_bundle, never reaches executeStep/HANDLERS or the recovery cascade.
  // Two speeds (see handover.js's header): most hand-overs finish inside HANDOVER_INCALL_MS and
  // never leave this function — the person clicks the in-page banner and execution just
  // continues in this same call. If they haven't by then, this throws a distinct non-recovery
  // signal (mirroring agent_span_pause) that server.js parks the page on and returns a pause
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
    const armed = await handover.arm(ctx.context, page, step, ctx.runCtx && ctx.runCtx.runId, ctx);
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

  // Engine v2 C4: an author-placed span the caller's agent carries out, one action at a time (see
  // agent_span.js). It also does what the removed ai_review step did: with an `answer` it asks a question and
  // binds the structured reply. A planned pause beside handover: no selector, never reaches
  // executeStep/HANDLERS or the recovery cascade. server.js drives the rounds on the parked page and, when the
  // span ends well, binds `__agent_span_done_<i>` (and `__agent_span_answer_<i>`) into inputs before the resume;
  // this just consumes them. It never asks a person itself, and it fails closed wherever no agent is there to answer.
  if (step.type === "agent_span") {
    const doneKey = `__agent_span_done_${i}`;
    if (Object.prototype.hasOwnProperty.call(state.inputs, doneKey)) {
      delete state.inputs[doneKey];
      const answerKey = `__agent_span_answer_${i}`;
      if (Object.prototype.hasOwnProperty.call(state.inputs, answerKey)) {
        const outputName = (step.answer && step.answer.output_name) || `agent_span_output_${i}`;
        const answer = state.inputs[answerKey];
        state.inputs[outputName] = answer;
        Object.assign(state.inputs, flattenAnswer(outputName, answer));
        delete state.inputs[answerKey];
      }
      state.hasExecutedStep = true;
      state.prevStepType = step.type;
      state.prevPage = page;
      return;
    }
    if (ctx.dryRun) {
      // A preview never lets an agent act on the page: the span is withheld, like a committing step.
      state.dryRunSkipped.push({ index: i, intent: step.intent || step.type });
      ctx.tracker.emit("dry_run_skip", { si: i });
      state.hasExecutedStep = true;
      state.prevStepType = step.type;
      state.prevPage = page;
      return;
    }
    const refuse = (code, message) => {
      ctx.tracker.emit("step_fail", { si: i, fc: code });
      throw stepFailure(step, i, Object.assign(new Error(message), { agentSpanRefused: code }), null, state.warnings);
    };
    if (state.loopDepth > 0 || state.forkDepth > 0) {
      refuse("agent_span_inside_loop", "An agent span cannot run inside a repeated or conditional step.");
    }
    if (ctx.agentSpans === false) {
      refuse("agent_span_no_agent", "This step needs an AI agent to carry it out, and a scheduled run has none. Run it from a chat instead.");
    }
    // Nothing to do when the author's check already holds and no answer is wanted: zero actions, zero tokens.
    if (!step.answer && await agentSpan.untilHolds(page, step.until, state.inputs, await capturePreStepSignature(page))) {
      ctx.tracker.emit("span_done", { si: i, n: 0 });
      state.hasExecutedStep = true;
      state.prevStepType = step.type;
      state.prevPage = page;
      return;
    }
    const brief = (s) => String((s && (s.intent || s.description || s.type)) || "").slice(0, 80);
    const span = await agentSpan.begin(page, step, state.inputs, {
      skill: ctx.slug,
      stepsDone: steps.slice(0, i).map(brief).slice(-12),
      stepsAfter: steps.slice(i + 1).map(brief).slice(0, 12),
    });
    ctx.tracker.emit("span_start", { si: i });
    throw Object.assign(new Error("agent_span_pause"), { spanPause: true, stepIndex: i, step, page, span });
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

  // Run another installed workflow's steps here ("call another workflow"). Like for_each it re-enters
  // THIS function for every step, so tabs, recovery, GATE/VERIFY and dry-run all apply unchanged. See
  // call_workflow.js for everything checked before the run started, and runCallWorkflowStep below.
  if (step.type === "call_workflow") {
    await runCallWorkflowStep(ctx, state, step, page, i);
    state.hasExecutedStep = true;
    state.prevStepType = step.type;
    state.prevPage = page;
    return;
  }

  // Outcome fork — "after the previous action the app is in one of several known states". Like
  // for_each it runs its chosen option through THIS SAME function, so the option's steps get tab
  // resolution, GATE/VERIFY, recovery and dry-run's destructive-skip, unlike a best-effort
  // runBranchBody. See fork.js and runForkStep below.
  // A variant branch ("record another way from here"): a wait_for_one_of marked `flow` whose options
  // are whole ways the rest of the workflow can go. Unlike a cookie-banner branch, the chosen body is
  // real workflow, so it runs through THIS function too (recovery, GATE/VERIFY, tabs) and a failure
  // inside it fails the run instead of being swallowed. See runVariantBranchStep.
  if (step.type === "wait_for_one_of" && step.flow === true) {
    await runVariantBranchStep(ctx, state, step, page, i);
    state.hasExecutedStep = true;
    state.prevStepType = step.type;
    state.prevPage = page;
    return;
  }

  if (step.type === "outcome_fork") {
    const outcome = await runForkStep(ctx, state, step, page, i);
    // A popup can open after the watch window closed. For the next two steps of this same list, a
    // popup this fork knows is still its popup, not a stranger blocking the click (EXEC-49 G5).
    // Never in a dry run (nothing was closed, so it would be handled twice) or inside an option body.
    state.armedFork = (outcome === "clean" || outcome === "continue") && !ctx.dryRun && !state.forkDepth
      ? { steps, forkIndex: i, until: i + 2, states: step.states }
      : null;
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
    // The same rule as the Tier 1 ladder (cascade.js): a popup asking for a decision is never
    // dismissed on anyone's behalf, not even by an agent's nomination. Only a person answers it.
    const liveDialog = await evalOn(page, pageScripts.dialogProbe).catch(() => null);
    if (liveDialog && liveDialog !== EVAL_TIMED_OUT && fork.isDecisionDialog(liveDialog)) {
      appendRecoveryEvent({ event: "overlay_dismiss_rejected", slug: ctx.slug, step_index: i, reason: "decision_dialog" });
      step._dismiss_rejected = "decision_dialog";
      delete step._dismiss_selector;
      delete step._dismiss_escape;
    }
  }
  if (state.armedFork && step.type !== "outcome_fork") await handleLateFork(ctx, state, steps, i, step, page);
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

  // The page must still be the size the workflow was recorded at: a step on a drifted page (zoomed,
  // clipped) would click the wrong place or find nothing. The host panel repairs it; if it can't, stop.
  const geometry = await checkGeometry(page, ctx);
  if (!geometry.ok) {
    if (geometry.unsupported) {
      if (!ctx.geometryWarned) {
        ctx.geometryWarned = true;
        state.warnings.push("The test browser is not at its normal size and this version of Conxa can't reset it. Buttons may look zoomed or cut off.");
      }
    } else {
      ctx.tracker.emit("step_fail", { si: i, fc: "fault_page_size" });
      appendRecoveryEvent({ event: "fault_detected", slug: ctx.slug, step_index: i, kind: "page_size", source: "geometry" });
      throw stepFailure(step, i, Object.assign(new Error(PAGE_SIZE_MESSAGE), { fault: true, faultKind: "page_size" }), null, state.warnings);
    }
  }
  noteBackground(ctx);
  const preShot = await maybeCapturePreStep(page, step);
  const primarySelector = baseSelector(step, state.inputs);
  // Pre-action baseline for the state_changed assertion (only captured when the step actually
  // carries one — cheap, but no reason to pay it on every step).
  const stateBaseline = needsStateChangedBaseline(step) ? await capturePreStepSignature(page) : null;
  // Completion gate: what the page shows before the action, so only NEW progress/errors count after.
  // The evidence notebook opens here too: from now until the verdict it writes down what every tab this run
  // owns and every visible frame shows, and the request/response facts, so the gate is not limited to one page.
  let completion = { baseline: null, sinceMs: 0 };
  if (COMPLETION_GATE_ENABLED && isNonIdempotent(step)) {
    const notebook = await openNotebook(ctx, page, null, uploadFileName(step, state.inputs) || identityNeedle(step, state.inputs));
    completion = { baseline: await captureCompletionBaseline(page, notebook), sinceMs: Date.now(), attempted: true, notebook };
  }

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
    emitVerifyResult(ctx, i, verdict);
    if (!verdict.pass) {
      ctx.tracker.emit("verify_fail", { si: i, ch: verdict.channel });
      throw Object.assign(new Error(`Verification failed: ${verdict.channel}`), {
        verifyFail: true,
        verifyResults: verdict.results,
      });
    }
    const gateV = await confirmCompleted(ctx, state, step, i, page, completion, preShot);
    await proveStep(ctx, state, step, i, { verify: verdict, gate: gateV, preShot, notebook: completion.notebook });
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
    if (err && err.completionGate) throw err; // the action already happened: never recover into a re-run
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
    // on_missing: "skip" -- the author said that a record which is provably not on the page means there is
    // nothing to do here (nothing to update, nothing to delete). Only the "list is there, this record is
    // not in it" case qualifies; an ambiguous or unrendered list still fails closed, and skipping a step
    // never acts on a different row. Reported, never silent.
    if (primaryErr.entityAbsent && step.on_missing === "skip") {
      state.skippedByInput.push({ index: i, intent: step.intent || "", input: "record not on the page" });
      state.warnings.push(`Step ${i + 1}${step.intent ? ` (${step.intent})` : ""} was skipped: its record isn't on the page.`);
      if (ctx.onPhase) ctx.onPhase(`step_${i}_skipped_missing`, { step: i });
      return;
    }
    // on_missing: "ask" -- the same provable absence, but the author wants a person to look: the run
    // pauses like a hand-over (banner, file or http signal, then resume at this step) and, once the
    // person says they've dealt with it, tries the step again from the top. Still never acts on
    // another row, and an ambiguous or unrendered list still fails closed.
    if (primaryErr.entityAbsent && step.on_missing === "ask" && ctx.context) {
      const doneKey = `__handover_done_${i}`;
      if (Object.prototype.hasOwnProperty.call(state.inputs, doneKey)) {
        delete state.inputs[doneKey];
        return runStepFrame(ctx, state, steps, i, frame);
      }
      const asked = { ...step, type: "handover", message: step.message || `“${step.intent || "This step"}”: its record isn't on the page. Fix that, then click Done.` };
      const armed = await handover.arm(ctx.context, page, asked, ctx.runCtx && ctx.runCtx.runId, ctx);
      const winner = await Promise.race([
        armed.signal,
        new Promise((resolve) => setTimeout(() => resolve(null), handover.HANDOVER_INCALL_MS)),
      ]);
      if (winner) {
        await armed.disarm();
        ctx.tracker.emit("handover_resumed", { si: i, via: winner, inCall: true, reason: "on_missing" });
        return runStepFrame(ctx, state, steps, i, frame);
      }
      throw Object.assign(new Error("handover_pause"), { handoverPause: true, stepIndex: i, step: asked, page, armed, dryRun: ctx.dryRun });
    }
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
        const verdict2 = await verifyStep(page, step, state.inputs, stateBaseline, ctx.dialogQueue, { capMs: RECHECK_CAP_MS }).catch(() => null);
        if (verdict2 && verdict2.pass) {
          emitVerifyResult(ctx, i, verdict2);
          const gateV = await confirmCompleted(ctx, state, step, i, page, completion, preShot);
          await proveStep(ctx, state, step, i, { verify: verdict2, gate: gateV, preShot, notebook: completion.notebook });
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
          emitVerifyResult(ctx, i, verdict2);
          if (verdict2.pass) {
            const gateV = await confirmCompleted(ctx, state, step, i, page, completion, preShot);
            await proveStep(ctx, state, step, i, { verify: verdict2, gate: gateV, preShot, notebook: completion.notebook });
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
          if (retryErr && retryErr.completionGate) throw retryErr;
          primaryErr = retryErr;
          primaryErr.earlyDomSnapshot = await captureEarlyDomSnapshot(page, step, state.inputs);
          primaryErr.failedPage = page;
        }
      }
    }
  }

  // EXEC-49 — the app itself says it cannot go on as asked (expired sign-in, no access, offline,
  // rate limit, duplicate data, a rejected form, a record that is gone). Re-finding the element
  // cannot fix that and an agent round would only spend tokens, so stop here with a typed reason.
  // Runs only for a step that already failed, after settle so a transient "Saving..." alert is gone.
  const fault = await detectFault(ctx, page, step, { mayHaveActed, sinceMs: stepStartedAt });
  if (fault) {
    ctx.tracker.emit("step_fail", { si: i, fc: `fault_${fault.kind}` });
    appendRecoveryEvent({ event: "fault_detected", slug: ctx.slug, step_index: i, kind: fault.kind, source: fault.source });
    throw stepFailure(step, i, Object.assign(new Error(faultMessage(fault)), { fault: true, faultKind: fault.kind }), preShot, state.warnings);
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
    if (primaryErr.decisionDialog) {
      emitAppObs(ctx.tracker, i, safePageUrl(page), primaryErr.decisionDialog.title, primaryErr.decisionDialog.buttons, false);
      await askAboutBlockingPopup(ctx, state, step, page, i, primaryErr);
    }
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

  emitVerifyResult(ctx, i, guard.lastVerify);
  const gateV = await confirmCompleted(ctx, state, step, i, page, completion, preShot);
  await proveStep(ctx, state, step, i, { verify: guard.lastVerify || null, gate: gateV, recovered: true, preShot, notebook: completion.notebook });
  state.recoveredSteps++;
  state.hasExecutedStep = true;
  state.prevStepType = step.type;
  state.prevPage = page;
}

// Keys the runtime itself binds for downloads. A called workflow can download a file and a later step
// of the caller can upload it, so these cross the call boundary in both directions; nothing else does.
const DOWNLOAD_INPUT_KEY_RE = /^(downloaded_file|__downloadCount)/;

// "Call another workflow": runs a callee's steps in a scope of their own. The callee sees only the
// inputs the call hands it (plus declared defaults and this run's download bindings), never the
// caller's other values; the caller gets back only the results the call asks for. state.inputs is
// emptied and refilled in place -- other code holds that same object -- and restored in `finally`.
//
// A failure inside the callee is final: it is reported without a step number the caller could resume
// from (a resume would re-run the whole call, repeating work that already happened), and it never
// reaches the agent-recovery round trip, which addresses top-level steps only. Tier A recovery of
// each callee step still runs, inside executeOneStep. A destructive-step guard pause fails closed for
// the same reason it does inside a loop (loopDepth is raised for the duration).
async function runCallWorkflowStep(ctx, state, step, page, i) {
  const call = step.call && typeof step.call === "object" ? step.call : {};
  const slug = String(call.skill || "");
  const stop = (message) => {
    ctx.tracker.emit("step_fail", { si: i, fc: "bad_input" });
    throw stepFailure(step, i, Object.assign(new Error(message), { badInput: true }), null, state.warnings);
  };
  const info = ctx.callees ? ctx.callees[slug] : null;
  if (!info) stop(`The workflow "${slug}" was not loaded for this run.`);
  const stack = ctx.callStack || [];
  if (stack.includes(slug) || stack.length >= MAX_CALL_DEPTH) stop(`The workflow "${slug}" cannot be called from here (it would call itself, or go too many levels deep).`);

  const handed = {};
  for (const f of info.declaredInputs) {
    if (f && f.name && f.default !== undefined && f.default !== null && f.default !== "") handed[f.name] = f.default;
  }
  for (const [name, value] of Object.entries(call.inputs && typeof call.inputs === "object" ? call.inputs : {})) {
    handed[name] = interpolate(String(value), state.inputs);
  }
  const missing = ((info.entry.manifest && info.entry.manifest.inputs_required) || [])
    .filter((n) => handed[n] === undefined || handed[n] === null || String(handed[n]).trim() === "");
  if (missing.length) stop(`The workflow "${slug}" needs ${missing.join(", ")}, but the value is empty.`);

  const saved = { ...state.inputs };
  const carried = {};
  for (const k of Object.keys(state.inputs)) if (DOWNLOAD_INPUT_KEY_RE.test(k)) carried[k] = state.inputs[k];
  for (const k of Object.keys(state.inputs)) delete state.inputs[k];
  Object.assign(state.inputs, carried, handed);

  const subCtx = { ...ctx, slug: `${ctx.slug}>${slug}`, callStack: [...stack, slug], onStep: undefined };
  const results = {};
  const carriedBack = {};
  state.loopDepth = (state.loopDepth || 0) + 1;
  ctx.tracker.emit("call_start", { si: i, n: info.steps.length });
  try {
    for (let j = 0; j < info.steps.length; j++) await executeOneStep(subCtx, state, info.steps, j);
    for (const [from, as] of Object.entries(call.outputs && typeof call.outputs === "object" ? call.outputs : {})) {
      if (state.inputs[from] !== undefined) results[String(as)] = state.inputs[from];
      else state.warnings.push(`The workflow "${slug}" did not produce "${from}", so "${as}" was not set.`);
    }
    for (const k of Object.keys(state.inputs)) if (DOWNLOAD_INPUT_KEY_RE.test(k)) carriedBack[k] = state.inputs[k];
    ctx.tracker.emit("call_done", { si: i });
  } catch (err) {
    if (err && !err.cancelled) {
      err.message = `In the called workflow "${slug}": ${err.message}`;
      delete err.failedAt;
      err.calledWorkflow = slug;
    }
    throw err;
  } finally {
    state.loopDepth -= 1;
    for (const k of Object.keys(state.inputs)) delete state.inputs[k];
    Object.assign(state.inputs, saved);
  }
  Object.assign(state.inputs, carriedBack, results);
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
  // An extract in the body overwrites one input per row; keep every row's value, in row order, and
  // hand the caller the list once the loop is done (a failed/skipped row contributes nothing).
  const bodyOutputs = declaredOutputNames(bodySteps);
  const perRow = Object.fromEntries(bodyOutputs.map((n) => [n, []]));

  // Resuming a guard pause that happened inside this loop: continue AT the paused row and body step.
  // The row is found by its id, not its position, because rows the run already deleted are gone.
  let startIdx = 0;
  let startBody = 0;
  const resume = ctx.resumeLoop && ctx.resumeLoop.stepIndex === i ? ctx.resumeLoop : null;
  if (resume) {
    ctx.resumeLoop = null;
    startIdx = rowIds.indexOf(resume.rowId);
    if (startIdx < 0) {
      ctx.tracker.emit("step_fail", { si: i, fc: "loop_row_missing" });
      throw stepFailure(step, i, new Error(
        "The test paused on a row that is no longer on the page, so it can't continue where it stopped. Run the test again."
      ), null, state.warnings);
    }
    startBody = Number(resume.bodyIndex) || 0;
    for (const n of bodyOutputs) perRow[n] = Array.isArray(resume.perRow && resume.perRow[n]) ? [...resume.perRow[n]] : [];
  }

  state.loopDepth = (state.loopDepth || 0) + 1; // RT-9: a pause inside the body carries state.loopCursor
  try {
    for (let idx = startIdx; idx < rowIds.length; idx++) {
      const resumingRow = idx === startIdx && !!resume;
      state.inputs[idKey] = rowIds[idx];
      state.inputs[idxKey] = idx;
      if (!resumingRow) for (const n of bodyOutputs) delete state.inputs[n];
      try {
        for (let j = resumingRow ? startBody : 0; j < bodySteps.length; j++) {
          state.loopCursor = { stepIndex: i, rowId: rowIds[idx], bodyIndex: j };
          await executeOneStep(ctx, state, bodySteps, j);
        }
        for (const n of bodyOutputs) if (state.inputs[n] !== undefined) perRow[n].push(state.inputs[n]);
        processed++;
      } catch (bodyErr) {
        // Cancellation and a handover pause inside the body are never row-level
        // failures — they must always propagate immediately regardless of on_row_error. (Note:
        // patch_gate.py refuses to compile a handover step inside a for_each body in the first
        // place — unwinding this loop on a pause loses the iteration cursor, so resuming would
        // restart the loop from row 0 rather than continuing the interrupted row. This branch
        // only matters if an older/hand-authored pack manages to carry one anyway.)
        if (bodyErr && bodyErr.guardPause && bodyErr.loop) {
          bodyErr.loop.perRow = JSON.parse(JSON.stringify(perRow));
          throw bodyErr;
        }
        if (bodyErr && (bodyErr.cancelled || bodyErr.handoverPause)) throw bodyErr;
        failed++;
        ctx.tracker.emit("for_each_row_fail", { si: i, row_index: idx, continued: onRowError === "continue" });
        if (onRowError !== "continue") throw bodyErr;
      }
    }
  } finally {
    state.loopDepth -= 1;
    state.loopCursor = null;
    if (hadId) state.inputs[idKey] = prevId; else delete state.inputs[idKey];
    if (hadIdx) state.inputs[idxKey] = prevIdx; else delete state.inputs[idxKey];
  }
  for (const n of bodyOutputs) {
    if (perRow[n].length) state.inputs[n] = perRow[n]; else delete state.inputs[n];
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

function safePageUrl(page) {
  try { return page.url(); } catch (_) { return ""; }
}

// Tell the fleet's app map about a decision popup this run could not place, scrubbed down to its
// shape (scrub.js). Telemetry only: never read back by the run, never fails it.
function emitAppObs(tracker, si, url, title, labels, known) {
  try {
    const surface = fork.classifySurface({ title, controls: (Array.isArray(labels) ? labels : []).map((name) => ({ name })) });
    const obs = appKnowledge.observation(url, { title, labels, known, surface });
    if (obs) tracker.emit("app_obs", { si, ...obs });
  } catch (_) { /* advisory */ }
}

// What dialog is showing right now. A native alert/confirm/prompt blocks the page's JS thread
// (page.evaluate would just time out), so the queued Playwright Dialog is checked first; a DOM
// dialog comes from page_scripts.dialogProbe. Never throws: an unreadable page reads as "nothing
// open", and the fork then falls through to its clean path.
async function observeDialog(page, ctx) {
  const queued = ctx && Array.isArray(ctx.dialogQueue) ? ctx.dialogQueue[0] : null;
  if (queued) {
    const type = typeof queued.type === "function" ? queued.type() : "";
    const message = typeof queued.message === "function" ? queued.message() : "";
    const controls = [{ tag: "button", role: "button", name: "OK", text: "OK" }];
    if (type === "confirm" || type === "prompt") controls.push({ tag: "button", role: "button", name: "Cancel", text: "Cancel" });
    return { native: true, title: type, text: String(message || ""), controls };
  }
  try {
    const probe = await evalOn(page, pageScripts.dialogProbe);
    return probe && probe !== EVAL_TIMED_OUT ? probe : null;
  } catch (_) {
    return null;
  }
}

// Click a visible dialog button the PERSON chose by its label -- the unknown-popup path, where no
// compiled option exists to run. Scoped to the topmost open dialog and an exact accessible-name
// match, so a same-named button elsewhere on the page can never be hit. The label came from the
// live dialog inventory and was picked by a human, never by a model.
async function clickDialogButtonByLabel(page, label) {
  const dialogs = page.locator('dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]');
  const button = dialogs.last().getByRole("button", { name: label, exact: true });
  await button.first().click({ timeout: 5000 });
}

// RT-9: a destructive choice is checked HERE, at the fork's own index. The guard can't pause
// inside an option body (the cursor would be lost), so look before running it.
function forkGuard(ctx, state, step, page, i, destructive) {
  if (!(destructive && ctx.guard && ctx.guard.pause && !ctx.guard.released && !ctx.dryRun)) return;
  if (state.loopDepth > 0) {
    ctx.tracker.emit("step_fail", { si: i, fc: "guard_inside_loop" });
    throw stepFailure(step, i, new Error(
      "Test stopped before a repeated step that can't be undone: the guard can't pause inside a loop. " +
      "Turn the guard off to run it, or test it somewhere safe."
    ), null, state.warnings);
  }
  throw Object.assign(new Error("guard_pause"), { guardPause: true, stepIndex: i, step, page, remaining: 1 });
}

// Run one chosen option: the RT-9 guard check at the fork's own index, the option's steps through
// the real executeOneStep, then confirm the popup is actually gone.
async function runForkOption(ctx, state, step, page, i, option, title, answered) {
  const optionSteps = Array.isArray(option.steps) ? option.steps : [];

  forkGuard(ctx, state, step, page, i, optionSteps.some((s) => s && s.destructive === true));

  state.forkDepth = (state.forkDepth || 0) + 1;
  try {
    for (let j = 0; j < optionSteps.length; j++) {
      await executeOneStep(ctx, state, optionSteps, j);
    }
  } finally {
    state.forkDepth -= 1;
  }
  await confirmForkDialogClosed(ctx, state, step, page, i, answered, title, option.label || option.id);

  // Where the workflow goes from here (EXEC-49 G4). Default: carry on with the next recorded step.
  // "end": this path is a complete run on its own. "stop": the rest of the workflow does not cover
  // this path, so nothing past here may run. Inside a repeated step "end" would silently skip the
  // remaining rows, so there it stops too.
  const then = option.then === "end" || option.then === "stop" ? option.then : "rejoin";
  if (then === "end" && !(state.loopDepth > 0)) {
    appendRecoveryEvent({ event: "fork_option_end", slug: ctx.slug, step_index: i, option: option.id });
    throw Object.assign(new Error("run_ended"), { runEnded: true, stepIndex: i });
  }
  if (then !== "rejoin") {
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_option_stop" });
    appendRecoveryEvent({ event: "fork_option_stop", slug: ctx.slug, step_index: i, option: option.id });
    throw stepFailure(step, i, Object.assign(
      new Error(`Stopped after "${option.label || option.id}" in the "${title}" popup: the rest of this workflow does not cover that path.`),
      { forkDecisionNeeded: true },
    ), null, state.warnings);
  }
}

// The choice must have actually dealt with the popup. THAT popup (`answered`: the fork state, or the
// signature of the live popup taken before the click) still on screen means the click did nothing (or
// the app re-asked) -- failing here is cleaner than the next step tripping over it. A different surface
// is not this popup: a progress tray or "complete" notice opening right after the answer is the app
// working, so it is passed over, and a different blocking popup is left to the next step, with a warning.
async function confirmForkDialogClosed(ctx, state, step, page, i, answered, title, chosenLabel) {
  if (ctx.dryRun) return;
  let seen = null;
  const closed = await pollNegative(async () => {
    seen = await observeDialog(page, ctx);
    return !fork.stillShowing(answered, seen);
  }, 3000);
  if (!closed) {
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_dialog_still_open" });
    throw stepFailure(step, i, new Error(`The "${title}" popup is still open after choosing "${chosenLabel}".`), null, state.warnings);
  }
  const cls = fork.classifySurface(seen);
  if (cls && cls !== "notice" && fork.blocksPage(seen)) {
    state.warnings.push(`After choosing "${chosenLabel}" in the "${title}" popup, another popup ("${(seen && seen.title) || "untitled"}") appeared.`);
  }
}

// Picks the first option whose probe (an identity bundle or a selector) is on the page, polling up to
// timeout_ms, and runs that option's steps as full steps. None appearing is a failure: this is the
// workflow itself, and carrying on without it would report a pass for work that never happened.
async function runVariantBranchStep(ctx, state, step, page, i) {
  const options = Array.isArray(step.options) ? step.options.filter((o) => o && typeof o === "object") : [];
  if (!options.length) {
    ctx.tracker.emit("step_fail", { si: i, fc: "bad_input" });
    throw stepFailure(step, i, Object.assign(new Error("branch step has no options"), { badInput: true }), null, state.warnings);
  }
  const deadline = Date.now() + (Number(step.timeout_ms) || 5000);
  let chosen = -1;
  for (;;) {
    for (let k = 0; k < options.length && chosen < 0; k++) {
      if (await probePresent(page, options[k], state.inputs, 0)) chosen = k;
    }
    if (chosen >= 0 || Date.now() >= deadline) break;
    await new Promise((r) => setTimeout(r, 200));
  }
  if (chosen < 0) {
    ctx.tracker.emit("step_fail", { si: i, fc: "branch_no_match" });
    throw stepFailure(step, i, new Error(
      "None of the known ways this workflow can go from here appeared on the page. The app may have changed, or it is a way nobody recorded yet.",
    ), null, state.warnings);
  }
  ctx.tracker.emit("branch_chosen", { si: i, opt: chosen });
  const body = Array.isArray(options[chosen].steps) ? options[chosen].steps : [];
  state.forkDepth = (state.forkDepth || 0) + 1; // a guard pause has no cursor inside a branch body yet
  try {
    for (let j = 0; j < body.length; j++) await executeOneStep(ctx, state, body, j);
  } finally {
    state.forkDepth -= 1;
  }
}

// EXEC-49 P5 -- an UNRECORDED popup that asks a question, on a run whose caller can reason (an MCP
// client, or Build Studio's test with "let AI answer" on). The run parks and asks that caller to pick
// one of the popup's own visible buttons, deciding from the user's goal and asking the user when unsure.
// Never offered when any button looks irreversible (delete, pay, send, publish...), never inside a
// loop or an option body (a pause there has no cursor to resume from), never in a dry run: those stop.
function agentPopupPause(ctx, state, step, page, i, probe, { acted = false, known = false } = {}) {
  const title = (probe && probe.title) || "a popup";
  const stop = (why) => {
    ctx.tracker.emit("fork_decided", { si: i, st: "unknown", mode: "stop", src: "agent_gate" });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: "unknown", mode: "stop", source: "agent_gate", title, why });
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_decision_needed" });
    throw stepFailure(step, i, Object.assign(
      new Error(`Needs a decision: ${title}. ${why} Nothing was changed. Tell Conxa in the chat how this popup should be handled.`),
      { forkDecisionNeeded: true },
    ), null, state.warnings);
  };
  if (ctx.dryRun) stop("A dry run does not answer popups.");
  if (state.loopDepth > 0 || state.forkDepth > 0) stop("Nobody can be asked inside a repeated or conditional step.");
  const { choices, blockedBy } = fork.agentPopupChoices(probe);
  if (blockedBy) stop(`It has a button that can't be undone ("${blockedBy}"), so it is never answered automatically.`);
  ctx.tracker.emit("fork_paused", { si: i, st: "unknown", src: "agent" });
  appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: "unknown", mode: "pause", reason: "agent_decides", source: "agent", title });
  throw Object.assign(new Error("fork_pause"), {
    forkPause: true,
    stepIndex: i,
    step,
    page,
    info: {
      title,
      text: (probe && probe.text) || "",
      reason: "agent_decides",
      suggested: null,
      unknown: true,
      knownFromMap: !!known,
      agent: true,
      acted: !!acted,
      stepIntent: step.intent || step.type || "",
      choices,
    },
  });
}

// A step failed and a decision popup is what is in the way. Put the question to the caller, unless the
// step can't be undone (that one fails closed) or this run has nobody to ask. Throws the pause; returns
// when it should not ask, and the ordinary failure follows.
async function askAboutBlockingPopup(ctx, state, step, page, i, primaryErr) {
  if (!ctx.answerPopups || step.destructive === true) return;
  const blocking = await observeDialog(page, ctx);
  if (blocking && fork.isDecisionDialog(blocking)) {
    agentPopupPause(ctx, state, step, page, i, blocking, { acted: !!(primaryErr.verifyFail || primaryErr.mayHaveActed) });
  }
}

// Resume of an agent pause on a step that is NOT a fork: answer the popup with the chosen button, then
// either carry on (the step's own action had not run) or only re-check its result (it had: running it
// again could do the work twice). Called from executeOneStep when server.js bound the answer.
// Returns true when the step is finished (its result was re-checked), false when it should now run.
async function answerBlockingPopup(ctx, state, step, page, i) {
  const choice = String(state.inputs[`__fork_choice_${i}`]);
  const acted = state.inputs[`__fork_acted_${i}`] === 1;
  const byAgent = state.inputs[`__fork_agent_${i}`] === 1;
  for (const k of ["choice", "acted", "agent"]) delete state.inputs[`__fork_${k}_${i}`];
  const fail = (code, message, extra = {}) => {
    ctx.tracker.emit("step_fail", { si: i, fc: code });
    throw stepFailure(step, i, Object.assign(new Error(message), extra), null, state.warnings);
  };
  const who = byAgent ? "agent" : "human";

  if (choice === fork.STOP_CHOICE) {
    ctx.tracker.emit("fork_decided", { si: i, st: "unknown", mode: "stop", src: who });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, mode: "stop", source: who });
    fail("fork_stopped_by_user", "Stopped at the popup, as you chose.", { forkDecisionNeeded: true });
  }

  const probe = await observeDialog(page, ctx);
  if (!probe) {
    // Whoever answered (or the person, in the page) already closed it.
    ctx.tracker.emit("fork_decided", { si: i, st: "unknown", mode: "handled_in_page", src: who });
  } else {
    const labels = new Set(fork.probeLabelsRaw(probe).map((l) => fork.norm(l)));
    const title = probe.title || "a popup";
    if (!labels.has(fork.norm(choice))) {
      fail("fork_choice_invalid", `The popup changed: "${choice}" is no longer one of its buttons.`, { badInput: true });
    }
    // Checked again at click time, not only when the choices were offered: the popup may have changed.
    if (byAgent && fork.isIrreversibleLabel(choice)) {
      fail("fork_decision_needed", `"${choice}" can't be undone, so it is never chosen automatically. Nothing was changed.`, { forkDecisionNeeded: true });
    }
    countSurface(ctx, state, step, i, "unknown", title);
    ctx.tracker.emit("fork_decided", { si: i, st: "unknown", opt: choice, mode: "run", src: who });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: "unknown", option: choice, mode: "run", source: who, title });
    if (ctx.dryRun) {
      state.warnings.push(`Dry run: did not click "${choice}" in the "${title}" popup.`);
    } else {
      try {
        await clickDialogButtonByLabel(page, choice);
      } catch (e) {
        ctx.tracker.emit("step_fail", { si: i, fc: "fork_click_failed" });
        throw stepFailure(step, i, e, null, state.warnings);
      }
      await confirmForkDialogClosed(ctx, state, step, page, i, fork.surfaceSignature(probe), title, choice);
    }
    state.warnings.push(`The "${title}" popup was answered with "${choice}" ${byAgent ? "by the assistant" : "by a person"}.`);
  }

  if (!acted) return false; // the step's own action never ran: let it run now that the way is clear
  // The action already ran and the popup was only in the way of seeing its result: check it, never redo it.
  const verdict = await verifyStep(page, step, state.inputs, null, ctx.dialogQueue).catch(() => null);
  if (verdict && verdict.pass) {
    emitVerifyResult(ctx, i, verdict);
    await proveStep(ctx, state, step, i, { verify: verdict });
    ctx.tracker.emit("tier_ok", { si: i, tier: "tier1_after_popup" });
    state.hasExecutedStep = true;
    state.prevStepType = step.type;
    state.prevPage = page;
    return true;
  }
  fail("verify_fail_after_popup", `Step ${i + 1} had already run; after the popup was answered its result still could not be confirmed. It was not run again.`, { verifyFail: true });
  return true;
}

// A surface the run keeps handling is a loop, not progress. Counted per (popup, loop row, called
// workflow), because a legitimate "Saved" confirmation inside a for_each shows once per row.
const MAX_SURFACE_HANDLINGS = 2;

function countSurface(ctx, state, step, i, stateId, title) {
  const row = state.loopCursor && state.loopCursor.rowId != null ? String(state.loopCursor.rowId) : "";
  const key = [stateId, fork.norm(fork.valueFreeTitle(title)), row, (ctx.callStack || []).join(">")].join("|");
  state.surfaceCounts = state.surfaceCounts || new Map();
  const n = (state.surfaceCounts.get(key) || 0) + 1;
  state.surfaceCounts.set(key, n);
  if (n > MAX_SURFACE_HANDLINGS) {
    ctx.tracker.emit("step_fail", { si: i, fc: "surface_loop" });
    appendRecoveryEvent({ event: "surface_loop", slug: ctx.slug, step_index: i, state: stateId, title, count: n });
    throw stepFailure(step, i, Object.assign(
      new Error(`The "${title}" popup keeps coming back (${n} times in this run), so the run was stopped instead of answering it again.`),
      { forkDecisionNeeded: true },
    ), null, state.warnings);
  }
}

// A popup the previous fork step knows, caught on one of the next two steps (G5). Only a state the
// fork lists is acted on: a stranger stays "left alone" as before.
async function handleLateFork(ctx, state, steps, i, step, page) {
  const armed = state.armedFork;
  if (armed.steps !== steps) return;
  if (i > armed.until) { state.armedFork = null; return; }
  if (i <= armed.forkIndex || state.forkDepth > 0 || ctx.dryRun) return;
  const probe = await observeDialog(page, ctx);
  const seen = fork.matchState(probe, armed.states);
  if (seen.kind !== "state") return;
  state.armedFork = null;
  const stateId = seen.state.id;
  const title = (seen.state.signature && seen.state.signature.title) || (probe && probe.title) || "a popup";
  let decision;
  try {
    decision = fork.decide(seen.state, state.inputs, { loopDepth: state.loopDepth || 0 });
  } catch (e) {
    ctx.tracker.emit("step_fail", { si: i, fc: e && e.badInput ? "bad_input" : "fork_decision" });
    throw stepFailure(step, i, e, null, state.warnings);
  }
  // The test guard pauses at the fork's own index, which is behind us now: a destructive option
  // cannot be paused for here, so it stops rather than running unguarded.
  if (decision.do === "run" && ctx.guard && ctx.guard.pause && !ctx.guard.released
      && (decision.option.steps || []).some((s) => s && s.destructive === true)) {
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_decision_needed" });
    throw stepFailure(step, i, Object.assign(
      new Error(`The "${title}" popup opened late and its answer can't be undone; the test guard is on, so the test stopped.`),
      { forkDecisionNeeded: true },
    ), null, state.warnings);
  }
  ctx.tracker.emit("fork_observed", { si: i, st: stateId, late: 1 });
  await actOnForkDecision(ctx, state, step, page, i, decision, { stateId, title, probe, seen, known: null, late: true });
}

// What to do once a fork has decided. Shared by the fork step itself and by a late popup (a known
// state that opened after the watch window closed, caught on one of the next steps). Returns
// "continue" when the popup was left alone, nothing otherwise. `late` degrades a pause to a stop:
// the resume path for a pause is bound to the fork step's own index.
async function actOnForkDecision(ctx, state, step, page, i, decision, { stateId, title, probe, seen, known, late = false }) {
  if (late && decision.do === "pause") {
    ctx.tracker.emit("fork_decided", { si: i, st: stateId, mode: "stop", src: "late" });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: stateId, mode: "stop", source: "late", title });
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_decision_needed" });
    throw stepFailure(step, i, Object.assign(new Error(`Needs a decision: ${title}. It opened after the step that was meant to handle it.`), { forkDecisionNeeded: true }), null, state.warnings);
  }

  if (decision.do === "continue") {
    // Nothing here is evidence of a choice, or the author said to leave it. Click nothing and go
    // on as recorded; the warning travels with the run, so if this surface is what blocks the
    // next step, that step's failure names it.
    ctx.tracker.emit("fork_decided", { si: i, st: stateId, mode: "continue", src: "policy" });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: stateId, mode: "continue", source: "policy", title });
    state.warnings.push(`"${title}" was on screen and was left alone; the run went on as recorded.`);
    return "continue";
  }

  if (decision.do === "pause") {
    // A decision a person asked to be asked about (policy.reviewed). Park the page and hand
    // server.js the question; it resumes through __fork_choice_<i>. Never answered by a model on
    // the person's behalf.
    ctx.tracker.emit("fork_paused", { si: i, st: stateId });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: stateId, mode: "pause", reason: decision.reason, title });
    throw Object.assign(new Error("fork_pause"), {
      forkPause: true,
      stepIndex: i,
      step,
      page,
      info: {
        title,
        text: (probe && probe.text) || "",
        reason: decision.reason,
        suggested: decision.suggested || null,
        unknown: seen.kind !== "state",
        knownFromMap: !!known,
        choices: fork.pauseChoices(seen.kind === "state" ? seen.state : null, probe).map((c) => {
          // A popup the app map knows: say which of its real buttons are risky. The labels are
          // still only the ones on screen, so the answer can never name an invented action.
          const role = known && c.role === "unknown" ? appKnowledge.roleOf(known, c.label) : null;
          return role ? { ...c, role } : c;
        }),
      },
    });
  }

  if (decision.do === "click") {
    countSurface(ctx, state, step, i, stateId, title);
    // The author's standing policy for a popup the app map knows (set in the App maps page, shipped
    // with the skill). Same safety as a fork's `auto`: only a visible button, a consequential one
    // only when the author approved it (decideKnown), and the destructive guard still applies.
    ctx.tracker.emit("fork_decided", { si: i, st: stateId, opt: decision.label, mode: "run", src: decision.source });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: stateId, option: decision.label, mode: "run", source: decision.source, title });
    forkGuard(ctx, state, step, page, i, appKnowledge.roleOf(known, decision.label) === "consequential");
    if (ctx.dryRun) {
      state.warnings.push(`Dry run: did not click "${decision.label}" in the "${title}" popup.`);
      return;
    }
    try {
      await clickDialogButtonByLabel(page, decision.label);
    } catch (e) {
      ctx.tracker.emit("step_fail", { si: i, fc: "fork_click_failed" });
      throw stepFailure(step, i, e, null, state.warnings);
    }
    await confirmForkDialogClosed(ctx, state, step, page, i, fork.surfaceSignature(probe), title, decision.label);
    return;
  }

  if (decision.do === "stop") {
    const message = decision.message || `Needs a decision: ${title}`;
    ctx.tracker.emit("fork_decided", { si: i, st: stateId, mode: "stop", src: "policy" });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: stateId, mode: "stop", source: "policy", title });
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_decision_needed" });
    throw stepFailure(step, i, Object.assign(new Error(message), { forkDecisionNeeded: true }), null, state.warnings);
  }

  const option = decision.option;
  countSurface(ctx, state, step, i, stateId, title);
  ctx.tracker.emit("fork_decided", { si: i, st: stateId, opt: option.id, mode: "run", src: decision.source });
  appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: stateId, option: option.id, mode: "run", source: decision.source, title });
  await runForkOption(ctx, state, step, page, i, option, title, seen.state);
}

async function runForkStep(ctx, state, step, page, i) {
  const states = Array.isArray(step.states) ? step.states : [];
  if (!states.length) {
    ctx.tracker.emit("step_fail", { si: i, fc: "bad_input" });
    throw stepFailure(step, i, Object.assign(new Error("outcome_fork step has no states"), { badInput: true }), null, state.warnings);
  }

  // Resuming after a person answered: server.js bound their choice under __fork_choice_<i>.
  const choiceKey = `__fork_choice_${i}`;
  if (Object.prototype.hasOwnProperty.call(state.inputs, choiceKey)) {
    const choice = String(state.inputs[choiceKey]);
    delete state.inputs[choiceKey];
    return runForkAnswer(ctx, state, step, page, i, states, choice);
  }

  const timeoutMs = fork.clampTimeout(step.timeout_ms);
  const deadline = Date.now() + timeoutMs;

  // Watch for up to timeout_ms: a dialog can open a beat after the triggering action returns.
  // A known state ends the wait at once. Anything else keeps being watched: a toast or progress
  // panel goes away on its own (the last look is then clean), and a real popup may arrive behind it.
  let probe = null;
  let seen = { kind: "clean" };
  for (;;) {
    probe = await observeDialog(page, ctx);
    seen = fork.matchState(probe, states);
    if (seen.kind === "state" || Date.now() >= deadline) break;
    await new Promise((r) => setTimeout(r, Math.min(200, Math.max(0, deadline - Date.now()))));
  }

  if (seen.kind === "clean") {
    ctx.tracker.emit("fork_observed", { si: i, st: "clean" });
    ctx.tracker.emit("fork_clean", { si: i });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: "clean", mode: "none", source: "observed" });
    return "clean";
  }

  let decision;
  let stateId = "unknown";
  let title = (probe && probe.title) || "a popup";
  let known = null; // a popup the app map knows although this fork does not list it
  try {
    if (seen.kind === "state") {
      stateId = seen.state.id;
      title = (seen.state.signature && seen.state.signature.title) || title;
      decision = fork.decide(seen.state, state.inputs, { loopDepth: state.loopDepth || 0 });
    } else {
      ctx.tracker.emit("fork_unknown", { si: i });
      known = appKnowledge.lookupPopup(ctx.appKnowledge, appKnowledge.hostOf(safePageUrl(page)), probe);
      emitAppObs(ctx.tracker, i, safePageUrl(page), probe && probe.title, fork.probeLabelsRaw(probe), !!known);
      if (known) {
        stateId = `map:${known.id}`;
        decision = appKnowledge.decideKnown(known, probe, { loopDepth: state.loopDepth || 0 });
      } else {
        decision = fork.decideUnknown(step, probe, { loopDepth: state.loopDepth || 0 });
      }
    }
  } catch (e) {
    ctx.tracker.emit("step_fail", { si: i, fc: e && e.badInput ? "bad_input" : "fork_decision" });
    throw stepFailure(step, i, e, null, state.warnings);
  }
  ctx.tracker.emit("fork_observed", { si: i, st: stateId });

  // EXEC-49 P5: a popup this fork never heard of, on a run whose caller can reason: ask it (the
  // popup's own visible buttons only; irreversible-looking ones and loops stop instead).
  if (seen.kind !== "state" && decision.do === "continue" && ctx.answerPopups && fork.isDecisionDialog(probe)) {
    return agentPopupPause(ctx, state, step, page, i, probe, { known: !!known });
  }
  return actOnForkDecision(ctx, state, step, page, i, decision, { stateId, title, probe, seen, known });
}

// Re-entry after a person chose. Looks at the page again rather than trusting the park: the
// person may have clicked in the page themselves, or the app may have shown something else.
async function runForkAnswer(ctx, state, step, page, i, states, choice) {
  if (choice === fork.STOP_CHOICE) {
    ctx.tracker.emit("fork_decided", { si: i, st: "answered", mode: "stop", src: "human" });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, mode: "stop", source: "human" });
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_stopped_by_user" });
    throw stepFailure(step, i, Object.assign(new Error("Stopped at the popup, as you chose."), { forkDecisionNeeded: true }), null, state.warnings);
  }

  const probe = await observeDialog(page, ctx);
  const seen = fork.matchState(probe, states);

  if (seen.kind === "clean") {
    // The person dealt with it in the page themselves.
    ctx.tracker.emit("fork_decided", { si: i, st: "answered", mode: "handled_in_page", src: "human" });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, mode: "handled_in_page", source: "human" });
    return;
  }

  const title = (probe && probe.title) || "a popup";
  if (seen.kind === "state") {
    const option = fork.findOption(seen.state, choice);
    if (!option) {
      ctx.tracker.emit("step_fail", { si: i, fc: "fork_choice_invalid" });
      throw stepFailure(step, i, Object.assign(new Error(`"${choice}" is not one of the choices for the "${title}" popup.`), { badInput: true }), null, state.warnings);
    }
    countSurface(ctx, state, step, i, seen.state.id, title);
    ctx.tracker.emit("fork_decided", { si: i, st: seen.state.id, opt: option.id, mode: "run", src: "human" });
    appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: seen.state.id, option: option.id, mode: "run", source: "human", title });
    await runForkOption(ctx, state, step, page, i, option, title, seen.state);
    return;
  }

  // An unrecognised popup: the person picked one of its visible buttons by label.
  const labels = new Set(fork.probeLabelsRaw(probe).map((l) => fork.norm(l)));
  if (!labels.has(fork.norm(choice))) {
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_choice_invalid" });
    throw stepFailure(step, i, Object.assign(new Error(`The popup changed: "${choice}" is no longer one of its buttons.`), { badInput: true }), null, state.warnings);
  }
  if (state.inputs[`__fork_agent_${i}`] === 1 && fork.isIrreversibleLabel(choice)) {
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_decision_needed" });
    throw stepFailure(step, i, Object.assign(new Error(`"${choice}" can't be undone, so it is never chosen automatically. Nothing was changed.`), { forkDecisionNeeded: true }), null, state.warnings);
  }
  countSurface(ctx, state, step, i, "unknown", title);
  ctx.tracker.emit("fork_decided", { si: i, st: "unknown", opt: choice, mode: "run", src: state.inputs[`__fork_agent_${i}`] === 1 ? "agent" : "human" });
  appendRecoveryEvent({ event: "fork_decision", slug: ctx.slug, step_index: i, state: "unknown", option: choice, mode: "run", source: "human", title });
  if (ctx.dryRun) {
    state.warnings.push(`Dry run: did not click "${choice}" in the "${title}" popup.`);
    return;
  }
  try {
    await clickDialogButtonByLabel(page, choice);
  } catch (e) {
    ctx.tracker.emit("step_fail", { si: i, fc: "fork_click_failed" });
    throw stepFailure(step, i, e, null, state.warnings);
  }
  await confirmForkDialogClosed(ctx, state, step, page, i, fork.surfaceSignature(probe), title, choice);
}

async function runPlan(startPage, steps, inputs, startFrom, slug, { onStep, onPhase, cancelCheck, deadlineAt, scheduled, tracker, downloadQueue, dialogQueue, structuralFingerprint, environmentFingerprint, watch, runId, hostOwned, hostRunId, dataDir, dryRun, isResume, context, guard, stopBefore, resumeLoop, appKnowledge: knowledge, callees, answerPopups, agentSpans } = {}) {
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
    // (agent_span answers, download_observed) write into — shared and mutated across the whole run,
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
    skippedByInput: [],
    // Proof of Done: what each step proved (see proveStep), plain notes for a low-risk step that could
    // not be confirmed, and where the run stopped because a risky one could not be.
    proof: { steps: [], notes: [], stopped: null, later: [] },
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
  // Completion gate: the last step that acts gets the full wait (it is what the run's "Done" rests on).
  let lastActStep = null;
  for (let k = steps.length - 1; k >= startFrom && !lastActStep; k--) if (isNonIdempotent(steps[k])) lastActStep = steps[k];

  const ctx = {
    lastActStep,
    steps, // the plan's own steps: a value_equals check finds the earlier field it re-reads here
    lastStep: steps[steps.length - 1] || null, // a popup left open by this step has no next step to answer it
    deadlineAt: deadlineAt || null, // the run's own time limit: the completion wait stops short of it
    scheduled: scheduled === true,  // nobody can be asked: "couldn't confirm" on any step ends the run
    onStep, onPhase, cancelCheck, tracker: t, downloadQueue, dialogQueue,
    tabs, watch, dryRun, slug, runCtx, context, // context: EXEC-21 handover needs it to arm its banner
    guard, // RT-9: Studio-only pause before the first step that can't be undone (see executeOneStep)
    stopBefore, // Studio-only: park just before this step (segment re-recording)
    resumeLoop: resumeLoop || null, // a guard pause inside a for_each body: {stepIndex, rowId, bodyIndex, perRow}
    appKnowledge: knowledge || null, // advisory popup knowledge for an unrecognised dialog (app_knowledge.js)
    callees: callees || null, // call_workflow: the workflows this one calls, loaded and checked at run start
    callStack: [slug],
    answerPopups: answerPopups === true, // EXEC-49: an MCP client may be asked about an unrecorded question popup
    agentSpans: agentSpans !== false,   // Engine v2 C4: false (a scheduled run) = an agent_span step fails closed
    hostOwned: hostOwned === true, hostRunId: hostRunId ?? runId, // page_geometry.js: who to ask to repair the page size
  };

  let endedAt = null;
  const ran = new Set(); // source_step_key of every step that executed and passed (feeds resolveLaterProofs)
  try {
    for (let i = startFrom; i < steps.length; i++) {
      try {
        await raceCancel(executeOneStep(ctx, state, steps, i), ctx.cancelCheck);
        const skipped = [...state.dryRunSkipped, ...state.skippedByInput].some((s) => s.index === i);
        if (steps[i] && steps[i].source_step_key && !skipped) ran.add(steps[i].source_step_key);
      } catch (e) {
        // A popup option marked "end" finished the run on purpose: not a failure, and nothing after runs.
        if (e && e.runEnded) {
          endedAt = e.stepIndex;
          state.warnings.push(`The run finished at step ${e.stepIndex + 1}: the answer chosen for the popup ends this workflow.`);
          break;
        }
        throw e;
      }
    }
  } finally {
    closeNotebook(ctx); // however the plan ends, no listener outlives it
  }
  resolveLaterProofs(state, ran);

  return {
    recoveredSteps: state.recoveredSteps, warnings: state.warnings, dryRunSkipped: state.dryRunSkipped,
    skippedByInput: state.skippedByInput, outputs: collectOutputs(steps, state.inputs),
    proof: { steps: state.proof.steps, notes: state.proof.notes },
    ...(endedAt !== null ? { endedAt } : {}),
  };
}

// ── run_if and outputs ───────────────────────────────────────────────────────────────────────

// { input, op: "not_empty" | "equals" | "not_equals", value? }. Anything malformed means "run the
// step": a bad condition must never silently remove a step from a run.
function runIfDecision(step, inputs) {
  const cond = step && step.run_if;
  if (!cond || typeof cond !== "object" || !cond.input) return null;
  const raw = inputs ? inputs[cond.input] : undefined;
  const have = raw !== undefined && raw !== null && String(raw).trim() !== "";
  const op = String(cond.op || "not_empty");
  let run;
  if (op === "not_empty") run = have;
  else if (op === "equals") run = have && String(raw).trim() === String(cond.value ?? "").trim();
  else if (op === "not_equals") run = !have || String(raw).trim() !== String(cond.value ?? "").trim();
  else return null;
  return { skip: !run, input: String(cond.input) };
}

// A {{placeholder}} cannot reach inside an object, so each plain field of an agent_span answer is also bound on
// its own as `<output_name>_<field>` (see agent_span.js). That is what lets a later step use the answer, and what
// `run_if` reads to choose between steps.
const flattenAnswer = agentSpan.flattenAnswer;

// Every extract / agent_span answer output_name, nested bodies included. `withPrivate` also lists a
// clipboard_read's name: it must survive a pause (server.js carries it across) but is never reported
// to the caller, because the clipboard may hold anything the person copied.
function declaredOutputNames(steps, withPrivate = false) {
  const names = [];
  const walk = (list) => {
    for (const s of Array.isArray(list) ? list : []) {
      if (!s || typeof s !== "object") continue;
      if (s.type === "extract" && s.output_name) names.push(String(s.output_name));
      if (s.type === "agent_span" && s.answer && s.answer.output_name) names.push(String(s.answer.output_name));
      if (withPrivate && s.type === "clipboard_read" && s.output_name) names.push(String(s.output_name));
      walk(s.steps);
      if (s.for_each) walk(s.for_each.steps);
      if (s.branch) walk(s.branch.steps);
    }
  };
  walk(steps);
  return names;
}

// The values this run read off the page, by name: every extract step's output_name (nested bodies
// included) that now holds a value. A name no step produced is simply absent.
function collectOutputs(steps, inputs) {
  const names = declaredOutputNames(steps);
  const out = {};
  for (const n of names) if (inputs && inputs[n] !== undefined) out[n] = inputs[n];
  return out;
}

// Auth-failure detection — login redirect or session-expired page heuristics. A page that is a
// sign-in page by name (/login, /signin, /session-expired) is a failure on its own. A sign-in hop
// (/auth, /oauth2, /sso, /logout) is also an ordinary in-app page (/settings/auth), so it counts
// only with a second piece of evidence: a password box, or a sign-in title. The words are shared
// with the recorder and the sign-in ladder (login_paths.js).
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
  const pathname = pathOf(url);
  if (SIGN_IN_PAGE_RE.test(pathname)) return true;
  let titled = false;
  try {
    titled = AUTH_FAILURE_TITLE_RE.test(await page.title());
  } catch (_) {}
  if (titled) return true;
  if (!SSO_HOP_RE.test(pathname)) return false;
  try {
    return (await countOn(page.locator("input[type=password]"))) > 0;
  } catch (_) {
    return false; // can't tell: a hop-looking address alone is not proof the session died
  }
}

module.exports = {
  NOOP_STEP_TYPES,
  NAVIGATION_STEP_TYPES,
  resolveLaterProofs,
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
  stepFailure,
  detectFault,
  countSurface,
  askAboutBlockingPopup,
  runIfDecision,
  collectOutputs,
  declaredOutputNames,
  flattenAnswer,
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
