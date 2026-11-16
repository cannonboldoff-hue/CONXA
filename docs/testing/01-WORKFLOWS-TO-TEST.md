# ⏳ Workflows To Test

> **Shortcut:** almost everything here (WF-1…WF-10) is consolidated into ONE mega-recording
> with click-by-click manual steps in
> [`03-ONE-WORKFLOW-RUNBOOK.md`](03-ONE-WORKFLOW-RUNBOOK.md). Use this file as the reference
> for what each check proves and where failures go; use the runbook to actually run it.

Everything here is **pending**: no successful manual end-to-end run has been logged yet.
When a workflow passes manually, move its section to
[`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md), add what the pass proves, and bump that
file's dashboard.

Consolidation rule used for this file: **fewer but longer workflows**. Related small tests are
merged into one long recording wherever they share sites or mechanics, so a single record →
compile → replay session covers many checks at once.

---

## How to work

1. Record with Build Studio → compile → replay via Studio Run Test or a real runtime install.
2. Log every result using this block:
   ```
   [WF-x] <segment>
   Result: PASS | DEGRADED | FAIL | CRASH
   rec __s · compile __s · exec __s · LLM calls at runtime: __ (must be 0 unless stated)
   Evidence: screenshot / events.jsonl excerpt / recovery-log excerpt
   ```
   `DEGRADED` = finished but with warnings/retries/fallbacks — these are gold, log them.
3. Failures go to `TODO.md` under the item named in each workflow ("Where failures go").
4. A passing workflow graduates to `02-WORKFLOWS-PASSED.md` after **one clean manual run**;
   promote it into `runtime/test/gate-skill/` as a CI fixture after three consecutive passes.

## Site arsenal (all free)

| # | Site | Login | For |
|---|---|---|---|
| S1 | the-internet.herokuapp.com | `tomsmith` / `SuperSecretPassword!` (on `/login`) | iframes, hovers, drag-drop, alerts, popups, uploads/downloads, dynamic loading, infinite scroll, windows |
| S2 | ui-test-automation-playground.blogspot.com | none | dynamic IDs, changing text, AJAX |
| S3 | demoqa.com | none | forms, date picker, select menu, upload/download, dynamic properties |
| S4 | jqueryui.com | none | autocomplete, datepicker (inside iframes!) |
| S5 | saucedemo.com | `standard_user` / `secret_sauce` | checkout flow, 100-step loops |
| S6 | parabank.parasoft.com | `john` / `demo` | multi-page banking, 100+ steps |
| S7 | computer-database.gatling.io | none | CRUD + pagination loop (100-step generator) |
| S8 | opensource-demo.orangehrmlive.com | `Admin` / `admin123` | heavy React SPA, hover menus, modals |
| S9 | demo.owasp-juice.shop | register free | search, filters, reviews, SPA |
| S10 | datatables.net/examples/data_sources/dom.html | none | identical-row tables (margin-gate test) |
| S11 | en.wikipedia.org | none | stable DOM control site, i18n flip |
| S12 | excalidraw.com | none | canvas app — expected refusal case |
| S13 | google.com/recaptcha/api2/demo | none | CAPTCHA boundary test |
| S14 | automationexercise.com | register free | e-commerce signup + long cart chain |
| S15 | filebin.net | none | anonymous bins: multi-upload, ZIP download |
| S16 | github.com public repo folder | none | ≥20 real downloadable files |

Local fixtures live in [`fixtures/`](fixtures/): `mutator.html` (DOM mutates every 0.7 s),
`recovery-fixture.html` (tier-targeted element drift), `dismiss-fixture.html` (overlay ladder).
Serve over HTTP (`python -m http.server 8099 --directory docs\testing\fixtures *> server.log`),
never `file://`.

> **A drift-healing test must never target a commit-intent or destructive element.** The
> compiler classifies a click whose intent reads as a commit (`submit`/`confirm`, or any
> `submit_text_tokens` entry — see `policy/default_policy.json`) as *irreversible*, and PROD-3
> deliberately gives an irreversible step Layer 1 and **no re-resolution at any tier** — no a11y
> retry, no agent park. Such a step can only ever fail closed, so it can never demonstrate
> healing. This bit the mutator fixture once already (its button was labelled `Submit`, every
> replay ended in `destructive_recovery_halted`); `conxa-cloud/tests/test_fixture_intent_classification.py`
> now pins the fixture's vocabulary to the policy so it cannot regress silently.

---

## WF-1 — Starter marathon: navigation, login, forms, tabs, single-file transfer, alerts

**Sites:** S11 (en.wikipedia.org) + S1 (the-internet.herokuapp.com) + S3 (demoqa.com) ·
**Tabs:** up to 3

**Re-test under the strict end-to-end rule (2026-11-16 campaign,
[`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md)):** a leg counts only once it reaches the
approved, publish-ready state through Studio's normal controls with independent proof of the
outcome. Each leg that passes is removed from here and recorded in
[`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md) (Leg C, the S3 form fill, is there as P-11;
Leg A, the Wikipedia control baseline, as P-12; Leg D, the new-tab round trip, as P-13; Leg E, the
download → upload handoff, as P-14; Leg F, native JS dialogs, as P-15). Still to pass:

- Leg B — S1 login + credential grep. The recorder drops sign-in pages by design, so the login is
  done through the group's saved sign-in, not recorded.

Earlier history: legs A, B, D and E passed the `03-ONE-WORKFLOW-RUNBOOK.md` follow-up run on
2026-09-03, and Leg F passed on 2026-09-04 (P-5). Both runs predate the strict rule above.

**Where failures go (regressions only):** `TODO.md` EXEC-34 (remaining unbounded `.evaluate()`
sites, unrelated follow-up); EXEC-5 (#43 tab landing, #31/#32 download/upload verification).

---

## WF-2 — Rich-element gauntlet: iframes, dynamic identity, widgets, scroll, hover, drag

**Sites:** S1 (the-internet.herokuapp.com) + S4 (jqueryui.com) + S3 (demoqa.com) +
S2 (ui-test-automation-playground.blogspot.com) · **Tabs:** 1

Segments A (dynamic IDs, C.1), C (late-rendered elements, C.5), D (iframe chain preservation,
E.1), and G (infinite scroll, E.7) all **passed** via the `03-ONE-WORKFLOW-RUNBOOK.md` follow-up
run, 2026-09-03 — see [`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md). Segment B (both halves —
changing-text C.3 via the runbook, and moved-element C.4 via a standalone fixture run) **passed**
2026-09-03 — see **P-4** in [`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md). Everything below is
still open.

### Segment E (remainder) — Nested iframes bonus: datepicker (E.2) ✅ passed 2026-09-04
Moved to [`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md) — see **P-6**.

### Segment F — Autocomplete/typeahead race (E.6) ✅ passed 2026-09-04
Moved to [`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md) — see **P-7**.

### Segment H — Hover menus (E.8) 🧪 beta — needs human review
S1 `/hovers`: hover avatar 2 → "View profile". Did **not** replay reliably in the runbook follow-up
run despite real recorder/runtime support for hover reveals — see `TODO.md` **EXEC-29a** (split
out 2026-09-04 from the native-JS-dialog half of this same investigation, which turned out to be
an unrelated record-ordering/replay-deadlock bug, now resolved — EXEC-29). Downgraded to beta
(too flaky for unattended replay, requires a human to watch/confirm each run) until an isolated
repro with `events.jsonl`/`recovery.log` evidence lands a real fix.

### Segment I — Drag and drop (E.9) — confirmed hard limitation
S1 `/drag_and_drop`: box A onto box B; S3 `/sortable`: item 5 to position 1. Reconfirmed as a hard
limitation in the 2026-09-03 follow-up run (skipped with a warning at build time, unchanged since
`FIX.md` 2026-08-26) — not a new gap, no further action needed here.

### Segment J — Shadow DOM (C.6) ✅ passed 2026-09-04
Moved to [`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md) — see **P-8**.

**Where failures go:** EXEC-29 (hover replay); EXEC-5 (#43, already proven — reopen only on
regression); iframe pipeline issues → TRD "Iframe Pipeline" owners; selector durability → compiler
items in `TODO.md`.

---

## WF-3 — Scale stress: 50-step warm-up → 100+ step flagship → banking marathon

**Sites:** S7 (computer-database.gatling.io) + S5 (saucedemo.com) + S6 (parabank.parasoft.com) ·
**Tabs:** 1 · The headline showcase.

### Leg A — 50-step warm-up
S7: ONE take ~50 actions — search "Apple" → open result → back → filter "IBM" → open → back →
add 5 computers (≈8 actions each) → delete 2. Note recorder responsiveness/dropped events;
compile wall time + LLM call count; execute; count first-try successes. PASS ≥95% clean, no silent
degradation warnings.

> **Adjacent evidence (not a pass of this leg):** a separate 104-step, 6-tab, 6-host mega-workflow
> (`mega-workflow-fc8031f2`) replayed clean with zero recovery events on 2026-09-04 — see **P-9** in
> `02-WORKFLOWS-PASSED.md`. It proves scale + multi-host reliability but doesn't run saucedemo's
> specific 6×-loop-then-checkout script below, so Leg B stays open.

### Leg B — 100+ step flagship ⭐
S5: ONE continuous take of this loop **6× without pausing** (≈120 actions): sort Z→A → add 2
products → cart → remove 1 → continue shopping. After loop 6: full checkout → First/Last/Zip →
Finish → "Thank you" assertion → logout. Inspect review: loops deduplicated? intents sane at
length? Package + execute, time it, then **do the task manually once with a stopwatch** — that's
the comparison number. Fill the metrics table (events/steps, compile time/calls, exec time,
first-try rate, manual time, Human Edit needed?).

### Leg C — Compile determinism at scale
Re-compile Leg B's session via `conxa-cloud/scripts/recompile_session.py <session_id>` and diff
fingerprints/selectors between the two SkillPackages. Identical = PASS; differing selectors =
IdentityBundle/grammar nondeterminism.

### Leg D — Banking marathon (100+ steps, many page types)
S6 (`john`/`demo`): login → Accounts Overview → Transfer Funds ×3 (verify each) → Pay Bills ×2 →
Find Transactions (date range, then amount) → Open New Account → logout. Naturally 100–140 actions
across many page types — unlike Leg B's repetitive loop.

**The pitch being validated:** agent does it live = tokens every run + drift; human ≈20 min/run;
compiled skill replays forever after one compile.
**Where failures go:** EXEC-10 (long-chain); determinism → IdentityBundle items.

---

## WF-4 — Real-app journeys: SPA CRUD, money checkout, multi-site relay, i18n, reviews

**Sites:** S8 (opensource-demo.orangehrmlive.com) + S5 (saucedemo.com) +
S14 (automationexercise.com) + S11 (en.wikipedia.org) + S9 (demo.owasp-juice.shop)

### Leg A — Heavy SPA CRUD
S8 OrangeHRM: Dashboard → Admin → User Management → Add user (fill form, save) → search new user →
edit → delete → confirm toast disappears. Toasts auto-dismiss ~3 s — do assertions catch them?
Watch SPA route changes without reload; recorded waits surviving.

### Leg B — E-commerce checkout money-demo ⭐
S5 full journey: login → price low→high filter → add 3 cheapest → cart → info form → verify total
math → Finish → "THANK YOU FOR YOUR ORDER" assertion → logout. Execute 3× consecutively (also
feeds WF-10 fatigue test).

### Leg C — Multi-site relay
One workflow, two domains: S14 search "tshirt", note price → navigate to S11 → search that term →
assert loaded. Tests auth/state handling across navigations.

### Leg D — i18n flip
Record on English Wikipedia: search "Automation" → first result → assert heading. Execute SAME
skill against `de.wikipedia.org`. Directly tests text-signal weight in identity — note which
non-textual signals saved it (or didn't).

### Leg E — Reviews / star ratings / dynamic widgets
S9 Juice Shop: leave a product review (modal + star click + textarea + submit), read it back in
the list.

**PASS:** all legs clean; checkout correct 3/3; i18n flip resolves by non-text signal or fails
loudly (never wrong-click).
**Where failures go:** EXEC-10; entity-binding concerns → PROD-3.

---

## WF-5 — Files at scale: bulk 20-file transfer + cross-run consistency

**Sites:** S15 (filebin.net) / S16 (github.com public repo folder) / S1 (the-internet.herokuapp.com)
/ S3 (demoqa.com) · **Steps:** ~25 per run × multiple runs · **Tabs:** 2

Two mechanics — decide which you're testing before recording:

| | Shape A — 20 files one at a time | Shape B — 20 files in ONE action |
|---|---|---|
| Compiled shape | 40 steps, each bound to its own filename | 2 steps |
| Supported? | Yes | Yes — zip extraction at download time (EXEC-20); upload takes a folder path (W-8) |

### Part A — Shape A unrolled loop ✅ passed (at n=5, not the n=20 below)
Download source: S16 GitHub folder raw links (fixed set) or S1 `/download` (drifts).
Upload target: `demoqa.com/upload-download` (single-file input is fine here).
Record download #N → upload #N interleaved ×20. `_bind_downloads_to_uploads` binds each upload to
its own earlier download by exact recorded filename — **identity check: file #7 uploaded must be
file #7 downloaded**, not #3 or a duplicate. Also confirm the 20-iteration loop replays from the
compiled sequence, not LLM re-deciding clicks per file (zero runtime LLM calls).

> **Passed 2026-09-04 — see P-10 in `02-WORKFLOWS-PASSED.md`.** Run used 5 interleaved pairs
> (`github.com/github/gitignore` → `demoqa.com/upload-download`), not the 20 specced above —
> logged as closing this leg by team decision. `_bind_downloads_to_uploads` bound each upload to
> a distinct incrementing placeholder in recorded order (no duplicate/cross binding), replay was
> `run_success` with zero recovery events and zero LLM calls, and the per-run download folder
> byte-matched the original recorded files. Parts B, C, and D below are still open.

### Part B — Shape B genuinely 20 at once
Upload 20 dummy files to a filebin bin by hand → "Download files" → one ZIP (one
`download_observed`; zip extracted at download time since EXEC-20). Reupload side needs an
`<input type="file" multiple>` — verified: `filebin.net`, `tmpfiles.org`,
`blueimp.github.io/jQuery-File-Upload`, `file.io`, `demo.automationtesting.in/FileUpload.html`.
Single-file-only (cannot test Shape B): demoqa upload, herokuapp `/upload`.
**Folder-path mechanism (W-8):** pass ONE folder path as `file_path`; runtime expands to files in
name order (non-recursive; empty folder throws). Cheap pre-check before scaling: record picking 3
files at once, replay with a folder of 3 — count arrivals. Recording with one file and replaying
with a folder of 20 is legitimate and worth testing. Single-file control + folder → immediate
typed refusal, no recovery cascade, zero tokens.
⚠️ Public file hosts — generated dummy files only, never real documents.

> **Partial — download/ZIP leg only, 2026-09-05.** Workflow `WF-5-S-A`
> (`63a419a0-a526-4aa7-960c-84d9ccb78828`, session `e58b63a5-17ef-46d8-8185-69c470de79b0`)
> uploaded 8 files to a fresh filebin bin, clicked "Download files" once, and the ZIP extracted
> to all 8 files — the download/`download_observed`/zip-extraction mechanic checks out. Compiled
> and test-replayed, `last_test_status: passed`, but **not a Part B pass**: the recorded reupload
> leg only opened `storage.filebin.net` presigned view/download links, never an actual
> `<input type="file" multiple>` upload target, and the test replay's `file_path` input was a
> single external file, not a folder — so the W-8 folder-expansion mechanism was never exercised.
> Compile also came back `review_needed`. **Still open:** reupload to a real multi-file input,
> the 3-file folder pre-check, the 5-file full-scale folder replay (using 5 in place of the 20
> specced above, matching the Part A team decision), and the single-file-control-vs-folder
> refusal check.

### Part C — Cross-run consistency (does yesterday's run leak into today's?)
Run A: replay the skill against one 20-file set (`report-01…20.pdf`). Confirm downloads land under
`~/.conxa/downloads/{runId}/` — a **fresh folder per execution**, not shared OS Downloads.
Run B: replay the SAME compiled skill against a different 20-file set (`invoice-01…20.pdf`).
Every Run-B upload must be one of ITS OWN files — no `report-*` leftovers. Retention check (W-7):
Run A's `{runId}` dir still exists immediately after Run B starts (inside retention window), gone
once older than `CONXA_RUN_RETENTION_DAYS` (default 7).

### Part D — Break probes folded in
- **Same-name collision (old B-3, W-3):** download 5 files, ≥3 sharing suggested filenames →
  inspect `{runId}/` and count. Predicted: silent overwrite, fewer files than downloads, run still
  reports success — the most dangerous failure class here (silent data loss).
- **Popup download (old B-5, W-5):** click a link whose new tab immediately downloads — the popup's
  file must now be saved (listeners attach to every opened page).

**PASS:** 20/20 identity-correct uploads both shapes; no cross-run leakage; retention sweep
confirmed; collision/popup probes behave as designed (or get filed as the W-3 bug).
**Where failures go:** EXEC-10, EXEC-15 (Shape B), W-3 → new TODO item (highest severity),
retention → W-7 follow-ups.

---

## WF-6 — Conditional branch steps in one workflow (EXEC-1)

**Site:** S1 (the-internet.herokuapp.com) only (`/entry_ad` + `/login`) · **Steps:** ~15–20 ·
**Tabs:** 1

Tests steps that must **succeed whether or not something appears** — popups, expired-session
dialogs, A/B outcomes. All three primitives (`try_dismiss`, `if_present`, `wait_for_one_of`) in
one compiled skill. Background: the recorder never invents branches; it only flags dialog-shaped
clicks (`bridge.js::detectOptionalContainer` → advisory hint). Only human confirmation in Human
Edit converts it into a real branch (`workflow_mutations.confirm_optional_interstitial`) — the
compiler staying hands-off is itself part of the test.

### Stage 1 — Record (~8–10 steps)
Clear S1 site data (Entry Ad shows once per profile). Go `/entry_ad` → modal appears → Close →
navigate `/login` → fill `tomsmith` / `SuperSecretPassword!` → Login → success flash. Stop.
**Do not record Logout.**

### Stage 2 — Human Edit (where branches are born)
1. Find the Close-click step → **confirm optional interstitial** → becomes `try_dismiss` seeded
   with recorded selector + container signal. Fallback if unflagged: insert `try_dismiss`
   manually.
2. Insert `if_present` BEFORE the try_dismiss: probe = modal container; body = click Close. One
   run then exercises if_present's positive path AND try_dismiss's negative path.
3. After the Login click insert `wait_for_one_of` (`required:false`): option 1 probe
   `#flash.success` → body clicks Logout; option 2 probe `#flash.error` → empty body.
4. Save, compile. Verify compile left the flagged step an ordinary required step pre-confirmation.

### Stage 3 — Pack build
Set `CONXA_REQUIRED_RUNTIME` explicitly — the branch executor may not be in the tagged manifest
floor yet; an older app layer silently no-ops unknown step types (for a branch = skips its whole
body while reporting success).

### Stage 4 — Replay matrix
| Run | Setup | Expected |
|---|---|---|
| R1 | Clear site data, replay | Modal appears → if_present hits → Close → try_dismiss finds nothing, silent → login → success arm → Logout. PASS |
| R2 | Same profile, no clearing | Modal absent → branches skip silently, faster run, success arm again. PASS — absence is success |
| R3 | Wrong password value temporarily | Error flash → option 2 arm → Logout NOT clicked → run succeeds. Revert after |

In EVERY run check recovery log/tier counters: branch probes must never escalate the cascade and
never burn LLM tokens (branch bodies are best-effort; handlers never throw). Any branch-attributable
escalation = regression against the zero-token invariant.

Bonus leg (optional): S1 `/notification_message_rendered` — random success/error per click; insert
`wait_for_one_of` probing `#flash.success` vs `#flash.error` (`required:false`), replay 3–4×,
different arms fire, run never fails either way. Closest cheap stand-in for real-world A/B.

### Adversarial variants
- **AV-1 unmatchable candidates:** blank try_dismiss candidates → still passes via Escape fallback, zero escalation.
- **AV-2 poisoned nested body:** bogus selector in if_present body → run still passes (failing body ≠ failing run; confirm live; `test_branch.js` covers offline).
- **AV-3 required starvation:** `wait_for_one_of required:true` pointed at nonexistent selectors → clean immediate typed failure, not a cascade.
- **AV-4 old-runtime silent skip:** replay pack built WITHOUT `CONXA_REQUIRED_RUNTIME` against a pre-branch app layer → predicted: bodies skipped, success reported, nothing dismissed. If a CURRENT app layer also skips → regression in run.js/handlers.js dispatch.
- **AV-5 popup that was NEVER recorded:** record minimal variant with zero branch steps (skip `/entry_ad`), replay once on a cleared profile so the modal appears mid-run. Runtime has NO proactive overlay sweep — recovery fires only when a click throws INTERCEPTED. Tier-1 ladder: Escape → known consent-pattern list (`dismiss_patterns.js`) → host-learned winners (`learned_overlays.json`). Outcomes: ignored (luck) / Escape (T1 free) / pattern match (T1 free, `tier1_dismiss_pattern`, learned store skips it next time) / bespoke modal → T3+ paid or ceiling failure. Then apply the fix by hand (insert `try_dismiss` first step), republish, replay both futures → silent passes.

> **Note:** AV-5a's fixture-based walkthrough was validated programmatically on real headless
> Chromium 2026-08-25 (see P-2 in `02-WORKFLOWS-PASSED.md`) using
> [`fixtures/dismiss-fixture.html`](fixtures/dismiss-fixture.html). What remains manual here:
> driving it through Build Studio record → pack → replay, including R2→R3 learning behavior in a
> real Studio workflow.

**What this proves on pass:** the full EXEC-1 loop (observe → compiler hands-off → human authors →
runtime executes best-effort); stochastic popups cost zero tiers and zero tokens present OR absent;
deterministic and truly random A/B both drivable via wait_for_one_of; old-runtime hazard justifies
the manifest floor.
**Where failures go:** reopen `TODO.md` EXEC-1 with dated update; probe failures → EXEC-5 #31/#32;
AV-5 tier surprises → recovery cascade regression.

---

## WF-7 — Recovery-cascade drill: force Tier A and Tier B on purpose (+ live self-heal suite)

**Passed in full 2026-09-03** — fixture replay matrix (R0–R5) and the live-site self-heal suite
(D.1–D.7) both moved to `02-WORKFLOWS-PASSED.md` **P-3**. D.6 needed a substitute mechanism
(the spec's sort-control shuffle no longer exists on the named live site) — see P-3 for detail.

**Sites:** local [`fixtures/recovery-fixture.html`](fixtures/recovery-fixture.html) + live-site legs ·
**Steps:** ~6 per replay × 6 + live legs

Workflows above hope recovery never fires; this makes it fire deterministically. Tier recap
(`docs/TRD.md` §10.1): **Tier A** mechanical fix from the Playwright error (re-find/scroll/dismiss/wait)
plus a11y role+name and dialog-scope — zero tokens, never a guessed different element. **Tier B**
armed agent round: ranked indexed list of live interactive elements + screenshots + intent, Claude
replies a candidate_index verified against the uniqueness gate — paid. Studio ceiling 2 stops at A;
MCP ceiling 4 allows B (up to two armed rounds). Log lines still use the old numbers (`tier2_a11y`,
`agent_recovery_requested {tier:3}`) — that is the wire format, not the product names.

### Setup
Serve `docs/testing/fixtures` on :8099. Record minimal skill against
`http://localhost:8099/recovery-fixture.html` (no param = clean): click "Buy Now", stop, compile.
Before mutating anything confirm in `recovery.json`: `selector_context.alternatives` includes
something surviving mode `t2` (here `#checkout-btn`), and the step carries readable anchors.
Build the pack.

### Fixture replays (edit navigate URL to add `?v=…`)
Grep after each run:
```powershell
Get-Content "$env:USERPROFILE\.conxa\logs\recovery.log" -Tail 80        # real runtime
Get-Content "conxa-builder\python\sandbox\.conxa\logs\recovery.log" -Tail 80  # Studio sandbox
```
Events: `layer_recovered layer:2 tier2_a11y`, `repair_event`, `agent_recovery_requested {tier:3}`,
`agent_override_applied/rejected`, `recovery_ceiling_reached`, `retry_budget_exhausted`,
`recovery_stagnant_stop`. Also grep `server.log` `/__log?` lines to confirm the RIGHT button was clicked.

| # | Variant | Where | Expected |
|---|---|---|---|
| R0 | `clean` | Studio Run Test | Passes, clicked `clean:target`, ZERO recovery events. Anything else = weak compiled identity; fix before continuing |
| R1 | `?v=t2` | Studio Run Test (ceiling 2) | Passes via Tier A (`tier2_a11y`/`layer_recovered`), zero agent events, exactly one click `t2:target`. Free-self-heal proof |
| R2 | `?v=t3` | Studio Run Test (ceiling 2) | FAILS deterministically with `recovery_ceiling_reached`, no screenshots. Negative control: sandbox never spends tokens |
| R3 | `?v=t3` | Claude Desktop `execute_skill` | Full Tier B loop: fail Tier A → `agent_recovery_requested` → Claude replies candidate_index → `agent_override_applied` → resumes and completes. Server log: exactly `t3:target`, NOT a decoy (ranking + uniqueness gate) |
| R4 | `?v=gone` | Claude Desktop | Claude declines honestly or nomination rejected (`agent_override_rejected`) → clean typed failure naming the step. A "success" here = ranking/gating regression |
| R5 | `?v=gone` again, same session | Claude Desktop | Budget/stagnation guards bite: `retry_budget_exhausted`, `recovery_stagnant_stop`. Failure stays bounded. Do not expect a cheaper repeat: the 2026-11-15 run took the same 21.3 s on each of six failures (05 report) |

Pass criteria: R0–R1 exact clicks, R2 fails zero-token, R3 one real click + candidate_index (not a
literal selector), R4 fails cleanly, R5 guards fire. Deviations map to: ceiling wiring /
`candidate_digest.js` ranking / `validateOverrideSelector` margin / `failure_response.js` contract.

### Live-site self-heal legs (folded-in Suite D)
Against a local HTML page or S1/S2 with manual edits between runs:
- **D.1 rename:** change button label "Submit"→"Confirm", keep role/testid → old skill recovers.
  Measure tier + latency; ZERO proxy calls expected.
- **D.2 move:** wrap button in a different `<div>` → relational signals tested live.
- **D.3 past healing:** change label AND structure AND drop testid → clean T3 escalation or clear
  final error, never crash/hang.
- **D.4 wrong page:** browser parked on unrelated URL → navigates itself or fast clear failure.
- **D.5 slow render:** 5000 ms setTimeout before the button → retry/wait tuning vs premature fail.
- **D.6 live version of D.1:** S9 product card under a different sort order — positions shuffle,
  identity should hold.
- **D.7 cost audit:** tally recoveries/tiers/tokens across all legs. Claim = most breakage heals at
  zero marginal cost; frequent T3 hits = durability scoring needs work.

**What this proves on pass:** T2 is a real free self-heal path; the ceiling contract holds both
ways; T3 ranks honestly under decoy competition and closes through the uniqueness gate ("AI
nominates, runtime verifies"); failure is bounded (it does not get cheaper on repeat).
**Where failures go:** EXEC-23 (missed interactable), EXEC-4 (T3 handoff shape), cascade modules
(`cascade.js`/`resolution.js`) with R1 logs attached.

---

## WF-8 — Remaining break tests: budget poisoning, timeouts, endurance, wrong-row safety

**Sites:** S3 (demoqa.com, B-4) + WF-1/WF-3's known-good skill (B-6/B-7) +
S14 (automationexercise.com, B-8)

(B-1, B-2, B-3, B-5 are folded into WF-1/WF-5 as post-fix confirmations.)

### B-4 — Poison the retry budget (failure makes the next run worse)
Any site; `demoqa.com/dynamic-properties` convenient. Record a workflow targeting an element you
can MAKE fail (hard throttle, or delay > 2500 ms action timeout). Run → expect failure. **Do not
restart the MCP client.** Run twice more in the same session. Predicted (W-4): runs 2–3 fail
earlier with less recovery (`retry_budget_exhausted` near-immediately); restart the client and it
behaves like run 1 again → diagnosis confirmed. Commercially: "it broke once and now keeps breaking,
worse" — the pilot-ending failure mode. NOTE: budget clearing was since added on the success path —
this test checks the FAILURE path specifically.

### B-6 — Slow-site timeout stress
Replay WF-1's known-good login leg (or WF-3's chain) under heavy throttling (Slow 3G) or with
`CONXA_ACTION_TIMEOUT_MS` lowered to ~1200. Note which step types fail first and at what tier
recovery gives up. Purpose: how much headroom do the 2500/8000 defaults really have, and are the
env overrides enough answer for a customer whose internal app is simply slow?

### B-7 — Long-run endurance (the overnight one)
Same known-good workflow ×20 consecutive runs, runtime NOT restarted. Watch: process memory growth;
browser-context cache; retry-budget map accumulation; recovery-log growth toward 10 MB rotation;
download-folder pileup (W-7 sweep should cap it). Run 20 must behave like run 1.

### B-8 — Wrong-data-on-the-right-button (the safety test) ⭐ PROD-3
`automationexercise.com` cart or any 5+ row list with identical action buttons. Record deleting
**the third item specifically**. Replay after changing the list (reorder/remove/add) so row 3 now
holds different data. Predicted risk: positional resolution removes the wrong record. The margin
gate + IdentityBundle should prevent a confident wrong pick — this is how we find out. **If it
removes the wrong row: not a quiet bug — it is THE blocker for finance/HR/payroll sales.**

**Where failures go:** B-4 → new TODO item (budget not cleared on failure); B-6 → EXEC-10 (timeout
guidance); B-7 → new TODO per symptom; B-8 → PROD-3 directly.

---

## WF-9 — EXEC-11 mega-workflow: one recording, one replay gauntlet (R1–R8)

**Sites:** local [`fixtures/mutator.html`](fixtures/mutator.html) + S1 (the-internet.herokuapp.com)
+ S4 (jqueryui.com, datepicker) + S5 (saucedemo.com) + S9 (demo.owasp-juice.shop) +
S7 (computer-database.gatling.io) · R6/R7 also touch S11 (en.wikipedia.org) + S12 (excalidraw.com)
+ S13 (google.com/recaptcha/api2/demo) · **Tabs:** 4

~60 steps, 6 domains, 4 tabs in ONE take, then eight replays of the SAME skill turning it into
every hard-mode test. Log under `TODO.md` TEST-12 (governance gaps → PROD-18).
Manual time: ~45 min setup + ~15 min recording + ~30 min gauntlet + optional overnight.

> **Adjacent evidence (not a pass of this workflow):** a separate 104-step, 6-tab, 6-host
> mega-workflow (`mega-workflow-fc8031f2`) replayed clean with zero recovery events on 2026-09-04 —
> see **P-9** in `02-WORKFLOWS-PASSED.md`. It's larger than this spec's ~60-step target and proves
> the same scale/multi-tab shape, but its sites (Wikipedia, demoqa, the-internet, uitestingplayground,
> saucedemo, local fixture) don't match this workflow's mutator/jQuery-UI/Juice-Shop/computer-database
> mix or its R1–R8 replay gauntlet, so WF-9 stays open.

| Tag | Meaning | Exercised by |
|---|---|---|
| H-1 | DOM mutates between record & replay | Segment A, R1 |
| H-2 | Late-render/scroll-load + timeout headroom | Segment B, R3 |
| H-3 | Mid-run session death fails honestly | R4 |
| H-4 | Replay inputs differ from recorded data | Segment E, R2 |
| H-5 | Entity binding — right row, always (PROD-3) | Segment D, R2 probe |
| H-6 | Cross-skill output→input chaining via `execute_sequence` | Segment F, R2 |
| H-7/H-8 | Same-platform serialization / cross-platform parallelism | R5, R6 |
| H-9 | Endurance + retry-budget poisoning over 20 runs | R8 |
| H-10 | Iframe chain under rapid alternation | Segment C |
| H-11/H-12 | Canvas + CAPTCHA refuse cleanly | R7 |

### PHASE 0 — Setup
Fixture: serve [`fixtures/mutator.html`](fixtures/mutator.html) on :8099 (same server as every
other fixture — `python -m http.server 8099 --directory docs\testing\fixtures`). Its button
visibly jumps to the bottom of the page and relabels every ~0.7 s, cycling `Go` / `Send` /
`Do It` / `Proceed`. Accounts: saucedemo `standard_user/secret_sauce`;
Juice Shop throwaway; computer-database none. Studio running, group `MEGA-11` created + auth done;
terminal tailing sessions; MCP client connected (`list_skills` works).

### PHASE 1 — The single recording (in exactly this order, no stopping)
- **A — Mutator (tab 1, ~6 steps, H-1):** type `Mega Test Run` name, `mega@test.local` email,
  click the drifting button (whatever its label says now — `Go`/`Send`/`Do It`/`Proceed`), wait
  for "Thanks, Mega Test Run!". None of those labels reads as a commit, which is what keeps the
  step re-resolvable — see the note in Phase 0's fixture list.
- **B — Scroll + late-render (new tab 2, ~8 steps, H-2):** `/infinite_scroll` scroll to 4 blocks +
  assert; `/dynamic_loading/2` Start → wait spinner → click "Hello World!".
- **C — Iframe ping-pong (same tab, ~10 steps, H-10):** `/nested_frames` act LEFT → RIGHT →
  BOTTOM; `/datepicker/` open widget (one iframe deep), pick a specific day.
- **D — Entity-specific cart (new tab 3, ~14 steps, H-5):** saucedemo login → add Backpack +
  Bike Light + Bolt T-Shirt → open cart → remove **Backpack specifically** (not first row) →
  assert badge `2`.
- **E — Typed search (new tab 4, ~6 steps, H-4):** Juice Shop → dismiss banner → search `Apple` →
  Enter → assert results → click first, confirm Apple product.
- **F — Chaining source (same tab, ~10 steps, H-6):** computer-database → filter `MacBook` → open
  detail → note introduced date. **Stop.** Expect 55–65 raw events.

### PHASE 2 — Compile & install
Verify: no raw dynamic ids survived as sole selectors (esp. Segment A); tab markers at segment
boundaries; frame markers for left/right/bottom + datepicker; step count ≥50 (compare event count —
no truncation). Build → publish → sync → `list_skills` shows mega-11. **Grep bundle for
`secret_sauce` — found = STOP, critical bug.**

### PHASE 3 — Replay gauntlet (all manual, in order)
| R | Do | Expect |
|---|---|---|
| R1 | Baseline, no overrides | Full success; telemetry `run_success`; Tier ≤2; ZERO LLM calls |
| R2 | Override inputs: `Second Pass`/`second@test.local`/search `Banana`/filter `ASCI White` | EVERY value followed; Banana clicked not Apple; ASCI White detail. Zero-result probe: search `zzzqqqxxx` → LOUD failure at results step, never clicking whatever's on screen |
| R3 | Re-run R1 with `CONXA_ACTION_TIMEOUT_MS=1200` (shrink headroom artificially) | Slower; note which steps needed recovery, which tier; still zero LLM calls |
| R4 | Kill mid-run: close runtime browser window mid-Segment-D or block network after Segment C | Clean failure NAMING the step. Not success, not hang, not healing into a login form |
| R5 | TWO mega-11 `execute_skill` calls back-to-back | Strictly sequential (H-7 serialization via host_lock); both succeed, badge `2` twice, no interleaving |
| R6 | Mega-11 + tiny wiki skill (open → Random article → assert body) SIMULTANEOUSLY | Timestamp OVERLAP (true parallelism). Mid-flight `cancel_execution` with wiki run_id: wiki dies, mega finishes untouched |
| R7 | Two tiny throwaway recordings: Excalidraw draw/move/delete; reCAPTCHA demo form submit | Fast clean refusals at first canvas-only action / CAPTCHA, minimal token burn |
| R8 | Loop mega-11 ×20 unattended; sabotage run 7 with `CONXA_ACTION_TIMEOUT_MS=400` | Flat memory trend, stable log growth, leftover-run cleanup. THE key check: run 8 behaves like run 1 (full budget available) — if it fails faster with less recovery, retry-budget poisoning is live; capture both logs |

Scorecard per replay:
```
R#  Result: PASS | PASS-WITH-NOTES | FAIL | CRASH
    Execute: __s   Max recovery tier: __   LLM calls at runtime: __ (must be 0 except R7 notes)
    Evidence: screenshot / events.jsonl excerpt / recovery log excerpt
```

Exit criteria: R1, R2, R5, R6 pass outright; R2's zero-result probe and R4 fail loudly and
honestly; R7 clean refusals; R8 flat memory + run 8 ≈ run 1.
**Where misses go:** TEST-12; wrong-row removal → PROD-3 immediately; governance/evidence gaps →
PROD-18; budget poisoning → W-4 follow-up. Promote to `runtime/test/gate-skill/` after three
consecutive passes.

---

## WF-10 — Environment chaos + input-data edge cases

**Sites:** S5 (saucedemo.com, WF-4 Leg B checkout, suggested known-good skill) + any packaged
skill for Suite H

Run chaos against a known-good skill (WF-4 Leg B checkout works well).

### Chaos matrix (Suite G)
| # | Chaos | Steps | Expected |
|---|---|---|---|
| G.1 | Slow network (Slow 3G) | execute known-good skill | Slower but completes; sane timeouts |
| G.2 | Kill network mid-run (drop Wi-Fi at step ~3, restore 30 s) | | Clean failure w/ actionable error OR resilient resume; never zombie browser |
| G.3 | Kill browser mid-run (terminate Chromium ~step 5) | | Clean failure; next run spawns fresh |
| G.4 | Concurrent runs (two skills simultaneously) | | No instance collision, no tracker races |
| G.5 | Cancel mid-flight (`cancel_execution` half-way) | | Prompt stop, browser cleaned, status correct, partial telemetry |
| G.6 | Fatigue ×10 (checkout ten times back-to-back) | | No state leakage (cart/cookies), no memory growth, batches match |
| G.7 | Sleep/resume machine mid-execution, wake after 2 min | | Defined behavior — timeout error or resume, not eternal hang |

Log per test: observed behavior, leftover processes (`Get-Process chrome,node`), tracker counts vs
executions.

### Input edge cases (Suite H)
Take any packaged skill with a text input; feed via `execute_skill`:

| # | Input | Expectation |
|---|---|---|
| H.1 | `""` empty | Clear validation error, no execution |
| H.2 | `"   "` spaces only | Defined: reject or trim |
| H.3 | 10,000-char string | No crash; truncate or reject sensibly |
| H.4 | `日本語 🎉 café` unicode+emoji | Typed correctly into target field |
| H.5 | `<script>alert(1)</script>'; DROP TABLE users--` | Literal text only in target app; nothing executed locally |
| H.6 | Newlines + tabs | Defined behavior |
| H.7 | `007` / `1e5` / `-0` | Not coerced/mangled |
| H.8 | Missing required input entirely | Clear guidance via `get_skill_inputs` beforehand |

Also verify `get_skill_inputs` declares fields properly so a customer's agent knows what to supply.

---

## WF-11 — Cloud & entitlement gates (plan-tier limits)

**Sites:** none — cloud API only, no browser workflow

Backend locally: `cd conxa-cloud/backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`.
Grab a Clerk JWT from frontend dev-server network tab; admin token from `CONXA_ADMIN_TOKEN`.
Source of truth before/after every test:
```powershell
curl.exe -s -H "Authorization: Bearer $TOKEN" "$BASE/entitlements/current"
```
Change plan without payment:
```powershell
curl.exe -s -X POST "$BASE/entitlements/admin/billing" -H "Authorization: $ADMIN" `
  -H "Content-Type: application/json" -d '{"workspace_id":"<org_xxx>","plan":"free"}'
```
(`duration_days` optional; Enterprise "unlimited" means admin-set overrides — out-of-box 0/None,
test that too.)

### Tests (run each across Free → Starter → Pro → Enterprise)
| # | Test | Gate / expected error (HTTP) | Key steps & sub-checks |
|---|---|---|---|
| 1 | Compile credits exhaust | `reserve_compile_credit` → 402 `compile_credit_limit_exceeded` | Reserve unique IDs till limit (Free @25). Sub-tests: same ID twice idempotent (counted once); release returns credit; commit counts permanently; reservation TTL expiry (~30 min / lower TTL) |
| 2 | Human-edit token pool | `ensure_human_edit_available` → 402 `human_edit_pool_exceeded` | Burn 500k via proxy `usage_class:"human_edit"`; compile-class must stay unaffected (separate pools) |
| 3 | Machine limit | `ensure_machine_slot` → 402 `machine_limit_exceeded` | device-A allowed, device-B blocked @Free; machines list reflects; revoke frees slot; revoked device counts brand-new again; Starter @3 |
| 4 | Seat limit | `ensure_seats_available` → 402 `seat_limit_exceeded` | 4th Clerk-org member blocked; existing members keep working; unlimited plan fine. NOTE: fails OPEN if Clerk unreachable |
| 5 | Workflow cap / soft-lock | 402 `workflow_limit_exceeded` / `workflow_locked` | Publish #26 @Free fails; REPUBLISHING active workflow still OK at cap; Pro→Starter downgrade locks oldest-first; locked republish fails, upgrade unlocks automatically |
| 6 | Trial expiry (30 d) | `trial_expired` 402 | Backdate `trial_started_at` → builds blocked; sync + tracking KEEP working (execution is local); paid plans never expire |
| 7 | Distribution ladder | `distribution_not_permitted` 402 | External installer upload blocked Free/Starter, internal OK; external OK on Pro |
| 8 | White-label | `white_label_not_permitted` 402 | Blocked on Pro, allowed Enterprise |
| 9 | Ops tier | `ops_tier_required` **403** | audit-events/tracking dashboard/drift blocked @Free(none); basic-only @Starter; all pass Pro/Enterprise |
| 10 | Timed grant expiry | `plan_expires_at` honored | Pro for 1 day → auto-reverts to free limits without a downgrade job; Cashfree subs unaffected |
| 11 | Analytics retention | cutoff filter | Free 0 days hides everything; Starter 90 d shows recent; null override = unlimited |
| 12 | Compile pool routing | provider pool choice | Debug-log router: Free → free pool, Starter+ → premium pool |
| 13 | BYOK | `byok_enabled_for` | PUT /byok Azure deployment blocked ≤Pro, allowed Enterprise |
| 14 | Add-on stacking | billing addons | `credits_addon_20=1` on exhausted Free → 25+20 credits, 500k+200k tokens; reserving past old limit works; deactivate drops limits, usage stays used |
| 15 | Kill switches | env toggles default TRUE | Each `ENTITLEMENTS_ENFORCE_*`=false disables its gate; restore true → blocking resumes. Fresh deploy with no env MUST enforce everything |

Matrix quick view: compile ✅@25/@200/@500/no-block*; machines/seats/workflows same shape;
external distro ❌❌✅✅; white-label ❌❌❌✅; ops ❌/basic/✅/✅ (*Enterprise needs explicit numeric overrides — verify un-overridden Enterprise behaves sanely rather than blocking everything).

Error-code rule of thumb: payment-shaped limits → **402**; capability/permission → **403**.
Gotchas: meter says unlimited = None limit; stale plan = `billing_for()` cache (restart backend
after manual KV edits); stuck reservation = TTL; compile test won't trip = leftover kill-switch false.

Folded-in Suite J: invalid company token → clear re-registration prompt, not stack trace;
telemetry under fire — 20 rapid executions → `/tracking/{co}/events` batches match, no dupes/loss.

---

## WF-12 — Production-readiness go / no-go checklist

**Sites:** any practice site for Phase 4's record step (S1 the-internet.herokuapp.com / S3
demoqa.com convenient) + Conxa's own cloud dashboard/Studio/installer surfaces for every other
phase

Full-system pass before calling Conxa production-ready. Clean Windows VM simulates the customer.
Accounts needed: company owner, team member, fresh customer. Tick in order; on failure stop, note
in Issues, fix, restart from that step.

### Phase 1 — Cloud backend health
- `/healthz` 200; `/readyz` 200 (DB ping — if 5xx DO NOT proceed, deploy gate depends on it).
- Render started cleanly with `SKILL_AUTH_REQUIRED=true`: DB URL, Clerk issuer/JWKS, CORS,
  Cashfree creds, ≥1 LLM key present.
- NO filesystem-DB fallback line in production logs.

### Phase 2 — Frontend
Dashboard loads <5 s, no console errors; owner login works; logout/login as member; visit every
screen (Dashboard, Skill Packages, Billing, Team, Settings) without crashes/blank data.

### Phase 3 — Auth & team
Owner can invite/change-role/remove. Member cannot see owner actions nor fetch owner URLs directly.
No-token and garbage-token API calls → 401.

### Phase 4 — Studio core flow
App launches, Python backend clean; Clerk PKCE login persists across app restart (keyring).
Record 4–6 actions on a practice site → all events appear in order. Compile clean; report has
selectors, sensible confidence, assertions after submit. Editor re-target of one step passes patch
gate. Build `.exe` installer → bundle contains **NO `auth/auth.json`, NO storageState, NO
credentials of any kind** 🚨 (any hit = hard blocker, STOP).

### Phase 5 — Publish & hosting
Publish succeeds; pack appears in dashboard Skill Packages; hosted content matches compile;
version number correct.

### Phase 6 — Customer simulation
Install downloaded `.exe` on the clean VM; `~/.conxa/` contains host exe + `conxa-app/`;
`version.json` correct; per-company token auth works. Sync pulls published packs atomically
(SHA-256 match). Via MCP: `list_skills` / `get_skill_inputs` / `execute_skill` valid inputs →
browser opens, completes; `get_execution_status` success. Recovery spot-check: renamed button →
L1/L2 heals with zero tokens; truly missing element → graceful typed error, no silent hang.
Telemetry arrives at tracking endpoint with success/failure counts.

### Phase 7 — Billing (Cashfree sandbox)
Buy plan with test card → webhook fires → Billing screen updates. Free-plan limit hit → clear
upgrade message; after upgrade limit lifts. Failing card → clear error, plan NOT upgraded, no
partial state.

### Phase 8 — Auto-updates
Newer `app-vX.Y.Z` manifest → runtime polls, downloads, loads; `min_host` respected; simulated bad
update → rollback to previous versioned dir. Host version bump flows through manifest/installer.
Dev→stable promotion validates Ed25519 signatures; stable customers receive it.

### Phase 9 — Security spot checks
Routes under `/api/v1` (except documented tracking exception); expired JWTs rejected; runtime
session files AES-GCM ciphertext; Company A cannot fetch Company B packs (swap tokens to prove);
LLM proxy rejects unauthenticated; no API keys in built frontend JS.

### Phase 10 — Performance smoke
Same skill ×10 — all pass, no RAM creep. Three different skills back-to-back — no state bleed.
`/healthz` ×50 fast 200s. LONG workflow (20+) full chain works. Browser killed mid-execution →
clear error, no crash.

### Final gate
All boxes ticked or fixed-and-retested; clean-machine tested; telemetry matches the session.
**Hard blockers (any ONE = NO-GO):** credentials in build output · `/readyz` failing · FS-DB
fallback active · cross-company leakage · Tier A burning tokens · broken update rollback.

Test Run Info + Issues Found tables: fill date/tester/versions and log issues with severity and
fix status; sign GO/NO-GO at the bottom of the run.

---

## WF-13 — Variables, loop, branch, dedupe: Sheets→Gmail + GitHub PR triage

**Sites:** Google Sheets + Gmail (Leg A) · GitHub (Leg B) · **Accounts:** your own Google account,
your own GitHub account with at least one repo that has 2+ open PRs (or open 2 test PRs first) ·
**Tabs:** 1 per leg

Two short recordings picked to isolate variables/loop/dedupe (Leg A) and branch/recovery (Leg B)
without needing any of the arsenal sites above. Record each leg separately in Build Studio so a
failure in one doesn't block diagnosing the other.

### Leg A — Sheets → Gmail (variables, loop, dedupe)

**Step 1 — Build the source sheet.**
1. Go to `sheets.google.com` → **Blank spreadsheet**.
2. In row 1 type headers: `A1=Name`, `B1=Email`, `C1=Amount`.
3. Fill 3 data rows, e.g.:
   - Row 2: `Alex`, `alex@example.com`, `250`
   - Row 3: `Priya`, `priya@example.com`, `1800`
   - Row 4: `Sam`, `sam@example.com`, `90`
4. Leave the tab open — you'll read from it during recording.

**Step 2 — Start recording in Build Studio.**
1. Open Build Studio → **New Recording**.
2. Name it `sheets-to-gmail-test`.
3. Click **Start Recording**.

**Step 3 — Record the per-row actions (only do ONE row live — this is what the loop will replay for the rest).**
1. Switch to the Sheets tab. Click cell **A2** (`Alex`) — this is your `{{name}}` source.
2. Click cell **B2** (`alex@example.com`) — `{{email}}` source.
3. Click cell **C2** (`250`) — `{{amount}}` source.
4. Switch to Gmail (`mail.google.com`). Click **Compose**.
5. Click the **To** field, type `alex@example.com` (or paste from the cell you just clicked, if Build Studio's recorder captures copy/paste — otherwise just type the same value you saw in B2).
6. Click the **Subject** field, type `Payment update for Alex`.
7. Click the **Body**, type `Hi Alex, your recorded amount is 250.`
8. **Branch point:** since `250 < 1000`, click **Send** directly. Do NOT click any "star"/"important" marker on this row.
9. Stop recording here. **Do not loop live for rows 3–4** — that's what Human Edit is for.

**Step 4 — Human Edit: turn this into a loop + branch.**
1. Open the recorded workflow in the editor.
2. Select the three Sheets-read steps (A2/B2/C2 clicks) and the Gmail compose/send steps as one group.
3. Use **Wrap in for_each** (or the editor's loop-insertion action) bound to the sheet range `A2:C4` — this makes `{{name}}`, `{{email}}`, `{{amount}}` per-iteration variables instead of literal recorded text.
4. Insert a branch before the Send step: **if** `{{amount}} > 1000` → click the star/important-mark icon in Gmail's compose toolbar **before** Send; **else** → Send directly (this is exactly what you recorded for the `250` row, so the else-branch is your recorded path).
5. Save. Compile.

**Step 5 — Verify compile output.**
1. Open the compile report. Confirm there is **one** loop step over the 3 rows, not 9 duplicated steps — that's the dedupe check.
2. Confirm the compiled step's `value` fields show `{{name}}`, `{{email}}`, `{{amount}}` tokens, not the literal `Alex`/`alex@example.com`/`250` you recorded — that's the variable-binding check.
3. Confirm the branch step exists once, referencing `{{amount}}`.

**Step 6 — Replay and check the result.**
1. Run the compiled skill (Studio Run Test or via MCP `execute_skill`).
2. Check Gmail's Sent folder: 3 emails, one per row, each with the row's own name/email/amount in the body — not all 3 saying "Alex".
3. Check that Priya's email (amount `1800`) got starred/marked important and Alex's and Sam's did not — that's the branch check.
4. Check recovery/LLM logs: 0 recovery events, 0 runtime LLM calls expected on a clean replay.

### Leg B — GitHub PR triage (branch + recovery)

**Step 1 — Prepare two PRs in one repo.**
1. In a repo you own, open **Pull requests**.
2. Make sure at least one open PR shows a green **"Merge"** button (no conflicts) and at least one shows a **"Resolve conflicts"** button (or the "This branch has conflicts" banner). If you don't have one naturally, create a throwaway branch that edits the same line as `main` in two different ways and open a PR from it to force a conflict.

**Step 2 — Record on the clean-path PR.**
1. Start a new Build Studio recording named `github-pr-triage-test`.
2. Go to `github.com/<you>/<repo>/pulls`.
3. Click the clean PR's title to open it.
4. Click **Merge pull request** → **Confirm merge**.
5. Stop recording.

**Step 3 — Human Edit: add the branch for the conflicted case.**
1. Open the recording in the editor.
2. Before the Merge click, insert an `if_present` probe: does the "Resolve conflicts" button / conflict banner exist on this PR page?
   - **If present:** click **Resolve conflicts**, wait for GitHub's inline conflict editor to load, then stop the branch there (don't try to auto-resolve text conflicts — just prove the branch fires and stops safely).
   - **If absent (your recorded path):** click **Merge pull request** → **Confirm merge**, exactly as recorded.
3. Wrap the whole thing in a `for_each` over the PR list if you want to test more than one PR per run (optional for this test — one branch firing correctly is the main proof).
4. Save. Compile.

**Step 4 — Replay against the clean PR (regression check).**
1. Run the skill against the same clean PR (or another clean one). Expect: `if_present` finds nothing → falls through to Merge → PR merges. Zero recovery events.

**Step 5 — Replay against the conflicted PR (branch + recovery check).**
1. Run the same compiled skill against the conflicted PR's URL/selection.
2. Expect: `if_present` detects the conflict banner → clicks "Resolve conflicts" → stops there without error. The Merge button must NOT be clicked on this run.
3. To also test self-healing: before this replay, resize your browser window narrower (or toggle GitHub's light/dark theme) so the button layout shifts slightly, then replay. Check the recovery log — if the selector needed a Tier A re-find, that's the self-heal working; if it needed a Tier B agent call, that's still a pass but log the token cost.

**Step 6 — Record results.**
Use the standard log block from "How to work" above for both legs. If either branch fires on the wrong PR type (e.g. Merge gets clicked on a conflicted PR, or vice versa), that's a hard fail — log it immediately as a PROD-3-style entity/branch-safety issue, same severity class as WF-8's B-8.

**Where failures go:** loop/dedupe issues → compiler `IdentityBundle`/loop-wrapping items in `TODO.md`; variable-binding issues → editor patch-gate items; branch misfires → treat like WF-6/WF-8 B-8 (wrong-branch-taken is a safety bug, not a cosmetic one); recovery issues → WF-7's cascade owners.

---

## WF-14 — AI Review step: reasoning checkpoint (EXEC-13)

**Sites:** S11 (en.wikipedia.org) + S3 (demoqa.com/text-box) · **Tabs:** 1

`ai_review` is an author-placed reasoning checkpoint (`docs/TRD.md` §11.3) — it pauses the run,
asks Claude a structured question about the live page, and binds the answer into a later step. It
sits outside the recovery cascade (no tokens spent unless this step fires, on purpose) and must
never be answered by guessing — always verify a real pause happened and a real answer came back.

### Stage 1 — Record, insert the review step, and verify compile
1. Record (~4 steps): `en.wikipedia.org` → search box → type `Marie Curie` → Enter → land on the
   article; then `demoqa.com/text-box` → click **Full Name** → type placeholder `test` → **Submit**.
   Stop recording.
2. In the editor, select the step right after the Wikipedia article loads and insert an **AI
   Review** step:
   - **Prompt:** `Read the visible page text and classify this Wikipedia article's subject into exactly one of: person, place, organization, event, other. Reply with only the category word.`
   - **Output schema:** enum of `person | place | organization | event | other`.
   - **Output name:** `article_category` (becomes the `{{article_category}}` placeholder).
   - **On failure:** `use_default`, **Default value:** `other`.
3. Replace the demoqa **Full Name** field's recorded value (`test`) with `{{article_category}}`, so
   the review's answer is what actually gets typed. Save. Compile.
4. Verify: the compile report shows one `ai_review` step with your prompt/schema/`on_failure`, not
   silently dropped (only a blank prompt gets dropped); the demoqa step's value shows the
   `{{article_category}}` token, not the literal `test`.
5. Safety-gate check: try inserting a **destructive** step (e.g. delete/remove) directly after the
   AI Review step and save. **Expect the patch gate to refuse the save** — PROD-3's
   `destructive_step_cannot_directly_follow_ai_review` rule: a review answer has no entity binding,
   so it can gate *whether* a later step runs but never supply *what* a destructive step acts on.
   Remove that destructive step before continuing.

### Stage 2 — Replay in the Studio sandbox (self-answering path)
1. Run **Studio Run Test** 5 times. The sandbox has no agent in the loop, so `cmd_test_workflow`
   answers each pause itself through the metered cloud LLM proxy.
2. Expect every run to complete; demoqa's Full Name field ends up filled with one of `person /
   place / organization / event / other` (Marie Curie → expect `person`); each report shows exactly
   one `ai_review` pause answered. Studio caps at 5 pauses per test run (a safety limit against a
   misconfigured workflow holding the sandbox's host-lock open forever) — a 6th pause in one run
   hitting the cap is expected behavior, not a bug, if you engineer a workflow with 6+ review steps.

### Stage 3 — Replay via real MCP: pause, bad answer, timeout, token audit
1. Install the pack, connect Claude Desktop, call `execute_skill`. **Expect a pause, not a
   completed run** — the review prompt, a live DOM inventory, a current-page screenshot (confirm it
   actually shows the Marie Curie article), plus `_meta: {"conxa/ai_review": {...}}` naming the
   exact `resume_from`/`review_results` call to make.
2. Answer it: call `execute_skill` again with `resume_from` and
   `review_results: {"<step_index>": "person"}`. Confirm it resumes from the same parked page (not
   a fresh navigation) and completes with the demoqa field showing `person`.
3. **Bad-answer path:** resume once with an answer outside the enum (e.g. `"banana"`). Expect a
   bounded re-ask (`REVIEW_RETRY_MAX = 2`), not an immediate hard failure. Exhaust both re-asks with
   bad answers and confirm `on_failure` applies (`use_default` → binds `other`, continues from the
   same parked page).
4. **Never-answered path:** trigger a pause and don't resume it. Wait past `PARK_TTL_MS` (~180s) and
   confirm the parked browser closes itself (`Get-Process chrome,node` shows nothing leaked).
5. **Zero-token sanity check:** every OTHER step (searches, clicks, typing) shows zero LLM calls in
   the recovery/LLM log — only the one deliberate `ai_review` call spends tokens.

**What this proves on pass:** the pause/resume contract works end-to-end for both a real agent and
the token-free sandbox self-answer path; a bad or missing answer degrades safely (re-ask → default,
never a crash or a silent wrong click); the destructive-step safety gate actually blocks the unsafe
pattern in the editor.
**Where failures go:** `TODO.md` EXEC-13; safety-gate bypass → PROD-3 immediately (same severity
class as WF-8's B-8); pause/resume plumbing → `runtime/app/review_pause.js` / `server.js`'s
`_resumeReviewStep`.

---

## WF-15 — Hand-Over step: yield the page to a person (EXEC-21)

**Site:** S13 (google.com/recaptcha/api2/demo) · **Tabs:** 1 (plus any popup the CAPTCHA flow
itself opens)

`handover` is `ai_review`'s human sibling (`docs/TRD.md` §11.4) — it pauses the run and lets a
*person*, not Claude, do the one thing only they can do (here: solve a CAPTCHA), then reclaims the
page. Unlike `ai_review`, drift while paused is expected and fine — the whole point is letting a
person change the page.

### Stage 1 — Record, insert the hand-over, and the fast "person already there" path
1. Record (~3 steps): go to `google.com/recaptcha/api2/demo` → click the **I'm not a robot**
   checkbox (don't worry about solving the challenge during recording, cancel/retry after) →
   click **Submit**. Stop recording.
2. In the editor, insert a **Hand-Over** step immediately before the checkbox-click step:
   - **Message:** `Please solve the CAPTCHA challenge, then let the workflow continue.`
   - **On failure:** `abort` (only option besides `continue` — no `use_default`, a hand-over
     produces no answer value).
   - **Resume-when probe** (worth testing): a probe checking the checkbox now reads as checked
     (e.g. its `aria-checked`/checked-state selector) — gates the actual resume, not just "the
     person clicked something."
3. Save. Compile. Confirm the compile report shows one `handover` step with your message, not
   dropped (only a blank message gets dropped).
4. Install the pack, connect Claude Desktop, call `execute_skill`. The moment the run reaches the
   hand-over, **sit at the keyboard and solve the CAPTCHA yourself within 120 seconds**
   (`CONXA_HANDOVER_INCALL_MS` default). Expect the run to continue and complete **inside the same
   `execute_skill` call** — no pause response, no park, nothing for the agent to do; confirm via
   the run log that no `handoverPause` event fired.

### Stage 2 — Real pause: all three resume signals, host-lock release, drift tolerance
Run three separate times, deliberately waiting past 120 seconds before touching the CAPTCHA each
time, and resume via a different signal each run:

| Run | Resume via | What to check |
|---|---|---|
| P1 | Click the **in-page banner** the hand-over injects (should appear on the CAPTCHA tab automatically, and re-appear if you open a new tab) | Solve CAPTCHA → click banner → run resumes and completes from the parked page |
| P2 | `conxa-runtime.exe resume <run_id>` from a terminal (get `run_id` from the pause response) | Solve CAPTCHA first, then run the CLI command → run resumes |
| P3 | Drop a file at `~/.conxa/resume/<run_id>.cmd` (any content) | Solve CAPTCHA, create the file (`New-Item`), confirm the `fs.watch`/poll fallback picks it up within ~5s and resumes |

For each: confirm the pause response showed the page screenshot/DOM (mirrors `ai_review`'s pause
shape); confirm the platform's `host_lock` was **released** during the pause (start a second,
unrelated skill against a *different* site during the pause and confirm it runs immediately, not
blocked); confirm the resume happened via the runtime's own long-lived process (check the run's own
log timestamps against when you actually solved the CAPTCHA, not a fresh agent-initiated call).

Then, reusing the same setup, confirm drift tolerance and clean-failure boundaries:
- Trigger a pause, navigate the paused tab away and back before resuming — confirm resume still
  succeeds (no divergence refusal, unlike `ai_review` — this is by design).
- Trigger a pause, **close the tab** entirely before resuming (via CLI or file-drop, since the
  banner is gone with the tab) — confirm this fails the step cleanly (tab resolution fails) rather
  than silently continuing on whatever page happens to be current.
- With the `resume_when` probe from Stage 1 set: trigger a pause, resume without actually
  completing the checkbox — confirm the resume is refused/times out cleanly rather than continuing
  on an unfinished CAPTCHA.

### Stage 3 — Known limitations (confirm they fail as documented, not worse)
- **Native dialogs:** if you can arrange a variant where a hand-over precedes a JS `alert`/
  `confirm`/`prompt`, confirm a person genuinely cannot interact with it live (Playwright's dialog
  listener intercepts it at the CDP level) — expect this to be a known, stated limitation, not
  something to debug further.
- **Hand-over inside a loop:** wrap the hand-over step in a `for_each` (e.g. loop over 2 dummy rows
  and put the CAPTCHA hand-over inside the loop body), trigger a pause mid-loop, and resume.
  Expect it to NOT resume at the same iteration — the loop unwinds and the iteration cursor is
  lost. This is a stated gap (no compile-time guard yet rejects authoring a hand-over inside a loop
  body) — log it as confirmed-expected, not a new bug, unless it does something worse (e.g. crashes
  or silently skips the rest of the loop with a false success).
- **Runtime restart:** trigger a pause, then restart the runtime process entirely before resuming.
  Expect the hand-over to be lost (not durable across a restart — this is EXEC-22, separately
  tracked, not part of this test).

**What this proves on pass:** all three resume signals work independently; the fast "person already
there" path never parks or spends a token; the platform lock releases during a long pause so
sibling runs aren't starved; drift during a hand-over is tolerated by design while a closed tab or
failed probe still fails cleanly; the documented limitations (dialogs, loop bodies, restarts) fail
exactly as documented and not worse.
**Where failures go:** `TODO.md` EXEC-21 (built shape) / EXEC-22 (restart durability, separately
tracked) / loop-guard gap (tracked, not urgent unless it fails unsafely); host-lock starvation →
`runtime/app/host_lock.js`; any silent continue after a closed tab or failed probe → treat as a
safety bug, same severity class as WF-8's B-8.

---

## WF-16 — Intelligent Compiler: how a recording turns into a reliable skill

**Sites:** S3 (demoqa.com — forms, date picker, dynamic properties) + S10 (datatables.net — identical
rows) + S16 (github.com public repo folder, for a single download→upload pair) · **Tabs:** 1 ·
**Tools:** the compile report panel in Build Studio (opens automatically after compiling; also
reachable from a skill's detail page)

This is not a new feature test — it is a guided tour of what "the compiler is smart" actually
means in practice, broken into small, checkable pieces a non-engineer can verify by eye. Every
piece below is something the compiler does automatically, with **zero manual selector-writing**;
your job is to record something ordinary and confirm the compiler produced something trustworthy.

Plain-language explainer before you start: when you record actions in a browser, all the compiler
first sees is "the user clicked something." A website's buttons can be renamed, moved, or have
their invisible ID numbers change between when you record and when the skill runs again weeks
later. The "intelligent compiler" is the part that looks at everything it can see about the
element you clicked — its visible label, its role (button vs. link vs. checkbox), nearby text,
its position relative to other elements — and bundles all of that into one "identity" for the
element, instead of betting everything on one fragile detail. It also reads the whole recording
once it's done and adds a layer of *meaning* on top: what is this workflow trying to do, which
typed values should become fill-in-the-blank inputs instead of frozen text, and which steps look
like they didn't matter. None of this second layer is allowed to touch how an element gets found
on the page — that job stays with the identity bundle alone.

### Segment A — Multi-signal identity (does the compiler use more than one clue?)

1. Record on `demoqa.com/text-box`: click **Full Name**, type `Test User` → click **Email**, type
   `test@example.com` → click **Submit**. Stop recording, compile.
2. Open the compile report. For the Full Name step, find its identity information (labelled
   "Identity Bundle" or similar in the report / skill JSON). Confirm you can see **more than one**
   signal recorded for that single element — for example its accessible role (`textbox`), a
   test-id if the site has one, and nearby label text (`Full Name`) — not just one raw CSS
   selector string.
3. Confirm nowhere in that identity information does a signal look like it was **invented** —
   every value should trace back to something actually visible/inspectable on the real page (no
   selector that looks like a guessed sentence, no placeholder text that wasn't on the page).
4. Confirm the step's confidence score (shown in the compile report) is reasonably high for this
   simple, stable form — a low score here with no obvious reason is worth flagging.

**What this proves:** the compiler is building identity from several independent clues about the
same element, the way a person would recognize a button by more than just its exact pixel
position — this is what lets a later step survive small site changes without any human rewriting
a selector (see WF-7 for what happens when it has to actually recover from a change).

### Segment B — The meaning-layer pass writes intent, never addresses

1. Re-open the Full Name / Email recording from Segment A (or record a fresh short one). Before
   compiling, note the literal values you typed (`Test User`, `test@example.com`).
2. Compile. Open the compile report and look at the compiled step's **value** field for the Full
   Name step.
3. Expect: the value is now a fill-in-the-blank token like `{{full_name}}`, not the literal text
   `Test User` you typed — the compiler recognized this as a piece of information that should be
   supplied fresh on every run, not frozen from the one time you recorded it. Same check for the
   email field → `{{email}}`.
4. Look for a short plain-English description attached to each step (a "semantic description" —
   something like "Fill in the full name field"). Confirm it reads like a person wrote it, not a
   raw technical log line.
5. **What must NOT have changed:** the step's underlying element-finding information (its identity
   bundle / selectors) from Segment A. This meaning-layer pass is only allowed to add plain-English
   labels and turn typed text into placeholders — it is never allowed to change *how* the compiler
   finds the element on the page. If you can compare a "before this pass" and "after" version of
   the same step's selector data and they differ, that is a serious bug — log it immediately, same
   severity as WF-8's B-8.

**What this proves:** the compiler adds a helpful "what does this step mean" layer without ever
touching the "how do I find this element" layer — meaning and addressing are kept strictly apart.

### Segment C — A step the compiler decides didn't matter gets archived, not deleted

1. Record a short flow on `demoqa.com/dynamic-properties`: click somewhere that visibly does
   nothing (e.g. click blank page whitespace, or a disabled-looking control) as an extra step in
   the middle of an otherwise normal 4–5 step recording. Stop, compile.
2. Open the compile report's list of archived/removed steps (look for a section like "archived
   steps" or "steps removed as no-ops"). Confirm the no-op click you added shows up there —
   **not silently gone**, but visible with a reason.
3. Confirm the rest of the workflow (the steps that DID matter) compiled normally and the archived
   step is genuinely absent from the steps that will run at replay time.
4. Confirm you can still see, in the archive, what the original recorded step looked like — this
   is the audit trail proving nothing was silently thrown away.

**What this proves:** when the compiler decides a step is noise, it says so out loud and keeps a
record, rather than quietly making the workflow shorter with no trace.

### Segment D — A suggested check is advisory, never a hard gate

1. Using any of the recordings above, look at the compiled step right after a "commit" action
   (e.g. right after Submit on the demoqa form). Check whether the compile report added a
   suggested extra check (an "assertion") — something like "the page text changes" or "the URL
   changes" after that step.
2. If one was added, confirm its type is a plain text/URL/page-state check — **never** a check
   that says "a specific element selector must exist/not exist," since that kind of check would
   effectively be the compiler writing a selector by another name.
3. Confirm the suggested check is marked **optional/advisory** (not required to pass) — deliberately
   run the workflow once on a version of the page where that check would fail (e.g. block the
   network right after Submit) and confirm the run still reports success rather than failing on
   the suggested check alone.

**What this proves:** the compiler is allowed to suggest "here's a way to double-check this step
worked," but a wrong suggestion can never fail an otherwise-successful run.

### Segment E — Custom date pickers collapse into one clean step

1. Record on `demoqa.com` (a page with a calendar-style date picker, e.g. the Date of Birth field
   on the practice form): click to open the picker, navigate a month if needed, click a specific
   day. This is usually 3–5 separate recorded clicks (open, maybe navigate, pick day).
2. Compile. Open the compile report's step list and count how many steps represent that date pick.
3. Expect: **one** step, not three-to-five — the compiler recognized the whole "open → navigate →
   pick" sequence as one date-picking action and collapsed it, remembering which exact day you
   picked as a reusable value rather than a frozen click sequence.
4. Replay the skill and, if the skill takes a date as an input, replay it with a **different**
   date than the one you recorded (e.g. a different month entirely, requiring several
   month-navigation clicks the picker will do on its own). Confirm it correctly lands on the new
   date, not the recorded one — proving it's driving the widget live, not replaying frozen clicks.

**What this proves:** the compiler recognizes a whole interaction pattern (not just individual
clicks) and turns it into something that can be re-parameterized, the same idea as Segment B but
for a much fussier kind of control.

### Segment F — Loop suggestions: "you did this once, want it for a list?"

1. Record on S16 (a public GitHub repo folder): download one file, then upload that exact same
   file back somewhere that accepts uploads (e.g. `demoqa.com/upload-download`) — a simple
   download→upload pair, one file only.
2. Compile. Open the compile report and look for a "loop suggestion" (may be labeled "turn this
   into a loop" or similar) in the editor/Human Edit view.
3. Expect a suggestion proposing to generalize this single file's name into a list your workflow
   could loop over (a `for_each`), **not automatically applied** — it should require you to click
   Accept in the editor before anything changes.
4. Accept it, save, and confirm the compiled workflow now has one loop step instead of the two
   fixed download/upload steps, and that it still runs correctly for the single file (a loop of
   one is a valid loop).
5. Reject-path check: repeat with a fresh recording, and this time do NOT accept the suggestion.
   Confirm the workflow compiles and runs exactly as it would have with no suggestion feature at
   all — declining a suggestion must never change behavior.

**What this proves:** the compiler notices a repeatable pattern and offers to generalize it, but
never rewrites your workflow without an explicit human click — same governance model as every
other "the AI proposes, a person disposes" feature in this file (WF-6's branches, WF-14's AI
Review, WF-17 below).

### Segment G — Compile determinism (recompiling shouldn't reshuffle anything)

This is the same check as WF-3 Leg C — if you've already run it there, you don't need to repeat
it here. If not: recompile the SAME recorded session twice (`conxa-cloud/scripts/recompile_session.py
<session_id>` run twice) and diff the two compiled outputs' selectors/identity bundles. Identical =
PASS. Any difference between two compiles of the exact same recording means something in the
compiler is non-deterministic, which would make even the "same recording, same day" case unreliable.

**Where failures go:** `TODO.md` under the `IdentityBundle`/`selector_grammar` items for Segment A;
BUILD-25 (second-opinion / meaning-layer) items for Segments B–D — a Segment B selector-mismatch
finding is PROD-3-severity, log it as such; date-picker items for Segment E; loop-suggestion items
for Segment F.

---

## WF-17 — Conxa Copilot: the "why did this fail" chat assistant in Human Edit

**Sites:** local [`fixtures/recovery-fixture.html`](fixtures/recovery-fixture.html) (easiest way to
force a clean, repeatable failure — see WF-7's setup) · **Tabs:** 1 · **Where:** Build Studio →
open a workflow in Human Edit → the floating chat launcher (bottom corner of the editor, not the
Tools-rail dialog)

Plain-language explainer: Conxa Copilot is a chat window that lives inside the workflow editor.
When a test run fails, instead of you staring at a stack trace, you can ask the Copilot "why did
this fail?" and it looks at the same evidence a person would — a screenshot of the page at the
moment it broke, what the compiler originally believed about that step, and the actual error — and
explains it in plain English. It can also **suggest** small fixes (like "type a plain description
into this field" or "call this value {{customer_name}} instead of freezing the text you typed"),
but it never applies anything by itself and it is never allowed to touch how an element gets found
on the page (see WF-16 Segment B — this is the same boundary, enforced the same way).

### Stage 1 — Force a failure to diagnose

1. Serve the recovery fixture (`python -m http.server 8099 --directory docs\testing\fixtures`).
   Record a minimal skill against `http://localhost:8099/recovery-fixture.html?v=gone` (a variant
   where the button truly isn't there) — click "Buy Now", stop, compile.
2. Run **Studio Run Test**. Expect it to fail cleanly (this variant is designed to be unrecoverable
   — see WF-7 R4). You now have a real failed run to diagnose.

### Stage 2 — Open Copilot and ask about the failure

1. In Human Edit, click the Copilot launcher (bottom-corner floating button, not a tab inside the
   Tools rail). A chat panel should open without hiding the step list behind it.
2. Type: `Why did this step fail?` and send.
3. Expect: the reply streams in live (text appears progressively, like a typing effect — not a
   long pause followed by the whole answer at once) and references the ACTUAL failure — e.g.
   mentions the button/element that was expected and that it could not be found — not a generic
   "something went wrong" non-answer. If a failure screenshot exists, the reply should reflect
   what's actually visible on that screenshot (describe it back to Copilot yourself and confirm it
   matches what the panel is discussing).
4. Confirm a "thinking" indicator shows immediately after you send the message (before any text
   streams in), so the panel never looks frozen/unresponsive during the gap before the first word
   arrives.

### Stage 3 — A proposal is a suggestion, not an edit

1. Continue the conversation: ask `Can you suggest a fix?` (or similar). If the failure is a
   genuinely unrecoverable missing-element case (which `?v=gone` is, by design), Copilot may
   correctly say there's nothing it can safely propose — that's a valid, honest answer, not a bug.
   To actually exercise a proposal, use a workflow where the meaningful fix is something Copilot
   CAN legally touch — e.g. record a form with a value that should have been a variable but was
   compiled as frozen text (see WF-16 Segment B), and ask Copilot to review it.
2. When a proposal card appears, confirm it shows a clear before/after diff (what would change)
   and two explicit buttons — Accept and Reject. Confirm the proposal's *type* is limited to things
   like the field's value, its input-binding token, its intent/description, or an advisory
   assertion — **never** anything that looks like a selector or element-identity data. If you ever
   see a proposal that would change how an element is found, that is a hard safety-rule violation —
   log it immediately, same severity as WF-8's B-8.
3. Click **Accept** on a legitimate proposal. Confirm: the editor's step actually updates to match
   the diff you saw; the change goes through the same "Undo" system as a manual edit (test Undo —
   it should revert the Copilot's change exactly like it would a manual one); the workflow still
   compiles cleanly afterward.
4. On a different proposal (or a fresh one), click **Reject** instead. Confirm the step is
   genuinely unchanged. There's no user-visible proof the rejection was "logged" (that's an
   internal record for improving future suggestions), but confirm at minimum that rejecting one
   proposal doesn't block you from asking Copilot a follow-up question or getting a new proposal
   later in the same conversation.

### Stage 4 — Conversation mechanics

1. **Edit and resend:** scroll back to your first message in the conversation, edit it to ask
   something different, and send. Confirm the conversation after that point is replaced (the old
   replies after your edited message disappear) and a fresh reply comes back for the new question
   — not the old replies plus a duplicate new one.
2. **New session:** click "new conversation" / start fresh. Confirm the chat visibly clears. Then
   navigate away from Human Edit and back (or restart Studio) — confirm your previous conversation
   isn't silently lost forever; it should be archived somewhere retrievable (even if there's no UI
   to browse old sessions yet, the underlying file should exist — ask an engineer to confirm the
   session-archive file grew if you want hard proof).

### Stage 5 — Honest about missing evidence

1. Pick a skill that has **never** been test-run in this Studio install (a freshly compiled one you
   haven't clicked "Run Test" on yet). Open Copilot and ask `Why did this fail?` anyway.
2. Expect: Copilot should say, in effect, that there's no failed run on record for this skill / it
   has nothing to inspect — **not** invent a plausible-sounding but fabricated diagnosis. This is
   the most important honesty check in this workflow: an AI assistant that confidently explains a
   failure it never actually saw is worse than one that admits it can't help yet.

**What this proves on pass:** the Copilot reasons from real evidence (or admits it has none),
proposes only within its legal boundary (meaning, never selectors), never applies anything without
an explicit Accept click, and a rejection is a real dead end that changes nothing.
**Where failures go:** `TODO.md` BUILD-26; a proposal that touches selector/identity data →
PROD-3-severity, log immediately; a fabricated diagnosis with no real evidence → log as an
overconfidence/hallucination bug against `llm/copilot.py`.

---

## WF-18 — Authentication: signing in to every part of Conxa

**Sites:** none required for Legs A/B/D (Conxa's own login screens) · S1 (the-internet.herokuapp.com
`/login`) for Leg C · **Tools:** a clean-ish browser profile helps for the first-run checks

Conxa has **four separate places** someone signs in, and they don't share tokens with each other
on purpose — mixing them up is a real risk, so this workflow tests each one on its own and then
checks the boundaries between them.

| Leg | Who is signing in | Into what | Proves |
|---|---|---|---|
| A | You, the SaaS vendor / developer | Build Studio → Conxa Cloud | Clerk login works, persists, refreshes silently |
| B | Any caller | Conxa Cloud's own API | Protected routes actually reject bad tokens |
| C | The end customer's own login on THEIR target website | Runtime, while a skill runs | The runtime can sign into a real site and remember it safely |
| D | A person using the Conxa Execute desktop app | Conxa Execute → Conxa Cloud | Execute's login is independent of Studio's, and workspace access resolves correctly |

### Leg A — Build Studio login (Clerk)

1. Launch Build Studio fully signed out (or click Sign Out first if already logged in). Click
   **Sign in**.
2. Expect: your default browser opens to a Conxa/Clerk login page (not an embedded webview inside
   Studio itself). Log in with a real account.
3. Expect: after logging in in the browser, Build Studio itself — not the browser — shows you as
   signed in within a few seconds, with no copy-pasting of a code required. If it hangs for more
   than ~15 seconds, that's a fail — check the app didn't fail to catch the browser's redirect.
4. Fully quit and relaunch Build Studio. Expect: **still signed in**, no second login prompt — the
   session was saved to your OS's secure credential store, not just kept in memory.
5. Leave Studio open and idle for longer than the access token's short lifetime (or, faster: ask an
   engineer to artificially expire it) — then trigger any action that calls the cloud (e.g. compile
   a workflow). Expect: it just works, with no visible re-login prompt — the expired token should
   refresh itself silently in the background.
6. Click **Sign Out**. Relaunch Studio. Expect: genuinely signed out, login screen shown again —
   confirm the credential was actually removed from the OS credential store, not just hidden in the
   UI (ask an engineer to check, or try signing in as a DIFFERENT account and confirm no leftover
   identity from the first account appears anywhere).

### Leg B — Cloud API rejects what it should

Using a REST tool (curl/Postman) against the cloud backend directly (see WF-11's setup for how to
point at a local or deployed backend):

1. Call any protected endpoint (e.g. the entitlements endpoint from WF-11) with **no**
   Authorization header at all. Expect a clean `401`, never a `500` or a page of stack trace.
2. Call the same endpoint with a garbage/made-up bearer token. Expect `401` again.
3. Call it with a real, valid token that belongs to a DIFFERENT company/workspace than the data
   you're trying to read. Expect either `401`/`403` or an empty/scoped result — never another
   company's data (this is the cross-company leakage check also listed in WF-12 Phase 9; if you've
   already proven it there, you don't need to repeat it here).
4. If your environment has an admin token configured, call an admin-only route with it and confirm
   it works; call the SAME admin route with an ordinary user's token and confirm it's refused —
   the admin bypass should only ever widen access for the literal admin secret, never for a regular
   logged-in user.

### Leg C — Runtime signing into a target website, and remembering it safely

This is the "someone else's site, not Conxa's own login" case — the thing an end customer's skill
actually automates.

1. Record and compile a tiny skill against S1's `/login` page (username `tomsmith`, password
   `SuperSecretPassword!`) with no credentials pre-filled — Build Studio should treat this as a
   normal login form step, same as any other form.
2. Install/sync the pack, run it via Claude Desktop's `execute_skill` for the very first time on a
   machine that has never signed into this site before. Expect: a real, visible sign-in browser
   window opens automatically, pre-navigated to the right login page — you don't have to find it
   yourself.
3. Sign in by hand in that window. Expect: the window closes itself shortly after you land on the
   post-login page (not instantly on the very first click — give it a couple of seconds) and the
   run continues/completes on its own from there.
4. **Session is remembered:** run `execute_skill` again for the same skill. Expect: **no** sign-in
   window this time — it reuses the saved session and goes straight through.
5. **Session is encrypted, not plaintext:** open the file Conxa saved the session into (path under
   `~/.conxa/data/sessions/` — see `docs/Auth-and-Updater.md` §"Per-company session-encryption
   key"). Confirm it is NOT readable text (cookies, tokens, etc. in the clear) — it should look
   like random encrypted bytes/base64 gibberish, never a plain cookie jar you could copy to another
   machine and use.
6. **Multi-signal login detection — the "don't save mid-MFA" check (added 2026-09-22, worth
   testing now that it just shipped).** If you have access to any test site with a two-step login
   (password, then a texted/emailed code), record and run a skill against it. Trigger a fresh
   sign-in window. After entering just the password (before entering the code), watch that the
   runtime does **not** prematurely treat this as "signed in" — it should keep waiting through the
   code-entry screen and only close/save once you're truly past it. If you don't have a real 2-step
   site handy, at minimum confirm on the S1 single-step login that the window closes only after the
   post-login flash message is visible, not the instant the URL merely changes.
7. **Session expiry / mid-run re-login:** if you can force the target site to invalidate the
   session (log out from a different browser using the same account, or wait out its natural
   expiry), run the skill again. Expect: the runtime detects the failed/redirected state mid-run
   (not by simply seeing ANY navigate to a login-shaped URL — a workflow that legitimately records
   its OWN visit to a login page as a normal step must not falsely trigger this) and opens a fresh
   sign-in window automatically rather than failing the whole run outright.
8. **Two apps, one workflow (if you have a multi-app skill / workflow group set up):** confirm each
   app's session is tracked and re-validated independently — signing out of one app's session
   shouldn't force a re-login on the other app's steps.

### Leg D — Conxa Execute's own login

1. Launch the Conxa Execute desktop app fully signed out. Sign in the same way as Leg A (browser
   PKCE flow).
2. **Confirm it's genuinely independent of Build Studio:** with Build Studio ALSO open and signed
   in (or signed out — either way) on the same machine, sign into Execute. Expect no interference
   between the two — each app should open its own browser tab/callback and neither should
   accidentally grab the other's redirect (they listen on different local ports internally, so
   this should just work — the test is confirming it actually does, not just that it's supposed
   to).
3. Open Execute's workspace/context picker (Settings). Confirm it lists: your personal workspace,
   any real Clerk organizations you belong to, and any team workspace where someone granted you an
   Execute-only seat (see Leg D-continued below). Switch between two of them and confirm the app
   remembers your choice across a restart.
4. **Seat-grant claim (if you have access to grant one):** as a workspace admin in the Cloud
   Dashboard's Team screen, grant an Execute seat to an email address that is NOT already a member
   of that workspace. Then sign into Execute as that exact email for the first time. Expect: the
   grant is automatically recognized (no invite code to paste) and that workspace now appears in
   the picker without any extra step.

**Where failures go:** Leg A → `docs/Auth-and-Updater.md` §1.1 owners; Leg B → `app/api/security.py`;
Leg C → `runtime/auth_manager.js` / `browser.js` (session), `login_signals.js` (multi-signal
detection — this shipped 2026-09-22, treat any regression here as high priority); Leg D → §1.1a /
§13.4c (Execute auth + seat grants). Any session file found readable in plaintext, or any
cross-company/cross-account data leak in Leg B/D → stop and log immediately as a security issue,
same severity as WF-12's hard blockers.

---

## WF-19 — Conxa Execute: the desktop chat app and its in-app browser

**Sites:** any packaged skill for the browser-panel legs (WF-1's or WF-4's known-good skills work
well) · **Tools:** the Conxa Execute desktop app, signed in per WF-18 Leg D

Plain-language explainer: Conxa Execute is a separate small desktop chat app — think "a chat
window with Conxa's automated skills wired in as tools it can call," plus a live browser view so
you can actually watch what's happening on screen while a skill runs, instead of a headless
browser working invisibly in the background. It has no login system of its own for billing and no
"bring your own API key" mode — everything routes through the same Conxa Cloud account and the same
plan your workspace already pays for.

### Stage 1 — Basic chat + tool use

1. Open Execute, make sure you're signed in (WF-18 Leg D) and a workspace is selected.
2. Ask it something that requires it to look at your available skills, e.g. `What skills do I have
   available?` Expect a normal streamed reply — text appears progressively, not all at once — that
   correctly lists real skills from your account (cross-check against Build Studio's skill list).
3. Ask it to actually run one: `Run <skill name>` for a simple, known-good skill (e.g. WF-4 Leg B's
   saucedemo checkout, or anything short). Expect it to recognize this as a tool call, not just
   describe what it WOULD do — confirm a real browser action actually starts (see Stage 3 for
   watching it happen live).
4. If the model you're routed to is a "reasoning" model, watch for a distinct "Thinking…" block
   that appears and updates BEFORE the final answer text starts, then gets replaced by the real
   streamed answer. If your account isn't routed to a reasoning-capable model this may never
   appear — that's fine, not a bug; just confirm the plain answer still streams normally either way.

### Stage 2 — Personal vs. team workspace billing

1. With the personal workspace selected (Settings → workspace picker), send a chat message that
   costs at least a small amount of usage. Note any usage/credits indicator if visible.
2. Switch to a team workspace (one you're a real member of, or hold a seat grant for). Send another
   message. Confirm the app clearly shows which workspace is active (so you never accidentally
   spend a client's team credits while meaning to test on your own personal account, or vice
   versa).
3. If you have access to the Cloud Dashboard's usage/billing view for that team workspace, confirm
   the team's usage number went up after your Stage-2-step-2 message — proving the spend actually
   landed on the workspace you had selected, not always your personal one regardless of the picker.

### Stage 3 — The in-app browser panel

1. Trigger a skill run that needs a real browser (Stage 1 step 3, or any packaged skill with
   several visible steps). While it runs, watch Execute's own browser panel (not a separate
   Chromium window popping up outside the app).
2. Expect: you can SEE the actual page the skill is interacting with, live, inside the app's own
   panel — this is the runtime lending its own browser view to Execute rather than launching a
   second, separate browser. Confirm only ONE browser process is actually doing the work (check
   `Get-Process chrome,node` — you should not see two independent Chromium instances for one run).
3. **Login signals in the panel (shipped this week — 2026-09-22/23, worth extra attention right
   now):** run a skill whose target site needs a sign-in the runtime hasn't cached yet (same setup
   as WF-18 Leg C). While the sign-in window is open, check whether Execute's panel surfaces any
   indication of sign-in state (e.g. a "waiting for sign-in" / "signed in" signal, or an
   OTP-pending pause state) rather than the panel just sitting there with no feedback. Confirm the
   signal updates and clears once you actually finish signing in — a stuck "still waiting" state
   after you've clearly finished signing in is a bug, log it with the exact timing (this is the
   exact area the last few commits touched, so a fresh regression here is high-value to catch).
4. Close the panel or navigate away mid-run, then come back to it (if the UI allows switching away
   and back). Confirm the live view resumes/reconnects rather than showing a frozen last frame.

### Stage 4 — Local-only session storage

1. Have a real chat conversation with a few back-and-forth turns. Fully quit Execute and relaunch
   it. Expect: the conversation history is still there (saved locally on this machine).
2. If you have a second machine also signed into the same account, confirm the conversation from
   Stage 4 step 1 does **NOT** appear there — chat history is local-only by design, not synced
   across devices. This is expected behavior, not a bug — just confirm it matches the doc rather
   than silently drifting into an unexpected sync.

**What this proves on pass:** Execute's chat and tool-calling work end-to-end against real skills;
billing correctly follows whichever workspace is selected; the in-app browser panel shows the
REAL runtime browser (not a duplicate) and correctly reflects sign-in state as it changes; chat
history survives a restart but never leaves the machine.
**Where failures go:** Stage 1 → `conxa-execute/app/vendor/opencode/loop/run_turn.js` /
`execute_client.js`; Stage 2 → §13.4c (Execute seat/workspace billing); Stage 3 →
`runtime/app/host_browser.js` (server side) / `conxa-execute/app/electron/browser_panel.js` +
`browser_control.js` (client side) — a duplicate/second browser process is a hard blocker, log
immediately; login-signal regressions → the same `login_signals.js` owners as WF-18 Leg C;
Stage 4 → `conxa-execute/app/vendor/opencode/storage/storage.js`.

---

## What to watch for across ALL workflows (known gaps these tests exercise)

- **Tab-landing correctness** — next action after any switch lands in the expected tab (EXEC-5 #43).
- **Download/upload verification** — runtime waits for download completion; confirms upload success
  rather than fire-and-forget (EXEC-5 #31/#32).
- **Recovery tier ceiling** — more than a couple of Tier B escalations on stable demo sites = red flag.
- **Long-run stability** — degradation past ~step 25 (memory, stale frame refs, iframe offsets).
- **Compile step count** — truncation above 30 steps at compile, separate from runtime replay.
- **File identity in loops** — each upload carries ITS OWN iteration's file, not a loose match.
- **Branch discipline (WF-6)** — conditional steps never escalate or spend tokens; unknown step
  types skipped silently by old runtimes (the manifest floor's reason to exist).

## Confirmed-by-design limitations (behave AS DESIGNED, not worse)

1. CAPTCHA/OTP not automatable — graceful handoff (WF-2/E.13-14 analogs, WF-9 R7).
2. Canvas apps can't build identity — clean error, not hang (WF-9 R7).
3. Tier A deterministic only — no silent LLM fallback (verify zero proxy calls in WF-7).
4. `frame_enter`/`frame_exit` never retried — fail fast, no hang (WF-2 Segments D/E).
5. Cloud never compiles/executes — only telemetry/sync leaves the machine.
6. Tracking endpoint outside `/api/v1` — known tracked exception, not a discovery.
7. Host exe stays `--no-bytecode` — Playwright segfault in ANY runtime test → suspect this first.
8. Margin gate may refuse valid-looking clicks — false negatives ARE the design (fail-safe over
   wrong-click); distinguish "refused safely" from "broken".

New limitations found → results log → `TODO.md`. Doc contradictions → update `docs/TRD.md` /
`docs/App-Flow.md`. After your session append findings to `FIX.md`.
