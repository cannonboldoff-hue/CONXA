"use strict";
/**
 * handover.js — EXEC-21 (hand-over shape): yields the live page to a person for the part only
 * they can do (2FA, CAPTCHA, e-signature, a one-off login), then reclaims it and resumes the
 * run from the next step. Reuses EXEC-13's shipped park-and-resume primitive (agent_span.js /
 * recovery_park.js — same `_parks` Map, same divergence-fingerprint idea) with a different
 * answerer and three changes forced by the human timescale, all handled by the caller
 * (server.js), not here: the wait is unbounded (a person, not a model, so no fixed TTL like
 * the agent span's PARK_TTL_MS); the host lock is released for the pause window rather than held, so
 * a long hand-over does not starve every sibling run touching the same platform; and there is no
 * structured answer to validate — "done" is the whole signal.
 *
 * Not a recovery mechanism — see agent_span.js's header for why an agent span deliberately stays
 * outside the Tier 1-4 cascade (no selector, no identity_bundle, never touches recovery_stage.js
 * or retry_budget.js's state). The same reasoning applies here.
 *
 * Three independent resume signals feed one race (arm() below): an in-page banner (primary —
 * works with zero extra infrastructure because Playwright's connection to the page is already
 * bidirectional via context.exposeBinding), a file drop under ~/.conxa/resume/ (mirrors
 * scheduler_daemon.js's existing command-file pattern, for a CLI or a script to trigger resume),
 * and a token-gated loopback HTTP listener (for a local tool that wants to POST a resume). The
 * HTTP listener is the runtime's first inbound network surface ever — see server.js's header on
 * why that's deliberately scoped to exist ONLY while a hand-over is pending, closed on disarm.
 */
const fs = require("fs");
const path = require("path");
const http = require("http");
const crypto = require("crypto");
const os = require("os");
const { probePresent } = require("./handlers");
const { resolveStepPage } = require("./tabs");
const { evalOn } = require("./page_eval");
const { capture } = require("./page_geometry");
const hostBrowser = require("./host_browser");

const CONXA_DIR = process.env.CONXA_DATA_DIR || path.join(os.homedir(), ".conxa");
const RESUME_DIR = path.join(CONXA_DIR, "resume");

// How long executeOneStep holds the MCP call open waiting for the person before giving up and
// letting server.js park the page + return a pause response instead. Kept comfortably under
// EXECUTION_DEADLINE_MS (210s) so there's always time to park cleanly rather than racing the
// client's own ~240s abandon timeout. Most hand-overs (a quick 2FA code) finish well inside this
// and never park at all — see run.js's "fast path" comment.
const HANDOVER_INCALL_MS = Number(process.env.CONXA_HANDOVER_INCALL_MS) || 120000;

// Lifetime of a PARKED hand-over — a person-scale wait, not a model-scale one. Compare EXEC-13's
// PARK_TTL_MS = 180s, which is the right order of magnitude for Claude to read a question and
// answer it, and the wrong order of magnitude for a person to notice a browser tab, do
// something in it, and come back.
const HANDOVER_PARK_TTL_MS = Number(process.env.CONXA_HANDOVER_PARK_TTL_MS) || 30 * 60 * 1000;

// Studio's test never parks a hand-over (it waits for the person inside the call instead), and a
// run that can't be parked stops in plain words when the wait runs out. Read per call so tests can set it.
const noPark = () => process.env.CONXA_HANDOVER_NO_PARK === "1";

const BANNER_ID = "__conxa_handover_banner__";

// Runs inside the page. Idempotent (a reconnect after navigation re-adds it if a fresh document
// dropped it; calling it twice on the same document is a no-op via the id check) and self-
// contained — no dependency on anything else being loaded into the page.
function _bannerScript(message, bindingName) {
  const safeMsg = JSON.stringify(String(message || ""));
  const binding = JSON.stringify(String(bindingName || ""));
  // A fresh document has no <html> yet when an init script runs: wait for it instead of giving up
  // (the banner used to vanish after any navigation). The ping asks the runtime whether this is
  // one of the run's own pages and the hand-over is still open; a stale script draws nothing.
  return `(() => {
    const add = async () => {
      if (document.getElementById(${JSON.stringify(BANNER_ID)})) return;
      if (!document.documentElement) { document.addEventListener("DOMContentLoaded", add, { once: true }); return; }
      let ok = false;
      try { ok = (await window[${binding}]("ping")) === true; } catch (_) { ok = false; }
      if (!ok || document.getElementById(${JSON.stringify(BANNER_ID)})) return;
      const el = document.createElement("div");
      el.id = ${JSON.stringify(BANNER_ID)};
      el.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#1a1a2e;color:#fff;padding:12px 16px;font:14px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;display:flex;align-items:center;justify-content:space-between;gap:12px;box-shadow:0 2px 8px rgba(0,0,0,.35)";
      const msg = document.createElement("span");
      msg.textContent = ${safeMsg};
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Done — resume workflow";
      btn.style.cssText = "background:#4f8cff;color:#fff;border:0;border-radius:6px;padding:8px 14px;font:inherit;font-weight:600;cursor:pointer;white-space:nowrap;flex:0 0 auto";
      btn.addEventListener("click", () => {
        btn.disabled = true;
        btn.textContent = "Resuming…";
        try { window[${binding}]("done"); } catch (_) {}
      });
      el.appendChild(msg);
      el.appendChild(btn);
      document.documentElement.appendChild(el);
    };
    return add();
  })();`;
}

