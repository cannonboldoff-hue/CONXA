# EXEC-67 + BUILD-117 (8) — live counts in bindings, stale names, and the measured leftovers (2026-11-24)

Workflow: `wf3-github-tour-40-3770ced3` (the hand-fixed v41, copied from the Studio sandbox into a scratch `CONXA_DIR`),
replayed headless through MCP stdio against the source runtime with `replay.mjs` (this folder; EXEC-61's script with the
pack dir taken from `EXEC67_CONXA`). Not signed in: public `github.com/cli/cli` as a visitor, `CONXA_MAX_RECOVERY_TIER=2`.
Logout case: `logout_probe.cjs` signs in to the-internet's public demo and runs the row matcher on the live page.
`probe_bind.py` runs the compiler's row-binding detection over a session's recorded clicks.

## Runs

| Run | Pack | Code | Result | Wall |
|---|---|---|---|---|
| `shipped` | as on disk (tab links bound to "1.1k", "58", "17"; breadcrumb bound to "Switch repository") | after | **FAIL at step 3** "Bound record could not be uniquely located" (the count is no longer 58) | 25 s |
| `rebuilt` (tab bindings removed = what today's compiler writes for them) | | after the count fix, before the tooltip fix | **FAIL at step 5**: the breadcrumb is bound to the repo picker's tooltip "Switch repository", which a signed-out page does not have | 28 s |
| `nobind-newcode` | no bindings = what today's compiler writes for every step of this recording (`probe_bind.py`) | after | **Done, please check** (step 41 "couldn't confirm") | 164 s |

EXEC-61's `after1` (same no-binding copy, code before this change): 236 s.

## (1) Live counts in bindings — fixed in the compiler

- `_own_texts` splits a trailing count off the clicked control's label ("Pull requests 58 (58)" -> "pull requests",
  "58", "(58)"): those are the control's own words, so they never name a row, and since "pull requests" sits in this
  row only, the link's own address already picks it and no binding is written.
- Found on the way: the breadcrumb's identifier came from a tooltip (`aria-hidden`, `popover`). Text kept out of
  sight (`[aria-hidden="true"]`, `[popover]`, `[role="tooltip"]`) is no longer an identifier candidate.
- `probe_bind.py 63b8a72a-…` over all 41 recorded clicks: no step gets a binding now (before: 40 of 41).
- **The shipped pack keeps its old bindings until it is rebuilt.** Nothing at run time can make "58" match a page that
  now says 61; the fix reaches this workflow through Studio's rebuild. Not done here (the rebuild was not run in
  Studio; whether it keeps the hand-set `a[href=…]` selectors was not checked).

## BUILD-117 (8) — "WF1 B secure area logout r4" — fixed at run time, no rebuild needed

- Cause: the pack was compiled 2026-11-16 with `body > div.row` "Logged in as". The compiler names a row by one run
  of text; on that page the run sits beside an element (`<p>Logged in as <span>tomsmith</span></p>`). WF-7
  (2026-11-18) made the runtime compare whole elements only, so the run was never found and Logout stopped with
  "Bound record could not be uniquely located". Different from (1): not a live count, a compiler/runtime mismatch on
  what a "whole value" is.
- Fix: `page_scripts.js::rowsHoldingWholeValue` also compares each run of text directly inside an element, still as a
  whole value ("Logged in" does not match "Logged in as"; "Carol" does not match "Carol Ann").
- Live probe (`logout_probe.cjs`, signed in on the-internet): 3 rows; old matcher `[]`, new matcher `[1]` (the content
  row), "Logged in" `[]`.
- A rebuild would not bind at all (today's compiler already skips a bare layout `.row`), covered by
  `test_logout_beside_logged_in_as_is_not_a_row`.

## (2) Stale name "Pull requests 58 (58)" — fixed in the resolver

- Cause: the recorded name carries the count; the live link says another count, so name and text earned no credit and
  the single `a[href]` match scored 0.43 < 0.5: a miss, a settle retry, ~8 s per step.
- Fix: `resolver.js::textCredit` drops a count shown and echoed in brackets ("58 (58)", "1.1k (1.1k)") from both
  sides; the same words then count as the same name. A lone trailing number ("Page 2" vs "Page 3") is not treated as
  a count, and two tabs that differ only by count still tie and are refused (uniqueMargin unchanged).
- Measured: the 8 Pull requests steps took 9.5 s each in EXEC-61 `after1`; now 1.0–2.9 s (mean 1.5 s), all
  `tier1_compiled`, no settle retry.

## (3) Pulse selector drift — measured, not a code fix

- Live: the Insights tab's link is now `href="/cli/cli/insights"`; the pack's hand-set selector is
  `a[href="/cli/cli/pulse"]`. Each of the 4 Pulse steps misses, Tier 2 a11y finds "Insights" and the step is proven:
  ~14 s of each ~18 s step (56 s of the 164 s run). Recovery works as designed.
- Fix belongs to the pack: re-point the step in Human Edit (chat never re-points a step) or re-record it.

## (4) Report-05 numbers (28 s per step, 118 s failure at step 3) — not reproducible

That run was signed in on the 14 November app layer with the un-fixed pack, which is no longer on disk. Today, public:
the shipped pack fails at step 3 in 25 s through the bound-record path; with today's bindings the whole tour runs in
164 s (3.9 s mean per step).

## (5) Step 41 "couldn't confirm" — honest, left as is

The last step's full wait sees only GitHub's fixed-value Languages bar (`span[progressbar]`); it is let go after 20 s
as `done/still`, which is a "can't tell", never a yes. Nothing else on the page changes for a plain link back to the
repo home, so nothing can prove the work finished. 21.4 s in this run.

## (6) The 20 s full-wait window — written down, not tuned

`completion.js::FULL_STATIC_BAR_MS = 20000` is the compiler's stall default for a non-long step (`effects.py`), not a
number measured on a slow export. It only lets go of bars that each show a fixed number and do not change, with no
spinner, no `aria-busy`, no progress words and no open data-changing request; uploads pass 0 (`run.js`). Risk: a real
export whose percentage bar is frozen for over 20 s while the server works without an open request would end the wait
as "can't tell" (the step is not proven; the next step may start early). Measuring it needs a real slow export (none
in the test apps). No change made.
