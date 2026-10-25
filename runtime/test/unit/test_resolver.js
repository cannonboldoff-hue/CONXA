"use strict";

const test = require("node:test");
const assert = require("node:assert");

const { resolve, scoreCandidate } = require("../../app/resolver");

// Mock root: maps selector → node array.
function mockRoot(map) {
  return { queryAll: sel => map[sel] || [] };
}

const TESTID_SIG = { engine: "testid", selector: "internal:testid=[data-testid=\"submit\"]", durability: 0.99, orthogonality_class: "test-contract" };
const ROLE_SIG = { engine: "role", selector: "internal:role=button[name=\"Submit\"]", durability: 0.95, orthogonality_class: "semantic-aria" };
const TEXT_SIG = { engine: "text", selector: "internal:text=\"Submit\"", durability: 0.85, orthogonality_class: "visible-text" };

test("single match above threshold returns node", () => {
  const node = { testid: "submit", role: "button", name: "Submit", text: "Submit" };
  const root = mockRoot({ [TESTID_SIG.selector]: [node] });
  const fp = { data_testid: "submit", role: "button", aria_label: "Submit" };
  const r = resolve([TESTID_SIG], fp, root, {});
  assert.strictEqual(r.node, node);
  assert.strictEqual(r.signalUsed.engine, "testid");
});

test("durability order: testid tried before text", () => {
  const good = { testid: "submit", role: "button", name: "Submit", text: "Submit" };
  const root = mockRoot({
    [TESTID_SIG.selector]: [good],
    [TEXT_SIG.selector]: [good],
  });
  const fp = { data_testid: "submit", role: "button", aria_label: "Submit" };
  // Pass in reverse order to prove resolver re-sorts by durability.
  const r = resolve([TEXT_SIG, TESTID_SIG], fp, root, {});
  assert.strictEqual(r.signalUsed.engine, "testid");
});

test("multi-match below margin returns ambiguous", () => {
  // Two near-identical buttons → neither wins the margin gate.
  const a = { role: "button", name: "Save", text: "Save" };
  const b = { role: "button", name: "Save", text: "Save" };
  const root = mockRoot({ [ROLE_SIG.selector]: [a, b] });
  const fp = { role: "button", aria_label: "Save" };
  const r = resolve([ROLE_SIG], fp, root, { uniqueMargin: 0.15 });
  assert.strictEqual(r.ambiguous, true);
  // The tie is reported, so a failure can name the elements that tied.
  assert.strictEqual(r.tied.length, 1);
  assert.strictEqual(r.tied[0].selector, ROLE_SIG.selector);
  assert.strictEqual(r.tied[0].matches, 2);
  assert.strictEqual(r.tied[0].margin, 0);
  assert.deepStrictEqual(r.tied[0].top.map(c => c.name), ["Save", "Save"]);
});

test("multi-match with clear winner returns best node and margin", () => {
  const winner = { role: "button", name: "Submit order", text: "Submit order", data_testid: "" };
  const loser = { role: "button", name: "Cancel", text: "Cancel" };
  const root = mockRoot({ [ROLE_SIG.selector]: [loser, winner] });
  const fp = { role: "button", aria_label: "Submit order", inner_text: "Submit order" };
  const r = resolve([ROLE_SIG], fp, root, { uniqueMargin: 0.1 });
  assert.strictEqual(r.node, winner);
  assert.ok(r.margin >= 0.1);
});

test("no match across all signals returns miss", () => {
  const root = mockRoot({});
  const fp = { data_testid: "submit" };
  const r = resolve([TESTID_SIG, ROLE_SIG], fp, root, {});
  assert.strictEqual(r.miss, true);
});

