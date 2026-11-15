"use strict";
// The runtime's stderr copy of its log. The file log is the record; stderr is a convenience for whoever launched us
// (Build Studio reads step progress from it). A parent that does not read stderr fills the pipe, and on Windows a write
// to a full pipe blocks the WHOLE thread: timers, the run deadline and the inspector all stop, and a run hangs forever.
// (Measured: Node 24 on Windows, every process stopped at about 115 KB of log; stream.setBlocking(false) does not help.)
//
// So nothing on the main thread ever writes to stderr. A worker thread owns the (possibly blocking) write behind a
// bounded byte budget shared through an Int32Array. When the budget is full the line is dropped, never waited for.

const { Worker } = require("worker_threads");

const LIMIT_BYTES = 256 * 1024;
const EXIT_DRAIN_MS = 200;

const WORKER_SRC = `
  const { parentPort, workerData } = require("worker_threads");
  const fs = require("fs");
  const queued = new Int32Array(workerData);
  parentPort.on("message", (line) => {
    try { fs.writeSync(2, line); } catch (_) { /* a closed or full pipe is the parent's problem, not a reason to stop */ }
    Atomics.sub(queued, 0, Buffer.byteLength(line));
    Atomics.notify(queued, 0);
  });
`;

function createStderrSink({ limitBytes = LIMIT_BYTES } = {}) {
  let worker = null;
  let queued = null;
  let broken = false;
  let dropped = 0;

  function start() {
    try {
      queued = new Int32Array(new SharedArrayBuffer(4));
      worker = new Worker(WORKER_SRC, { eval: true, workerData: queued.buffer });
      worker.unref(); // never keeps the process alive
      worker.on("error", () => { broken = true; });
      worker.on("exit", () => { broken = true; });
      process.once("exit", onProcessExit);
    } catch (_) {
      broken = true;
    }
  }

  // process.exit() waits for every worker thread to stop, and a thread blocked inside a write to a full pipe never
  // stops (measured: the process hangs for good). So at exit the worker gets a short chance to drain; if the pipe is
  // still stuck the process ends itself right here instead of waiting for it. Clean shutdowns are not affected.
  function onProcessExit() {
    if (!queued || broken) return;
    const deadline = Date.now() + EXIT_DRAIN_MS;
    let left = Atomics.load(queued, 0);
    while (left > 0 && Date.now() < deadline) {
      Atomics.wait(queued, 0, left, Math.max(1, deadline - Date.now()));
      left = Atomics.load(queued, 0);
    }
    if (left > 0) process.kill(process.pid, "SIGKILL");
  }

  return {
    /** Queue one line for stderr. Returns false when it was dropped. Never blocks. */
    write(line) {
      if (broken) { dropped++; return false; }
      if (!worker) start();
      if (broken) { dropped++; return false; }
      const n = Buffer.byteLength(line);
      if (Atomics.load(queued, 0) + n > limitBytes) { dropped++; return false; }
      Atomics.add(queued, 0, n);
      worker.postMessage(line);
      return true;
    },
    stats() { return { dropped, queuedBytes: queued ? Atomics.load(queued, 0) : 0, broken }; },
    close() { try { if (worker) worker.terminate(); } catch (_) { /* already gone */ } },
  };
}

/** Route every write made to this process's stderr (console.error, Node warnings, a dependency) through `sink`, so no
 *  stray main-thread write can block on a pipe nobody reads. Returns a function that restores the original. */
function takeOverProcessStderr(sink) {
  const original = process.stderr.write;
  process.stderr.write = function (chunk, encoding, cb) {
    try { sink.write(typeof chunk === "string" ? chunk : Buffer.from(chunk).toString("utf8")); } catch (_) { /* never throw from stderr */ }
    const done = typeof encoding === "function" ? encoding : cb;
    if (typeof done === "function") done();
    return true;
  };
  return () => { process.stderr.write = original; };
}

module.exports = { createStderrSink, takeOverProcessStderr, LIMIT_BYTES };
