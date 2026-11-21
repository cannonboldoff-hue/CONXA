"use strict";
/** Follows a run the runtime detached (waiting for sign-in) until it ends. */

// "paused": the run stopped to wait for an answer; its call is over, so following it ends too.
const TERMINAL = new Set(["completed", "failed", "cancelled", "paused", "unknown"]);
// A chat-visible marker instead of a new message field: the transcript goes to the model proxy as-is.
const FOLLOW_UP_PREFIX = "[Conxa run update]";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Polls get_execution_status until the run ends; tool errors / bad JSON just keep polling. */
async function waitForRunEnd(runId, callTool, { intervalMs = 3000 } = {}) {
  for (;;) {
    try {
      const st = JSON.parse(await callTool("get_execution_status", { run_id: runId }));
      if (st && TERMINAL.has(st.state)) return st;
    } catch {
      // engine busy or restarting — try again
    }
    await sleep(intervalMs);
  }
}

module.exports = { waitForRunEnd, TERMINAL, FOLLOW_UP_PREFIX };
