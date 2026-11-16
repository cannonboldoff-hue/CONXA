# ✅ Workflows Successfully Tested Manually

This file is the **hall of fame**: every workflow that has actually been recorded, compiled, and
replayed end-to-end by hand (or validated against a real browser) moves here from
[`01-WORKFLOWS-TO-TEST.md`](01-WORKFLOWS-TO-TEST.md). Each entry says **what the new pass proves**
about what Conxa can do today.

When a workflow in `01-WORKFLOWS-TO-TEST.md` passes manually:

1. Move its section here (condensed, with date + evidence pointer).
2. Add a line under "What this proves Conxa can do now".
3. Update the dashboard below.

---

## 📊 Dashboard — what Conxa can do on the internet right now

> Hand-maintained estimate of how many real-world internet workflows Conxa can currently
> record → compile → replay reliably, based only on passes in this file.
> **2026-09-04 (no re-rate):** WF-5 Part A (interleaved download→upload identity, GitHub →
> demoqa.com) passed — see **P-10**. Run at n=5 pairs, not the n=20 the spec calls for; logged as
> closing this leg anyway by team decision, since `_bind_downloads_to_uploads` binds by filename
> regardless of pair count. Parts B (all-at-once/ZIP), C (cross-run leakage), and D (collision/popup
> probes) of WF-5 are still open, so this alone doesn't move the ceiling.
> **2026-09-04 (no re-rate):** a standalone 104-step, 6-tab, 6-host mega-workflow
> (`mega-workflow-fc8031f2`) replayed end to end with **zero recovery events** in 57.4s — see
> **P-9**. More than double P-1's step count, but its site mix (Wikipedia, demoqa, the-internet,
> uitestingplayground, saucedemo, local fixture) doesn't complete any single named WF-x leg
> start-to-finish, so it stands alone as a scale/reliability proof rather than closing WF-3 Leg B
> or WF-9.
> **2026-09-04 (no re-rate):** WF-2 Segment J — a real shadow-DOM web component (Shoelace's
> `<sl-button>`) now compiles to a durable, unique role-based selector instead of an ambiguous
> generic CSS class (see **P-8**; root cause and fix in `FIX.md` 2026-09-04). First proof on a
> modern shadow-DOM component library, but a single site/library, not broad site-class coverage —
> doesn't move the ceiling on its own.
> **2026-09-04 (no re-rate):** WF-2 Segment F — a jQuery UI autocomplete suggestion pick now
> compiles to the actual clicked option instead of a selector contaminated with an unrelated
> input's placeholder (see **P-7**; the same bug had already corrupted a Login button's selector
> on an earlier recording, `TODO.md` **BUILD-22**, now resolved with the correct root cause). A
> selector-integrity fix, not new site-class coverage, so this doesn't move the ceiling on its own.
> **2026-09-04 (no re-rate):** WF-2 Segment E (remainder) — a nested-iframe calendar widget's date
> pick now compiles into a real, reusable input instead of a frozen literal click (see **P-6**;
> root cause and fix in `FIX.md` 2026-09-04). Closes the last open piece of Segment E. A single
> supporting pass, not a re-rate on its own.
> **2026-09-04 (no re-rate):** WF-1 Leg F (native JS dialog replay — alert/confirm/prompt, typed
> answer echoed) passed clean after the `TODO.md` EXEC-29 fix (see **P-5**). Closes half of the
> "soft gap" called out below; hover-reveal (`/hovers`, EXEC-29a) is still open. A single-leg
> precursor pass, not a re-rate on its own.
> **Bumped 22%→23% (2026-09-03, same day):** WF-7's live-site self-heal suite (D.1–D.7) also
> passed in full, closing out the LAST unproven piece named in the prior bump note. D.6 is the
> first proof this session that relational/structural identity resolution — and resolver.js's
> ambiguous-candidates fall-through specifically — holds on a real live site under real position
> drift, not just a controlled fixture. 6 of 7 legs resolved at zero token cost (D.7 audit).
> **Why ~23%, not higher:** one flagship run proves *capability*, not *reliability* — SPA sites,
> bulk-file identity, cross-run re-run, and endurance are still unproven manually, and
> canvas/CAPTCHA/OTP/drag-drop are hard walls. Hover-reveal and native-dialog replay (`/hovers`,
> `/javascript_alerts`) are a separate, newly found *soft* gap (2026-09-03) — unlike the hard
> walls, both have working recorder and runtime support on paper, they just haven't been proven
> reliable end to end yet (`TODO.md` EXEC-29).
> **Bumped 20%→22% (2026-09-03):** WF-7's full recovery-cascade drill (R0–R5) passed end to end
> against real Chromium — the last individually-unproven piece named in the note above. This is
> the first live proof of the *agent-mediated* tier (Tier B: ranked candidate digest → verified
> override → resume), the Studio zero-token negative control, an honest decline with no
> plausible-match, and the retry-budget/stagnation guard — distinct from the Tier A dismiss-ladder
> (P-2) and Tier A drift-heal already proved. One honest caveat: R1 passed but not via the specific
> Tier 2 a11y path the drill originally intended to exercise — see `TODO.md` **EXEC-31**.
> **Bumped 18%→20% (2026-09-03):** the runbook follow-up run proved two things individually for
> the first time — a *live* Tier-A recovery pass (drift-healing a relabeled button, zero tokens,
> real Chromium, not just the dismissal-ladder P-2 already proved) and entity-specific cart
> binding (H-5/PROD-3, the sales-blocking safety test). A small step, not a re-rate — the harder
> gaps above are untouched.
> Cheapest upgrades: WF-5 bulk-file legs next, then WF-4 Leg A.

```
Internet workflow coverage:  ▓▓▓▓▓▓░░░░░░░░░░░░░░░░  ~23%

Last updated: 2026-09-04
Passed workflows:             5   (1 flagship, reconfirmed by a 2026-09-03 follow-up run
                                    + 1 full recovery-cascade drill + live self-heal suite (WF-7)
                                    + 1 bulk-file identity leg (WF-5 Part A, n=5)
                                    + 2 precursors/supporting)
Longest verified workflow:    42 steps · 6 tabs · 6 hosts · 5 domains (2026-08-25 flagship;
                                    2026-09-03 follow-up run was shorter by design — see below)
Zero-token recovery proven:   yes (Tier A dismiss ladder + Tier A drift-heal, real Chromium)
Agent-mediated recovery proven: yes (Tier B override + resume, real Chromium — WF-7)
Auth exclusion verified:      yes (no secrets in published bundles)

Headroom note: the proven SHAPE (multi-tab + file handoff + authenticated chains + full recovery
cascade) suggests a ~30% ceiling is reachable once WF-4/WF-5 pass — the gap is unproven
reliability, not missing architecture.
```

