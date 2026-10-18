"use strict";
/**
 * browser_control.js — the loopback HTTP server the runtime calls to ask Build Studio for a browser
 * view (the CDP-connecting side of the seam is runtime/app/host_browser.js). Same shape as Conxa
 * Execute's: bound to 127.0.0.1 only, a random per-process token so nothing else on the machine can
 * drive it blind, JSON in and out.
 *
 *   new_view      { runId, label? }          -> { markerUrl, tabId }   first tab of a test run
 *   new_tab       { runId, label?, focus? }  -> { markerUrl, tabId }   a tab_open step's later tab
 *   close_view    { runId, tabId }           -> { ok: true }           destroy ONE view
 *   run_end       { runId }                  -> { ok: true }           the run finished — the view stays
 *   save_download { runId, url, dest, timeoutMs? } -> { ok: true }
 */
const http = require("http");
const crypto = require("crypto");
const panel = require("./browser_panel");

let _server = null;
let _token = null;

function start() {
  if (_server) return Promise.resolve({ url: `http://127.0.0.1:${_server.address().port}/control?token=${_token}` });
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
        const result = await dispatch(parsed);
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

async function dispatch({ op, runId, tabId, label, focus, url, dest, timeoutMs }) {
  if (!runId) throw new Error("missing runId");
  switch (op) {
    case "new_view": return panel.newView(runId, { label });
    case "new_tab": return panel.newTab(runId, { label, focus });
    case "close_view":
      if (!tabId) throw new Error("missing tabId");
      await panel.closeTab(runId, tabId);
      return { ok: true };
    case "run_end": await panel.runEnd(runId); return { ok: true };
    case "save_download":
      if (!url || !dest) throw new Error("missing url or dest");
      return panel.saveDownload(runId, url, dest, timeoutMs);
    default: throw new Error(`unknown op: ${op}`);
  }
}

function stop() {
  if (_server) { try { _server.close(); } catch (_) { /* best effort */ } _server = null; }
}

module.exports = { start, stop, dispatch };
