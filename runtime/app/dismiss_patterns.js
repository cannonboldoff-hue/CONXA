"use strict";
const { evalOn, countOn, EVAL_TIMED_OUT } = require("./page_eval");
const pageScripts = require("./page_scripts");

// Known-pattern overlay dismissal — EXEC-5's "dismiss-known-pattern library", Tier 1 slice.
//
// When a step fails because an unexpected overlay intercepts pointer events
// (classifyException → INTERCEPTED), the Tier 1 remedy used to be exactly one Escape press.
// Most real-world consent/interstitial UI is built from a handful of ubiquitous toolkits
// (OneTrust, Cookiebot, TrustArc, Iubenda, Quantcast, CookieYes, Complianz), so a bounded
// list of their accept/close affordances resolves most never-recorded popups
// deterministically — zero tokens, zero network (cascade.js's purity constraint).
//
// Safety rules — do not loosen:
// - Accept/close affordances ONLY. Never decline buttons (a legal choice the customer must
//   make themselves) and never bare `.modal button` / footer selectors (could confirm a
//   load-bearing dialog like "Confirm delete?"). Every entry names a dismiss control
//   specifically; close-style entries are scoped to dialog/modal containers so unrelated
//   page chrome (sticky headers with X icons) can never match.
// - Reactive only: this ladder fires solely after an INTERCEPTED classification — never as
//   a proactive sweep — so UI the workflow intends to interact with is only ever touched
//   after it actually blocked a recorded target.
// - Learned entries (learned_dismissals.js) are tried before the static list, but they are
//   host-scoped, TTL-bound, and still have to exist and click cleanly at use time.

// Per-candidate click budget. Must comfortably exceed the first-input warm-up cost of a
// freshly loaded page (observed >400 ms on cold headless Chromium); absent candidates never
// reach this timeout (they are skipped by count()), so it only bounds present-but-broken ones.
const DISMISS_PROBE_TIMEOUT_MS = 1200;

const KNOWN_DISMISS_SELECTORS = [
  // ── Named consent toolkits (accept / dismiss affordances) ──────────────────
  "#onetrust-accept-btn-handler",
  "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
  "#truste-consent-button",
  ".iubenda-cs-accept-btn",
  "#qc-cmp2-ui button[mode='primary']",
  ".cmplz-accept",
  ".cky-btn-accept",
  "#cookie_action_close_header",
  // ── Generic accept phrasing ────────────────────────────────────────────────
  "button[aria-label*='accept' i]",
  "[id*='cookie' i] button[class*='accept' i]",
  "[class*='cookie' i] button[class*='accept' i]",
  "[class*='consent' i] button[class*='accept' i]",
  // ── Close affordances, scoped to dialog/modal chrome only ──────────────────
  "[role='dialog'] button[aria-label*='close' i]",
  "[role='alertdialog'] button[aria-label*='close' i]",
  "[aria-modal='true'] button[aria-label*='close' i]",
  ".modal button[class*='close' i]",
];

// Try every candidate in order and click EVERY one that exists and clicks cleanly — not
// just the first. Two reasons this is deliberate:
//   1. Consent stacks are layered in the wild (banner accept → preferences dialog accept):
//      stopping at the first click would strand mid-stack and degrade to paid recovery.
//   2. Some earlier-list candidates click but dismiss nothing (toolkit variants); continuing
//      is what actually clears the overlay, and what makes the learned store record the
//      *winning* tail of the sequence rather than a dead end.
// Absent candidates cost ~one count() each, so the common single-banner case is unaffected.
// Returns { selectors: [...clicked in order], source } or null when nothing matched.
async function dismissKnownOverlay(page, { learned = [], timeoutMs = DISMISS_PROBE_TIMEOUT_MS } = {}) {
  const clicked = [];
  let sawLearned = false;
  // Learned entries go first, but a selector present in BOTH lists must be visited once,
  // not twice (continuation semantics would otherwise click it again on the way through).
  const seen = new Set();
  const queue = [];
  for (const s of [...learned, ...KNOWN_DISMISS_SELECTORS]) {
    if (!seen.has(s)) { seen.add(s); queue.push(s); }
  }
  for (const selector of queue) {
    try {
      const locator = page.locator(selector);
      const n = await countOn(locator, DISMISS_PROBE_TIMEOUT_MS);
      if (n === EVAL_TIMED_OUT || n < 1) continue;
      await locator.first().click({ timeout: timeoutMs });
      clicked.push(selector);
      if (learned.includes(selector)) sawLearned = true;
    } catch (_) {
      // present but unclickable right now → try the next candidate
    }
  }
  if (!clicked.length) return null;
  const anyKnown = clicked.some(s => !learned.includes(s));
  const source = sawLearned ? (anyKnown ? "mixed" : "learned") : "known";
  return { selectors: clicked, source };
}

