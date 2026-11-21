"use strict";
/**
 * run_registry.js — the multi-run replacement for server.js's old single
 * `activeExecution` slot (RT-3). Runs are admitted up to a cap; each run is
 * tracked independently by its own runId so cancel/status can address one
 * run without touching its siblings.
 *
 * Extracted as its own module (same reason as recovery_park.js/failure_response.js):
 * server.js connects MCP stdio at import time, so nothing in it is unit-testable in
 * place.
 */

const { outcomeFromText } = require("./proof");

const MAX_CONCURRENT_RUNS =Number(process.env.CONXA_MAX_CONCURRENT_RUNS) || 5;

const _runs = new Map(); // runId -> exec

// Admits `exec` (must carry a unique `.runId`) if under the cap. Returns false — and admits
// nothing — when the cap is already reached, so the caller can refuse honestly instead of
// silently overwriting a sibling run.
function begin(exec) {
  if (_runs.size >= MAX_CONCURRENT_RUNS) return false;
  _runs.set(exec.runId, exec);
  return true;
}

function end(runId) {
  _runs.delete(runId);
}

function get(runId) {
  return _runs.get(runId) || null;
}

function count() {
  return _runs.size;
}

// Snapshot for get_execution_status and for the cap/cancel-ambiguity messages — plain data,
// not the live exec objects (those carry Playwright handles callers must never touch directly).
function list() {
  const now = Date.now();
  return [..._runs.values()].map((exec) => ({
    run_id: exec.runId,
    skill: exec.slug,
    workspace_id: exec.workspace_id,
    step: exec.step,
    total: exec.total,
    started_at: exec.startedAt,
    elapsed_ms: now - Date.parse(exec.startedAt),
    cancel_requested: !!exec.cancelRequested,
    // Non-empty while this run is blocked behind host_lock.js waiting for another run to finish
    // with the same external platform (RT-3 follow-up) — lets an agent see WHY a run is stalled
    // instead of just watching `step` stay at 0.
    waiting_for_host: (exec.waitingForHost && exec.waitingForHost.length) ? exec.waitingForHost : null,
  }));
}

// Flips the named run's cancel flag. Returns false if no such run is active — the caller (a
// stale run_id, or a run that already finished) gets a clear "no such run" rather than a
// silent no-op.
function requestCancel(runId) {
  const exec = _runs.get(runId);
  if (exec) { exec.cancelRequested = true; return true; }
  // A run still waiting for a human to sign in has no exec yet — flag the wait so it never starts.
  const waiting = _awaiting.get(runId);
  if (waiting) { waiting.cancelled = true; return true; }
  return false;
}

// ─── Runs detached from their execute_skill call ─────────────────────────────
// When sign-in is missing, execute_skill returns at once and the run waits in the background for a
// human to finish (server.js: _detachUntilSignedIn). The caller can no longer learn anything from a
// return value, so the registry answers instead: is it still waiting and on what (awaiting_auth), how
// did it end (a small ring of recent results), and cancel. An awaiting run holds no run slot — it
// executes nothing yet — so it never counts against the concurrent-run cap; the browser session it
// holds is what the session cap bounds.
const RECENT_MAX = 10;
const SUMMARY_MAX = 1000;
const _awaiting = new Map(); // runId -> { run_id, skill, workspace_id, apps, statusOf, since, cancelled }
let _recent = [];            // oldest first

