"use strict";
// WF-8 8.13: a run never acts on a robot check; it stops before the step with a plain message.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const path = require("path");
const { humanCheckBlocking, HUMAN_CHECK_MESSAGE } = require("../../app/human_check");

const frame = (url, visible) => ({ url: () => url, frameElement: async () => ({ isVisible: async () => visible }) });
const page = (...frames) => ({ frames: () => frames });
const ANCHOR = "https://www.google.com/recaptcha/api2/anchor?ar=1&k=x";
const BFRAME = "https://www.google.com/recaptcha/api2/bframe?hl=en&k=x";

test("a step inside the robot check stops the run; a hand-over step never does", async () => {
  const click = { type: "click", frame: { chain: [{ url: ANCHOR }] } };
  assert.strictEqual(await humanCheckBlocking(page(), click), "step");
  assert.strictEqual(await humanCheckBlocking(page(frame(BFRAME, true)), { type: "handover" }), null);
});

test("a challenge showing stops any step; a hidden one or the checkbox beside a form does not", async () => {
  const typing = { type: "type", frame: { chain: [] } };
  assert.strictEqual(await humanCheckBlocking(page(frame(BFRAME, true)), typing), "challenge");
  assert.strictEqual(await humanCheckBlocking(page(frame(BFRAME, false), frame(ANCHOR, true)), typing), null);
  assert.strictEqual(await humanCheckBlocking(page(frame("https://www.google.com/recaptcha/api2/demo", true)), typing), null);
  const broken = { url: () => BFRAME, frameElement: async () => { throw new Error("detached"); } };
  assert.strictEqual(await humanCheckBlocking(page(broken), typing), null);
  assert.match(HUMAN_CHECK_MESSAGE, /hand-over step/);
});

const PY = path.join(__dirname, "../../../conxa-builder/python/conxa_compile/compiler/human_check.py");
test("the runtime and the compiler name the same robot-check frames", { skip: !fs.existsSync(PY) && "compiler not in this checkout" }, () => {
  const py = fs.readFileSync(PY, "utf8");
  for (const host of ["recaptcha\\.net", "hcaptcha\\.com", "challenges\\.cloudflare\\.com", "(?:anchor|bframe)"]) {
    assert.ok(py.includes(host), `compiler is missing ${host}`);
  }
});
