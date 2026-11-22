// A run that is waiting for a human to sign in is detached from its execute_skill call, so the
// registry has to answer three questions the caller can no longer answer from a return value:
// "is it still waiting, and on what" (awaiting_auth), "how did it end" (recent results), and
// "stop it" (cancel reaches a run that isn't executing anything yet).
const test   = require("node:test");
const assert = require("node:assert");

const reg = require("../../app/run_registry");

const apps = [{ id: "sf", name: "Salesforce", key: "ws__sf" }, { id: "gd", name: "Google Drive", key: "ws__gd" }];
const state = { "ws__sf": "signed_in", "ws__gd": "waiting" };
const begin = (id = "r_1") => reg.beginAwaitingAuth({ run_id: id, skill: "sync-leads", workspace_id: "ws", apps, statusOf: (k) => state[k] });

test.beforeEach(() => { reg._resetAuth(); });

test("an awaiting run reports each app's live sign-in status, by name", () => {
  begin();
  const [w] = reg.listAwaitingAuth();
  assert.strictEqual(w.run_id, "r_1");
  assert.strictEqual(w.skill, "sync-leads");
  assert.deepStrictEqual(w.apps, [{ app: "Salesforce", status: "signed_in" }, { app: "Google Drive", status: "waiting" }]);
  assert.ok(w.elapsed_ms >= 0);
  state["ws__gd"] = "signed_in"; // the human finishes the second one
  assert.strictEqual(reg.listAwaitingAuth()[0].apps[1].status, "signed_in");
  state["ws__gd"] = "waiting";
});

test("ending the wait removes it from the awaiting list", () => {
  begin();
  reg.endAwaitingAuth("r_1");
  assert.deepStrictEqual(reg.listAwaitingAuth(), []);
});

test("status(run_id) tells awaiting, then completed/failed once recorded, and null for an unknown id", () => {
  begin();
  assert.strictEqual(reg.status("r_1").state, "awaiting_auth");
  reg.endAwaitingAuth("r_1");
  reg.recordResult("r_1", { skill: "sync-leads", status: "completed", summary: "Done. URL: https://x" });
  const done = reg.status("r_1");
  assert.strictEqual(done.state, "completed");
  assert.strictEqual(done.summary, "Done. URL: https://x");
  reg.recordResult("r_2", { skill: "s", status: "failed", summary: "Authentication failed or was cancelled." });
  assert.strictEqual(reg.status("r_2").state, "failed");
  assert.strictEqual(reg.status("nope"), null);
});

test("recent results are capped, newest kept", () => {
  for (let i = 0; i < 15; i++) reg.recordResult(`r_${i}`, { skill: "s", status: "completed", summary: `n${i}` });
  const recent = reg.listRecent();
  assert.strictEqual(recent.length, 10);
  assert.strictEqual(recent[recent.length - 1].run_id, "r_14");
  assert.strictEqual(reg.status("r_0"), null, "the oldest fell off");
  assert.strictEqual(reg.status("r_14").state, "completed");
});

test("recording a result for the same run twice keeps only the latest", () => {
  reg.recordResult("r_1", { skill: "s", status: "failed", summary: "first" });
  reg.recordResult("r_1", { skill: "s", status: "completed", summary: "second" });
  assert.strictEqual(reg.listRecent().filter((r) => r.run_id === "r_1").length, 1);
  assert.strictEqual(reg.status("r_1").summary, "second");
});

test("a very long summary is trimmed so the ring stays small", () => {
  reg.recordResult("r_1", { skill: "s", status: "completed", summary: "x".repeat(5000) });
  assert.ok(reg.status("r_1").summary.length <= 1200);
});

test("cancel reaches a run that is only waiting for sign-in, and the waiter can see it", () => {
  begin();
  assert.strictEqual(reg.isAuthCancelled("r_1"), false);
  assert.strictEqual(reg.requestCancel("r_1"), true);
  assert.strictEqual(reg.isAuthCancelled("r_1"), true);
  assert.strictEqual(reg.requestCancel("nope"), false, "an unknown id is still reported, not silently accepted");
});

