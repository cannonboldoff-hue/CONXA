"use strict";
// Pure multiple-choice replay math, extracted the same way resolver.js is kept separate from
// resolve_adapter.js and date_picker.js is kept separate from handlers.js: nothing here touches
// Playwright or the live page, so it's unit-testable without a browser. handlers.js's
// set_radio/select/select_option/set_checkbox choice branches are the adapters that turn these
// into actual locator actions — see CLAUDE.md's multiple-choice plan for the full design.
//
// Each `options` entry is {value, label, selector} (compiler/choice.py's payload shape, carried
// through handler_hints.choice.options).

function _norm(s) {
  return String(s == null ? "" : s)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

// A sentinel distinct from both "found exactly one" (an option object) and "no match at this
// rung, keep looking" (null) -- an ambiguous rung must stop the ladder immediately rather than
// fall through to a looser one, which could resolve the ambiguity by accident into the wrong
// option. "Never guess" applies to ambiguity exactly as much as to no-match.
const AMBIGUOUS = Symbol("ambiguous");

function _rung(options, pred) {
  const matches = options.filter(pred);
  if (matches.length === 1) return matches[0];
  if (matches.length > 1) return AMBIGUOUS;
  return null;
}

/** Resolve a caller-supplied value ("male", "M", "Male ") against the recorded option set.
 * Returns the matched option, or null when nothing matches OR the match is ambiguous -- this
 * function never guesses; a null result means the caller must surface every valid option and
 * fail closed rather than act on a same-looking-but-wrong element. */
function matchOption(userValue, options) {
  const opts = Array.isArray(options) ? options.filter(Boolean) : [];
  const raw = String(userValue == null ? "" : userValue).trim();
  if (!raw || !opts.length) return null;

  // Rung 1: exact match, label then value.
  let hit = _rung(opts, (o) => o.label === raw);
  if (hit === AMBIGUOUS) return null;
  if (hit) return hit;
  hit = _rung(opts, (o) => o.value === raw);
  if (hit === AMBIGUOUS) return null;
  if (hit) return hit;

  // Rung 2: normalized match (trim/collapse-whitespace/casefold/strip-punctuation), label then
  // value -- catches case, whitespace, and punctuation variants ("male", "MALE", " Male").
  const normRaw = _norm(raw);
  hit = _rung(opts, (o) => _norm(o.label) === normRaw);
  if (hit === AMBIGUOUS) return null;
  if (hit) return hit;
  hit = _rung(opts, (o) => _norm(o.value) === normRaw);
  if (hit === AMBIGUOUS) return null;
  if (hit) return hit;

  // Rung 3: unique prefix match ("M" -> Male, but never when "Married" also starts with "M").
  hit = _rung(opts, (o) => _norm(o.label).startsWith(normRaw));
  if (hit === AMBIGUOUS) return null;
  if (hit) return hit;
  hit = _rung(opts, (o) => _norm(o.value).startsWith(normRaw));
  if (hit === AMBIGUOUS) return null;
  if (hit) return hit;

  return null;
}

/** Multi-valued sibling of matchOption for checkbox groups: `userValue` is either an array of
 * picks or a comma-separated string. Returns the matched options, or null if ANY requested pick
 * fails to resolve to exactly one option -- a partial match is not a safe partial answer. */
function matchOptions(userValue, options) {
  const opts = Array.isArray(options) ? options.filter(Boolean) : [];
  if (userValue == null) return [];
  let picks;
  if (Array.isArray(userValue)) {
    picks = userValue.map((v) => String(v == null ? "" : v).trim()).filter(Boolean);
  } else {
    picks = String(userValue)
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
  }
  if (!picks.length) return [];
  const matched = [];
  const seenValues = new Set();
  for (const pick of picks) {
    const opt = matchOption(pick, opts);
    if (!opt) return null;
    if (!seenValues.has(opt.value)) {
      seenValues.add(opt.value);
      matched.push(opt);
    }
  }
  return matched;
}

/** Human-readable "not one of: A, B, C" text for a bad-input error, so the failure names every
 * valid option instead of just rejecting silently. */
function optionsSummary(options) {
  return (Array.isArray(options) ? options : [])
    .filter(Boolean)
    .map((o) => o.label || o.value)
    .filter(Boolean)
    .join(", ");
}

/** The selector to act on for one option. Older recordings gave every radio/checkbox in a group the same
 * shared selector (`input[name="gender"]`), which matches all of them; when another option carries the
 * same selector, point at this one by group name + value instead. */
function optionSelector(option, choice) {
  const sel = String((option && option.selector) || "");
  const opts = Array.isArray(choice && choice.options) ? choice.options : [];
  const shared = sel && opts.some((o) => o && o !== option && o.selector === sel);
  const kind = choice && choice.kind;
  if (!shared || !choice.group_key || option.value == null || (kind !== "radio" && kind !== "checkbox")) return sel;
  const esc = (v) => String(v).replace(/[\\"]/g, "\\$&");
  return `input[type="${kind}"][name="${esc(choice.group_key)}"][value="${esc(option.value)}"]`;
}

module.exports = { matchOption, matchOptions, optionsSummary, optionSelector };
