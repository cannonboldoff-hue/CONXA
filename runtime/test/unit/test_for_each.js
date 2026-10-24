"use strict";

// EXEC-38 — the "for each row matching X, do steps A-C" iteration primitive. Body steps run
// through the same executeOneStep every top-level step uses (tab resolution, GATE/VERIFY,
// recovery, dry-run's destructive-skip), so most of these tests pair a destructive body step
// with dry_run: true — that exercises the full enumerate → per-row entity-bind → resolve path
// without needing to mock the click-handler/gate dispatch chain (see test_dry_run.js, whose
// mock shape this borrows).

const test = require("node:test");
const assert = require("node:assert");

const { runPlan } = require("../../app/run");

const idleContext = { on: () => {} };

// N rows, each with its own full text (used as its identifier — see resolution.js's
// enumerateRows) and a delete-button matching a shared testid signal. entityRoots narrows to
// exactly the row whose text contains {{row_id}}, so the button item returned per row is
// distinct per iteration — proving the loop actually re-scopes each time, not just resolves the
// same element N times.
function mockRowsPage(rowTexts, { missingButtonAt = -1 } = {}) {
  const calls = { enumerate: 0 };
  const rows = rowTexts.map((text, idx) => {
    const btnItem = { evaluate: async () => ({ testid: "delete-btn", role: "button", name: "Delete", text: "Delete" }) };
    return {
      text,
      evaluate: async () => text, // enumerateRows reads each row's own text this way
      locator: (sel) => ({
        all: async () => (idx === missingButtonAt || sel !== '[data-testid="delete-btn"]' ? [] : [btnItem]),
      }),
    };
  });

  const containerLocator = {
    all: async () => { calls.enumerate++; return rows; },
    filter: ({ hasText }) => {
      const match = rows.find((r) => r.text.includes(hasText));
      return { count: async () => (match ? 1 : 0), first: () => match };
    },
  };

  return {
    calls,
    waitForLoadState: async () => {},
    context: () => idleContext,
    url: () => "https://x.test",
    locator: (sel) => (sel === "table#items tr" ? containerLocator : { all: async () => [] }),
  };
}

const CONTAINER_SEL = "table#items tr";

function destructiveBodyStep() {
  return {
    type: "click",
    intent: "delete_row",
    destructive: true,
    identity_bundle: {
      signals: [{ engine: "testid", selector: 'internal:testid=[data-testid="delete-btn"]', durability: 0.99, orthogonality_class: "test-contract" }],
      fingerprint: { data_testid: "delete-btn", role: "button", aria_label: "Delete" },
      stable_hash: "",
      frame_chain: [],
    },
    entity_binding: { container_selector: CONTAINER_SEL, identifier: "{{row_id}}", source: "input", confirmed: true },
  };
}

function forEachStep({ maxIterations = 10, noCap = false, onRowError, steps } = {}) {
  const step = {
    type: "for_each",
    rows: { container_selector: CONTAINER_SEL },
    as: "row",
    steps: steps || [destructiveBodyStep()],
  };
  if (!noCap) step.max_iterations = maxIterations;
  if (onRowError) step.on_row_error = onRowError;
  return step;
}

test("for_each + dry_run: visits every row exactly once, entity-bound per row, never dispatches", async () => {
  const page = mockRowsPage(["Invoice #1", "Invoice #2", "Invoice #3"]);
  const result = await runPlan(page, [forEachStep()], {}, 0, "for-each-basic", { dryRun: true });
  assert.strictEqual(result.dryRunSkipped.length, 3);
  assert.deepStrictEqual(result.dryRunSkipped.map((s) => s.index), [0, 0, 0]); // body-local index each iteration
});

test("for_each snapshots the row list exactly once, not per iteration", async () => {
  const page = mockRowsPage(["A", "B", "C"]);
  await runPlan(page, [forEachStep()], {}, 0, "for-each-snapshot", { dryRun: true });
  assert.strictEqual(page.calls.enumerate, 1);
});

test("for_each caps at max_iterations even when more rows exist", async () => {
  const page = mockRowsPage(["A", "B", "C", "D", "E"]);
  const result = await runPlan(page, [forEachStep({ maxIterations: 2 })], {}, 0, "for-each-cap", { dryRun: true });
  assert.strictEqual(result.dryRunSkipped.length, 2);
});

test("for_each with no max_iterations refuses to run at all", async () => {
  const page = mockRowsPage(["A", "B"]);
  await assert.rejects(
    () => runPlan(page, [forEachStep({ noCap: true })], {}, 0, "for-each-nocap", { dryRun: true }),
    (err) => {
      assert.strictEqual(err.failedAt, 0);
      assert.match(err.message, /max_iterations/);
      return true;
    }
  );
  assert.strictEqual(page.calls.enumerate, 0, "must refuse before ever enumerating rows");
});