function _removeBannerScript() {
  return `(() => { const el = document.getElementById(${JSON.stringify(BANNER_ID)}); if (el) el.remove(); })();`;
}

// Arms every signal source for one hand-over pause and returns { signal, disarm, ... } where
// `signal` resolves to the source name ("banner"|"file"|"http") the moment ANY of them fires.
// Callers race `signal` against their own in-call timeout (run.js) or just await it after a
// resume (server.js re-arms on the parked page). `disarm()` is idempotent and MUST be called on
// every exit path (signalled, timed out into a park, or the run torn down for an unrelated
// reason) or the loopback listener and file watcher leak.
async function arm(context, page, step, runId, host) {
  let resolveSignal;
  let settled = false;
  const signal = new Promise((resolve) => { resolveSignal = resolve; });
  const fire = (via) => {
    if (settled) return;
    settled = true;
    resolveSignal(via);
  };

  const message = (step && step.message) || "Please complete this step, then click Done.";

  // A host panel keeps its input shield over a run's page; the person has to be able to click Done, so lift it
  // for the pause and put it back in disarm(). `host` is { hostOwned, hostRunId } from the run.
  const hostInput = (open) => (host && host.hostOwned ? hostBrowser.setInputOpen({ runId: host.hostRunId, open }) : null);
  await hostInput(true);

  // 1) In-page banner. exposeBinding installs a function INSIDE the page's JS world that calls
  // back into this Node process — Playwright's page/context connection is bidirectional, so this
  // needs no port and works even after the MCP call that armed it has already returned.
  // addInitScript re-injects the banner into every future document (a navigation, a fresh tab)
  // in this context; the immediate evaluate() below covers documents that already exist.
  // The binding name is unique per arm (Playwright refuses a name already exposed on a context, which
  // used to leave a second hand-over wired to the first one's finished signal). The context is shared
  // with other pages (Studio's window, another run's tabs), so the binding itself decides where the
  // banner shows: only on this run's pages, and only while this hand-over is open.
  const bindingName = `__conxaHandoverDone_${crypto.randomBytes(8).toString("hex")}`;
  const ownPages = () => [page, ...((host && host.tabs && host.tabs.pages) ? host.tabs.pages.values() : [])];
  const isRunPage = async (p) => {
    if (!p) return false;
    const own = ownPages();
    if (own.includes(p)) return true;
    const opener = typeof p.opener === "function" ? await p.opener().catch(() => null) : null;
    return !!opener && own.includes(opener);
  };
  await context.exposeBinding(bindingName, async (source, op) => {
    const mine = await isRunPage(source && source.page);
    if (op === "ping") return !settled && mine;
    if (mine) fire("banner");
    return undefined;
  }).catch(() => {});
  await context.addInitScript(_bannerScript(message, bindingName)).catch(() => {});
  const livePages = () => { try { return context.pages(); } catch (_) { return [page]; } };
  await Promise.all(livePages().map((pg) => evalOn(pg, _bannerScript(message, bindingName)).catch(() => {})));
  const onNewPage = (pg) => { evalOn(pg, _bannerScript(message, bindingName)).catch(() => {}); };
  context.on("page", onNewPage);

  // 2) File-drop resume: ~/.conxa/resume/<run_id>.cmd. fs.watch for near-instant pickup, plus a
  // periodic fallback tick — the same belt-and-suspenders pattern scheduler_daemon.js already
  // uses for its own command drain (fs.watch can miss events on some network/Windows filesystem
  // combinations).
  try { fs.mkdirSync(RESUME_DIR, { recursive: true }); } catch (_) {}
  const cmdFile = path.join(RESUME_DIR, `${runId}.cmd`);
  try { fs.unlinkSync(cmdFile); } catch (_) {} // clear any stale file from a previous pause
  const checkFile = () => {
    if (settled) return;
    let exists = false;
    try { exists = fs.existsSync(cmdFile); } catch (_) { return; }
    if (exists) {
      try { fs.unlinkSync(cmdFile); } catch (_) {}
      fire("file");
    }
  };
  let watcher = null;
  try { watcher = fs.watch(RESUME_DIR, () => checkFile()); } catch (_) { watcher = null; }
  const fileTick = setInterval(checkFile, 5000);
  if (fileTick.unref) fileTick.unref();

  // 3) Loopback HTTP resume — bound to 127.0.0.1 only, armed ONLY while this hand-over is
  // pending, closed in disarm(). Random per-pause token so nothing else on the machine can
  // trigger it blind.
  const token = crypto.randomBytes(16).toString("hex");
  const httpServer = http.createServer((req, res) => {
    let u;
    try { u = new URL(req.url, "http://127.0.0.1"); } catch (_) { res.writeHead(400); res.end(); return; }
    if (req.method === "POST" && u.pathname === `/resume/${runId}` && u.searchParams.get("token") === token) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
      fire("http");
    } else {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, error: "not found" }));
    }
  });
  let httpPort = null;
  try {
    await new Promise((resolve, reject) => {
      httpServer.once("error", reject);
      httpServer.listen(0, "127.0.0.1", resolve);
    });
    httpPort = httpServer.address().port;
  } catch (_) { httpPort = null; }

  const disarm = async () => {
    context.off("page", onNewPage);
    // Playwright has no removeBinding — the exposed function outlives disarm(), but its ping now
    // answers false (and `fire` is a no-op), so a stale init script draws nothing.
    settled = true;
    clearInterval(fileTick);
    if (watcher) { try { watcher.close(); } catch (_) {} }
    try { fs.unlinkSync(cmdFile); } catch (_) {}
    await Promise.all(livePages().map((pg) => evalOn(pg, _removeBannerScript()).catch(() => {})));
    await new Promise((resolve) => { try { httpServer.close(() => resolve()); } catch (_) { resolve(); } });
    await hostInput(false);
  };

  return { signal, disarm, message, httpPort, token, cmdFile, runId };
}

