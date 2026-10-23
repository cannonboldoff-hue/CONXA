"use strict";
// Recovery cascade seam, extracted from run.js: the Tier 1 deterministic ladder
// and the Tier 2 zero-token mechanisms (a11y re-probe, re-hover, dialog scope),
// each closing through a verified action re-run. LLM fires at Tier 3+ only
// (AGENTS.md Key Invariants) — nothing in this module performs network I/O
// (CI-guarded by check_recovery_purity.js).
//
// Every stage here obeys one rule: it may change WHEN or WHERE it looks, never WHICH element it
// settles for. A stage either retries the recorded selector (after a wait, scroll, dismissal,
// hover or dialog scoping) or clears the same uniqueness gate primary resolution uses. Stages
// that guessed a different element by text affinity and clicked `.first()` ungated used to live
// here and were deleted: a wrong click mutates the page before the agent tier ever sees it, so
// they lowered the ceiling of the tier behind them. Identity guessing now happens only where a
// wrong guess is caught before it acts.
const { classifyException, remedyFor } = require("./recovery");
const { appendRecoveryEvent } = require("./recovery_log");
const { SECONDARY_ACTION_TIMEOUT_MS, CAPTURE_PRESTEP } = require("./run_config");
const { asObject, asArray, isNonIdempotent } = require("./step_utils");
const { rootCandidates } = require("./resolution");
const {
  stepWithSelector,
  walkHoverChain,
} = require("./locators");
const { executeStep } = require("./handlers");
const { verifyStep, hasRequiredAssertion, capturePreStepSignature, signatureChanged } = require("./assertions");
const { dismissKnownOverlay } = require("./dismiss_patterns");
const learnedDismissals = require("./learned_dismissals");
const { evalOn } = require("./page_eval");
const pageScripts = require("./page_scripts");
const { recordOverlayObservation } = require("./overlay_capture");
const { isDecisionDialog } = require("./fork");

const INTERACTIVE_STEP_TYPES = new Set([
  "click", "dblclick", "right_click",
  "type", "fill", "focus", "select", "select_option",
  "set_checkbox", "set_radio", "date_pick",
  "drag_drop", "keyboard_shortcut", "upload",
]);

const DIALOG_CONTAINERS = ['[role="dialog"]', '[role="alertdialog"]', '[aria-modal="true"]', ".modal"];

// ── EXEC-24: the action guard ────────────────────────────────────────────────────────────────
//
// The cascade below is a sequence of RESOLUTION STRATEGIES, but it is also — and this is what
// went unaccounted for — a sequence of ACTIONS. Each stage dispatches a real input event, and
// nothing between two stages ever asked whether the previous one already changed the page. So a
// Submit whose confirmation renders slower than its own poll got clicked again by the next stage.
//
// The fix is the deterministic equivalent of what browser-use and computer-use buy with a model
// call per attempt (re-perceive, then decide): before RE-dispatching a non-idempotent action, ask
// for local evidence that the previous attempt already landed. Two sources, in order of strength:
//
//   1. the step's own post-condition — if every assertion holds, the step already succeeded;
//   2. the pre-step page signature — if the page moved, something happened and we must not
//      guess what, so the ladder stops and the agent tier gets the page as it actually is.
//
// Zero-token by construction: both are DOM arithmetic already computed elsewhere in the runtime.
function createActionGuard(step, { acted = false, signature = null } = {}) {
  return {
    // Only step types where a second dispatch duplicates an effect are guarded at all; a
    // fill/select/hover recovers exactly as aggressively as it always has.
    guarded: isNonIdempotent(step),
    // Whether an action for THIS step may already have been dispatched (the primary attempt
    // threw from inside the action callback, or succeeded and only its verify failed).
    acted,
    // Page signature captured before the step's first action — the "before" of the delta.
    signature,
    // Set once the guard stops the ladder; surfaced on the failure so the agent is told the
    // page may already reflect a recovery attempt.
    blocked: null,
  };
}

