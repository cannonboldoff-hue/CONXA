# 🎯 One Workflow Runbook — the whole test suite as ONE recording

This replaces the leg-by-leg grind of [`01-WORKFLOWS-TO-TEST.md`](01-WORKFLOWS-TO-TEST.md)
with **one mega-recording** followed by **a replay gauntlet**. Same coverage, one take.

**Why this works:** almost every leg in 01 shares mechanics (click / type / assert / tab /
iframe / download). Record them once, then turn the SAME compiled skill into every hard-mode
test just by changing how you replay it. That is exactly the product pitch — so testing it
this way also proves the pitch.

**What cannot merge (kept as tiny companions):**

| Item | Why it can't merge | Where |
|---|---|---|
| M1 — Recovery drill | Needs a deliberately-broken element per replay (`?v=t2/t3/gone`) | §M1 |
| M2 — Branch skill | Requires Human Edit to author `try_dismiss` / `if_present` / `wait_for_one_of` | §M2 |
| Appendix A — Cloud entitlement gates | curl/API work, no browser workflow involved | §Appendix A |
| Appendix B — Production go/no-go | Whole-system checklist, already its own procedure | WF-12 (unchanged) |

Everything else from 01 (WF-1…WF-10) folds into the phases below.

---

## Before you start (15 min, once)

1. Serve the fixtures:
   ```powershell
   python -m http.server 8099 --directory docs\testing\fixtures *> server.log
   ```
2. Accounts ready:
   - the-internet.herokuapp.com: `tomsmith` / `SuperSecretPassword!`
   - saucedemo.com: `standard_user` / `secret_sauce`
   - Juice Shop: register a throwaway account
3. Build Studio open, workspace group created, Clerk auth done. **After any runtime change** run
   `scriptsuild-app-local.ps1` (with `CONXA_STUDIO_HOME` set to the Studio home you test with), then
   **restart Build Studio**: the test sandbox copies the new runtime only at startup, so until the restart its
   `version.json` still shows the old build and the test runs old code.
4. MCP client connected — `list_skills` works.
5. Terminal tailing logs:
   ```powershell
   Get-Content "$env:USERPROFILE\.conxa\logs\recovery.log" -Wait -Tail 20
   ```

---

## PHASE 1 — THE recording (~100 steps, one take, do NOT stop)

Record in exactly this order. Each letter = a segment; the tag in parens is what it proves.
Tabs: you will end up with ~7 tabs — that is intentional.

### A — Control baseline (tab 1: wikipedia.org)
1. Open `https://en.wikipedia.org/wiki/Main_Page`.
2. Click **Random article**.
3. Click the **first link in the article body**.
4. Click browser **Back**.
> Proves: trivial click-through compiles with no raw dynamic IDs (old WF-1 Leg A).

### B — Mutator fixture (tab 2: localhost:8099/mutator.html)
5. Type `Mega Test Run` into the **name** field.
6. Type `mega@test.local` into the **email** field.
7. Click **the drifting button** (whatever its label currently says — it cycles `Go` / `Send` /
   `Do It` / `Proceed` every 0.7 s and jumps to the bottom of the page).
8. Wait for / assert text **"Thanks, Mega Test Run!"**.
> Proves: H-1 DOM mutation between record & replay.

> **A drift-healing test must never target a commit-intent or destructive element.** The
> compiler classifies a click whose intent reads as a commit (`submit`/`confirm`, or any
> `submit_text_tokens` entry — see `policy/default_policy.json`) as *irreversible*, and PROD-3
> deliberately gives an irreversible step Layer 1 and **no re-resolution at any tier** — no a11y
> retry, no agent park. Such a step can only ever fail closed, so it can never demonstrate
> healing. This bit the mutator fixture once already (its button was labelled `Submit`, every
> replay ended in `destructive_recovery_halted`); `conxa-cloud/tests/test_fixture_intent_classification.py`
> now pins the fixture's vocabulary to the policy so it cannot regress silently.


### C — Dynamic identity (tab 3: http://uitestingplayground.com — plain http, its https cert is broken)
9. On the **Dynamic ID** page: click **Button with Dynamic ID**.
10. On the **Text Input** page: type `Conxa`, then click the button whose label changes.
> Proves: stable_hash strips dynamic IDs; changing-text heals at Tier A zero tokens (C.1/C.3).

