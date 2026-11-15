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
const pageScripts = require("./page_scripts");

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
  // A host panel's page is only drawn while its view is on screen, and this CDP call answers only for a
  // drawn page: on a hidden tab, a covered pane or a minimized window it never does (it used to cost the
  // full 30 s on every failure report). So there it gets a short turn, then the host takes the still.
  const hosted = !!(process.env.CONXA_HOST_BROWSER_CDP || "").trim();
  const cdpTimeout = hosted ? Math.min(timeout, HOST_CAPTURE_AFTER_MS) : timeout;
  let timer;
  try {
    const cdp = await _session(page);
    const params = { format: type === "png" ? "png" : "jpeg" };
    if (params.format === "jpeg" && quality) params.quality = quality;
    const shot = await Promise.race([
      cdp.send("Page.captureScreenshot", params),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("capture timed out")), cdpTimeout); }),
    ]);
    return Buffer.from(shot.data, "base64");
  } catch (_) {
    _sessions.delete(page); // a detached session would fail every later call
    return hosted ? hostCapture(page, { type, quality }) : null;
  } finally {
    clearTimeout(timer);
  }
}

const HOST_CAPTURE_AFTER_MS = 1500;

// The host's own still of its page, or null (an older host that does not know the op, a page that is not a host tab).
async function hostCapture(page, opts) {
  try {
    const host = require("./host_browser");
    return typeof host.capture === "function" ? await host.capture(page, opts) : null;
  } catch (_) {
    return null;
  }
}

// A page that could not be measured (timed out, or answered with something else) is not a wrong-sized
// page: the check is advisory then.
const _unmeasured = (live) => live === EVAL_TIMED_OUT || !Array.isArray(live);

// The width is the contract (layout breakpoints hang on it); the height follows the host's pane, so any
// height a panel can hand out is right. The DPR is no contract: a host shrinks a narrow pane's page with
// the view's zoom, so it reads as the screen's DPR times the zoom (above or below 1). Playwright's clipped
// screenshot putting the page back at native size changes the width, which this catches (and inputLands).
// The width is off by a pixel at most from the zoom's rounding.
function _matches(live) {
  return Math.abs(live[0] - RUN_VIEWPORT.width) <= 1 && live[1] >= MIN_H;
}

const PROBE_POINT = { x: 97, y: 61 };

// The invariant that actually matters: a point the runtime aims at is the point the page receives.
// The page's own report of its size cannot show this. A host that shrinks the page the wrong way
// (an emulation `scale`) still reports 1280 x 720, yet every Playwright click lands at 1/scale of where
// it was aimed, so steps fail with "intercepts pointer events" and the real cause is invisible.
// True when it cannot be measured (no mouse, no answer): advisory, like the size check.
async function inputLands(page) {
  if (!page || !page.mouse || typeof page.mouse.move !== "function") return true;
  try {
    const seen = evalOn(page, pageScripts.firstMouseMove, undefined, 3000);
    await page.mouse.move(PROBE_POINT.x, PROBE_POINT.y);
    const at = await seen;
    if (!Array.isArray(at)) return true;
    return Math.abs(at[0] - PROBE_POINT.x) <= 2 && Math.abs(at[1] - PROBE_POINT.y) <= 2;
  } catch (_) {
    return true;
  }
}

// { ok: true } when the page is at the contract size and input lands where it is aimed (or could not be
// measured: advisory, a busy page is not a wrong-sized one). Otherwise asks the host panel to repair it
// and measures again: { ok: false, live } when the repair did not hold, { ok: false, unsupported: true }
// when the host is too old to know the repair op or not answering (the app layer updates separately
// from the host exe).
async function checkGeometry(page, { hostOwned, hostRunId } = {}) {
  // Only a host panel owns (and can repair) the page size. A browser the runtime launched itself is
  // created at its viewport by Playwright and nothing else resizes it.
  if (!hostOwned || !hostRunId) return { ok: true };
  const good = async (live) => (_unmeasured(live) || _matches(live)) && (await inputLands(page));
  const first = await evalOn(page, pageScripts.viewportSize).catch(() => EVAL_TIMED_OUT);
  if (await good(first)) return { ok: true };
  try {
    await require("./host_browser").ensureGeometry({ runId: hostRunId });
  } catch (_) {
    // An older host answers HTTP 500 "unknown op"; a wedged one times out. Either way it cannot repair.
    return { ok: false, live: first, unsupported: true };
  }
  const again = await evalOn(page, pageScripts.viewportSize).catch(() => EVAL_TIMED_OUT);
  return (await good(again)) ? { ok: true } : { ok: false, live: again };
}

module.exports = { RUN_VIEWPORT, MIN_H, MAX_H, runViewport, capture, checkGeometry };
