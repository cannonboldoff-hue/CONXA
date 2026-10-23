"use strict";
/**
 * failure_response.js — assembly of the MCP failure response payloads, extracted
 * from server.js so the LLM prompt-engineering text (the agent recovery request)
 * is unit-testable without loading the whole server.
 *
 * Two shapes, decided by the recovery-tier ceiling (CONXA_MAX_RECOVERY_TIER):
 *   • ceiling 2 (Build Studio): a concise, deterministic failure. No agent
 *     handoff, no screenshots — the compiled pack is judged on its T1/T2 merits.
 *   • ceiling ≥ 3: the ARMED agent round — a browser-use-style ranked, indexed
 *     candidate digest of the live page, plus CUA-style visual grounding (the
 *     failure screenshot, the pre-step screenshot, and the recording-time
 *     reference image when it is actually present on disk). The client nominates
 *     a candidate_index (reflection contract) or a selector, and the runtime
 *     re-verifies that pick against a uniqueness gate before acting.
 *
 * EVERY round is armed. This replaced a split where round one was text-only and
 * screenshots were withheld for a separate later round — a token-cost lever that
 * inverted the priority. The first round is the likeliest to succeed, so it is
 * the one that should be best armed; withholding saved tokens on the attempts
 * that were going to work anyway and cost a whole wasted round-trip (the entire
 * digest re-sent) on the ones that actually needed the pictures. Ceiling 3 and 4
 * now behave identically; ceiling 2 is unchanged.
 *
 * Everything that used to be a server.js module-level binding arrives via `deps`,
 * evaluated at call time (server.js binds some of them late inside its SDK try block).
 */
const fs   = require("fs");
const path = require("path");
const pageScripts = require("./page_scripts");
const { evalOn, EVAL_TIMED_OUT } = require("./page_eval");
const { capturePageFingerprint, parkKey } = require("./recovery_park");
const recoveryStage = require("./recovery_stage");
const { buildIndexedDigest } = require("./candidate_digest");
const artifactStore = require("./artifact_store");
const recoveryLog = require("./recovery_log");

// Compact, recovery-relevant description of the step the cascade could not resolve.
// Drives semantic matching: the agent matches THIS intent against the live DOM.
function stepRecoveryContext(err) {
  const step = err && err.failedStep ? err.failedStep : null;
  if (!step) return null;
  const fp = (step.identity_bundle && step.identity_bundle.fingerprint) || {};

  // Anchors from recovery.json are human-readable descriptions written at compile time and
  // stable across UI drift — unlike compiled fingerprint fields (inner_text, data_testid) which
  // may be stale. Prefer the highest-priority anchor as the element label; fall back to the
  // fingerprint when anchors are absent.
  const anchors = Array.isArray(step.anchors)
    ? step.anchors
        .filter(a => a && typeof a.text === "string" && a.text.trim())
        .sort((a, b) => (b.priority || 0) - (a.priority || 0))
        .map(a => a.text.trim())
    : [];

  const ctx = {
    action: step.type || "",
    intent: step._intent || step.label || "",
    target: {
      role:        fp.role || undefined,
      name:        fp.aria_label || fp.name || undefined,
      text:        anchors[0] || fp.inner_text || undefined,
      data_testid: fp.data_testid || undefined,
    },
  };
  if (anchors.length) ctx.anchors = anchors;
  if (step.value && typeof step.value === "string" && step.value.length < 80) ctx.value = step.value;
  // Tell the agent explicitly when the target lives inside a nested iframe — otherwise it has no
  // way to know the "Interactive elements NOW" list (top-level only, unless a frame-scoped
  // inventory was also gathered) might not be the whole picture.
  const frameChain = Array.isArray(step.identity_bundle && step.identity_bundle.frame_chain)
    ? step.identity_bundle.frame_chain
    : [];
  if (frameChain.length) ctx.frame = { depth: frameChain.length };
  // Strip empty target fields so the agent sees only positive identity signals.
  ctx.target = Object.fromEntries(Object.entries(ctx.target).filter(([, v]) => v));
  if (!Object.keys(ctx.target).length) delete ctx.target;
  return ctx;
}

