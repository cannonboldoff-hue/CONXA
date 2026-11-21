"use strict";
// Covers the pack.skills repopulation fix in sync.js: the server's delta response
// is the authoritative full skill list for a company, and must be written back to
// pack.json on every successful delta fetch — whether or not any skill actually
// changed — so that a thin installer (which ships with pack.skills empty) and any
// skill added to a company post-install both become visible to skill_loader.js's
// registry, which reads pack.skills off disk rather than scanning the directory.
//
// Network mocking follows test_manifest_manager.js's precedent of avoiding a real
// TLS round trip: https.get is mocked directly (module-cached, so the same object
// reference sync.js already captured), rather than spinning up a live HTTPS server.

const test = require("node:test");
const assert = require("node:assert");
const { mock } = require("node:test");
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const https = require("https");

const { syncSkillPacks } = require("../../app/sync");
const versionManager = require("../../app/version_manager");

// A successful sync now also calls durable_context.updateDurableContext()
// (best-effort, see sync.js), which resolves its target paths from the REAL
// os.homedir()/APPDATA/LOCALAPPDATA unless overridden. Without isolating
// those here, every test below would write into this machine's actual
// ~/.claude/skills, ~/.cursor/rules, etc. — sandbox HOME the same way
// test_mcp_register.js and test_mcp_hosts.js do, so a passing test run never
// touches a real AI-agent config.
const ENV_KEYS = ["USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"];
let savedEnv;
test.beforeEach(() => {
  savedEnv = {};
  for (const k of ENV_KEYS) savedEnv[k] = process.env[k];
  const sandboxHome = fs.mkdtempSync(path.join(os.tmpdir(), "sync-test-home-"));
  process.env.USERPROFILE = sandboxHome;
  process.env.HOME = sandboxHome;
  process.env.APPDATA = path.join(sandboxHome, "AppData", "Roaming");
  process.env.LOCALAPPDATA = path.join(sandboxHome, "AppData", "Local");
});
test.afterEach(() => {
  for (const k of ENV_KEYS) {
    if (savedEnv[k] === undefined) delete process.env[k];
    else process.env[k] = savedEnv[k];
  }
});

function mkSkillPacksDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "sync-test-"));
}

function writePack(skillPacksDir, workspace_id, overrides = {}) {
  const workspaceDir = path.join(skillPacksDir, workspace_id);
  fs.mkdirSync(workspaceDir, { recursive: true });
  const pack = {
    workspace_id,
    sync_endpoint: "https://cloud.example/api/v1/workflows/v2/" + workspace_id + "/skill-packs/delta",
    sync_token: "test-token",
    skills: [],
    ...overrides,
  };
  fs.writeFileSync(path.join(workspaceDir, "pack.json"), JSON.stringify(pack, null, 2));
  return path.join(workspaceDir, "pack.json");
}

function mockDeltaResponse(payload, statusCode = 200) {
  return mock.method(https, "get", (_url, _options, callback) => {
    const dataHandlers = [];
    const endHandlers = [];
    const res = {
      statusCode,
      on(event, cb) {
        if (event === "data") dataHandlers.push(cb);
        if (event === "end") endHandlers.push(cb);
        return res;
      },
    };
    process.nextTick(() => {
      dataHandlers.forEach((cb) => cb(Buffer.from(JSON.stringify(payload))));
      endHandlers.forEach((cb) => cb());
    });
    callback(res);
    return { setTimeout() {}, on() {}, destroy() {} };
  });
}

test("no_change delta still repopulates pack.skills and pack.last_synced", async (t) => {
  const skillPacksDir = mkSkillPacksDir();
  const packPath = writePack(skillPacksDir, "acme", { skills: [] }); // thin-installer start state

  const getMock = mockDeltaResponse({
    skills: [{ name: "skill-a", action: "no_change", group: "_default" }],
  });
  t.after(() => getMock.mock.restore());

  await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });

  const pack = JSON.parse(fs.readFileSync(packPath, "utf8"));
  assert.deepStrictEqual(pack.skills, ["skill-a"], "server's skill list must be written back even with no changes");
  assert.deepStrictEqual(pack.skill_groups, { "skill-a": "_default" }, "skill_groups must be written back so the next sync's version lookup knows where to look");
  assert.ok(pack.last_synced, "last_synced must be set even when nothing changed");
  assert.ok(!fs.existsSync(path.join(skillPacksDir, "acme", "_default", "skill-a")), "no_change must not create any skill directory");

  fs.rmSync(skillPacksDir, { recursive: true, force: true });
});

