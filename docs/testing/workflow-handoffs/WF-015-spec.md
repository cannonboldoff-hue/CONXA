# WF-015 spec — Endurance, slow sites, chaos (source: 01 §WF-15)

## WF-15 — Endurance, slow sites, chaos, and a person interfering

**Known-good skills:** the approved WF-3 skills (P-27 saucedemo, P-29 ParaBank) and WF-4 Leg B.
Watch Studio, runtime and Chromium memory throughout.

| # | Chaos | Must happen | If wrong |
|---|---|---|---|
| 15.1 | Same skill ×20 in one Studio session, nothing restarted | Run 20 behaves like run 1: similar time, flat memory, logs rotate under 10 MB, download folders swept | S1 |
| 15.2 | Force a failure on run 7 (`CONXA_ACTION_TIMEOUT_MS=400`), then run 8 normally | Run 8 = run 1. If it fails faster with less recovery, the retry budget is poisoned (W-4) | S1 |
| 15.3 | Slow network (throttled) and `CONXA_ACTION_TIMEOUT_MS=1200` | Slower but passes, or a plain timeout naming the step; note which step types fail first | S2 |
| 15.4 | Drop the network mid-run for 30 s | Plain failure naming the step; no zombie browser | S1 |
| 15.5 | Kill Chromium mid-run | Plain failure; next run starts fresh | S1 |
| 15.6 | Stop the test with the button, and with chat "stop" | Stops promptly; browser cleaned; status correct | S1 |
| 15.7 | Quit Studio mid-run; relaunch | No orphan processes; workflow not stuck in "testing" | S1 |
| 15.8 | Click and type into the browser pane during a test | Blocked by the shield; the run's own clicks still land | S0 |
| 15.9 | Resize the Studio window; drag the panel divider during a test | Page width stays 1280; run unaffected | S1 |
| 15.10 | Hide the pane / switch to another workflow mid-test | Run behaves the same while hidden; still images still correct | S1 |
| 15.11 | Cloud backend down during a Studio test | Test still runs locally (the cloud never executes); compile shows a plain "can't reach" message | S1 |
| 15.12 | Sleep the PC mid-run, wake after 2 min | Defined: plain timeout or resume, never an endless hang (needs a person) | S1 |

**Failures go to:** W-4 (budget), EXEC-10 (timeouts), `handover.js` (shield),
`page_geometry.js` / `browser_panel.js` (size and hidden pages), new TODO per symptom.

---

## WF-16 — Through a real agent (MCP)
