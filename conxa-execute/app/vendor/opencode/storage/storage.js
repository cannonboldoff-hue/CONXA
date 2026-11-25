"use strict";
/**
 * Ported from opencode's storage service (packages/opencode/src/storage/
 * storage.ts at the commit pinned in ../../../NOTICE) — same
 * read/update/write/list/remove interface keyed by string[] path segments
 * (-> path.join(dir, ...key) + ".json"), same per-file locking (one lock per
 * target file, so unrelated sessions never contend with each other).
 *
 * Differences from the original, each a substitution not an omission:
 *  - FSUtil / Global.Path.data (opencode's private, unpublished
 *    @opencode-ai/core internals) -> plain fs/promises + Electron's userData dir.
 *  - The MIGRATIONS array and its Git dependency are dropped entirely —
 *    nothing to migrate, this is a new store.
 *  - list() does a flat single-level readdir under the prefix, not opencode's
 *    recursive glob — conxa-execute's key-space is two flat namespaces
 *    (session/<id>.json, message/<id>.json), not opencode's nested
 *    session/message/part hierarchy, so there is nothing to recurse into.
 *  - Locking is a plain promise-chain mutex per file path, not opencode's
 *    TxReentrantLock or the effect package's Semaphore (an earlier version of
 *    this file used effect's Effect.makeSemaphore(1) purely to get this same
 *    one-permit-per-file behavior, which pulled the whole `effect` package in
 *    as a runtime dependency for six lines of actual logic — replaced with a
 *    plain mutex so the dependency, and the FiberFailure-unwrapping ceremony
 *    it required, are gone). Same cost as before: a mutex has no separate
 *    read-lock mode, so concurrent reads of the same file still serialize
 *    like writes do.
 */
const fs = require("fs/promises");
const path = require("path");

class NotFoundError extends Error {
  constructor(target) {
    super(`Resource not found: ${target}`);
    this.name = "NotFoundError";
  }
}

function dataDir() {
  // CONXA_EXECUTE_STORAGE_DIR lets the self-check below (and any other test)
  // run under plain `node`, where `electron`'s app module doesn't resolve —
  // it's only ever a real module inside Electron's own main-process runtime.
  if (process.env.CONXA_EXECUTE_STORAGE_DIR) return process.env.CONXA_EXECUTE_STORAGE_DIR;
  // Per person (AUTH-26): chats live in the signed-in person's own folder.
  return path.join(require("../../../electron/account").dir(), "storage");
}

function keyToFile(key) {
  return path.join(dataDir(), ...key) + ".json";
}

function isMissing(err) {
  return Boolean(err) && err.code === "ENOENT";
}

// ponytail: a plain Map of promise chains, not an eagerly-evicting registry
// (opencode's RcMap) — a desktop app holding a few dozen chained promises in
// memory for its lifetime is not a real cost. Revisit if session-file counts
// ever get large enough to matter.
const _locks = new Map();
function withLock(target, fn) {
  const prior = _locks.get(target) || Promise.resolve();
  const next = prior.then(fn, fn); // run fn regardless of the prior chain's outcome
  // Swallow so an awaited failure doesn't become an unhandled rejection on
  // the chain itself — callers still see the real rejection via `next`.
  _locks.set(target, next.then(() => undefined, () => undefined));
  return next;
}

async function remove(key) {
  const target = keyToFile(key);
  return withLock(target, async () => {
    try {
      await fs.rm(target);
    } catch (err) {
      if (!isMissing(err)) throw err;
    }
  });
}

async function read(key) {
  const target = keyToFile(key);
  return withLock(target, async () => {
    try {
      return JSON.parse(await fs.readFile(target, "utf8"));
    } catch (err) {
      throw isMissing(err) ? new NotFoundError(target) : err;
    }
  });
}

async function write(key, content) {
  const target = keyToFile(key);
  return withLock(target, async () => {
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, JSON.stringify(content, null, 2));
  });
}

async function update(key, fn) {
  const target = keyToFile(key);
  return withLock(target, async () => {
    let content;
    try {
      content = JSON.parse(await fs.readFile(target, "utf8"));
    } catch (err) {
      throw isMissing(err) ? new NotFoundError(target) : err;
    }
    fn(content);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, JSON.stringify(content, null, 2));
    return content;
  });
}

async function list(prefix) {
  const dir = path.join(dataDir(), ...prefix);
  let entries;
  try {
    entries = await fs.readdir(dir);
  } catch (err) {
    if (isMissing(err)) return [];
    throw err;
  }
  return entries
    .filter((f) => f.endsWith(".json"))
    .map((f) => [...prefix, f.slice(0, -5)])
    .sort((a, b) => a.join("/").localeCompare(b.join("/")));
}

module.exports = { read, update, write, list, remove, NotFoundError };