// After the signal fires (or after a resumed call re-adopts a parked hand-over): confirm the
// recorded tab is still resolvable and not closed — Key Invariant, never fall back to "whatever
// page is current" — and if the step declared `resume_when`, require that condition true before
// handing control back to the run. A failed revalidation is a clean step failure, never a blind
// continue onto a page the person left in an unexpected state.
async function revalidate(tabsRegistry, step, watch) {
  let page;
  try {
    page = await resolveStepPage(tabsRegistry, step, { watch });
  } catch (e) {
    throw Object.assign(
      new Error("The page the person was working on was closed during the hand-over, so the run stopped. Nothing after this step ran."),
      { handoverRevalidationFailed: true, handoverFailed: true, cause: e }
    );
  }
  if (page.isClosed()) {
    throw Object.assign(
      new Error("The page the person was working on was closed during the hand-over, so the run stopped. Nothing after this step ran."),
      { handoverRevalidationFailed: true, handoverFailed: true }
    );
  }
  if (step && step.resume_when) {
    const timeout = Number(step.resume_when_timeout_ms) || 10000;
    const ok = await probePresent(page, step.resume_when, {}, timeout).catch(() => false);
    if (!ok) {
      throw Object.assign(
        new Error("After Done, the page still didn't show what this step waits for, so the run stopped. Nothing after this step ran."),
        { handoverRevalidationFailed: true, handoverFailed: true }
      );
    }
  }
  return page;
}

// Builds the MCP response for a parked hand-over pause. Mirrors review_pause.js's
// buildReviewRequest shape (text + current screenshot + _meta for non-agent callers) but
// describes a HAND-OVER, not a question — there is nothing for the agent to answer, just
// something to relay to the person at the keyboard.
async function buildHandoverRequest(page, step, stepIndex, opts = {}) {
  const message = (step && step.message) || "Please complete this step, then click Done.";
  const url = (() => { try { return page.url(); } catch (_) { return ""; } })();
  const stepNo = stepIndex + 1;

  const header =
    `Execution paused at step ${stepNo} for a hand-over — this needs a person at the keyboard, ` +
    `not the agent.\nPage URL: ${url}\n\n${message}\n\n` +
    `A banner is showing in the browser window with a "Done — resume workflow" button. Tell the ` +
    `person to complete the step there and click it — the run resumes automatically the moment ` +
    `they do; no further action is needed from you here.` +
    (opts.runId ? ` To see how it ends, call get_execution_status with run_id: ${opts.runId}.` : "");

  const content = [{ type: "text", text: header }];
  const shot = await capture(page, { type: "jpeg", quality: 80 });
  if (shot) {
    content.push(
      { type: "text", text: "Current page:" },
      { type: "image", data: shot.toString("base64"), mimeType: "image/jpeg" }
    );
  }

  return {
    content,
    _meta: {
      "conxa/handover": {
        step_index: stepIndex,
        message,
        run_id: opts.runId || null,
        resume_http: opts.httpPort ? { port: opts.httpPort, token: opts.token, path: `/resume/${opts.runId}` } : null,
        resume_file: opts.cmdFile || null,
      },
    },
  };
}

module.exports = {
  HANDOVER_INCALL_MS,
  HANDOVER_PARK_TTL_MS,
  noPark,
  _bannerScript,
  arm,
  revalidate,
  buildHandoverRequest,
};
