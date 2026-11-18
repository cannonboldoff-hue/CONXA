"use strict";
// fault_classify.js — pure. Decides whether a failed step failed because the APP said it cannot go
// on as asked (expired sign-in, no permission, offline, rate limit, duplicate data, a rejected
// form, a record that is gone) rather than because the recorded element moved. Such a failure is
// final: re-finding the element cannot fix it and an agent round would only spend tokens, so
// run.js stops the run with a typed reason before the recovery cascade (docs/Popup-and-State-
// Handling.md §5, class "fault").
//
// Evidence is deliberately narrow, because a wrong "fault" ends a run that recovery could have
// healed:
// - a response STATUS that arrived after the step started (page_status.js), or the browser being
//   offline: both are signals the page itself did not write, so they count for any step;
// - the app's own TEXT (a role=alert / aria-live=assertive region, or the message of a field marked
//   aria-invalid), and only when the step may already have acted. A step that never reached the
//   page cannot have caused an error message, so a stale alert is not blamed on it. Loose inline
//   text such as a plain "Required" label is never evidence (the probe only reports alert regions
//   and aria-invalid messages).

const KINDS = ["auth_expired", "no_permission", "network", "rate_limited", "duplicate", "validation", "not_found"];

// Priority order: the first kind with a match wins, most specific first.
const TEXT_RULES = [
  ["auth_expired", /\b(session (has )?(expired|timed out|ended)|signed out|logged out|sign ?in again|log ?in again|please (log|sign) ?in|token (has )?expired|not authenticated|unauthori[sz]ed)\b/i],
  ["no_permission", /\b(do(es)?(n'?t| not) have (the )?(permission|access|rights)|no (permission|access)|access denied|permission denied|forbidden|not authori[sz]ed|insufficient (permissions?|privileges?))\b/i],
  ["rate_limited", /\b(too many (requests|attempts)|rate limit(ed)?|slow down|quota (exceeded|reached)|try again (later|in))\b/i],
  ["network", /\b(network error|no (internet|connection)|connection (lost|failed|error|problem)|you are offline|can'?t reach|could not connect|failed to fetch|request timed out)\b/i],
  ["not_found", /\b(not found|no longer (exists|available)|does(n'?t| not) exist|has been (deleted|removed|moved)|can'?t be found|cannot be found)\b/i],
  ["duplicate", /\b(already (exists|taken|in use|registered|been used)|duplicate|must be unique|is taken)\b/i],
  ["validation", /\b(is invalid|not valid|invalid\b|must be|is required|cannot be (blank|empty)|too (short|long)|incorrect|please (enter|provide|select|choose))\b/i],
];
// Text-based kinds that describe the data this step just sent. They need the step to have acted.
const NEEDS_ACTED = new Set(["duplicate", "validation"]);

function statusKind(status) {
  const s = Number(status);
  if (!Number.isFinite(s)) return null;
  if (s === 401) return "auth_expired";
  if (s === 403) return "no_permission";
  if (s === 404) return "not_found";
  if (s === 429) return "rate_limited";
  if (s === 408 || s >= 500) return "network";
  return null;
}

function short(text) {
  return String(text == null ? "" : text).replace(/\s+/g, " ").trim().slice(0, 120);
}

// A data-changing request the app never answered because the connection went away (the server closed it,
// the network dropped). Chromium's own words for it. A request the page or the browser cancelled or blocked
// (ERR_ABORTED, ERR_BLOCKED_BY_CLIENT, a CORS ERR_FAILED) is not the app's doing and is left out.
const DROPPED_RE = /ERR_(EMPTY_RESPONSE|CONNECTION_(RESET|CLOSED|REFUSED|FAILED|TIMED_OUT)|INTERNET_DISCONNECTED|NETWORK_CHANGED|TIMED_OUT|NAME_NOT_RESOLVED|ADDRESS_UNREACHABLE)\b/i;

/** @param {{alerts?: string[], offline?: boolean, status?: number|null, dropped?: string|null, mayHaveActed?: boolean}} evidence
 *  `dropped`: why a data-changing request this step started failed with no answer (the browser's error text).
 *  @returns {{kind: string, source: "offline"|"status"|"connection"|"text", evidence: string} | null} */
function classifyFault({ alerts = [], offline = false, status = null, dropped = null, mayHaveActed = false } = {}) {
  if (offline) return { kind: "network", source: "offline", evidence: "the browser is offline" };
  const fromStatus = statusKind(status);
  if (fromStatus) return { kind: fromStatus, source: "status", evidence: `the app answered with status ${Number(status)}` };
  if (dropped && DROPPED_RE.test(String(dropped))) {
    return { kind: "network", source: "connection", evidence: "the connection dropped before the app answered" };
  }
  if (!mayHaveActed) return null;
  const texts = (Array.isArray(alerts) ? alerts : []).map(short).filter(Boolean);
  for (const [kind, re] of TEXT_RULES) {
    const hit = texts.find((t) => re.test(t));
    if (hit) return { kind, source: "text", evidence: hit };
  }
  return null;
}

// Completion gate (completion.js): did the app SAY the thing it was asked to do did not work? Narrower
// than classifyFault on purpose. This reads status regions after a step that already passed its own
// check, where a wrong "failed" ends a run that actually succeeded, so the loose kinds ("validation",
// "not_found": "No results found" is a normal live-region message) are left out and a "0 failed" or
// "no errors" summary is never a failure.
const COMPLETION_KINDS = new Set(["auth_expired", "no_permission", "rate_limited", "network", "duplicate"]);
const GENERIC_FAILURE_RE = /\b(failed|couldn'?t|could not|unable to|something went wrong|(was|were|wasn'?t|weren'?t|not) (been )?(uploaded|saved|sent|submitted)|error (uploading|saving|sending|submitting))\b/i;
const ZERO_FAILURES_RE = /\b(0|no|zero) (failed|failures?|errors?)\b/i;

/**
 * `appFailure` (optional) is a RegExp built from the words THIS app uses for failure, learned from its recordings
 * (app_knowledge.js::signalsFor), so an app that is not in English is judged by its own words as well as the English ones.
 * @returns {{kind: string, evidence: string} | null}
 */
function classifyCompletionFailure(texts, appFailure = null) {
  for (const t of (Array.isArray(texts) ? texts : []).map(short).filter(Boolean)) {
    if (ZERO_FAILURES_RE.test(t)) continue;
    const f = classifyFault({ alerts: [t], mayHaveActed: true });
    if (f && COMPLETION_KINDS.has(f.kind)) return { kind: f.kind, evidence: t };
    if (GENERIC_FAILURE_RE.test(t) || (appFailure && appFailure.test(t))) return { kind: "action_failed", evidence: t };
  }
  return null;
}

// A native alert() nobody recorded: the app is telling the person something mid-run ("Session expired", "Could not
// save"). The run cannot carry on as if nothing was said, so it is always a fault, typed by its words when they say
// what went wrong and quoted either way.
function alertFault(text) {
  const t = short(text);
  if (!t) return { kind: "alert", source: "alert", evidence: "it had no text" };
  return classifyFault({ alerts: [t], mayHaveActed: true }) || { kind: "alert", source: "text", evidence: t };
}

const PLAIN = {
  auth_expired: "Your sign-in to the app ended",
  no_permission: "The app says you don't have access to this",
  network: "The app could not be reached",
  rate_limited: "The app says there were too many requests",
  duplicate: "The app says this already exists",
  validation: "The app rejected what was entered",
  not_found: "The app says this is gone or was never there",
  alert: "The app showed a message",
};

function faultMessage(fault) {
  const lead = PLAIN[fault.kind] || "The app reported a problem";
  return fault.source === "text" ? `${lead}: "${fault.evidence}"` : `${lead} (${fault.evidence})`;
}

module.exports = { KINDS, classifyFault, alertFault, classifyCompletionFailure, faultMessage, statusKind, NEEDS_ACTED };
