"use strict";
// fork.js — outcome forks. Pure decision math for the `outcome_fork` step: nothing here touches
// Playwright or the live page, so it is unit-testable without a browser (the same split as
// resolver.js / resolve_adapter.js and choice.js / handlers.js). run.js::runForkStep is the
// adapter that observes the page, calls these, and executes the chosen option.
//
// An outcome fork says: "after this action the app can legitimately land in one of several known
// states (no dialog; a 'file already exists' dialog; ...); each known state has a policy". The
// runtime's job is to recognise which state it is in WITHOUT a model, then apply that policy —
// and to fail closed (pause or stop) rather than guess whenever the page is not one it knows.
//
// Shape (execution.json), all fields optional unless stated:
//   { type: "outcome_fork", timeout_ms, unknown_policy: "ask_human"|"stop",
//     clean: { observed },
//     states: [{ id, kind, signature: { title, button_labels, message_tokens, container_selector },
//                options: [{ id, label, role: "dismiss"|"consequential"|"neutral", steps: [...] }],
//                policy: { mode: "auto", option_id, author_approved }
//                      | { mode: "by_input", input }
//                      | { mode: "ask_human", suggested }
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
  return norm(String(text || "").replace(/(["'“”‘’]).*?\1/g, " ").replace(/\d+/g, " "))
    .split(" ").filter((w) => w.length > 2);
}

function probeLabels(probe) {
  return asArray(probe && probe.controls)
    .map((c) => norm((c && (c.name || c.text)) || ""))
    .filter(Boolean);
}

function titleCompatible(sigTitle, probeTitle) {
  const a = norm(sigTitle);
  const b = norm(probeTitle);
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
 *   { do: "pause", reason, suggested? }   a person has to decide
 *   { do: "stop", message }         fail the run with this message
 * `source` records who decided (audit): "policy" | "input". Throws a badInput error for an
 * unusable by_input value (so the caller learns the valid options). Inside a loop a pause cannot
 * keep its place, so it degrades to a stop. */
function decide(state, inputs, { loopDepth = 0 } = {}) {
  const policy = (state && state.policy) || {};
  const mode = String(policy.mode || "stop");
  const title = (state && state.signature && state.signature.title) || "a popup";
  const stopMessage = policy.message || `Needs a decision: ${title}`;
  const pause = (reason, suggested) => (
    loopDepth > 0
      ? { do: "stop", message: `${stopMessage} (a person can't be asked inside a repeated step)` }
      : { do: "pause", reason, suggested: suggested || null }
  );

  if (mode === "auto") {
    const option = findOption(state, policy.option_id);
    if (!option) return { do: "stop", message: stopMessage };
    if (option.role === "consequential" && !policy.author_approved) {
      return pause("consequential_unapproved", option.id);
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

/** The decision for a dialog no state recognises. */
function decideUnknown(fork, probe, { loopDepth = 0 } = {}) {
  const title = (probe && probe.title) || "an unexpected popup";
  const message = `Needs a decision: ${title}`;
  if ((fork && fork.unknown_policy) === "stop" || loopDepth > 0) return { do: "stop", message };
  return { do: "pause", reason: "unknown_dialog", suggested: null };
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

// Reserved id for "stop the run" in a pause's choice list. Not a slug, so it can never collide
// with an option id derived from a button label.
const STOP_CHOICE = "__stop__";

function probeLabelsRaw(probe) {
  return asArray(probe && probe.controls)
    .map((c) => String((c && (c.name || c.text)) || "").trim())
    .filter(Boolean);
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
  STOP_CHOICE,
  probeLabelsRaw,
  pauseChoices,
  DEFAULT_TIMEOUT_MS,
  clampTimeout,
  messageTokens,
  matchState,
  decide,
  decideUnknown,
  findOption,
  norm,
};
