# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**An AI assistant can ask how a finished workflow went, and a refused access key is said plainly — 2026-11-21**
When an AI assistant ran a workflow and then asked "how did that run go?", Conxa answered "I don't know that run", even though the run had just finished. Now every run is remembered with how it ended: done, failed, cancelled, or paused to wait for an answer. When the key in an installation is no longer accepted, Conxa used to say nothing while updates and run reports quietly stopped; it now adds one plain sentence saying the installed workflows still work and to ask for a new installer. An unusual answer from the cloud with no list of workflows in it can no longer hide the workflows already installed.
— 2026-11-21

**A sign-in that runs out mid-test is reported as a sign-in, and the chat stays in order — 2026-11-21**
When a website signed you out in the middle of a test and showed its sign-in form at an ordinary address, the test said a button "wasn't found". It now recognises the sign-in form and pauses with "A sign-in expired during the test", unless the workflow itself fills in a password. Leaving a workflow during a test and coming back no longer puts old test results under the newest messages. A test whose browser tab was closed now says so in plain words instead of "Something went wrong".
— 2026-11-21

**Captured values, suggestion lists, popup answers and start pages work with new data — 2026-11-21**
A value Studio reads off a page (a price, a total) used to be found again by its own words, so a different search could not find it. It is now found by where it sits and what is around it. After you type a name and pick from the list that appears, Studio now asks once whether to pick the suggestion that shows what was typed; only your yes turns that on, and it stops if none or several match. A check on a result that appears after a popup's answer ("Successfully Deleted") can now be placed on that answer, and a check on a site follows the start page if you move it to another site of the same app. A button's label that merely appeared no longer counts as proof the work finished.
Still open: the new check-wording rules are not yet used by every place that offers checks, and unticking a "proof of done" check still leaves it listed.
— 2026-11-21

**Steps keep their name when the workflow changes, and a few safety gaps are closed — 2026-11-21**
Everything Studio remembers about a step — your answers, past test results, suggestions — used to be filed under a label worked out from the step's button or box, so a change to how Studio recognises that button could have lost it all. Each step now gets its own permanent label, and older workflows keep exactly the labels they had. The Test button also stays available while a suggested change is waiting, though approving still waits for it.
Chat can no longer be talked into typing into a password box or sending a run to a sign-in page, and a rename of a typed value no longer rewrites unrelated hidden settings of the step. Pressing "I'm signed in" a moment too early now looks again before saying it couldn't confirm, and re-recording part of a workflow is held back by open questions the same way Test is.
— 2026-11-21

**The backlog now shows only what is still open — 2026-11-21**
The fixes from today were moved out of the to-do list into the finished list, and each half-finished item now says exactly what is left. When you build a workflow, the summary Studio writes is no longer allowed to mention a new tab if the workflow never opens one. A single missing landmark on a page no longer triggers the "this app may have been redesigned" warning.
— 2026-11-21

**Tests now tell you honestly what happened, and remember what you already answered — 2026-11-21**
A test that could not run because Build Studio's window was hidden used to count as a failed test. Now it says the test couldn't run, leaves your last real result alone, and Studio brings its window back when you press Test. A test that finished without proving every step can only be approved after you tick each step you checked yourself. If you said "go ahead without asking" for a step, or "always this one" for a typed value or a date, the test and the later questions now respect that, even after you build again.
Check wording no longer shows doubled quote marks, and the test card shows plain step names. Still open: Studio's saved sign-ins are not yet locked with the computer's keychain, and a confirmation message on the next page is not yet offered as a check.
— 2026-11-21

**Big files, whole folders and bad downloads are now tested end to end — 2026-11-21**
We re-ran the file tests on a practice site: folders of 3 and 20 files, empty files, names with accents, and files up to 200 MB. Every file arrived once, in order and unchanged, and a folder handed to a single-file box is refused in plain words. An empty or never-ending download now stops with a clear reason instead of "HTTP 500". One gap remains: a very slow site can make a big upload report a failure even though the file arrived.
— 2026-11-21
