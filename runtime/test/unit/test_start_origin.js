"use strict";

// BUILD-101 (6): an address check follows the site the run starts on.

const test = require("node:test");
const assert = require("node:assert");
const { followStartOrigin, originOf, pyEscape } = require("../../app/start_origin");
const { interpolate } = require("../../app/interpolate");

const nav = (url) => ({ type: "navigate", url });
const landing = (target, extra = {}) => ({ type: "click", validation: { assertions: [{ type: "url_pattern", target, required: false, ...extra }] } });

test("python's re.escape and this one spell a site the same way", () => {
  assert.equal(pyEscape("https://en.wikipedia.org"), "https://en\\.wikipedia\\.org");
  assert.equal(pyEscape("https://my-app.example.com:8443"), "https://my\\-app\\.example\\.com:8443");
});

test("a landing check moves to the site the run starts on, path untouched", () => {
  const steps = [nav("https://de.wikipedia.org/wiki/Hauptseite"),
    landing("^https://en\\.wikipedia\\.org/wiki/[^/?#]+", { start_origin: "https://en.wikipedia.org" })];
  const out = followStartOrigin(steps, {}, interpolate);
  assert.equal(out[1].validation.assertions[0].target, "^https://de\\.wikipedia\\.org/wiki/[^/?#]+");
  assert.ok(new RegExp(out[1].validation.assertions[0].target).test("https://de.wikipedia.org/wiki/Automatisierung"));
  assert.equal(steps[1].validation.assertions[0].target, "^https://en\\.wikipedia\\.org/wiki/[^/?#]+", "the pack itself is not changed");
});

test("a run that starts where it was recorded changes nothing", () => {
  const steps = [nav("https://en.wikipedia.org/"), landing("^https://en\\.wikipedia\\.org/wiki/x", { start_origin: "https://en.wikipedia.org" })];
  assert.strictEqual(followStartOrigin(steps, {}, interpolate), steps);
});

test("a check with no stamp, or on another site, is left alone", () => {
  const steps = [nav("https://de.wikipedia.org/"),
    landing("^https://en\\.wikipedia\\.org/wiki/x"),
    landing("^https://login\\.example\\.com/cb", { start_origin: "https://en.wikipedia.org" })];
  assert.strictEqual(followStartOrigin(steps, {}, interpolate), steps);
});

test("a start address that is an input is read with the run's value", () => {
  const steps = [nav("{{site}}/start"), landing("^https://a\\.test/x", { start_origin: "https://a.test" })];
  const out = followStartOrigin(steps, { site: "https://b.test" }, interpolate);
  assert.equal(out[1].validation.assertions[0].target, "^https://b\\.test/x");
});

test("checks inside a popup option or a loop body follow too, and so does a stays-check", () => {
  const probe = { type: "url_stays", pattern: "^https://a\\.test/done", start_origin: "https://a.test" };
  const steps = [nav("https://b.test/"), {
    type: "outcome_fork", states: [{ options: [{ steps: [landing("^https://a\\.test/gone", { start_origin: "https://a.test" })] }] }],
    validation: { effect: { checks: [{ id: "c", probe }] } },
  }];
  const out = followStartOrigin(steps, {}, interpolate);
  assert.equal(out[1].states[0].options[0].steps[0].validation.assertions[0].target, "^https://b\\.test/gone");
  assert.equal(out[1].validation.effect.checks[0].probe.pattern, "^https://b\\.test/done");
});

test("no navigate step, or an unreadable start, moves nothing", () => {
  const steps = [landing("^https://a\\.test/x", { start_origin: "https://a.test" })];
  assert.strictEqual(followStartOrigin(steps, {}, interpolate), steps);
  assert.equal(originOf("about:blank"), "");
  const bad = [nav("{{missing}}"), ...steps];
  assert.strictEqual(followStartOrigin(bad, {}, interpolate), bad);
});

// ── the file a press sends was chosen by an earlier step (BUILD-103 (4)) ─────────────────────────────────────────────
const { uploadFileName } = require("../../app/run");

test("a press that sends picked files looks for the pick's file name; an upload keeps its own", () => {
  const pick = { type: "upload", source_step_key: "rec7", value: "/tmp/files/report final.pdf" };
  const press = { type: "click", source_step_key: "rec9",
    validation: { effect: { checks: [{ probe: { type: "fresh_look", file_from: "rec7" } }] } } };
  const steps = [pick, press];
  assert.equal(uploadFileName(pick, {}, steps), "report final.pdf");
  assert.equal(uploadFileName(press, {}, steps), "report final.pdf");
  assert.equal(uploadFileName(press, {}, [press]), null, "the pick is gone: nothing to look for");
  assert.equal(uploadFileName({ type: "click" }, {}, steps), null);
});