| Capability | Status | Proven by |
|---|---|---|
| Cross-domain download → upload handoff (`{{downloaded_file}}` binding) | ✅ proven | Mega-workflow 2026-08-25 |
| Multi-tab execution incl. `tab_switch` back to an earlier tab | ✅ proven | Mega-workflow + precursor 2026-08-23 |
| Site-opened popups + user-opened tabs in one run | ✅ proven | Mega-workflow 2026-08-25 |
| Authenticated chains on real SaaS (Render dashboard, Vercel) | ✅ proven | Mega-workflow 2026-08-25 |
| Zero-token overlay dismissal (Tier-1 known-pattern ladder + learning) | ✅ proven (real Chromium) | AV-5a walkthrough 2026-08-25 |
| Bulk ZIP download observed mid-run | ✅ download side only | Mega-workflow 2026-08-25 |
| Entity-specific cart action (right row removed, not first row — H-5/PROD-3 sales-blocker) | ✅ proven | Runbook follow-up 2026-09-03 |
| Slow-network headroom + honest mid-run session-death failure (no hang, no zombie process) | ✅ proven | Runbook follow-up 2026-09-03 (R3/R4) |
| Native JS dialog replay (alert/confirm/prompt, typed answer echoed) | ✅ proven | P-5, 2026-09-04 (`TODO.md` EXEC-29) |
| Hover-reveal replay (`/hovers`) | 🧪 beta — needs human review, not a hard wall | Runbook follow-up 2026-09-03; `TODO.md` EXEC-29a |
| Agent-mediated (Tier B) recovery: ranked candidate digest → verified override → resume, no wrong-decoy click | ✅ proven | WF-7 drill R3, 2026-09-03 |
| Studio deterministic ceiling: zero-token negative control (no agent tokens spent even when recovery is theoretically possible) | ✅ proven | WF-7 drill R2, 2026-09-03 |
| Honest decline when no plausible match exists (no wrong-guess click) | ✅ proven | WF-7 drill R4, 2026-09-03 |
| Retry-budget / stagnation guard stops runaway recovery spend, cost drops on repeat failure | ⚠️ not reproduced 2026-11-15 (21.3 s on every repeat, see 05 report) | WF-7 drill R5, 2026-09-03 |
| Identity survives real DOM drift (rename, restructure, delayed render, stale recorded position) | ✅ proven | WF-7 Suite D.1–D.3/D.5, 2026-09-03 |
| Never assumes stale/leftover page state is current — always verifies or self-navigates | ✅ proven | WF-7 Suite D.4, 2026-09-03 |
| Relational/anchor-based identity resolves correctly on a real live site under real position drift, including resolver.js's ambiguous-candidates fall-through with no per-item testid | ✅ proven | WF-7 Suite D.6, 2026-09-03 |
| Interleaved bulk download→upload identity binding (each upload traced to its own earlier download by filename, no cross-pairing) | ✅ proven (n=5) | P-10, 2026-09-04 |
| Dynamic/self-heal elements at scale · 20-file bulk identity at full spec volume · all-at-once (Shape B) bulk transfer · cross-run leakage · branch steps live replay | ⏳ pending | see `01-WORKFLOWS-TO-TEST.md` |
| 100+ step single compiled skill, 6 tabs/6 hosts in one continuous replay, zero recovery events | ✅ proven | P-9, 2026-09-04 |
| Nested-iframe calendar-widget date pick, parameterized as a real reusable input (not a frozen literal) | ✅ proven | P-6, 2026-09-04 |
| jQuery UI-style autocomplete suggestion pick (menu container correctly resolved to the clicked item, selector never contaminated by an unrelated preceding input) | ✅ proven | P-7, 2026-09-04 |

---

## Passed Workflow P-1 — EXEC-10 mega-workflow: long-chain, multi-tab, cross-domain, file transfer

**Date passed:** 2026-08-25 05:33 local (Build Studio Run Test)
**Shape:** one recorded skill — **42 steps, 6 tabs** (initial + 4 user-opened + 1 site-opened popup),
**6 hosts**, compiled clean (`compile_status: ok`), replayed successfully end to end.

### Segments (all passed in one run)

| Segment | Site(s) | What it exercised |
|---|---|---|
| Download files from bin A → ZIP download observed | `filebin.net` | Bulk-download side (Shape B download) |
| New tab → upload of that downloaded file | `demoqa.com/upload-download` | The core handoff — compiled to `{{downloaded_file_2}}` (W-2 binding), no `file_path` input supplied at all |
| Real `tab_switch` back to tab A (+ browser Back), second download | `filebin.net` | Tab-context landing on return to the initial tab |
| Third-domain tab → upload #2 | `the-internet.herokuapp.com/upload` | Second cross-domain upload, bound to `{{downloaded_file_3}}` |
| ~10-step authenticated deploy chain | `dashboard.render.com` | Length stress on a real authenticated SaaS |
| User-opened tab → site-opened popup → sign-in there | `vercel.com` → deployed app | User-opened-tab + site-opened-popup handling |

### Why this new pass matters — what it proves Conxa can do now

Before this pass, multi-tab replay and download→upload binding existed only as unit tests. This
single run proves Conxa can take **one human recording that spans five websites and six tabs** and
replay it faithfully with **zero agent tokens at runtime**:

- **Real file plumbing:** a file downloaded on one website is uploaded on another without anyone
  supplying a path — the runtime binds it automatically.
- **Correct tab memory:** actions land in the tab they were recorded in, even after switching back
  to a tab used minutes earlier.
- **Popups don't derail runs:** both tabs the *user* opened and a popup opened *by the site* are
  followed correctly, including signing in inside the popup.
- **Authenticated SaaS is drivable:** a ~10-step logged-in deploy chain on Render completed — this
  is the realistic enterprise shape, not just demo sites.
- **Scale:** 42 steps compiled clean and replayed clean — compile did not truncate and nothing
  degraded over the length of the run.

**Precursors (2026-08-23):** two published skills proved the halves separately first — a 9-step
filebin-only download→tab-switch→upload skill, and a 22-step Render→Vercel cross-domain multi-tab
skill with both a user-opened tab and a site-opened popup. Session log:
`docs/archive/sessions/session-ses_fd4e.md`.

**Reconfirmed + extended (2026-09-03):** a second, shorter recording against the same EXEC-10
shape re-ran the mutator/dynamic-identity/tab/upload mechanics clean and proved two capabilities
this flagship run never isolated on its own — entity-specific cart binding and live Tier-A
recovery — while also surfacing a real gap (hover/dialog replay). Not filed as its own numbered
pass since it didn't fully clear its own runbook; see the follow-up section below for the honest
pass/fail breakdown.

---

## Passed Workflow P-3 — WF-7: full recovery-cascade drill + live self-heal suite (Tier A + Tier B, real Chromium)

**Date passed:** 2026-09-03, in full — fixture replay matrix (R0–R5) and the live-site self-heal
suite (D.1–D.7). Driven directly against the real dev runtime (`~/.conxa-dev`) over the same stdio
MCP protocol Claude Desktop uses, with `recovery.log`/`runtime.log` evidence for every row.

### Rows (all six passed)

| # | Variant | What it proved |
|---|---|---|
| R0 | `clean` | Baseline: zero recovery events, correct click — compiled identity is not artificially weak |
| R1 | `?v=t2` (id survives, everything else drifts) | Correct click via primary durability-ranked signal resolution — see caveat below |
| R2 | `?v=t3`, Studio ceiling 2 | Deterministic `recovery_ceiling_reached` failure, **zero** agent tokens spent — the negative control |
| R3 | `?v=t3`, real MCP ceiling 4, 2 decoys present | `agent_recovery_requested` → ranked `candidate_index` digest → `agent_override_applied` → resumed and completed, exact target clicked, decoys never touched |
| R4 | `?v=gone`, no plausible match | Clean typed failure — no wrong-guess click when nothing on the page matches |
| R5 | `?v=gone` repeated, same session | `recovery_stagnant_stop` fired on the 3rd identical-fingerprint round; that response was plain text — cheaper than the armed rounds before it. (2026-11-15 re-run: not reproduced, every failure took 21.3 s.) |

