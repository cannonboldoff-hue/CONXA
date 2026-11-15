# 🎯 Coverage-Bump Runbook — the next three tests, in order

Three tests, cheapest-and-highest-value first, picked to close the specific gaps named in
[`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md)'s dashboard "why not higher" note:

| # | Test | Closes | Why this order |
|---|---|---|---|
| 1 | **WF-7** — recovery-cascade drill (Tier A + Tier B on purpose) | "full multi-tier recovery drill" unproven | Cheapest — local fixture, no live-site flakiness, ~1 hour |
| 2 | **WF-5** — bulk 20-file transfer | "bulk-file identity unproven manually" | Medium — public file hosts, mechanical but slow |
| 3 | **WF-4 Leg A** — heavy SPA CRUD (OrangeHRM) | "SPA sites unproven manually" — the single biggest remaining gap | Hardest to get clean — toasts, route changes, no reload |

Each section below is self-contained: setup, exact steps, what to expect, and where the result
goes. Full background on these three lives in
[`01-WORKFLOWS-TO-TEST.md`](01-WORKFLOWS-TO-TEST.md) (§WF-7, §WF-5, §WF-4) — this doc expands
each into a click-by-click runbook the way
[`03-ONE-WORKFLOW-RUNBOOK.md`](03-ONE-WORKFLOW-RUNBOOK.md) does for the mega-workflow.

---

## Before you start (10 min, once)

1. Serve the local fixtures (needed for Test 1 only):
   ```powershell
   python -m http.server 8099 --directory docs\testing\fixtures *> server.log
   ```
2. Accounts ready:
   - OrangeHRM demo (Test 3): `Admin` / `admin123` — no registration needed, shared demo instance.
   - No login needed for Test 1 (local fixture) or Test 2 (anonymous file hosts).
3. Build Studio open, workspace group created, Clerk auth done.
4. MCP client connected (Claude Desktop) — `list_skills` works. Tests 1's R3–R5 specifically need
   this; Tests 2 and 3 can run entirely through Build Studio's Run Test. If `execute_skill` fails
   immediately with `browserType.launch: Executable doesn't exist`, the dev runtime's `CONXA_DIR`
   (`~/.conxa-dev`) has no Chromium staged — see **TEST-13** in `TODO.md`.
5. Terminal tailing the recovery log (Test 1 only):
   ```powershell
   Get-Content "$env:USERPROFILE\.conxa\logs\recovery.log" -Wait -Tail 20
   ```
6. Have `02-WORKFLOWS-PASSED.md`, `TODO.md`, and `FIX.md` open — you'll be logging into all three
   as you go (see "When done" at the bottom).

---

## TEST 1 — WF-7: force Tier A and Tier B recovery on purpose

**Proves:** the deterministic zero-token recovery tier (Tier A) actually heals real drift, the
Studio recovery ceiling actually stops before spending tokens, and the paid agent tier (Tier B)
ranks honestly and closes through the uniqueness gate instead of guessing.

### Step 1 — Record the base skill
1. With the fixture server running (see above), open Build Studio and start a new recording.
2. Navigate to `http://localhost:8099/recovery-fixture.html` (no `?v=` param — this is the
   "clean" variant).
3. Click **Buy Now**.
4. Stop recording. Compile.

### Step 2 — Verify the compile before touching anything
5. Open the compiled step's files and confirm:
   - `execution.json`'s `identity_bundle.signals` for this step includes a `css-id` (or other
     structural) signal that survives mode `t2` (should be `#checkout-btn`) — this is where the
     fallback chain lives now; `recovery.json`'s `selector_context.alternatives` was removed in a
     2026-09 change (fuzzy-text fallback caused wrong-element clicks) and no longer carries it.
   - `recovery.json`'s step carries readable anchors (`anchors: [{"text": "...", "priority": 1}, ...]`).
