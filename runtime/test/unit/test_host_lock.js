"use strict";

// RT-3 follow-up: two runs both touching the SAME external platform (e.g. both hitting Render)
// concurrently is a real risk at the platform's own application layer (lost updates, one deploy
// cancelling another, bot/rate-limit detection) even though the runtime itself is now fully
// isolated per-run. host_lock.js serializes ONLY runs that overlap on a host — this suite proves
// that overlap blocks, non-overlap doesn't, give-up never leaves a host stuck, and a multi-host
// (Workflow Group) acquire is atomic (never half-held).

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const hostLock = require("../../app/host_lock");

function holder(runId, slug = "some-skill") {
  return { runId, slug };
}

test("two runs on DIFFERENT hosts never block each other", async () => {
  const a = await hostLock.acquireHosts(["render.com"], holder("r1"), {});
  const b = await hostLock.acquireHosts(["vercel.com"], holder("r2"), {});
  assert.ok(typeof a.release === "function");
  assert.ok(typeof b.release === "function");
  a.release();
  b.release();
});

test("two runs on the SAME host: second blocks until the first releases", async () => {
  const a = await hostLock.acquireHosts(["render.com"], holder("r1"), {});
  assert.ok(a.release);

  let bSettled = false;
  const bPromise = hostLock.acquireHosts(["render.com"], holder("r2"), {}).then((r) => { bSettled = true; return r; });

  // Give the event loop a couple ticks — b must still be waiting (host_lock polls every 250ms
  // by default; well under that, b cannot have acquired yet).
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(bSettled, false, "second acquirer must not succeed while the first still holds the lock");

  a.release();
  const b = await bPromise;
  assert.ok(bSettled);
  assert.ok(typeof b.release === "function");
  b.release();
});

test("giving up (isDone) never leaves the host stuck for a later acquirer", async () => {
  const a = await hostLock.acquireHosts(["render.com"], holder("r1"), {});
  assert.ok(a.release);

  // r2 gives up almost immediately (simulates a run whose deadline already passed / was cancelled).
  let calls = 0;
  const r2 = await hostLock.acquireHosts(["render.com"], holder("r2"), {
    isDone: () => (++calls >= 1), pollMs: 5,
  });
  assert.strictEqual(r2.release, undefined, "a given-up acquire must not return a release function");
  assert.strictEqual(r2.host, "render.com");
  assert.strictEqual(r2.blocker.runId, "r1");

  a.release();
  // r3 must be able to acquire cleanly — r2's give-up must not have left any reservation behind.
  const r3 = await hostLock.acquireHosts(["render.com"], holder("r3"), {});
  assert.ok(typeof r3.release === "function");
  r3.release();
});

test("multi-host acquire is atomic — never half-held when one host is already taken", async () => {
  const a = await hostLock.acquireHosts(["render.com"], holder("r1"), {});
  assert.ok(a.release);

  // r2 needs BOTH render.com (taken) and vercel.com (free) — must not silently claim vercel.com
  // while blocked on render.com, or a third run needing only vercel.com would wrongly block too.
  let calls = 0;
  const r2 = await hostLock.acquireHosts(["vercel.com", "render.com"], holder("r2"), {
    isDone: () => (++calls >= 1), pollMs: 5,
  });
  assert.strictEqual(r2.release, undefined);

  // vercel.com must still be free — r2's blocked attempt never partially claimed it.
  const r3 = await hostLock.acquireHosts(["vercel.com"], holder("r3"), {});
  assert.ok(typeof r3.release === "function", "vercel.com must remain free after r2's blocked multi-host attempt");
  r3.release();
  a.release();
});

test("host order in the input list doesn't matter — canonical sort prevents ordering-based deadlock", async () => {
  const a = await hostLock.acquireHosts(["render.com", "vercel.com"], holder("r1"), {});
  assert.ok(a.release);
  const activeAfterA = hostLock.activeHosts().map((h) => h.host).sort();
  assert.deepStrictEqual(activeAfterA, ["render.com", "vercel.com"]);
  a.release();

  // Same two hosts, reversed input order, from a different holder — must acquire cleanly now
  // that a released them, proving the sort makes acquisition order-independent.
  const b = await hostLock.acquireHosts(["vercel.com", "render.com"], holder("r2"), {});
  assert.ok(b.release);
  b.release();
});

test("a run with no resolvable target host acquires immediately (fail open, not closed)", async () => {
  const r = await hostLock.acquireHosts([], holder("r1"), {});
  assert.ok(typeof r.release === "function");
  r.release();
});

test("RT-15: two calls of one public-site skill (group with no sign-in apps) run one after the other", async () => {
  const { resolveTargetHosts } = require("../../app/target_hosts");
  const entry = { workspace_id: "acme", slug: "wiki_search", manifest: { group_id: "g1", target_url: "https://en.wikipedia.org/" } };
  const hosts = () => resolveTargetHosts([{ entry }], { resolveGroup: () => ({ id: "g1", apps: [] }) });
  const a = await hostLock.acquireHosts(hosts(), holder("r1", "wiki_search"), {});
  let calls = 0;
  const b = await hostLock.acquireHosts(hosts(), holder("r2", "wiki_search"), { isDone: () => (++calls >= 2), pollMs: 5 });
  assert.strictEqual(b.release, undefined, "the second call must wait while the first holds the host");
  assert.strictEqual(b.host, "en.wikipedia.org");
  a.release();
  const c = await hostLock.acquireHosts(hosts(), holder("r3", "wiki_search"), {});
  assert.ok(typeof c.release === "function");
  c.release();
});

test("activeHosts() reports who holds what", async () => {
  const a = await hostLock.acquireHosts(["render.com"], holder("r1", "deploy-service"), {});
  const active = hostLock.activeHosts();
  const row = active.find((h) => h.host === "render.com");
  assert.ok(row);
  assert.strictEqual(row.run_id, "r1");
  assert.strictEqual(row.skill, "deploy-service");
  a.release();
  assert.strictEqual(hostLock.activeHosts().find((h) => h.host === "render.com"), undefined);
});

test("an unwritable locksDir fails OPEN (in-process-only), never refuses the run", async () => {
  // A FILE (not a directory) at the locks-dir path makes file_lock.js's mkdirSync/writeFileSync
  // fail — simulates a read-only/unwritable data dir without touching real permissions.
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "host-lock-fail-"));
  const blockedLocksDir = path.join(parent, "not-a-dir");
  fs.writeFileSync(blockedLocksDir, "");
  const r = await hostLock.acquireHosts(["render.com"], holder("r1"), { locksDir: blockedLocksDir });
  assert.ok(typeof r.release === "function", "must degrade to in-process locking, not report a blocker");
  assert.strictEqual(r.blocker, undefined);
  r.release();
});