test("for_each with on_row_error: stop (default) halts the whole run on the first row failure", async () => {
  const page = mockRowsPage(["A", "B", "C"], { missingButtonAt: 1 }); // row B's target can't resolve
  await assert.rejects(
    () => runPlan(page, [forEachStep()], {}, 0, "for-each-stop", { dryRun: true }),
    (err) => err.failedAt === 0
  );
});

test("for_each with on_row_error: continue skips a failing row and finishes the rest", async () => {
  const page = mockRowsPage(["A", "B", "C"], { missingButtonAt: 1 });
  const result = await runPlan(page, [forEachStep({ onRowError: "continue" })], {}, 0, "for-each-continue", { dryRun: true });
  // Rows A and C resolved+skipped; row B failed and was skipped over, not fatal.
  assert.strictEqual(result.dryRunSkipped.length, 2);
});

// Regression: on_row_error: "continue" tracked processed/failed as tracker-only telemetry that
// never reached the run's own result — a loop could finish with rows silently skipped and report
// plain success. It must now surface in the run's warnings, the same channel a passing run's
// other advisory issues already use.
test("for_each with on_row_error: continue surfaces a warning naming how many rows failed", async () => {
  const page = mockRowsPage(["A", "B", "C"], { missingButtonAt: 1 });
  const result = await runPlan(page, [forEachStep({ onRowError: "continue" })], {}, 0, "for-each-continue-warns", { dryRun: true });
  assert.strictEqual(result.warnings.length, 1);
  assert.match(result.warnings[0], /1 of 3 item\(s\) failed/);
});

const NOOP_BODY = [{ type: "frame_enter", intent: "noop" }];

test("for_each restores inputs after the loop — no {{row_id}}/{{row_index}} leak to later steps", async () => {
  const page = mockRowsPage(["A", "B"]);
  const inputs = {};
  await runPlan(page, [forEachStep({ steps: NOOP_BODY })], inputs, 0, "for-each-restore");
  assert.strictEqual(Object.prototype.hasOwnProperty.call(inputs, "row_id"), false);
  assert.strictEqual(Object.prototype.hasOwnProperty.call(inputs, "row_index"), false);
});

test("for_each restores a pre-existing input value of the same name after the loop", async () => {
  const page = mockRowsPage(["A", "B"]);
  const inputs = { row_id: "pre-existing" };
  await runPlan(page, [forEachStep({ steps: NOOP_BODY })], inputs, 0, "for-each-restore-existing");
  assert.strictEqual(inputs.row_id, "pre-existing");
});

test("for_each with a non-destructive, non-dry-run body step actually runs the loop body", async () => {
  // A pure no-op step type (frame_enter) proves the loop mechanics themselves work outside the
  // dry-run path, without needing to mock the click-handler/gate dispatch chain.
  const page = mockRowsPage(["A", "B", "C"]);
  const bodyStep = { type: "frame_enter", intent: "noop" };
  const result = await runPlan(page, [forEachStep({ steps: [bodyStep] })], {}, 0, "for-each-realbody");
  assert.strictEqual(result.dryRunSkipped.length, 0); // nothing destructive/dry-run here
  // No throw = every iteration's no-op body step ran to completion for all 3 rows.
});

test("for_each with zero matching rows is a no-op, not an error", async () => {
  const page = mockRowsPage([]);
  const result = await runPlan(page, [forEachStep()], {}, 0, "for-each-empty", { dryRun: true });
  assert.deepStrictEqual(result.dryRunSkipped, []);
  // Regression: zero rows used to be an entirely silent no-op — a run could report plain
  // success having processed nothing, with no signal anywhere that the loop found no work.
  assert.strictEqual(result.warnings.length, 1);
  assert.match(result.warnings[0], /found no items to process/);
});

// EXEC-38 items source (Github->Drive Files Handoff generalization) — a loop driven by a named
// runtime input (comma-separated text) instead of a DOM row scan. No container to query, so
// these reuse test_dry_run.js's plain resolvable-page shape rather than mockRowsPage's
// container/entityRoots machinery — an items-loop body ordinarily carries no entity_binding at
// all (there is no live row to re-locate). Mirrors the rows-source tests above one-for-one;
// the only thing under test is splitListInput's enumeration, everything past it is identical.
function mockItemsPage() {
  const item = { evaluate: async () => ({ testid: "submit", role: "button", name: "Submit", text: "Submit" }) };
  return {
    waitForLoadState: async () => {},
    context: () => idleContext,
    url: () => "https://x.test",
    locator: (sel) => ({ all: async () => (sel === '[data-testid="submit"]' ? [item] : []) }),
  };
}

