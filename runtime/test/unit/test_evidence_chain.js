"use strict";
// PROD-18 — evidence chain coverage: seq/prev-hash linking across batches, and the
// spill-on-failure / drain-on-retry path that keeps a batch alive when its first POST fails.
// Drives createTracker against a real local http.createServer rather than mocking the
// transport, since the thing worth verifying is the exact bytes that cross the wire.

const test = require("node:test");
const assert = require("node:assert");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");

const { canonicalJSON } = require("../../app/canonical_json");

function withTempDataDir(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-evidence-test-"));
  const prevDataDir = process.env.CONXA_DATA_DIR;
  process.env.CONXA_DATA_DIR = dir;
  return Promise.resolve()
    .then(() => fn(dir))
    .finally(() => {
      if (prevDataDir === undefined) delete process.env.CONXA_DATA_DIR;
      else process.env.CONXA_DATA_DIR = prevDataDir;
      fs.rmSync(dir, { recursive: true, force: true });
    });
}

// A small controllable HTTP server: fails the first `failCount` requests with 500, then
// succeeds. Records every parsed body it receives.
function startServer(failCount) {
  const received = [];
  let remaining = failCount;
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      received.push(JSON.parse(body));
      if (remaining > 0) { remaining--; res.writeHead(500); res.end("nope"); }
      else { res.writeHead(202); res.end("{}"); }
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ server, received, port: server.address().port }));
  });
}

function expectedHash(token, evts, prev, rid, seq) {
  const linkable = { e: evts, p: prev, r: rid, s: seq };
  return crypto.createHmac("sha256", token).update(canonicalJSON(linkable)).digest("hex");
}

test("chain links seq/prev/h correctly across two flushed batches", async () => {
  await withTempDataDir(async () => {
    delete require.cache[require.resolve("../../app/tracker")];
    const { createTracker } = require("../../app/tracker");
    const { server, received, port } = await startServer(0);
    try {
      const tracker = createTracker(
        { enabled: true, tracking_url: `http://127.0.0.1:${port}/events`, tracking_token: "tok-123" },
        { runtime_version: "1.0.0", workflow_id: "wf1" }
      );
      const run = tracker.forRun("run-abc", { uid: "u1", wid: "" });

      run.emit("wf_start", {});
      await tracker.flush();

      run.emit("wf_ok", { dur: 10, tot: 3, rec: 0 });
      await tracker.flush();

      tracker.destroy();

      assert.strictEqual(received.length, 2);
      assert.strictEqual(received[0].seq, 0);
      assert.strictEqual(received[0].prev, "");
      assert.strictEqual(
        received[0].h,
        expectedHash("tok-123", received[0].evts, "", "run-abc", 0)
      );

      assert.strictEqual(received[1].seq, 1);
      assert.strictEqual(received[1].prev, received[0].h);
      assert.strictEqual(
        received[1].h,
        expectedHash("tok-123", received[1].evts, received[0].h, "run-abc", 1)
      );
    } finally {
      server.close();
    }
  });
});

test("forRun resets chain state so a second run starts back at seq 0", async () => {
  await withTempDataDir(async () => {
    delete require.cache[require.resolve("../../app/tracker")];
    const { createTracker } = require("../../app/tracker");
    const { server, received, port } = await startServer(0);
    try {
      const tracker = createTracker(
        { enabled: true, tracking_url: `http://127.0.0.1:${port}/events`, tracking_token: "tok-123" },
        { runtime_version: "1.0.0" }
      );

      const runA = tracker.forRun("run-a");
      runA.emit("wf_start", {});
      await tracker.flush();

      const runB = tracker.forRun("run-b");
      runB.emit("wf_start", {});
      await tracker.flush();

      tracker.destroy();

      assert.strictEqual(received[0].rid, "run-a");
      assert.strictEqual(received[0].seq, 0);
      assert.strictEqual(received[1].rid, "run-b");
      assert.strictEqual(received[1].seq, 0);
      assert.strictEqual(received[1].prev, "", "a new run must not chain onto the previous run's hash");
    } finally {
      server.close();
    }
  });
});