6. If either is missing, do not proceed — the compile itself needs fixing first (file as its own
   TODO item, don't continue the drill on a weak compile).
7. Build the pack.

### Step 3 — Fixture replay matrix (R0–R5)
For each row: edit the recording's navigate step to point at
`http://localhost:8099/recovery-fixture.html?v=<variant>` — either in Build Studio's Workflow
Editor (select the `navigate` step, change its URL field) or directly in the workflow JSON
(`data\workflows\<workflow_id>.json`, `type: "navigate"` step's `url` field, then recompile) —
replay, then grep both logs:
```powershell
Get-Content "$env:USERPROFILE\.conxa\logs\recovery.log" -Tail 80        # real runtime
Get-Content "conxa-builder\python\sandbox\.conxa\logs\recovery.log" -Tail 80  # Studio sandbox
```
Also grep `server.log`'s `/__log?` lines to confirm which button was actually clicked.

| # | Variant | Where to run it | What must happen |
|---|---|---|---|
| **R0** | `clean` (no param) | Studio Run Test | Passes, clicked `clean:target`, **zero** recovery events in the log. If anything fired here, the compiled identity is weak — fix that before continuing the drill. |
| **R1** | `?v=t2` | Studio Run Test (ceiling 2) | Passes, exactly one click on `t2:target` — but **not** via a `tier2_a11y` recovery event. `resolver.js` walks all compiled `identity_bundle.signals` durability-ranked *before* the recovery cascade ever engages, and `t2`'s surviving `id="checkout-btn"` is already compiled in as a `css-id` signal — so this resolves at primary resolution with **zero** recovery events, never reaching `cascade.js` at all. See **EXEC-31** in `TODO.md`: under the current architecture no fixture variant that preserves even one compiled signal can exercise Tier 2 a11y recovery specifically. |
| **R2** | `?v=t3` | Studio Run Test (ceiling 2) | **Fails** deterministically with `recovery_ceiling_reached`, no screenshots taken. This is the negative control — Studio must never spend tokens even when it could theoretically recover further. |
| **R3** | `?v=t3` | Claude Desktop `execute_skill` | Tier A fails, then `agent_recovery_requested` → Claude replies a `candidate_index` → `agent_override_applied` → run resumes and completes. Server log must show exactly `t3:target` clicked — never a decoy, even though decoys are present (ranking + uniqueness gate must both hold). |
| **R4** | `?v=gone` | Claude Desktop | Claude either declines honestly or its nomination is rejected (`agent_override_rejected`) → a clean, typed failure naming the step. A "success" here is a ranking/gating regression — file it immediately if you see one. |
| **R5** | `?v=gone` again, same session | Claude Desktop | Budget/stagnation guards should bite: `retry_budget_exhausted` or `recovery_stagnant_stop` in the log. The failure must stay bounded and never get more expensive. It does not get cheaper: 2026-11-15 measured 21.3 s on every repeat. |

**R5 gotcha:** to reach it, call `execute_skill` again **plainly** (no `resume_from`) — repeating the
whole failing step from a fresh navigate is what accumulates recovery rounds toward the stagnation
guard (3 identical-fingerprint rounds trips it: `recordRound`'s `STAGNATION_LIMIT = 2` consecutive
repeats). A `resume_from` with no `step_overrides` entry for that step used to silently skip the
navigate and replay against a blank page (fixed 2026-09-03, **EXEC-32** in `TODO.md`) — it now
refuses cleanly instead, naming the step and keeping the pending recovery park alive for a follow-up
call with the correct override.

**Pass criteria for this phase:** R0/R1 click exactly the right element, R2 fails at zero token
cost, R3 makes exactly one real click via a verified `candidate_index` (never a literal selector
handed straight to the DOM), R4 fails cleanly, R5's guards actually fire.

### Step 4 — Live-site self-heal legs (Suite D)

**Passed in full 2026-09-03 — see `02-WORKFLOWS-PASSED.md` P-3.** One note for next time: D.6's
named live target (`demo.owasp-juice.shop`) has dropped its sort control entirely and also crashes
for a fresh/cookie-less automated session — both site-side, confirmed via direct comparison against
a normal browser tab. Substitute `automationexercise.com/products` (stable, purpose-built for this)
using its search-filter as the position-shuffle mechanism instead of a sort control.

Same base skill (or a fresh local-HTML/S1/S2 recording), edited by hand between runs — no `?v=`
params this time, real DOM edits:

8. **D.1 — rename:** change the button's label from "Submit" to "Confirm", keep its role/testid
   unchanged → replay → the old compiled skill should recover on its own. Note the tier and
   latency. Zero LLM proxy calls expected.
9. **D.2 — move:** wrap the button in a different `<div>` → replay → relational signals are being
   tested live now, not just text.
10. **D.3 — past healing:** change the label AND the structure AND drop the `data-testid` all at
    once → replay → expect either a clean Tier B (agent) escalation or a clear final error —
    never a crash or a hang.
11. **D.4 — wrong page:** park the browser on an unrelated URL before running → the skill should
    either self-navigate to the right page or fail fast and clearly — never hang waiting.
12. **D.5 — slow render:** add a 5000ms `setTimeout` before the button renders → replay → confirms
    retry/wait tuning is sane, not a premature fail.
13. **D.6 — live version of D.1 on a real site:** on Juice Shop (`demo.owasp-juice.shop`), find a
    product card, replay under a different sort order so the card's position shuffles → identity
    should hold (not click whatever's now in that position).
14. **D.7 — cost audit:** tally recoveries, tiers hit, and tokens spent across all seven legs
    above. The claim being tested is "most real-world breakage heals at zero marginal cost" — if
    you're seeing frequent Tier B (agent) hits for what should be cheap drift, that's a signal the
    durability/anchor scoring needs work, not that recovery itself is broken.

**What a full pass proves:** Tier A is a real free self-heal path (not theoretical), the ceiling
contract holds in both directions (Studio never overspends, MCP can still escalate), Tier B ranks
honestly even under decoy competition and always closes through the uniqueness gate ("AI
nominates, runtime verifies" — never "AI clicks"), and failure is bounded (it does not get cheaper on
repeat, and never more expensive).

**Where failures go:** `TODO.md` **EXEC-23** (a genuinely missed interactable), **EXEC-4** (Tier B
handoff shape), or `cascade.js`/`resolution.js` directly — attach the R-number and its log excerpt
to whichever item you file under.

---

## TEST 2 — WF-5: bulk 20-file transfer + cross-run consistency

**Proves:** a skill that moves many files at once keeps each file's identity straight (file #7
uploaded really is file #7 downloaded, never a duplicate or a neighbor), and one run's files never
leak into the next run's.

Decide Shape A vs. Shape B **before** you start recording — they compile to different shapes and
you should not mix them in one take.

### Part A — Shape A: 20 files, one at a time (compiles to 40 steps)
15. Pick a download source: either `github.com` (a public repo folder with ≥20 real files — fixed
    set, won't drift between record and replay) or `the-internet.herokuapp.com/download`
    (drifts — use only if you want to stress that too).
16. Pick the upload target: `demoqa.com/upload-download` (single-file input, fine for this shape).
17. Record: download file #1 → upload file #1 → download file #2 → upload file #2 → … interleaved,
    ×20 total pairs. Stop, compile.
18. Verify the compile: `_bind_downloads_to_uploads` should bind each upload to its own earlier
    download by exact recorded filename.
19. Replay. **Identity check:** confirm file #7 uploaded is genuinely file #7 downloaded — not #3,
    not a duplicate. Walk all 20 pairs, not just a sample.
20. Confirm the 20-iteration loop replays straight from the compiled sequence — watch
    `get_execution_status`/the log for any sign of an LLM being asked to re-decide a click per
    file. Expect **zero** runtime LLM calls across all 20.

### Part B — Shape B: all 20 in one action (compiles to 2 steps)
21. Upload 20 dummy files by hand into a fresh `filebin.net` bin (⚠️ generated dummy files only —
    never real documents, this is a public host).
22. Record: click "Download files" → **one** ZIP comes down (`download_observed` fires once; the
    ZIP is extracted at download time per EXEC-20, already shipped).
23. For the reupload side, use a site with a real `<input type="file" multiple>` — verified
    working: `filebin.net`, `tmpfiles.org`, `blueimp.github.io/jQuery-File-Upload`, `file.io`,
    `demo.automationtesting.in/FileUpload.html`. (Single-file-only hosts like the demoqa upload or
    herokuapp `/upload` **cannot** test this shape — don't use them here.)
24. Stop, compile.
25. **Folder-path mechanism check (W-8):** at replay, pass **one folder path** as the `file_path`
    input (not 20 individual paths) — the runtime should expand it to every file inside, in name
    order, non-recursive. An empty folder should throw a clear error, not hang.
26. Cheap pre-check before scaling to 20: record picking just 3 files at once, then replay against
    a folder containing exactly 3 files. Count what actually arrives at the upload target before
    you commit to the full 20.
27. Also confirm the asymmetric case is a clean typed refusal, zero recovery cascade, zero tokens:
    record with a **single** file, then replay with a **folder** as the input.

### Part C — Cross-run consistency (does yesterday's run leak into today's?)
28. **Run A:** replay the Shape A or Shape B skill against one 20-file set named
    `report-01.pdf` … `report-20.pdf`.
29. Confirm downloads land under `~/.conxa/downloads/{runId}/` — a **fresh folder per execution**,
    never the shared OS Downloads folder.
30. **Run B:** replay the *same compiled skill* against a different 20-file set,
    `invoice-01.pdf` … `invoice-20.pdf`.
31. Every file Run B uploads must be one of its own `invoice-*` files — zero `report-*` leftovers
    from Run A.
32. **Retention check (W-7):** confirm Run A's `{runId}` directory still exists immediately after
    Run B starts (it's inside the retention window), then confirm it's gone once it's older than
    `CONXA_RUN_RETENTION_DAYS` (default 7 days — you can lower this env var temporarily to test
    the sweep without waiting a week).

### Part D — Two break probes to fold in while you're here
33. **Same-name collision (W-3):** deliberately download 5 files where ≥3 share the site's
    suggested filename. Inspect `{runId}/` and count what's actually there. Predicted failure
    mode: a silent overwrite — fewer files present than downloads attempted, while the run still
    reports success. This is the single most dangerous failure class in this test (silent data
    loss) — if you see it, file it as the **highest-severity** new TODO item, don't just note it.
34. **Popup download (W-5):** click a link whose new tab immediately triggers a download — confirm
    the popup's file is actually saved (download listeners must be attached to every opened page,
    not just the initial one).

**PASS:** 20/20 identity-correct uploads in both shapes; zero cross-run leakage; the retention
sweep confirmed; the collision and popup probes either behave as designed or are filed as bugs.

**Where failures go:** `TODO.md` **EXEC-10**, **EXEC-15** (Shape B specifically), a new item for
W-3 if the collision probe reproduces (flag as highest severity), **W-7** follow-ups for anything
retention-related.

---

## TEST 3 — WF-4 Leg A: heavy SPA CRUD (OrangeHRM)

**Proves:** Conxa can drive a real single-page React app through a full create → read → update →
delete cycle, correctly waiting through client-side route changes (no full page reload to key off
of) and catching short-lived UI feedback (auto-dismissing toasts) instead of racing them.

### Step 1 — Record
35. Log into `opensource-demo.orangehrmlive.com` with `Admin` / `admin123`.
36. From the **Dashboard**, navigate: **Admin** → **User Management** → **Users**.
37. Click **Add** (or the equivalent "add user" action). Fill the form — username, password,
    user role, employee name — and **Save**.
38. Search for the newly created user by name/username in the user list.
39. Open that user and **Edit** something about it (e.g. change the user role), save the edit.
40. Delete the user.
41. **Confirm the "deleted" toast/notification appears, then wait for it to auto-dismiss** (it
    disappears on its own after roughly 3 seconds) before ending the recording — don't stop
    recording while the toast is still on screen.
42. Stop recording. Compile.

### Step 2 — What to specifically watch for at compile + replay
43. **Toast timing:** OrangeHRM's confirmation toasts auto-dismiss after ~3s. Check whether the
    compiled assertion actually caught the toast's text before it vanished, or whether the
    assertion step's timing assumes a static element still on screen. If replay intermittently
    fails to see the toast, that's a real finding — file it (see below), don't just re-run until
    it passes.
44. **Route changes without reload:** OrangeHRM navigates Dashboard → Admin → User Management via
    client-side routing — no full page load in between. Check whether any recorded "wait for page
    load" step is mistakenly waiting on a signal (like a `navigate`/load event) that a SPA route
    change never fires. If a step's wait strategy silently degrades to a fixed timeout instead of
    an adaptive one, note which step and how long it waited.
45. **Recorded waits surviving replay:** replay the compiled skill at least twice. Confirm the
    same waits that worked at record time still resolve correctly at replay time — SPA state
    transitions can be timing-sensitive in ways a static-page recording isn't.
46. Grep the compile report the same way `03-ONE-WORKFLOW-RUNBOOK.md`'s Phase 2 does: no raw
    dynamic IDs survived as sole selectors, step count matches event count (no silent truncation),
    assertions present after the toast and after the search.

**PASS:** the full add → search → edit → delete → toast-confirm cycle compiles clean and replays
clean twice in a row, with the toast text actually captured (not raced) and zero LLM calls at
runtime.

**Where failures go:** `TODO.md` **EXEC-10** (entity-binding/SPA concerns route to **PROD-3**);
a toast-timing miss or a route-change wait-strategy miss should each get their own new TODO item
if reproduced — attach the compiled step's `handler_hints`/wait strategy and a recovery-log
excerpt.

---

## When done

Same convention as `03-ONE-WORKFLOW-RUNBOOK.md`:

1. **Log results into [`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md).** Each of these three is
   independent — a clean pass on Test 1 doesn't require Tests 2/3 to also be done. Update the
   dashboard's "why not higher" note and coverage % only for whichever of the three actually
   passed clean (per that file's own rule: bump % only when a new class of site/mechanic gets
   proven, not for every individual run).
2. **File failures into `TODO.md`** under the item named in each test's "Where failures go" line
   above, with `events.jsonl` / `recovery.log` evidence attached — don't file a bare "it broke."
3. **Append a plain-language entry to `FIX.md`** per `CLAUDE.md`'s rules, dated the day you ran it.
4. If a test passes three consecutive times, it's eligible for promotion into
   `runtime/test/gate-skill/` as a CI fixture (Test 1's fixture-based R0–R5 matrix is the easiest
   of the three to make CI-safe, since it needs no live external site).
