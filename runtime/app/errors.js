"use strict";
// errors.js — the one place raw internal/Playwright error text is turned into something a
// person (or the recovery agent) can read. Every failure funnel (run.js's stepFailure,
// server.js's catch-alls, http_client.js, host/mcp_register.js's config-editor codes) routes
// through here instead of hand-rolling its own wording, so the mapping only needs to live once.
//
// plainCause() never throws and never loses information: `detail` always carries at least the
// first line of the original message, so recovery (which reads err.message today) keeps working
// even for patterns this file doesn't recognize yet.

// Ordered: first pattern to match wins. Keep specific traps (e.g. "intercepts pointer events")
// before generic ones (e.g. "Timeout").
const PATTERNS = [
  [/intercepts pointer events/i, "Another element was covering the target, so the click couldn't land."],
  [/strict mode violation/i, "The recorded selector matched more than one element on the page."],
  [/Target (page|closed)|has been closed/i, "The browser tab closed before the step finished."],
  [/Timeout \d+ms exceeded/i, "The page didn't respond in time — the element never appeared or the action never completed."],
  [/net::ERR_/i, "The page failed to load (network error)."],
  [/Executable doesn't exist/i, "The browser couldn't start — its files are missing or damaged."],
  [/Invalid regular expression/i, "One of the skill's patterns isn't valid — it needs to be fixed at compile time."],
  [/is not a valid selector/i, "One of the skill's selectors isn't valid — it needs to be fixed at compile time."],
];

// Playwright appends a multi-line "Call log:" trace to many errors — useful in server logs,
// noise in a message shown to a person. Keep only the first line for `detail`.
function firstLine(message) {
  const text = String(message == null ? "" : message);
  const idx = text.indexOf("\nCall log:");
  const head = idx === -1 ? text : text.slice(0, idx);
  return head.split("\n")[0].trim();
}

// { summary: one plain sentence, detail: first line of the raw message (still technical,
// meant for the recovery agent / logs, never hidden from evidence). Never throws.
function plainCause(err) {
  const raw = (err && err.message) || String(err || "");
  const detail = firstLine(raw) || "No error message was provided.";
  // A deliberate stop (a popup with no recorded answer, an app fault, a bad input, "couldn't confirm") is thrown
  // with a sentence written for a person. It is the summary; wrapping it as "unexpected" would hide the cause.
  if (err && (err.forkDecisionNeeded || err.fault || err.badInput || err.agentSpanRefused)) return { summary: detail, detail: "" };
  for (const [re, summary] of PATTERNS) {
    if (re.test(raw)) return { summary, detail };
  }
  return { summary: "Something unexpected went wrong.", detail };
}

// host/mcp_register.js's per-target editor status codes ("ok" / "would-*" / "skipped:*" /
// "error:*"), shared by config_edit.js, config_edit_toml.js and config_edit_yaml.js. Only the
// "error:" codes need translating — everything else is already a plain word.
const CONFIG_STATUS_TEXT = {
  "error:unparseable": "the config file has a syntax error and couldn't be read",
  "error:changed-underneath": "the config file changed on disk while it was being edited",
  "error:not-a-regular-file": "that config path isn't a regular file",
};

// `status` is one of the codes above, or "error:write-failed:<code>" / "error:stat-failed:<code>"
// (the suffix is a raw fs error code such as EPERM/EACCES/ENOSPC).
function configStatusText(status) {
  if (CONFIG_STATUS_TEXT[status]) return CONFIG_STATUS_TEXT[status];
  const writeFailed = /^error:write-failed:(.+)$/.exec(status || "");
  if (writeFailed) return `couldn't write the config file (${writeFailed[1]})`;
  const statFailed = /^error:stat-failed:(.+)$/.exec(status || "");
  if (statFailed) return `couldn't read the config file (${statFailed[1]})`;
  if (typeof status === "string" && status.startsWith("error:")) return `couldn't update the config file (${status.slice(6)})`;
  return status;
}

module.exports = { plainCause, firstLine, configStatusText, CONFIG_STATUS_TEXT };