// WF-16 16.11: a refused key must keep every installed skill and be said in plain words.
test("a refused sync key (401/403) keeps the installed skills and marks the workspace refused", async (t) => {
  const accessRefused = require("../../app/access_refused");
  accessRefused._reset();
  for (const status of [401, 403]) {
    const skillPacksDir = mkSkillPacksDir();
    const packPath = writePack(skillPacksDir, "acme", { skills: ["skill-a"] });
    const getMock = mockDeltaResponse({ detail: "invalid_sync_token" }, status);
    await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });
    getMock.mock.restore();
    assert.deepStrictEqual(JSON.parse(fs.readFileSync(packPath, "utf8")).skills, ["skill-a"], `HTTP ${status} must not touch pack.skills`);
    assert.strictEqual(accessRefused.isRefused("acme"), true, `HTTP ${status} must mark the workspace refused`);

    // The next accepted answer clears it.
    const okMock = mockDeltaResponse({ skills: [{ name: "skill-a", action: "no_change", group: "_default" }] });
    await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });
    okMock.mock.restore();
    assert.strictEqual(accessRefused.isRefused("acme"), false);
    fs.rmSync(skillPacksDir, { recursive: true, force: true });
  }
  t.after(() => accessRefused._reset());
});

test("an answer with no skill list (a 304 or a stray body) never hides the installed skills", async (t) => {
  for (const [payload, status] of [[{}, 200], [{ files: [] }, 304], [{ skills: "nope" }, 200]]) {
    const skillPacksDir = mkSkillPacksDir();
    const packPath = writePack(skillPacksDir, "acme", { skills: ["skill-a", "skill-b"] });
    const getMock = mockDeltaResponse(payload, status);
    await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });
    getMock.mock.restore();
    assert.deepStrictEqual(JSON.parse(fs.readFileSync(packPath, "utf8")).skills, ["skill-a", "skill-b"], `${status} ${JSON.stringify(payload)}`);
    fs.rmSync(skillPacksDir, { recursive: true, force: true });
  }
});

test("update delta downloads, activates, and repopulates pack.skills", async (t) => {
  const skillPacksDir = mkSkillPacksDir();
  const packPath = writePack(skillPacksDir, "acme", { skills: [] });

  const content = Buffer.from('{"skills":[]}');
  const sha256 = crypto.createHash("sha256").update(content).digest("hex");

  const getMock = mockDeltaResponse({
    skills: [
      {
        name: "skill-a",
        action: "update",
        version: "1.0.0",
        group: "_default",
        files: [{ path: "manifest.json", sha256, content_base64: content.toString("base64") }],
      },
    ],
  });
  t.after(() => getMock.mock.restore());

  await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });

  const pack = JSON.parse(fs.readFileSync(packPath, "utf8"));
  assert.deepStrictEqual(pack.skills, ["skill-a"]);
  assert.deepStrictEqual(pack.skill_groups, { "skill-a": "_default" });
  assert.ok(pack.last_synced);

  const skillRoot = path.join(skillPacksDir, "acme", "_default", "skill-a");
  const currentLink = path.join(skillRoot, "current");
  assert.ok(fs.existsSync(path.join(skillRoot, "v1.0.0", "manifest.json")), "skill file must be written under its version dir");
  assert.ok(fs.existsSync(path.join(currentLink, "manifest.json")), "current junction must resolve to the activated version");

  fs.rmSync(skillPacksDir, { recursive: true, force: true });
});

test("skills in different groups are written to separate nested directories", async (t) => {
  const skillPacksDir = mkSkillPacksDir();
  const packPath = writePack(skillPacksDir, "acme", { skills: [] });

  const content = Buffer.from('{"skills":[]}');
  const sha256 = crypto.createHash("sha256").update(content).digest("hex");
  const fileEntry = { path: "manifest.json", sha256, content_base64: content.toString("base64") };

  const getMock = mockDeltaResponse({
    skills: [
      { name: "skill-a", action: "update", version: "1.0.0", group: "group-111", files: [fileEntry] },
      { name: "skill-b", action: "update", version: "1.0.0", group: "group-222", files: [fileEntry] },
    ],
  });
  t.after(() => getMock.mock.restore());

  await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });

  const pack = JSON.parse(fs.readFileSync(packPath, "utf8"));
  assert.deepStrictEqual(pack.skill_groups, { "skill-a": "group-111", "skill-b": "group-222" });
  assert.ok(fs.existsSync(path.join(skillPacksDir, "acme", "group-111", "skill-a", "current", "manifest.json")));
  assert.ok(fs.existsSync(path.join(skillPacksDir, "acme", "group-222", "skill-b", "current", "manifest.json")));

  fs.rmSync(skillPacksDir, { recursive: true, force: true });
});