test("falls through ambiguous signal to a later unique signal", () => {
  const a = { role: "button", name: "Save", text: "Save" };
  const b = { role: "button", name: "Save", text: "Save" };
  const unique = { testid: "save-btn", role: "button", name: "Save", text: "Save" };
  const root = mockRoot({
    [ROLE_SIG.selector]: [a, b],          // ambiguous
    [TESTID_SIG.selector]: [unique],      // unique
  });
  const fp = { data_testid: "save-btn", role: "button", aria_label: "Save" };
  // testid has higher durability, so it's tried first and resolves uniquely.
  const r = resolve([ROLE_SIG, TESTID_SIG], fp, root, {});
  assert.strictEqual(r.node, unique);
  assert.strictEqual(r.signalUsed.engine, "testid");
});

test("stable_hash match acts as tie-breaker", () => {
  const a = { role: "button", name: "Go", text: "Go", stableHash: "hashA" };
  const b = { role: "button", name: "Go", text: "Go", stableHash: "hashB" };
  const root = mockRoot({ [ROLE_SIG.selector]: [a, b] });
  const fp = { role: "button", aria_label: "Go", stable_hash: "hashB" };
  const r = resolve([ROLE_SIG], fp, root, { uniqueMargin: 0.1 });
  assert.strictEqual(r.node, b);
});

test("scoreCandidate rewards testid agreement", () => {
  const fp = { data_testid: "x", role: "button", aria_label: "X" };
  const match = scoreCandidate({ testid: "x", role: "button", name: "X" }, fp);
  const mismatch = scoreCandidate({ testid: "y", role: "button", name: "X" }, fp);
  assert.ok(match > mismatch);
});

// Regression (audit H-3): a label-less search box is named from its placeholder at compile
// time (identity_bundle.py's aria_label||name||inner_text||placeholder). Without placeholder
// in fpName, a correct candidate lost its name-match bonus entirely.
test("scoreCandidate rewards placeholder-derived name agreement", () => {
  const fp = { role: "combobox", aria_label: "", name: "", inner_text: "", placeholder: "Search" };
  const match = scoreCandidate({ role: "combobox", name: "Search", text: "" }, fp);
  const mismatch = scoreCandidate({ role: "combobox", name: "Filter", text: "" }, fp);
  assert.ok(match > mismatch);
});

test("invalid root returns miss", () => {
  const r = resolve([TESTID_SIG], {}, null, {});
  assert.strictEqual(r.miss, true);
});

// Regression: form control whose compiled fingerprint records the TAG ("input") as the
// role and omits data_testid. The implicit ARIA role is "textbox" and the only positive
// signal is a unique testid. Pre-fix this scored 0 and resolved to {miss}, silently
// degrading every input step onto flaky string-selector recovery.
test("unique testid input resolves despite tag-role and empty fingerprint testid", () => {
  const INPUT_SIG = { engine: "testid", selector: "internal:testid=[data-testid=\"repo-input\"]", durability: 0.99, orthogonality_class: "test-contract" };
  const node = { role: "textbox", name: "", text: "", testid: "repo-input", anchorNeighbors: [] };
  const root = mockRoot({ [INPUT_SIG.selector]: [node] });
  const fp = { role: "input", data_testid: "", aria_label: "", name: "", inner_text: "" };
  const r = resolve([INPUT_SIG], fp, root, {});
  assert.strictEqual(r.node, node);
  assert.strictEqual(r.signalUsed.engine, "testid");
});

// A unique contract signal must still be REJECTED when the node positively contradicts
// the recorded fingerprint (different non-empty testid) — absence of agreement is trusted,
// active contradiction is not.
test("unique testid match is rejected when node testid contradicts fingerprint", () => {
  const SIG = { engine: "testid", selector: "internal:testid=[data-testid=\"a\"]", durability: 0.99 };
  const node = { role: "textbox", testid: "different-id" };
  const root = mockRoot({ [SIG.selector]: [node] });
  const fp = { role: "input", data_testid: "expected-id" };
  const r = resolve([SIG], fp, root, {});
  assert.strictEqual(r.miss, true);
});