test("a batch that fails to POST spills to disk, and drainSpill delivers it later", async () => {
  await withTempDataDir(async (dataDir) => {
    delete require.cache[require.resolve("../../app/tracker")];
    const { createTracker, drainSpill } = require("../../app/tracker");
    const { server, received, port } = await startServer(1); // first POST fails, then succeeds
    try {
      const tracker = createTracker(
        { enabled: true, tracking_url: `http://127.0.0.1:${port}/events`, tracking_token: "tok-123" },
        { runtime_version: "1.0.0" }
      );
      const run = tracker.forRun("run-spill");
      run.emit("wf_start", {});
      const result = await tracker.flush();
      tracker.destroy();

      assert.strictEqual(result.ok, false, "the forced 500 must be reported as a failed flush");
      const spillPath = path.join(dataDir, "logs", "telemetry-spill.jsonl");
      assert.ok(fs.existsSync(spillPath), "a failed flush must leave a spill file");
      const lines = fs.readFileSync(spillPath, "utf8").split("\n").filter(Boolean);
      assert.strictEqual(lines.length, 1);
      const spilled = JSON.parse(lines[0]);
      assert.strictEqual(spilled.rid, "run-spill");
      assert.strictEqual(spilled._dest.url, `http://127.0.0.1:${port}/events`);
      assert.strictEqual(spilled._dest.token, "tok-123");

      // Second request already succeeded against the server's failCount=1, so drain here
      // exercises a THIRD request against an already-satisfied server — use a fresh server
      // primed to always succeed to isolate what drainSpill itself does.
    } finally {
      server.close();
    }

    const { server: server2, received: received2, port: port2 } = await startServer(0);
    try {
      // Rewrite the spilled line's destination to the fresh always-succeeding server so the
      // drain has something to actually deliver to.
      const spillPath = path.join(dataDir, "logs", "telemetry-spill.jsonl");
      const line = fs.readFileSync(spillPath, "utf8").split("\n").filter(Boolean)[0];
      const rec = JSON.parse(line);
      rec._dest.url = `http://127.0.0.1:${port2}/events`;
      fs.writeFileSync(spillPath, `${JSON.stringify(rec)}\n`);

      await drainSpill();

      assert.strictEqual(received2.length, 1, "drainSpill must deliver the spilled batch");
      assert.strictEqual(received2[0].rid, "run-spill");
      assert.ok(!fs.existsSync(spillPath) || fs.readFileSync(spillPath, "utf8").trim() === "",
        "a fully-delivered spill file must be cleared");
    } finally {
      server2.close();
    }
  });
});

test("drainSpill leaves a still-failing batch in the spill file", async () => {
  await withTempDataDir(async (dataDir) => {
    delete require.cache[require.resolve("../../app/tracker")];
    const { drainSpill } = require("../../app/tracker");
    const logsDir = path.join(dataDir, "logs");
    fs.mkdirSync(logsDir, { recursive: true });
    const spillPath = path.join(logsDir, "telemetry-spill.jsonl");
    fs.writeFileSync(spillPath, `${JSON.stringify({
      rid: "run-stuck", seq: 0, prev: "", h: "deadbeef", evts: [{ e: "wf_start", ts: 1 }],
      _dest: { url: "http://127.0.0.1:1", token: "t" }, // nothing listening — always fails
    })}\n`);

    await drainSpill();

    assert.ok(fs.existsSync(spillPath), "a batch that still can't be delivered must stay spilled");
    const lines = fs.readFileSync(spillPath, "utf8").split("\n").filter(Boolean);
    assert.strictEqual(lines.length, 1);
  });
});

// A rejected (401/404/…) request will never succeed on retry — a rotated tracking token or a
// route that no longer exists. Unlike a network/5xx failure, this must never spill or stay
// spilled: doing so used to fill the (capped) spill file with dead lines forever, which then
// dropped all NEWER real evidence instead (see tracker.js's SPILL_MAX_LINES comment).
function startRejectingServer(status) {
  const received = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => { received.push(JSON.parse(body)); res.writeHead(status); res.end("no"); });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ server, received, port: server.address().port }));
  });
}

test("a permanently-rejected (401) flush is never spilled to disk", async () => {
  await withTempDataDir(async (dataDir) => {
    delete require.cache[require.resolve("../../app/tracker")];
    const { createTracker } = require("../../app/tracker");
    const { server, received, port } = await startRejectingServer(401);
    try {
      const tracker = createTracker(
        { enabled: true, tracking_url: `http://127.0.0.1:${port}/events`, tracking_token: "stale-token" },
        { runtime_version: "1.0.0", workflow_id: "wf1" }
      );
      const run = tracker.forRun("run-401", {});
      run.emit("wf_start", {});
      const result = await tracker.flush();
      tracker.destroy();

      assert.strictEqual(received.length, 1);
      assert.strictEqual(result.ok, false);
      const spillPath = path.join(dataDir, "logs", "telemetry-spill.jsonl");
      assert.strictEqual(fs.existsSync(spillPath), false);
    } finally {
      server.close();
    }
  });
});

