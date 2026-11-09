"use strict";
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { classifyRunResult, extractRunId, CHAT_TOOLS } = require("../classify");

describe("classifyRunResult", () => {
  it("marks Done. as completed", () => {
    assert.equal(classifyRunResult("Done. (run_id: abc)"), "completed");
  });
  it("marks a run that finished with a note, or was accepted, as completed", () => {
    assert.equal(classifyRunResult("Done, please check. URL: x (run_id: abc)"), "completed");
    assert.equal(classifyRunResult("Submitted. The upload goes on. (run_id: abc)"), "completed");
  });
  it("never reads 'couldn't confirm' or 'partly done' as success", () => {
    assert.equal(classifyRunResult("Couldn't confirm. I couldn't confirm that the upload finished."), "failed");
    assert.equal(classifyRunResult("Partly done. 2 of 3 files."), "failed");
  });
  it("marks cap refusal as busy", () => {
    assert.equal(classifyRunResult("Too many workflows are already running"), "busy");
  });
  it("marks a run waiting on the user's sign-in as awaiting_auth, not failed", () => {
    // The run is detached and starts by itself once they sign in — showing "Failed" would be a lie.
    assert.equal(classifyRunResult("Authentication required — please sign in to Salesforce in the browser window that just opened. (run_id: r_1)."), "awaiting_auth");
    assert.equal(classifyRunResult("Waiting for authentication to complete in the browser — finish signing in to Salesforce."), "awaiting_auth");
    assert.equal(classifyRunResult("Authentication failed: the browser could not start"), "failed");
  });
  it("extracts the run_id from the awaiting-auth message", () => {
    assert.equal(extractRunId("...Check progress with get_execution_status (run_id: r_mub05v4i_2jjgj)."), "r_mub05v4i_2jjgj");
  });
  it("marks anything else failed", () => {
    assert.equal(classifyRunResult("Skill not found"), "failed");
  });
  it("extracts run_id", () => {
    assert.equal(extractRunId("Done. (run_id: run_9)"), "run_9");
  });
});

describe("chat tool lock", () => {
  it("only allows the five Conxa MCP tools", () => {
    // authenticate opens sign-in windows for the user to type into; it never takes credentials.
    // get_execution_status is read-only: it is how the chat follows a run that is waiting for sign-in.
    assert.deepEqual([...CHAT_TOOLS].sort(), ["authenticate", "execute_skill", "get_execution_status", "get_skill_inputs", "list_skills"]);
    assert.equal(CHAT_TOOLS.has("bash"), false);
    assert.equal(CHAT_TOOLS.has("write"), false);
  });
});
