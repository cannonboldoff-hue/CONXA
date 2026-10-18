// Plain node:test for the shell's status vocabulary (renderer/src/lib/status.ts).
//   node --experimental-strip-types --test test/status.test.mjs

import assert from "node:assert/strict";
import test from "node:test";
import { STATUS, stepTypeColor, workflowGlyph, worstStatus } from "../renderer/src/lib/status.ts";

test("every status pairs a shape with a colour", () => {
  const glyphs = Object.values(STATUS).map((s) => s.glyph);
  assert.equal(new Set(glyphs).size, 5);
  for (const s of Object.values(STATUS)) assert.match(s.color, /^#[0-9a-f]{6}$/);
});

test("clay is never used as a status or step-type colour", () => {
  const clay = "#d97757";
  for (const s of Object.values(STATUS)) assert.notEqual(s.color, clay);
  for (const k of ["click", "select", "type", "navigate", "scroll", "hover"]) {
    assert.notEqual(stepTypeColor(k), clay);
  }
  assert.equal(stepTypeColor("Click"), "#e88fb4");
  assert.equal(stepTypeColor("select"), "#e88fb4");
});

test("stage -> glyph", () => {
  assert.equal(workflowGlyph({ stage: "ready_to_compile", session_id: null }).kind, "none");
  assert.equal(workflowGlyph({ stage: "ready_to_compile", session_id: "s1" }).kind, "warn");
  assert.equal(workflowGlyph({ stage: "compiling" }).kind, "run");
  assert.equal(workflowGlyph({ stage: "queued" }).kind, "run");
  assert.equal(workflowGlyph({ stage: "needs_review" }).kind, "warn");
  assert.equal(workflowGlyph({ stage: "needs_test", last_test_status: "never" }).kind, "warn");
  assert.equal(workflowGlyph({ stage: "needs_test", last_test_status: "failed" }).kind, "fail");
  assert.equal(workflowGlyph({ stage: "ready" }).kind, "ok");
  assert.equal(workflowGlyph({ stage: "error" }).kind, "fail");
  assert.equal(workflowGlyph({ stage: "something-new" }).kind, "none");
});

test("a group's glyph is its worst child", () => {
  assert.equal(worstStatus(["ok", "warn", "none"]), "warn");
  assert.equal(worstStatus(["ok", "fail", "warn"]), "fail");
  assert.equal(worstStatus(["ok", "ok"]), "ok");
  assert.equal(worstStatus([]), "none");
});

test("interim progress rows follow the workflow's real fields", async () => {
  const { interimProgress } = await import("../renderer/src/lib/status.ts");
  const fresh = interimProgress({ stage: "ready_to_compile", session_id: null, status: "ready" });
  assert.deepEqual(fresh.map((r) => r.kind), ["ok", "none", "none", "none", "none", "none"]);
  const failedTest = interimProgress({ stage: "needs_test", session_id: "s", skill_id: "k", status: "ready", last_test_status: "failed" });
  assert.equal(failedTest.find((r) => r.label === "Test").kind, "fail");
  const compiling = interimProgress({ stage: "compiling", session_id: "s", status: "ready" });
  assert.equal(compiling.find((r) => r.label === "Compile").kind, "run");
  const approved = interimProgress({ stage: "ready", session_id: "s", skill_id: "k", status: "ready", last_test_status: "passed", signed_off: true });
  assert.ok(approved.every((r) => r.kind === "ok"));
});