// WF-16 16.11: a refused tracking key marks the run's workspace so the agent is told in plain words;
// an accepted flush clears it, and a 5xx says nothing about the key.
test("a 401/403 tracking flush marks the workspace refused; a 2xx clears it; a 5xx leaves it", async () => {
  await withTempDataDir(async () => {
    delete require.cache[require.resolve("../../app/tracker")];
    const { createTracker } = require("../../app/tracker");
    const accessRefused = require("../../app/access_refused");
    accessRefused._reset();
    const flushTo = async (port) => {
      const tracker = createTracker(
        { enabled: true, tracking_url: `http://127.0.0.1:${port}/events`, tracking_token: "t" },
        { runtime_version: "1.0.0", workflow_id: "wf1", company_id: "ws-x" }
      );
      tracker.forRun("run-x", {}).emit("wf_start", {});
      await tracker.flush();
      tracker.destroy();
    };
    for (const [status, expectRefused] of [[403, true], [500, true], [202, false], [401, true]]) {
      const s = await startRejectingServer(status);
      try { await flushTo(s.port); } finally { s.server.close(); }
      assert.strictEqual(accessRefused.isRefused("ws-x"), expectRefused, `after HTTP ${status}`);
    }
    accessRefused._reset();
    // BUILD-117 (11): no key was sent (a Build Studio test run), so a 401 says nothing about an installed key.
    const s = await startRejectingServer(401);
    try {
      const tracker = createTracker(
        { enabled: true, tracking_url: `http://127.0.0.1:${s.port}/events`, tracking_token: "" },
        { runtime_version: "1.0.0", workflow_id: "wf1", company_id: "ws-x" }
      );
      tracker.forRun("run-y", {}).emit("wf_start", {});
      await tracker.flush();
      tracker.destroy();
    } finally { s.server.close(); }
    assert.strictEqual(accessRefused.isRefused("ws-x"), false);
    accessRefused._reset();
  });
});

test("drainSpill drops an already-spilled line once it comes back 401, instead of keeping it forever", async () => {
  await withTempDataDir(async (dataDir) => {
    delete require.cache[require.resolve("../../app/tracker")];
    const { drainSpill } = require("../../app/tracker");
    const { server, port } = await startRejectingServer(401);
    try {
      const logsDir = path.join(dataDir, "logs");
      fs.mkdirSync(logsDir, { recursive: true });
      const spillPath = path.join(logsDir, "telemetry-spill.jsonl");
      fs.writeFileSync(spillPath, `${JSON.stringify({
        rid: "run-stale", seq: 0, prev: "", h: "deadbeef", evts: [{ e: "wf_start", ts: 1 }],
        _dest: { url: `http://127.0.0.1:${port}/events`, token: "stale-token" },
      })}\n`);

      await drainSpill();

      assert.strictEqual(fs.existsSync(spillPath), false, "a dead-forever line must be dropped, not kept");
    } finally {
      server.close();
    }
  });
});

test("mapErrorToCode: recognized failures still classify; everything else is 'unknown', not 'selector_missing'", () => {
  delete require.cache[require.resolve("../../app/tracker")];
  const { mapErrorToCode } = require("../../app/tracker");
  assert.strictEqual(mapErrorToCode(new Error("Locator not found: #foo")), "selector_missing");
  assert.strictEqual(mapErrorToCode(new Error("Missing selector")), "selector_missing");
  assert.strictEqual(mapErrorToCode(new Error("Timeout 5000ms exceeded")), "timeout");
  assert.strictEqual(mapErrorToCode(new Error("net::ERR_CONNECTION_RESET")), "navigation_failed");
  assert.strictEqual(mapErrorToCode(new Error("Execution cancelled")), "cancelled");
  assert.strictEqual(mapErrorToCode(new Error("Permission denied writing to disk")), "unknown");
  assert.strictEqual(mapErrorToCode(new Error("Unexpected token in JSON")), "unknown");
});
