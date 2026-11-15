"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const vm = require("../../app/version_manager");

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "vm-names-"));
// No released_at on purpose: ordering must then come from the folder name (semver).
function mk(root, name) {
  const dir = path.join(root, name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "server.js"), "module.exports = {};");
  fs.writeFileSync(path.join(dir, "version.json"), "{}");
  return dir;
}
const names = (root) => vm.listVersions(root).map((v) => v.name);
const act = (root, dir, keep = 3) => vm.activate(root, dir, { requiredFiles: ["server.js"], keep });

test("parseVersionDir accepts plain, app-, host- and local-dev names; rejects junk", () => {
  assert.strictEqual(vm.parseVersionDir("v1.2.3").prefix, "");
  assert.strictEqual(vm.parseVersionDir("app-v1.2.3").prefix, "app");
  assert.strictEqual(vm.parseVersionDir("host-v1.2.3").version, "1.2.3");
  const dev = vm.parseVersionDir("app-v0.0.0-local.20261114215504");
  assert.deepStrictEqual(dev.prerelease, ["local", "20261114215504"]);
  for (const bad of ["current", "1.2.3", "v1.2", "foo-v1.2.3", "app-1.2.3", "v1.2.3.4"]) {
    assert.strictEqual(vm.parseVersionDir(bad), null, bad);
  }
});

for (const name of ["v1.2.3", "app-v1.2.3", "host-v1.2.3", "app-v0.0.0-local.20261114215504"]) {
  test(`listVersions lists ${name}`, () => {
    const root = tmp();
    mk(root, name);
    assert.deepStrictEqual(names(root), [name]);
    fs.rmSync(root, { recursive: true, force: true });
  });
}

test("ordering is semver: numeric parts, release beats prerelease, local builds by timestamp", () => {
  const root = tmp();
  for (const n of [
    "app-v1.9.0", "app-v1.10.0", "app-v1.10.0-rc.1",
    "app-v0.0.0-local.20261114215504", "app-v0.0.0-local.20261115010101", "app-v0.0.0-local.9",
  ]) mk(root, n);
  assert.deepStrictEqual(names(root), [
    "app-v1.10.0", "app-v1.10.0-rc.1", "app-v1.9.0",
    "app-v0.0.0-local.20261115010101", "app-v0.0.0-local.20261114215504", "app-v0.0.0-local.9",
  ]);
  fs.rmSync(root, { recursive: true, force: true });
});

test("rollback picks the previous app-v folder, never the current one", () => {
  const root = tmp();
  const a = mk(root, "app-v1.0.0");
  const b = mk(root, "app-v1.1.0");
  act(root, a);
  act(root, b);
  assert.strictEqual(vm.currentVersion(root), "app-v1.1.0");
  assert.strictEqual(vm.rollback(root).version, "app-v1.0.0");
  assert.strictEqual(vm.currentVersion(root), "app-v1.0.0");
  fs.rmSync(root, { recursive: true, force: true });
});

test("rollback from the middle goes to the next-older, not back to the newest", () => {
  const root = tmp();
  for (const n of ["host-v1.0.0", "host-v1.1.0", "host-v1.2.0"]) mk(root, n);
  act(root, path.join(root, "host-v1.1.0"));
  assert.strictEqual(vm.rollback(root).version, "host-v1.0.0");
  fs.rmSync(root, { recursive: true, force: true });
});

test("rollback with one version returns null and leaves current alone", () => {
  const root = tmp();
  act(root, mk(root, "app-v1.0.0"));
  assert.strictEqual(vm.rollback(root), null);
  assert.strictEqual(vm.currentVersion(root), "app-v1.0.0");
  fs.rmSync(root, { recursive: true, force: true });
});

test("prune keeps current + immediately previous even at keep=1, deletes the rest", () => {
  const root = tmp();
  const dirs = [];
  for (const n of ["app-v1.0.0", "app-v1.1.0", "app-v1.2.0", "app-v1.3.0"]) {
    dirs.push(mk(root, n)); // real flow: a version lands on disk right before its activate
    act(root, dirs[dirs.length - 1], 1);
  }
  assert.deepStrictEqual(names(root), ["app-v1.3.0", "app-v1.2.0"]);
  assert.strictEqual(fs.existsSync(dirs[0]), false);
  assert.strictEqual(fs.existsSync(dirs[1]), false);
  assert.strictEqual(vm.currentVersion(root), "app-v1.3.0");
  fs.rmSync(root, { recursive: true, force: true });
});

test("prune never deletes current even when current is not the newest folder", () => {
  const root = tmp();
  const old = mk(root, "app-v1.0.0");
  mk(root, "app-v1.1.0");
  mk(root, "app-v1.2.0");
  mk(root, "app-v1.3.0");
  act(root, old, 1); // current = oldest; the three newer ones outrank it
  assert.strictEqual(fs.existsSync(old), true);
  assert.strictEqual(vm.currentVersion(root), "app-v1.0.0");
  fs.rmSync(root, { recursive: true, force: true });
});
