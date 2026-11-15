"use strict";
// PROD-18 — policy_gate.js coverage. `evaluate()` is pure (no I/O) — most of this file
// drives it directly. `loadPolicy()`'s fetch/cache/verify path gets a smaller, focused
// pass against a real local http.createServer plus a real Ed25519 keypair, mirroring
// manifest_manager.js's own signature-verification tests.

const test = require("node:test");
const assert = require("node:assert");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");

const policyGate = require("../../app/policy_gate");
const { canonicalJSON } = require("../../app/canonical_json");

// ── evaluate(): deny-list ────────────────────────────────────────────────────

test("evaluate allows a run with no denied hosts and no windows", () => {
  const result = policyGate.evaluate({ policy_version: 1, windows: [], denied_hosts: [] }, {
    skills: ["invoice-export"], hosts: ["app.example.com"],
  });
  assert.deepStrictEqual(result, { allow: true });
});

test("evaluate denies a host on the deny-list", () => {
  const doc = { policy_version: 2, windows: [], denied_hosts: ["payroll-legacy.acme.com"] };
  const result = policyGate.evaluate(doc, { hosts: ["payroll-legacy.acme.com"] });
  assert.strictEqual(result.allow, false);
  assert.strictEqual(result.code, "denied_host");
  assert.match(result.message, /payroll-legacy\.acme\.com/);
});

test("evaluate deny-list check is case-insensitive", () => {
  const doc = { policy_version: 1, windows: [], denied_hosts: ["Payroll.Acme.Com"] };
  const result = policyGate.evaluate(doc, { hosts: ["payroll.acme.com"] });
  assert.strictEqual(result.allow, false);
  assert.strictEqual(result.code, "denied_host");
});

test("evaluate allows a host not on the deny-list", () => {
  const doc = { policy_version: 1, windows: [], denied_hosts: ["payroll-legacy.acme.com"] };
  const result = policyGate.evaluate(doc, { hosts: ["crm.example.com"] });
  assert.deepStrictEqual(result, { allow: true });
});

test("evaluate fails closed when a deny-list is configured but hosts can't be resolved", () => {
  const doc = { policy_version: 1, windows: [], denied_hosts: ["payroll-legacy.acme.com"] };
  const result = policyGate.evaluate(doc, { hosts: [] });
  assert.strictEqual(result.allow, false);
  assert.strictEqual(result.code, "host_unresolved");
});

test("evaluate does not require host resolution when there is no deny-list", () => {
  const doc = { policy_version: 1, windows: [], denied_hosts: [] };
  const result = policyGate.evaluate(doc, { hosts: [] });
  assert.deepStrictEqual(result, { allow: true });
});

// ── evaluate(): windows ──────────────────────────────────────────────────────

// 2024-01-08 is a Monday. 09:00 IST is 03:30 UTC.
const _MONDAY_1000_IST_UTC = Date.UTC(2024, 0, 8, 4, 30, 0);   // 10:00 Asia/Kolkata
const _MONDAY_2000_IST_UTC = Date.UTC(2024, 0, 8, 14, 30, 0);  // 20:00 Asia/Kolkata
const _SATURDAY_1000_IST_UTC = Date.UTC(2024, 0, 13, 4, 30, 0); // 10:00 Asia/Kolkata, Saturday

function _window(overrides) {
  return { skills: ["payroll-*"], tz: "Asia/Kolkata", days: [1, 2, 3, 4, 5], start: "09:00", end: "17:00", ...overrides };
}

test("evaluate allows a matching skill inside its window", () => {
  const doc = { policy_version: 1, windows: [_window()], denied_hosts: [] };
  const result = policyGate.evaluate(doc, { skills: ["payroll-export"], nowMs: _MONDAY_1000_IST_UTC });
  assert.deepStrictEqual(result, { allow: true });
});

test("evaluate denies a matching skill outside its window (time)", () => {
  const doc = { policy_version: 5, windows: [_window()], denied_hosts: [] };
  const result = policyGate.evaluate(doc, { skills: ["payroll-export"], nowMs: _MONDAY_2000_IST_UTC });
  assert.strictEqual(result.allow, false);
  assert.strictEqual(result.code, "outside_window");
  assert.match(result.message, /payroll-export/);
  assert.match(result.message, /policy version 5/);
});

test("evaluate denies a matching skill outside its window (day)", () => {
  const doc = { policy_version: 1, windows: [_window()], denied_hosts: [] };
  const result = policyGate.evaluate(doc, { skills: ["payroll-export"], nowMs: _SATURDAY_1000_IST_UTC });
  assert.strictEqual(result.allow, false);
  assert.strictEqual(result.code, "outside_window");
});

