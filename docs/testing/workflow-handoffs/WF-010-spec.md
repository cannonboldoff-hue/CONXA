# WF-010 spec — Branches and popups (source: 01 §WF-10, lines 308-339)

Rules: popups never answered on anyone's behalf; branches never spend tokens; nothing pauses by default.
Sites: S1 the-internet.herokuapp.com (/entry_ad, /login tomsmith/SuperSecretPassword!, /notification_message_rendered, /javascript_alerts); fixtures dismiss-fixture.html, decision.html (TO BUILD: "Delete all items? OK/Cancel" box, ?ask=1 on / off, log clicks to /__log?).
Happy path: clear S1 site data -> /entry_ad -> close modal -> /login -> sign in -> success msg (no Logout recorded). Human Edit: Close click = optional interstitial (try_dismiss); if_present(modal) before it; after Login wait_for_one_of(required:false): success->click Logout, error->nothing. Compile, answer, test, approve.
Cases (pass rule in brackets; every run: no cascade escalation, zero tokens):
10.1 cleared data: if_present closes modal, try_dismiss silent, success arm logs out [S1]
10.2 same profile no modal: branches skip silently, faster [S1]
10.3 wrong password input: error arm, Logout NOT clicked, run passes [S0]
10.4 /notification_message_rendered x4 wait_for_one_of success vs error: different arms, never fails [S1]
10.5 blank try_dismiss candidates: passes via Escape, no escalation [S1]
10.6 bogus element in if_present body: run still passes [S1]
10.7 wait_for_one_of required:true at nothing: fast plain failure, no cascade [S1]
10.8 record Cancel on JS confirm: compiles to a decision, Cancel chosen on replay [S0]
10.9 decision.html?ask=1 unrecorded OK/Cancel mid-run: never answered by Studio, run stops plainly [S0]
10.10 consequential decision option w/o author approval: does not run; runs only after set_fork_policy via Human Edit or chat+Confirm [S0]
10.11 run_if on input: reads inputs only, guard never skipped [S0]
10.12 on_missing:"skip" with element present but hidden: NOT skipped [S0]
10.13 unrecorded modal mid-run (skip /entry_ad when recording): Tier-1 ladder dismisses free or plain failure, never paid tier [S1]
Seeds: runtime/test/fixtures/decision_surface_cases.json, destructive_cases.json. Owners: docs/Popup-and-State-Handling.md; failures -> EXEC-1, S0 -> PROD-3.
Pass = every case PASS (or BLOCKED with reason). Old-runtime silent-skip check -> WF-16, out of scope.
