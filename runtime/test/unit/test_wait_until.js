"use strict";

// wait_until: waits for something that happens on its own. Driven through executeStep with a fake
// page, the same way the other handler tests do, so no browser is needed.

const test = require("node:test");
const assert = require("node:assert");
const { executeStep } = require("../../app/handlers");

function timeoutError() {
  return Object.assign(new Error("Timeout 1000ms exceeded"), { name: "TimeoutError" });
}

function fakePage(overrides = {}) {
  const calls = [];
  const page = {
    calls,
    waitForSelector: async (...a) => { calls.push(["waitForSelector", ...a]); },
    waitForLoadState: async (...a) => { calls.push(["waitForLoadState", ...a]); },
    waitForURL: async (...a) => { calls.push(["waitForURL", ...a]); },
    getByText: (text) => ({
      first: () => ({ waitFor: async (opts) => { calls.push(["getByText.waitFor", text, opts]); } }),
    }),
    ...overrides,
  };
  return page;
}

const step = (until, extra = {}) => ({ type: "wait_until", until, ...extra });

test("wait_until: element_visible waits for the selector to be visible", async () => {
  const page = fakePage();
  await executeStep(page, step({ kind: "element_visible", selector: "#done" }, { timeout_ms: 5000 }), {});
  assert.deepStrictEqual(page.calls[0], ["waitForSelector", "#done", { state: "visible", timeout: 5000 }]);
});

test("wait_until: element_hidden waits for the selector to go away", async () => {
  const page = fakePage();
  await executeStep(page, step({ kind: "element_hidden", selector: ".spinner" }), {});
  assert.strictEqual(page.calls[0][2].state, "hidden");
  assert.strictEqual(page.calls[0][2].timeout, 30000, "default timeout is 30 s");
});

test("wait_until: {{inputs}} in the condition are filled in", async () => {
  const page = fakePage();
  await executeStep(page, step({ kind: "text_visible", text: "Order {{order_id}} shipped" }), { order_id: "A-7" });
  assert.strictEqual(page.calls[0][1], "Order A-7 shipped");
});

test("wait_until: url_contains checks the address fragment", async () => {
  let predicate;
  const page = fakePage({ waitForURL: async (fn) => { predicate = fn; } });
  await executeStep(page, step({ kind: "url_contains", url: "/thanks" }), {});
  assert.strictEqual(predicate(new URL("https://x.test/orders/thanks?id=1")), true);
  assert.strictEqual(predicate(new URL("https://x.test/orders")), false);
});

test("wait_until: load_state accepts only the three page states", async () => {
  const page = fakePage();
  await executeStep(page, step({ kind: "load_state", state: "networkidle" }), {});
  assert.deepStrictEqual(page.calls[0].slice(0, 2), ["waitForLoadState", "networkidle"]);
  await assert.rejects(() => executeStep(page, step({ kind: "load_state", state: "soon" }), {}), /not a page load state/);
});

test("wait_until: one wait never exceeds 120 s, and never goes below 1 s", async () => {
  const page = fakePage();
  await executeStep(page, step({ kind: "element_visible", selector: "#a" }, { timeout_ms: 9_999_999 }), {});
  assert.strictEqual(page.calls[0][2].timeout, 120000);
  await executeStep(page, step({ kind: "element_visible", selector: "#a" }, { timeout_ms: 5 }), {});
  assert.strictEqual(page.calls[1][2].timeout, 1000);
});

test("wait_until: a timeout is a non-retryable failure that names what was waited for", async () => {
  const page = fakePage({ waitForSelector: async () => { throw timeoutError(); } });
  await assert.rejects(
    () => executeStep(page, step({ kind: "element_visible", selector: "#done" }, { timeout_ms: 2000 }), {}),
    (err) => err.badInput === true && /waited 2s for #done to show/.test(err.message),
  );
});

test("wait_until: on_timeout continue lets the run go on", async () => {
  const page = fakePage({ waitForSelector: async () => { throw timeoutError(); } });
  await executeStep(page, step({ kind: "element_visible", selector: "#done" }, { on_timeout: "continue" }), {});
});

test("wait_until: an error that is not a timeout is not swallowed", async () => {
  const page = fakePage({ waitForSelector: async () => { throw new Error("page closed"); } });
  await assert.rejects(() => executeStep(page, step({ kind: "element_visible", selector: "#x" }, { on_timeout: "continue" }), {}), /page closed/);
});

test("wait_until: a missing or unknown condition is a clear, non-retryable error", async () => {
  const page = fakePage();
  for (const until of [{}, { kind: "element_visible" }, { kind: "text_visible", text: "  " }, { kind: "url_contains" }, { kind: "teleport" }]) {
    await assert.rejects(() => executeStep(page, step(until), {}), (err) => err.badInput === true && /^wait_until:/.test(err.message));
  }
});
