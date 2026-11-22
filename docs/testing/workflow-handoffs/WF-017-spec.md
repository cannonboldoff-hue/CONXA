# WF-017 spec — Sign-in everywhere (source: 01 §WF-17)

## WF-17 — Sign-in everywhere

Studio-run sign-in through a group was already proven in WF-1 Leg B (P-16). This WF covers the rest.
Wrong account, wrong password and abandoned sign-ins are the break tests.

| # | Do | Must happen | If wrong |
|---|---|---|---|
| 17.1 | Studio signed out → Sign in | Default browser opens the login page; Studio shows signed in within ~15 s, no code to paste | S1 |
| 17.2 | Quit and relaunch | Still signed in (OS credential store) | S2 |
| 17.3 | Let the access token expire, then compile | Silent refresh; no prompt | S1 |
| 17.4 | Sign out, relaunch, sign in as a different account | Truly signed out; nothing from the first account shows anywhere | S0 |
| 17.5 | Close the browser sign-in without finishing | Plain message, can retry or cancel; nothing stuck | S1 |
| 17.6 | Target-site session saved after a group sign-in (S1) | Files are encrypted gibberish, never a readable cookie jar: Studio side `~/.conxa-build-studio*/data/groups/<id>/auth/<app>.json` (Windows DPAPI); runtime side `<data>/cache/sessions/<workspace>__<app>_state.json` (AES-GCM). (`~/.conxa/data/sessions/` holds recordings, not sign-ins.) | S0 |
| 17.7 | Wrong password in a target-site sign-in window | Window stays open; nothing saved; plain guidance | S1 |
| 17.8 | Session expires mid-run (sign out from another browser) | Detected; a fresh sign-in offered; a workflow that visits a login page as a normal step does not trigger it | S1 |
| 17.9 | Two-step login (password, then code) — only if a test site is available | Waits through the code screen; saves only once truly signed in | S1 |
| 17.10 | A workflow using two apps; sign out of one | Only that app asks again | S2 |

**Failures go to:** `docs/Auth-and-Updater.md` §1.1 owners; `runtime/auth_manager.js`,
`login_signals.js`. Readable session file → stop, security issue.