// Recorded-target identity signals for RANKING the live inventory against
// (candidate_digest.buildIndexedDigest). Wider than stepRecoveryContext's
// display shape: also carries the fingerprint tag and raw inner_text so tag
// affinity and text affinity can score independently of display truncation.
function digestTargetContext(err) {
  const step = err && err.failedStep ? err.failedStep : null;
  if (!step) return null;
  const fp = (step.identity_bundle && step.identity_bundle.fingerprint) || {};
  const anchors = Array.isArray(step.anchors)
    ? step.anchors.map(a => (a && typeof a.text === "string" ? a.text.trim() : "")).filter(Boolean)
    : [];
  return {
    tag:         fp.tag || undefined,
    role:        fp.role || undefined,
    text:        fp.inner_text || undefined,
    name:        fp.aria_label || fp.name || undefined,
    data_testid: fp.data_testid || undefined,
    anchors,
  };
}

// The compiled expected-post-condition for the failed step — lets the agent tell "element not
// found" apart from "action ran but produced the wrong outcome", and know what success looks
// like once its recovery attempt resumes. `stepAssertions` and `evaluateAssertion` iterate the
// same array in the same order (run.js `verifyStep`), so `results[i]` pairs with `defs[i]`.
function expectedStateBlock(err, stepAssertions) {
  if (!err.failedStep) return null;
  const defs = stepAssertions(err.failedStep);
  if (!defs.length) return null;
  const results = Array.isArray(err.verifyResults) ? err.verifyResults : null;
  const lines = defs.map((a, i) => {
    const r = results && results[i];
    const parts = [
      a.type || "?",
      a.target ? `target=${a.target}` : null,
      a.expected !== undefined ? `expected=${JSON.stringify(a.expected)}` : null,
      a.required === false ? "advisory" : "required",
      r ? (r.ok ? "held" : "FAILED") : null,
    ];
    return "  - " + parts.filter(Boolean).join(", ");
  });
  const verdict = err.verifyFail
    ? "The action ran without throwing, but its expected post-condition did not hold:"
    : "This step's expected post-condition (what should hold once recovery succeeds):";
  return `${verdict}\n${lines.join("\n")}`;
}

// A compact trace of what already executed, so the agent can reason about how the current page
// state was reached instead of guessing from a single frame.
function executedStepsBreadcrumb(steps, failedAt) {
  if (!Array.isArray(steps) || typeof failedAt !== "number" || failedAt <= 0) return null;
  const lines = [];
  for (let i = 0; i < failedAt && i < steps.length; i++) {
    const s = steps[i];
    if (!s) continue;
    const intent = String(s._intent || s.label || s.type || "").slice(0, 80);
    lines.push(`  ${i}: ${s.type || "?"} — ${intent}`);
  }
  return lines.length ? `Executed steps (leading context):\n${lines.join("\n")}` : null;
}

// Gather the ground-truth interactive-element inventory AFTER the cascade: T1/T2 remedies
// (dismiss-overlay, scroll, re-hover, ...) change the page, so the state the agent reasons
// about must be captured fresh. Merges a frame-scoped inventory when the step targets an
// iframe — document.querySelectorAll cannot see into frames, and a "ground truth" that
// silently omits the entire frame would be worse than none.
//
// EXEC-30: also runs the live overlay probe and merges its controls tagged `in_overlay`.
// domInventory() truncates at 50 entries in DOM ORDER, so a modal appended late to <body> can
// be cut out of the plain inventory entirely — the probe answers "is something covering the
// page right now" independent of that ordering, and catches the case where an overlay HIDES
// the target rather than intercepting a click (never an INTERCEPTED classification at all).
// Returns { inventory, overlay } — `overlay` is the probe's container descriptor, or null.
async function gatherInventory(page, err, deps) {
  let inventory = null;
  // EXEC-29 Guard A: this failure-response path is exactly where a blocked renderer is most
  // likely — it runs right after the primary action already timed out — so an unbounded
  // page.evaluate() here previously had no protection at all.
  try {
    const result = await evalOn(page, pageScripts.domInventory);
    inventory = result === EVAL_TIMED_OUT ? null : result;
  } catch (_) {}

  const failedStep = err && err.failedStep;
  if (failedStep) {
    try {
      const frameEntries = await deps.frameScopedInventory(page, failedStep, {});
      if (Array.isArray(frameEntries) && frameEntries.length) {
        inventory = [
          ...(Array.isArray(inventory) ? inventory : []),
          ...frameEntries.map(e => ({ ...e, in_frame: true })),
        ];
      }
    } catch (_) {}
  }

  let overlay = null;
  try {
    const probed = await evalOn(page, pageScripts.overlayProbe);
    if (probed && probed !== EVAL_TIMED_OUT && probed.container) {
      overlay = probed.container;
      inventory = [
        ...(Array.isArray(inventory) ? inventory : []),
        ...(Array.isArray(probed.controls) ? probed.controls.map(e => ({ ...e, in_overlay: true })) : []),
      ];
    }
  } catch (_) {}

  return { inventory: Array.isArray(inventory) ? inventory : null, overlay };
}

