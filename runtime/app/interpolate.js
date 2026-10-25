"use strict";
// Input interpolation for skill steps, extracted from run.js. Pure.
//
// Grammar must match conxa_compile/editor/placeholder_grammar.py PLACEHOLDER_RE exactly —
// a looser runtime grammar let hyphenated/spaced {{ids}} interpolate here while staying
// invisible to the compiler/UI scanners and always resolving to "" (audit finding C3).
const PLACEHOLDER_SOURCE = "\\{\\{\\s*([a-zA-Z][a-zA-Z0-9_]*)\\s*\\}\\}";

function interpolate(value, inputs) {
  if (typeof value !== "string") return value;
  return value.replace(new RegExp(PLACEHOLDER_SOURCE, "g"), (_, key) => String(inputs[key] ?? ""));
}

// Fields that describe a step to a reader rather than drive it: a {{id}} named there is not
// something the step's execution depends on.
const PROSE_KEYS = new Set(["description", "label", "intent", "title", "note", "notes", "why", "_recorded_context", "_anchor_sentence"]);

// Which run inputs a step's execution reads, and whether each was empty. Walks every string the
// step carries (selectors, signals, fingerprint, values, urls, branch bodies...) so it holds for
// any step type without a per-type list. Used to blame a failure on an input before blaming the
// step. Never returns the VALUES: the result is written to failure evidence that is read back
// into an LLM prompt, and an input may be a password. The caller that needs a value already has it.
function inputsReferenced(step, inputs) {
  const ids = new Set();
  const seen = new Set();
  const walk = (node) => {
    if (typeof node === "string") {
      for (const m of node.matchAll(new RegExp(PLACEHOLDER_SOURCE, "g"))) ids.add(m[1]);
    } else if (node && typeof node === "object" && !seen.has(node)) {
      seen.add(node);
      for (const [k, v] of Array.isArray(node) ? node.entries() : Object.entries(node)) {
        if (!PROSE_KEYS.has(k)) walk(v);
      }
    }
  };
  walk(step);
  const given = inputs && typeof inputs === "object" ? inputs : {};
  return [...ids].map(id => ({ id, empty: given[id] === undefined || given[id] === null || given[id] === "" }));
}

// Names of declared inputs whose value fails their own `pattern`. Names only, never values (an input
// may be a password). Search semantics, like the Studio's check; a pattern that does not compile, an
// empty value and a list value say nothing about the input and are skipped.
function inputsFailingPattern(declared, inputs) {
  const out = [];
  for (const f of declared || []) {
    const v = inputs && f && f.name ? inputs[f.name] : undefined;
    if (!f || !f.pattern || typeof v === "object" || v === undefined || v === null || String(v) === "") continue;
    let re;
    try { re = new RegExp(f.pattern); } catch (_) { continue; }
    if (!re.test(String(v))) out.push(f.name);
  }
  return out;
}

module.exports = { interpolate, inputsReferenced, inputsFailingPattern };
