"use strict";

const https  = require("https");
const http   = require("http");
const url    = require("url");
const fs     = require("fs");
const path   = require("path");
const crypto = require("crypto");
const { canonicalJSON } = require("./canonical_json");
const { noteAccess } = require("./access_refused");

const MAX_QUEUE   = 50;
const FLUSH_EVERY = 10;   // events before auto-flush
const FLUSH_MS    = 2000; // timer interval

// Evidence-chain spill: a batch that fails to POST is appended here instead of being lost
// outright (PROD-18). One JSONL line per undelivered batch, capped and drained on the next
// run's startup. Deliberately the OPPOSITE eviction policy of the in-memory queue's
// drop-oldest ring: once a batch is on disk, the oldest evidence is the most valuable, so
// a full spill file drops the NEWEST line rather than truncating history.
const SPILL_MAX_LINES = 200;

function _spillPath() {
  const dataDir = process.env.CONXA_DATA_DIR || process.env.CONXA_DIR || "";
  return dataDir ? path.join(dataDir, "logs", "telemetry-spill.jsonl") : "";
}

// A spilled record carries its own delivery destination (`_dest: {url, token, rv}`) rather
// than relying on whichever tracker instance later calls drainSpill to happen to own the
// right workspace's credentials — createTracker is instantiated fresh per execute_skill
// call (server.js:910), so the tracker draining the file is very often not the one that
// wrote a given line.
function _spillAppend(envelope, dest) {
  const p = _spillPath();
  if (!p) return;
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    let lineCount = 0;
    try { lineCount = fs.readFileSync(p, "utf8").split("\n").filter(Boolean).length; } catch (_) {}
    if (lineCount >= SPILL_MAX_LINES) return; // drop newest — earliest evidence wins
    fs.appendFileSync(p, `${JSON.stringify({ _dest: dest, ...envelope })}\n`);
  } catch (_) {}
}

// Raw HTTP POST, independent of any createTracker closure — used both by _post (via the
// closure, for its logging) and directly by drainSpill (which has no bound workspace).
// `status` is the real HTTP status when a response came back, or 0 for a network/timeout
// failure that never got one — callers use this to tell "will never succeed" (4xx: bad token,
// wrong route) apart from "might succeed later" (network blip, 5xx).
function _rawPost(destUrl, token, runtimeVersion, envelope, timeoutMs) {
  return new Promise((resolve) => {
    let trackingUrl;
    try { trackingUrl = new url.URL(destUrl); } catch (_) { return resolve({ ok: false, status: 0 }); }
    const payload = JSON.stringify(envelope);
    const lib = trackingUrl.protocol === "https:" ? https : http;
    try {
      const req = lib.request({
        hostname: trackingUrl.hostname,
        port: trackingUrl.port || (trackingUrl.protocol === "https:" ? 443 : 80),
        path: trackingUrl.pathname + (trackingUrl.search || ""),
        method: "POST",
        headers: {
          "Content-Type":      "application/json",
          "Content-Length":    Buffer.byteLength(payload),
          "X-Tracking-Token":  token || "",
          "X-Runtime-Version": runtimeVersion || "",
        },
      }, (res) => {
        const ok = res.statusCode >= 200 && res.statusCode < 300;
        res.resume();
        resolve({ ok, status: res.statusCode });
      });
      req.on("error", () => resolve({ ok: false, status: 0 }));
      req.setTimeout(timeoutMs || 5000, () => { req.destroy(); resolve({ ok: false, status: 0 }); });
      req.write(payload);
      req.end();
    } catch (_) {
      resolve({ ok: false, status: 0 });
    }
  });
}

// A 4xx means the request itself is invalid (bad/rotated token, wrong route, malformed body) —
// retrying it, whether live or from the spill file, gets the same 4xx forever. Only a network
// failure (status 0) or a 5xx (server-side, may recover) is worth holding onto.
function _isPermanentRejection(status) {
  return status >= 400 && status < 500;
}

