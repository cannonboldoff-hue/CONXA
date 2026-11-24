# What YOU must do for P0 (2026-11-25)

This list comes from the P0 section of `TODO.md`. Code work is done or needs a session. These items need a person: you.

Do them in this order. Tasks 1 and 2 are first because they block other work.

---

## Task 1 — Connect releases to the cloud (P0-H1, item UPD-5) — do today

**Why:** Every release build shows green but never tells the cloud about the new version. So no customer ever gets an update. The fix is two settings on GitHub. There is no code to write.

**Time:** about 15 minutes.

**Steps:**

1. Open the Render dashboard. Open the production backend service. Go to **Environment**. Copy the value of `CONXA_ADMIN_TOKEN`. Keep it private.
2. Open the Conxa repo on GitHub. Go to **Settings → Secrets and variables → Actions**.
3. Click the **Variables** tab. Add a new repository variable:
   - Name: `CLOUD_API_URL`
   - Value: `https://apis.conxa.in`
4. Click the **Secrets** tab. Add a new repository secret:
   - Name: `CLOUD_ADMIN_TOKEN`
   - Value: the token you copied in step 1.
5. Put the already-built versions into the cloud. Do ONE of these:
   - **Easy:** re-run the release workflows (`build-runtime-host.yml`, `build-runtime-app.yml`) on the latest tags (`host-v3.2.0`, `app-v3.2.0` or newer). Use the Actions tab → pick the run → **Re-run all jobs**.
   - **Or** ask me to write the one POST command for you. You still need the token.
6. Check it worked. In a terminal run:
   ```
   curl https://apis.conxa.in/api/v1/manifest.json
   ```
   `conxa_runtime` and `conxa_app` must show the latest tagged versions.

**Done when:** the curl shows the latest versions. Then tell me. I will run the live check for P0-1 (Studio's update source) and close it.

---

## Task 2 — Clean up the shared installer slot on production (P0-H2, item OPS-1)

**Why:** Before a past bug was fixed, every company's installer was saved into one shared place (`v1` / `v2`). Anyone opening `/installers/v2` could get another company's installer. The bad files may still be on the server.

**Do this only after the fix (SG-28) is deployed to production.** If you do it before, the slot fills up again.

**Steps:**

1. Confirm the SG-28 fix is live on Render (latest deploy includes commit `861a6d5c` or newer).
2. On the production server storage, look for:
   - folders `data/installers/v1/` and `data/installers/v2/`
   - database rows named `installer_versions__v1` and `installer_versions__v2`
3. Look at what is inside first. Then delete those folders and rows. Do not touch any other company's folder.
4. Check in a browser or with curl:
   - `https://apis.conxa.in/api/v1/installers/v1` must give **404**
   - `https://apis.conxa.in/api/v1/installers/v2` must give **404**
5. Email every vendor who was ever sent an `/installers/v2` link. Say: the link may have given them another company's installer, they should rebuild and re-download their own installer. Ask me if you want a draft email.

**Do not** do this from the repo or from tests. It is real production data.

---

## Task 3 — Decide: Electron's own OK/Cancel box (P0-5, item BUILD-117 part 4)

**What happens:** When you test a workflow inside Studio and the website shows an "OK / Cancel" popup, Studio shows its own card to answer it. But Electron's own grey OK/Cancel box also stays on screen after the answer. This only happens in the test run view, not when recording.

**The choice:**

- **A (my recommendation):** Stop showing Electron's own boxes in run views. Studio's card is the only place to answer. Cleaner for non-technical people.
- **B:** Leave it as it is.

**What to do:** Reply to me with "A" or "B". Once you decide, I build it.

**Also test by hand (needs Studio open):**

1. Run a test where the page shows an OK/Cancel popup.
2. Check Studio shows its card for it.
3. Click **OK on Electron's box** instead of the card. Does the run carry on correctly?
4. Check the leftover box does not block Studio's window.

Tell me what you saw.

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

## Task 6 — Small housekeeping (P0-5)

1. Delete the empty folder `C:\c` by hand (File Explorer).
2. In the Studio sidebar delete the two throwaway chats: **LP price check** and **Export a recurring report**.

---

## Task 7 — Check the chat change live (P0-5, BUILD-117 part 9)

I changed the chat so it first checks if the workflow already does what you asked. This is a prompt change. Only a live try shows if it works.

1. Open a chat in Studio. Ask for something the workflow already does.
2. Does the chat say so, instead of adding a duplicate step?
3. Also look for the old bug: General Chat says "I can't create the workflow from here" but a "create workflow" card shows under it. Does it still happen?

Tell me what you saw.

---

## Task 8 — Decisions I need from you (BUILD-117 part 11)

Reply to each with a short answer:

1. After you answer a question, the line "went back, asked again below" stays. **Hide it, or keep it?**
2. A captured price was named `shop_search` because the name came from the page heading. **Change the naming rule?** (This is your call. I will not guess.)
3. The first message in a new chat only shows after a reload. If you can make it happen again, tell me the exact steps.

---

## Task 9 — Finish the long tab test (P0-7, item EXEC-10)

The 35-action, 3-tab recording ("EXEC10 tabs r3") passes 33 of 34 steps. The last step fails.

**What needs a person:**

1. Run the replay on the **real released host exe** (not the dev exe). The fixes are only proven on the dev exe. Tell me the result.
2. Re-record or fix the failing step "Type the number" on the-internet `/inputs` (an unlabelled input). Ask me for the fix first. It is a code issue, but you must re-test after.
3. Note: recording on demoqa is very slow (ad frames, about one event per 2 minutes). Plan extra time.

---

## Task 10 — The two big manual test passes (P0-8, items TEST-11 and TEST-12) — do LAST

**Do not start until:** Task 1 is done, and tasks 3–9 are done or decided.

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

- **P0-H3 — AUTH-26:** You already decided: leave as designed. No work.
- **P0-2, P0-3, P0-6:** Done and closed.

## Quick order

| # | Task | Time | Blocks |
|---|---|---|---|
| 1 | GitHub settings + check | 15 min | everything in update pipeline, Task 10 |
| 2 | Purge shared installer slot | 30 min | safety of customer installers |
| 3 | Decide A or B on OK/Cancel box | 2 min | one build |
| 4 | Rebuild GitHub tour + Pulse fix | 30 min | P0-4 close |
| 5 | Rebuild 3 LP workflows | 30 min | P0-5 close |
| 6 | Delete `C:\c`, 2 chats | 2 min | none |
| 7 | Live check chat | 10 min | P0-5 close |
| 8 | Three small decisions | 5 min | none |
| 9 | Real-exe tab test | 1 hr | P0-7 close |
| 10 | Keys, then two big test passes | days | P0 close |