// Decide whether a re-dispatch is allowed. Returns "recovered" when the step turns out to have
// already succeeded, "blocked" when acting again would risk doubling an effect, "go" otherwise.
async function guardDecision(page, step, inputs, baseline, guard) {
  if (!guard || !guard.guarded || !guard.acted) return "go";

  const verdict = await verifyStep(page, step, inputs, baseline);
  if (verdict.results.length) {
    // Post-conditions exist and ALL hold → the action landed; the earlier failure was a slow
    // render, not a broken step. Deliberately checks `results`, not `verdict.pass`: assertions
    // currently ship advisory-only, so `pass` stays true even when an advisory one fails.
    if (verdict.results.every(r => r.ok)) return "recovered";
    // A post-condition that does NOT hold is the author's own statement that the outcome did
    // not land — retry, exactly as before. This is the case test_recovery_verify.js pins.
    return "go";
  }

  // No post-condition to settle it. A page that moved after a possible dispatch is the only
  // evidence available, and it is ambiguous by nature — so treat it as "may already have acted"
  // and stop rather than clicking again to find out.
  const now = await capturePreStepSignature(page);
  if (signatureChanged(guard.signature, now)) {
    guard.blocked = "action-may-have-taken-effect";
    return "blocked";
  }
  return "go";
}

// The single choke point where recovery re-runs a step's action. Closing the "recovered but
// unverified" gap lives here: when the step carries a required (enforced) post-condition, a
// successful action re-run is not enough — the post-condition must re-hold before recovery is
// allowed to report success. Steps with no required assertion are unaffected (no new false
// failures on non-consequential steps).
async function recoverWithSelector(page, step, inputs, selector, onSuccess, baseline = null, guard = null) {
  if (!selector) return false;

  // EXEC-24: check BEFORE acting, not only after. Everything below this line may dispatch.
  const decision = await guardDecision(page, step, inputs, baseline, guard);
  if (decision === "blocked") return false;
  if (decision === "recovered") {
    if (onSuccess) onSuccess();
    return true;
  }

  try {
    await executeStep(page, stepWithSelector(step, selector), inputs);
    if (guard) guard.acted = true;
    if (hasRequiredAssertion(step)) {
      const verdict = await verifyStep(page, step, inputs, baseline);
      if (!verdict.pass) return false;
    }
    if (onSuccess) onSuccess();
    return true;
  } catch (err) {
    if (guard && err && err.mayHaveActed) guard.acted = true;
    return false;
  }
}

// Derive an element's accessible name from its recorded fingerprint for a11y recovery.
// Precedence must mirror the compiler's canonical derivation (identity_bundle.py's
// _accessible_name) and resolver.js's fpName.
// placeholder covers label-less inputs (e.g. a search box) that the compiler names from
// their placeholder text — without it here, recovery for exactly those elements sees an
// empty name and bails before ever trying. alt/title cover elements whose only name is an
// attribute (a bare <img>). `label_text` is the nearest <label>/sibling context — for
// content elements (links, buttons, images) it is NOT the element's accessible name and can
// point at a neighbour (e.g. the blueprint link's label_text was mis-captured as "Project"),
// which would make `role=link[name="Project"]` recover the WRONG element. It is therefore
// gated on the form-control tags whose accessible name legitimately comes from their label —
// the same gate identity_bundle.py applies, so compile and recovery cannot disagree.
// Tag OR role: a fingerprint may carry either (the compiler records both, recovery callers
// and older packs sometimes only one), and a control named by its label is a form control
// under either name.
const LABELLED_TAGS = new Set([
  "input", "select", "textarea",
  "textbox", "searchbox", "combobox", "listbox", "spinbutton", "checkbox", "radio",
]);

// ARIA "name from content" roles — mirrors identity_bundle.py's _NAME_FROM_CONTENT_ROLES.
// A combobox/listbox/textbox/searchbox/spinbutton/bare <select> never gets its accessible
// name from its own inner text (concatenated <option> text isn't a name any browser
// computes), so recovery must not treat inner_text as a name candidate for those either —
// it would recover on a name the compiler itself refuses to fabricate.
const NAME_FROM_CONTENT_ROLES = new Set([
  "button", "link", "heading", "cell", "gridcell", "columnheader", "rowheader",
  "checkbox", "radio", "menuitem", "menuitemcheckbox", "menuitemradio",
  "option", "tab", "treeitem", "switch", "tooltip",
]);

