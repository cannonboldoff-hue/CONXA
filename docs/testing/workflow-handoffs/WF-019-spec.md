# WF-019 spec — Conxa Execute desktop app (source: 01 §WF-19)

## WF-19 — Conxa Execute desktop app

Separate app; sign in the same way as Studio (browser PKCE).

| # | Do | Must happen | If wrong |
|---|---|---|---|
| 19.1 | Sign in with Studio also open | No interference; each app catches its own redirect | S1 |
| 19.2 | Workspace picker | Lists personal, real organizations and Execute-seat workspaces; choice survives restart | S2 |
| 19.3 | Seat granted to a new email; sign in as it | Workspace appears with no code to paste | S1 |
| 19.4 | "What skills do I have?" then "Run <skill>" | Streams; real skills; a real run starts | S1 |
| 19.5 | Send a message on personal, then on a team workspace | Active workspace is obvious; the spend lands on the selected one | S0 |
| 19.6 | Watch a run in the in-app browser panel | The real runtime page, live; only one Chromium doing the work | S0 if two browsers |
| 19.7 | A run that needs a target-site sign-in | The panel shows the sign-in state and clears it when done | S1 |
| 19.8 | Switch away from the panel mid-run and back | Live view reconnects, no frozen frame | S2 |
| 19.9 | Quit and relaunch; check a second PC | History kept locally; never appears on the other PC | S1 |

**Failures go to:** `conxa-execute/app/…` (`run_turn.js`, `browser_panel.js`, `storage.js`),
`runtime/app/host_browser.js`, §13.4c (seats and billing).

---
