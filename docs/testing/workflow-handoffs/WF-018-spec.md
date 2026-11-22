# WF-018 spec — Hand-over step (source: 01 §WF-18)

## WF-18 — Hand-over step (a person takes the page)

**Needs a person** to solve a CAPTCHA. **Site:** S13. Record: checkbox → Submit; insert a Hand-Over
before the checkbox (message, on failure `abort`, resume-when probe = checkbox checked).

| # | Do | Must happen |
|---|---|---|
| 18.1 | Person solves within 120 s | Run continues inside the same call; no pause event; no tokens |
| 18.2 | Wait past 120 s, resume by the in-page banner / `conxa-runtime.exe resume <run_id>` / a file at `~/.conxa/resume/<run_id>.cmd` | Each resumes from the parked page; the host lock was released during the pause (another skill on another site ran) |
| 18.3 | Navigate away and back before resuming | Resume still works (drift is allowed here by design) |
| 18.4 | Close the tab, then resume | Clean failure; never continues on another page (S0 if it does) |
| 18.5 | Resume without ticking the checkbox | Refused or times out cleanly |
| 18.6 | Hand-over inside a loop; runtime restart during a pause; a JS dialog during a hand-over | Known limits (loop cursor lost, EXEC-22, dialogs intercepted): must fail as documented, not worse |

**Failures go to:** EXEC-21 / EXEC-22; `runtime/app/host_lock.js`.

---
