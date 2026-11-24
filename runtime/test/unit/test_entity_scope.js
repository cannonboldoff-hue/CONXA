"use strict";
// PROD-3 entity binding — runtime\app\resolution.js::entityRoots / isEntityNotFound.

const test = require("node:test");
const assert = require("node:assert");

const { entityRoots, isEntityNotFound } = require("../../app/resolution");

// Minimal Playwright-shaped fake: root.locator(sel) -> a "rows" locator supporting .count(), .evaluateAll() and
// .nth(), the real chain entityRoots drives.
// A row is "cell | cell | cell": each cell is an element, the row's text is the cells joined by tabs. The real page
// script (pageScripts.rowsHoldingWholeValue) runs over these fake elements.
function fakeRow(text) {
  const cells = text.split(" | ");
  return { innerText: cells.join("\t"), querySelectorAll: () => cells.map(c => ({ tagName: "TD", innerText: c })) };
}
function fakeRoot(rowTexts) {
  return {
    locator(containerSelector) {
      return {
        _containerSelector: containerSelector,
        async count() { return rowTexts.length; },
        async evaluateAll(fn, arg) { return fn(rowTexts.map(fakeRow), arg); },
        nth(i) { return { _row: rowTexts[i] }; },
      };
    },
  };
}

const STEP = {
  entity_binding: { container_selector: "table#invoices tr", identifier: "Invoice #12345", source: "literal", confirmed: true },
};

test("no binding on the step: roots pass through unchanged", async () => {
  const roots = [fakeRoot(["Invoice #1"])];
  const out = await entityRoots(roots, { entity_binding: null }, {});
  assert.strictEqual(out, roots);
});

test("exactly one match: narrows to that one row", async () => {
  const root = fakeRoot(["Invoice #12344", "Invoice #12345", "Invoice #12346"]);
  const out = await entityRoots([root], STEP, {});
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0]._row, "Invoice #12345");
});

test("zero matches: fails closed to []", async () => {
  const root = fakeRoot(["Invoice #99999"]);
  const out = await entityRoots([root], STEP, {});
  assert.deepStrictEqual(out, []);
});

test("two matches (ambiguous): fails closed to [], never picks one", async () => {
  const root = fakeRoot(["Invoice #12345 | draft", "Invoice #12345 | final"]);
  const out = await entityRoots([root], STEP, {});
  assert.deepStrictEqual(out, []);
});

test("unresolved {{input}} identifier: fails closed without querying the page", async () => {
  const step = { entity_binding: { container_selector: "table tr", identifier: "{{invoice_no}}" } };
  const out = await entityRoots([fakeRoot(["anything"])], step, {});
  assert.deepStrictEqual(out, []);
});

test("identifier resolves from a run input via interpolate", async () => {
  // the input is the typed part of the cell's text (compiler/entity_binding.py::template_identifier)
  const step = { entity_binding: { container_selector: "table tr", identifier: "Invoice #{{invoice_no}}" } };
  const root = fakeRoot(["Invoice #55555"]);
  const out = await entityRoots([root], step, { invoice_no: "55555" });
  assert.strictEqual(out.length, 1);
});

test("isEntityNotFound: true only when a binding exists and narrowing produced zero roots", () => {
  assert.strictEqual(isEntityNotFound(STEP, []), true);
  assert.strictEqual(isEntityNotFound(STEP, [{}]), false);
  assert.strictEqual(isEntityNotFound({ entity_binding: null }, []), false);
});

test("multiple frame-scoped roots: each narrowed independently, non-matching roots drop out", async () => {
  const rootA = fakeRoot(["Invoice #99999"]);       // no match
  const rootB = fakeRoot(["Invoice #12345"]);        // exactly one match
  const out = await entityRoots([rootA, rootB], STEP, {});
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0]._row, "Invoice #12345");
});

// on_missing: "skip" may only ever act on a PROVABLE absence -- the list is on the page and none of its
// records is this run's. Everything else (ambiguous, empty/unrendered list) must still fail closed.
test("absent is true only when the list is there and holds no matching record", async () => {
  const none = await entityRoots([fakeRoot(["Invoice #1", "Invoice #2"])], STEP, {});
  assert.deepStrictEqual(none, []);
  assert.strictEqual(none.absent, true);

  const ambiguous = await entityRoots([fakeRoot(["Invoice #12345 | a", "Invoice #12345 | b"])], STEP, {});
  assert.deepStrictEqual(ambiguous, []);
  assert.strictEqual(ambiguous.absent, false);                         // more than one match is not "absent"

  const emptyList = await entityRoots([fakeRoot([])], STEP, {});
  assert.strictEqual(emptyList.absent, false);                         // an unrendered list proves nothing

  const found = await entityRoots([fakeRoot(["Invoice #12345"])], STEP, {});
  assert.strictEqual(found.length, 1);
  assert.strictEqual(found.absent, false);

  const unresolved = await entityRoots([fakeRoot(["Invoice #1"])], { entity_binding: { ...STEP.entity_binding, identifier: "{{inv}}" } }, {});
  assert.deepStrictEqual(unresolved, []);
  assert.notStrictEqual(unresolved.absent, true);                      // an input nobody supplied is not a missing record
});