// css-id is also a contract signal: #name with tag-role "input" and a label-derived name.
test("css-id input resolves via role-alias agreement", () => {
  const SIG = { engine: "css-id", selector: "#name", durability: 0.45, orthogonality_class: "structural" };
  const node = { role: "textbox", name: "name", text: "", testid: "" };
  const root = mockRoot({ [SIG.selector]: [node] });
  const fp = { role: "input", name: "name", data_testid: "" };
  const r = resolve([SIG], fp, root, {});
  assert.strictEqual(r.node, node);
});

// the-internet.herokuapp.com/hovers regression. A bare <img> has no testid, no text and no
// name attribute, so its only surviving signal is structural — and the ONLY fingerprint field
// that can score it is `role`. extractDescriptor's implicit-role table used to cover a/button/
// input only, so the live candidate reported role "" and roleAgrees() returned false for the
// empty side. The one signal that actually FOUND the element scored 0/0.30 and was rejected
// below threshold, and the hover step failed with "element not found".
test("nameless img resolves on role agreement alone", () => {
  const SIG = { engine: "xpath", selector: "xpath=/html/body/div[2]/div/div/div[2]/img", durability: 0.01, orthogonality_class: "structural" };
  const node = { role: "img", name: "User Avatar", text: "", testid: "", anchorNeighbors: [] };
  const root = mockRoot({ [SIG.selector]: [node] });
  const fp = { role: "img", tag: "img", alt: "User Avatar", anchor_phrases: [] };
  const r = resolve([SIG], fp, root, {});
  assert.strictEqual(r.node, node);
});

test("empty node role never agrees with a named fingerprint role", () => {
  // Guards the roleAgrees() empty-side rule directly: if a future tag is missing from
  // extractDescriptor's table, the score collapses rather than matching by accident.
  assert.strictEqual(scoreCandidate({ role: "", name: "", text: "" }, { role: "img" }), 0);
});

// alt is an <img>'s accessible name — the fingerprint must be able to match a live
// descriptor named from it, or the image scores on role alone and sits one weight away
// from the threshold.
test("alt participates in fingerprint name matching", () => {
  const s = scoreCandidate(
    { role: "img", name: "User Avatar", text: "", testid: "" },
    { role: "img", alt: "User Avatar" },
  );
  assert.strictEqual(s, 1);
});

// Mirrors identity_bundle.py's _NAME_FROM_CONTENT_ROLES gate: a combobox never gets its
// accessible name from its own inner text, so scoring must not treat a node whose `.name`
// happens to equal that concatenated option-list text as a "name agreement" — that would
// disagree with the compiler, which refuses to emit a role signal built from the same
// fabricated name in the first place. Regression for react-datepicker's nameless year
// <select>, whose <option> text ("1900 1901 1902 ... 1915") is not an accessible name.
test("inner_text does not participate in name matching for a nameless combobox", () => {
  const optionListText = "1900 1901 1902 1903 1904 1905 1906 1907 1908 1909 1910";
  const fp = { tag: "select", role: "combobox", inner_text: optionListText };
  const nodeMatchingOptionList = { role: "combobox", name: optionListText, text: "unrelated" };
  const nodeNotMatching = { role: "combobox", name: "something else entirely", text: "unrelated" };
  // Before the gate: nodeMatchingOptionList would score higher (fabricated name agreement).
  // After: neither gets a name-signal contribution, so both score identically on role alone.
  assert.strictEqual(
    scoreCandidate(nodeMatchingOptionList, fp),
    scoreCandidate(nodeNotMatching, fp),
  );
});

// Control: the same inner_text DOES still participate in name matching for a role that is
// genuinely named from its content (a button), proving the gate is role-specific, not a
// blanket removal of inner_text from name matching.
test("inner_text still participates in name matching for a name-from-content role (button)", () => {
  const s = scoreCandidate(
    { role: "button", name: "Submit", text: "Submit", testid: "" },
    { tag: "button", role: "button", inner_text: "Submit" },
  );
  assert.strictEqual(s, 1);
});

