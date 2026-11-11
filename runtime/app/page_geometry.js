"use strict";
/**
 * page_geometry.js — the runtime's side of "the page keeps the size it was recorded at".
 *
 * A host panel (Build Studio / Conxa Execute) holds each page at RUN_VIEWPORT's width, DPR 1, with
 * Emulation.setDeviceMetricsOverride. That override is one shared setting per page, and Playwright's
 * page.screenshot() overwrites it: it sends Page.captureScreenshot WITH a clip, and Chromium then
 * "restores" the page to what Playwright's own CDP session last knew (nothing), i.e. the screen's
 * native DPR. The page ends up drawn too big and clipped. Re-sending the same override is a no-op.
 *
 * So two rules live here:
 *   1. capture() is the only way the runtime takes a screenshot: raw CDP, no clip.
 *   2. checkGeometry() measures the live page and asks the host panel (the one owner of the size) to
 *      repair it when it is wrong. A test keeps both rules (test_page_geometry_single_owner.py).
 */

const { evalOn, EVAL_TIMED_OUT } = require("./page_eval");

// The page width every run sees, and its default height, matching what the recorder starts at
// (host_view.py's RECORD_VIEWPORT; a pytest keeps them equal). The WIDTH is the contract: layout
// breakpoints hang on it. Studio's panel lets the height follow its pane (viewport fit "width"), so a
// recording is made at the pane's height and a replay is given that same height (runViewport).
const RUN_VIEWPORT = { width: 1280, height: 720 };
// The page heights a host panel may hand out; mirrors browser_panel.js (a pytest keeps them equal).
const MIN_H = 480, MAX_H = 1440;

// The viewport a run's browser is created at: the contract width and the height the workflow was
// recorded at (the manifest's environment.viewport.h), kept to what a panel can show. A manifest
// without one gets the default height.
function runViewport(recordedHeight) {
  const h = Math.round(Number(recordedHeight));
  return { width: RUN_VIEWPORT.width, height: Number.isFinite(h) && h > 0 ? Math.min(MAX_H, Math.max(MIN_H, h)) : RUN_VIEWPORT.height };
}

const _sessions = new WeakMap(); // page -> Promise<CDPSession>

function _session(page) {
  let s = _sessions.get(page);
  if (!s) {
    s = page.context().newCDPSession(page);
    _sessions.set(page, s);
  }
  return s;
}

// Takes the page's current viewport as a JPEG/PNG Buffer, or null. Never throws, like the
// `.screenshot().catch(() => null)` calls it replaces.
async function capture(page, { type = "jpeg", quality, timeout = 30000 } = {}) {
  // shortcut: fakes only — a real Chromium page always has newCDPSession. Only a missing API falls back,
  // never an error, so a real page never takes the clip-sending path.
  if (!page || typeof page.context !== "function" || typeof page.context().newCDPSession !== "function") {
    return page.screenshot({ type, ...(quality ? { quality } : {}), timeout }).catch(() => null);
  }
  let timer;
  try {
    const cdp = await _session(page);
    const params = { format: type === "png" ? "png" : "jpeg" };
    if (params.format === "jpeg" && quality) params.quality = quality;
    const shot = await Promise.race([
      cdp.send("Page.captureScreenshot", params),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("capture timed out")), timeout); }),
    ]);
    return Buffer.from(shot.data, "base64");
  } catch (_) {
    _sessions.delete(page); // a detached session would fail every later call
    return null;
  } finally {
    clearTimeout(timer);
  }
}

const _measure = () => [window.innerWidth, window.innerHeight, window.devicePixelRatio];

// A page that could not be measured (timed out, or answered with something else) is not a wrong-sized
// page: the check is advisory then.
const _unmeasured = (live) => live === EVAL_TIMED_OUT || !Array.isArray(live);

// The width and DPR are the contract; the height follows the host's pane, so any height a panel can
// hand out is right. What this catches is Playwright's clipped screenshot putting the page back at the
// screen's native DPR, which also changes the width.
function _matches(live) {
  return live[0] === RUN_VIEWPORT.width && live[1] >= MIN_H && Math.abs(live[2] - 1) < 0.01;
}

// { ok: true } when the page is at the contract size (or could not be measured: advisory, a busy page
// is not a wrong-sized one). Otherwise asks the host panel to repair it and measures again:
// { ok: false, live } when the repair did not hold, { ok: false, unsupported: true } when the host is
// too old to know the repair op or not answering (the app layer updates separately from the host exe).
async function checkGeometry(page, { hostOwned, hostRunId } = {}) {
  // Only a host panel owns (and can repair) the page size. A browser the runtime launched itself is
  // created at its viewport by Playwright and nothing else resizes it.
  if (!hostOwned || !hostRunId) return { ok: true };
  const first = await evalOn(page, _measure).catch(() => EVAL_TIMED_OUT);
  if (_unmeasured(first) || _matches(first)) return { ok: true };
  try {
    await require("./host_browser").ensureGeometry({ runId: hostRunId });
  } catch (_) {
    // An older host answers HTTP 500 "unknown op"; a wedged one times out. Either way it cannot repair.
    return { ok: false, live: first, unsupported: true };
  }
  const again = await evalOn(page, _measure).catch(() => EVAL_TIMED_OUT);
  return _unmeasured(again) || _matches(again) ? { ok: true } : { ok: false, live: again };
}

module.exports = { RUN_VIEWPORT, MIN_H, MAX_H, runViewport, capture, checkGeometry };
