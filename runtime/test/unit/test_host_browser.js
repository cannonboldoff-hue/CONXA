"use strict";
// host_browser.js's one job is to be INERT for every client except Conxa Execute, and to
// never turn "Execute's panel is unreachable" into a failed run. Both are load-bearing
// promises from the plan this shipped under ("Conxa Execute — in-app browser panel") —
// see browser.js::_buildExecContext for the branch these pin.
const test   = require("node:test");
const assert = require("node:assert");

const { _buildExecContext, RUN_VIEWPORT } = require("../../app/browser");

test("with CONXA_HOST_BROWSER_CDP unset, _buildExecContext takes the launch path", async () => {
  delete process.env.CONXA_HOST_BROWSER_CDP;
  const { browser, context, hostOwned } = await _buildExecContext(undefined, false, { runId: "r_unit_1" });
  try {
    assert.ok(!hostOwned, "a launched browser must not be reported as hostOwned");
    assert.ok(context, "a launched browser still gets a real context");
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
});

test("a launched context holds the page at RUN_VIEWPORT", async () => {
  delete process.env.CONXA_HOST_BROWSER_CDP;
  const { browser, context } = await _buildExecContext(undefined, true, {});
  try {
    const page = await context.newPage();
    assert.deepStrictEqual(page.viewportSize(), RUN_VIEWPORT);
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
});

test("with the endpoint set but unreachable, it logs the fallback and still returns a working launched browser", async () => {
  process.env.CONXA_HOST_BROWSER_CDP = "http://127.0.0.1:1"; // port 1: nothing listens there
  const logs = [];
  try {
    const { browser, context, hostOwned } = await _buildExecContext(undefined, false, {
      runId: "r_unit_2",
      logFn: (level, event, meta) => logs.push({ level, event, meta }),
    });
    try {
      assert.ok(!hostOwned, "a fallen-back browser must not be reported as hostOwned");
      assert.ok(context, "the run still gets a real, working browser");
      assert.ok(
        logs.some((l) => l.event === "host_browser_fallback"),
        "the fallback must be logged, not silently swallowed",
      );
    } finally {
      await context.close().catch(() => {});
      await browser.close().catch(() => {});
    }
  } finally {
    delete process.env.CONXA_HOST_BROWSER_CDP;
  }
});

test("headless runs never attempt the host branch, even with the endpoint set", async () => {
  // Every scheduled/Claude-Desktop run is headless — this is what makes host_browser.js
  // inert for them regardless of what env vars happen to be set (they never set these,
  // but a shared machine running both Execute and a scheduled task is exactly the case
  // this guards).
  process.env.CONXA_HOST_BROWSER_CDP = "http://127.0.0.1:1";
  const logs = [];
  try {
    const { browser, context, hostOwned } = await _buildExecContext(undefined, true, {
      runId: "r_unit_3",
      logFn: (level, event, meta) => logs.push({ level, event, meta }),
    });
    try {
      assert.ok(!hostOwned);
      assert.ok(context);
      assert.ok(
        !logs.some((l) => l.event === "host_browser_fallback"),
        "a headless run must never even attempt the host branch — nothing to fall back from",
      );
    } finally {
      await context.close().catch(() => {});
      await browser.close().catch(() => {});
    }
  } finally {
    delete process.env.CONXA_HOST_BROWSER_CDP;
  }
});

test("without a runId, the host branch is never attempted even when headed and the endpoint is set", async () => {
  // Mirrors browser.js's two no-app/no-required-app early-return paths (getGroupAuthContext),
  // which call _buildExecContext(undefined, headless) with no opts at all.
  process.env.CONXA_HOST_BROWSER_CDP = "http://127.0.0.1:1";
  const logs = [];
  try {
    const { browser, context, hostOwned } = await _buildExecContext(undefined, false, {
      logFn: (level, event, meta) => logs.push({ level, event, meta }),
    });
    try {
      assert.ok(!hostOwned);
      assert.ok(context);
      assert.ok(!logs.some((l) => l.event === "host_browser_fallback"));
    } finally {
      await context.close().catch(() => {});
      await browser.close().catch(() => {});
    }
  } finally {
    delete process.env.CONXA_HOST_BROWSER_CDP;
  }
});
