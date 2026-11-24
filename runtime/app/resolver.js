"use strict";
const { fingerprintName, stripKeyboardShortcutTail } = require("./accessible_name");

// Pure, browser-independent element resolver (Phase 8 — Final Selector Architecture).
//
// resolve() walks IdentityBundle signals in durability order and applies a strict
// uniqueness gate: it never blindly picks candidate[0]. On ambiguity it scores each
// candidate against the recorded fingerprint and only accepts a winner when its margin
// over the runner-up clears `uniqueMargin`; otherwise it falls through to the next signal.
//
// The `root` argument must expose `queryAll(selector) -> node[]`. Each node must expose
// the fingerprint-comparable getters consumed by scoreCandidate (role, name, text, testid,
// stableHash, anchorNeighbors). This keeps the module unit-testable with mock roots.

const DEFAULT_CONFIDENCE_THRESHOLD = 0.5;
const DEFAULT_UNIQUE_MARGIN = 0.15;

function str(v) {
  return typeof v === "string" ? v : (v == null ? "" : String(v));
}

function norm(v) {
  return str(v).trim().toLowerCase();
}

// The compiled fingerprint's `role` is frequently the raw HTML tag (e.g. "input",
// "a", "select") rather than the computed ARIA role, while the live DOM extractor
// reports the implicit ARIA role ("textbox", "link", "combobox"). A naive string
// compare therefore reports a FALSE disagreement for every form control — which, when
// the fingerprint carries no other positive signal (empty data_testid/name/text),
// collapses the candidate's score to 0 and makes the resolver reject a uniquely
// testid-matched element. Treat tag names and their implicit roles as compatible.
const ROLE_ALIASES = {
  input:    ["textbox", "searchbox", "combobox", "spinbutton", "checkbox", "radio", "button"],
  textarea: ["textbox"],
  select:   ["combobox", "listbox"],
  a:        ["link"],
  button:   ["button"],
  img:      ["img"],
};

// Elements whose text is their concatenated <option> children. The compiler records this via
// innerText (whitespace BETWEEN options: "1900 1901 1902"); page_scripts.js's extractDescriptor
// reads textContent (no separator: "190019011902"). The two can never be equal or substrings of
// one another, so scoring inner_text for these elements contributes weight that is guaranteed
// never to be earned — which is exactly how a uniquely-matched react-datepicker year <select>
// scored 0.444 against a 0.5 threshold and was rejected despite being the only candidate.
// Mirrors identity_bundle.py's _OPTION_CONTENT_TAGS and bridge.js's OPTION_CONTENT_TAGS.
const OPTION_CONTENT_TAGS = new Set(["select", "datalist", "optgroup", "combobox", "listbox"]);

function roleAgrees(fpRole, nodeRole) {
  const f = norm(fpRole);
  const n = norm(nodeRole);
  if (!f || !n) return false;
  if (f === n) return true;
  if ((ROLE_ALIASES[f] || []).includes(n)) return true;
  if ((ROLE_ALIASES[n] || []).includes(f)) return true;
  return false;
}

// A "contract" signal is a structural identity that is unique by construction on a
// well-formed page (an explicit test id or a DOM id). A single such match is the
// strongest identity available — the fingerprint scorer exists to DISAMBIGUATE
// multiple matches and to guard low-durability text/xpath drift, not to VETO a unique
// contract match just because the recorded fingerprint is impoverished.
function isContractSignal(signal) {
  const engine = norm(signal && signal.engine);
  return engine === "testid" || engine === "css-id";
}

// The node positively CONTRADICTS the fingerprint only when a strong recorded field is
// present AND demonstrably different (not merely absent). Absence of agreement is not
// contradiction — that is the whole point of trusting a unique contract signal.
function contradicts(node, fingerprint) {
  const fp = fingerprint || {};
  const fpTestid = norm(fp.data_testid);
  if (fpTestid && node && norm(node.testid) && norm(node.testid) !== fpTestid) return true;
  return false;
}