**Caveat (honest, not a failure):** R1 passed by resolving through `resolver.js`'s primary
durability-ranked signal walk (a surviving `css-id` signal), not through the Tier 2 a11y recovery
cascade the row was originally designed to exercise — under the current architecture, no drift that
preserves even one compiled signal ever reaches the cascade. Filed as `TODO.md` **EXEC-31**; the
drill still proves what matters (drift healed with zero tokens), just via a different mechanism
than R1's row description names.

### Why this pass matters — what it proves Conxa can do now

Before this pass, the *agent-mediated* recovery tier (Tier B — the paid, LLM-reasoning path)
had never been proven end to end against a real running skill; only the zero-token Tier A ladder
(P-2) and a live Tier A drift-heal (2026-09-03 follow-up) had real-browser evidence. This drill
closes that gap directly:

- **The armed-agent round-trip actually works:** a failed step returns a ranked, indexed candidate
  list plus screenshots; a verified `candidate_index`/selector pick resumes the exact parked page
  and completes — never a blind guess, never `.first()`.
- **Decoys don't fool the ranking:** R3's fixture placed two decoy buttons alongside the real
  target; the correct one was clicked every time.
- **Zero-token discipline holds under real drift, not just in Studio's forced ceiling:** R2 proves
  Studio spends nothing even when recovery is theoretically possible; R4 proves the agent itself
  declines rather than guessing when nothing matches.
- **Cost discipline is real, not theoretical:** R5's stagnation guard measurably cut the cost of a
  repeated identical failure instead of re-sending the full payload forever. (Not reproduced on 2026-11-15: every repeat took 21.3 s; see the 05 report.)

One real bug found and fixed in the course of this drill (not a workflow gap): `resume_from`
without a matching `step_overrides` entry used to silently replay from a blank page with a
misleading error — fixed same-day, `TODO.md` **EXEC-32** (resolved).

### Suite D — live-site self-heal legs (D.1–D.7), same day

Run directly against the real dev runtime, same evidence standard as R0–R5 (`recovery.log` +
`runtime.log` for every row).

| # | What changed | Result |
|---|---|---|
| D.1 rename | Label changed, testid/id/aria-label untouched | ✅ zero recovery events — primary contract signal (testid) resolved it directly |
| D.2 move | Wrapped one level deeper in a new `<div>`, identity untouched | ✅ zero recovery events — a stale `recorded_context` (parent/index) didn't mislead the still-valid testid signal |
| D.3 past healing | Label + structure changed, testid **and** id dropped | ✅ full Tier B escalation: ranked digest → verified `candidate_index` → resumed and completed on the single non-decoy candidate |
| D.4 wrong page | Browser left mid-failure on an unrelated page from a prior run | ✅ the next fresh call self-navigated to the correct URL and completed cleanly — never assumed the leftover page was current |
| D.5 slow render | Element rendered 5s late (default action timeout is 2.5s) | ✅ genuinely hit `tier2_a11y` (zero tokens) — waited past the default timeout instead of failing prematurely; the first real Tier-2 a11y firing all session |
| D.6 live-site position shift | See below | ✅ (see below) |
| D.7 cost audit | See below | ✅ (see below) |

**D.6, adapted:** the spec's live target (`demo.owasp-juice.shop`) has since dropped its sort
control entirely (checked the DOM directly — no sort UI of any kind), and separately turned out to
crash specifically for a fresh/cookie-less automated session (confirmed via 4 reproducible attempts
through the runtime vs. a normal browser tab loading it fine) — both site-side, not a Conxa gap.
Substituted **`automationexercise.com/products`**, a stable, purpose-built automation-testing site
with the same real shape the leg needs: all 34 "Add to cart" links share identical role/text/class
with no per-item testid, so a specific product is only identifiable via its own name anchored to a
container (`.productinfo:has-text("Winter Top") a.add-to-cart`). Ran the compiled step against two
real states of the live site — the default listing (target at index 4) and a search-filtered view
(`?search=Top`, target shifted to index 1, verified via direct DOM inspection both times — both
states independently confirmed reachable and correctly cart-verified via a manual click in a
separate browser tab). **Both passed with zero recovery events** — role/text signals were
non-unique across 34 identical buttons (the first live, non-fixture exercise of resolver.js's
ambiguous-candidates fall-through this session) and correctly fell through to the name-anchored
structural signal, which resolved to the one correct product regardless of position.

**D.7 cost audit:** 6 of 7 legs resolved at **zero token cost** — either directly via a durable
primary signal (D.1, D.2, D.6) or via genuine zero-token Tier 2 a11y recovery (D.5). Only D.3, the
one leg deliberately engineered to strip every deterministic signal, needed the paid Tier B agent
round — exactly once, resolved in a single round-trip. This is real, live-browser evidence for the
"most real-world breakage heals at zero marginal cost" claim, not a theoretical one.

**Full pass — what it proves:** Tier A is a real free self-heal path under actual DOM drift, not
just the fixture's synthetic variants; the ceiling contract holds in both directions; Tier B ranks
honestly under decoy-free ambiguity; a stale recorded position never misleads a still-valid
contract signal; the runtime never assumes stale page state is current; and identity genuinely
survives a live site's own real reordering, not just a controlled fixture's.

---

## Follow-up run (not a hall-of-fame pass) — `03-ONE-WORKFLOW-RUNBOOK.md`, 2026-09-03

**Not filed as "Passed Workflow P-#"** — per this file's own maintenance rule, that title is
reserved for a *successful end-to-end run*, and this one wasn't (hover + native dialogs failed).
Logged here anyway because it re-confirms most of P-1's shape on a fresh recording and surfaces
two capabilities — entity-specific cart targeting and timeout/mid-run-death resilience — that
were never individually proven before, alongside a newly-found soft gap. Both new capability rows
and the gap are already reflected in the dashboard above.

**Date run:** 2026-09-03 (Build Studio, manual recording + replay against the runbook in
[`03-ONE-WORKFLOW-RUNBOOK.md`](03-ONE-WORKFLOW-RUNBOOK.md)).

**What passed:** Segments A–D (Wikipedia baseline, mutator drift-heal, dynamic identity,
the-internet marathon) minus hover and native dialogs, Segment E (demoqa forms + upload
handoff), and Segment F (saucedemo entity-specific cart removal). Replay resilience checks R3
(slow network, `CONXA_ACTION_TIMEOUT_MS=1200`) and R4 (mid-run session death) both came back
clean per the recovery/server logs — matching what §PHASE 3 of the runbook expects.

**What did not pass or was not exercised in this take:**
- Segment D's `/hovers` and `/javascript_alerts` steps did not replay reliably, despite the
  recorder and runtime both having real support for hover reveals and native dialogs (see
  `TODO.md` **EXEC-29**, new 2026-09-03).
