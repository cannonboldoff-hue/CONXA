"use strict";
// EXEC-49 P3: a step that fails because the APP refused (403, offline, a rejected form) stops the run
// with a typed reason before the recovery cascade; a plain selector miss, a decision popup and a stale
// alert on a step that never acted are NOT faults.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-fault-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");

const pageScripts = require("../../app/page_scripts");
const pageStatus = require("../../app/page_status");
const { classifyFault, faultMessage, statusKind } = require("../../app/fault_classify");
const { detectFault, runPlan } = require("../../app/run");

// ─── fault_classify (pure) ─────────────────────────────────────────────────────────────────

test("statusKind maps the refusals that mean something and ignores the rest", () => {
  assert.strictEqual(statusKind(401), "auth_expired");
  assert.strictEqual(statusKind(403), "no_permission");
  assert.strictEqual(statusKind(404), "not_found");
  assert.strictEqual(statusKind(429), "rate_limited");
  assert.strictEqual(statusKind(503), "network");
  assert.strictEqual(statusKind(200), null);
  assert.strictEqual(statusKind(400), null);
  assert.strictEqual(statusKind(undefined), null);
});

test("offline and a fresh status count for any step, even one that never acted", () => {
  assert.strictEqual(classifyFault({ offline: true }).kind, "network");
  assert.strictEqual(classifyFault({ status: 403, mayHaveActed: false }).kind, "no_permission");
});

test("alert text counts only when the step may already have acted", () => {
  const alerts = ["Name already exists. Choose another."];
  assert.strictEqual(classifyFault({ alerts, mayHaveActed: true }).kind, "duplicate");
  assert.strictEqual(classifyFault({ alerts, mayHaveActed: false }), null, "a stale alert is not this step's doing");
});

test("each text kind is recognised, most specific first", () => {
  const kind = (t) => (classifyFault({ alerts: [t], mayHaveActed: true }) || { kind: null }).kind;
  assert.strictEqual(kind("Your session has expired. Please sign in again."), "auth_expired");
  assert.strictEqual(kind("You don't have permission to do that"), "no_permission");
  assert.strictEqual(kind("Too many requests, slow down"), "rate_limited");
  assert.strictEqual(kind("Network error: failed to fetch"), "network");
  assert.strictEqual(kind("This project no longer exists"), "not_found");
  assert.strictEqual(kind("Email is invalid"), "validation");
  assert.strictEqual(kind("Saved successfully"), null);
  assert.strictEqual(kind(""), null);
});