### D — the-internet marathon (tab 4: the-internet.herokuapp.com)
Late render:
11. `/dynamic_loading/2` → click **Start** → wait → assert/click **Hello World!**.
Scroll:
12. `/infinite_scroll` → scroll until **4 blocks** loaded → click the **last paragraph**.
Hover:
13. `/hovers` → hover **avatar 2** → click **View profile**, come back.
Dialogs:
14. `/javascript_alerts` → click **Alert** → accept.
15. Click **Confirm** → accept.
16. Click **Prompt** → type `Conxa prompt` → accept → verify result shows your text.
Iframe (TinyMCE):
17. `/iframe` → click **Bold** in toolbar → click INTO the editor iframe → type
    `Hello from Conxa` → select all → click **Bold**.
Nested frames:
18. `/nested_frames` → click/act inside **LEFT** frame → then **RIGHT** → then **BOTTOM**.
Windows:
19. `/windows` → click **Click Here** (new tab opens) → assert heading **"New Window"**
    → switch back to original tab → click **Elemental Selenium**.
Login (⭐ security target):
20. `/login` → type `tomsmith` / `SuperSecretPassword!` → click **Login** →
    assert flash **"You logged into a secure area!"**.
Download:
21. `/download` → click **some-file.txt** (or any file) → let it download.
Drag:
22. `/drag_and_drop` → drag **box A onto box B**.
> Proves: WF-1 legs B–F, WF-2 segments C/D/E/G/H/I in one sweep. Iframe markers must appear
> verbatim; dialogs must record accept/type; download becomes `{{downloaded_file}}` binding.

### E — Forms + upload handoff (tab 5: demoqa.com)
23. `/text-box` → fill Full Name, Email, Current Address → **Submit** → assert echo block.
24. `/automation-practice-form` → pick a **Gender** radio, tick **one Hobby** checkbox → Submit.
25. `/upload-download` → click **Select File** → choose the file downloaded in step 21 →
    verify the **filename is echoed** back.
> Proves: varied field types; the download→new-tab upload handoff WITHOUT any `file_path`
> input at replay (compiled `{{downloaded_file}}` must carry it).

### F — Entity-specific cart ⭐ (tab 6: saucedemo.com)
26. Login `standard_user` / `secret_sauce`.
27. Add **Backpack** + **Bike Light** + **Bolt T-Shirt** (3 adds).
28. Open **cart**.
29. Remove **Backpack specifically** (the item, NOT the first row blindly).
30. Assert cart **badge = 2**.
31. Logout.
> Proves: H-5 entity binding / margin gate (the sales-blocking safety test, old B-8/WF-8);
> also gives us the credential-grep target `secret_sauce` (WF-1 Leg B check).

### G — SPA journey (same tab: demo.owasp-juice.shop)
32. Dismiss the welcome banner if present.
33. Search `Apple` → press Enter.
34. Assert results → click the **first result** → confirm it's an Apple product.
35. Open its **Reviews** → expand review modal → click **star 4** → type
    `Mega run review` → **Submit** → read the review back in the list.
> Proves: SPA routing, modal + star-rating widgets, review read-back (WF-4 Leg E).

### H — Chaining source (tab 7: computer-database.gatling.io)
36. Filter `MacBook` → open the result detail → note the introduced date. **Stop recording.**
> Proves: H-6 — this segment's output feeds the cross-skill chaining probe in R6b.

Expect **~95–110 raw events**. If the recorder died mid-take, restart the take from the
segment boundary where it broke (segments are independent enough for that).

---

## PHASE 2 — Compile, inspect, package (20 min)

37. Stop → compile. Check the report:
    - [ ] No raw dynamic IDs survived as sole selectors (esp. segments B/C).
    - [ ] Tab markers at every segment boundary; frame markers for TinyMCE + LEFT/RIGHT/BOTTOM.
    - [ ] `frame_enter`/`frame_exit` steps have `no_recovery_block`.
    - [ ] Step count ≥ 60 and matches event count (no truncation above ~30 steps).
    - [ ] Assertions present after: mutator thanks-text, login flash, badge=2, dialog results,
          filename echo.
38. 🚨 **Credential grep (hard blocker):**
    ```powershell
    Select-String -Path ".\<bundle-folder>\*" -Pattern "SuperSecretPassword","secret_sauce" -SimpleMatch
    ```
    Any hit = STOP, auth-exclusion invariant broken.
39. Determinism check (old WF-3 Leg C): recompile the same session via
    `conxa-cloud/scripts/recompile_session.py <session_id>` and diff fingerprints/selectors
    between the two SkillPackages. Identical = PASS.
40. Build pack → publish → sync → `list_skills` shows it. Install/hosting path exercised.

