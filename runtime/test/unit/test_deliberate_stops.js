"use strict";
// A fork stop, a bad input and an app fault are DELIBERATE stops, not exhausted recovery. They must
// keep their flag through run.js::stepFailure (the single funnel every step failure passes through)
// and must get the plain terminal response: no candidate digest, no step_overrides protocol.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-deliberate-stops-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");

const { stepFailure } = require("../../app/run");
const { buildFailureResponse } = require("../../app/failure_response");

const STEP = { type: "outcome_fork" };

test("stepFailure keeps forkDecisionNeeded, badInput, fault/faultKind and decisionDialog", () => {
  const fork = stepFailure(STEP, 2, Object.assign(new Error("Needs a decision: Upload options"), { forkDecisionNeeded: true }), null, []);
  assert.strictEqual(fork.forkDecisionNeeded, true);

  const bad = stepFailure(STEP, 2, Object.assign(new Error("not valid"), { badInput: true }), null, []);
  assert.strictEqual(bad.badInput, true);

  const fault = stepFailure(STEP, 2, Object.assign(new Error("no access"), { fault: true, faultKind: "no_permission" }), null, []);
  assert.strictEqual(fault.fault, true);
  assert.strictEqual(fault.faultKind, "no_permission");

  const dd = { title: "Upload options", buttons: ["Replace", "Cancel"] };
  const blocked = stepFailure({ type: "click" }, 4, Object.assign(new Error("intercepted"), { decisionDialog: dd }), null, []);
  assert.deepStrictEqual(blocked.decisionDialog, dd);
  assert.ok(!blocked.forkDecisionNeeded && !blocked.badInput && !blocked.fault, "flags are not invented");
});

const BASE_DEPS = {
  appendRecoveryEvent: () => {},
  stepAssertions: () => [],
  frameScopedInventory: async () => null,
  agentRecoveryEnabled: true,
  maxRecoveryTier: 4,
};
const page = { url: () => "https://x.example/", screenshot: async () => Buffer.from("x"), evaluate: async () => ({}) };
const entry = (slug) => ({ slug, workspace_id: `ws-${slug}`, skillDir: fs.mkdtempSync(path.join(os.tmpdir(), "conxa-ds-skill-")) });

for (const [name, flags, pattern] of [
  ["fork stop", { forkDecisionNeeded: true }, /needs a decision nobody has authorised/],
  ["bad input", { badInput: true }, /input or a setting is not valid/],
  ["app fault", { fault: true, faultKind: "duplicate" }, /app reported a problem \(duplicate\)/],
]) {
  test(`${name}: terminal response, no override protocol, even with agent recovery on`, async () => {
    const err = stepFailure(STEP, 1, Object.assign(new Error("stop"), flags), null, []);
    const resp = await buildFailureResponse(page, err, entry(`ds-${name.replace(/\s/g, "-")}`), null, null, BASE_DEPS);
    const text = resp.content.map((c) => c.text || "").join("\n");
    assert.match(text, pattern);
    assert.ok(!/step_overrides/.test(text), "no candidate override protocol");
    assert.strictEqual(resp.content.length, 1, "no screenshots or digest");
  });
}