// ── EXEC-30: gate for an AGENT-NOMINATED dismissal ─────────────────────────────────────────────
// The static ladder above is a closed list the runtime built itself — every entry was chosen by
// us, so it never needs a runtime safety check. An agent nomination is different: Tier B is
// picking an element off a live, previously-unseen page, and it can only ever act on it if the
// label reads as an incidental dismissal, never a commit. Deny wins on any conflict (a button
// labelled "Confirm and close" must never be clicked).
const DISMISS_ALLOW_RE = /\b(close|dismiss|cancel|skip|not now|no thanks|maybe later|later|continue without)\b|[×✕✖]/i;
const DISMISS_DENY_RE = /\b(confirm|accept|agree|allow|submit|save|delete|remove|pay|buy|order|subscribe|decline|reject|opt[\s-]?out)\b/i;

// label: the element's accessible-name-ish string (aria-label || innerText || title).
// inDialogChrome: whether the element sits inside dialog/modal/alertdialog chrome — an unlabeled
// icon button (a bare "×" with no text) is only trusted there, never on arbitrary page chrome.
function isSafeDismissLabel(label, { inDialogChrome = false } = {}) {
  const text = String(label || "").trim();
  if (DISMISS_DENY_RE.test(text)) return false;
  if (DISMISS_ALLOW_RE.test(text)) return true;
  return !text && inDialogChrome;
}

const DIALOG_CHROME_SEL = '[role="dialog"], [role="alertdialog"], [aria-modal="true"], .modal, dialog';

// Click an element the agent nominated for overlay dismissal, gated by isSafeDismissLabel.
// Mirrors dismissKnownOverlay's caution but for a single, untrusted, agent-picked selector:
//   - no match on the live page → { ok:false, reason:"no-match" }
//   - present but its label fails the gate → { ok:false, reason:"unsafe-label", label }
//   - gate passes → click it → { ok:true, selector, label }
// Never throws for an ordinary miss; only a genuinely broken page (locator API failure) escapes.
async function dismissAgentNominated(page, selector, { timeoutMs = DISMISS_PROBE_TIMEOUT_MS } = {}) {
  if (!selector) return { ok: false, reason: "no-match" };
  const locator = page.locator(selector);
  const n = await countOn(locator, timeoutMs);
  if (n === EVAL_TIMED_OUT || n < 1) return { ok: false, reason: "no-match" };
  const first = locator.first();

  const info = await evalOn(first, pageScripts.dismissLabelInfo, DIALOG_CHROME_SEL, timeoutMs).catch(() => ({ label: "", inDialogChrome: false }));
  const safeInfo = info === EVAL_TIMED_OUT ? { label: "", inDialogChrome: false } : info;

  if (!isSafeDismissLabel(safeInfo.label, { inDialogChrome: safeInfo.inDialogChrome })) {
    return { ok: false, reason: "unsafe-label", label: safeInfo.label };
  }

  await first.click({ timeout: timeoutMs });
  return { ok: true, selector, label: safeInfo.label };
}

module.exports = {
  KNOWN_DISMISS_SELECTORS,
  dismissKnownOverlay,
  DISMISS_PROBE_TIMEOUT_MS,
  DISMISS_ALLOW_RE,
  DISMISS_DENY_RE,
  isSafeDismissLabel,
  dismissAgentNominated,
};
