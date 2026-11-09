"use strict";
/** Copied from runtime/app/scheduler_daemon.js so form history matches scheduled runs. */

/** Chat path: Conxa MCP tools only. No bash, write, or OpenCode default tools. */
// get_execution_status is read-only: it is how the chat follows a run that is waiting for sign-in.
const CHAT_TOOLS = new Set(["list_skills", "get_skill_inputs", "execute_skill", "authenticate", "get_execution_status"]);

function classifyRunResult(text) {
  const t = String(text || "");
  const trimmed = t.trim();
  // "Done, please check." finished with a note; "Submitted." was accepted and finishes on its own.
  // "Couldn't confirm." and "Partly done." are not success. Same rule as the runtime scheduler.
  if (/^(Done\.|Done, please check\.|Submitted\.)/.test(trimmed)) return "completed";
  if (t.includes("Too many workflows are already running")) return "busy";
  // The runtime detached the run: sign-in tabs are open and the workflow starts by itself afterwards.
  if (/^(Authentication required — please sign in|Waiting for authentication to complete)/.test(trimmed)) return "awaiting_auth";
  return "failed";
}

function extractRunId(text) {
  const m = /\(run_id:\s*([^)]+)\)/.exec(String(text || ""));
  return m ? m[1].trim() : null;
}

module.exports = { classifyRunResult, extractRunId, CHAT_TOOLS };
