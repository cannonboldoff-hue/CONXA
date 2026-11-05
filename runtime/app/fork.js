"use strict";
// fork.js — outcome forks. Pure decision math for the `outcome_fork` step: nothing here touches
// Playwright or the live page, so it is unit-testable without a browser (the same split as
// resolver.js / resolve_adapter.js and choice.js / handlers.js). run.js::runForkStep is the
// adapter that observes the page, calls these, and executes the chosen option.
//
// An outcome fork says: "after this action the app can legitimately land in one of several known
// states (no dialog; a 'file already exists' dialog; ...); each known state has a policy". The
// runtime's job is to recognise which state it is in WITHOUT a model, then apply that policy —
// and to never guess: it does not click a popup it does not know.
//
// Nothing pauses on its own. A run goes on as recorded; a person is asked only where a person put
// that question in (`policy.reviewed`, written by the editor's set_fork_policy, or a handover step).
// A surface the fork does not list (a toast, a progress panel, a popup nobody taught it) is
// observed and passed over -- nothing here clicks it -- and if it really blocks the page, the next
// step fails with the surface named in its warnings. What the runtime still never does is answer a
// decision for anyone: a consequential option without the author's approval stops the run.
//
// Shape (execution.json), all fields optional unless stated:
//   { type: "outcome_fork", timeout_ms, unknown_policy: "continue"|"stop",
//     clean: { observed },
//     states: [{ id, kind, signature: { title, button_labels, message_tokens, container_selector },
//                options: [{ id, label, role: "dismiss"|"consequential"|"neutral", steps: [...] }],
//                policy: { mode: "auto", option_id, author_approved }
//                      | { mode: "by_input", input, reviewed }
//                      | { mode: "ask_human", suggested, reviewed }
//                      | { mode: "continue" }
//                      | { mode: "stop", message } }] }
//
// Safety rules — do not loosen:
// - A consequential option (Replace / Delete / Overwrite ...) never runs under `auto` unless the
//   author explicitly approved it (`author_approved`, set only by the editor's set_fork_policy).
// - An unknown dialog is never matched loosely: every button label in a state's signature must be
//   on screen, and two equally good states means "unknown", not "pick the first".
// - A caller-supplied `by_input` value is matched with choice.js (never guesses on ambiguity); the
//   value `ask` (the default) hands the decision to a person.

const { matchOption } = require("./choice");

const DEFAULT_TIMEOUT_MS = 4000;
const MIN_TIMEOUT_MS = 1500;
const MAX_TIMEOUT_MS = 8000;

// A quoted span: "..." or '...' (a quote that opens a word, so a contraction like don't is not one) or
// the typographic pairs. Mirrors outcome_fork.py::_QUOTED_RE.
const QUOTED_RE = /"[^"]*"|(?<![A-Za-z0-9])'[^']*'(?![A-Za-z0-9])|“[^”]*”|‘[^’]*’/g;

