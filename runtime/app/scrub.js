"use strict";

// The only door popup text goes through on its way into telemetry (`app_obs`, docs/TRD.md 11.5).
//
// A customer's machine must never send what a person typed or what a page showed them -- only the
// SHAPE of a popup: a few words of its title, its button labels, the page's route. Everything
// errs toward dropping. This mirrors conxa_compile/app_graph/scrub.py and the route rule in
// compiler/navigation_effects.py; both sides are tested against the same cases in
// test/fixtures/scrub_cases.json so they cannot drift apart.
//
// Known, accepted difference: \w is ASCII-only here and Unicode-aware in Python, which only matters
// for an apostrophe next to a non-ASCII letter.

const MAX_LABEL_WORDS = 5;
const DYNAMIC_SEGMENT_MIN_LEN = 16;

const URL_RE = /https?:\/\/\S+/gi;
const EMAIL_RE = /\S+@\S+/g;
const QUOTED_RE = /"[^"]*"|“[^”]*”|‘[^’]*’|(?<!\w)'[^']*'(?!\w)/g;
const FILEISH_RE = /\S+\.[A-Za-z0-9]{1,5}\b/g;
const HAS_DIGIT_RE = /\d/;

function scrubText(text, maxLen = 60) {
  let s = String(text == null ? "" : text);
  for (const rx of [URL_RE, EMAIL_RE, QUOTED_RE, FILEISH_RE]) s = s.replace(rx, " ");
  return s.split(/\s+/).filter((w) => w && !HAS_DIGIT_RE.test(w)).join(" ").slice(0, maxLen).trim();
}

/** A control label ("Keep both"). null when nothing is left, what is left is long enough to be
 * content, or scrubbing had to remove anything at all (a label carrying a file name, a number or a
 * quoted name is content). */
function scrubLabel(text) {
  const s = scrubText(text, 40);
  const raw = String(text == null ? "" : text).split(/\s+/).filter(Boolean).join(" ");
  if (!s || s.split(/\s+/).length > MAX_LABEL_WORDS || s !== raw) return null;
  return s;
}

// Mirrors compiler/outcome_fork.py::message_tokens.
function messageTokens(text) {
  const stripped = String(text == null ? "" : text).replace(/(["'“”‘’]).*?\1/g, " ").replace(/\d+/g, " ");
  const seen = [];
  for (const w of stripped.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ")) {
    if (w.length > 2 && !seen.includes(w)) seen.push(w);
  }
  return seen.slice(0, 12);
}

/** A page title as a handful of lowercase words. */
function scrubTokens(text, limit = 8) {
  return messageTokens(scrubText(text, 120)).slice(0, limit);
}

/** "/orders/:id" for https://app.com/orders/847291?token=abc ; "" for anything that is not http(s). */
function routeTemplate(url) {
  let u;
  try { u = new URL(String(url || "").trim()); } catch (_) { return ""; }
  if ((u.protocol !== "http:" && u.protocol !== "https:") || !u.host) return "";
  const segs = u.pathname.split("/").filter(Boolean)
    .map((seg) => (HAS_DIGIT_RE.test(seg) || seg.length >= DYNAMIC_SEGMENT_MIN_LEN ? ":id" : seg));
  return "/" + segs.join("/");
}

module.exports = { scrubText, scrubLabel, scrubTokens, messageTokens, routeTemplate };