const DESTRUCTIVE_ITEM_STEP = {
  type: "click",
  intent: "download_file",
  destructive: true,
  identity_bundle: {
    signals: [{ engine: "testid", selector: 'internal:testid=[data-testid="submit"]', durability: 0.99, orthogonality_class: "test-contract" }],
    fingerprint: { data_testid: "submit", role: "button", aria_label: "Submit" },
    stable_hash: "",
    frame_chain: [],
  },
};

function itemsForEachStep({ maxIterations = 10, noCap = false, steps } = {}) {
  const step = {
    type: "for_each",
    items: "files",
    as: "file",
    steps: steps || [DESTRUCTIVE_ITEM_STEP],
  };
  if (!noCap) step.max_iterations = maxIterations;
  return step;
}

test("for_each items source: comma-separated input drives one iteration per value", async () => {
  const page = mockItemsPage();
  const result = await runPlan(page, [itemsForEachStep()], { files: "a.txt, b.txt, c.txt" }, 0, "for-each-items-basic", { dryRun: true });
  assert.strictEqual(result.dryRunSkipped.length, 3);
});

test("for_each items source: a single value with no comma is a one-iteration loop, not a second code path", async () => {
  const page = mockItemsPage();
  const result = await runPlan(page, [itemsForEachStep()], { files: "a.txt" }, 0, "for-each-items-single", { dryRun: true });
  assert.strictEqual(result.dryRunSkipped.length, 1);
});

test("for_each items source: surrounding whitespace around each value is trimmed", async () => {
  const page = mockItemsPage();
  const result = await runPlan(page, [itemsForEachStep()], { files: "  a.txt ,  b.txt  " }, 0, "for-each-items-trim", { dryRun: true });
  assert.strictEqual(result.dryRunSkipped.length, 2);
});

test("for_each items source: an empty/absent input is a clean no-op, not an error", async () => {
  const page = mockItemsPage();
  const result = await runPlan(page, [itemsForEachStep()], {}, 0, "for-each-items-empty", { dryRun: true });
  assert.deepStrictEqual(result.dryRunSkipped, []);
  // Same regression as the rows-source test above: an empty "files" input (the caller never
  // supplied one, or supplied a blank string) should be visible, not indistinguishable from a
  // fully successful run that genuinely had nothing to do.
  assert.strictEqual(result.warnings.length, 1);
  assert.match(result.warnings[0], /found no items to process/);
});

test("for_each items source: duplicate values collapse to one iteration", async () => {
  const page = mockItemsPage();
  const result = await runPlan(page, [itemsForEachStep()], { files: "a.txt, a.txt, b.txt" }, 0, "for-each-items-dedup", { dryRun: true });
  assert.strictEqual(result.dryRunSkipped.length, 2);
});

test("for_each items source: caps at max_iterations even when more values exist", async () => {
  const page = mockItemsPage();
  const result = await runPlan(page, [itemsForEachStep({ maxIterations: 2 })], { files: "a,b,c,d,e" }, 0, "for-each-items-cap", { dryRun: true });
  assert.strictEqual(result.dryRunSkipped.length, 2);
});

test("for_each items source with no max_iterations refuses to run at all", async () => {
  const page = mockItemsPage();
  await assert.rejects(
    () => runPlan(page, [itemsForEachStep({ noCap: true })], { files: "a,b" }, 0, "for-each-items-nocap", { dryRun: true }),
    (err) => {
      assert.strictEqual(err.failedAt, 0);
      assert.match(err.message, /max_iterations/);
      return true;
    }
  );
});

test("for_each items source restores inputs after the loop — no {{file_id}}/{{file_index}} leak to later steps", async () => {
  const page = mockItemsPage();
  const inputs = { files: "a,b" };
  await runPlan(page, [itemsForEachStep({ steps: NOOP_BODY })], inputs, 0, "for-each-items-restore");
  assert.strictEqual(Object.prototype.hasOwnProperty.call(inputs, "file_id"), false);
  assert.strictEqual(Object.prototype.hasOwnProperty.call(inputs, "file_index"), false);
});

test("an extract in the loop body reports every row's value, in row order", async () => {
  const { HANDLERS } = require("../../app/handlers");
  const real = HANDLERS.extract;
  HANDLERS.extract = async (_page, _step, inputs) => { inputs.total = `v_${inputs.row_id}`; };
  try {
    const page = mockRowsPage([]);
    const step = {
      type: "for_each", as: "row", max_iterations: 5, items: "ids",
      steps: [{ type: "extract", output_name: "total", intent: "read_total" }],
    };
    const result = await runPlan(page, [step], { ids: "a,b,c" }, 0, "for-each-extract", {});
    assert.deepStrictEqual(result.outputs, { total: ["v_a", "v_b", "v_c"] });
  } finally {
    HANDLERS.extract = real;
  }
});
