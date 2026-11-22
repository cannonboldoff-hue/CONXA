"use strict";
// A host-owned run whose page is not at the recorded size (and the host cannot put it back) stops before
// the step, as a typed fault, with no recovery and no agent round. A page the host repairs runs on.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-pagesize-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");
const Module = require("node:module");

const repairs = [];
let repairFixesPage = false;
const realLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === "./host_browser") {
    return { ensureGeometry: async (a) => { repairs.push(a); if (repairFixesPage) pageWidth = 1280; return { ok: true }; } };
  }
  return realLoad.call(this, request, ...rest);
};
const { runPlan } = require("../../app/run");

let pageWidth = 1024; // a page Playwright's screenshot put back at native size
function page() {
  return {
    url: () => "https://app.test/home",
    title: async () => "Home",
    on: () => {},
    mainFrame: () => "main",
    context: () => ({ on: () => {} }),
    waitForLoadState: async () => {},
    waitForTimeout: async () => {},
    goto: async () => {},
    evaluate: async (fn) => (String(fn).includes("devicePixelRatio") ? [pageWidth, 720, 1.25] : null),
  };
}
const OPTS = (events) => ({ tracker: { emit: (c, d) => events.push([c, d]) }, hostOwned: true, hostRunId: "run-1" });

test("runPlan: a host-owned page stuck at the wrong size ends the run as a page_size fault before the step", async () => {
  pageWidth = 1024; repairFixesPage = false; repairs.length = 0;
  const events = [];
  await assert.rejects(
    runPlan(page(), [{ type: "navigate", url: "https://app.test/next" }], {}, 0, "slug-size", OPTS(events)),
    (err) => {
      assert.strictEqual(err.fault, true);
      assert.strictEqual(err.faultKind, "page_size");
      assert.strictEqual(err.failedAt, 0);
      assert.match(err.message, /not showing the page at its normal size/);
      assert.doesNotMatch(err.message, /Build Studio|the test/, "shared by both apps: names neither");
      return true;
    },
  );
  assert.deepStrictEqual(repairs, [{ runId: "run-1" }], "the host was asked to repair it once");
  assert.ok(events.some(([c, d]) => c === "step_fail" && d.fc === "fault_page_size"));
  assert.ok(!events.some(([c]) => c === "repair_event"), "no recovery was attempted");
});

test("runPlan: a page the host repairs runs the step normally", async () => {
  pageWidth = 1024; repairFixesPage = true; repairs.length = 0;
  const events = [];
  await runPlan(page(), [{ type: "navigate", url: "https://app.test/next" }], {}, 0, "slug-size-ok", OPTS(events));
  assert.strictEqual(repairs.length, 1);
  assert.ok(!events.some(([c, d]) => c === "step_fail" && d.fc === "fault_page_size"));
});

test("runPlan: a page with no width (the window is minimized) says to bring the window back", async () => {
  pageWidth = 0; repairFixesPage = false; repairs.length = 0;
  await assert.rejects(
    runPlan(page(), [{ type: "navigate", url: "https://app.test/next" }], {}, 0, "slug-size-hidden", OPTS([])),
    (err) => err.faultKind === "page_size" && /minimized or hidden/.test(err.message) && /Bring the window back/.test(err.message)
      && !/Build Studio|the test/.test(err.message),
  );
});
