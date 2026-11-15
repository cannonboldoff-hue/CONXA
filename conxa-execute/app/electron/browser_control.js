"use strict";
/**
 * browser_control.js — the loopback HTTP server the runtime calls into to ask Execute
 * for a browser view (see runtime/app/host_browser.js, the CDP-connecting side of this
 * seam). Same shape as the runtime's own loopback listener in runtime/app/handover.js:
 * bound to 127.0.0.1 only, random per-process token so nothing else on the machine can
 * drive it blind, JSON in and out.
 *
 * Four ops, each mapping straight onto a browser_panel.js function:
 *   new_view   { runId, label?, focus?, loginKey?, viewport? } -> { markerUrl, tabId }   first tab of a run
 *                       (viewport { width, height } fixes the page size; the panel shrinks it to fit)
 *                       (focus: a login — bring this run to the foreground even if another is selected)
 *   new_tab    { runId, label?, loginKey? }  -> { markerUrl, tabId }   a tab_open step's later tab,
 *                       or a sign-in tab of a session that already has a panel open
 *   close_view { runId, tabId }   -> { ok: true }           destroy ONE view (a finished login)
 *   run_end    { runId }          -> { ok: true }           destroy the run's views + partition
 *   save_download { runId, url, dest, timeoutMs? } -> { ok: true }  copy the run's finished download
 *                       of `url` to `dest` (Execute saves run downloads itself — browser_panel.js)
 * `label` names the tab in the panel's strip (a login tab carries its app name). `loginKey`
 * (AUTH-6) marks a tab as an actual sign-in tab — never set on a judge/prover probe tab — so the
 * renderer can show an "I'm done signing in" action on it; the raw key never reaches the renderer,
 * only a boolean (see browser_panel.js's _describeTab).
 */
const http = require("http");
const crypto = require("crypto");
const panel = require("./browser_panel");

let _server = null;
let _token = null;

function start() {
  if (_server) return { url: `http://127.0.0.1:${_server.address().port}/control?token=${_token}` };
  _token = crypto.randomBytes(16).toString("hex");
  _server = http.createServer((req, res) => {
    if (req.method !== "POST") { res.writeHead(404); res.end(); return; }
    let u;
    try { u = new URL(req.url, "http://127.0.0.1"); } catch (_) { res.writeHead(400); res.end(); return; }
    if (u.pathname !== "/control" || u.searchParams.get("token") !== _token) {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, error: "not found" }));
      return;
    }
    let body = "";
    req.on("data", (c) => { body += c; if (body.length > 65536) req.destroy(); });
    req.on("end", async () => {
      let parsed;
      try { parsed = JSON.parse(body); } catch (_) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: "bad json" }));
        return;
      }
      try {
        const result = await _dispatch(parsed);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(result));
      } catch (e) {
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
  });
  _server.listen(0, "127.0.0.1");
  return new Promise((resolve, reject) => {
    _server.once("listening", () => resolve({ url: `http://127.0.0.1:${_server.address().port}/control?token=${_token}` }));
    _server.once("error", reject);
  });
}

async function _dispatch({ op, runId, tabId, label, focus, loginKey, viewport, url, dest, timeoutMs, open, type, quality }) {
  if (!runId) throw new Error("missing runId");
  switch (op) {
    case "new_view":   return panel.newView(runId, { label, focus, loginKey, viewport });
    case "new_tab":    return panel.newTab(runId, { label, focus, loginKey });
    case "close_view":
      if (!tabId) throw new Error("missing tabId");
      await panel.closeTab(runId, tabId);
      return { ok: true };
    case "run_end":    await panel.runEnd(runId); return { ok: true };
    case "ensure_geometry": return panel.ensureGeometry(runId);
    case "input":      return panel.setInputOpen(runId, open);
    case "capture":    return panel.capture(runId, tabId, { type, quality });
    case "save_download":
      if (!url || !dest) throw new Error("missing url or dest");
      return panel.saveDownload(runId, url, dest, timeoutMs);
    default: throw new Error(`unknown op: ${op}`);
  }
}

function stop() {
  if (_server) { try { _server.close(); } catch (_) {} _server = null; }
}

module.exports = { start, stop };
