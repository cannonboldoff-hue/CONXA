"use strict";

// clipboard_write / clipboard_read: driven through executeStep with a fake page. The browser clipboard
// is the computer's clipboard, so these tests also pin down what must NOT happen: clipboard text in
// an error message, in a run's reported outputs, or lost across a pause.

const test = require("node:test");
const assert = require("node:assert");
const { executeStep } = require("../../app/handlers");
const { declaredOutputNames, collectOutputs } = require("../../app/run");

function fakePage({ url = "https://app.example/x", clipboard = "", grantError = null, evalError = null } = {}) {
  const calls = [];
  return {
    calls,
    url: () => url,
    context: () => ({
      grantPermissions: async (perms, opts) => {
        calls.push(["grant", perms, opts]);
        if (grantError) throw grantError;
      },
    }),
    evaluate: async (fn, arg) => {
      if (evalError) throw evalError;
      const src = String(fn);
      if (src.includes("writeText")) { calls.push(["write", arg]); return undefined; }
      return clipboard;
    },
  };
}

test("clipboard_write: grants the page's own origin, then writes the text with {{inputs}} filled in", async () => {
  const page = fakePage();
  await executeStep(page, { type: "clipboard_write", value: "Order {{id}}" }, { id: "A-7" });
  assert.deepStrictEqual(page.calls[0], ["grant", ["clipboard-read", "clipboard-write"], { origin: "https://app.example" }]);
  assert.deepStrictEqual(page.calls[1], ["write", "Order A-7"]);
});

test("clipboard_read: binds the text under the step's name", async () => {
  const page = fakePage({ clipboard: "copied text" });
  const inputs = {};
  await executeStep(page, { type: "clipboard_read", output_name: "pasted" }, inputs);
  assert.strictEqual(inputs.pasted, "copied text");
});

test("clipboard_read: an empty clipboard fails clearly and does not bind an empty value", async () => {
  const inputs = {};
  await assert.rejects(
    () => executeStep(fakePage({ clipboard: "" }), { type: "clipboard_read", output_name: "pasted" }, inputs),
    (err) => err.badInput === true && /clipboard is empty/.test(err.message),
  );
  assert.strictEqual("pasted" in inputs, false);
});

test("clipboard: a blank page, a refused grant and a refused read all give a clear non-retryable error", async () => {
  for (const [page, pattern] of [
    [fakePage({ url: "about:blank" }), /blank page/],
    [fakePage({ grantError: new Error("nope") }), /would not allow clipboard access/],
    [fakePage({ evalError: new Error("NotAllowedError: Read permission denied.") }), /secure https page/],
  ]) {
    await assert.rejects(
      () => executeStep(page, { type: "clipboard_read", output_name: "x" }, {}),
      (err) => err.badInput === true && pattern.test(err.message),
    );
  }
});

test("clipboard: a bad name or no text is refused before the clipboard is touched", async () => {
  const page = fakePage({ clipboard: "x" });
  await assert.rejects(() => executeStep(page, { type: "clipboard_read", output_name: "bad name" }, {}), /valid name/);
  await assert.rejects(() => executeStep(page, { type: "clipboard_write", value: "   {{missing}}" }, {}), /no text to copy/);
  assert.strictEqual(page.calls.length, 0);
});

test("clipboard text never appears in an error message", async () => {
  const secret = "hunter2-secret";
  const page = fakePage({ clipboard: secret, evalError: new Error(`failed with ${secret}`) });
  await assert.rejects(
    () => executeStep(page, { type: "clipboard_read", output_name: "x" }, {}),
    (err) => !err.message.includes(secret),
  );
});

test("a clipboard_read name is carried across a pause but never reported as a run output", () => {
  const steps = [{ type: "extract", output_name: "ticket" }, { type: "clipboard_read", output_name: "pasted" }];
  assert.deepStrictEqual(declaredOutputNames(steps), ["ticket"]);
  assert.deepStrictEqual(declaredOutputNames(steps, true), ["ticket", "pasted"]);
  assert.deepStrictEqual(collectOutputs(steps, { ticket: "T-1", pasted: "secret" }), { ticket: "T-1" });
});
