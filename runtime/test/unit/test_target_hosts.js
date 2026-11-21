"use strict";

// Platform-tag / multi-host lock keying: a group workflow that starts in one app
// and links into a sibling mid-recording (e.g. "Deploy a Service on Render then
// Visit frontend on Vercel") must resolve to EVERY app in its group, so a
// sibling run overlapping ANY of those hosts serializes against it while runs on
// disjoint platforms stay parallel. Proves resolveTargetHosts (extracted from
// server.js) computes that union — a group is the unit of sign-in, so it is also
// the unit of locking. See conxa Workflow.visited_hosts for the Studio-side twin.

const test = require("node:test");
const assert = require("node:assert");

const { _hostOf, resolveTargetHosts } = require("../../app/target_hosts");

const RENDER = { id: "app_render", name: "Render", login_url: "https://dashboard.render.com/login", success_url: "https://dashboard.render.com/home" };
const VERCEL = { id: "app_vercel", name: "Vercel", login_url: "https://vercel.com/login", success_url: "https://vercel.com/{}" };
const BILLING = { id: "app_billing", name: "Billing", login_url: "https://billing.example.com/login", success_url: "https://billing.example.com/home" };

function deps(group) {
  return { resolveGroup: (_workspaceId, _groupId) => group };
}

test("_hostOf handles full URLs, bad input, and returns lowercase hostnames via URL semantics", () => {
  assert.strictEqual(_hostOf("https://Dashboard.Render.com/x"), "dashboard.render.com");
  assert.strictEqual(_hostOf("not a url"), "");
  assert.strictEqual(_hostOf(""), "");
});

test("a group skill resolves to EVERY app's host in its group", () => {
  const resolved = [{
    entry: {
      workspace_id: "acme",
      slug: "deploy_render_visit_vercel",
      manifest: { group_id: "g1", target_url: "https://dashboard.render.com" },
    },
  }];
  const hosts = resolveTargetHosts(resolved, deps({ id: "g1", apps: [RENDER, VERCEL] }));
  assert.deepStrictEqual(hosts.sort(), ["dashboard.render.com", "vercel.com"]);
});

test("a group app the skill never touches still counts — the whole group is signed in and locked as one", () => {
  const resolved = [{
    entry: {
      workspace_id: "acme",
      slug: "deploy_only",
      manifest: { group_id: "g1", target_url: "https://dashboard.render.com" },
    },
  }];
  const hosts = resolveTargetHosts(resolved, deps({ id: "g1", apps: [RENDER, VERCEL, BILLING] }));
  assert.deepStrictEqual(hosts.sort(), ["billing.example.com", "dashboard.render.com", "vercel.com"]);
});

test("a non-group (standalone) skill contributes its own target_url host", () => {
  const resolved = [{
    entry: {
      workspace_id: "acme",
      slug: "standalone",
      manifest: { target_url: "https://example.com/app" },
    },
  }];
  const hosts = resolveTargetHosts(resolved, deps(null));
  assert.deepStrictEqual(hosts, ["example.com"]);
});

test("RT-15: a group with no sign-in apps still locks the skill's starting-page host", () => {
  // WF-16 16.3 attempt 1: a public-site skill in a group with apps: [] resolved to no host, so two
  // calls of the same skill ran side by side. The target_url host must count.
  const resolved = [{
    entry: { workspace_id: "acme", slug: "wiki_search", manifest: { group_id: "g1", target_url: "https://en.wikipedia.org/wiki/Main_Page" } },
  }];
  assert.deepStrictEqual(resolveTargetHosts(resolved, deps({ id: "g1", apps: [] })), ["en.wikipedia.org"]);
});

test("RT-15: a group skill's starting-page host counts even when no group app names it", () => {
  const resolved = [{
    entry: { workspace_id: "acme", slug: "billing_report", manifest: { group_id: "g1", target_url: "https://reports.example.org/x" } },
  }];
  const hosts = resolveTargetHosts(resolved, deps({ id: "g1", apps: [RENDER] }));
  assert.deepStrictEqual(hosts.sort(), ["dashboard.render.com", "reports.example.org"]);
});

test("an unresolvable group contributes no host (fail open — nothing to lock)", () => {
  const resolved = [{
    entry: {
      workspace_id: "acme",
      slug: "ghost_group",
      manifest: { group_id: "g_missing" },
    },
  }];
  const hosts = resolveTargetHosts(resolved, { resolveGroup: () => null });
  assert.deepStrictEqual(hosts, []);
});

test("only target_url names a host — entry_url/login_url are never written onto a manifest", () => {
  const resolved = [{
    entry: { workspace_id: "acme", slug: "legacy_fields", manifest: { entry_url: "https://a.example.com", login_url: "https://b.example.com" } },
  }];
  assert.deepStrictEqual(resolveTargetHosts(resolved, deps(null)), []);
});
