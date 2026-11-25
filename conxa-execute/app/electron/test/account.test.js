"use strict";
// AUTH-26: each signed-in person keeps chats, history and settings in their own folder.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const base = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-exec-acct-"));
process.env.CONXA_EXECUTE_USER_DATA = base;
const account = require("../account");

const A = { user_id: "user_A" };
const B = { user_id: "user_B" };

test("key follows the person; signed out is the holding folder", () => {
  assert.equal(account.keyFromIdentity(A), "user_A");
  assert.equal(account.keyFromIdentity(null), "_signed_out");
  assert.ok(account.keyFromIdentity({ user_id: "u/../x " + "y".repeat(80) }).length <= 40);
});

test("first person takes the earlier shared files; the second starts empty", () => {
  fs.mkdirSync(path.join(base, "storage", "session"), { recursive: true });
  fs.writeFileSync(path.join(base, "storage", "session", "s1.json"), "{}");
  fs.writeFileSync(path.join(base, "run-history.json"), "[]");

  assert.equal(account.apply(A), true);
  assert.ok(fs.existsSync(path.join(base, "accounts", "user_A", "storage", "session", "s1.json")));
  assert.ok(fs.existsSync(path.join(base, "accounts", "user_A", "run-history.json")));
  assert.ok(!fs.existsSync(path.join(base, "storage")));

  assert.equal(account.apply(null), true);
  assert.equal(account.apply(B), true);
  assert.equal(account.dir(), path.join(base, "accounts", "user_B"));
  assert.ok(!fs.existsSync(path.join(base, "accounts", "user_B", "storage")));

  assert.equal(account.apply(A), true);
  assert.ok(fs.existsSync(path.join(account.dir(), "storage", "session", "s1.json")));
  assert.equal(account.apply(A), false);
});

test("the choice survives a relaunch", () => {
  const pointer = JSON.parse(fs.readFileSync(path.join(base, "active_account.json"), "utf8"));
  assert.equal(pointer.account_key, "user_A");
});