function a11yRecoveryName(fingerprint) {
  const fp = asObject(fingerprint);
  const isLabelled = LABELLED_TAGS.has(String(fp.tag || "").toLowerCase())
    || LABELLED_TAGS.has(String(fp.role || "").toLowerCase());
  const labelName = isLabelled ? fp.label_text : "";
  const isNamedFromContent = NAME_FROM_CONTENT_ROLES.has(String(fp.tag || "").toLowerCase())
    || NAME_FROM_CONTENT_ROLES.has(String(fp.role || "").toLowerCase());
  const innerTextName = isNamedFromContent ? fp.inner_text : "";
  // `name` is deliberately excluded — the HTML form-field attribute, never an ARIA
  // accessible-name source (identity_bundle.py's _accessible_name never includes it either).
  return stripKeyboardShortcutTail(String(
    fp.aria_label || fp.alt || fp.title || innerTextName || fp.placeholder || labelName || "",
  ).trim());
}

// Mirrors identity_bundle.py::_strip_keyboard_shortcut_tail. Drive (and similar) append
// "Alt+C then U" to the visible label; Playwright's accessible name does not.
const SHORTCUT_TAIL_RE = /\s+(?:(?:Ctrl|Control|Alt|Shift|Cmd|Command|Meta|Win|Windows|Option)\s*\+\s*)+[A-Za-z0-9](?:\s+then\s+(?:(?:(?:Ctrl|Control|Alt|Shift|Cmd|Command|Meta|Win|Windows|Option)\s*\+\s*)+[A-Za-z0-9]|[A-Za-z0-9]))*\s*$/i;

function stripKeyboardShortcutTail(text) {
  return String(text || "").replace(SHORTCUT_TAIL_RE, "").trim();
}

async function recoverWithA11y(page, step, inputs, slug, stepIndex, tracker, baseline = null, guard = null) {
  const bundle = asObject(step.identity_bundle);
  const fingerprint = asObject(bundle.fingerprint);
  const role = String(fingerprint.role || "").trim();
  const name = a11yRecoveryName(fingerprint);
  if (!name) return false;

  // Re-probe by accessible name, but resolve THROUGH the pure matcher (fingerprint scoring +
  // strict uniqueness gate), never a raw `.first()` click. This is the architectural fix: a11y
  // recovery can no longer pick a wrong-but-name-matching node — a candidate must out-score the
  // recorded fingerprint and clear the uniqueness margin, exactly like primary resolution. We do
  // this by handing the matcher a synthetic bundle of the accessible-name signals while keeping
  // the recorded fingerprint + frame_chain so scoring and boundary context are unchanged.
  const signals = [];
  if (role) signals.push({ engine: "role", selector: `internal:role=${role}[name="${name}"]`, durability: 0.9 });
  signals.push({ engine: "text_based", selector: `internal:text="${name.slice(0, 80)}"`, durability: 0.8 });

  const method = role ? "a11y:role" : "a11y:text";
  const a11yStep = { ...step, identity_bundle: { ...bundle, signals } };
  delete a11yStep._explicit_selector;  // force the PRIMARY (matcher) path, not string mode

  // This stage calls executeStep directly rather than through recoverWithSelector, so it needs
  // the same pre-dispatch guard — it is a dispatch like any other (EXEC-24).
  const decision = await guardDecision(page, step, inputs, baseline, guard);
  if (decision === "blocked") return false;
  if (decision === "recovered") {
    appendRecoveryEvent({ event: "tier2_a11y", slug, step_index: stepIndex, recovery_method: "already-held" });
    return true;
  }

  try {
    await executeStep(page, a11yStep, inputs);
    if (guard) guard.acted = true;
    if (hasRequiredAssertion(step)) {
      const verdict = await verifyStep(page, step, inputs, baseline);
      if (!verdict.pass) return false;
    }
    appendRecoveryEvent({ event: "tier2_a11y", slug, step_index: stepIndex, recovery_method: method });
    tracker.emit("tier_ok", { si: stepIndex, tier: "tier2_a11y", sel: method });
    return true;
  } catch (err) {
    if (guard && err && err.mayHaveActed) guard.acted = true;
    return false;
  }
}

async function recoverWithDialogScope(page, step, inputs, slug, stepIndex, primarySelector, tracker, baseline = null, guard = null) {
  if (step.type !== "click" || !primarySelector) return false;

  for (const container of DIALOG_CONTAINERS) {
    const selector = `${container} ${primarySelector}`;
    const recovered = await recoverWithSelector(page, step, inputs, selector, () => {
      appendRecoveryEvent({ event: "layer_recovered", layer: 3, slug, step_index: stepIndex, mode: "dialog" });
      tracker.emit("rec_ok", { si: stepIndex, sc: "selector" });
    }, baseline, guard);
    if (recovered) return true;
    if (guard && guard.blocked) return false;
  }

  return false;
}

