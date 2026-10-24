"use strict";

// Browser-side adapter that lets the pure resolver (resolver.js) run against a live Playwright
// page. The pure resolve() expects a synchronous root.queryAll(selector); the DOM is async, so we
// pre-gather candidate descriptors per signal, then hand resolve() a synchronous map view.

const crypto = require("crypto");
const { extractDescriptor: _extractDescriptor } = require("./page_scripts");
const { evalOn, EVAL_TIMED_OUT } = require("./page_eval");

// Build a getByRole locator from a `role=<role>[name="<name>"]` grammar string — the
// `internal:` prefix is optional so this also handles the display-form string
// (selector_grammar.py::signal_to_display) that ends up in target.primary_selector /
// the compiled step's top-level "selector" field.
// Quoted names in Playwright's *parser* are exact, but we do not re-parse through that
// parser — we call getByRole. Leave exact at Playwright's default (substring): compile
// strips trailing accelerator hints ("File upload Alt+C then U" → "File upload"), and the
// live accessible name still has the hint. exact:true then matches nothing. Ambiguous
// substring hits (Blueprint vs Blueprints) are the resolver uniqueness/margin gate's job.
function roleLocator(root, raw) {
  const rm = String(raw).match(/(?:internal:)?role=([a-zA-Z]+)(?:\[name="([^"]*)"\])?/);
  if (!rm) return null;
  return rm[2] ? root.getByRole(rm[1], { name: rm[2] }) : root.getByRole(rm[1]);
}

// Build a getByText locator from an `internal:text="…"` / `text="…"` grammar string —
// same optional-prefix tolerance as roleLocator, and the same substring/case-insensitive
// default so a compile-time-stripped accelerator hint doesn't break an exact match.
function textLocator(root, raw) {
  const s = String(raw);
  let tm = s.match(/internal:text="([^"]*)"/);
  if (!tm) tm = s.match(/^text=["']?(.+?)["']?$/);
  return tm ? root.getByText(tm[1]) : null;
}

// Engine-agnostic: resolve any selector string (compiled `internal:` grammar, or its
// unprefixed display form) the same tolerant way, regardless of which runtime path holds
// it. Used by the primary resolver (via signalToLocator below) and by any other consumer
// that only has a raw string — recovery's explicit-selector re-runs, drift detection —
// so a role/text selector never means "exact match" in one place and "substring" in
// another depending on which code happened to receive it.
function toLocator(root, raw) {
  return roleLocator(root, raw) || textLocator(root, raw) || root.locator(raw);
}

// ── Signal → Playwright locator ────────────────────────────────────────────
// Map a compiled IdentitySignal to a Playwright locator builder. We key off signal.engine and
// parse role/name/testid/text out of the Playwright `internal:` grammar, rather than relying on
// Playwright to parse internal: strings (not a public guarantee).
function signalToLocator(root, signal, interpolate, inputs) {
  const engine = String(signal.engine || "");
  const raw = interpolate(String(signal.selector || ""), inputs);
  if (!raw) return null;

  if (engine === "testid") {
    // Honour the page's actual attribute name (data-test-id vs data-testid). Playwright's
    // getByTestId is hard-wired to data-testid with no hyphen, so a hyphenated attribute would
    // silently match nothing and drop the signal. The compiler's to_playwright_grammar always
    // emits this as a literal [attr="value"] CSS selector after the internal: prefix (see
    // selector_grammar.py) — use root.locator() on it directly so the exact attribute name is
    // preserved.
    return root.locator(raw.replace(/^internal:testid=/, "").trim());
  }
  if (engine === "role") {
    return roleLocator(root, raw) || root.locator(raw);
  }
  if (engine === "text_based") {
    return textLocator(root, raw) || root.locator(raw);
  }
  if (engine === "relational") {
    // Playwright has no `right-of=`/`left-of=`/… chain engine (the recorded `>> right-of=…`
    // serialization is not a parseable selector — only the CSS `:right-of()` pseudo exists,
    // and it cannot take a role-based base). Resolve the durable base role+name instead; the
    // resolver's fingerprint scoring + uniqueness gate disambiguates among siblings, which is
    // what the spatial anchor was meant to do anyway.
    const base = raw.split(">>")[0].trim();
    return roleLocator(root, base) || root.locator(base);
  }
  if (engine === "xpath") {
    return root.locator(raw.startsWith("xpath=") ? raw : ("xpath=" + raw));
  }
  if (engine === "attr") {
    return root.locator(raw);
  }
  // css-id, css-structural, css
  return root.locator(raw);
}

// _extractDescriptor is imported from page_scripts.js (runs in the browser context — see that
// file's header for why it lives in its own, less-obfuscated module).

function _sha256(s) {
  return crypto.createHash("sha256").update(s).digest("hex");
}

// Gather candidate descriptors for every signal. Returns { [signal.selector]: descriptor[] },
// each descriptor carrying its live locator (`_loc`) so the caller can act on the winner.
async function gatherCandidates(roots, signals, interpolate, inputs, perSignalCap = 25) {
  const map = {};
  for (const sig of signals) {
    const descriptors = [];
    for (const root of roots) {
      let locator;
      try { locator = signalToLocator(root, sig, interpolate, inputs); } catch (_) { locator = null; }
      if (!locator) continue;
      let all = [];
      try { all = await locator.all(); } catch (_) { all = []; }
      for (const item of all.slice(0, perSignalCap)) {
        let d;
        try { d = await evalOn(item, _extractDescriptor); } catch (_) { continue; }
        if (!d || d === EVAL_TIMED_OUT) continue;
        d.stableHash = d._hashPayload ? _sha256(d._hashPayload) : "";
        delete d._hashPayload;
        d._loc = item;
        descriptors.push(d);
      }
    }
    map[sig.selector] = descriptors;
  }
  return map;
}

// The fields scoreCandidate compares against a candidate's visible text / accessible name.
const TEXT_FINGERPRINT_FIELDS = ["inner_text", "aria_label", "name", "title", "alt", "label_text", "placeholder"];

// Shape the bundle's fingerprint into what resolver.scoreCandidate expects. A clicked item that
// changes each run (BUILD-49) carries `{{input}}` in its text signals AND the same token in these
// fingerprint fields, so the score compares the candidate against THIS run's text; the recorded
// stable_hash describes the recorded item, so it is dropped (no hash tie-break) for such a bundle.
function bundleFingerprint(bundle, interpolate, inputs) {
  const fp = (bundle && bundle.fingerprint) || {};
  const out = { ...fp, stable_hash: (bundle && bundle.stable_hash) || "" };
  if (typeof interpolate !== "function") return out;
  let templated = false;
  for (const f of TEXT_FINGERPRINT_FIELDS) {
    if (typeof out[f] === "string" && out[f].includes("{{")) {
      out[f] = interpolate(out[f], inputs || {});
      templated = true;
    }
  }
  if (templated) out.stable_hash = "";
  return out;
}

module.exports = {
  signalToLocator, gatherCandidates, bundleFingerprint, _extractDescriptor, _sha256,
  roleLocator, textLocator, toLocator,
};
