"use strict";

// Engine v2 C3, the Node half of the action contract (the Python half is
// conxa-cloud/tests/test_action_contract.py). packages/conxa-core/conxa_core/models/action_contract.json says
// which runtime step type each recorded kind becomes and how it runs; this checks the handler table agrees,
// so a step type the runtime cannot run is found here, not by a customer. The file is a test input only.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { HANDLERS, NOOP_STEP_TYPES, PRE_DISPATCH_STEP_TYPES } = require("../../app/handlers");

const FILE = path.join(__dirname, "..", "..", "..", "packages", "conxa-core", "conxa_core", "models", "action_contract.json");
const skip = fs.existsSync(FILE) ? false : "action_contract.json not in this checkout";
const contract = skip ? { kinds: {}, step_only: {} } : JSON.parse(fs.readFileSync(FILE, "utf8"));
const noop = new Set(NOOP_STEP_TYPES);

// every runtime step type the contract names, with how it must run
const wanted = new Map();
for (const [kind, v] of Object.entries(contract.kinds)) if (v.step) wanted.set(v.step, v.run);
for (const [kind, v] of Object.entries(contract.step_only)) wanted.set(kind, v.run);

test("each step type runs the way the contract says", { skip }, () => {
  for (const [type, run] of wanted) {
    if (run === "handler") {
      assert.ok(typeof HANDLERS[type] === "function", `${type}: contract says handler, HANDLERS has none`);
      assert.ok(!noop.has(type), `${type}: contract says handler, but it is a no-op type`);
    } else if (run === "noop") {
      assert.ok(noop.has(type), `${type}: contract says no-op, not in NOOP_STEP_TYPES`);
    } else if (run === "intercept") {
      assert.ok(PRE_DISPATCH_STEP_TYPES.has(type), `${type}: contract says intercept, not in PRE_DISPATCH_STEP_TYPES`);
    } else {
      assert.fail(`${type}: unknown run value ${run}`);
    }
  }
});

test("every handler and every no-op type is in the contract (no dead or undeclared handler)", { skip }, () => {
  const known = new Set([...Object.keys(contract.kinds), ...Object.keys(contract.step_only), ...wanted.keys()]);
  for (const type of Object.keys(HANDLERS)) assert.ok(known.has(type), `${type}: in HANDLERS but not in the contract`);
  for (const type of NOOP_STEP_TYPES) assert.ok(known.has(type), `${type}: in NOOP_STEP_TYPES but not in the contract`);
});

test("every pre-dispatch type is in the contract", { skip }, () => {
  for (const type of PRE_DISPATCH_STEP_TYPES) {
    assert.ok(wanted.get(type) === "intercept", `${type}: pre-dispatch type the contract does not call an intercept`);
  }
});