// ── Popup-listbox / <select> scoring (demoqa react-datepicker regression) ───────────────
//
// A react-datepicker year <select> has no testid, no accessible name and no neighbour text
// short enough for extractDescriptor to collect. Its ONLY agreeing field is `role`. Two
// weights were nonetheless being charged against it and could never be earned:
//   - inner_text: the compiler records innerText ("1900 1901 1902"), the live descriptor
//     reads textContent ("190019011902") — never equal, never a substring of the other.
//   - anchor_phrases: node.anchorNeighbors comes back EMPTY, and absence is not disagreement.
// Result: 0.20/0.45 = 0.444 against a 0.5 threshold, so a UNIQUELY matched element was
// rejected and the step failed as a resolve miss.

const YEAR_TEXT_SPACED = "1900 1901 1902 1903 1904";
const YEAR_TEXT_DENSE = "19001901190219031904";

const SELECT_FP = {
  role: "combobox",
  tag: "select",
  inner_text: YEAR_TEXT_SPACED,
  data_testid: "",
  aria_label: "",
  name: "",
  placeholder: "",
  anchor_phrases: ["Practice Form", "Choose Date"],
};

const SELECT_NODE = {
  role: "combobox",
  name: YEAR_TEXT_DENSE,
  text: YEAR_TEXT_DENSE,
  testid: "",
  anchorNeighbors: [],
};

test("a <select> matched only by role clears the confidence threshold", () => {
  const s = scoreCandidate(SELECT_NODE, SELECT_FP);
  assert.ok(s >= 0.5, `expected >= 0.5, got ${s}`);
});

test("option-content inner_text is not scored for a <select>", () => {
  // Whether the live text agrees or not must make no difference — the field is not scored.
  const agreeing = scoreCandidate({ ...SELECT_NODE, text: YEAR_TEXT_SPACED }, SELECT_FP);
  const disagreeing = scoreCandidate(SELECT_NODE, SELECT_FP);
  assert.strictEqual(agreeing, disagreeing);
});

test("inner_text is still scored for a name-from-content role", () => {
  const fp = { role: "button", tag: "button", inner_text: "Submit", anchor_phrases: [] };
  const agreeing = scoreCandidate({ role: "button", text: "Submit", anchorNeighbors: [] }, fp);
  const disagreeing = scoreCandidate({ role: "button", text: "Cancel", anchorNeighbors: [] }, fp);
  assert.ok(agreeing > disagreeing, "button inner_text must still carry weight");
});

test("absent neighbour text is not counted as anchor disagreement", () => {
  const fp = { role: "button", tag: "button", inner_text: "Submit", anchor_phrases: ["Practice Form"] };
  const noNeighbours = scoreCandidate({ role: "button", text: "Submit", anchorNeighbors: [] }, fp);
  const wrongNeighbours = scoreCandidate(
    { role: "button", text: "Submit", anchorNeighbors: ["Totally Unrelated"] }, fp);
  assert.ok(noNeighbours > wrongNeighbours,
    "an element with no collectable neighbours must not be penalised like one that contradicts");
});

test("agreeing neighbour text still beats contradicting neighbour text", () => {
  const fp = { role: "button", tag: "button", inner_text: "Submit", anchor_phrases: ["Practice Form"] };
  const right = scoreCandidate({ role: "button", text: "Submit", anchorNeighbors: ["Practice Form"] }, fp);
  const wrong = scoreCandidate({ role: "button", text: "Submit", anchorNeighbors: ["Nope"] }, fp);
  assert.ok(right > wrong);
});

test("a uniquely matched <select> resolves instead of missing", () => {
  const sig = { engine: "css-structural", selector: "select.react-datepicker__year-select", durability: 0.3, orthogonality_class: "structural" };
  const r = resolve([sig], SELECT_FP, mockRoot({ [sig.selector]: [SELECT_NODE] }));
  assert.ok(r.node, `expected a hit, got ${JSON.stringify(r)}`);
});
