// captureReAuth must resolve the DYING app by the failing page's own host, not the
// group's start app — a group pack's manifest.target_url is the app the workflow
// STARTS in, which is the wrong app when a sibling's session is the one that died
// mid-run. See CLAUDE.md's group-auth notes and server.js's call site.
"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

let pass = 0;
function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
      console.log(`  ✓ ${name}`);
      pass++;
    })
    .catch((e) => {
      console.log(`  ✗ ${name}: ${e.message}`);
      process.exitCode = 1;
    });
}

const tmpDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-reauth-"));
process.env.CONXA_DATA_DIR = tmpDataDir;
process.env.CONXA_DIR = tmpDataDir;

const browser = require("../../app/browser");

const WORKSPACE_ID = "acme";
const RENDER = { id: "app_render", name: "Render", login_url: "https://dashboard.render.com/login", success_url: "https://dashboard.render.com/home" };
const BILLING = {
  id: "app_billing", name: "Billing", login_url: "https://billing.example.com/login", success_url: "https://billing.example.com/home",
  // P0: Application Authentication Recording — Billing has a learned definition, Render doesn't,
  // so hasAuthDefinition below must reflect the RESOLVED app, not a workspace-wide flag.
  auth_definition: { version: 1, probe_url: "https://billing.example.com/login", signed_out: {}, signed_in: {}, session_keys: [] },
};

function writePack(groups) {
  const dir = path.join(tmpDataDir, "skill-packs", WORKSPACE_ID);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "pack.json"), JSON.stringify({ workspace_id: WORKSPACE_ID, groups }));
}

async function run() {
  writePack([{ id: "g1", name: "Sales", apps: [RENDER, BILLING] }]);

  await check("resolves the app matching the failing page's own host, not the manifest's start app", async () => {
    const calls = [];
    const logFn = (_level, event, data) => calls.push({ event, data });
    // The failing page bounced to Billing's login screen — server.js passes that
    // page's live URL as loginUrl, ahead of manifest.target_url (Render, the start app).
    await browser.captureReAuth(WORKSPACE_ID, "https://billing.example.com/login", null, tmpDataDir, logFn, {
      groupId: "g1",
      fallbackUrl: "https://dashboard.render.com",
    });
    const resolved = calls.find((c) => c.event === "reauth_app_resolved");
    assert.ok(resolved, "expected a reauth_app_resolved log");
    assert.strictEqual(resolved.data.appId, "app_billing");
    assert.strictEqual(resolved.data.matchedBy, "failing-page-host");
  });

  await check("hasAuthDefinition reflects the RESOLVED app's own learned definition, not a workspace-wide flag", async () => {
    const withDefinition = await browser.captureReAuth(WORKSPACE_ID, "https://billing.example.com/login", null, tmpDataDir, () => {}, {
      groupId: "g1",
      fallbackUrl: "https://dashboard.render.com",
    });
    assert.strictEqual(withDefinition.hasAuthDefinition, true, "Billing has a learned definition");
    // The auto-retry text uses this sentence alone; it must not tell the agent to call again.
    assert.ok(withDefinition.summary && withDefinition.summary.includes("expired mid-run"), "summary is set");
    assert.ok(!/execute_skill/.test(withDefinition.summary), "summary does not mention execute_skill");

    const withoutDefinition = await browser.captureReAuth(WORKSPACE_ID, "https://dashboard.render.com/login", null, tmpDataDir, () => {}, {
      groupId: "g1",
      fallbackUrl: "https://billing.example.com",
    });
    assert.strictEqual(withoutDefinition.hasAuthDefinition, false, "Render has no learned definition");
  });

  await check("a mid-run auth failure drops the dying app's 'validated' stamp, so the next call re-validates for real", async () => {
    // Without this the 6h stamp survives: the next execute_skill skips the live check, runs with the
    // same dead session and fails identically — while the error promises a sign-in prompt.
    const sessionPath = path.join(tmpDataDir, "billing_session.json");
    fs.writeFileSync(sessionPath, JSON.stringify({ cookies: [] }));
    const billingKey = `${WORKSPACE_ID}__${BILLING.id}`;
    const renderKey = `${WORKSPACE_ID}__${RENDER.id}`;
    browser._writeValidationCache(billingKey, sessionPath);
    browser._writeValidationCache(renderKey, sessionPath);
    assert.ok(browser._readValidationCache(billingKey, sessionPath) > 0, "precondition: stamped");

    await browser.captureReAuth(WORKSPACE_ID, "https://billing.example.com/login", null, tmpDataDir, () => {}, {
      groupId: "g1",
      fallbackUrl: "https://dashboard.render.com",
    });

    assert.strictEqual(browser._readValidationCache(billingKey, sessionPath), 0, "the dying app's stamp must be gone");
    assert.ok(browser._readValidationCache(renderKey, sessionPath) > 0, "a sibling's stamp must be left alone");
  });

  await check("falls back to the manifest's fallback host when the failing-page host matches nothing", async () => {
    const calls = [];
    const logFn = (_level, event, data) => calls.push({ event, data });
    // A third-party OAuth hop landed on a host that matches no app in the group.
    await browser.captureReAuth(WORKSPACE_ID, "https://accounts.google.com/o/oauth2/auth", null, tmpDataDir, logFn, {
      groupId: "g1",
      fallbackUrl: "https://dashboard.render.com",
    });
    const resolved = calls.find((c) => c.event === "reauth_app_resolved");
    assert.ok(resolved);
    assert.strictEqual(resolved.data.appId, "app_render");
    assert.strictEqual(resolved.data.matchedBy, "manifest-fallback-host");
  });

  await check("does not blame an unrelated app when nothing matches at all", async () => {
    const calls = [];
    const logFn = (_level, event, data) => calls.push({ event, data });
    // Neither host matches any group app — e.g. a workflow's own recorded login step
    // failing on bad credentials against a site that was never registered as a group app.
    // Must NOT guess group.apps[0] (Render) here; that used to blame a real, uninvolved
    // app for a failure that had nothing to do with it.
    const result = await browser.captureReAuth(WORKSPACE_ID, "https://accounts.google.com/o/oauth2/auth", null, tmpDataDir, logFn, {
      groupId: "g1",
      fallbackUrl: "https://also-unmatched.example.net",
    });
    assert.strictEqual(result.unresolved, true);
    assert.ok(!calls.find((c) => c.event === "reauth_app_resolved"), "must not resolve a fabricated app");
    const unresolved = calls.find((c) => c.event === "reauth_app_unresolved");
    assert.ok(unresolved, "expected a reauth_app_unresolved log");
  });

  await check("an unregistered login host gets a plain-language note naming the host, the group, and the fix", async () => {
    const result = await browser.captureReAuth(WORKSPACE_ID, "https://drive.google.com/signin?x=1", null, tmpDataDir, () => {}, {
      groupId: "g1",
      fallbackUrl: "https://also-unmatched.example.net",
    });
    assert.strictEqual(result.unresolved, true);
    assert.match(result.note, /drive\.google\.com/);
    assert.match(result.note, /no sign-in set up/i);
    assert.match(result.note, /Build Studio/);
    assert.match(result.note, /Sales group/, "names the group so the user knows where to add it");
  });

  fs.rmSync(tmpDataDir, { recursive: true, force: true });
  console.log(`\n${pass} passed`);
  if (process.exitCode) process.exit(process.exitCode);
  process.exit(0);
}

run();