test("faultMessage is plain and quotes the app's own words", () => {
  assert.match(faultMessage({ kind: "duplicate", source: "text", evidence: "Name already exists" }), /already exists: "Name already exists"/);
  assert.match(faultMessage({ kind: "no_permission", source: "status", evidence: "the app answered with status 403" }), /don't have access.*403/);
});

// ─── page_status ───────────────────────────────────────────────────────────────────────────

function respPage(responses) {
  const handlers = {};
  const page = { on: (ev, fn) => { handlers[ev] = fn; }, mainFrame: () => "main" };
  pageStatus.attach(page);
  const feed = (r) => handlers.response({
    status: () => r.status,
    request: () => ({ isNavigationRequest: () => !!r.nav, resourceType: () => r.type || "document" }),
    frame: () => (r.nav ? "main" : "other"),
  });
  responses.forEach(feed);
  return { page, feed };
}

test("page_status keeps a main-frame 4xx/5xx, and a good page load clears it", () => {
  const { page, feed } = respPage([{ status: 403, nav: true }]);
  assert.strictEqual(pageStatus.recent(page, 0).status, 403);
  feed({ status: 200, nav: true });
  assert.strictEqual(pageStatus.recent(page, 0), null);
});

test("page_status ignores a background 404 and non-xhr resources, keeps an xhr 401/429/5xx", () => {
  const { page, feed } = respPage([{ status: 404, type: "xhr" }, { status: 500, type: "image" }]);
  assert.strictEqual(pageStatus.recent(page, 0), null);
  feed({ status: 429, type: "fetch" });
  assert.strictEqual(pageStatus.recent(page, 0).status, 429);
});

test("page_status only returns a refusal that arrived after the step started", () => {
  const { page } = respPage([{ status: 403, nav: true }]);
  assert.strictEqual(pageStatus.recent(page, Date.now() + 60_000), null);
});

// ─── faultProbe (runs in the page realm) ───────────────────────────────────────────────────

test("faultProbe is a self-contained function the page can parse on its own", () => {
  assert.strictEqual(typeof pageScripts.faultProbe, "function");
  assert.doesNotThrow(() => new Function(`return (${pageScripts.faultProbe.toString()})`)());
});

// ─── detectFault ───────────────────────────────────────────────────────────────────────────

function mockPage({ dialog = null, fault = { offline: false, alerts: [], invalid: [] } } = {}) {
  return {
    url: () => "https://app.test/projects",
    title: async () => "Projects",
    on: () => {},
    mainFrame: () => "main",
    context: () => ({ on: () => {} }),
    waitForLoadState: async () => {},
    evaluate: async (fn) => {
      if (fn === pageScripts.dialogProbe) return dialog;
      if (fn === pageScripts.faultProbe) return fault;
      return null;
    },
  };
}
const CTX = { dialogQueue: [] };

test("detectFault: an alert on a step that acted is a fault; a clean page is not", async () => {
  const page = mockPage({ fault: { offline: false, alerts: ["Name already exists"], invalid: [] } });
  assert.strictEqual((await detectFault(CTX, page, { type: "click" }, { mayHaveActed: true, sinceMs: 0 })).kind, "duplicate");
  assert.strictEqual(await detectFault(CTX, page, { type: "click" }, { mayHaveActed: false, sinceMs: 0 }), null);
  assert.strictEqual(await detectFault(CTX, mockPage(), { type: "click" }, { mayHaveActed: true, sinceMs: 0 }), null);
});

test("detectFault: an aria-invalid message counts like an alert", async () => {
  const page = mockPage({ fault: { offline: false, alerts: [], invalid: ["Email is invalid"] } });
  assert.strictEqual((await detectFault(CTX, page, { type: "type" }, { mayHaveActed: true, sinceMs: 0 })).kind, "validation");
});

test("detectFault: a decision popup is never a fault, and neither is a fork step or a pending native dialog", async () => {
  const fault = { offline: true, alerts: ["Session expired"], invalid: [] };
  const decision = { title: "Upload options", text: "", controls: [{ name: "Replace" }, { name: "Keep both" }, { name: "Cancel" }] };
  assert.strictEqual(await detectFault(CTX, mockPage({ dialog: decision, fault }), { type: "click" }, { mayHaveActed: true, sinceMs: 0 }), null);
  assert.strictEqual(await detectFault(CTX, mockPage({ fault }), { type: "outcome_fork" }, { mayHaveActed: true, sinceMs: 0 }), null);
  assert.strictEqual(await detectFault({ dialogQueue: [{}] }, mockPage({ fault }), { type: "click" }, { mayHaveActed: true, sinceMs: 0 }), null);
});

test("detectFault: a 403 the page returned after the step started is a fault", async () => {
  const page = mockPage();
  const since = Date.now();
  pageStatus.note(page, { status: 403, nav: true, at: since + 5 });
  assert.strictEqual((await detectFault(CTX, page, { type: "navigate" }, { mayHaveActed: false, sinceMs: since })).kind, "no_permission");
  assert.strictEqual(await detectFault(CTX, page, { type: "navigate" }, { mayHaveActed: false, sinceMs: since + 60_000 }), null);
});

// ─── end to end through runPlan ────────────────────────────────────────────────────────────

test("runPlan: a navigation that ends in a 403 stops the run as a typed fault, skipping recovery", async () => {
  const events = [];
  const page = mockPage();
  page.goto = async () => {
    pageStatus.note(page, { status: 403, nav: true, at: Date.now() });
    throw new Error("net::ERR_HTTP_RESPONSE_CODE_FAILURE");
  };
  const tracker = { emit: (code, data) => events.push([code, data]) };
  await assert.rejects(
    runPlan(page, [{ type: "navigate", url: "https://app.test/secret" }], {}, 0, "slug-fault", { tracker }),
    (err) => {
      assert.strictEqual(err.fault, true);
      assert.strictEqual(err.faultKind, "no_permission");
      assert.strictEqual(err.failedAt, 0);
      assert.match(err.message, /don't have access/);
      return true;
    },
  );
  assert.ok(events.some(([c, d]) => c === "step_fail" && d.fc === "fault_no_permission"));
  assert.ok(!events.some(([c]) => c === "repair_event"), "no recovery was attempted");
});
