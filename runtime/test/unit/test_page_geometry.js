"use strict";
// page_geometry.js: the one screenshot path (raw CDP, never a clip) and the per-step size check.
const test = require("node:test");
const assert = require("node:assert");
const Module = require("node:module");

// host_browser needs Playwright from the host exe; stub it so the repair call can be observed.
const hostCalls = [];
let hostReply = () => ({ ok: true });
const realLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === "./host_browser") return { ensureGeometry: async (a) => { hostCalls.push(a); return hostReply(); } };
  return realLoad.call(this, request, ...rest);
};
const { RUN_VIEWPORT, capture, checkGeometry } = require("../../app/page_geometry");

function cdpPage({ send } = {}) {
  const sent = [];
  const cdp = { send: async (method, params) => { sent.push({ method, params }); return send ? send(method, params) : { data: Buffer.from("img").toString("base64") }; } };
  const page = { sent, context: () => ({ newCDPSession: async () => cdp }), screenshot: async () => { throw new Error("page.screenshot must never be used"); } };
  return page;
}

test("capture sends Page.captureScreenshot with no clip and returns the bytes", async () => {
  const page = cdpPage();
  const buf = await capture(page, { type: "jpeg", quality: 70 });
  assert.strictEqual(buf.toString(), "img");
  assert.strictEqual(page.sent.length, 1);
  assert.strictEqual(page.sent[0].method, "Page.captureScreenshot");
  assert.deepStrictEqual(page.sent[0].params, { format: "jpeg", quality: 70 });
  assert.ok(!("clip" in page.sent[0].params));
});

test("capture returns null on an error and on a timeout, never throws, and never falls back to page.screenshot", async () => {
  assert.strictEqual(await capture(cdpPage({ send: async () => { throw new Error("boom"); } })), null);
  assert.strictEqual(await capture(cdpPage({ send: () => new Promise(() => {}) }), { timeout: 20 }), null);
});

test("capture falls back to page.screenshot only for a page without CDP (a test fake)", async () => {
  const buf = Buffer.from("fake");
  assert.strictEqual(await capture({ screenshot: async () => buf }, { type: "png" }), buf);
});

const sizedPage = (...sizes) => {
  let n = 0;
  return { evaluate: async () => sizes[Math.min(n++, sizes.length - 1)] };
};
const HOST = { hostOwned: true, hostRunId: "r1" };

test("checkGeometry: right size asks nothing of the host", async () => {
  hostCalls.length = 0;
  assert.deepStrictEqual(await checkGeometry(sizedPage([RUN_VIEWPORT.width, RUN_VIEWPORT.height, 1]), HOST), { ok: true });
  assert.strictEqual(hostCalls.length, 0);
});

test("checkGeometry: a wrong DPR is repaired by the host and measured again", async () => {
  hostCalls.length = 0;
  const r = await checkGeometry(sizedPage([1280, 720, 1.25], [1280, 720, 1]), HOST);
  assert.deepStrictEqual(r, { ok: true });
  assert.deepStrictEqual(hostCalls, [{ runId: "r1" }]);
});

test("checkGeometry: a repair that does not hold is not ok", async () => {
  const r = await checkGeometry(sizedPage([1280, 720, 1.25]), HOST);
  assert.strictEqual(r.ok, false);
  assert.ok(!r.unsupported);
});

test("checkGeometry: a host that cannot answer the repair op is unsupported, not a failure to guess at", async () => {
  hostReply = () => { throw new Error("HTTP 500"); };
  const r = await checkGeometry(sizedPage([1024, 576, 1.25]), HOST);
  hostReply = () => ({ ok: true });
  assert.deepStrictEqual({ ok: r.ok, unsupported: r.unsupported }, { ok: false, unsupported: true });
});

test("checkGeometry: a page that cannot be measured, or has no host owner, passes", async () => {
  assert.deepStrictEqual(await checkGeometry(sizedPage(null), HOST), { ok: true });
  assert.deepStrictEqual(await checkGeometry(sizedPage([800, 600, 2]), { hostOwned: false }), { ok: true });
});
