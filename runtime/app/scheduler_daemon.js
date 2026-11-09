"use strict";
/**
 * scheduler_daemon.js — the standalone runner (PROD-5): fires scheduled skills with
 * NO chat application open, so every chat app becomes an optional front door.
 *
 * Architecture (decided 2026-08-26, TODO.md PROD-5): this daemon is a thin MCP
 * CLIENT. It spawns the SAME conxa-runtime.exe a chat host would spawn and drives
 * it over stdio JSON-RPC exactly like Claude Desktop does — so auth pre-flight,
 * skill sync, telemetry, the concurrency cap (run_registry), and the platform
 * serialization rule (host_lock + file_lock, across processes) all apply unchanged.
 * A bug here can degrade scheduling but never breaks chat-driven execution.
 *
 * Schedules live in <CONXA_DATA_DIR>/scheduler/ (scheduler_store.js). The cloud
 * holds neither schedules nor work items — Horizon-2 doctrine, docs/PRD.md §14.5.
 *
 * Missed-run policy ("catch-up within grace"): a slot is fired if it is at most
 * catchup_grace_minutes old when the daemon sees it; older missed slots are marked
 * skipped (never stampeded). Capacity pressure DEFERS rather than skips — a run
 * refused because the engine is full is retried on later ticks until its grace
 * runs out.
 */

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { parseCron, nextAfter } = require("./cron_lite");
const { createStore } = require("./scheduler_store");
const fileLock = require("./file_lock");

const TICK_MS = 30_000;
const DAEMON_LOCK_STALE_MS = 20_000;
const ENGINE_IDLE_KILL_MS = Number(process.env.CONXA_SCHEDULER_IDLE_MS) || 10 * 60_000;

// ─── Pure decision logic (unit-tested without spawning anything) ──────────────

/**
 * Decide what this tick should do for each schedule.
 * `schedules`: store records (+ decrypted inputs not needed here).
 * `now`: Date. `ctx`: { paused, inflight:Set<id>, maxParallel }.
 *
 * Actions:
 *   fire        — due (or within catch-up grace); carries ISO `slot`
 *   deferred    — due, but the local parallel budget is spent; retried next tick
 *   skip        — oldest pending slot is past grace; carries skipped_slots count
 *   invalid_cron— record is broken; surfaced so state/logs show WHY nothing fires
 *   reschedule  — record had no next_run_at (fresh or legacy); carry computed value
 */
function computeActions(schedules, now, ctx) {
  const actions = [];
  if (!Array.isArray(schedules) || schedules.length === 0) return actions;
  if (ctx && ctx.paused) return actions;
  const inflight = ctx.inflight instanceof Set ? ctx.inflight : new Set(ctx.inflight || []);
  const maxParallel = Math.max(1, Number(ctx.maxParallel) || 5);

  const fires = [];
  for (const s of schedules) {
    if (!s.enabled) continue;
    if (inflight.has(s.id)) continue;
    let cron;
    try {
      cron = parseCron(s.cron);
    } catch (e) {
      actions.push({ action: "invalid_cron", schedule: s, reason: e.message });
      continue;
    }

    let nr = s.next_run_at ? new Date(s.next_run_at) : null;
    if (!nr || isNaN(nr.getTime())) {
      const fresh = nextAfter(cron, now);
      actions.push({ action: "reschedule", schedule: s, next_run_at: fresh ? fresh.toISOString() : null });
      continue;
    }
    if (now.getTime() < nr.getTime()) continue; // future slot — nothing to do this tick

    const graceMs = Math.max(0, Number(s.catchup_grace_minutes) || 0) * 60_000;
    if (now.getTime() - nr.getTime() <= graceMs) {
      fires.push({ action: "fire", schedule: s, slot: nr.toISOString(), overdueMs: now.getTime() - nr.getTime() });
      continue;
    }

    // Past grace: advance past every slot that is ITSELF beyond grace, and stop at
    // the first slot young enough to still be firable — that one stays pending as
    // next_run_at and fires normally. Fast-forwarding all the way to `now` would
    // wrongly discard a fresh slot that is still within catch-up range.
    let cursor = nr;
    let missed = 1;
    for (;;) {
      const nx = nextAfter(cron, cursor);
      if (!nx || now.getTime() - nx.getTime() <= graceMs) break;
      cursor = nx;
      missed += 1;
      if (missed >= 100_000) break; // defensive bound; cron granularity is minutes
    }
    const resume = nextAfter(cron, cursor);
    actions.push({
      action: "skip",
      schedule: s,
      skipped_slots: missed,
      last_missed_slot: cursor.toISOString(),
      next_run_at: resume ? resume.toISOString() : null,
    });
  }

  // Oldest-due first; beyond the parallel budget the slot stays PENDING (deferred),
  // never skipped — capacity pressure is transient, staleness is not.
  fires.sort((a, b) => String(a.slot).localeCompare(String(b.slot)));
  let free = Math.max(0, maxParallel - inflight.size);
  for (const f of fires) {
    if (free > 0) { actions.push(f); free -= 1; }
    else actions.push({ action: "deferred", schedule: f.schedule, slot: f.slot });
  }
  return actions;
}