// Drain every spilled batch, best-effort, using each line's own recorded destination.
// Called once at runtime startup (server.js) before any run's own traffic.
async function drainSpill(log) {
  const p = _spillPath();
  let lines;
  try { lines = fs.readFileSync(p, "utf8").split("\n").filter(Boolean); } catch (_) { return; }
  if (lines.length === 0) return;
  const remaining = [];
  let dropped = 0;
  for (const line of lines) {
    let record;
    try { record = JSON.parse(line); } catch (_) { continue; } // drop unparsable lines
    const { _dest, ...envelope } = record;
    const dest = _dest || {};
    const result = await _rawPost(dest.url, dest.token, dest.rv, envelope, 5000);
    if (!result.ok) {
      // Without this, one permanently-rejected batch (a rotated tracking token, a route that no
      // longer exists) never leaves the file: it's retried and re-fails on every drain forever,
      // and once SPILL_MAX_LINES fills up with dead lines, all NEWER real evidence is dropped
      // instead (see _spillAppend's "drop newest" comment above).
      if (_isPermanentRejection(result.status)) dropped++;
      else remaining.push(line);
    }
  }
  try {
    if (remaining.length) fs.writeFileSync(p, `${remaining.join("\n")}\n`);
    else fs.unlinkSync(p);
  } catch (_) {}
  if (log) {
    try { log("info", "telemetry_spill_drained", { attempted: lines.length, remaining: remaining.length, dropped }); } catch (_) {}
  }
}

/**
 * Map a runtime error to a compact failure reason code.
 * Exported so server.js can reuse it for wf_fail.
 */
function mapErrorToCode(err) {
  const msg = (err && err.message) ? err.message : String(err || "");
  if (/url .* does not match/i.test(msg))    return "url_mismatch";
  if (/timeout/i.test(msg))                  return "timeout";
  if (/net::|ERR_|navigation/i.test(msg))    return "navigation_failed";
  if (/cancel/i.test(msg))                   return "cancelled";
  if (/locator not found|missing selector|element not found/i.test(msg)) return "selector_missing";
  // Anything unrecognized — permissions, a crash, an auth failure, a plain bug — used to be
  // silently reported as "selector_missing", which is misleading on the fleet dashboard's drift
  // signal (a real selector problem looks the same as one that never was).
  return "unknown";
}

/**
 * createTracker(trackingConfig, runtimeContext) → tracker
 *
 * trackingConfig: { enabled, tracking_url, tracking_token,
 *                   workspace_id, schema_version, protocol_version }
 * runtimeContext: { runtime_version, workflow_id, workflow_version, workspace_id }
 */