test("evaluate leaves a skill matched by no window unrestricted", () => {
  const doc = { policy_version: 1, windows: [_window({ skills: ["payroll-*"] })], denied_hosts: [] };
  const result = policyGate.evaluate(doc, { skills: ["totally-unrelated-skill"], nowMs: _SATURDAY_1000_IST_UTC });
  assert.deepStrictEqual(result, { allow: true }, "windows restrict, they never default-deny an unmatched skill");
});

test("evaluate allows when ANY of a skill's multiple windows is satisfied", () => {
  const doc = {
    policy_version: 1,
    windows: [
      _window({ days: [1, 2, 3, 4, 5], start: "09:00", end: "17:00" }), // weekday business hours
      _window({ days: [6, 7], start: "00:00", end: "23:59" }),          // weekend, any time
    ],
    denied_hosts: [],
  };
  const result = policyGate.evaluate(doc, { skills: ["payroll-export"], nowMs: _SATURDAY_1000_IST_UTC });
  assert.deepStrictEqual(result, { allow: true });
});

test("evaluate skill glob matches a prefix pattern", () => {
  const doc = { policy_version: 1, windows: [_window({ skills: ["payroll-*"] })], denied_hosts: [] };
  const inside = policyGate.evaluate(doc, { skills: ["payroll-export-eu"], nowMs: _MONDAY_1000_IST_UTC });
  const outside = policyGate.evaluate(doc, { skills: ["payroll-export-eu"], nowMs: _MONDAY_2000_IST_UTC });
  assert.strictEqual(inside.allow, true);
  assert.strictEqual(outside.allow, false);
});

test("evaluate treats an empty or wildcard window skills list as covering every skill", () => {
  const doc = { policy_version: 1, windows: [_window({ skills: [] })], denied_hosts: [] };
  const result = policyGate.evaluate(doc, { skills: ["anything-at-all"], nowMs: _MONDAY_2000_IST_UTC });
  assert.strictEqual(result.allow, false, "an empty skills list on a window means it applies to every skill");
});

// ── probeTimezoneSupport ─────────────────────────────────────────────────────

test("probeTimezoneSupport passes on a normal Node runtime", () => {
  assert.strictEqual(policyGate.probeTimezoneSupport(), true);
});

// ── loadPolicy(): fetch / verify / cache ─────────────────────────────────────

function withTempDataDir(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-policy-test-"));
  return Promise.resolve().then(() => fn(dir)).finally(() => fs.rmSync(dir, { recursive: true, force: true }));
}

function _keypair() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const publicKeyB64 = publicKey.export({ format: "jwk" }).x; // base64url — convert to base64
  return { publicKey, privateKey, publicKeyB64: Buffer.from(publicKeyB64, "base64url").toString("base64") };
}

function _sign(privateKey, doc) {
  const unsigned = { ...doc };
  delete unsigned.signature;
  const sig = crypto.sign(null, Buffer.from(canonicalJSON(unsigned)), privateKey);
  return { ...unsigned, signature: sig.toString("base64") };
}

function _server(handler) {
  const server = http.createServer(handler);
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port })));
}

test("loadPolicy fetches, verifies, and caches a signed policy", async () => {
  await withTempDataDir(async (dataDir) => {
    const { privateKey, publicKeyB64 } = _keypair();
    const signed = _sign(privateKey, {
      policy_version: 1, workspace_id: "acme", key_id: "v1",
      issued_at: Math.floor(Date.now() / 1000), expires_at: Math.floor(Date.now() / 1000) + 86400,
      windows: [], denied_hosts: [],
    });
    const { server, port } = await _server((req, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(signed));
    });
    try {
      const result = await policyGate.loadPolicy({
        dataDir, apiUrl: `http://127.0.0.1:${port}`, workspaceId: "acme",
        trackingToken: "tok", publicKeyB64,
      });
      assert.strictEqual(result.status, "fresh");
      assert.strictEqual(result.doc.policy_version, 1);
      assert.ok(fs.existsSync(path.join(dataDir, "policy", "acme.json")));
    } finally {
      server.close();
    }
  });
});