/** The engine child command: the packaged exe IS the server; dev spawns node on server.js. */
function resolveEngineCommand() {
  if (process.pkg) return { command: process.execPath, args: [] };
  return { command: process.execPath, args: [path.join(__dirname, "server.js")] };
}

/**
 * Classify an execute_skill response body. The engine returns plain text: success
 * starts "Done." / "Done, please check." / "Submitted." (server.js success path, proof.js); the RT-3 cap refusal has a stable prefix;
 * everything else is a failure (selector failure payload, session expiry, deadline…).
 */
function classifyRunResult(text) {
  const t = String(text || "");
  const trimmed = t.trim();
  // "Done." is a clean run; "Done, please check." finished with a note (a low-risk step could not be
  // confirmed); "Submitted." was accepted and finishes on its own. "Couldn't confirm." and
  // "Partly done." are not success: the work may or may not have happened.
  if (/^(Done\.|Done, please check\.|Submitted\.)/.test(trimmed)) return "completed";
  if (t.includes("Too many workflows are already running")) return "busy";
  return "failed";
}

/**
 * A run that ended "Submitted." (Proof of Done): the app accepted the work and it was still running when the call ran
 * out of time, so the page was parked. `resume_from: N` in the text is the step to check on. Null for anything else.
 * Pure.
 */
function submittedResume(text) {
  const t = String(text || "").trim();
  if (!/^Submitted\./.test(t)) return null;
  const m = /resume_from:\s*(\d+)/.exec(t);
  return m ? { resumeFrom: Number(m[1]) } : null;
}

// How often, and for how long, a scheduled run goes back to check on a step that was still running. The park lasts
// CONXA_PROOF_PARK_TTL_MS (15 min); 28 checks 30 s apart stay inside it.
const PROOF_RECHECK_WAIT_MS = Number(process.env.CONXA_PROOF_RECHECK_WAIT_MS) >= 0 ? Number(process.env.CONXA_PROOF_RECHECK_WAIT_MS) : 30_000;
const PROOF_RECHECK_MAX = Number(process.env.CONXA_PROOF_RECHECK_MAX) >= 0 ? Number(process.env.CONXA_PROOF_RECHECK_MAX) : 28;

function _extractRunId(text) {
  const m = /\(run_id:\s*([^)]+)\)/.exec(String(text || ""));
  return m ? m[1].trim() : null;
}

// ─── Long-running half ────────────────────────────────────────────────────────

function _dataDir() {
  return process.env.CONXA_DATA_DIR || require("./host_bridge").env().dataDir;
}