---

## PHASE 3 — Replay gauntlet (the SAME skill, 8 replays)

Scorecard block per replay (log every one):
```
R#  Result: PASS | PASS-WITH-NOTES | FAIL | CRASH
    Execute: __s   Max tier: __   LLM calls at runtime: __ (must be 0 unless noted)
    Evidence: screenshot / events.jsonl / recovery-log excerpt
```

| # | Do this manually | Expect (maps to old…) |
|---|---|---|
| **R1** | Baseline replay, no overrides | Full success, Tier ≤2, ZERO LLM calls, telemetry `run_success`. Every segment lands in the right tab (EXEC-5 #43); download→upload handoff works with no `file_path` supplied |
| **R2** | Replay with overridden inputs: name `Second Pass`, email `second@test.local`, search `Banana`, filter `ASCI White` | Every value followed — Banana clicked not Apple; ASCI White detail opens. Then zero-result probe: rerun searching `zzzqqqxxx` → LOUD failure at the results step, never clicking whatever's on screen |
| **R3** | Re-run R1 with env `CONXA_ACTION_TIMEOUT_MS=1200` | Slower; log which steps needed recovery and at which tier; still zero LLM. Answers "how much headroom do the defaults have" (B-6, G.1) |
| **R4** | Kill mid-run: close the runtime browser during Segment F (or drop Wi-Fi for 30 s after Segment D) | Clean typed failure NAMING the step. Not success, not hang, no zombie Chromium (`Get-Process chrome,node`) (H-3, G.2, G.3) |
| **R5** | Two `execute_skill` calls back-to-back on this skill | Strictly sequential (host_lock), both succeed, no interleaving (H-7). The lock key is the starting page's host plus every sign-in app's host in the group, so a public-site skill in a group with no sign-in apps also serialises (RT-15, 2026-11-21; before that it ran side by side, seen 2026-11-15 and WF-16 16.3 attempt 1). Only a skill whose manifest has no web starting page at all takes no lock and runs side by side, by design (fail open; no per-skill-name lock) |
| **R6** | This skill + a tiny wiki skill (Random article) SIMULTANEOUSLY; then mid-flight `cancel_execution` with the wiki run_id | Timestamps OVERLAP (parallelism); cancel kills only the wiki run, mega finishes untouched (H-8, G.4, G.5) |
| **R6b** | Chain probe: `execute_sequence` with a second skill consuming Segment H's noted date (H-6) | Output→input chaining works |
| **R7** | Two throwaway micro-recordings: Excalidraw draw/move; reCAPTCHA demo submit | Fast clean refusals at first canvas-only action / CAPTCHA — minimal token burn (H-11/H-12). These cost LLM tokens by design — note them |
| **R8** | Loop the skill ×20 unattended; sabotage run 7 with `CONXA_ACTION_TIMEOUT_MS=400`; don't restart anything | Flat memory trend, log growth stable, download folders swept (W-7). KEY: run 8 behaves like run 1 — failing faster = retry-budget poisoning live (B-4/B-7/H-9, G.6). Sleep/resume the machine mid one run → defined timeout/resume, never eternal hang (G.7) |

**Exit criteria:** R1/R2/R5/R6 pass outright; R2 zero-result probe and R4 fail loudly; R7 clean
refusals; R8 flat memory and run 8 ≈ run 1.

### Post-gauntlet input edge cases (Suite H, 10 min)
Against any packaged skill with a text input, call `execute_skill` with:
empty string · spaces-only · 10,000 chars · `日本語 🎉 café` · `<script>alert(1)</script>'; DROP TABLE users--` · newlines/tabs · `007` / `1e5` / `-0` · missing required input entirely.
Expect: validation errors, literal text only in the target app, nothing coerced/executed
locally, `get_skill_inputs` declaring fields properly beforehand.

---

## M1 — Companion: recovery drill (~30 min, tiny 2-step skill)

Cannot merge — needs elements that break ON PURPOSE per variant.

41. Record against `http://localhost:8099/recovery-fixture.html`: click **Buy Now**, stop,
    compile. Confirm `recovery.json` `selector_context.alternatives` survives mode `t2`
    (`#checkout-btn`). Build pack.
42. Replay matrix — grep recovery.log + server.log after each:

| # | Navigate URL | Runner | Expected |
|---|---|---|---|
| R0 | (clean) | Studio Run Test | Passes, clicked `clean:target`, ZERO recovery events |
| R1 | `?v=t2` | Studio Run Test | Passes via `tier2_a11y`, zero agent events, exactly one click `t2:target` |
| R2 | `?v=t3` | Studio Run Test (ceiling 2) | FAILS with `recovery_ceiling_reached`, zero tokens |
| R3 | `?v=t3` | Claude Desktop | Full T3: `agent_recovery_requested` → candidate_index → `agent_override_applied` → completes; clicked `t3:target`, not a decoy |
| R4 | `?v=gone` | Claude Desktop | Honest decline or `agent_override_rejected` → clean typed failure. A "success" here = ranking regression |
| R5 | `?v=gone` again | Claude Desktop | `retry_budget_exhausted` / `recovery_stagnant_stop` if a guard bites. Failure stays bounded; do not expect it to get cheaper (2026-11-15: 21.3 s every time) |

43. Live self-heal legs (edit page between runs, zero proxy calls expected):
    rename label → heals T2 (D.1); wrap in another div (D.2); change label AND structure AND
    drop testid → clean T3 or clear error (D.3); park browser on wrong URL → self-navigates or
    fast failure (D.4); 5000 ms delayed button → waits, not premature fail (D.5); Juice Shop
    card under different sort order → identity holds (D.6). Tally tiers/tokens across all (D.7).

---

## M2 — Companion: branch-authoring skill (~30 min, ~15 steps)

Cannot merge — the recorder never invents branches; a human authors them in Human Edit.

44. Clear the-internet site data (Entry Ad shows once per profile). Record:
    `/entry_ad` → modal appears → **Close** → `/login` → `tomsmith` /
    `SuperSecretPassword!` → Login → success flash. Do NOT record logout.
45. Human Edit:
    - Confirm optional interstitial on the Close-click → seeded `try_dismiss`
      (or insert one manually if unflagged).
    - Insert `if_present` BEFORE it: probe modal container, body = click Close.
    - After Login click insert `wait_for_one_of` (`required:false`): probe `#flash.success`
      → body clicks Logout; probe `#flash.error` → empty body.
46. Save → compile (flagged step must stay an ordinary required step pre-confirmation) →
    build with `CONXA_REQUIRED_RUNTIME` set explicitly.
47. Replay matrix: R1 cleared-profile (modal hit → dismissed → logout arm) · R2 warm profile
    (absence = silent success) · R3 temporarily wrong password (error arm, no logout, still
    succeeds). In EVERY run: zero tier escalation, zero tokens.
48. Adversarial, quick pass: blank try_dismiss candidates (AV-1) · poisoned if_present body
    (AV-2) · `required:true` on nonexistent selectors → immediate typed failure (AV-3) ·
    pre-branch app layer silently skips bodies (AV-4) · unrecorded popup appears mid-run →
    Escape/pattern dismissal, then fix by hand and republish (AV-5).

---

## Appendix A — Cloud & entitlement gates (no browser workflow possible)

Backend local: `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`. Get a Clerk JWT;
admin token from `CONXA_ADMIN_TOKEN`. Source of truth before/after each:
`curl.exe -s -H "Authorization: Bearer $TOKEN" "$BASE/entitlements/current"`.
Force plan: `POST /entitlements/admin/billing {"workspace_id":"<org_xxx>","plan":"free"}`.

Run the 15-test table in WF-11 unchanged (compile credits, human-edit pool, machines, seats,
workflow cap, trial expiry, distribution ladder, white-label, ops tier, timed grant, analytics
retention, pool routing, BYOK, add-on stacking, kill switches) — Free → Starter → Pro →
Enterprise. Rule of thumb: payment-shaped = 402, capability = 403. Plus Suite J: invalid token
→ clean re-registration prompt; 20 rapid executions → tracking batches match, no loss.

---

## Appendix B — Everything else that was already fine

- **WF-5 Shape B (20 files at once)** and **cross-run consistency**: worth keeping as its own
  recording ONLY if you need bulk-file proof beyond step 21/25's single-file handoff. If yes,
  record the 20× download→upload loop separately (it's mechanical, no thinking required) and
  reuse R8-style replays with two different file sets for the leakage/retention check.
- **WF-12 Production go/no-go** stays as-is — it's a system checklist, not a workflow.

---

## When done

Log results into `02-WORKFLOWS-PASSED.md` (three consecutive passes ⇒ promote to
`runtime/test/gate-skill/` CI fixture), file failures into `TODO.md` using the old WF numbers
from `01-WORKFLOWS-TO-TEST.md` (they're preserved in the tables above), and append findings to
`FIX.md`.
