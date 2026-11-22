"use strict";
// WF-19 F6: an abandoned sign-in can be cancelled, which frees its callback port at once; a newer sign-in
// replaces one the person walked away from. `electron` is stubbed; nothing here reaches Clerk.
const test = require("node:test");
const assert = require("node:assert");
const Module = require("module");
const http = require("http");

process.env.CONXA_EXECUTE_CLERK_DOMAIN = "https://clerk.test";
process.env.CONXA_EXECUTE_CLERK_CLIENT_ID = "client-test";
process.env.CONXA_EXECUTE_AUTH_PORT = "52950";

const opened = []; // every authorize URL the app asked the browser to open
const realLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === "electron") {
    return {
      app: { isPackaged: true, getPath: () => require("os").tmpdir() },
      shell: { openExternal: async (url) => { opened.push(new URL(url)); } },
      safeStorage: { isEncryptionAvailable: () => false },
    };
  }
  return realLoad.call(this, request, ...rest);
};
const auth = require("../auth_service");
Module._load = realLoad;

const tick = () => new Promise((r) => setImmediate(r));
const waitOpened = async (n) => { while (opened.length < n) await tick(); };
const portOf = (u) => Number(new URL(u.searchParams.get("redirect_uri")).port);
const canListen = (port) => new Promise((resolve) => {
  const s = http.createServer();
  s.once("error", () => resolve(false));
  s.listen(port, "127.0.0.1", () => s.close(() => resolve(true)));
});

test("cancelLogin ends the waiting sign-in with login_cancelled and frees its port", async () => {
  const before = opened.length;
  const waiting = assert.rejects(auth.login(), /login_cancelled/);
  await waitOpened(before + 1);
  const port = portOf(opened[before]);
  assert.strictEqual(await canListen(port), false, "the callback port is held while the person signs in");
  auth.cancelLogin();
  await waiting;
  await tick();
  assert.strictEqual(await canListen(port), true, "the port is free again right after Cancel");
});

test("a second sign-in cancels the first one", async () => {
  const before = opened.length;
  const first = assert.rejects(auth.login(), /login_cancelled/);
  await waitOpened(before + 1);
  const second = assert.rejects(auth.login(), /login_cancelled/);
  await first; // replaced, not left waiting for 300 s
  await waitOpened(before + 2);
  auth.cancelLogin(); // ends the second, so the test leaves nothing listening
  await second;
});

test("cancelLogin with nothing waiting does nothing", () => {
  auth.cancelLogin();
});