// WF-7 S2: "more than one row shows it" is told apart from "it isn't there", so the person is not told a duplicate
// record "wasn't found". Neither case ever acts on a row.
test("several is true only when more than one row shows the record", async () => {
  const dupes = await entityRoots([fakeRoot(["Invoice #12345 | a", "Invoice #12345 | b"])], STEP, {});
  assert.deepStrictEqual(dupes, []);
  assert.strictEqual(dupes.several, true);
  assert.strictEqual(dupes.absent, false);

  const none = await entityRoots([fakeRoot(["Invoice #1"])], STEP, {});
  assert.strictEqual(none.several, false);
  const found = await entityRoots([fakeRoot(["Invoice #12345"])], STEP, {});
  assert.strictEqual(found.several, false);
});

// WF-7 7.6: a search that finds nothing leaves only a look-alike row elsewhere ("Recently edited"). The step bound to
// the searched value fails before acting, and says the record isn't on the page.
test("resolveStep refuses a look-alike row when the searched record is not on the page", async () => {
  const { resolveStep } = require("../../app/resolution");
  const step = {
    entity_binding: { container_selector: "tbody > tr", identifier: "{{search_by_name}}", source: "input" },
    identity_bundle: { signals: [{ selector: "internal:text=\"Edit\"", durability: 0.85 }], fingerprint: {}, frame_chain: [] },
  };
  const page = fakeRoot(["Eve | eve@example.com | Edit Delete"]);
  await assert.rejects(resolveStep(page, step, { search_by_name: "zzzqqq" }),
    (err) => err.entityNotFound === true && err.entityAbsent === true && /could not be uniquely located/.test(err.message));
  const twice = fakeRoot(["Carol | carol@example.com | Edit", "Carol | carol@example.com | Edit"]);
  await assert.rejects(resolveStep(twice, step, { search_by_name: "Carol" }),
    (err) => err.entityNotFound === true && err.entitySeveral === true && /more than one row/.test(err.message));
});

// WF-7 7.swap: with Carol's row gone and "Carol Ann" in its place, `hasText` ("Carol" is in "Carol Ann") picked
// Carol Ann's row and the test passed. A row counts only when a cell (or the row's whole text, a for_each row) is the
// value itself, compared without case or extra spaces.
test("a longer look-alike is not the bound row: Carol Ann never stands in for Carol", async () => {
  const step = { entity_binding: { container_selector: "tbody > tr", identifier: "Carol" } };
  const swapped = await entityRoots([fakeRoot(["Alice | a@x | Edit", "Carol Ann | ca@x | Edit"])], step, {});
  assert.deepStrictEqual(swapped, []);
  assert.strictEqual(swapped.absent, true);                            // provably absent, not "more than one"
  const both = await entityRoots([fakeRoot(["Carol Ann | ca@x | Edit", "carol  | c@x | Edit"])], step, {});
  assert.strictEqual(both.length, 1);
  assert.strictEqual(both[0]._row, "carol  | c@x | Edit");             // case and spaces do not matter
  const whole = await entityRoots([fakeRoot(["Bob | b@x", "Carol | c@x"])],
    { entity_binding: { container_selector: "tr", identifier: "Carol\tc@x" } }, {});
  assert.strictEqual(whole.length, 1);                                 // a for_each row: its own whole text
});

// BUILD-117 (8): "WF1 B secure area logout r4" (compiled 2026-11-16) binds Logout to `body > div.row` "Logged in as".
// The compiler names a row by one run of text, and here that run sits beside an element:
// <p>Logged in as <span id="username">tomsmith</span></p>. Matching whole elements only (WF-7) never found it.
const text = (v) => ({ nodeType: 3, nodeValue: v });
const el = (tagName, kids) => ({
  tagName, childNodes: kids,
  innerText: kids.map(k => k.nodeType === 3 ? k.nodeValue : k.innerText).join(""),
  querySelectorAll: () => kids.filter(k => k.nodeType !== 3).flatMap(k => [k, ...k.querySelectorAll()]),
});
const logoutRows = (who) => [
  el("DIV", [el("DIV", [])]),                                            // the flash-messages row
  el("DIV", [el("DIV", [el("H2", [text("Secure Area")]),
    el("P", [text("Logged in as "), el("SPAN", [text(who)])]), el("A", [text(" Logout")])])]),
];
const rowsRoot = (rows) => ({ locator: () => ({ count: async () => rows.length, evaluateAll: async (fn, a) => fn(rows, a), nth: (i) => ({ _row: i }) }) });