// Weighted agreement between a candidate node and the recorded fingerprint.
// Returns a score in [0, 1]. Higher = stronger match.
// How well a live name/text agrees with the recorded one: the same words count fully, words merely contained in
// (or containing) the recorded ones count for less. Locators match contained-in on purpose (c320dba: a compiled
// "File upload" must still find "File upload Alt+C then U"), so this is where "Java" must beat "JavaScript" (WF-2 F).
// A keyboard-shortcut tail on the live side is dropped first, so that case is an exact match here. Contained-in alone
// stays under the confidence threshold: a look-alike with only a longer name is never clicked on text alone.
const CONTAINED_CREDIT = 0.4;
// A count shown and then repeated in brackets for screen readers ("Pull requests 58 (58)", "Issues 1.1k (1.1k)") is a
// live count, never part of the name: dropped on both sides, the recorded tab is still the same words (EXEC-67).
// Only the echoed form: a lone trailing number ("Page 2") may be what tells two links apart.
const COUNT_ECHO_RE = /\s+(\d[\d.,]*[km]?\+?)\s+\(\1\)$/i;
function textCredit(recorded, live) {
  const r = norm(recorded);
  const l = norm(stripKeyboardShortcutTail(str(live)));
  if (!r || !l) return 0;
  if (l === r) return 1;
  const rBare = r.replace(COUNT_ECHO_RE, "");
  if (rBare !== r && rBare && rBare === l.replace(COUNT_ECHO_RE, "")) return 1;
  return l.includes(r) || r.includes(l) ? CONTAINED_CREDIT : 0;
}

// A recorded "role" that is only the element's tag, with no role the live extractor can report for it ("li", "div"),
// cannot be compared with a live role: charging it made the recorded element itself fail its own fingerprint.
function comparableRole(fp) {
  const role = norm(fp.role);
  return !!role && !(role === norm(fp.tag) && !ROLE_ALIASES[role]);
}