- Segment D's `/drag_and_drop` step — reconfirmed as a hard limitation, unchanged from the
  dashboard note below (skipped with a warning at build time per `FIX.md` 2026-08-26).
- Segment D's iframe/nested-frames sub-steps and Segment G (SPA journey, `demo.owasp-juice.shop`)
  were not captured in this particular recording — both were validated working in an earlier,
  separate manual pass, so this is scope-of-this-take, not a regression.
- Segment H (chaining source, `computer-database.gatling.io`) — the site itself failed to load
  during this run; read as external-site availability, not a reproduced Conxa-side bug.

**Why this matters:** confirms the runbook's core mechanics (drift-healing, dynamic identity,
multi-tab forms/upload, entity-specific cart binding) hold up in a real manual run, and that the
runtime's slow-network and mid-run-death resilience (H-2/H-3 shapes) are solid. It also drew a
sharper line around two remaining real gaps — hover and native-dialog replay — that looked closed
on paper (recorder support landed 2026-08-26, runtime handlers already exist) but aren't proven
end to end yet. Full detail: `TODO.md` **TEST-12**'s 2026-09-03 update.

---

## Passed Workflow P-5 — WF-1 Leg F: native JS dialog replay (alert/confirm/prompt)

**Date passed:** 2026-09-04 10:17 local (Build Studio sandbox replay, run `r_mtmsuy52_ga9ei`,
session `7fb53d62-bd70-44fc-a957-0267a600f526` in `~/.conxa-build-studio-dev`).

**Shape:** S1 `/javascript_alerts` — click "Click for JS Alert" → accept; click "Click for JS
Confirm" → accept; click "Click for JS Prompt" → type answer → accept. 7 compiled steps.

**Result:** `steps_executed:7`, `execute_success`, zero recovery events in `recovery.log`, zero
LLM calls. Clean end to end — the earlier hang (`r_mtm1ttc8_ilx68`, 2026-09-03) and the follow-up
dropped-answer/timeout failure (`r_mtmsrh2t_iaqah`, 2026-09-04 10:14) do not reproduce.

**Why this matters:** confirms the `TODO.md` **EXEC-29** fix — reordering recorded dialog events
by timestamp, exempting dialog markers from duplicate-collapse, and pre-arming the dialog handler
one step ahead to avoid the replay deadlock — actually closes the gap live, not just in the fix's
own reasoning. Native JS dialogs graduate out of the "soft gap" bucket; hover-reveal (`/hovers`)
remains open separately under `TODO.md` **EXEC-29a**.

---

## Passed Workflow P-6 — WF-2 Segment E (remainder): nested-iframe datepicker, parameterized (E.2)

**Date passed:** 2026-09-04 (Build Studio, session `e12eb59d-7000-416a-ae1a-9c440a9a3282` in
`~/.conxa-build-studio-dev`).

**Shape:** S4 (jqueryui.com/datepicker) — one iframe deep: open the date field, navigate next
month ×2, click day 15, compile, replay.

**What was fixed en route:** the recording compiled and replayed the exact clicked date correctly
from the start, but the date was baked in as a literal, not exposed as an input — jQuery UI's day
cells carry no `data-date`/`aria-label`/`title` at all, only the bare day number as text, which
`bridge.js`'s date-cell detector didn't recognize. Fixed by adding a text-based fallback (day
number + the cell's ancestor `data-month`/`data-year`, or the grid's header text) with an
overflow/disabled-cell exclusion so an adjacent month's greyed day is never mistaken for the
picked one — see `FIX.md` 2026-09-04.

### What was confirmed

- The compiled skill's iframe click run collapsed into one `action: "date_pick"` step
  (`handler_hints.control_kind: "date_picker"`, `input_binding: "due_date"`) instead of raw
  literal clicks.
