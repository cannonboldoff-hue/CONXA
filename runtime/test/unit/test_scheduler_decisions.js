"use strict";

// scheduler_daemon.js pure decision logic (PROD-5). Pinned here — the exact
// missed-run contract the product promised:
//   due now            → fire
//   overdue ≤ grace    → fire (catch-up), oldest slot first
//   overdue > grace    → ONE skip record, fast-forwarded to the newest missed slot
//   capacity pressure  → DEFERRED (retried next tick), never skipped
//   paused/disabled/inflight/invalid-cron → handled without firing

const test = require("node:test");
const assert = require("node:assert");
const { computeActions, classifyRunResult, submittedResume } = require("../../app/scheduler_daemon");

const NOW = new Date(2026, 7, 26, 9, 0); // Wed Aug 26 2026 09:00 local

function sched(overrides = {}) {
  return {
    id: "sch_a",
    slug: "some-skill",
    cron: "0 * * * *", // hourly, on the hour
    enabled: true,
    catchup_grace_minutes: 60,
    next_run_at: new Date(2026, 7, 26, 8, 0).toISOString(), // one hour ago → DUE
    last_slot_fired: new Date(2026, 7, 26, 7, 0).toISOString(),
    ...overrides,
  };
}

test("a due schedule fires with its ISO slot", () => {
  const actions = computeActions([sched()], NOW, { inflight: [], maxParallel: 5 });
  const fire = actions.find((a) => a.action === "fire");
  assert.ok(fire);
  assert.strictEqual(fire.slot, new Date(2026, 7, 26, 8, 0).toISOString());
});

test("overdue WITHIN grace still fires (catch-up after reboot/sleep)", () => {
  // Slot was 07:00; now 08:40 → 100min old, grace 120min → catch-up fires.
  const s = sched({
    next_run_at: new Date(2026, 7, 26, 7, 0).toISOString(),
    catchup_grace_minutes: 120,
  });
  const actions = computeActions([s], new Date(2026, 7, 26, 8, 40), { inflight: [], maxParallel: 5 });
  assert.ok(actions.find((a) => a.action === "fire"));
});

test("overdue BEYOND grace skips ONCE and fast-forwards to the newest missed slot", () => {
  // Hourly slots at 02:00..08:00 all pending, now 09:00, grace 60 → the 08:00 slot
  // is exactly at the edge (fires); use 07:00 as oldest pending so 08:00 is also
  // past… no: keep it crisp — oldest pending 06:00, grace 30min. Missed: 06:00,
  // 07:00, 08:00 (09:00 hasn't arrived yet). Skip records 3 slots, newest 08:00,
  // next_run_at 09:00.
  const s = sched({
    next_run_at: new Date(2026, 7, 26, 6, 0).toISOString(),
    catchup_grace_minutes: 30,
  });
  const actions = computeActions([s], NOW, { inflight: [], maxParallel: 5 });
  const skip = actions.find((a) => a.action === "skip");
  assert.ok(skip, JSON.stringify(actions));
  assert.strictEqual(skip.skipped_slots, 3);
  assert.strictEqual(skip.last_missed_slot, new Date(2026, 7, 26, 8, 0).toISOString());
  assert.strictEqual(skip.next_run_at, new Date(2026, 7, 26, 9, 0).toISOString());
});

test("capacity pressure defers instead of skipping or over-firing", () => {
  // All three slots within grace (600min) so each takes the fire path and only the
  // parallel budget decides fire vs deferred.
  const schedules = [
    sched({ id: "sch_1", next_run_at: new Date(2026, 7, 26, 6, 0).toISOString(), catchup_grace_minutes: 600 }),
    sched({ id: "sch_2", next_run_at: new Date(2026, 7, 26, 7, 0).toISOString(), catchup_grace_minutes: 600 }),
    sched({ id: "sch_3", next_run_at: new Date(2026, 7, 26, 8, 0).toISOString(), catchup_grace_minutes: 600 }),
  ];
  const actions = computeActions(schedules, NOW, { inflight: [], maxParallel: 2 });
  const fires = actions.filter((a) => a.action === "fire");
  const deferred = actions.filter((a) => a.action === "deferred");
  assert.strictEqual(fires.length, 2);
  assert.strictEqual(deferred.length, 1);
  // Oldest-due first:
  assert.strictEqual(fires[0].schedule.id, "sch_1");
  assert.strictEqual(fires[1].schedule.id, "sch_2");
  assert.strictEqual(deferred[0].schedule.id, "sch_3");
});

