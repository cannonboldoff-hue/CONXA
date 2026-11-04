"use strict";
// stripKeyboardShortcutTail — shared fixture with identity_bundle.py::strip_keyboard_shortcut_tail.
// The same cases run through the Python twin in conxa-cloud/tests/test_nameless_element_identity.py.
// If the two disagree, a name the compiler matched at record time would not match at recovery time.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const { stripKeyboardShortcutTail } = require("../../app/cascade");

const CASES = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "fixtures", "accessible_name_cases.json"), "utf-8"),
);

for (const c of CASES) {
  test(`stripKeyboardShortcutTail fixture: ${c.name}`, () => {
    assert.strictEqual(stripKeyboardShortcutTail(c.input), c.expect);
  });
}

// fingerprintName — shared fixture with identity_bundle.py::_accessible_name. Resolver scoring and
// cascade recovery both go through it, so they can no longer disagree with the compiler about a name.
const { fingerprintName } = require("../../app/accessible_name");
const NAME_CASES = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "fixtures", "fingerprint_name_cases.json"), "utf-8"),
);

for (const c of NAME_CASES) {
  test(`fingerprintName fixture: ${c.name}`, () => {
    assert.strictEqual(fingerprintName(c.fingerprint), c.expect);
  });
}