- The skill's `inputs` array now declares a real `{"id": "due_date", "type": "date", "default":
  "2026-07-15", ...}` — a caller can now pass a different date at execution time.
- New regression coverage added: `conxa-cloud/tests/test_recorder_bridge_js.py`'s three
  jQuery-UI-shaped fixtures (bare-text day cell via ancestor attrs, via header-text fallback, and
  overflow-cell exclusion) — 56/56 passing across `test_recorder_bridge_js.py` +
  `test_date_picker_collapse.py`.

### Why this passes matters

Closes the last open piece of WF-2 Segment E named in `01-WORKFLOWS-TO-TEST.md`, and generalizes
past jQuery UI specifically: any calendar widget whose day cells carry no machine attribute and no
aria-label (bare visible day-number text only) now compiles into a real, reusable date input
instead of a frozen recording of one specific day.

---

## Passed Workflow P-7 — WF-2 Segment F: autocomplete/typeahead selector integrity (E.6)

**Date passed:** 2026-09-04 17:20 local (Build Studio, session `cc93a956-e6f3-45ec-a7cd-5c8cfb4382b1`
in `~/.conxa-build-studio-dev`; skill `wf-2-s-f-0580ba31`).

**Shape:** S4 (`jqueryui.com/autocomplete`) — type into the tags field, pick the "JavaScript"
suggestion; plus S3 (`demoqa.com/select-menu`) — pick a value from a `react-select` widget.
8 compiled steps.

**What was fixed en route:** an earlier attempt on this same segment (`WF-2-S-F`, session
`3651c833`) compiled a click that could never match at replay. Two independent bugs, both
root-caused and fixed:

- **Recorder (`bridge.js`):** jQuery UI's menu widget puts `tabindex="0"` on the `<ul>` itself
  while each item's clickable node is `tabindex="-1"` — the recorder's container-vs-item walk
  accepted the whole `<ul>` as the click target instead of the clicked `<li>`, so the recorded
  target text was "Java JavaScript" (both options concatenated) instead of "JavaScript".
- **Packager (`skill_package_builder_saved_skill.py`):** a blind rewrite armed on any `type` step
  whose value was a bare input placeholder and unconditionally overwrote the *next* click's
  selector with it, with no check the two steps were related. This produced
  `selector: text="{{tags}}"` on the option-pick click. The same mechanism, on an unrelated earlier
  recording, had already turned a Login button's selector into `text="{{user_password}}"` —
  `TODO.md` **BUILD-22**, whose original root-cause guess (an index desync) was wrong; this is the
  real mechanism, now resolved. Both rewrite passes were deleted rather than patched.

See `FIX.md` 2026-09-04 for the plain-language writeup.

### What was confirmed

- `last_test_status: passed`, no error, replayed with non-default inputs
  (`tags: "javascript"`, `react_select_3_listbox: "Mr."`).
- The compiled option-pick step is now `intent: select_autocomplete_suggestion`,
  `selector: text="JavaScript"`, confidence 0.85, zero warnings — matching its own identity bundle,
  not contaminated by the preceding `type` step's placeholder.
- No step in the compiled skill's `execution.json` or `recovery.json` contains an unrelated `{{`
  placeholder.
- New regression coverage: `conxa-cloud/tests/test_recorder_bridge_js.py`'s menu-container-item
  test, and `conxa-cloud/tests/test_saved_skill_selector_integrity.py` pinning the BUILD-22 fix —
  full suite (1164 passed, 1 pre-existing skip) green.

### What did not pass or was not exercised

- **The `select-menu` "Select Option" field and second `react-select` widget** from the original
  recording attempt didn't reappear in this fresh recording (only one `select_option` step was
  captured) — narrower scope than the segment's own instructions describe, not a regression.
  Separately, an *orphaned-input* variant of this same demoqa page (a required input with no step
  consuming it) was found during the investigation and filed as `TODO.md` **BUILD-24**.

### Why this pass matters

Confirms the click-target and selector-integrity fixes hold on a real fresh recording, not just in
unit tests — and, independently, root-causes and closes out `TODO.md` **BUILD-22**, a
credential-shaped selector bug that had been open since 2026-09-02.

---

## Passed Workflow P-8 — WF-2 Segment J: Shadow DOM click identity (C.6)

**Date passed:** 2026-09-04 (Build Studio, workflow `wf-2-s-j-218b116d`, session
`bd0accba-8262-4def-b06a-46e54e7ef1a6` in `~/.conxa-build-studio-dev`).

**Shape:** `https://shoelace.style/components/button` — a real interactive shadow-DOM web
component library (Shoelace's `<sl-button>`, a native `<button>` inside an open shadow root).
Recording clicked a promo banner's dismiss button, scrolled, then clicked the "Primary" variant
button demo. 3 compiled steps.

**What was fixed en route:** an earlier attempt on this same segment (`WF-2-S-J`, session
`7d5fe8ef`) compiled with `compile_min_confidence: 0.21` and failed replay with "Ambiguous element
resolution (no signal cleared uniqueness gate)". Root cause: the shadow-internal `<button>` a click
resolves to has no visible label of its own — Shoelace projects it in from the light-DOM host — so
the recorder captured empty `inner_text`/`aria_label`, and the compiler fell back to a generic CSS
class shared by every button of that variant on the page. A uniqueness check that can't see inside
shadow roots (`_count_css` runs against a plain HTML snapshot) wrongly stamped that non-unique
selector `unique_at_compile=True` anyway, so it shipped instead of being rejected — and the real
resolver, with live shadow access, correctly refused to guess between the actual matches at replay.
Fixed in `bridge.js` (falls back to the shadow host's own text/aria-label when the clicked
element's own are empty, and now populates the previously-unused `shadow_path` field) and in
`identity_bundle.py`/`selector_filters.py`/`build.py` (a shadow-sourced structural CSS signal's
`unique_at_compile` stamp is no longer trusted). See `FIX.md` 2026-09-04 for the plain-language
writeup.

### What was confirmed

- `last_test_status: passed`, no error.
- The real target step (clicking "Primary") compiled as `intent: click_primary_button_example`,
  `primary_selector: role=button[name="Primary"]`, `selector_confidence: 0.95`, zero warnings —
  the accessible name ("Primary") was correctly recovered and a unique role-based selector chosen,
  not the old generic CSS-class fallback.
- New regression coverage:
  `test_recorder_bridge_js.py::test_shadow_button_with_empty_own_label_falls_back_to_host_text` and
  `test_element_fingerprint.py::test_css_structural_signal_not_trusted_unique_when_shadow_sourced`
  reproduce the exact failure mechanism and pin the fix — full suite (1166 passed, 1 pre-existing
  skip) green.

### What did not pass or was not exercised

- The accidental first click (the promo banner's "No, thanks" button) still compiled at low
  confidence (0.21, structural CSS only) — its `inner_text` was correctly captured ("No, thanks",
  no shadow root involved for this one), but its role-based candidate was independently dropped as
  "resolves to nothing" against the recorded snapshot, most likely because the banner hadn't
  finished mounting/animating in at the moment the snapshot was taken. Replay still succeeded here
  (only one matching button was actually present), but this step's low-confidence selector is a
  separate, pre-existing gap (snapshot-timing vs. a real animated element), not a shadow-DOM issue
  — not chased further per this segment's scope.
- The `shadow_path`-population and `css_shadow_unverifiable` compiler guard weren't directly
  exercised by this run's winning step — the click landed on the light-DOM host directly (a quirk
  of how the browser resolves clicks on slotted text), so no shadow boundary was actually crossed
  in `composedPath()` this time. Both are covered by the unit tests above, which construct the
  shadow-crossing case directly.

### Why this pass matters

First real proof that Conxa can identify and durably select an element rendered by a modern
shadow-DOM web component library (Shoelace, and by extension similar libraries like Ionic or
Vaadin) — previously an untested, then a confirmed-broken, class of site.

---

## Passed Workflow P-2 — Tier-1 known-pattern dismissal ladder + learning store (AV-5a)

**Date passed:** 2026-08-25 (executed programmatically against real headless Chromium using
[`fixtures/dismiss-fixture.html`](fixtures/dismiss-fixture.html) and the same ladder code path).
Unit tests cover the mechanics offline; this pass proved them against a live page end-to-end.

### What was confirmed

- R1–R4 click sequences exactly as predicted; R5 (bespoke modal) failed cleanly with no free pass.
- The learned store (`learned_overlays.json`) gained only `localhost` entries.
- **R2→R3 learning transition:** run 1 clicked a dead-end decoy candidate then continued to the
  real accept button; run 2 skipped straight to the learned winner — the decoy was never clicked.

### Why this new pass matters — what it proves Conxa can do now

- **Cookie banners and famous consent popups are dismissed for free.** Escape plus a bounded
  known-pattern list resolves overlays at **zero LLM tokens**, and the runtime *remembers* which
  button worked per site, getting faster on every repeat visit.
- Two implementation facts validated that mocks would have missed: the ladder clicks **every
  present candidate in order** (a dead-end candidate must not stop it), and per-candidate probe
  budget needs ~1.2 s headroom on a cold page.

---

## Passed Workflow P-4 — WF-2 Segment B (remainder): moved element, role/text-only identity (C.4)

**Date passed:** 2026-09-03 (Build Studio Run Test, against
[`fixtures/recovery-fixture.html`](fixtures/recovery-fixture.html)'s new `moved` variant).

Recorded a single click on the fixture's "Buy Now" button in its default top-left position (full
identity: `id`/`data-testid`/`aria-label`/text all present), compiled, then replayed with
`?v=moved` — every attribute signal (`id`, `data-testid`, `aria-label`, `class`) stripped, only
role (`button`) + visible text (`Buy Now`) left, and the element relocated into an unrelated
`nav.sidebar` container (36px collapsed strip, pinned bottom-right) instead of its recorded
top-left parent.

### What was confirmed

- `run_success`, 2/2 steps executed, correct element clicked (`server.log`:
  `GET /__log?moved:target`) — not a miss, not a wrong click.
- **Zero recovery-cascade events** in `recovery.log` for this run. The compiled `identity_bundle`
  already carried `role` and `text_based` as durability-ranked signals (from the clean recording),
  so `resolver.js`'s primary signal walk found the moved element directly — `cascade.js` never
  engaged. Same shape as **EXEC-31** (`TODO.md`): a fixture variant that preserves even one
  compiled signal resolves before the recovery cascade gets a chance to fire, so this proves
  role/text durability at primary resolution, not tier-based healing specifically.

### Why this passes matters

Confirms role + visible text alone — with every attribute and structural-position signal gone —
still correctly re-locate a recorded element after a real, drastic DOM move (not just one level of
nesting, as D.2 already covered). Closes the last open piece of WF-2 Segment B named in
`01-WORKFLOWS-TO-TEST.md`.

---

## Passed Workflow P-9 — 104-step, 6-tab, 6-host mega-workflow: scale + zero-recovery reliability

**Date passed:** 2026-09-04 19:12–19:13 UTC (2026-09-05 00:42–00:43 local), Build Studio sandbox
replay, run `r_mtnbzg5a_3ipkk`, skill `mega-workflow-fc8031f2` (session
`102b022c-e74b-4915-a96e-b9b18f8cf8c6`) in `~/.conxa-build-studio-dev`.

**Shape:** one recorded skill — **104 compiled steps, 6 tabs** (`tab_open` ×5 + 1 site `popup`),
**6 hosts** (`en.wikipedia.org`, `demoqa.com`, `the-internet.herokuapp.com`,
`uitestingplayground.com`, `www.saucedemo.com`, a local fixture server), ~412s original recording.
Step mix: 21 navigate, 21 focus, 21 click, 14 type, 7 keyboard_shortcut, 5 tab_open, 5 scroll, 2
select_option, 2 browser_back, 1 each of upload / set_radio / set_checkbox / popup /
download_observed / date_pick. No iframes in this recording.

### What was confirmed

- `execute_start` declared `total_steps:104`, `max_recovery_tier:2` (Studio ceiling); runtime log
  shows every step advancing cleanly to `steps_complete:104` in **57.4s** wall time; `execute_success`.
- **Zero entries in `recovery.log`** for this run_id across all 104 steps — no drift, no self-heal,
  zero LLM calls.
- The workflow's `last_test_status` is `"passed"`, `last_test_error: null`.

**Caveat (stale field, not a functional issue):** the workflow's own KV record still carries
`compile_status: "failed"` — left over from an earlier compile attempt
(`compile_min_confidence: 0.315`, 6 steps with warnings) that predates this run. The
`execution.json` that actually executed is a freshly regenerated 185 KB / 104-step package, and
the run itself completed clean; the field just wasn't refreshed by the successful recompile+test
cycle that produced this pass — worth a `TODO.md` entry if it recurs on another workflow.

### Why this pass matters

The largest single compiled-and-replayed step count proven in this repo so far — more than double
P-1's 42 steps — completing in under a minute with zero recovery events across 6 different hosts
and 6 tabs in one continuous replay. This is the first real evidence toward the "at scale, with no
degradation" claim `01-WORKFLOWS-TO-TEST.md`'s dashboard still marks pending, though its site mix
cuts across several named WF-x site lists rather than fulfilling any one leg's specific script
(WF-3 Leg B's saucedemo-only loop, or WF-9's mutator/jQuery-UI/Juice-Shop/computer-database mix),
so it stands alone as a scale/reliability proof rather than closing out either of those items.

---

## Passed Workflow P-10 — WF-5 Part A: interleaved download→upload identity chain (n=5)

**Date passed:** 2026-09-04 19:44 UTC (2026-09-05 01:14 local), Build Studio sandbox replay, run
`r_mtnd3rru_83gtj`, skill `wf-5-l-a-35f14a1f` (workflow `35f14a1f-93be-45df-9536-772c82d4a248`,
session `2c54060b-ebaf-4da9-b1e1-23057b25a37c`) in `~/.conxa-build-studio-dev`.

**Shape:** download source `github.com/github/gitignore`, upload target
`demoqa.com/upload-download`. Recorded 5 download→upload pairs interleaved (not batched), in this
order: `AL.gitignore`, `Actionscript.gitignore`, `Ada.gitignore`, `AdventureGameStudio.gitignore`,
`Agda.gitignore`. Compiled to 40 steps across 2 tabs (5 `download_observed`, 5 `upload`, plus
navigate/click/tab_switch steps around each pair). Compile: confidence 0.95, 0 warnings.

### What was confirmed

- Each `upload` step's bound value is a distinct, sequentially incrementing placeholder
  (`{{downloaded_file}}`, `{{downloaded_file_2}}` … `{{downloaded_file_5}}`), one per upload in
  recorded order — `_bind_downloads_to_uploads` bound every upload to its own preceding download,
  never a duplicate or a neighbor's file.
- Replay logged `run_success`, `steps_executed:40`, matching the compiled step count. Zero entries
  in `recovery.log` for this run — zero Tier A/B recovery events, zero LLM calls.
- The replay's own per-run download folder (`sandbox/data/runs/r_mtnd3rru_83gtj/`) contains exactly
  the 5 expected files, byte-identical (`cmp`) to the files recorded in the original session's
  download folder — confirms the replay re-downloaded the correct real files rather than reusing a
  stale/cached copy.
- Workflow record: `last_test_status: "passed"`, `last_test_error: null`, `signed_off: true`.

### Deviation from spec, logged transparently

`01-WORKFLOWS-TO-TEST.md` specs this leg at 20 pairs; this run used 5. The binding mechanism keys
strictly on exact recorded filename per pair and has no dependency on pair count, so a clean 5-pair
result is real evidence the mechanism works — but it is not the same strength of evidence as a
clean 20-pair result would be, since a lower count gives fewer chances for an off-by-one or
wrong-neighbor bind to surface. This leg is recorded as **closed at n=5** by team decision; if a
wrong-pairing bug that only appears at higher volume turns up later, re-open this item and re-test
at the full spec'd 20.

### Where this doesn't reach

Upload-side identity is confirmed via the compiled binding metadata and the download-side
byte-match, not a demoqa-side readback of which filename it actually received (no such API exists).
WF-5 Part B (Shape B, all-at-once ZIP transfer), Part C (cross-run leakage/retention), and Part D
(same-name collision + popup-download probes) remain open.

---

## Passed Workflow P-11 — WF-1 Leg C: form fill to approved, publish-ready (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF1 C demoqa text box r3`,
`ad31ae0a-7422-46b9-b7e6-6ab7163b8e7c`, group "E2E 2026-11-16", passing run `r_mwiava3f_nek4m`).
Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** S3 `demoqa.com/text-box` — Full Name, Email, Current Address, Permanent Address, Submit;
the page echoes the four values. Driven through Studio's normal controls only: create, record,
questions answered on purpose, test, approve.

**What was confirmed:** 11 actions recorded; 13 steps, 3 checks after the answers; all four typed
values became run inputs with the recorded values prefilled; the test passed with no "couldn't
confirm" warning and the Submit step proven; the echo block showed all four values during the
replay (read from the page); the workflow reached **Approved** with Publish offered.

**Fixed en route (root causes, all general):** the account-privacy check refused a reserved sample
address ("name@example.com") and the compile scrub missed the recorder's own selectors; a typed
"how we know it worked" check was put on a trailing scroll step; email/password inputs had two
names; "words on the screen" meant an exact whole-line match; and a person's own words check could
never prove a "send" step on an app that answers in the page without a server request.

**Why this pass matters:** first workflow taken all the way to the approved, publish-ready state under
the strict end-to-end rule, and it proves Conxa can confirm work on apps that never contact a server
(client-side forms) by the words the author said mean "it worked".

---

## Passed Workflow P-12 — WF-1 Leg A: Wikipedia search, link, Back, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF1 A wikipedia control r4`,
`71600c18-defc-4259-8371-a1d31e44e3e2`, group "E2E 2026-11-16", passing run `r_mwik9npg_iyoiv`).
Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** S11 `en.wikipedia.org` — click the search box, type "Ada Lovelace", Enter, click the lead
link "analytical engine", browser Back. 6 compiled steps: navigate, focus `#searchInput`, type
`{{search_wikipedia}}`, Enter, click `role=link[name="analytical engine"]`, browser_back with a
required "the search words appear" check.

**What was confirmed:** Back is recorded and replayed; no click step leans on a generated id alone
(the leg's original purpose); the test passed with every step confirmed and no warning; the run
ended on the Ada Lovelace article (`execute_success`); the workflow reached **Approved**.

**Fixed en route (root causes, all general):** the recorder described an element as it looked after
the press, and Wikipedia changes its search box when pressed; pressing Enter in a form was recorded
twice (the key and the browser's own click on the submit button); and the compiler decided "same
element" from the element's description, so a box the page swapped for a new one on focus produced
an invented focus step from the wrong description, and the review pass archived the right one. Now
the recorder tracks each element with a token that survives the page changing or replacing it.

**Why this pass matters:** modern sites routinely rebuild a control the moment it is touched (search
boxes, rich editors, menus). This is the first proof Conxa records and replays such a control
correctly, from a fresh page, with no hand fix.

---

## Passed Workflow P-13 — WF-1 Leg D: site-opened tabs and a switch back, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF1 D new tab round trip r3`,
`fe1a05f0-d5bd-4433-977d-4bf8bdbb71bd`, group "E2E 2026-11-16", passing run `r_mwima47v_024b1`).
Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** S1 `the-internet.herokuapp.com/windows` — click "Click Here" (the site opens `/windows/new`
in a new tab, heading "New Window"), switch back to the first tab in Studio's tab strip, click
"Elemental Selenium" (opens `elementalselenium.com` in another tab). 8 compiled steps across 3 tabs:
navigate, click, tab_open, popup, tab_switch, click, tab_open, popup.

**What was confirmed:** both site-opened tabs were recorded inside Studio's own browser and compiled
as their own steps; the switch back was compiled and the second click ran on the original tab; the
first test passed with all steps confirmed and no warning, with no fix applied; the workflow reached
**Approved**.

**Fixed en route (root causes, both general):** Build Studio closed outright whenever a website
opened a new tab during a recording (the debugger was attached to the tab before its first page,
which crashes Electron), and "The screen changes" could never pass for a click whose effect is a new
tab (only the clicked page was compared).

**Why this pass matters:** "open in a new tab" links, sign-in popups and share windows are everywhere;
before this, recording any of them in Studio's own browser crashed the app.

---

## Passed Workflow P-14 — WF-1 Leg E: download → upload handoff across sites, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF1 E download upload handoff`,
`40a91392-6eb2-4ba9-bf81-b052cdc7d5f8`, group "E2E 2026-11-16", passing runs `r_mwimmsso_dpq42` and
`r_mwimnz8n_cqnvm`). Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** S1 `the-internet.herokuapp.com/download` (click `some-file.txt`) → a new tab opened by the
person → S3 `demoqa.com/upload-download` (upload that file). 6 compiled steps: navigate, click,
download_observed, tab_open, navigate, upload with value `{{downloaded_file}}`.

**What was confirmed:** the upload is bound to the file this same run downloads (no `file_path`
input; the test form asked for nothing); each replay downloaded a fresh `some-file.txt` into its own
run folder; demoqa echoed `C:\fakepath\some-file.txt` during the replay; the test passed twice with
every step confirmed and no warning; the workflow reached **Approved**. No fix was needed.

**Why this pass matters:** reconfirms the download→upload handoff (P-1, P-10) under the strict rule,
through Studio's own browser with a person-opened tab, from recording to publish-ready.

---

## Passed Workflow P-15 — WF-1 Leg F: alert, confirm and prompt replayed in Studio, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF1 F js dialogs r2`,
`ed9ffcbb-b0b8-4fca-b3fc-5ff30cf4ecd8`, group "E2E 2026-11-16", passing run `r_mwiodbms_96c7f`).
Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** S1 `the-internet.herokuapp.com/javascript_alerts` — alert (OK), confirm (OK), prompt (typed
answer, OK). 7 compiled steps; the prompt answer is the input `dialog_answer`.

**What was confirmed:** replayed with a different answer than recorded (`Conxa second`), the page
showed "You entered: Conxa second"; the prompt step took 3.4 s; the test passed with no warning; the
workflow reached **Approved**.

**Fixed en route (root cause, general):** Electron, which Studio's test browser and Conxa Execute are
built on, does not implement `window.prompt()`, so a recorded prompt could never be answered on
replay there (P-5 passed in September in a separate Chromium, before tests moved into Studio's own
browser). In a host-owned browser the runtime now arms the page with the recorded or given answer,
one-shot, the way the recorder already did.

---

## Passed Workflow P-16 — WF-1 Leg B: signed-in start through a group sign-in, logout, no password in the package (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF1 B secure area logout r4`,
`ad722abe-24fc-4af8-92d1-2dd79357502e`, group "E2E Signin4 2026-11-16", passing runs
`r_mwiwpmhv_7pfdq` and `r_mwiwrrlp_bk0nw`). Full diary:
[`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** S1 `the-internet.herokuapp.com`. The group's sign-in was done once in Studio's sign-in
browser (nothing typed there is recorded). The workflow starts on `/secure` signed in and clicks
Logout. Compiled to 2 steps. Checks on the Logout step: the address changes, it lands on `/login`,
and "You logged out of the secure area!" appears.

**What was confirmed:**
- The group's saved sign-in still opened the Secure Area after the recording and after both tests.
- Two test runs passed, each ending in `execute_success` on `/login`, with no "sign-in expired" and
  no "couldn't confirm".
- The workflow reached **Approved**.
- Credential check: `SuperSecretPassword` appears in no file of the built package or its staged
  copy. The user name appears only as on-screen page text (filed as AUTH-24).

**Fixed en route (root causes, general):**
1. A recording that ended signed out overwrote the group's saved sign-in. It is now kept only
   after the sign-in check confirms it.
2. "Run test" now rebuilds when the group's sign-in setup changed since the last build.
3. The recorder lost where a click led whenever the click loaded a new page. It now writes the
   landing onto that click.
4. At the address a step was recorded landing on, a sign-in page or a "signed out" message is that
   step's result, not an expired sign-in.

---

## Passed Workflow P-17 — WF-2 Segment H: hover-revealed link, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF2 H hover profile r2`,
`c5f4f1bd-cb57-41be-bc91-35a27c81aa2b`, group "E2E 2026-11-16", passing runs `r_mwixmfv6_lkgzs` and
`r_mwixo39j_8q5lq`). Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** S1 `/hovers`. Rest the mouse on picture 2 with real mouse movement, then click its
"View profile". Compiled to 2 steps. The click is tied to the "name: user2" caption, and the hover is
not its own step: the runtime reveals the hidden link the way a person would.