// rec: { run_id, skill, workspace_id, apps: [{ id, name, key }], statusOf(key) -> "waiting" | ...,
//        fingerprint? }
function beginAwaitingAuth(rec) {
  _awaiting.set(rec.run_id, { ...rec, since: Date.now(), cancelled: false });
}
function endAwaitingAuth(runId) {
  _awaiting.delete(runId);
}
function isAuthCancelled(runId) {
  const w = _awaiting.get(runId);
  return !!(w && w.cancelled);
}
function _awaitingSnapshot(w) {
  return {
    run_id: w.run_id,
    skill: w.skill,
    workspace_id: w.workspace_id,
    apps: w.apps.map((a) => ({ app: a.name, status: w.statusOf(a.key) })),
    since: new Date(w.since).toISOString(),
    elapsed_ms: Date.now() - w.since,
  };
}
// A run whose wait entry lingers while it executes (server.js keeps it until the resumed call
// returns) is reported as running, not awaiting.
function listAwaitingAuth() {
  return [..._awaiting.values()].filter((w) => !_runs.has(w.run_id)).map(_awaitingSnapshot);
}
// The run already waiting on an identical request (same tool, skill and inputs), so a second,
// impatient execute_skill joins it instead of queueing a duplicate that would fire twice on sign-in.
function findAwaiting(fingerprint) {
  if (!fingerprint) return null;
  for (const w of _awaiting.values()) if (w.fingerprint === fingerprint) return _awaitingSnapshot(w);
  return null;
}

// status: "completed" | "failed" | "cancelled". Trimmed — this is a status line, not a transcript.
function recordResult(runId, { skill, status, summary }) {
  _recent = _recent.filter((r) => r.run_id !== runId);
  _recent.push({
    run_id: runId, skill, status,
    summary: String(summary || "").slice(0, SUMMARY_MAX),
    ended_at: new Date().toISOString(),
  });
  if (_recent.length > RECENT_MAX) _recent = _recent.slice(-RECENT_MAX);
}

const _textOf = (res) => ((res && res.content) || []).filter((c) => c && c.type === "text").map((c) => c.text).join("\n");
// A run that stopped to wait for an answer (span, popup, hand-over, a step still working, a test
// guard) ended its call without failing.
const PAUSE_META = ["conxa/agent_span", "conxa/fork", "conxa/handover", "conxa/proof_wait", "conxa/guard", "conxa/stopped_at", "conxa/handed_off"];
// Success is the "Done." prefix (Build Studio's Run Test reads it the same way) — err() is just text().
function resultStatus(res) {
  const t = _textOf(res);
  const oc = outcomeFromText(t);
  if (oc === "done" || oc === "done_please_check" || oc === "submitted") return "completed";
  if (res && res._meta && PAUSE_META.some((k) => res._meta[k])) return "paused";
  return /^Execution cancelled/.test(t) ? "cancelled" : "failed";
}

// How an execute_skill/execute_sequence call ended (server.js _handleTool, every run path). Recorded
// only for a run the caller can name: its answer gave this run_id, or it continues one in the
// background (`continuation`). A refusal before the run started never shows an id. A hand-off to a
// sign-in wait is recorded later, by whatever finishes that wait. Returns whether it recorded.
function recordCallResult(runId, skill, res, { continuation = false } = {}) {
  const text = _textOf(res);
  if (!continuation && !text.includes(runId)) return false;
  if (res && res._meta && res._meta["conxa/awaiting_auth"]) return false;
  recordResult(runId, { skill, status: resultStatus(res), summary: text });
  return true;
}

function listRecent() {
  return _recent.map((r) => ({ ...r }));
}

// One run, wherever it is in its life: running, awaiting_auth, or ended. null if unknown/expired.
function status(runId) {
  const exec = _runs.get(runId);
  if (exec) return { state: "running", ...list().find((r) => r.run_id === runId) };
  const w = _awaiting.get(runId);
  if (w) return { state: "awaiting_auth", ..._awaitingSnapshot(w) };
  const done = _recent.find((r) => r.run_id === runId);
  return done ? { state: done.status, ...done } : null;
}

// Test hook: forget awaiting runs and recorded results.
function _resetAuth() {
  _awaiting.clear();
  _recent = [];
}

module.exports = {
  MAX_CONCURRENT_RUNS, begin, end, get, list, count, requestCancel,
  beginAwaitingAuth, endAwaitingAuth, isAuthCancelled, listAwaitingAuth, findAwaiting, recordResult, listRecent, status, _resetAuth,
  resultStatus, recordCallResult,
};