// EXEC-30 — say the overlay is there, and how to clear it. Only emitted when the live probe
// actually found one. Distinct from frameNotFoundNoteText/overrideNoteText but same shape:
// appended into `notes`.
function overlayNoteText(overlay, resumeKey) {
  if (!overlay) return "";
  const label = overlay.text ? ` ("${overlay.text.slice(0, 80)}")` : "";
  return `\n\nNote: something is currently covering the page${label} — rows tagged [overlay] in ` +
    `the ranked element list below belong to it. If this looks like an incidental popup (a promo, ` +
    `a consent banner, a permission prompt) rather than something the recorded workflow expects, ` +
    `you may dismiss it before retrying: add "dismiss": { "candidate_index": <index> } (or ` +
    `{ "selector": "…" }, or { "escape": true }) inside step_overrides["${resumeKey}"], alongside ` +
    `or instead of a target override. Only nominate a close/cancel/skip/dismiss control — never ` +
    `accept/confirm/agree/submit/delete/pay/subscribe or any other commit-style control; the ` +
    `runtime refuses those regardless. If the overlay is asking you to commit to something ` +
    `(confirm, accept, pay, delete) rather than merely blocking the view, it is not incidental — ` +
    `do not dismiss it, stop and tell the user instead. A popup that offers several buttons to ` +
    `choose between (for example Replace / Keep both / Cancel) is always such a decision: the ` +
    `runtime will not dismiss it even if you nominate it, so ask the user which button to press.`;
}

// EXEC-30 — the PREVIOUS round's dismiss nomination was refused. Tell the agent why, the same
// way overrideNoteText explains a rejected target pick, so it doesn't just repeat the mistake.
function dismissRejectedNoteText(err) {
  const reason = err && err.failedStep && err.failedStep._dismiss_rejected;
  if (!reason) return "";
  if (reason === "no-match") {
    return `\n\nYour previous dismiss nomination matched no element on the current page — the ` +
      `overlay may have already closed, or the selector/index was stale. Check the current ` +
      `[overlay] rows before nominating again.`;
  }
  if (reason === "unsafe-label") {
    return `\n\nYour previous dismiss nomination was refused: its label reads as a commit action ` +
      `(accept/confirm/submit/delete/etc.), not an incidental dismissal, so the runtime would not ` +
      `click it. Only nominate a close/cancel/skip-style control.`;
  }
  return `\n\nYour previous dismiss nomination could not be applied (${reason}).`;
}

// Secondary, and only when it actually differs from the current one: the inventory at the
// exact moment of failure, before the T1/T2 cascade ran. A dropdown or dialog listed here may
// have since closed — it must never be mistaken for the state to act on now.
function earlySnapshotDiffers(err, currentInventory) {
  const earlyInventory = Array.isArray(err.earlyDomSnapshot) ? err.earlyDomSnapshot : null;
  return !!earlyInventory
    && JSON.stringify(earlyInventory) !== JSON.stringify(currentInventory);
}

// When the previous resume's override failed our uniqueness gate (run.js validateOverrideSelector),
// tell the agent exactly why and what it actually matched, instead of just re-describing the
// original failure as if nothing had been tried.
function overrideNoteText(err) {
  if (!err.overrideValidationFailed) return "";
  if (err.overrideReason === "frame-not-found") {
    return `\n\nYour previous recovery pick could not even be tried: the frame/iframe this ` +
      `element is supposed to live inside could not be located on the current page (it may not ` +
      `have opened, or its identity changed). Picking a different element will not help — first ` +
      `confirm whether the panel/dialog that should contain it is actually open.`;
  }
  const matched = Array.isArray(err.overrideCandidates) && err.overrideCandidates.length
    ? ` Candidates it matched: ${JSON.stringify(err.overrideCandidates)}.`
    : "";
  return `\n\nYour previous recovery pick (${err.overrideReason === "no-match"
      ? "an explicit selector"
      : "candidate_index or selector"}) ${err.overrideReason === "no-match"
      ? "matched no element on the current page"
      : "matched multiple elements with no clear winner"}.` +
    matched +
    ` Pick a different index from the ranked list below.`;
}