function createTracker(trackingConfig, runtimeContext) {
  const cfg = trackingConfig  || {};
  const ctx = runtimeContext  || {};
  const log = typeof ctx.log === "function" ? ctx.log : null;

  function _warn(msg, extra) {
    if (!log) return;
    try { log("warn", msg, extra || {}); } catch (_) {}
  }

  function _info(msg, extra) {
    if (!log) return;
    try { log("info", msg, extra || {}); } catch (_) {}
  }

  // Disabled: return a no-op tracker so callers never branch
  if (!cfg.enabled) {
    _info("tracking_disabled", {
      workspace_id: cfg.workspace_id || ctx.workspace_id || "",
      workflow_id: ctx.workflow_id || "",
    });
    const noop = () => {};
    return {
      forRun:  () => ({ emit: noop }),
      flush:   () => Promise.resolve({ ok: false }),
      destroy: noop,
    };
  }

  // Enabled but nothing to send to: every flush this tracker would ever do is doomed before it
  // starts, and used to still spill each failed batch with `url: undefined` — silently filling
  // the spill file with lines that can never be delivered. Log it loudly (this is a config bug,
  // not routine offline telemetry) and return the same no-op tracker `!cfg.enabled` does.
  if (!cfg.tracking_url) {
    if (log) { try { log("error", "tracking_url_missing", {
      workspace_id: cfg.workspace_id || ctx.workspace_id || "",
      workflow_id: ctx.workflow_id || "",
    }); } catch (_) {} }
    const noop = () => {};
    return {
      forRun:  () => ({ emit: noop }),
      flush:   () => Promise.resolve({ ok: false }),
      destroy: noop,
    };
  }
  if (!cfg.tracking_token) {
    _warn("tracking_token_missing", {
      workspace_id: cfg.workspace_id || ctx.workspace_id || "",
      workflow_id: ctx.workflow_id || "",
    });
  }

  let queue    = [];
  let _flushing = false;
  let _timer   = setInterval(_tick, FLUSH_MS);
  if (_timer.unref) _timer.unref(); // don't prevent process exit

  // Active run context (set by forRun)
  let _runCtx = { rid: "", uid: "", wid: "" };
  // Evidence chain state (PROD-18) — reset per run in forRun(). `seq` counts batches as
  // they're CONSTRUCTED (not delivered), so a batch lost to a failed POST that never
  // reaches spill still leaves a detectable hole rather than silently renumbering.
  let _chainSeq  = 0;
  let _chainPrev = "";

  function _tick() {
    if (!_flushing && queue.length > 0) _flushNow();
  }

  // events: [{e, ts, ...}]. Builds the chain-linked envelope once so a failed POST can
  // spill the exact bytes that were (or would have been) sent.
  function _buildEnvelope(events) {
    const seq  = _chainSeq++;
    const prev = _chainPrev;
    const linkable = { e: events, p: prev, r: _runCtx.rid, s: seq };
    const h = crypto
      .createHmac("sha256", cfg.tracking_token || "")
      .update(canonicalJSON(linkable))
      .digest("hex");
    _chainPrev = h;
    return {
      v:   cfg.protocol_version || 1,
      sv:  cfg.schema_version   || 1,
      cid:  cfg.workspace_id       || ctx.workspace_id || "",
      wfid: ctx.workflow_id      || "",
      wfv:  ctx.workflow_version || "",
      rv:   ctx.runtime_version  || "",
      rid: _runCtx.rid,
      uid: _runCtx.uid,
      wid: _runCtx.wid,
      seq, prev, h,
      evts: events,
    };
  }

  async function _flushNow(timeoutMs) {
    if (_flushing || queue.length === 0) return { ok: true }; // nothing to send is not a failure
    _flushing = true;
    const batch = queue.splice(0, queue.length);
    const envelope = _buildEnvelope(batch);
    let result = { ok: false };
    try {
      result = await _post(envelope, timeoutMs);
    } catch (_) {
      // silent — telemetry must never surface errors
    } finally {
      _flushing = false;
    }
    noteAccess(ctx.company_id || cfg.workspace_id || ctx.workspace_id, "tracking", result.status);
    // A 4xx here would never succeed on a later drain either — same reasoning as drainSpill's
    // own drop-on-permanent-rejection.
    if (!result.ok && !_isPermanentRejection(result.status)) {
      _spillAppend(envelope, {
        url: cfg.tracking_url, token: cfg.tracking_token, rv: ctx.runtime_version,
      });
    }
    return result;
  }

  // _post(envelope) → Promise<{ok: boolean}>. `envelope` is a pre-built, chain-linked
  // payload from _buildEnvelope. Logs around the shared `_rawPost` transport.
  async function _post(envelope, timeoutMs) {
    if (!cfg.tracking_url) {
      _warn("tracking_invalid_url", { url: cfg.tracking_url || "" });
      return { ok: false };
    }
    _info("tracking_flush_start", {
      event_count: envelope.evts.length,
      run_id: _runCtx.rid,
      url: cfg.tracking_url,
      token_present: Boolean(cfg.tracking_token),
    });
    const result = await _rawPost(cfg.tracking_url, cfg.tracking_token, ctx.runtime_version, envelope, timeoutMs);
    if (result.ok) {
      _info("tracking_http_success", { event_count: envelope.evts.length, run_id: _runCtx.rid });
    } else {
      _warn("tracking_http_failed", { event_count: envelope.evts.length, run_id: _runCtx.rid, status: result.status });
    }
    return result;
  }

  function _enqueue(event) {
    if (queue.length >= MAX_QUEUE) queue.shift(); // drop oldest
    queue.push(event);
    if (queue.length >= FLUSH_EVERY) _flushNow();
  }

  /**
   * Bind a run_id and optional user context.
   * Returns a scoped { emit } tied to this run.
   */
  function forRun(runId, userCtx) {
    _runCtx = {
      rid: runId || "",
      uid: (userCtx && userCtx.uid) || "",
      wid: (userCtx && userCtx.wid) || "",
    };
    _chainSeq  = 0;
    _chainPrev = "";
    _info("tracking_run_started", {
      run_id: _runCtx.rid,
      company: cfg.company_id || ctx.company_id || "",
      workflow_id: ctx.workflow_id || "",
      tracking_url_present: Boolean(cfg.tracking_url),
      tracking_token_present: Boolean(cfg.tracking_token),
    });
    return {
      emit(eventCode, fields) {
        const evt = Object.assign({ e: eventCode, ts: Date.now() }, fields || {});
        _enqueue(evt);
      },
    };
  }

  // flush({timeoutMs}) → Promise<{ok: boolean}>. The PROD-18 start receipt passes a short
  // timeoutMs (2s) so admission never waits as long as an ordinary background flush would.
  async function flush(opts) {
    return _flushNow(opts && opts.timeoutMs);
  }

  function destroy() {
    if (_timer) { clearInterval(_timer); _timer = null; }
  }

  return { forRun, flush, destroy };
}

module.exports = { createTracker, mapErrorToCode, drainSpill };