**What was confirmed:**
- Answered with offered choices only, nothing typed.
- Two test runs each went `/hovers` → `/users/2`, watched live, never another profile, each
  ending in `execute_success`.
- **Approved.** This closes EXEC-29a's "hover doesn't replay reliably" for this page.

**Fixed en route (general):**
- A which-one word in a question ("the third one") that the browser's own record doesn't show now
  falls back to Studio's plain wording. The video reader had misread the picked item.
- The address check's tick box now shows the address instead of a blank.

---

## Passed Workflow P-18 — WF-2 Segment D: a click inside an iframe, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF2 D iframe tabs r4`,
`30b9b831-817c-4341-8a3d-84d3ef5df716`, group "E2E 2026-11-16", passing runs `r_mwj0ky0k_tg6j2` and
`r_mwj0mn3h_arwrr`). Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** `jqueryui.com/tabs/`. The demo runs in `iframe.demo-frame`, which has no id or name, and
its src is relative. The workflow clicks the third tab inside the frame. Compiled to 2 steps: a
navigate to the top page, then a click carrying a one-level `frame_chain`. The click must show
"Mauris eleifend est et turpis", the words the person typed as proof.

**What was confirmed:**
- Two test runs, watched live: the top page stayed on `/tabs/`, and the frame's panel 3 became
  visible.