test("paused, disabled, in-flight, and future schedules produce no fire actions", () => {
  const base = [sched()];
  assert.deepStrictEqual(computeActions(base, NOW, { paused: true, inflight: [], maxParallel: 5 }).filter((a) => a.action === "fire"), []);
  assert.deepStrictEqual(computeActions([sched({ enabled: false })], NOW, { inflight: [], maxParallel: 5 }).filter((a) => a.action === "fire"), []);
  assert.deepStrictEqual(computeActions(base, NOW, { inflight: ["sch_a"], maxParallel: 5 }).filter((a) => a.action === "fire"), []);

  const future = sched({ next_run_at: new Date(2026, 7, 26, 10, 0).toISOString() });
  const acts = computeActions([future], NOW, { inflight: [], maxParallel: 5 });
  assert.strictEqual(acts.filter((a) => a.action === "fire" || a.action === "skip").length, 0);
});

test("an invalid cron surfaces as invalid_cron — never silently ignored", () => {
  const actions = computeActions([sched({ cron: "garbage" })], NOW, { inflight: [], maxParallel: 5 });
  assert.ok(actions.find((a) => a.action === "invalid_cron"));
});

test("a legacy/fresh record without next_run_at gets rescheduled, not fired", () => {
  const actions = computeActions([sched({ next_run_at: null })], NOW, { inflight: [], maxParallel: 5 });
  const r = actions.find((a) => a.action === "reschedule");
  assert.ok(r);
  assert.strictEqual(r.next_run_at, new Date(2026, 7, 26, 10, 0).toISOString()); // strictly future
});

test("classifyRunResult reads engine responses by their stable markers", () => {
  assert.strictEqual(classifyRunResult("Done. URL: https://x\n(run_id: r_1)"), "completed");
  assert.strictEqual(classifyRunResult("Done, please check. URL: https://x\n\nPlease check:\n  - note\n(run_id: r_1)"), "completed");
  assert.strictEqual(classifyRunResult("Submitted. The upload goes on. (run_id: r_1)"), "completed");
  for (const unsure of ["Couldn't confirm. I couldn't confirm that \"Send\" finished.", "Partly done. 2 of 3."]) {
    assert.strictEqual(classifyRunResult(unsure), "failed", `${unsure} must not read as success`);
  }
  assert.strictEqual(classifyRunResult("Too many workflows are already running (5/5): …"), "busy");
  assert.strictEqual(classifyRunResult("Execution stopped after exceeding the 210s time budget…"), "failed");
  assert.strictEqual(classifyRunResult(""), "failed");
});

test("submittedResume reads the step to check on from a 'Submitted.' result, and nothing else", () => {
  const text = 'Submitted. "Upload the file" (step 4) is still running in the app. Call execute_skill again for "x" with resume_from: 3 to check on it. (run_id: r_1)';
  assert.deepStrictEqual(submittedResume(text), { resumeFrom: 3 });
  assert.strictEqual(submittedResume("Done. URL: x (run_id: r_1)"), null);
  assert.strictEqual(submittedResume("Submitted. no step named"), null);
  assert.strictEqual(submittedResume(""), null);
  // after the allowed time it is reported as a failure, not as a quiet completion
  assert.strictEqual(classifyRunResult(`Couldn't confirm in time. ${text}`), "failed");
});