test("a run of text beside an element names its row: the old logout workflow finds its row (BUILD-117 (8))", async () => {
  const step = { entity_binding: { container_selector: "body > div.row", identifier: "Logged in as", source: "literal", confirmed: false } };
  const out = await entityRoots([rowsRoot(logoutRows("tomsmith"))], step, {});
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0]._row, 1);
  // still a whole value: part of a run is not the run
  const part = await entityRoots([rowsRoot(logoutRows("tomsmith"))], { entity_binding: { ...step.entity_binding, identifier: "Logged in" } }, {});
  assert.deepStrictEqual(part, []);
});

// ── "the suggestion that shows what was typed" (entity_binding.match "contains", BUILD-102 (6)) ─────────────────────
const PICK = (extra = {}) => ({
  entity_binding: { container_selector: "[role=option]", identifier: "{{name}}", source: "input", match: "contains", self_target: true, ...extra },
});

test("contains: the one suggestion that holds the typed words is the match, case and spacing aside", async () => {
  const root = fakeRoot(["John Smith", "Amelia   Brown", "Johnny Cash"]);
  const out = await entityRoots([root], PICK({ confirmed: true }), { name: "amelia" });
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0]._row, "Amelia   Brown");
});

test("contains: two suggestions that hold the words fail closed, none either", async () => {
  const root = fakeRoot(["John Smith", "John Doe", "Johnny Cash"]);
  assert.deepStrictEqual(await entityRoots([root], PICK({ confirmed: true }), { name: "john" }), []);
  assert.deepStrictEqual(await entityRoots([root], PICK({ confirmed: true }), { name: "zed" }), []);
});

test("contains: words inside another word are not a match (Car is not Carol)", async () => {
  const root = fakeRoot(["Carol Ann"]);
  assert.deepStrictEqual(await entityRoots([root], PICK({ confirmed: true }), { name: "car" }), []);
});

test("contains: until the person said yes the binding does nothing", async () => {
  const roots = [fakeRoot(["John Smith", "Johnny Cash"])];
  const step = PICK({ confirmed: false });
  assert.strictEqual(await entityRoots(roots, step, { name: "john" }), roots);
  assert.strictEqual(isEntityNotFound(step, []), false);
});

test("whole-value bindings are unchanged by the new field", async () => {
  const root = fakeRoot(["Carol Ann", "Carol"]);
  const step = { entity_binding: { container_selector: "tr", identifier: "Carol", source: "literal", match: "whole" } };
  const out = await entityRoots([root], step, {});
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0]._row, "Carol");
});

// ── "Always the first result?" (entity_binding.position, BUILD-101 (7)) ─────────────────────────────────────────────
const PLACE = (extra = {}) => ({ entity_binding: { container_selector: "ul.results > li", identifier: "", position: 1, ...extra } });
function listsRoot(lists) { // rows spread over `lists` parents
  const rows = lists.flatMap((n, p) => Array.from({ length: n }, (_, i) => ({ parentElement: `list${p}`, _row: `${p}:${i}` })));
  return {
    locator() {
      return { async count() { return rows.length; }, async evaluateAll(fn, arg) { return fn(rows, arg); }, nth(i) { return rows[i]; } };
    },
  };
}

test("place: until the person said yes the binding does nothing", async () => {
  const roots = [listsRoot([3])];
  assert.strictEqual(await entityRoots(roots, PLACE(), {}), roots);
  assert.strictEqual(isEntityNotFound(PLACE(), []), false);
});

test("place: once confirmed, the Nth row of the one list is the scope", async () => {
  const out = await entityRoots([listsRoot([3])], PLACE({ confirmed: true, position: 2 }), {});
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0]._row, "0:1");
});

test("place: two lists, too few rows or two frames fail closed, never absent", async () => {
  const two = await entityRoots([listsRoot([2, 2])], PLACE({ confirmed: true }), {});
  assert.deepStrictEqual(two, []);
  assert.notStrictEqual(two.absent, true);
  assert.deepStrictEqual(await entityRoots([listsRoot([1])], PLACE({ confirmed: true, position: 2 }), {}), []);
  assert.deepStrictEqual(await entityRoots([listsRoot([2]), listsRoot([2])], PLACE({ confirmed: true }), {}), []);
  assert.strictEqual(isEntityNotFound(PLACE({ confirmed: true }), []), true);
});

test("place: a binding with words keeps matching by its words", async () => {
  const out = await entityRoots([fakeRoot(["Carol", "Bob"])], PLACE({ confirmed: true, identifier: "Bob", position: 1 }), {});
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0]._row, "Bob");
});
