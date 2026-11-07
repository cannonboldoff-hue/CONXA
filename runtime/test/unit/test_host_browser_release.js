"use strict";
// host_browser.release() decides how much of a run's panel to tear down. A group pack opens
// one login per missing app under ONE runId, so a finished login must close only its own tab
// (close_view) — a run-wide run_end would destroy its still-open sibling logins.
const test   = require("node:test");
const assert = require("node:assert");
const http   = require("http");

const hostBrowser = require("../../app/host_browser");

function startControlStub() {
  const calls = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      calls.push(JSON.parse(body));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      process.env.CONXA_HOST_CONTROL_URL = `http://127.0.0.1:${server.address().port}/control?token=t`;
      resolve({ calls, close: () => { delete process.env.CONXA_HOST_CONTROL_URL; server.close(); } });
    });
  });
}

test("acquire asks the panel for a view at the given fixed viewport", async () => {
  const stub = await startControlStub();
  process.env.CONXA_HOST_BROWSER_CDP = "http://127.0.0.1:1"; // nothing listens: only the new_view call matters
  try {
    await assert.rejects(hostBrowser.acquire({ runId: "r1", viewport: { width: 1280, height: 720 } }));
    assert.strictEqual(stub.calls[0].op, "new_view");
    assert.deepStrictEqual(stub.calls[0].viewport, { width: 1280, height: 720 });
  } finally {
    delete process.env.CONXA_HOST_BROWSER_CDP;
    stub.close();
  }
});

test("release with a tabId posts close_view for that one tab only", async () => {
  const stub = await startControlStub();
  try {
    await hostBrowser.release({ runId: "r1", tabId: "t1" });
    assert.deepStrictEqual(stub.calls, [{ op: "close_view", runId: "r1", tabId: "t1" }]);
  } finally {
    stub.close();
  }
});

test("release without a tabId still posts run_end for the whole run", async () => {
  const stub = await startControlStub();
  try {
    await hostBrowser.release({ runId: "r1" });
    assert.deepStrictEqual(stub.calls, [{ op: "run_end", runId: "r1" }]);
  } finally {
    stub.close();
  }
});

test("release never throws when Execute's control channel is unreachable", async () => {
  process.env.CONXA_HOST_CONTROL_URL = "http://127.0.0.1:1/control?token=t";
  try {
    await hostBrowser.release({ runId: "r1", tabId: "t1" });
  } finally {
    delete process.env.CONXA_HOST_CONTROL_URL;
  }
});
