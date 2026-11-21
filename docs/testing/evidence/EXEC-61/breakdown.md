# EXEC-61 — where the time goes on the 41-step GitHub tour (2026-11-21)

Workflow: `wf3-github-tour-40-3770ced3` (Sales group, the hand-fixed v41 from report 05 §26), copied from the Studio
test sandbox into a scratch `CONXA_DIR`. Replayed headless through MCP stdio against the source runtime
(`runtime/app/server.js`), `CONXA_MAX_RECOVERY_TIER=2`, deadline 30 min, telemetry sent to a local sink.
**Not signed in:** the copy's group has no sign-in apps and the data dir is fresh, so `github.com/cli/cli` was read as a
public visitor. No credentials were used. Tools: `replay.mjs`, `analyze.mjs`, `probe_busy.cjs` (this folder).

## Runs

| Run | What | Result | Wall |
|---|---|---|---|
| `baseline1` | pack as shipped | **FAIL at step 3** in 15.5 s: "Bound record could not be uniquely located" (every tab link is bound to its live count, `entity_binding.identifier` "58", "1.1k", "17") | 30 s |
| `nobind1` | same, `entity_binding` removed in the scratch copy only (measurement), code before the fix | **FAIL at step 41** after waiting 300 s: "still in progress after 5 minutes" | 646 s |
| `after1` | same copy, code after the fix | **Done, please check** (step 41 "couldn't confirm") | 236 s |

## Breakdown (`nobind1` -> `after1`, seconds, from `step_ms` and `completion.waited`)

| Where | Steps | Before | After | Cause |
|---|---|---|---|---|
| Completion gate on the repo home | 5, 11, 15, 21, 25, 31, 35 | 106.0 (15.1 each, `still_working`, `bw: span[progressbar]`) | 30.6 (~4.3 each, `done/still`) | GitHub draws the Languages bar as `role="progressbar"` with a fixed `aria-valuenow` (Go 96, TypeScript 3, Shell 1) |
| Completion gate on Pulse | 7, 17, 27, 37 | 60.5 | 15.0 | Same: the merged/open PR and issue ratios are `role="progressbar"` |
| Last step, full gate on the repo home | 41 | 300.4, then the run FAILED | 21.4 (20 s window), run ends "couldn't confirm" | Same bar; a full wait had no stall window for a pack without `validation.effect` |
| Pulse: not the gate | 7, 17, 27, 37 | 58.8 | 57.3 | Drift: the hand-set `a[href="/cli/cli/pulse"]` no longer matches (the tab now lands on `/insights`); ~5.5 s of resolve misses, Tier 2 a11y finds "Insights", then the required `url_pattern …/pulse` answers no. Recovery working as designed |
| Pull requests steps | 3, 9, 13, 19, 23, 29, 33, 39 | 80.4 | 75.7 | Resolver: the first try misses (signals are the recorded name "Pull requests 58 (58)"), settle-retry (`phase: action`) then `transient_recovered`; ~8 s of resolve misses each. Not a wait |
| Every other step | 21 steps | 32.2 | 28.2 | ~0.5–1.7 s gate + ~0.2–0.9 s act/verify |

Gate share of all step time: 76 % before, 38 % after. Mean per step 15.6 s -> 5.6 s.

`probe_busy.out.txt`: the bars appear ~1 s after load and never change over 6 s; Issues, Pull requests, Actions and
Security show none.

## Change

`completion.js`: bars that each show a fixed number (aria-valuenow, data-progress, a `<progress>` value) and stay
unchanged are let go: short wait after 3 s, full wait after 20 s (the stall `effects.py` writes for a non-long step),
an upload never (`run.js` passes `staticBarMs: 0`). The answer is `done` with `still`, which `gateAnswer` already
turns into `cant_tell`, so it is never a yes. A spinner, `aria-busy`, progress words or an open data-changing
request still hold the wait. `page_scripts.completionProbe`: an indeterminate `<progress>` no longer reads as "0".

## Not explained here

- Report 05's 28 s per step (19 min) was a signed-in run on the 14 November app layer; this replay is public and
  on today's code (15.6 s per step before the fix). The 28 s was not reproduced; the static-bar hold explains
  ~15 s on 11 of 41 steps and 300 s on the last one.
- The original 118 s failure at step 3 was on the un-fixed pack (generic selector + count text), which is no longer
  on disk. Today the same step fails in 15.5 s through the bound-record fast path.
