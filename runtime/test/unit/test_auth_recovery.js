"use strict";
// Pure-node tests for Phase 5: auth-failure detection + session-encryption fallback.
// Run with: node runtime/test/test_auth_recovery.js

const assert = require("assert");
const { isAuthFailure } = require("../../app/run");

let passed = 0;
let failed = 0;

async function test(label, fn) {
  try {
    await fn();
    console.log(`  ✓ ${label}`);
    passed++;
  } catch (e) {
    console.error(`  ✗ ${label}`);
    console.error(`    ${e.message}`);
    failed++;
  }
}

function makePage(url, title = "My App", passwordBoxes = 0) {
  return {
    url: () => url,
    title: async () => title,
    locator: () => ({ count: async () => passwordBoxes }),
  };
}

(async () => {
  console.log("isAuthFailure detection:");

  await test("login path → auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://app.example.com/login")), true);
  });

  await test("signin path → auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://app.example.com/signin?redirect=/dashboard")), true);
  });

  await test("session-expired path → auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://app.example.com/session-expired")), true);
  });

  // A sign-in hop (/auth, /sso, /oauth2, /logout) is also an ordinary in-app page (/settings/auth),
  // so it counts only with a second piece of evidence.
  await test("auth hop with a password box → auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://app.example.com/auth/challenge", "My App", 1)), true);
  });

  await test("auth hop with a sign-in title → auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://app.example.com/oauth2/authorize", "Sign in to continue")), true);
  });

  await test("auth hop with no sign-in evidence (an in-app settings page) → NOT auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://app.example.com/settings/auth")), false);
  });

  await test("a page whose address can't be probed for a password box → NOT auth failure", async () => {
    const page = { url: () => "https://app.example.com/sso", title: async () => "Home", locator: () => { throw new Error("gone"); } };
    assert.equal(await isAuthFailure(page), false);
  });

  await test("normal dashboard path → not auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://dashboard.render.com/services")), false);
  });

  // Regression (2026-09-02, mega-workflow): a skill that deliberately records a login page —
  // the-internet.herokuapp.com/login, whose form the workflow itself fills in — was reported as
  // "your saved sign-in expired" for an app the failing step never touched, AND lost its whole
  // Tier 1-4 recovery cascade (run.js short-circuits to stepFailure on an auth verdict).
  const RECORDED_LOGIN_STEPS = [
    { type: "navigate", url: "https://the-internet.herokuapp.com/login" },
    { type: "type", selector: "role=textbox[name=\"Username\"]" },
  ];

  await test("a login page the recording navigated to → NOT auth failure", async () => {
    assert.equal(
      await isAuthFailure(makePage("https://the-internet.herokuapp.com/login"), RECORDED_LOGIN_STEPS),
      false
    );
  });

  await test("recorded-url match ignores query and trailing slash", async () => {
    assert.equal(
      await isAuthFailure(makePage("https://the-internet.herokuapp.com/login/?x=1"), RECORDED_LOGIN_STEPS),
      false
    );
  });

  await test("a DIFFERENT login page in the same run → still auth failure", async () => {
    assert.equal(
      await isAuthFailure(makePage("https://dashboard.render.com/login"), RECORDED_LOGIN_STEPS),
      true
    );
  });

  await test("no steps passed → unchanged behaviour", async () => {
    assert.equal(await isAuthFailure(makePage("https://the-internet.herokuapp.com/login")), true);
  });

  await test("a recorded url does not suppress the TITLE heuristic elsewhere", async () => {
    assert.equal(
      await isAuthFailure(makePage("https://the-internet.herokuapp.com/secure", "Session Expired"), RECORDED_LOGIN_STEPS),
      true
    );
  });

  await test("title 'Sign in to Render' → auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://render.com/other", "Sign in to Render")), true);
  });

  await test("title 'Session Expired' → auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://app.example.com/", "Session Expired")), true);
  });

  await test("normal page title → not auth failure", async () => {
    assert.equal(await isAuthFailure(makePage("https://app.example.com/dashboard", "Dashboard")), false);
  });

  await test("login in subpath of legitimate URL → not auth failure", async () => {
    // /app/login-history should NOT match — regex requires login at end, /, or ?
    assert.equal(await isAuthFailure(makePage("https://app.example.com/settings/login-history")), false);
  });

  // Interactive-login capture detection (_reachedProtectedUrl, host/redirect matching) was
  // replaced by a signed-out baseline compare — see login_signals.js::isSignedInAgainstBaseline
  // and its coverage in test_login_signals.js, including the GitHub www./redirect case that
  // exposed the old hostname-equality bug.

  console.log("\nsession encryption fallback logging (SG-11):");
  const authManager = require("../../app/auth_manager");
  const fs = require("fs");
  const path = require("path");
  const os = require("os");

  await test("saveEncryptedSession returns false and logs a warning on failure", async () => {
    // A regular file in place of the sessions dir makes mkdirSync throw deterministically.
    const blockingFile = path.join(os.tmpdir(), `conxa-test-block-${Date.now()}`);
    fs.writeFileSync(blockingFile, "x");
    const warnings = [];
    const logFn = (level, msg) => { if (level === "warn") warnings.push(msg); };
    const ok = authManager.saveEncryptedSession("acme", { cookies: [] }, "aa".repeat(32), blockingFile, logFn);
    fs.unlinkSync(blockingFile);
    assert.equal(ok, false);
    assert.ok(warnings.includes("session_encryption_failed"));
  });

  await test("reencryptPlaintextSessions encrypts and deletes a planted plaintext session", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-sessions-"));
    const workspace_id = "reencrypt-co";
    const rawPath = path.join(dir, `${workspace_id}_raw_state.json`);
    fs.writeFileSync(rawPath, JSON.stringify({ cookies: [] }));
    const events = [];
    const logFn = (level, msg) => events.push(`${level}:${msg}`);
    await authManager.reencryptPlaintextSessions(dir, async () => "bb".repeat(32), logFn);
    assert.ok(!fs.existsSync(rawPath), "plaintext original should be deleted");
    assert.ok(fs.existsSync(path.join(dir, `${workspace_id}_state.json`)), "encrypted file should exist");
    assert.ok(events.includes("info:plaintext_session_reencrypted"));
    fs.rmSync(dir, { recursive: true, force: true });
  });

  await test("reencryptPlaintextSessions leaves plaintext in place if key fetch fails", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-sessions-"));
    const workspace_id = "reencrypt-fail-co";
    const rawPath = path.join(dir, `${workspace_id}_raw_state.json`);
    fs.writeFileSync(rawPath, JSON.stringify({ cookies: [] }));
    const events = [];
    const logFn = (level, msg) => events.push(`${level}:${msg}`);
    await authManager.reencryptPlaintextSessions(dir, async () => { throw new Error("keytar down"); }, logFn);
    assert.ok(fs.existsSync(rawPath), "plaintext should remain when re-encryption fails");
    assert.ok(events.includes("warn:plaintext_session_reencrypt_failed"));
    fs.rmSync(dir, { recursive: true, force: true });
  });

  const total = passed + failed;
  console.log(`\n${passed}/${total} passed`);
  process.exit(failed > 0 ? 1 : 0);
})();
