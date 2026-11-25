# What YOU must do for P0 (2026-11-25)

This list comes from the P0 section of `TODO.md`. Code work is done or needs a session. These items need a person: you.

Tasks 1, 2, 3, 6, 7 and 8 are done and filed in `Done.md`; their numbers are not reused.

---

## Task 4 — Rebuild and republish the GitHub tour workflow (P0-4, item EXEC-67)

**Why:** The shipped pack `wf3-github-tour-40` still has the old wrong link in it. It fails at step 3 ("Bound record could not be uniquely located"). The code fix is done. The old pack must be rebuilt.

**Steps:**

1. Open Build Studio. Open workflow `wf3-github-tour-40`.
2. Rebuild it (the rebuild action).
3. Run a test. Step 3 must now pass.
4. **Pulse step:** the saved Pulse link now lands on the wrong page (`/insights`). Open **Human Edit**, find the Pulse step, and point it at the real Pulse tab again. (Chat cannot do this. Only a person can re-point a step.)
5. Publish it again.
6. On a real, slow export, watch how long the run waits. The wait is 20 seconds now and was never tuned on real data. Tell me if it is too short or too long.

---

## Task 5 — Rebuild old workflows and answer their questions (P0-5, BUILD-117 parts 1 and 10)

Workflows made before the fix keep an old, wrong re-check list until rebuilt.

1. In Studio, rebuild these three: **LP paste form**, **LP delete customer**, **LP secure logout**.
2. For each, answer the questions again when Studio asks.
3. A workflow where you cleared a pre-filled box without typing must be **re-recorded**. The clear was lost.

---

## Task 9 — Finish the long tab test (P0-7, item EXEC-10)

The 35-action, 3-tab recording ("EXEC10 tabs r3") passes 33 of 34 steps. The last step fails.

**What needs a person:**

1. Run the replay on the **real released host exe** (not the dev exe). The fixes are only proven on the dev exe. Tell me the result.
2. Re-record or fix the failing step "Type the number" on the-internet `/inputs` (an unlabelled input). Ask me for the fix first. It is a code issue, but you must re-test after.
3. Note: recording on demoqa is very slow (ad frames, about one event per 2 minutes). Plan extra time.

---

## Task 10 — The two big manual test passes (P0-8, items TEST-11 and TEST-12) — do LAST

**Do not start until:** tasks 3–9 are done or decided.

**Step 1 — Fix the AI keys first.** Old keys may be expired or rate-limited. A bad key hides real bugs.
1. Follow `conxa-cloud/backend/ROUTER_SETUP.md`. Refresh each provider key.
2. Check each provider answers a test request before any test.

**Step 2 — TEST-11:** Run every test plan in `docs/testing/01-WORKFLOWS-TO-TEST.md` (WF-1 to WF-12). Record the result of each. Anything that fails: either fix it or write it as its own new item in `TODO.md` with how to repeat it.

**Step 3 — TEST-12:** Run the hard-mode gauntlet (WF-9 in the same file):
1. Build the mutator test page first (Phase 0 of the doc).
2. Record one ~60-step workflow across 6 sites and 4 tabs.
3. Run replays R1 to R8.
4. Run the overnight 20-times loop.

**Pass means:** the recording compiles to 50+ steps; R1, R2, R5, R6 pass; R2's empty-result check and R4 fail with a clear message; R7 refuses cleanly; R8 shows steady memory.

---

## Nothing to do (just so you know)

- **AUTH-26:** Done 2026-11-25 (per-account folders, checked live against a mock sign-in). Nothing for you to do.

## Quick order

| # | Task | Time | Blocks |
|---|---|---|---|
| 4 | Rebuild GitHub tour + Pulse fix | 30 min | P0-4 close |
| 5 | Rebuild 3 LP workflows | 30 min | P0-5 close |
| 9 | Real-exe tab test | 1 hr | P0-7 close |
| 10 | Keys, then two big test passes | days | P0 close |