test("awaiting runs do not count against the concurrent-run cap (they hold no run slot)", () => {
  begin();
  assert.strictEqual(reg.count(), 0);
  assert.deepStrictEqual(reg.list(), []);
});

test("an identical request already waiting is found, so a second execute_skill never spawns a duplicate run", () => {
  reg.beginAwaitingAuth({ run_id: "r_1", skill: "sync-leads", workspace_id: "ws", apps, statusOf: (k) => state[k], fingerprint: "fp-A" });
  assert.strictEqual(reg.findAwaiting("fp-A").run_id, "r_1");
  assert.strictEqual(reg.findAwaiting("fp-B"), null, "different inputs are a different run");
  reg.endAwaitingAuth("r_1");
  assert.strictEqual(reg.findAwaiting("fp-A"), null);
});

test("a run that has started executing is no longer listed as awaiting, even though its wait entry lingers", () => {
  begin("r_1");
  const exec = { runId: "r_1", slug: "sync-leads", workspace_id: "ws", step: 0, total: 3, startedAt: new Date().toISOString() };
  assert.strictEqual(reg.begin(exec), true);
  try {
    assert.deepStrictEqual(reg.listAwaitingAuth(), []);
    assert.strictEqual(reg.status("r_1").state, "running");
  } finally { reg.end("r_1"); }
});

// WF-16 16.1: every execute call records how it ended, not only one that waited for sign-in.
const t = (text, _meta) => ({ content: [{ type: "text", text }], ...(_meta ? { _meta } : {}) });

test("recordCallResult: a finished run the caller was given an id for reads back as completed / failed / cancelled / paused", () => {
  assert.strictEqual(reg.recordCallResult("r_ok", "s", t("Done. URL: https://x\n(run_id: r_ok)")), true);
  assert.strictEqual(reg.status("r_ok").state, "completed");
  assert.match(reg.status("r_ok").summary, /^Done\./);
  reg.recordCallResult("r_bad", "s", { content: [{ type: "text", text: "Step 3 failed." }, { type: "text", text: "(run_id: r_bad)" }], isError: true });
  assert.strictEqual(reg.status("r_bad").state, "failed");
  reg.recordCallResult("r_c", "s", t("Execution cancelled. (run_id: r_c)"));
  assert.strictEqual(reg.status("r_c").state, "cancelled");
  reg.recordCallResult("r_p", "s", t("Pick one option. (run_id: r_p)", { "conxa/fork": { step_index: 6 } }));
  assert.strictEqual(reg.status("r_p").state, "paused");
  assert.deepStrictEqual(reg.listRecent().map((r) => r.run_id), ["r_ok", "r_bad", "r_c", "r_p"]);
});

test("recordCallResult skips a refusal that never showed the id, and a hand-off to a sign-in wait", () => {
  assert.strictEqual(reg.recordCallResult("r_x", "s", t("Skill not found: s. Call list_skills.")), false);
  assert.strictEqual(reg.recordCallResult("r_y", "s", t("Authentication required — sign in. (run_id: r_y)", { "conxa/awaiting_auth": { run_id: "r_y" } })), false);
  assert.deepStrictEqual(reg.listRecent(), []);
  // A background continuation of a named run is recorded even when its own answer shows no id.
  assert.strictEqual(reg.recordCallResult("r_z", "s", t("Retry budget exhausted at step 2."), { continuation: true }), true);
  assert.strictEqual(reg.status("r_z").state, "failed");
});

test("a hand-over pause that names the run id reads back as paused, then the self-resume finishes under the same id", async () => {
  const { buildHandoverRequest } = require("../../app/handover");
  const page = { url: () => "https://x.example", screenshot: async () => Buffer.from("s"), isClosed: () => false };
  const pause = await buildHandoverRequest(page, { message: "Please sign" }, 1, { runId: "r_ho" });
  assert.strictEqual(reg.recordCallResult("r_ho", "s", pause), true, "the pause shows the id, so it is recorded");
  assert.strictEqual(reg.status("r_ho").state, "paused");
  assert.strictEqual(reg.recordCallResult("r_ho", "s", t("Done. URL: https://x"), { continuation: true }), true);
  assert.strictEqual(reg.status("r_ho").state, "completed", "the same id now reads as finished");
});