test("a skill only present at the old flat location is treated as never-synced and redownloaded into the nested path", async (t) => {
  const skillPacksDir = mkSkillPacksDir();
  const packPath = writePack(skillPacksDir, "acme", { skills: ["skill-a"] });

  // Pre-seed the pre-Groups flat layout with an already-activated version, simulating
  // a machine that was fully synced before group-nesting shipped.
  const flatRoot = path.join(skillPacksDir, "acme", "skill-a");
  const flatVersionDir = path.join(flatRoot, "v1.0.0");
  fs.mkdirSync(flatVersionDir, { recursive: true });
  fs.writeFileSync(path.join(flatVersionDir, "manifest.json"), "{}");
  fs.writeFileSync(path.join(flatVersionDir, "version.json"), JSON.stringify({ skill_version: "1.0.0" }));
  versionManager.activate(flatRoot, flatVersionDir, { keep: 3, requiredFiles: ["manifest.json"] });

  const content = Buffer.from('{"skills":[]}');
  const sha256 = crypto.createHash("sha256").update(content).digest("hex");

  let requestedUrl = null;
  const getMock = mock.method(https, "get", (url, _options, callback) => {
    requestedUrl = url;
    const payload = {
      skills: [{
        name: "skill-a",
        action: "update",
        version: "1.0.0",
        group: "_default",
        files: [{ path: "manifest.json", sha256, content_base64: content.toString("base64") }],
      }],
    };
    const dataHandlers = [];
    const endHandlers = [];
    const res = {
      statusCode: 200,
      on(event, cb) {
        if (event === "data") dataHandlers.push(cb);
        if (event === "end") endHandlers.push(cb);
        return res;
      },
    };
    process.nextTick(() => {
      dataHandlers.forEach((cb) => cb(Buffer.from(JSON.stringify(payload))));
      endHandlers.forEach((cb) => cb());
    });
    callback(res);
    return { setTimeout() {}, on() {}, destroy() {} };
  });
  t.after(() => getMock.mock.restore());

  await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });

  // The version lookup that built the `since` query param must have found nothing at
  // the nested path and reported "0" — never the real "1.0.0" sitting at the old flat
  // location — which is what forces the server to report action:"update" here.
  const sinceParam = new URL(requestedUrl).searchParams.get("since");
  assert.deepStrictEqual(JSON.parse(sinceParam), { "skill-a": "0" }, "version lookup must not consult the old flat location");

  assert.ok(
    fs.existsSync(path.join(skillPacksDir, "acme", "_default", "skill-a", "current", "manifest.json")),
    "skill must be freshly written to the new nested location"
  );

  fs.rmSync(skillPacksDir, { recursive: true, force: true });
});

test("a brand-new skill added to a company post-install is picked up from nothing", async (t) => {
  // Simulates the exact thin-installer scenario: pack.skills starts empty (no
  // prior sync ever ran), and the company's cloud-side pack now has one skill.
  const skillPacksDir = mkSkillPacksDir();
  writePack(skillPacksDir, "acme", { skills: [] });

  const content = Buffer.from('{"skills":[]}');
  const sha256 = crypto.createHash("sha256").update(content).digest("hex");

  const getMock = mockDeltaResponse({
    skills: [
      {
        name: "brand-new-skill",
        action: "update",
        version: "1.0.0",
        group: "_default",
        files: [{ path: "manifest.json", sha256, content_base64: content.toString("base64") }],
      },
    ],
  });
  t.after(() => getMock.mock.restore());

  await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });

  const pack = JSON.parse(fs.readFileSync(path.join(skillPacksDir, "acme", "pack.json"), "utf8"));
  assert.deepStrictEqual(pack.skills, ["brand-new-skill"], "a skill never seen locally before must appear in pack.skills after one sync");

  fs.rmSync(skillPacksDir, { recursive: true, force: true });
});

test("a checksum mismatch records last_sync_errors instead of activating a corrupt version", async (t) => {
  const skillPacksDir = mkSkillPacksDir();
  const packPath = writePack(skillPacksDir, "acme", { skills: [] });

  const getMock = mockDeltaResponse({
    skills: [
      {
        name: "skill-a",
        action: "update",
        version: "1.0.0",
        group: "_default",
        // Wrong sha256 — atomicWrite must reject this file.
        files: [{ path: "manifest.json", sha256: "0".repeat(64), content_base64: Buffer.from("{}").toString("base64") }],
      },
    ],
  });
  t.after(() => getMock.mock.restore());

  await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });

  const pack = JSON.parse(fs.readFileSync(packPath, "utf8"));
  assert.equal(pack.last_sync_errors["skill-a"].code, "checksum_mismatch");
  assert.ok(
    !fs.existsSync(path.join(skillPacksDir, "acme", "_default", "skill-a", "current")),
    "a checksum-mismatched version must never be activated"
  );

  fs.rmSync(skillPacksDir, { recursive: true, force: true });
});