// Layer 1 deterministic ladder: apply a single targeted remedy keyed off the exception class,
// then retry the primary selector once. Zero-token. Returns true if the retry succeeded.
async function layer1Ladder(page, step, inputs, slug, stepIndex, primarySelector, primaryErr, baseline = null, guard = null, runCtx = null) {
  const klass = classifyException(primaryErr);
  const remedy = remedyFor(klass);
  if (remedy === "descend-layer2") {
    // A verify-fail means the action itself already ran without throwing — the DOM is exactly
    // as it was when the post-condition check failed. Retrying the same primary selector here
    // would just re-run the identical action and re-fail the same check. Skip the single-remedy
    // L1 retry entirely and let the cascade fall through to L2's resolution-changing mechanisms
    // (a11y re-probe, re-hover, dialog scope) below, each of which re-verifies the
    // post-condition via recoverWithSelector before reporting success.
    return false;
  }
  try {
    if (remedy === "scroll-into-view" && primarySelector) {
      // Scroll within the step's own resolved frame, not blindly the top-level page — a
      // selector match at top level (if any) is a different element than the one that's
      // actually out of view inside the iframe. If the frame chain itself can't be resolved
      // there's nothing sensible to scroll; fall through and let the retry below surface the
      // real failure instead of scrolling the wrong document.
      const scrollRoots = await rootCandidates(page, step, inputs);
      if (scrollRoots.length) {
        await scrollRoots[0].locator(primarySelector).first().scrollIntoViewIfNeeded({ timeout: SECONDARY_ACTION_TIMEOUT_MS });
      }
    } else if (remedy === "dismiss-overlay") {
      // A popup that is asking the person to CHOOSE (Replace / Keep both / Cancel, Discard / Keep
      // editing ...) is not in the way of the workflow, it is part of it -- and Escape, a generic
      // close button, or a learned dismissal would each quietly pick "cancel" on their behalf.
      // Refuse all three, say why, and leave the step failing as an INTERCEPTED click so it
      // escalates (Tier B / a person) instead of acting. Consent banners and plain close-me popups
      // are not decision dialogs and keep the zero-token known-pattern dismissal below.
      try {
        const live = await evalOn(page, pageScripts.dialogProbe);
        if (live && isDecisionDialog(live)) {
          if (primaryErr) {
            primaryErr.unknownOverlay = true;
            primaryErr.decisionDialog = { title: live.title, buttons: (live.controls || []).map((c) => c.name || c.text).filter(Boolean) };
          }
          appendRecoveryEvent({ event: "tier1_dismiss_refused_decision_dialog", slug, step_index: stepIndex,
            title: live.title, buttons: (live.controls || []).map((c) => c.name || c.text).filter(Boolean) });
          return false;
        }
      } catch (_) {}
      // Historical remedy first, unchanged: Escape closes OS-style dialogs.
      await page.keyboard.press("Escape").catch(() => {});
      // EXEC-5 known-pattern ladder: a never-recorded popup is usually built from one of a
      // few ubiquitous consent toolkits — try their accept/close affordances (plus anything
      // previously learned on THIS host) before giving up on Tier 1. Zero-token by
      // construction; reactive only (we got here because something actually intercepted).
      let dismissHit = null;
      try {
        const learned = learnedDismissals.selectorsFor(page.url());
        dismissHit = await dismissKnownOverlay(page, { learned });
        if (dismissHit) {
          // Record every clicked candidate: the winning tail matters, and refreshing each
          // keeps the replay order stable for the next interception on this host.
          for (const selector of dismissHit.selectors) learnedDismissals.record(page.url(), selector);
          appendRecoveryEvent({ event: "tier1_dismiss_pattern", slug,
            step_index: stepIndex, pattern: dismissHit.selectors.join(" | "), source: dismissHit.source });
        } else if (primaryErr) {
          // EXEC-30: an INTERCEPTED failure that no known pattern could clear — flag it so the
          // agent tier's failure payload can say "this looks like an unrecognized overlay"
          // instead of a bare element-not-found, and offer the dismiss override.
          primaryErr.unknownOverlay = true;
        }
      } catch (_) {}
      // BUILD-26 stage (f): capture what covered the target, here — the one point the runtime
      // already knows something intercepted a click (an INTERCEPTED classification) — and on
      // the recovered-and-passed path too, not only on eventual failure. `_writeStudioEvidence`
      // (failure_response.js) only fires when the whole step ultimately fails, so this is the
      // only place a passing run's overlay ever gets written.
      if (runCtx && runCtx.dataDir && runCtx.runId) {
        try {
          const probe = await evalOn(page, pageScripts.overlayProbe);
          if (probe) {
            recordOverlayObservation({
              dataDir: runCtx.dataDir, runId: runCtx.runId, slug, stepIndex,
              probe, dismissed: !!dismissHit,
            });
          }
        } catch (_) {}
      }
    } else if (remedy === "wait-stable" || remedy === "wait-enabled") {
      await page.waitForTimeout(300);
    } else if (remedy === "wait-navigation") {
      // The timeout carried Playwright's in-flight-navigation signature — give the page a
      // real chance to finish loading before retrying, instead of L2's fixed 250ms wait.
      await page.waitForLoadState("domcontentloaded", { timeout: SECONDARY_ACTION_TIMEOUT_MS }).catch(() => {});
    } else {
      return false; // re-resolve / retry-cascade handled by the broader cascade below
    }
  } catch (_) {
    return false;
  }
  const ok = await recoverWithSelector(page, step, inputs, primarySelector, () => {
    appendRecoveryEvent({ event: "layer1_ladder", slug, step_index: stepIndex, remedy });
  }, baseline, guard);
  return ok ? remedy : false;
}