function norm(s) {
  return String(s == null ? "" : s).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function clampTimeout(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_TIMEOUT_MS;
  return Math.min(MAX_TIMEOUT_MS, Math.max(MIN_TIMEOUT_MS, Math.round(n)));
}

function asArray(v) { return Array.isArray(v) ? v : []; }

// Words of a dialog message with digits / quoted text stripped, so "report.pdf already exists"
// and "q3.xlsx already exists" tokenize identically (matches the compiler's message_tokens).
function messageTokens(text) {
  return norm(String(text || "").replace(QUOTED_RE, " ").replace(/\d+/g, " "))
    .split(" ").filter((w) => w.length > 2);
}

function probeLabels(probe) {
  return asArray(probe && probe.controls)
    .map((c) => norm((c && (c.name || c.text)) || ""))
    .filter(Boolean);
}

// A popup title with quoted text and digits removed, so "Replace 'report.pdf'?" and
// "Replace 'q3.xlsx'?" are the same popup. Mirrors outcome_fork.py::value_free_title. Applied to
// BOTH sides so packs compiled before the compiler started stripping titles still match.
function valueFreeTitle(text) {
  return String(text == null ? "" : text).replace(QUOTED_RE, " ").replace(/\d+/g, " ").replace(/\s+/g, " ").trim();
}

function titleCompatible(sigTitle, probeTitle) {
  const a = norm(valueFreeTitle(sigTitle));
  const b = norm(valueFreeTitle(probeTitle));
  if (!a || !b) return true; // no title on one side: the button set has to carry the match
  return a === b || a.includes(b) || b.includes(a);
}

// Score a state against what is on screen. null = not a match. Higher = better.
function scoreState(state, probe) {
  const sig = (state && state.signature) || {};
  const wanted = asArray(sig.button_labels).map(norm).filter(Boolean);
  if (!wanted.length) return null;
  const have = new Set(probeLabels(probe));
  if (!wanted.every((l) => have.has(l))) return null;
  if (!titleCompatible(sig.title, probe && probe.title)) return null;
  // Exact button-set agreement beats a subset ("Replace / Keep both / Cancel" must not be
  // mistaken for a 2-button state that merely happens to be contained in it).
  let score = wanted.length * 10 - (have.size - wanted.length);
  const sigTokens = new Set(asArray(sig.message_tokens).map(norm));
  if (sigTokens.size) {
    const pt = messageTokens(probe && probe.text);
    score += pt.filter((t) => sigTokens.has(t)).length;
  }
  return score;
}

/** What is the page showing? `probe` is dialogProbe()'s result, or null when no dialog is open.
 * Returns { kind: "clean" } | { kind: "state", state } | { kind: "unknown" }. */
function matchState(probe, states) {
  if (!probe) return { kind: "clean" };
  let best = null;
  let bestScore = -Infinity;
  let tie = false;
  for (const state of asArray(states)) {
    const score = scoreState(state, probe);
    if (score == null) continue;
    if (score > bestScore) { best = state; bestScore = score; tie = false; }
    else if (score === bestScore) tie = true;
  }
  if (!best || tie) return { kind: "unknown" };
  return { kind: "state", state: best };
}

function badInput(message) {
  return Object.assign(new Error(message), { badInput: true });
}

function findOption(state, id) {
  return asArray(state && state.options).find((o) => o && o.id === id) || null;
}

/** Apply a state's policy. Returns
 *   { do: "run", option, source }   run this option's steps
 *   { do: "pause", reason, suggested? }   a person asked to be asked (policy.reviewed)
 *   { do: "continue" }              leave the popup alone and carry on
 *   { do: "stop", message }         fail the run with this message
 * `source` records who decided (audit): "policy" | "input". Throws a badInput error for an
 * unusable by_input value (so the caller learns the valid options). A pause happens only when the
 * author chose it (`policy.reviewed`); a compiler-written policy that would have asked a person
 * stops instead. Inside a loop a pause cannot keep its place, so it also degrades to a stop. */
function decide(state, inputs, { loopDepth = 0 } = {}) {
  const policy = (state && state.policy) || {};
  const mode = String(policy.mode || "stop");
  const title = (state && state.signature && state.signature.title) || "a popup";
  const stopMessage = policy.message || `Needs a decision: ${title}`;
  const needsAuthor = `${stopMessage}. Tell Conxa in the chat how this popup should be handled.`;
  const pause = (reason, suggested) => {
    if (loopDepth > 0) return { do: "stop", message: `${stopMessage} (a person can't be asked inside a repeated step)` };
    if (policy.reviewed !== true) return { do: "stop", message: needsAuthor };
    return { do: "pause", reason, suggested: suggested || null };
  };

  if (mode === "continue") return { do: "continue" };

  if (mode === "auto") {
    const option = findOption(state, policy.option_id);
    if (!option) return { do: "stop", message: stopMessage };
    if (option.role === "consequential" && !policy.author_approved) {
      return { do: "stop", message: `${needsAuthor} Nothing was changed.` };
    }
    return { do: "run", option, source: "policy" };
  }

  if (mode === "by_input") {
    const raw = inputs ? inputs[policy.input] : undefined;
    const text = String(raw == null ? "" : raw).trim();
    if (!text || norm(text) === "ask") return pause("ask_each_run", policy.suggested);
    const options = asArray(state.options).map((o) => ({ label: o.label, value: o.id, option: o }));
    const hit = matchOption(text, options);
    if (!hit) {
      const valid = options.map((o) => `"${o.label}"`).join(", ");
      throw badInput(`"${text}" isn't a valid choice for ${policy.input}. Valid choices: ${valid}, or "ask".`);
    }
    return { do: "run", option: hit.option, source: "input" };
  }

  if (mode === "ask_human") return pause("ask_human", policy.suggested);
  return { do: "stop", message: stopMessage };
}

/** The decision for a surface no state recognises: carry on as recorded, unless the fork was told
 * to stop. A recognised-nothing overlay is not evidence of a choice, so it never asks a person.
 * A legacy `unknown_policy: "ask_human"` from an older compile is treated as `continue`. */
function decideUnknown(fork, probe, { loopDepth = 0 } = {}) {
  const title = (probe && probe.title) || "an unexpected popup";
  if ((fork && fork.unknown_policy) === "stop") return { do: "stop", message: `Needs a decision: ${title}` };
  return { do: "continue", reason: "unknown_surface" };
}

// A blocking popup that is asking the person to CHOOSE, as opposed to one that is only in the way.
// Mirrors compiler/outcome_fork.py::classify_dialog so the compiler and the runtime agree on what a
// "decision dialog" is: two or more buttons, not all of them plain dismiss labels, no input fields
// (that is a form), and not a cookie/consent banner (those keep the known-pattern dismissal).
const PURE_DISMISS = new Set(["close", "cancel", "dismiss", "no thanks", "not now", "skip", "later", "maybe later", "x", "×"]);
const CONSENT_RE = /\b(cookie|cookies|consent|gdpr|privacy|accept all|reject all|manage preferences|onetrust)\b/i;

function isDecisionDialog(probe) {
  if (!probe || probe.native) return false;
  if (probe.hasFields) return false;
  const labels = probeLabelsRaw(probe);
  if (labels.length < 2) return false;
  if (CONSENT_RE.test([probe.title, probe.text, ...labels].join(" "))) return false;
  return !labels.every((l) => PURE_DISMISS.has(norm(l)));
}

// A button that cannot be taken back or that spends / sends / publishes. A popup with any of these is
// never answered by an agent; the run stops. Mirrors outcome_fork.py::is_irreversible_label (pinned
// by decision_surface_cases.json). Errs wide: a wrong match only ever stops a run.
const IRREVERSIBLE_RE = /\b(delete|remove|erase|permanently|pay|buy|purchase|order|checkout|send|submit|publish|transfer|revoke)\b/i;
// Mirrors outcome_fork.py::_CONSEQUENTIAL_RE (the fork-local word list).
const CONSEQUENTIAL_RE = /\b(replace|overwrite|discard|delete|remove|erase|override|permanently|don'?t save|do not save|revoke|reset|clear all)\b/i;

function isIrreversibleLabel(label) {
  return IRREVERSIBLE_RE.test(String(label == null ? "" : label));
}

// Surface classes, least to most cautious; when a surface fits several, the later one wins.
const CAUTION_ORDER = ["notice", "obstruction", "decision_benign", "info_request", "decision_consequential", "fault"];

function mostCautious(...classes) {
  let best = null;
  for (const c of classes) {
    if (CAUTION_ORDER.includes(c) && (best === null || CAUTION_ORDER.indexOf(c) > CAUTION_ORDER.indexOf(best))) best = c;
  }
  return best;
}

/** The class of what dialogProbe() saw: notice | obstruction | decision_benign | info_request |
 * decision_consequential | native, or null when nothing is open. "fault" comes from the fault probe,
 * never from a dialog. Mirrors outcome_fork.py::classify_surface. */
function classifySurface(probe) {
  if (!probe) return null;
  if (probe.native) return "native";
  if (probe.hasFields) return "info_request";
  const labels = probeLabelsRaw(probe);
  if (probe.progress || (probe.live && labels.length < 2)) return "notice";
  if (!isDecisionDialog(probe)) return "obstruction";
  return labels.some((l) => CONSEQUENTIAL_RE.test(l) || isIrreversibleLabel(l)) ? "decision_consequential" : "decision_benign";
}

// Reserved id for "stop the run" in a pause's choice list. Not a slug, so it can never collide
// with an option id derived from a button label.
const STOP_CHOICE = "__stop__";

function probeLabelsRaw(probe) {
  return asArray(probe && probe.controls)
    .map((c) => String((c && (c.name || c.text)) || "").trim())
    .filter(Boolean);
}

/** What an agent (the MCP client) may be asked about an UNRECORDED question popup: only its visible
 * buttons, and only when none of them looks irreversible. `blockedBy` names the first one that does,
 * so the run can stop with a reason instead of asking. The "stop" choice is always offered. */
function agentPopupChoices(probe) {
  const labels = probeLabelsRaw(probe);
  const blockedBy = labels.find(isIrreversibleLabel) || null;
  const choices = blockedBy
    ? []
    : labels.map((l) => ({ id: l, label: l, role: CONSEQUENTIAL_RE.test(l) ? "consequential" : "unknown" }));
  if (!blockedBy) choices.push({ id: STOP_CHOICE, label: "Stop the run", role: "stop" });
  return { choices, blockedBy };
}

/** The choices a person is offered when a fork pauses. A recognised state offers its own options
 * (by id); an unrecognised popup offers only the buttons actually on screen (by label) -- the
 * answer is later checked against the same live list, so no one can name an action that is not
 * there. Both end with "stop". */
function pauseChoices(state, probe) {
  const choices = state
    ? asArray(state.options).map((o) => ({ id: o.id, label: o.label || o.id, role: o.role || "neutral" }))
    : probeLabelsRaw(probe).map((l) => ({ id: l, label: l, role: "unknown" }));
  choices.push({ id: STOP_CHOICE, label: "Stop the run", role: "stop" });
  return choices;
}

module.exports = {
  isDecisionDialog,
  classifySurface,
  mostCautious,
  isIrreversibleLabel,
  CAUTION_ORDER,
  STOP_CHOICE,
  probeLabelsRaw,
  pauseChoices,
  agentPopupChoices,
  DEFAULT_TIMEOUT_MS,
  clampTimeout,
  messageTokens,
  matchState,
  valueFreeTitle,
  decide,
  decideUnknown,
  findOption,
  norm,
};
