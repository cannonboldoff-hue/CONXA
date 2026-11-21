# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**The list of known loose ends from last week's testing is now sorted into twelve jobs — 2026-11-21**
After last week's full testing run, about twenty-five small problems were still open, all mixed together in one long list. They are now grouped into twelve separate jobs, each covering one part of the product, with a clear order so two jobs never change the same thing at the same time. Four of the jobs have started; the rest wait for their turn. Nothing in the product itself changed yet.
— 2026-11-21

**A long GitHub workflow stopped waiting on chart bars that never move — 2026-11-21**
A long workflow on GitHub spent most of its time waiting for nothing. GitHub draws charts, such as the language bar on a project page, that look like loading bars, and the runner waited up to 15 seconds for each to finish. On the last step it waited five minutes and then reported a failure. The runner now treats a bar that never moves as a picture, not work, so the same 41-step workflow finishes in about four minutes instead of failing after eleven. We could not reproduce the older, slower signed-in run.
— 2026-11-21

**Account and plan details load much faster — 2026-11-21**
Checking a workspace's plan and usage got slower as more companies signed up. Each check read every company's records and re-saved a shared file, even when it was only looking. It now reads only your own workspace's records and saves only when something really changed. With 500 companies on the system, a check takes about a tenth of a second instead of nearly a second.
— 2026-11-21

**A search button on the HR demo is no longer named after the box above it — 2026-11-21**
When you recorded a search form on the HR demo, the Search button was saved as if it were called "Username", the name of the box above it. Studio now names a button only by its own words. The box is still named "Username", including when the page draws that name a moment late. Workflows now describe the right thing, so a button is less likely to be confused with the box.
— 2026-11-21

**Studio can now confirm a sign-in on the shopping and banking demo sites — 2026-11-21**
Before, setting up a sign-in on these two practice sites always ended in "Save anyway", because the sign-in page looks the same whether you are signed in or not. Studio now checks the page you land on after signing in, and it notices when a page refuses a signed-out visitor with an error. We tried both real sites several times and it worked every time, and the app's later sign-in check gave the right answer too. The check was run without Studio's own window, which still needs a look.
— 2026-11-21

**You can ask the conversation to repeat steps for every row of a list — 2026-11-21**
You can now ask in the conversation to repeat some steps for every row of a list, with several details per row, such as a person's email and their own subject. You can also ask to change which details an existing repeat goes through. Studio shows the exact change on a card and nothing happens until you press Apply, and one Undo takes it back. The review window now asks the same "enter each row of a list?" question, and answering it in either place gives the same result.
— 2026-11-21

**We checked whether a renamed Buy Now button still gets found — 2026-11-21**
We re-ran the practice test where a button is renamed but keeps its hidden page id. The workflow still finds and clicks the right button, with no paid AI help. If a button loses every way of being recognised, the workflow still stops instead of guessing, which is deliberate for purchase buttons. We also found that the practice page no longer gives enough proof that a purchase went through, so tests on it stop after the click; that is now on the to-do list.
— 2026-11-21

**Two runs of the same workflow on a public website no longer clash — 2026-11-21**
The app only made runs take turns for sites with a saved sign-in. A workflow on a public website, such as an encyclopedia page, could therefore be started twice at once and both runs would fight over the same page. Now the app also uses the workflow's starting page to decide who goes first, so the second run waits for the first to finish. This is checked by automatic tests; it has not been tried on the live site yet.
— 2026-11-21

**Seven loose ends from the 15 November test run now have an owner each — 2026-11-21**
After the 15 November test run, seven follow-up jobs were still open: re-checking sign-in learning on two real sites, re-checking a field label on the HR demo, finding out why one site's workflow runs slowly, timing the account-limits check against a real database, deciding whether two copies of the same workflow may run together, letting chat build table loops, and checking whether a renamed button heals itself. Each one is now its own clearly described job with a finish line, and six of them have been handed out to run side by side. The seventh waits so it does not collide with the first. Nothing in the product changed yet; this only makes sure no job is lost.
— 2026-11-21

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
