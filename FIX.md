# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

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