- "Step 2: confirmed", no warning, `execute_success` both times. **Approved.**

**Fixed en route (general):**
1. A frame identified only by a relative src was dropped from the chain. It is now kept, checked
   against the frame's full address.
2. A recording that starts inside a frame now opens the tab's top page, not the frame's document.
3. "How can we tell it worked?" is now asked, with a typing box, when the last step has no proof but
   the recording saw the screen change.
4. Text checks now count words inside frames too, before and after the step, so a result shown in
   a frame can prove the step.

---

## Passed Workflow P-19 — WF-2 Segment E: datepicker inside an iframe, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF2 E datepicker r3`,
`3a6cae91-cdf4-41bd-93c7-aaae99391d40`, group "E2E 2026-11-16", passing runs `r_mwj5zr5o_jhj6v` and
`r_mwj61q2g_1jeij`). Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** `jqueryui.com/datepicker/`. The demo is in a frame. Click the Date box, then day 15.
Compiled to navigate plus one `date_pick` step (fixed value `2026-11-15`, one-level `frame_chain`),
which must show "11/15/2026", the value the person typed as proof.

**What was confirmed:** both test runs passed with no warning, and the frame's box ended
"11/15/2026" (watched live). **Approved.**

**Fixed en route (general):**
- "Words you see on the screen" checks now read what visible text boxes hold. Passwords, hidden
  fields and file pickers are never read.
