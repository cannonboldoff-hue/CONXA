"use strict";

const test = require("node:test");
const assert = require("node:assert");

const { interpolate, inputsReferenced, inputsFailingPattern } = require("../../app/interpolate");

test("inputsFailingPattern: names mismatches, skips empty / bad regex / list values", () => {
  const declared = [
    { name: "email", pattern: "^[^@]+@[^@]+$" }, { name: "bad", pattern: "(" },
    { name: "blank", pattern: "^x$" }, { name: "list", pattern: "^x$" }, { name: "free" },
  ];
  const inputs = { email: "nope", bad: "v", blank: "", list: ["a"], free: "z" };
  assert.deepStrictEqual(inputsFailingPattern(declared, inputs), ["email"]);
  assert.deepStrictEqual(inputsFailingPattern(declared, { email: "a@b.c" }), []);
});

test("interpolate: replaces placeholders, missing input becomes empty", () => {
  assert.strictEqual(interpolate("a {{x}} b {{ y }}", { x: "1" }), "a 1 b ");
});

test("inputsReferenced: finds ids anywhere in a step, once each, and never returns a value", () => {
  const step = {
    type: "click",
    identity_bundle: { signals: [{ selector: 'role=link[name="{{file}}"]' }], fingerprint: { inner_text: "{{file}}" } },
    entity_binding: { identifier: "{{row}}" },
    branch: { body: [{ type: "fill", value: "{{note_text}}" }] },
  };
  const refs = inputsReferenced(step, { file: "Ada.gitignore", row: "", note_text: "hi" });
  assert.deepStrictEqual(refs, [
    { id: "file", empty: false },
    { id: "row", empty: true },
    { id: "note_text", empty: false },
  ]);
});

test("inputsReferenced: a missing input is empty; prose fields are not dependencies", () => {
  const refs = inputsReferenced({ type: "click", intent: "open {{ignored}}", _anchor_sentence: "{{ignored}}", value: "{{gone}}" }, {});
  assert.deepStrictEqual(refs, [{ id: "gone", empty: true }]);
});

test("inputsReferenced: tolerates no step, no inputs and cyclic objects", () => {
  assert.deepStrictEqual(inputsReferenced(undefined, undefined), []);
  const a = { value: "{{x}}" }; a.self = a;
  assert.deepStrictEqual(inputsReferenced(a, null), [{ id: "x", empty: true }]);
});