// An anchor written as a selector ("ul#ui-id-1") is not neighbour text and can never agree with it.
function textAnchor(a) {
  return !!a && !/^[a-z0-9_-]*[#.[][^\s]*$/i.test(a);
}

function scoreCandidate(node, fingerprint) {
  if (!node) return 0;
  const fp = fingerprint || {};
  let score = 0;
  let weight = 0;

  const add = (w, agree) => { weight += w; score += w * (agree === true ? 1 : (typeof agree === "number" ? agree : 0)); };

  const fpTestid = norm(fp.data_testid);
  if (fpTestid) add(0.30, norm(node.testid) === fpTestid);

  if (comparableRole(fp)) add(0.20, roleAgrees(fp.role, node.role));

  // The recorded element's accessible name, by the one function the compiler's rule is mirrored in
  // (accessible_name.js; Python twin identity_bundle.py::_accessible_name).
  const fpName = norm(fingerprintName(fp));
  if (fpName) add(0.25, textCredit(fpName, node.name || node.text));

  const isOptionContent = OPTION_CONTENT_TAGS.has(norm(fp.tag)) || OPTION_CONTENT_TAGS.has(norm(fp.role));
  const fpText = norm(isOptionContent ? "" : fp.inner_text);
  if (fpText) add(0.15, textCredit(fpText, node.text));

  // Absence of neighbour text is not disagreement — the same principle contradicts() encodes.
  // extractDescriptor only collects neighbour text under 60 chars, so an element whose parent
  // and siblings are all long (a datepicker dropdown container, a dense table cell) reports NO
  // neighbours at all. Charging the anchor weight against such a candidate penalises it for a
  // limit of the extractor rather than for any observed mismatch.
  const anchors = Array.isArray(fp.anchor_phrases) ? fp.anchor_phrases.map(norm).filter(textAnchor) : [];
  const neighbors = Array.isArray(node.anchorNeighbors) ? node.anchorNeighbors.map(norm).filter(Boolean) : [];
  if (anchors.length && neighbors.length) {
    add(0.10, anchors.some(a => neighbors.some(n => n.includes(a) || a.includes(n))));
  }

  // Which section the element sits in (its last heading before it): charged only when both sides have one, so packs
  // recorded before it existed score as they did. Weighted so twins that agree on role, name and text still clear the
  // 0.15 margin when only their sections differ (WF-2 Segment J: two "Toggle Me" widgets).
  const fpHeading = norm(fp.section_heading);
  const nodeHeading = norm(node.sectionHeading);
  if (fpHeading && nodeHeading) add(0.15, fpHeading === nodeHeading);

  // Language-neutral identity (W1): the element's own id and form-field name, and its input type. They stay the
  // same when the page is translated, so a box whose visible words changed still agrees on what it is. Each is
  // charged only when both sides carry it, so a pack recorded before they existed scores as it did.
  const fpId = str(fp.element_id);
  if (fpId && node.elId) add(0.20, fpId === node.elId);
  const fpField = norm(fp.name);
  if (fpField && norm(node.nameAttr)) add(0.15, fpField === norm(node.nameAttr));
  const fpType = norm(fp.input_type);
  if (fpType && norm(node.inputType)) add(0.05, fpType === norm(node.inputType));
  // The caption tied to the control (label[for], enclosing label, aria-labelledby), recorded as label_text.
  if (norm(fp.label_text) && norm(node.label)) add(0.10, textCredit(fp.label_text, node.label));

  return weight > 0 ? score / weight : 0;
}

// True when the node's stable_hash exactly matches the recorded fingerprint's. A pack built before form fields were
// hashed by their own id/name/placeholder/type holds the older hash (legacyStableHash). The new input adds a
// `|field|` part, so an old hash and a new one do not match each other by accident.
function stableHashMatch(node, fingerprint) {
  const fp = fingerprint || {};
  if (!fp.stable_hash || !node) return false;
  return node.stableHash === fp.stable_hash || node.legacyStableHash === fp.stable_hash;
}

// resolve(signals, fingerprint, root, opts)
//   → { node, score, margin, signalUsed } | { miss: true } | { ambiguous: true, tied: [...] }
function resolve(signals, fingerprint, root, opts) {
  const options = opts || {};
  const confidenceThreshold = typeof options.confidenceThreshold === "number"
    ? options.confidenceThreshold : DEFAULT_CONFIDENCE_THRESHOLD;
  const uniqueMargin = typeof options.uniqueMargin === "number"
    ? options.uniqueMargin : DEFAULT_UNIQUE_MARGIN;

  if (!root || typeof root.queryAll !== "function") return { miss: true };

  const ordered = Array.isArray(signals)
    ? signals.filter(s => s && s.selector).slice().sort((a, b) => (b.durability || 0) - (a.durability || 0))
    : [];

  let sawAmbiguous = false;
  const tied = [];

  for (const signal of ordered) {
    let candidates;
    try {
      candidates = root.queryAll(signal.selector) || [];
    } catch (_) {
      continue;
    }
    if (!candidates.length) continue;

    if (candidates.length === 1) {
      const s = scoreCandidate(candidates[0], fingerprint);
      if (s >= confidenceThreshold) {
        return { node: candidates[0], score: s, margin: 1, signalUsed: signal };
      }
      // A unique contract signal (testid / DOM id) is ground-truth identity. Accept it
      // even when the impoverished fingerprint yields no positive agreement, as long as
      // the candidate does not actively contradict the recorded element. This is what
      // keeps the zero-token primary path alive for form controls whose compiled
      // fingerprint records the tag ("input") instead of the ARIA role and omits the
      // test id — without it, every such step silently degrades onto flaky recovery.
      if (isContractSignal(signal) && !contradicts(candidates[0], fingerprint)) {
        return { node: candidates[0], score: s, margin: 1, signalUsed: signal, trustedContract: true };
      }
      continue;
    }

    // Multi-match → uniqueness gate via scored margin.
    const scored = candidates
      .map(c => ({ c, s: scoreCandidate(c, fingerprint), h: stableHashMatch(c, fingerprint) }))
      .sort((a, b) => b.s - a.s);
    const margin = scored[0].s - (scored[1] ? scored[1].s : 0);
    if (margin >= uniqueMargin && scored[0].s >= confidenceThreshold) {
      return { node: scored[0].c, score: scored[0].s, margin, signalUsed: signal };
    }
    // Tie-break: exactly one candidate's stable_hash matches the recorded element.
    const hashMatches = scored.filter(x => x.h);
    if (hashMatches.length === 1 && hashMatches[0].s >= confidenceThreshold) {
      return { node: hashMatches[0].c, score: hashMatches[0].s, margin, signalUsed: signal, stableHashTieBreak: true };
    }
    sawAmbiguous = true;
    // Kept so the failure can say WHICH elements tied, not just that something did.
    tied.push({
      selector: signal.selector,
      matches: candidates.length,
      margin: Number(margin.toFixed(2)),
      top: scored.slice(0, 3).map(x => ({
        role: x.c.role, name: x.c.name || x.c.text, score: Number(x.s.toFixed(2)),
      })),
    });
    // still ambiguous → try next signal
  }

  return sawAmbiguous ? { ambiguous: true, tied } : { miss: true };
}

// Exported (read-only) so tests can pin the exact gate values — a silent drift
// here would loosen the "never pick candidate[0]" invariant everywhere at once.
module.exports = { resolve, scoreCandidate, DEFAULT_CONFIDENCE_THRESHOLD, DEFAULT_UNIQUE_MARGIN };