- The relative-date question always offers "No, always <the recorded date>" next to "N days from
  today" and "different every time".

---

## Passed Workflow P-20 — WF-2 Segment F: autocomplete pick inside an iframe, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF2 F autocomplete r2`,
`0deaa579-abc9-446c-a77a-122a34e154f3`, group "E2E 2026-11-16", passing runs `r_mwj8c5rl_kqygd` and
`r_mwj8dfnm_jo7qp`). Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** `jqueryui.com/autocomplete/`, in the demo frame. Type "Ja", wait for the list ("Java",
"JavaScript"), pick "Java". Compiled to 4 steps with no inputs. The pick checks "Java" on the
screen, which includes what the box holds.

**What was confirmed:** two test runs, each with all 4 steps confirmed. The box went to "Java"
(watched live), never "JavaScript". **Approved.**

**Fixed en route (general):**
- The element scorer gives the same words full credit and contained-in words partial credit,
  never enough alone to act.
- A tag-only "role" and an anchor written as code are no longer charged against live candidates.
  The recorded element scored 0.33 against its own fingerprint.
- Regression from `c320dba` (2026-09-07, contained-in matching), three days after P-7.
- Keeping a typed value fixed also rewrites checks written from it, so the input is really dropped.

---

## Passed Workflow P-21 — WF-2 Segment J: identical shadow-DOM widgets, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF2 J shadow details r2`,
`7013dc7d-e168-421e-ba84-e62cf65ea8c8`, group "E2E 2026-11-16", passing runs `r_mwjamnrw_ged7m` and
`r_mwjaodcx_46dmk`). Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** `shoelace.style/components/details`. There are two identical `<sl-details summary="Toggle
Me">`, one under "Details" and one under "Customizing the Summary Icon". The header is
`summary#header` inside each shadow root. The workflow closes the promo dialog ("No, thanks"), then
opens the first. It must show "Lorem ipsum dolor sit amet", counted 1 before and 2 after.

**What was confirmed:** in both test runs (watched live) panel 0 opened and the twin (index 2) stayed
closed. All steps confirmed, no warning. **Approved.**

**Fixed en route (general):**
- Every click records `section_heading`, the last heading before it through shadow hosts. The
  resolver scores it (weight 0.15) only when both sides have one, so twins in different sections
  separate while twins in the same section are still refused.
- `shadow_path`, computed by the bridge since September but dropped on save, is now saved.
- Proof: only a required `text_present` (or completion checks) counts as proving the end step. A
  check that holds either way no longer suppresses "How can we tell it worked?".

---

## Passed Workflow P-22 — WF-2 Segment C: late-rendered result, to approved (strict criteria)

**Date passed:** 2026-11-16 (Build Studio dev, workflow `WF2 C late render`,
`740a014c-fcc7-4e91-9ae6-5281d0e6f2ba`, group "E2E 2026-11-16", passing runs `r_mwjax3kn_graas` and
`r_mwjayl8q_kxtse`). Full diary: [`06-E2E-TEST-AND-FIX-LOG.md`](06-E2E-TEST-AND-FIX-LOG.md).

**Shape:** S1 `/dynamic_loading/2`. Click Start; "Hello World!" is added about 5 s later. The click
must show "Hello World!" (typed by the person as proof).

**What was confirmed:** in both runs the click step waited for the late text (about 9 s), with the
text live-observed about 5 s in. No warning, `execute_success`. **Approved.** No fix needed.

---

## Passed support checks folded into P-1/P-2 (resolved en route)

These were tracked as break predictions / gaps and are now resolved — their regression fixtures
live in unit tests; re-confirm via the corresponding workflows in `01-WORKFLOWS-TO-TEST.md`:

| Old weakness | Status | Proof |
|---|---|---|
| W-1 tab steps were no-ops | ✅ resolved 2026-08-15 | `runtime/test/test_tabs.js`; proven live in P-1 |
| W-2 no download→upload binding | ✅ resolved 2026-08-15 | `test_download_upload_binding.py`; proven live in P-1 |
| W-5 download listener missed popups | ✅ resolved 2026-08-15 | Listeners attach to every opened page |
| W-7 downloads accumulated forever | ✅ resolved 2026-08-17 | `sweepOldRuns` retention sweep (7-day default) |
| W-8 multi-file upload unrepresentable | ✅ fixed 2026-08-16 (EXEC-20 zip extraction) | Unit-tested; live Shape-B replay still owed — see WF-5 |

---

## Maintenance rules

- A workflow moves here **only after a successful manual/real-browser end-to-end run** — never on
  unit-test strength alone.
- Keep the dashboard's coverage % honest: bump it only when the newly passed workflow unlocks a
  class of sites/mechanics not previously covered.
- If a later re-run of anything in this file fails, move it back to `01-WORKFLOWS-TO-TEST.md`
  with a dated regression note and lower the dashboard accordingly.