async function recoverStep(page, step, inputs, slug, stepIndex, primarySelector, tracker, primaryErr = null, cancelCheck = null, baseline = null, guard = null, dialogQueue = null, runCtx = null) {
  // EXEC-29 — every stage below eventually touches the page (an `.evaluate()` a11y probe, a
  // fresh `resolveStep`, a plain `waitForTimeout`), and a renderer with an open native dialog
  // does not run any of that: Chromium blocks the whole tab, not just the action that triggered
  // the dialog. Re-resolving here is meaningless (there is nothing to observe) and unbounded
  // (see the individual call sites' own deadline seam, page_eval.js) — so recovery must never
  // even start while a dialog is pending. `verifyStep` already carries this same short-circuit
  // (`assertions.js`, channel `dialog_pending`); this is its recovery-side twin.
  if (dialogQueue && dialogQueue.length) {
    const pending = dialogQueue[0];
    let dialogType = "unknown", dialogMessage = "";
    try { dialogType = pending.type(); dialogMessage = pending.message(); } catch (_) {}
    if (primaryErr) {
      primaryErr.dialogPending = true;
      primaryErr.dialogPendingType = dialogType;
      primaryErr.dialogPendingMessage = dialogMessage;
    }
    appendRecoveryEvent({ event: "recovery_skipped_dialog_pending", slug, step_index: stepIndex, dialog_type: dialogType });
    tracker.emit("rec_halt", { si: stepIndex, why: "dialog_pending" });
    return false;
  }
  // Each Tier 1/2 stage is individually time-bounded, but the cascade as a whole can run for tens
  // of seconds. If the MCP client cancels mid-recovery (e.g. its request timed out), bail at the
  // next stage boundary instead of grinding through every remaining stage on a doomed run.
  const bail = () => { if (cancelCheck && cancelCheck()) throw Object.assign(new Error("Execution cancelled"), { cancelled: true }); };

  // EXEC-24 — a guard is always present so no stage has to null-check it. Callers that know
  // whether the primary attempt already dispatched (run.js) pass a seeded one.
  const g = guard || createActionGuard(step);
  // Once a stage refuses to re-dispatch, no later stage may act either: they differ only in HOW
  // they find an element, and the reason to stop is about the page, not the strategy.
  const stopped = () => !!g.blocked;
  // Single exit for "recovery did not heal this step". A destructive step logs its deliberate
  // no-guess stop HERE rather than before Layer 2, because it now runs the same-element half of
  // Layer 2 first — the halt is the outcome, not the entry condition. Deliberately does not set
  // g.blocked: that would make stopped() abort the very stages this change exists to allow.
  const fail = () => {
    if (step.destructive === true) {
      appendRecoveryEvent({ event: "destructive_recovery_halted", slug, step_index: stepIndex });
      tracker.emit("rec_halt", { si: stepIndex, why: "destructive" });
    }
    return false;
  };

  // Layer 1 — deterministic exception ladder (targeted single remedy).
  // (Alternate-signal recovery is inherent: resolveStep already walks all bundle signals in
  // durability order, so there is no separate legacy compiled-selector tier.)
  const l1 = await layer1Ladder(page, step, inputs, slug, stepIndex, primarySelector, primaryErr, baseline, g, runCtx);
  if (l1) {
    tracker.emit("tier_ok", { si: stepIndex, tier: "layer1", sel: l1 });
    return { tier: "L1", method: l1 };
  }
  if (stopped()) return fail();

  // EXEC-24 / the failure model's "no guess on irreversible actions" rule. The line is
  // RE-IDENTIFICATION, not tier: a destructive step (pay / delete / submit — flagged at compile
  // time) may retry the SAME element under different timing or a narrower scope, but must never
  // be re-resolved to a different one. Acting on the wrong row is the failure this exists to
  // prevent; waiting 250ms longer is not.
  //
  // This used to halt the whole cascade here, which was correct when the stages below still
  // included a fallback-selector walk and a fuzzy text match — both picked a different element.
  // Those were deleted (see the dialog-scope comment below), and what remains splits cleanly:
  // recoverWithA11y re-probes by accessible name and CAN land on a different node, so it stays
  // blocked; transient / re-hover / dialog-scope all re-try `primarySelector` itself and cannot,
  // which makes them identical in kind to the L1 retry a destructive step already gets. Blocking
  // those while allowing L1 was an accident of where the gate sat, not a safety property.
  //
  // Failing the step is still terminal for the agent: run.js marks any unhealed destructive step
  // `destructiveHalt`, which keeps it out of server.js's recovery park — a step_overrides
  // candidate pick is re-identification by another name.
  if (step.destructive !== true) {
    bail();
    if (await recoverWithA11y(page, step, inputs, slug, stepIndex, tracker, baseline, g)) return { tier: "L2", method: "a11y" };
    if (stopped()) return fail();
  } else {
    appendRecoveryEvent({ event: "destructive_reidentify_skipped", slug, step_index: stepIndex });
    tracker.emit("rec_skip", { si: stepIndex, why: "destructive" });
  }

  bail();
  await page.waitForTimeout(250).catch(() => {}); // page/context/browser may have closed mid-recovery
  if (await recoverWithSelector(page, step, inputs, primarySelector, () => {
    appendRecoveryEvent({ event: "transient_recovered", slug, step_index: stepIndex });
  }, baseline, g)) return { tier: "L2", method: "transient" };
  if (stopped()) return fail();

  // Layer 2 — re-hover-then-retry (menu reveals), then dialog scoping.
  if (asArray(asObject(step.handler_hints).hover_chain).length) {
    bail();
    await walkHoverChain(page, step, inputs);
    if (await recoverWithSelector(page, step, inputs, primarySelector, () => {
      appendRecoveryEvent({ event: "layer2_rehover", slug, step_index: stepIndex });
    }, baseline, g)) return { tier: "L2", method: "rehover" };
    if (stopped()) return fail();
  }

  // Dialog scope is the last deterministic stage, and it is deliberately the ONLY remaining one
  // that changes where we look: it re-searches the SAME primary selector inside an open dialog
  // container, so it can never land on a different element. The two stages that used to follow it
  // (a fallback-selector walk and a fuzzy text match) were deleted — both picked a *different*
  // element by text affinity and clicked `.first()` with no uniqueness gate, which is the one
  // thing recovery must not do. Their upside is covered by the agent tier, which reasons over a
  // ranked digest and has its pick re-verified; their downside was unique to them, because a
  // wrong click mutates the page before the agent tier ever sees it, and no later tier can undo
  // that. Identity guessing belongs where a wrong guess is caught, not where it is dispatched.
  bail();
  return (await recoverWithDialogScope(page, step, inputs, slug, stepIndex, primarySelector, tracker, baseline, g))
    ? { tier: "L2", method: "dialog" }
    : fail();
}

async function maybeCapturePreStep(page, step) {
  if (!INTERACTIVE_STEP_TYPES.has(step.type) || !CAPTURE_PRESTEP) return null;
  return page.screenshot({ type: "jpeg", quality: 70, timeout: 1000 }).catch(() => null);
}

module.exports = {
  createActionGuard,
  guardDecision,
  recoverWithSelector,
  a11yRecoveryName,
  recoverWithA11y,
  recoverWithDialogScope,
  layer1Ladder,
  recoverStep,
  maybeCapturePreStep,
};
