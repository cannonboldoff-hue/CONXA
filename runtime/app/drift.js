"use strict";

// Pre-execution drift detection (advisory, warn-not-block).
//
// A skill pack carries a compiled `structural_fingerprint` (the first few
// interactive "landmarks" of the workflow — see compiler _build_structural_fingerprint).
// Before executing step 0 we check whether those landmarks are still present on
// the live page. If most of them have vanished the target app has likely been
// redesigned; we emit a `drift_detected` telemetry signal so the fleet dashboard
// can flag the pack for review. This NEVER blocks execution — the recovery
// cascade still runs per step. No LLM is involved; it is pure resolver scoring.

const { scoreCandidate } = require("./resolver");
const pageScripts = require("./page_scripts");
const { toLocator } = require("./resolve_adapter");
const { evalOn, EVAL_TIMED_OUT } = require("./page_eval");

const PRESENCE_THRESHOLD = 0.5;      // per-landmark agreement below this = "missing"
const DRIFT_RATIO_THRESHOLD = 0.5;   // fraction of missing landmarks that trips drift
const PER_LANDMARK_CAP = 20;         // max live candidates inspected per landmark

// Map a structural landmark to the fingerprint shape scoreCandidate() expects.
function landmarkFingerprint(lm) {
  return {
    data_testid: (lm && lm.data_testid) || "",
    aria_label: (lm && lm.aria_label) || "",
    inner_text: (lm && lm.inner_text) || "",
  };
}

// Pure: best agreement score of a landmark against a list of live descriptors.
function bestLandmarkScore(lm, descriptors) {
  const fp = landmarkFingerprint(lm);
  let best = 0;
  for (const d of descriptors || []) {
    const s = scoreCandidate(d, fp);
    if (s > best) best = s;
  }
  return best;
}

// Pure aggregation: given each landmark's best score, decide whether the pack drifted.
function assessDrift(landmarks, scores, opts = {}) {
  const presenceThreshold = typeof opts.presenceThreshold === "number" ? opts.presenceThreshold : PRESENCE_THRESHOLD;
  const driftRatioThreshold = typeof opts.driftRatioThreshold === "number" ? opts.driftRatioThreshold : DRIFT_RATIO_THRESHOLD;
  const total = Array.isArray(landmarks) ? landmarks.length : 0;
  if (!total) return { drift: false, total: 0, missing: 0, driftRatio: 0, missingIntents: [] };
  let missing = 0;
  const missingIntents = [];
  landmarks.forEach((lm, i) => {
    if ((scores[i] || 0) < presenceThreshold) {
      missing++;
      if (lm && lm.intent) missingIntents.push(lm.intent);
    }
  });
  const driftRatio = missing / total;
  return { drift: driftRatio >= driftRatioThreshold, total, missing, driftRatio, missingIntents };
}

// Escape a value for use inside a Playwright/CSS attribute selector's double quotes.
function _attrValue(v) {
  return String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

// Gather live candidate descriptors for one landmark, trying its strongest identity
// signals in turn and stopping at the first that produces matches. Best-effort; any
// locator that throws (e.g. an un-parseable primary_selector grammar) is skipped.
async function _gatherForLandmark(page, lm, cap) {
  const locators = [];
  const push = (fn) => { try { const l = fn(); if (l) locators.push(l); } catch (_) { /* skip */ } };
  if (lm.data_testid) {
    const v = _attrValue(lm.data_testid);
    push(() => page.locator(`[data-testid="${v}"], [data-test-id="${v}"]`));
  }
  if (lm.aria_label) push(() => page.locator(`[aria-label="${_attrValue(lm.aria_label)}"]`));
  // toLocator, not page.locator(): primary_selector may be a role=/text= display string
  // (selector_grammar.py::signal_to_display) — needs tolerant accessible-name matching,
  // not Playwright's exact-match selector engine.
  if (lm.primary_selector) push(() => toLocator(page, lm.primary_selector));
  if (lm.inner_text) push(() => page.getByText(lm.inner_text, { exact: false }));

  const descriptors = [];
  for (const loc of locators) {
    let all = [];
    try { all = await loc.all(); } catch (_) { all = []; }
    for (const item of all.slice(0, cap)) {
      let d;
      try { d = await evalOn(item, pageScripts.extractDescriptor); } catch (_) { continue; }
      if (d && d !== EVAL_TIMED_OUT) descriptors.push(d);
    }
    if (descriptors.length) break; // first productive signal is enough
  }
  return descriptors;
}

// Page-driven entry point. Returns the drift verdict (see assessDrift). Never throws.
async function detectPreExecDrift(page, structuralFingerprint, opts = {}) {
  const landmarks = structuralFingerprint && Array.isArray(structuralFingerprint.landmarks)
    ? structuralFingerprint.landmarks : [];
  if (!landmarks.length) return { drift: false, total: 0, missing: 0, driftRatio: 0, missingIntents: [] };
  const cap = typeof opts.perLandmarkCap === "number" ? opts.perLandmarkCap : PER_LANDMARK_CAP;
  const scores = [];
  for (const lm of landmarks) {
    let descriptors = [];
    try { descriptors = await _gatherForLandmark(page, lm, cap); } catch (_) { descriptors = []; }
    scores.push(bestLandmarkScore(lm, descriptors));
  }
  return assessDrift(landmarks, scores, opts);
}

module.exports = {
  detectPreExecDrift,
  assessDrift,
  bestLandmarkScore,
  landmarkFingerprint,
  PRESENCE_THRESHOLD,
  DRIFT_RATIO_THRESHOLD,
};