function start(opts = {}) {
  const dataDir = opts.dataDir || _dataDir();
  const baseDir = path.join(dataDir, "scheduler");
  const logsDir = path.join(baseDir, "logs");
  const commandsDir = path.join(baseDir, "commands");
  const stateFile = path.join(baseDir, "state.json");
  const daemonLockFile = path.join(baseDir, "daemon.lock");
  const locksDir = path.join(dataDir, "locks");

  for (const d of [baseDir, logsDir, commandsDir]) fs.mkdirSync(d, { recursive: true });

  const log = (level, event, data) => {
    const line = `[${new Date().toISOString()}] ${level.toUpperCase()} ${event} ${JSON.stringify(data || {})}\n`;
    try { fs.appendFileSync(path.join(logsDir, `scheduler-${new Date().toISOString().slice(0, 10)}.log`), line); } catch (_) {}
    if (opts.foreground) process.stderr.write(`[scheduler] ${line}`);
  };

  // ── Single-instance guard ──
  function _readDaemonLock() {
    try { return JSON.parse(fs.readFileSync(daemonLockFile, "utf8")); } catch (_) { return null; }
  }
  function _pidAlive(pid) {
    try { process.kill(pid, 0); return true; } catch (e) { return e.code === "EPERM"; }
  }
  const existing = _readDaemonLock();
  if (existing && existing.pid !== process.pid && _pidAlive(existing.pid)
      && Date.now() - new Date(existing.heartbeat_at || 0).getTime() < DAEMON_LOCK_STALE_MS) {
    process.stderr.write(`[scheduler] already running (pid ${existing.pid}) — exiting\n`);
    return Promise.resolve(2);
  }

  const store = createStore({
    dataDir,
    getSessionKeyFn: () => require("./auth_manager").getSessionKey("scheduler-v1", log),
    log,
  });

  const maxParallel = Math.max(1, Number(process.env.CONXA_MAX_CONCURRENT_RUNS) || 5);
  const inflight = new Map(); // schedule id -> in-flight promise
  const STARTED_AT = new Date().toISOString();
  let paused = false;
  let quitting = false;
  let shuttingDown = false;
  let tickRunning = false;
  let trayChild = null;
  let tickTimer = null;
  let idleTimer = null;
  let beatTimer = null;

  // ── Engine child (MCP client over stdio) ──
  let engine = null; // { client, lastUsedAt }
  async function _ensureEngine() {
    if (engine && engine.client) {
      engine.lastUsedAt = Date.now();
      return engine.client;
    }
    const req = global.__hostRequire || require;
    const { Client } = req("@modelcontextprotocol/sdk/client/index.js");
    const { StdioClientTransport } = req("@modelcontextprotocol/sdk/client/stdio.js");
    const cmd = resolveEngineCommand();
    const transport = new StdioClientTransport({
      command: cmd.command,
      args: cmd.args,
      // The SDK filters inherited env to a safe subset by default — the engine MUST
      // receive CONXA_DIR / CONXA_DATA_DIR / channel vars or it boots the wrong lane.
      env: process.env,
    });
    transport.onclose = () => { if (engine && engine.transport === transport) engine = null; };
    const client = new Client({ name: "conxa-scheduler", version: String(global.__runtimeVersion || "1.0.0") });
    await client.connect(transport);
    engine = { client, transport, lastUsedAt: Date.now() };
    log("info", "engine_connected", { pid: transport.pid ?? null });
    return client;
  }

  async function _stopEngine(reason) {
    const cur = engine;
    engine = null;
    if (!cur) return;
    try { await cur.client.close(); } catch (_) {}
    log("info", "engine_stopped", { reason: reason || "idle" });
  }

  // Idle reaper — the engine costs memory; scheduled work is bursty.
  idleTimer = setInterval(() => {
    if (engine && !quitting && inflight.size === 0 && Date.now() - engine.lastUsedAt > ENGINE_IDLE_KILL_MS) {
      _stopEngine("idle_timeout").catch(() => {});
    }
  }, 60_000);

  async function _callExecuteSkill(args) {
    const client = await _ensureEngine();
    const res = await client.callTool({ name: "execute_skill", arguments: args });
    return (res && Array.isArray(res.content) ? res.content.map((c) => c.text || "").join("\n") : "");
  }

  // ── Firing ──
  async function _fire(schedule, slotIso) {
    const id = schedule.id;
    const startedAt = new Date().toISOString();
    // The schedule had inputs configured, but this run couldn't decrypt them (a lost/rotated
    // keychain entry — see scheduler_store.js's _decryptedInputs) — firing anyway would run
    // with every input blank, silently. Record the slot as errored and skip firing rather than
    // guessing empty values into whatever the schedule was supposed to type.
    if (schedule.inputs_decrypt_failed) {
      log("error", "scheduled_run_inputs_undecryptable", { schedule_id: id, slug: schedule.slug, slot: slotIso });
      try {
        await store.update(id, {
          last_run: { at: startedAt, slot: slotIso || null, status: "error", run_id: null,
            message: "stored inputs could not be decrypted — re-create the schedule" },
        });
      } catch (_) {}
      inflight.delete(id);
      writeStateSafe();
      return;
    }
    log("info", "scheduled_run_start", { schedule_id: id, slug: schedule.slug, slot: slotIso, ad_hoc: !slotIso });
    writeStateSafe();
    try {
      const baseArgs = {
        skill: schedule.slug,
        ...(schedule.workspace_id ? { workspace_id: schedule.workspace_id } : {}),
        inputs: schedule.inputs || {},
        watch: false, // scheduled runs are always headless — visible windows compete for focus
        _trigger: "scheduled",
      };
      let text = await _callExecuteSkill(baseArgs);
      // The work was accepted but still running when the call ran out of time. Nobody is here to ask, so go back and
      // check on it (a re-check: nothing is done again) until it finishes, fails, or the time allowed runs out.
      for (let n = 0; n < PROOF_RECHECK_MAX; n++) {
        const wait = submittedResume(text);
        if (!wait) break;
        log("info", "scheduled_run_recheck", { schedule_id: id, slug: schedule.slug, attempt: n + 1, resume_from: wait.resumeFrom });
        await new Promise((resolve) => setTimeout(resolve, PROOF_RECHECK_WAIT_MS));
        text = await _callExecuteSkill({ ...baseArgs, resume_from: wait.resumeFrom });
      }
      // Still not confirmed after all that: an honest failure, never a quiet "done".
      if (submittedResume(text)) text = `Couldn't confirm in time. ${String(text).trim()}`;
      const status = classifyRunResult(text);
      const runId = _extractRunId(text);
      log(status === "completed" ? "info" : "warn", "scheduled_run_" + status, {
        schedule_id: id, slug: schedule.slug, slot: slotIso, run_id: runId,
      });
      const patch = {
        last_run: {
          at: startedAt,
          slot: slotIso || null,
          status, // completed | failed | busy | error
          run_id: runId,
          message: String(text).trim().slice(0, 300),
        },
      };
      if (status !== "busy" && slotIso) {
        // Advance the satisfied slot. A busy result leaves the slot PENDING — the
        // next tick retries it until catchup_grace_minutes runs out (computeActions).
        patch.last_slot_fired = slotIso;
        const cron = parseCron(schedule.cron);
        const next = nextAfter(cron, new Date(slotIso)) || nextAfter(cron, new Date());
        patch.next_run_at = next ? next.toISOString() : null;
      }
      await store.update(id, patch);
    } catch (e) {
      log("error", "scheduled_run_error", { schedule_id: id, slug: schedule.slug, error: e.message });
      try {
        await store.update(id, {
          last_run: { at: startedAt, slot: slotIso || null, status: "error", run_id: null, message: e.message.slice(0, 300) },
        });
      } catch (_) {}
    } finally {
      inflight.delete(id);
      writeStateSafe();
    }
  }

  // ── State for tray / CLI / doctor ──
  function writeStateSafe() {
    try { _writeState(); } catch (e) { log("warn", "state_write_failed", { error: e.message }); }
  }
  function _writeState() {
    let records;
    try { records = store.listMeta(); } catch (_) { records = []; }
    const active = [...inflight.entries()].map(([id]) => ({ schedule_id: id }));
    const body = {
      pid: process.pid,
      started_at: STARTED_AT,
      updated_at: new Date().toISOString(),
      paused,
      engine_running: !!engine,
      max_parallel: maxParallel,
      active,
      schedules: records.map((r) => ({
        id: r.id, name: r.name, slug: r.slug, workspace_id: r.workspace_id,
        enabled: r.enabled, cron: r.cron, next_run_at: r.next_run_at,
        last_run: r.last_run ? { at: r.last_run.at, slot: r.last_run.slot, status: r.last_run.status } : null,
      })),
    };
    fs.mkdirSync(baseDir, { recursive: true });
    const tmp = `${stateFile}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(body, null, 2));
    fs.renameSync(tmp, stateFile);
    return true;
  }

  // ── Commands from CLI/tray ──
  async function _drainCommands() {
    let names;
    try { names = fs.readdirSync(commandsDir).filter((n) => n.endsWith(".cmd")); } catch (_) { return false; }
    let wantQuit = false;
    for (const name of names) {
      const filePath = path.join(commandsDir, name);
      let cmd;
      try { cmd = JSON.parse(fs.readFileSync(filePath, "utf8")); } catch (_) { cmd = null; }
      try { fs.unlinkSync(filePath); } catch (_) {}
      if (!cmd || typeof cmd.type !== "string") continue;
      log("info", "command", cmd);
      switch (cmd.type) {
        case "pause": paused = true; break;
        case "resume": paused = false; break;
        case "quit": wantQuit = true; break;
        case "run_now": {
          try {
            const s = await store.get(cmd.schedule_id);
            if (s && !inflight.has(s.id)) {
              inflight.set(s.id, _fire(s, null)); // ad-hoc: touches last_run only, never the slot bookkeeping
            }
          } catch (e) { log("warn", "run_now_failed", { schedule_id: cmd.schedule_id, error: e.message }); }
          break;
        }
        default: break;
      }
    }
    return wantQuit;
  }

  // ── Tick ──
  async function _tick() {
    if (tickRunning || shuttingDown) return;
    tickRunning = true;
    try {
      if (await _drainCommands()) { await _shutdown("command"); return; }
      const schedules = await store.list();
      for (const a of computeActions(schedules, new Date(), { paused, inflight: new Set(inflight.keys()), maxParallel })) {
        switch (a.action) {
          case "fire":
            inflight.set(a.schedule.id, _fire(a.schedule, a.slot));
            break;
          case "skip":
            store.update(a.schedule.id, {
              last_slot_fired: a.last_missed_slot,
              next_run_at: a.next_run_at,
              last_run: {
                at: new Date().toISOString(), slot: a.last_missed_slot, status: "skipped",
                run_id: null, message: `${a.skipped_slots} scheduled slot(s) missed while the runner was unavailable (past catch-up grace)`,
              },
            }).catch((e) => log("warn", "skip_record_failed", { schedule_id: a.schedule.id, error: e.message }));
            log("warn", "slots_skipped", { schedule_id: a.schedule.id, slug: a.schedule.slug, skipped: a.skipped_slots });
            break;
          case "reschedule":
            store.update(a.schedule.id, { next_run_at: a.next_run_at })
              .catch((e) => log("warn", "reschedule_failed", { schedule_id: a.schedule.id, error: e.message }));
            break;
          case "invalid_cron":
            log("warn", "invalid_cron", { schedule_id: a.schedule.id, reason: a.reason });
            break;
          case "deferred":
            log("info", "run_deferred_capacity", { schedule_id: a.schedule.id, slug: a.schedule.slug, slot: a.slot });
            break;
          default: break;
        }
      }
    } catch (e) {
      log("error", "tick_failed", { error: e.message });
    } finally {
      tickRunning = false;
    }
    writeStateSafe();
  }

  // ── Heartbeat + lifecycle ──
  function _beat() {
    try {
      fs.writeFileSync(daemonLockFile, JSON.stringify({ pid: process.pid, heartbeat_at: new Date().toISOString() }));
    } catch (_) {}
  }

  async function _shutdown(reason) {
    if (shuttingDown) return;
    shuttingDown = true;
    quitting = true;
    log("info", "daemon_stopping", { reason: reason || "signal" });
    clearInterval(tickTimer);
    clearInterval(idleTimer);
    clearInterval(beatTimer);
    try { if (trayChild) process.kill(trayChild.pid); } catch (_) {}
    await Promise.allSettled([...inflight.values()]);
    await _stopEngine("shutdown");
    try { fs.unlinkSync(daemonLockFile); } catch (_) {}
    try { fs.unlinkSync(stateFile); } catch (_) {}
  }

  // ── Tray (Windows, PowerShell-native — zero npm deps, nothing extra shipped) ──
  function _spawnTray() {
    if (process.platform !== "win32" || opts.tray === false) return;
    const ps1 = path.join(__dirname, "tray_windows.ps1");
    if (!fs.existsSync(ps1)) return;
    try {
      trayChild = spawn(
        "powershell.exe",
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ps1, baseDir],
        { stdio: "ignore", windowsHide: true }
      );
      trayChild.on("exit", () => { trayChild = null; });
    } catch (e) {
      log("warn", "tray_spawn_failed", { error: e.message });
    }
  }

  // Command responsiveness: the 30s tick drains commands anyway, but pause/resume
  // from the tray should feel instant — watch the directory and tick early. The
  // tickRunning guard in _tick() serializes overlaps.
  try {
    fs.watch(commandsDir, () => { setTimeout(() => _tick(), 150); });
  } catch (_) { /* commands still drained each tick */ }

  tickTimer = setInterval(() => _tick(), TICK_MS);
  beatTimer = setInterval(_beat, 5_000);
  if (idleTimer.unref) idleTimer.unref();
  if (tickTimer.unref) tickTimer.unref();

  process.on("SIGINT", () => _shutdown("SIGINT"));
  process.on("SIGTERM", () => _shutdown("SIGTERM"));

  _beat();
  _spawnTray();
  log("info", "daemon_started", { pid: process.pid, data_dir: dataDir, max_parallel: maxParallel });
  writeStateSafe();
  _tick();

  // Keep the process alive independently of the unref'd timers.
  return new Promise((resolve) => {
    const done = setInterval(() => {
      if (shuttingDown && inflight.size === 0) { clearInterval(done); resolve(0); }
    }, 500);
  });
}

module.exports = { computeActions, resolveEngineCommand, classifyRunResult, submittedResume, start, fileLock };