test("a successful sync clears a previously recorded sync error for that skill", async (t) => {
  const skillPacksDir = mkSkillPacksDir();
  const packPath = writePack(skillPacksDir, "acme", {
    skills: ["skill-a"],
    last_sync_errors: { "skill-a": { code: "checksum_mismatch", at: "2026-08-19T00:00:00Z" } },
  });

  const content = Buffer.from('{"skills":[]}');
  const sha256 = crypto.createHash("sha256").update(content).digest("hex");
  const getMock = mockDeltaResponse({
    skills: [
      {
        name: "skill-a",
        action: "update",
        version: "1.0.0",
        group: "_default",
        files: [{ path: "manifest.json", sha256, content_base64: content.toString("base64") }],
      },
    ],
  });
  t.after(() => getMock.mock.restore());

  await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });

  const pack = JSON.parse(fs.readFileSync(packPath, "utf8"));
  assert.deepStrictEqual(pack.last_sync_errors, {}, "a skill that just activated successfully must not still read as failed");

  fs.rmSync(skillPacksDir, { recursive: true, force: true });
});


// ── Pass two: recovery artifacts must never hold up execution ────────────────────────────────
// The whole point of splitting the sync is that workflows can run the moment the CODE files
// activate. Artifacts are only read when a step fails and reaches the agent recovery tier, so
// waiting on them would charge every start for something almost no run uses.

const httpClient = require("../../app/http_client");

function withSlowArtifactFetch(fn) {
  const original = httpClient.postForBuffer;
  const state = { started: false, finished: false };
  httpClient.postForBuffer = async () => {
    state.started = true;
    await new Promise((r) => setTimeout(r, 150));
    state.finished = true;
    // An empty archive: this test is about timing, not extraction.
    return { body: Buffer.alloc(0), statusCode: 200, headers: {} };
  };
  return Promise.resolve(fn(state)).finally(() => { httpClient.postForBuffer = original; });
}

const ARTIFACT_DELTA = {
  skills: [{
    name: "skill-a", action: "no_change", group: "_default",
    artifacts: [{ path: "visuals/Image_1.jpg", sha256: "a".repeat(64) }],
  }],
};

test("the artifact pass does not block the sync — the gate opens without it", async (t) => {
  const skillPacksDir = mkSkillPacksDir();
  writePack(skillPacksDir, "acme", { skills: [] });
  const getMock = mockDeltaResponse(ARTIFACT_DELTA);
  t.after(() => getMock.mock.restore());

  await withSlowArtifactFetch(async (state) => {
    await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });
    assert.ok(state.started, "pass two was started");
    assert.ok(!state.finished, "but sync resolved without waiting for it — this is the gate");
    // Let it finish so it cannot leak into a later test.
    await new Promise((r) => setTimeout(r, 250));
    assert.ok(state.finished);
  });
});

test("artifacts:false starts no background pass at all", async (t) => {
  const skillPacksDir = mkSkillPacksDir();
  writePack(skillPacksDir, "acme", { skills: [] });
  const getMock = mockDeltaResponse(ARTIFACT_DELTA);
  t.after(() => getMock.mock.restore());

  await withSlowArtifactFetch(async (state) => {
    // The install-time `sync` subcommand passes this: that process exists to exit, and
    // unawaited work would either hold the installer open or be killed halfway.
    await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {}, artifacts: false });
    await new Promise((r) => setTimeout(r, 50));
    assert.ok(!state.started, "no artifact request may be issued when the caller opted out");
  });
});

test("a failing artifact pass leaves the sync itself clean", async (t) => {
  const skillPacksDir = mkSkillPacksDir();
  const packPath = writePack(skillPacksDir, "acme", { skills: [] });
  const getMock = mockDeltaResponse(ARTIFACT_DELTA);
  t.after(() => getMock.mock.restore());

  const original = httpClient.postForBuffer;
  httpClient.postForBuffer = async () => { throw new Error("network down"); };
  try {
    await syncSkillPacks(skillPacksDir, { timeoutMs: 4000, log: () => {} });
    await new Promise((r) => setTimeout(r, 50));
  } finally {
    httpClient.postForBuffer = original;
  }

  const pack = JSON.parse(fs.readFileSync(packPath, "utf8"));
  // A missing screenshot costs the agent tier one signal out of several. A sync error would
  // make a perfectly working skill show as failed on the Deployment dashboard.
  assert.deepStrictEqual(pack.last_sync_errors, {}, "a failed artifact fetch is not a sync failure");
  assert.deepStrictEqual(pack.skills, ["skill-a"], "pass one's results stand regardless");
});