// Distinct from a plain element-not-found: the step's target lives inside a frame/iframe, and
// that frame itself could not be located this time (not just the element inside it) — e.g. the
// panel never opened, or the iframe's identifying attribute changed on reattach. Proposing a
// new element selector cannot fix this; the agent needs to know the failure is one level up.
function frameNotFoundNoteText(err) {
  if (!err.frameNotFound) return "";
  return `\n\nNote: this step's target lives inside a frame/iframe, and that containing frame could ` +
    `not be located on the current page at all (not just the element inside it) — it may not ` +
    `have opened yet, may have closed, or its identifying attributes may have changed. The ` +
    `ranked element list below will not show anything from inside that frame. Check the ` +
    `screenshot for whether the expected panel/dialog is visible; if it never opened, the fix ` +
    `is likely earlier in the sequence (the step that should have opened it), not a new ` +
    `element for this step.`;
}

// PROD-3 — the failure model's "no guess on irreversible actions" rule: a destructive step that
// exhausted Layer 1, or any step whose bound record could not be uniquely re-located, never
// reaches the armed round's candidate digest below. Offering a ranked "pick a different element" list
// here would invite exactly the wrong-row guess the halt exists to prevent — this is a deliberate
// stop, not exhausted recovery, so it reads and behaves like one (see buildFailureResponse's
// !agentRecoveryEnabled terminal branch, which this mirrors).
function haltReasonNoteText(err) {
  if (err.destructiveHalt) {
    return `\n\nRecovery stopped deliberately: this step is flagged destructive/irreversible ` +
      `(delete, submit, pay, or similar), and the recorded target could not be re-found after ` +
      `the deterministic remedies (scroll/wait/dismiss-overlay) and one verified retry. This ` +
      `step will never fall through to "find something close" — acting on a different-looking ` +
      `element here risks acting on the wrong record. Fix the root cause (page structure, ` +
      `permissions, or the recorded selector) and re-run; do not attempt a candidate override.`;
  }
  if (err.entityNotFound) {
    return `\n\nRecovery stopped deliberately: this step is bound to a specific record (a row ` +
      `identified by recorded or run-input text), and that record could not be uniquely located ` +
      `on the current page — it may be missing, filtered out, or the list changed. Acting on a ` +
      `different row would mean acting on the wrong record. Verify the record still exists, ` +
      `then re-run; do not attempt a candidate override.`;
  }
  return "";
}

// Shared reasoning context for both tiers: intent, post-condition, trace, geometry.
// Where the target sat on the page when the workflow was RECORDED — parent element, the
// siblings around it, its index among them, and the enclosing form.
//
// Everything else in the payload describes the page as it is now. That is enough to answer
// "what is here?", but drift is a *change*, and a live inventory cannot express one. This block
// is the other half of the comparison: "it used to sit in the toolbar, third child, next to
// Export" is what turns an unfindable button into a moved one. Compiled at build time from
// signals the recorder already captured, so it costs nothing at runtime and works offline.
function recordedContextBlock(err) {
  const step = err && err.failedStep ? err.failedStep : null;
  const ctx = step && step._recorded_context;
  if (!ctx || typeof ctx !== "object" || !Object.keys(ctx).length) return null;
  return `Where this element sat AT RECORDING TIME (structure may since have changed — compare ` +
    `against the live list above, do not assume it still holds):\n${JSON.stringify(ctx)}`;
}

// A plain-English description of the target, written by a vision LLM at compile time from 5
// time-offset screenshots around the recorded action. Complements recordedContextBlock's
// structural "where it sat" with a visual/positional description in the same register a human
// would use — useful when drift is severe enough that neither text nor DOM structure still
// matches but a human-style description still would.
function anchorSentenceBlock(err) {
  const step = err && err.failedStep ? err.failedStep : null;
  const sentence = step && typeof step._anchor_sentence === "string" ? step._anchor_sentence.trim() : "";
  if (!sentence) return null;
  return `How this element was described when recorded: "${sentence}"`;
}

