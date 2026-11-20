"use strict";
// host_browser.release() decides how much of a run's panel to tear down. A group pack opens
// one login per missing app under ONE runId, so a finished login must close only its own tab
// (close_view) — a run-wide run_end would destroy its still-open sibling logins.
const test   = require("node:test");
const assert = require("node:assert");
const http   = require("http");

const hostBrowser = require("../../app/host_browser");

function startControlStub({ status = 200, reply = { ok: true }, hang = false } = {}) {
  const calls = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      calls.push(JSON.parse(body));
      if (hang) return; // a host that never answers
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(reply));
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

// WF-14: the host's typed reason ("empty: ...", "timeout: ...") must reach download_observed, not a bare "HTTP 500".
test("a failed save_download carries the host's own reason", async () => {
  const stub = await startControlStub({ status: 500, reply: { ok: false, error: "empty: download of u is 0 bytes" } });
  try {
    await assert.rejects(
      hostBrowser.saveDownload({ runId: "r1", download: { url: () => "u" }, dest: "d", timeoutMs: 1000 }),
      /^Error: empty: download of u is 0 bytes$/,
    );
  } finally {
    stub.close();
  }
});

test("a save_download the host never answers ends as a timeout", async () => {
  const stub = await startControlStub({ hang: true });
  try {
    await assert.rejects(
      hostBrowser.saveDownload({ runId: "r1", download: { url: () => "u" }, dest: "d", timeoutMs: 10 }),
      /^Error: timeout: /,
    );
  } finally {
    stub.close();
  }
});

test("a host error with no JSON body still fails with its status", async () => {
  const stub = await startControlStub({ status: 502, reply: "x" });
  try {
    await assert.rejects(hostBrowser.ensureGeometry({ runId: "r1" }), /^Error: HTTP 502$/);
  } finally {
    stub.close();
  }
});

test("only a loopback CDP endpoint is treated as sharing this machine's disk", () => {
  assert.ok(hostBrowser._isLoopback("http://127.0.0.1:64542"));
  assert.ok(hostBrowser._isLoopback("http://localhost:9222/"));
  assert.ok(!hostBrowser._isLoopback("http://10.0.0.5:9222"));
  assert.ok(!hostBrowser._isLoopback("http://127.0.0.1.evil.com:9222"));
});

test("release never throws when Execute's control channel is unreachable", async () => {
  process.env.CONXA_HOST_CONTROL_URL = "http://127.0.0.1:1/control?token=t";
  try {
    await hostBrowser.release({ runId: "r1", tabId: "t1" });
  } finally {
    delete process.env.CONXA_HOST_CONTROL_URL;
  }
});
