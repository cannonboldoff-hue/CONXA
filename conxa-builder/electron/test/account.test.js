"use strict";
// AUTH-26: the launcher turns the backend's active_account.json into a per-account data folder.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { readAccountKey, accountEnv } = require("../account");

const home = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-studio-acct-"));

test("no pointer, or a damaged one, means the signed-out holding folder", () => {
  assert.equal(readAccountKey(home), "_signed_out");
  fs.writeFileSync(path.join(home, "active_account.json"), "{not json");
  assert.equal(readAccountKey(home), "_signed_out");
  fs.writeFileSync(path.join(home, "active_account.json"), JSON.stringify({ account_key: "../evil" }));
  assert.equal(readAccountKey(home), "_signed_out");
});

test("a recorded account points the backend at its own folder", () => {
  fs.writeFileSync(path.join(home, "active_account.json"), JSON.stringify({ account_key: "org_abc" }));
  const key = readAccountKey(home);
  assert.equal(key, "org_abc");
  const env = accountEnv(home, key);
  assert.equal(env.SKILL_DATA_DIR, path.join(home, "accounts", "org_abc", "data"));
  assert.equal(env.CONXA_ACCOUNT_HOME, path.join(home, "accounts", "org_abc"));
});

test("a developer-chosen data folder turns switching off", () => {
  process.env.SKILL_DATA_DIR = "C:\somewhere";
  try { assert.deepEqual(accountEnv(home, "org_abc"), {}); } finally { delete process.env.SKILL_DATA_DIR; }
});