// BUILD-25 stage e: a workflow-position prior from the compiler's
// second-opinion pass (compiler/second_opinion.py's label_phase kind) — "this
// step is in the login phase" is a strong hint for the agent tier about what
// kind of page it should expect to see. Absent on any step the pass never ran
// on or didn't label, which is an ordinary state, not a failure.
function phaseHintBlock(err) {
  const step = err && err.failedStep ? err.failedStep : null;
  const phase = step && typeof step._phase === "string" ? step._phase.trim() : "";
  if (!phase) return null;
  return `This step is labeled as part of the workflow's "${phase}" phase.`;
}

// Locate the recording-time reference image for the failed step, if this machine actually has it.
//
// Two lookups, in order of trustworthiness:
//
//   1. The step's own `visual_ref` (compiled into recovery.json) resolved through the
//      content-addressed artifact store. This is the real path: it survives step renumbering,
//      because the store is keyed by bytes rather than by `Image_<n>` position.
//   2. The legacy in-pack `visuals/` folder. Packs published before artifacts synced separately
//      keep their images inside the skill directory, and the Studio sandbox stages them there.
//
// Returns null when neither has it — an ordinary state, not a failure. `visuals/` genuinely did
// not reach customer machines for a long time, and the header adapts rather than describing an
// image that was never attached.
function _resolveVisualRef(err, resolvedEntry, failedAt, deps) {
  const visualRef = err && err.failedStep && err.failedStep._visual_ref;
  if (visualRef && deps && deps.skillPacksDir) {
    const stored = artifactStore.resolveByPath(deps.skillPacksDir, resolvedEntry.skillDir, visualRef);
    if (stored) return stored;
  }

  const inPack = visualRef
    ? [path.join(resolvedEntry.skillDir, visualRef)]
    : [".jpg", ".jpeg", ".png"].map(ext =>
        path.join(resolvedEntry.skillDir, "visuals", `Image_${failedAt + 1}${ext}`));
  for (const candidate of inPack) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

// BUILD-26 stage (a1) — the Build Studio ceiling branch below returns four lines of text and
// discards everything else this module already knows how to compute: the live inventory, the
// overlay probe, the recovery trail, the anchor sentence. Human Review's copilot never runs
// above ceiling 2 (Studio forces CONXA_MAX_RECOVERY_TIER=2), so without this write there is
// nothing on disk for it to diagnose from. Relocates existing data; captures nothing new.
// Never throws — evidence capture must not turn a clean failure into a worse one.
async function _writeStudioEvidence(page, err, resolvedEntry, steps, failedAt, stepNo, deps) {
  const dataDir = deps && deps.dataDir;
  const runId = deps && deps.runId;
  if (!dataDir || !runId) return; // no run workspace to file this under

  try {
    const dir = path.join(dataDir, "runs", runId, "_evidence");
    fs.mkdirSync(dir, { recursive: true });

    // Live capture at the failure moment — NOT err.preShot, which is captured BEFORE the action
    // (run.js) and is null outright for non-interactive step types (navigate/wait/verify), where
    // the page-after-the-fact is exactly the evidence a verify failure needs.
    const failShot = await page.screenshot({ type: "jpeg", quality: 80 }).catch(() => null);
    if (failShot) fs.writeFileSync(path.join(dir, "failure.jpg"), failShot);
    if (err.preShot) fs.writeFileSync(path.join(dir, "pre_step.jpg"), err.preShot);

    const viewport = (() => { try { return page.viewportSize(); } catch (_) { return null; } })();
    let scrollY = null;
    try { const sy = await evalOn(page, pageScripts.getScrollY); scrollY = sy === EVAL_TIMED_OUT ? null : sy; } catch (_) {}

    const { inventory, overlay } = await gatherInventory(page, err, deps);
    const slug = resolvedEntry && resolvedEntry.slug;

    // BUILD-31: failed_at alone is a raw index into the step list as it existed at THIS run —
    // editor/evidence.py re-resolves it against the CURRENT document's step list on every read,
    // so an insert/delete before this index would otherwise silently mislabel the wrong step.
    // The runtime has no concept of the compiler's step_key (and reimplementing its hashing here
    // would drift from that source of truth), but identity_bundle.stable_hash IS the base every
    // step_key is built from (compiler/step_key.py) and already rides on `steps` when present —
    // write it so evidence.py can cross-check the step it resolves to before trusting it.
    const failedStep = Array.isArray(steps) ? steps[failedAt] : null;
    const failedStableHash = (failedStep && failedStep.identity_bundle && failedStep.identity_bundle.stable_hash) || null;

    const evidence = {
      run_id: runId,
      slug,
      ts: new Date().toISOString(),
      failed_at: failedAt,
      failed_stable_hash: failedStableHash,
      step_no: stepNo,
      message: err.message,
      raw_message: err.rawMessage || err.message,
      page_url: page.url(),
      viewport,
      scroll_y: scrollY,
      step_context: stepRecoveryContext(err),
      recorded_context: err.failedStep ? err.failedStep._recorded_context || null : null,
      anchor_sentence: err.failedStep ? err.failedStep._anchor_sentence || null : null,
      phase: err.failedStep ? err.failedStep._phase || null : null,
      expected_state: expectedStateBlock(err, deps.stepAssertions),
      breadcrumb: executedStepsBreadcrumb(steps, failedAt),
      inventory,
      overlay,
      recovery_trail: recoveryLog.readRecentEvents(slug, deps.runStartTs),
      // BUILD-26 stage (e): the EXEC-24 guard flags on `err` — without these the copilot cannot
      // tell "the click may already have fired" from "the element was never found", which is the
      // single most important thing to know before proposing a retest.
      action_may_have_taken_effect: !!err.actionMayHaveTakenEffect,
      recovery_halt_reason: err.recoveryHaltReason || null,
      destructive_halt: !!err.destructiveHalt,
      verify_fail: !!err.verifyFail,
    };
    fs.writeFileSync(path.join(dir, "evidence.json"), JSON.stringify(evidence));
  } catch (_) {
    // Same contract as recovery_log.js: a logging failure must never break the response.
  }
}

function buildContextSections(err, steps, failedAt, viewport, scrollY, stepAssertions) {
  const out = [];
  const intent = stepRecoveryContext(err);
  if (intent) out.push(`Failed step intent: ${JSON.stringify(intent)}`);
  const recorded = recordedContextBlock(err);
  if (recorded) out.push(recorded);
  const anchorSentence = anchorSentenceBlock(err);
  if (anchorSentence) out.push(anchorSentence);
  const phaseHint = phaseHintBlock(err);
  if (phaseHint) out.push(phaseHint);
  const expected = expectedStateBlock(err, stepAssertions);
  if (expected) out.push(expected);
  const breadcrumb = executedStepsBreadcrumb(steps, failedAt);
  if (breadcrumb) out.push(breadcrumb);
  if (viewport) out.push(`viewport: ${JSON.stringify(viewport)}, scrollY: ${scrollY}`);
  return out.join("\n\n");
}

async function buildFailureResponse(page, err, resolvedEntry, runTracker, steps, deps) {
  const {
    agentRecoveryEnabled,
    maxRecoveryTier,
    appendRecoveryEvent,
    stepAssertions,
    frameScopedInventory,
  } = deps;

  const url      = page.url();
  const failedAt = typeof err.failedAt === "number" ? err.failedAt : null;
  const stepNo   = failedAt !== null ? failedAt + 1 : "?";

  // Session expiry is handled by the caller (see the `session_expired` branch in the outer
  // catch, above the call site) before buildFailureResponse is ever reached — it isn't a
  // selector/DOM failure and needs no screenshot or recovery payload.

  // Build Studio (T1/T2 ceiling): deterministic terminal failure, no agent recovery payload.
  if (!agentRecoveryEnabled) {
    appendRecoveryEvent({ event: "recovery_ceiling_reached", tier: maxRecoveryTier,
      slug: resolvedEntry && resolvedEntry.slug, step_index: failedAt });
    await _writeStudioEvidence(page, err, resolvedEntry, steps, failedAt, stepNo, deps);
    const intent = stepRecoveryContext(err);
    const detail = intent ? `\nStep intent: ${JSON.stringify(intent)}` : "";
    return { content: [{ type: "text", text:
      `Execution failed at step ${stepNo}: ${err.message}\nPage URL: ${url}\n` +
      `Recovery ceiling Tier ${maxRecoveryTier} (deterministic cascade only — no agent recovery).${detail}` }] };
  }

  // PROD-3 — fail closed, same shape as the ceiling-reached terminal branch above: no digest, no
  // candidate list, no resumable park (server.js's `parkable` check excludes both flags too).
  if (err.destructiveHalt || err.entityNotFound) {
    // destructiveHalt is already logged at the moment cascade.js stops (destructive_recovery_halted);
    // entityNotFound has no earlier emit site (resolution.js is pure/sync-agnostic of the log), so
    // it's recorded here instead.
    if (err.entityNotFound) {
      appendRecoveryEvent({ event: "entity_binding_not_found",
        slug: resolvedEntry && resolvedEntry.slug, step_index: failedAt });
    }
    const intent = stepRecoveryContext(err);
    const detail = intent ? `\nStep intent: ${JSON.stringify(intent)}` : "";
    return { content: [{ type: "text", text:
      `Execution failed at step ${stepNo}: ${err.message}\nPage URL: ${url}${haltReasonNoteText(err)}${detail}` }] };
  }

  const stageKey = parkKey(resolvedEntry.workspace_id || "", resolvedEntry.slug || "");
  // Always the armed tier above the Studio ceiling — kept as a call rather than a constant
  // because the ceiling still decides it, and telemetry still reports it.
  const tier = recoveryStage.nextRecoveryTier({ maxRecoveryTier });

  // Stagnation hard cap (browser-use's soft PageFingerprint nudge, flipped to a hard stop):
  // identical page state across consecutive recovery rounds means nothing is changing — the
  // first identical round is still allowed (that is exactly the designed T3 → T4 escalation,
  // which adds vision even on an unchanged page); beyond STAGNATION_LIMIT identical rounds,
  // refuse further paid rounds and fail deterministically.
  const fp = await capturePageFingerprint(page);
  const { round, stagnant } = recoveryStage.recordRound(stageKey, failedAt, tier, fp);

  appendRecoveryEvent({ event: "agent_recovery_requested", tier, round,
    slug: resolvedEntry && resolvedEntry.slug, step_index: failedAt });
  if (runTracker) runTracker.emit("tier_escalated", { si: failedAt, l: tier });

  if (stagnant) {
    appendRecoveryEvent({ event: "recovery_stagnant_stop", slug: resolvedEntry && resolvedEntry.slug,
      step_index: failedAt, tier, round });
    if (runTracker) runTracker.emit("override_rejected", { si: failedAt, reason: "stagnant-page" });
    return { content: [{ type: "text", text:
      `Execution still failing at step ${stepNo}: ${err.message}\nPage URL: ${url}\n` +
      `Recovery stopped: the page has not changed across repeated Tier ${tier} recovery attempts, ` +
      `so further automated recovery would burn tokens without new information. Inspect the page ` +
      `yourself (or ask the user), fix the root cause, then call execute_skill again with ` +
      `resume_from: ${failedAt ?? 0} and an explicit step_overrides selector if you have one.` }] };
  }

  if (err.overrideValidationFailed) {
    appendRecoveryEvent({ event: "agent_override_rejected", slug: resolvedEntry && resolvedEntry.slug,
      step_index: failedAt, reason: err.overrideReason });
    if (runTracker) runTracker.emit("override_rejected", { si: failedAt, reason: err.overrideReason });
  }

  const resumeKey = failedAt !== null ? String(failedAt) : "0";

  // Ground truth: live, post-cascade inventory — always fresh (see gatherInventory).
  const { inventory: currentInventory, overlay } = await gatherInventory(page, err, { frameScopedInventory });

  // Rank-and-cap against the recorded target (never positional truncation), then publish the
  // nomination map so the next execute_skill call can resolve candidate_index → derived
  // selector through the validateOverrideSelector uniqueness gate.
  const digest = buildIndexedDigest(currentInventory || [], digestTargetContext(err));
  recoveryStage.setCandidateMap(stageKey, digest.map);

  const viewport = (() => { try { return page.viewportSize(); } catch (_) { return null; } })();
  let scrollY = null;
  try { const sy = await evalOn(page, pageScripts.getScrollY); scrollY = sy === EVAL_TIMED_OUT ? null : sy; } catch (_) {}

  const contextSections = buildContextSections(err, steps, failedAt, viewport, scrollY, stepAssertions);
  const notes = `${frameNotFoundNoteText(err)}${overrideNoteText(err)}` +
    `${overlayNoteText(overlay, resumeKey)}${dismissRejectedNoteText(err)}`;

  const earlyDiffers = earlySnapshotDiffers(err, currentInventory);

  // ── The armed agent round: ranked digest + CUA-style visual grounding ──
  // P7: capture as JPEG (lossless PNG is 3-8× larger; Claude token cost is dimension-based either way)
  const failShot = await page.screenshot({ type: "jpeg", quality: 80 }).catch(() => null);

  // P5: skip visual reference if already sent for this (slug, step) in this execution
  const visualRefKey = resolvedEntry && failedAt !== null ? `${resolvedEntry.slug}:${failedAt}` : null;
  const alreadySentRef = deps.sentVisualRefs && visualRefKey ? deps.sentVisualRefs.has(visualRefKey) : false;

  let visualRefData = null, visualRefMime = null;
  if (resolvedEntry && failedAt !== null && !alreadySentRef) {
    const refPath = _resolveVisualRef(err, resolvedEntry, failedAt, deps);
    if (refPath) {
      try {
        visualRefData = fs.readFileSync(refPath).toString("base64");
        visualRefMime = path.extname(refPath).toLowerCase() === ".png" ? "image/png" : "image/jpeg";
        if (deps.sentVisualRefs && visualRefKey) deps.sentVisualRefs.add(visualRefKey);
      } catch (_) {
        visualRefData = null;  // unreadable is the same as absent — the header adapts either way
      }
    }
  }

  // The recording-time reference image is only present when this skill's `visuals/` actually
  // reached this machine. Describing it unconditionally (as this header used to) told the model
  // to compare against an attachment that isn't there — an invitation to invent what it showed,
  // in the one tier that decides where to click. Describe only what is attached.
  const groundTruthSentence = visualRefData
    ? `The "Current page" image is ground truth; the recording-time reference image only shows ` +
      `how the target used to look and may be outdated.`
    : `The "Current page" image is ground truth. No recording-time reference image is available ` +
      `for this step — judge from the current page and the ranked element list alone, and do not ` +
      `assume how the target used to look.`;

  const header =
    `Execution failed at step ${stepNo} (Tier 1–2 cascade exhausted): ${err.message}\n` +
    `Page URL: ${url}\n\n` +
    `Self-healing recovery. The deterministic cascade could not resolve this step's element, so ` +
    `identify it yourself: the ranked element list below is ground truth for what EXISTS on the ` +
    `page, and the screenshots show what it LOOKS like. ${groundTruthSentence} ` +
    `Then resume by calling execute_skill again with:\n` +
    `  resume_from: ${failedAt ?? 0}\n` +
    `  step_overrides: { "${resumeKey}": { "candidate_index": <index>, "confidence": <0-1>, "why": "<one line>" } }\n` +
    `Rules:\n` +
    `- Nominate an index from the refreshed ranked element list below, or a selector you can ` +
    `read straight off the image (visible testid/id/label). The runtime re-verifies your pick ` +
    `against a uniqueness gate before acting — it never blindly trusts it.\n` +
    `- The screenshot is viewport-only; the target may be off-screen (see scrollY) — the ` +
    `element list proves existence even when the image does not; the images settle appearance ` +
    `and disambiguation.\n` +
    `- Do not guess — if nothing matches, tell the user the page has changed and ask how to ` +
    `proceed.${notes}`;

  const t4 = [contextSections];
  if (digest.shown) {
    t4.push(`Interactive elements NOW — refreshed after the failed round, ranked against the ` +
      `recorded target (${digest.shown} of ${digest.total}; nominate via candidate_index):\n${digest.text}`);
  }
  if (earlyDiffers) {
    t4.push(`Elements at the moment of failure, before Tier 1–2 remedies ran (may include ` +
      `since-closed transient UI — do not treat as current):\n${JSON.stringify(err.earlyDomSnapshot)}`);
  }

  const content = [{ type: "text", text: header }, { type: "text", text: t4.join("\n\n") }];

  if (err.preShot)    content.push({ type: "text", text: "Pre-step screenshot (before the action):" }, { type: "image", data: err.preShot.toString("base64"), mimeType: "image/jpeg" });
  if (visualRefData)  content.push({ type: "text", text: `Reference image of the target from recording (step ${stepNo}) — recording-time appearance, may be outdated:` }, { type: "image", data: visualRefData, mimeType: visualRefMime });
  if (failShot)       content.push({ type: "text", text: "Current page — ground truth:" }, { type: "image", data: failShot.toString("base64"), mimeType: "image/jpeg" });

  return { content };
}

module.exports = {
  buildFailureResponse,
  stepRecoveryContext,
  digestTargetContext,
  expectedStateBlock,
  executedStepsBreadcrumb,
  recordedContextBlock,
  anchorSentenceBlock,
  phaseHintBlock,
  gatherInventory,
  overlayNoteText,
  dismissRejectedNoteText,
  _writeStudioEvidence,
};