test("loadPolicy discards a document with an invalid signature", async () => {
  await withTempDataDir(async (dataDir) => {
    const { publicKeyB64 } = _keypair();
    const wrongKeypair = _keypair(); // signed with a DIFFERENT key than publicKeyB64 verifies against
    const tampered = _sign(wrongKeypair.privateKey, {
      policy_version: 1, workspace_id: "acme", windows: [], denied_hosts: [],
    });
    const { server, port } = await _server((req, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(tampered));
    });
    try {
      const result = await policyGate.loadPolicy({
        dataDir, apiUrl: `http://127.0.0.1:${port}`, workspaceId: "acme",
        trackingToken: "tok", publicKeyB64,
      });
      assert.strictEqual(result.status, "absent", "an unverifiable document must never be trusted, even as a fallback");
      assert.ok(!fs.existsSync(path.join(dataDir, "policy", "acme.json")));
    } finally {
      server.close();
    }
  });
});

test("loadPolicy reports absent for a 404 (genuinely ungoverned workspace)", async () => {
  await withTempDataDir(async (dataDir) => {
    const { publicKeyB64 } = _keypair();
    const { server, port } = await _server((req, res) => { res.writeHead(404); res.end(); });
    try {
      const result = await policyGate.loadPolicy({
        dataDir, apiUrl: `http://127.0.0.1:${port}`, workspaceId: "never-governed",
        trackingToken: "tok", publicKeyB64,
      });
      assert.strictEqual(result.status, "absent");
    } finally {
      server.close();
    }
  });
});

test("loadPolicy falls back to a still-verified cache when a re-fetch fails", async () => {
  await withTempDataDir(async (dataDir) => {
    const { privateKey, publicKeyB64 } = _keypair();
    const signed = _sign(privateKey, {
      policy_version: 1, workspace_id: "acme",
      issued_at: Math.floor(Date.now() / 1000), expires_at: Math.floor(Date.now() / 1000) + 86400,
      windows: [], denied_hosts: [],
    });
    const { server, port } = await _server((req, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(signed));
    });
    let result;
    try {
      result = await policyGate.loadPolicy({
        dataDir, apiUrl: `http://127.0.0.1:${port}`, workspaceId: "acme",
        trackingToken: "tok", publicKeyB64,
      });
      assert.strictEqual(result.status, "fresh");
    } finally {
      server.close(); // now unreachable
    }
    // Force a fetch attempt against the now-closed server; must fall back to the cache
    // written above rather than reporting absent.
    const second = await policyGate.loadPolicy({
      dataDir, apiUrl: `http://127.0.0.1:${port}`, workspaceId: "acme",
      trackingToken: "tok", publicKeyB64, forceFetch: true,
    });
    assert.strictEqual(second.status, "cache");
    assert.strictEqual(second.doc.policy_version, 1);
  });
});

test("loadPolicy reports expired when the cached policy's own expires_at has passed and refetch fails", async () => {
  await withTempDataDir(async (dataDir) => {
    const { privateKey, publicKeyB64 } = _keypair();
    const now = Math.floor(Date.now() / 1000);
    const signed = _sign(privateKey, {
      policy_version: 1, workspace_id: "acme",
      issued_at: now - 1000, expires_at: now - 10, // already expired
      windows: [], denied_hosts: [],
    });
    fs.mkdirSync(path.join(dataDir, "policy"), { recursive: true });
    fs.writeFileSync(path.join(dataDir, "policy", "acme.json"), JSON.stringify({ ...signed, _cached_at: Date.now() - 2 * 3600 * 1000 }));

    const result = await policyGate.loadPolicy({
      dataDir, apiUrl: "http://127.0.0.1:1", workspaceId: "acme", // nothing listening — refetch fails
      trackingToken: "tok", publicKeyB64,
    });
    assert.strictEqual(result.status, "expired");
  });
});

// W10: an unreachable cloud used to cost every run the full fetch timeout. After one failed fetch the next ones are skipped
// for a short while, and a governed workspace still falls back to its verified cache.
test("loadPolicy: after a failed fetch the next run does not wait for the network again", async () => {
  policyGate._resetFetchFailures();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "policy-cooldown-"));
  let hits = 0;
  const server = http.createServer((_req, res) => { hits++; res.statusCode = 500; res.end("down"); });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const apiUrl = `http://127.0.0.1:${server.address().port}`;
  try {
    const opts = { dataDir, apiUrl, workspaceId: "wrk_cd", trackingToken: "t", publicKeyB64: "" };
    assert.strictEqual((await policyGate.loadPolicy(opts)).status, "absent");
    const first = hits;
    assert.ok(first >= 1);
    assert.strictEqual((await policyGate.loadPolicy(opts)).status, "absent");
    assert.strictEqual(hits, first, "no second request while the cool-down lasts");
    assert.ok((await policyGate.loadPolicy({ ...opts, forceFetch: true })) && hits > first, "an explicit refresh still asks");
  } finally {
    server.close();
    policyGate._resetFetchFailures();
  }
});
