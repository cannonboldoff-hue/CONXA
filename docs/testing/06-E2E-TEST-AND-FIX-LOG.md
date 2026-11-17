# End-to-end test and fix log (started 2026-11-16)

> **Progress (2026-11-17):** 21 workflows (WF-1…WF-21), renumbered in run order on 2026-11-17 —
> see the old → new map at the bottom of `01-WORKFLOWS-TO-TEST.md`. Fully passed: WF-1, WF-2
> (Segment I is a confirmed limitation), WF-3. **In progress:** WF-4 (Legs A, C, D tested and fixed).
> Remaining: WF-4…WF-21. Latest commit: see `git log`.

This is the running diary of a test-and-fix campaign over every workflow in
[`01-WORKFLOWS-TO-TEST.md`](01-WORKFLOWS-TO-TEST.md). It is written in plain words, newest entry at
the bottom. Each entry says which workflow was tested, what happened, why, and what was changed.

## How the campaign works

- **Two roles.** A *tester* (a separate AI helper) drives the Build Studio exactly the way a person
  would: create the workflow, record it in the built-in browser, answer Studio's questions, compile,
  run the test, check the result really happened, and approve it. It only reports. An *investigator*
  (the main AI) reads every failure, finds the real cause in the code, fixes the cause for every
  workflow of that kind (never a one-site patch), and sends the same workflow back to be tested again
  from the start.
- **What counts as a pass.** All of these must hold: every listed step was recorded; compile finished
  and every question was answered on purpose; the test passed with no "couldn't confirm" warning; the
  real outcome was proven independently (the page itself, or the logs), not just Studio's verdict; and
  the workflow reached **Approved**, the state from which it can be published. Anything less is not
  added to [`02-WORKFLOWS-PASSED.md`](02-WORKFLOWS-PASSED.md).
- **What cannot be tested here** is logged as *blocked*, never as passed: a clean second computer,
  real billing, a person solving a CAPTCHA, or anything that changes the owner's real accounts without
  their say-so.

## Setup (2026-11-16)

- Build Studio restarted in the dev lane (its window had closed; only the page server was left).
- The test runtime Studio uses was **out of date**: it was built on 14 November, before 49 runtime
  files were changed on 15 November. It was rebuilt so tests run today's code. Lesson: after any
  runtime fix, rebuild it the same way before re-testing, or the re-test proves nothing.
- The practice pages in `docs/testing/fixtures` are served on port 8099, with every request written
  to a log so a click can be proven.
- Starting point of the automatic checks: runtime 1392 passed, 0 failed (1 skipped); Python
  3129 passed, 0 failed (1 skipped). Every fix must keep both at zero failures.

## Plan: every workflow, sorted

The plan is the status board at the top of [`01-WORKFLOWS-TO-TEST.md`](01-WORKFLOWS-TO-TEST.md)
(renumbered in run order on 2026-11-17). WF-1…WF-4 kept their numbers, so every diary entry below
still matches; for any other old number, use the map at the bottom of that file.

---

## Diary

### WF-1 Leg C — fill a form on demoqa and check the echo (assigned 2026-11-16)
Chosen first because it is simple, needs no sign-in and has a visible result to prove: four text
boxes filled, Submit pressed, and the page echoes the four values back.

**Test 1 — result: BLOCKED.** The tester recorded all four fields and Submit (11 actions), and the
page echoed the values. Compile finished (12 steps, 2 checks). Three questions were asked and
answered. Then pressing *Run test* stopped at once, before any browser opened, with: *"'WF1 C demoqa
text box' contains someone's account details (an email address) in how it finds a button. Compile
the workflow again, then build."* There is no "compile again" button, so a person is stuck.

The tester also saw two smaller things:
- On the last question ("How can we tell it worked?") typing your own words was refused with
  *"Only the description can be edited for a scroll step."*
- The Email field was missing from "Which of these are different every time?", and its test box
  came up empty although the other three were filled with what was recorded.

**What was really wrong (three separate causes, each a whole class of problem):**

1. *The privacy check could not tell sample text from a real address.* Conxa refuses to ship a
   workflow that finds a button by the recording person's own email (for example the account chip
   in a corner of Gmail). But the check treated **any** text shaped like an email as private. The
   demo form's Email box shows the hint "name@example.com", so the hint ended up in how the box is
   found, and the build was refused. "example.com" is a domain set aside worldwide for examples; no
   real mailbox can exist there. Worse, the cleaning done while compiling only looked at one part of
   what the recorder saves about an element, while the final check looked at all of it. So a real
   private address could slip past the cleaning and only be caught at the very end, as a dead end.
   *Kind of fix: architectural.* One shared rule now decides "is this a real person's address",
   used both by the cleaning step and by the final check, so they can never disagree. Sample
   addresses on the reserved example domains count as ordinary page text. The cleaning now covers
   every part the recorder saves about an element (its description, the ready-made ways to find
   it, and its surroundings), not just one.
2. *A typed "this is how I know it worked" check was always stuck on the very last step.* People
   often scroll down to look at the result after the last real action, so the last step is a
   scroll, and a scroll step cannot hold a check. *Fix:* the check now goes on the last step that
   is allowed to hold one, using the same per-step rules the editor enforces.
3. *Email and password boxes had two names.* When Conxa recognises an email or password box it
   uses a standard input name ("user email"), but it saved the step as if the input were called
   "email". The two names disagreed, so the question list skipped it and the test form could not
   find its recorded value. This affected every email and password box on every site. *Fix:* the
   standard name is now used for both.

**Changed:** `compiler/selector_filters.py`, `skill_package_builder.py`, `conversation/clarify.py`,
`compiler/input_binding.py`, plus new automatic tests for each cause.

**Test 2 (fresh recording, after the fixes) — result: FAIL.** All three fixes held: the test built,
the Email box was offered and pre-filled, and the typed check "Asha Rao" was accepted. The page
really did show all four values after Submit (read straight from the browser). But the test still
failed at Submit, twice over:
- With the check "Asha Rao": *"The step ran, but the page didn't end up where it should have"*
  (the words check said no).
- With that check removed, or replaced by the full line "Name:Asha Rao": *"I couldn't confirm that
  'Click Submit to send the form details.' finished … the run stopped instead of reporting
  success."*
No Approve button was offered, so the workflow could not become publish-ready.

**What was really wrong (two causes, both general):**

1. *"You see these words" meant two different things inside one check.* Before the step, Conxa
   counted the words anywhere in the page's text, ignoring capital letters. After the step, it
   demanded a whole block of text equal to the words, exactly. "Asha Rao" sits inside the line
   "Name:Asha Rao", so it was never found. A person who types "words you should see" means
   *contained in*, not *the whole line*. *Fix:* both halves now use the same meaning: contained
   in the page's text, any letter case.
2. *Conxa had no way to accept a person's own proof for a "send" step.* Conxa sorts proof into
   levels: "we did it" < "the app took it" < "the work finished" < "the result is really there".
   A Submit button counts as "send", which needs proof of "the work finished". The only automatic
   proof for that is watching the app answer a data-changing request over the network. This form
   never talks to a server: it shows the result in the page itself, like many simple tools and
   modern apps. A passing words check only ever counted as "the app took it", one level too low,
   so the run could never be confirmed. *Fix:* a words check counts as "the work finished" only
   when all of these hold: a person made it required (Conxa's own suggested checks are never
   required); the words were measured as absent before the step; and they are visible after it.
   That is exactly a check that would have said no if the click had done nothing, which is the
   rule Conxa uses for any proof. Text already on the page, or hidden in it, never counts.
   Separately, "How can we tell it worked?" now always offers "You see the <typed value> on the
   screen" when a step needs proof. Before, it only appeared if Conxa had written a words check
   of its own, so a person who just accepted the default had no proof that could ever hold here.

**Kind of problem:** architectural. One check had two meanings, and the proof levels gave a
person's own definition of "done" no way to count. Neither was specific to this site.

**Changed:** `runtime/app/assertions.js`, `runtime/app/proof.js`, `runtime/app/run.js`,
`conversation/clarify.py`, new tests `runtime/test/unit/test_text_check_meaning.js` and one in
`test_compile_clarify.py`. Automatic checks: Python 3134 passed, runtime 1394 passed, 0 failed.
The test engine Studio uses was rebuilt and Studio restarted before the next test.

**Test 3 (fresh recording, after the second round of fixes) — result: PASS.**
- Recorded the four fields and Submit (11 actions). Compile gave 12 steps; the tester's answer
  "Yes, ask first" to "can't be undone — should someone say yes first?" added a yes/no step
  before Submit (13 steps, 3 checks).
- "How can we tell it worked?" now offered "You see the full name / user email / current address /
  permanent address on the screen". The tester ticked all four and typed "Asha Rao" as well.
- The first two runs stopped at step 1 with "The test browser is not showing the page at its
  normal size". Studio's window had been minimized on the computer; that is the environment, not
  the product. After restoring the window, the run paused at "A person needs to confirm … Should
  it go ahead?". The tester pressed "Yes, go ahead", and the test passed: *"Test passed · 14
  steps · 2 min 13 s"*, with no "couldn't confirm" warning. The card's "What was checked" list
  shows the Submit step as **confirmed**.
- Independent proof: the browser showed the result box empty before the yes, and two seconds
  after it showed "Name:Asha Rao / Email:asha.rao@example.com / Current Address :12 Lake Road,
  Pune / Permananet Address :Same as current" (the misspelling is the demo site's). The runtime
  log shows `execute_success` for run `r_mwiava3f_nek4m`.
- The workflow was **approved** with the normal Approve button; the sidebar shows "Approved" and
  Publish is offered.

Smaller problems seen on the way, filed in `TODO.md` (BUILD-89, RT-16), none blocking: the
minimized-window message says "run the test again", which cannot help; the passed card shows
"confirmed_to_continue: [object Object]"; the yes/no step is named "Agent span goal"; and the local
log does not record which check proved a step.

**Final status of WF-1 Leg C: PASSED** (added to `02-WORKFLOWS-PASSED.md` as P-11).

### WF-1 Leg A — search Wikipedia, open a link, go back (assigned 2026-11-16)
The control baseline: search "Ada Lovelace", click the "analytical engine" link in the article, then
press the browser's Back button.

**Test 1 — result: FAIL.** Recording captured 9 actions, including Back (good). Compile finished;
the questions were sensible and answered (the search words became an input; the link stays fixed).
The test failed at step 2, both times: *"The element this step needs wasn't found on the page."*
No click step leaned on a throwaway generated id, which is what this leg was meant to check.

**What was really wrong (two causes, both general):**

1. *Conxa described an element as it looked after the click, not before.* Wikipedia's search box
   changes itself the moment it is clicked: its role turns from "search box" into "combo box".
   The recorder read the box when the click finished, so it saved the changed version. On a
   replay the page is fresh, the box is still a plain "search box", and nothing matched. The tester
   confirmed this outside Conxa in a plain browser. This affects any element a page changes as
   soon as you press it (menus, search boxes, buttons that turn into spinners). *Fix:* the recorder
   now notes the element at the very start of the press, before the page's own code reacts, and
   uses that description for the step. It does so only when the click finishes on that same
   element, or on the one the page swapped in for it.
2. *Pressing Enter was recorded twice.* When you press Enter in a form, the browser presses the
   form's submit button for you. The recorder saved both the Enter and that automatic "click on
   Search". A replay would press Enter, land on the article, and then try to click Search again.
   The same happens with Enter on a focused button. *Fix:* a click the browser makes because of
   the Enter that was just recorded is no longer saved as a second step. A real mouse click on the
   button still is.

**Kind of problem:** architectural, in the recorder. The step must describe what a replay will
meet, and one action of the person must be one step.

**Changed:** `recorder/bridge.js`; new tests in `test_recorder_bridge_js.py`. Python suite: 3137
passed, 0 failed. Studio restarted before the retest.

**Test 2 (fresh recording, after those fixes) — result: FAIL, same place.** Both recorder fixes
held: the click was saved as the original "search box", and no phantom "Click Search" appeared.
But the test still failed at step 2 with the same message.

**What was really wrong this time:** the recorder's description was now right, but the compiler
threw it away. Wikipedia also removes the box's name tag (`id`) when you click it. Conxa decided
whether two recorded actions were on "the same box" by comparing their descriptions, so the click
(old description) and the typing (new description) looked like two different boxes. It then
believed the typing had no click before it, and added its own "click into the box" step built from
the *changed* description. With two "click into the box" steps in a row, the review pass judged one
of them pointless and set aside the right one. *Kind of problem:* architectural. "Is this the same
element?" was answered from how the element looked, and a page can change how an element looks
at any moment.

*Fix:* the recorder now gives every element it sees a private token that stays the same for that
element however the page changes it. The compiler uses that token to know the click and the
typing are on the same box. The click stays as the step a replay meets first, and no extra click
is invented. The review pass is no longer allowed to set aside a press that changed the element
a later step uses, because that press is what makes the later step findable. This is the same kind
of objective guard it already had for clicks that start a download. Also fixed:
Studio no longer asks "A few things you typed are not in the workflow" about words that were only
recorded twice and are still typed by the workflow.

**Changed:** `recorder/bridge.js`, `recorder/session.py`, `compiler/step_anchors.py`,
`compiler/build.py`, `compiler/second_opinion.py`, `conversation/clarify.py`; new tests in
`test_phases.py`, `test_second_opinion.py`, `test_compile_ledger.py`. Python suite: 3140 passed,
0 failed. Studio was stopped once by the computer running low on memory; restarted for the retest.

**Test 3 (fresh recording) — result: FAIL, same place.** The recorder tagged the click and the
typing with *different* tokens. So my reading of Test 2 was wrong: Wikipedia does not change the
box, it throws it away and puts a brand-new box in its place. I checked that directly on the live
site with a plain browser. The click lands on the original box. Right after the click Wikipedia
removes it, adds a new box, and moves the typing cursor into the new one, and everything typed
goes there. A plain "focus" (without a click) causes the same swap, so a replay that focuses the
original box gets the new one too.

*Fix:* the recorder now notices exactly this case. When you type into a focused text box, and the
text box you clicked just before has disappeared from the page, the new box is noted as standing
in for the clicked one. The compiler treats such a pair as one box: the click stays as the first
step (the original box, which is what a fresh page shows), the typing goes to the new box, and no
extra click is invented. Re-running the compile clean-up on the Test 3 recording, with that note
added, gives exactly: open page → click the original box → type → Enter → click the link → Back,
and the click is protected from being set aside. *Kind of problem:* the same architectural one as
Test 2 ("is this the same element?"), now covering a page that swaps the element instead of
editing it.

**Changed:** `recorder/bridge.js`, `recorder/session.py`, `compiler/step_anchors.py`; new tests.
Python suite: 3141 passed, 0 failed. Studio restarted.

**Test 4 (fresh recording) — result: PASS.**
- The recording showed the new note at work: the typing and the Enter were marked as standing in for
  the clicked box.
- The compiled workflow is: open page → click the search box (the original box) → type the search
  words (an input) → Enter → click "analytical engine" → Back. Nothing was set aside.
- Questions were answered on purpose. The tester unticked the three suggested checks that were
  dropdown text, not real page text, and ticked "You see the search words on the screen".
- The test passed in 30 seconds with every step "confirmed" and no warning. The runtime log shows the
  run (`r_mwik9npg_iyoiv`) ending with `execute_success` on the Ada Lovelace article. The Back step's
  required check found the search words there.
- The workflow was approved with the normal button; the sidebar shows "Approved".

One question still appeared that should not have: "A few things you typed are not in the workflow"
(about the duplicate typing). The cause: the saved workflow keeps the recorded value in the recording,
not on the input itself, and my filter only looked at the input. It now also checks what each kept
step was recorded typing. Covered by an automatic test; it was not seen live yet.

Other small things the tester noticed, filed in `TODO.md` (BUILD-91): the compile is marked
"failed" in its own report although it worked; checks the person unticked stay on as optional
extras; check wording shows doubled quotes; the first three "how will we know" options were
dropdown text, ticked by default; and the run waited about 15 seconds after Enter.

**Final status of WF-1 Leg A: PASSED** (added to `02-WORKFLOWS-PASSED.md` as P-12).

### WF-1 Leg D — open a new tab and come back (assigned 2026-11-16)
On the-internet's "Multiple Windows" page: click "Click Here" (the site opens a new tab), check it
says "New Window", switch back to the first tab, then click "Elemental Selenium". Tests that each
step runs in the tab it was recorded in.

**Test 1 — result: BLOCKED (crash).** The moment "Click Here" was clicked during recording, the whole
Build Studio app closed with no message. This happened three times, including once with a real mouse
click and no test script attached, so a person would hit it too. Nothing could be recorded.

**What was really wrong:** when a website opens a new tab during a recording, Studio sets up its
connection to that tab (to size the page and to know the tab belongs to this recording) straight
away, inside the browser's own "create the tab" moment, before the tab has loaded anything. The
code's own comment already warned that doing this on a tab that never loaded a page crashes the app.
Tabs Studio opens itself load a page first, so they were safe; tabs a website opens were not. Test
runs were not affected because they skip that connection. *Kind of problem:* architectural, in the
app shell. Any "opens in a new tab" link, sign-in popup or share window would close Studio while
recording.

*Fix:* a tab a website opens is now connected only once its first page has started, and Studio
never waits on it for more than 5 seconds, so a tab that never loads cannot freeze the recording.
Checked by hand before handing back: recording `/windows` and clicking "Click Here" now opens the
new tab inside the recording, and Studio stays up.

**Changed:** `conxa-builder/electron/browser_panel.js`; its tests updated to pin "no connection
before the first page" (39 passed).

**Test 2 (fresh recording, after the crash fix) — result: passed only after a fix inside Studio, so
not counted.** Studio stayed up. The new tab was recorded, the switch back to the first tab was
compiled in, and so was the second link that opens another tab (8 steps). But "How can we tell it
worked?" offered only one option, "The screen changes", which the tester ticked. The first two
test runs then failed on the "Elemental Selenium" click: *"The step ran, but the page didn't end up
where it should have."* Studio's "Find the cause" correctly said the check could never pass, and its
one-click fix (make that check optional) led to a pass and approval. Strictly, a person should
not need that detour. (Also noted: the switch back to the first tab is not shown in the list of
captured actions, although it is in the compiled steps.)

**What was really wrong:** "The screen changes" was checked only on the tab where the click
happened, by comparing its address, its number of buttons and links, and its amount of text. A
click whose whole job is to open a new tab leaves its own page exactly as it was, so the check said
"nothing happened" every time. *Fix:* a new tab or window that this page itself opened during the
step now counts as a change. It is counted per page, so a tab opened by some other run can never
make it pass. A click that does nothing still fails the check.

**Changed:** `runtime/app/assertions.js`; new test `runtime/test/unit/test_new_tab_is_a_change.js`.
Runtime suite: 1395 passed, 0 failed. The test engine was rebuilt.

**Test 3 (fresh recording) — result: PASS.** 4 actions captured (two clicks, two new tabs). They
compiled to 8 steps: open the page → click "Click Here" → new tab opens → its "New Window" page
shows → switch back to the first tab → click "Elemental Selenium" → another tab opens → its page
shows. Studio asked no questions this time; the workflow has no inputs and nothing was unclear.
The first test passed in under a minute with all 8 steps "confirmed". The runtime log shows run
`r_mwima47v_024b1` taking each step in order and ending with `execute_success` on the original
`/windows` tab. Each click's effect is proven by the next step: a "new tab opens" step fails if no
tab opens. The workflow was approved with the normal button.

Honest notes: this time Studio did not offer "The screen changes" (in Test 2 it had, because that
compile's review pass happened to suggest it), so the runtime fix from Test 2 was not exercised
live here; its own browser test covers it. Two step labels say "in the new window" for actions on
the first tab (wording only, filed with BUILD-92).

**Final status of WF-1 Leg D: PASSED** (added to `02-WORKFLOWS-PASSED.md` as P-13).

### WF-1 Leg E — download a file, then upload it on another site (assigned 2026-11-16)
Download a file from the-internet's download page, open demoqa's upload page in a new tab, and upload
that same file. The replay must hand the file over by itself, with no file path typed in by anyone.

**Test 1 — result: PASS (first try, no fixes needed).**
- Recorded: click `some-file.txt` (downloaded), "+" for a new tab, open demoqa's upload page, pick
  the file. The file picker was the normal Windows "Open" window, already pointed at the recording's
  downloads folder.
- Compiled to 6 steps. The upload step's file is "the file downloaded earlier in this run", not a
  fixed path and not something a person must type. The test form had no inputs to fill.
- Two questions were answered on purpose. The tester unticked "the file shows up when the page is
  opened again", which is not true on this site, and typed `some-file.txt` as the proof instead.
- The test passed (6 steps, 10 s, all confirmed, no warning), twice. Each run's own folder held the
  freshly downloaded `some-file.txt`. During the second run the page itself showed
  `C:\fakepath\some-file.txt` as uploaded. The runtime log shows `execute_success`.
- Approved with the normal button.

Small things noticed, added to `TODO.md`: the live counter said 5 actions while the saved recording
said 4 (one was folded away, as the compile message explained); a check the person unticked still
appears in the plan; and the "What was checked" list says only "Step N: confirmed" with no plain
description.

**Final status of WF-1 Leg E: PASSED** (added to `02-WORKFLOWS-PASSED.md` as P-14).

### WF-1 Leg F — the browser's own pop-up boxes: alert, confirm, prompt (assigned 2026-11-16)
On the-internet's "JavaScript Alerts" page: an alert (OK), a confirm (OK), and a prompt where a short
text is typed and accepted. The page then shows what was answered, which is the proof.

**Test 1 — result: FAIL.** Recording worked: Studio showed each pop-up box in its own window ("The page
is asking"), and the tester answered them. The typed prompt answer became an input. Compile was
fine. But in four test runs the prompt was never answered: the page never showed "You entered:
...", and the prompt step sat for two minutes. One run ended as "passed — please check" with a "I
couldn't confirm" warning; it was not approved, because the outcome never happened.

**What was really wrong:** I tested it directly. Build Studio's test browser, and Conxa Execute's
browser, are built on Electron. Electron shows the plain "OK" box (alert) and the "OK / Cancel" box
(confirm), but it does **not** support the "type an answer" box (prompt) at all. When a page asks
for one, nothing happens and no tool can see or answer it. The recorder had already worked around
this by giving the page its own stand-in for the prompt box; the replay had nothing of the kind. So
any workflow with a prompt could never be replayed inside Studio or Execute, on any site. It would
work only in the customer's separate browser. *Kind of problem:* architectural, a gap between how
recording and replay handle the same browser.

*Fix:* when a replay runs inside Studio's or Execute's browser and the next recorded step answers a
prompt, the runtime now gives the page a one-time stand-in that returns the recorded answer, or
"Cancel" if Cancel was recorded. The page's own prompt box is put back right after. The prompt step
then sees the answer was already given and finishes at once, instead of waiting two minutes. In a
normal browser nothing changes. Checked in a bare Electron window: the stand-in returned
"Conxa prompt" for OK and nothing for Cancel.

**Changed:** `runtime/app/page_scripts.js`, `runtime/app/run.js`, `runtime/app/handlers.js`; new test
`runtime/test/unit/test_prompt_in_host_browser.js`. Runtime suite: 1397 passed, 0 failed. The test
engine was rebuilt.

Other things the tester noticed, filed in `TODO.md` (BUILD-93): a test that ends "couldn't confirm"
is still labelled "Test passed" and can be approved; the prompt's test input was empty the first
time, although the card says recorded values are filled in; a harmless "OK" on a confirm box is
treated as something that cannot be undone, and the test pauses on it even after the person said
"no need to ask".

**Test 2 (fresh recording, after the fix) — result: PASS.**
- Recorded the alert, the confirm and the prompt (answer `Conxa prompt`). Compiled to 7 steps with
  the prompt answer as an input.
- Two questions were answered on purpose. "You see the dialog answer on the screen" was ticked as
  the proof.
- The test used a *different* answer, `Conxa second`, to prove the run follows the input. The page
  showed "You entered: Conxa second", read directly from the page during the run.
- The prompt step took 3.4 seconds (it used to sit for 2 minutes). No warning. The run ended with
  `execute_success`.
- The test paused once before the confirm click, Studio's normal "ask before steps that can't be
  undone" pause, and was continued with the normal button. The workflow was approved.

Also seen: some step names are off by one around the pop-up steps (the execution is right; filed in
BUILD-93).

**Final status of WF-1 Leg F: PASSED** (added to `02-WORKFLOWS-PASSED.md` as P-15).

### WF-1 Leg B — sign in, and make sure no password ends up in the package (assigned 2026-11-16)
Conxa never records what is typed on a sign-in page, by design. So the sign-in is done once through
the group's own "sign in" step, which saves the session safely. A workflow then starts on the
signed-in page ("Secure Area") and clicks Logout; the page saying "You logged out of the secure
area!" proves the run really was signed in. Finally the built package is searched for the
password and user name, and neither may appear.

**Test 1 — result: BLOCKED.** The group was created and the tester signed in through Studio's sign-in
browser. Studio could not confirm the sign-in by itself, because the wizard never asks for the page
you land on, so "Save anyway" was needed. The success address was then set under Edit. The recording
started signed in on the Secure Area, as it should, and recorded the Logout click. Then every test
run failed with *"The Internet's saved sign-in expired mid-run."* "Sign in and test again" did not
open a sign-in window; it ran the same failing test again. After the tester signed in again through
the group page, every test refused to start: *"Sign-in setup for the E2E Signin group changed
after the last build. Rebuild the skill package before testing."* The normal screens have no
control for that.

Password check: no file in the package contains the password. The user name "tomsmith" appears
once, as text the page shows ("Logged in as tomsmith"), not as anything typed.

**What was really wrong (two causes, both general):**

1. *Finishing a recording overwrote the group's saved sign-in with a signed-out one.* When a
   recording ends, Studio copies the browser's cookies back into the group's saved sign-in, so that a
   refreshed session is kept. It was meant to skip this when the recording ended signed out. But it
   could only notice a sign-out if a session cookie *disappeared*. This site signs you out by
   rewriting the same cookie, so nothing disappeared, and the signed-out cookie replaced the good
   one. The tester proved it by using each saved cookie on the site: the old one opened the Secure
   Area, the new one was sent to the login page. So any workflow that signs out, or any site that
   signs out this way, silently broke every workflow in its group. *Fix:* the recording's cookies now
   replace the saved ones only after Studio's own sign-in check (the one behind "Check now") confirms
   they are still signed in. The check runs in the background after "Finish and save". Signed out, or
   a site Conxa cannot check, keeps the working session.
2. *"Run test" and the test itself disagreed on "out of date".* "Run test" rebuilds the test copy only
   when the workflow was edited after the last build. A separate check refuses the test when the
   group's sign-in setup changed since the build. A person who just signed in again hit the second
   with no way to satisfy it. *Fix:* "Run test" now also rebuilds when the sign-in setup changed, using
   the very same rule.

**Changed:** `handlers/session.py`, `handlers/test_flow.py`; tests updated in
`test_recording_group_app_scoping_and_writeback.py` plus a new one,
`test_test_build_follows_sign_in.py`. Python suite: 3144 passed, 0 failed. Smaller sign-in issues
(wizard never asks for the success address, a stale warning, "Sign in and test again" not opening
the sign-in window, the user name kept as page text, Studio's own session copy stored unencrypted)
are filed as AUTH-24.

**Test 2 (new group, fresh recording, after both fixes) — result: FAIL, for a third reason.** Both
fixes held:
- The saved sign-in was not replaced after the recording ended signed out. The file was unchanged,
  and the site still opened the Secure Area with it.
- "Run test" rebuilt its test copy by itself.

But every test run stopped with "The Internet's saved sign-in expired mid-run", although it plainly
was not expired: the run opened the Secure Area signed in, and the Logout click worked.

**What was really wrong:** I reproduced it outside Studio. After the click, the page says "You logged
out of the secure area!". The runtime's "did the app say it failed?" check reads "logged out" as "your
sign-in ended" and stops the run. The runtime can't tell a deliberate sign-out from an expired one
because the recording never told it where this click lands. The recorder throws away everything on
a sign-in page, so a recording never contains anything typed on one. That also threw away the
*arrival* on the sign-in page, the very result of the Logout click. So any workflow whose real
result is landing on a sign-in page (signing out, closing an account, ending a session) failed every
time, on any site. *Kind of problem:* architectural, a gap between what recording keeps and what replay
needs to judge the result.

*Fix:*
- **Recording:** an arrival on a sign-in page that the person's own click caused is now kept as
  that click's result. Nothing done *on* the sign-in page is kept, as before. If such an arrival
  is not caused by a click, it is still dropped and never becomes a step.
- **Replay:** at the address *that step* was recorded landing on, a sign-in page or a "you are
  signed out" message is the step's result, not a dead sign-in. The same rule is used in all four
  places that judge this. Any *other* step that lands on a sign-in page is still treated as an
  expired sign-in.
- **Checked against the real site:** with the recorded landing, the step passed and was proven
  (the logout message is new, and it is on the recorded address). Without it, the step still fails
  as before.

**Changed:** `recorder/session.py`, `compiler/navigation_effects.py`, `runtime/app/run.js`,
`runtime/app/server.js`, `runtime/app/effect_probes.js`. Tests were added to
`test_redirect_navigation.py` and `runtime/test/unit/test_auth_recovery.js`. Python: 3146
passed, 0 failed. Runtime: 1397 passed, 0 failed. The test engine was rebuilt and Studio
restarted.

Known limit: if a sign-in really expires during *recording* at a click, that click now records the
sign-in page as its landing. A later run where the sign-in expires at that same step is then not
caught there, but the next step's own check still catches it.

**Test 3 (new group, fresh recording) — result: FAIL, same message.** The tester showed that the
recording still had no landing for the Logout click. So the runtime half of the fix had nothing to
use.

**What was really wrong, one level deeper:** the recorder learns where a click led in two ways:
- **From inside the page itself**, which looks again once the page settles. When the click loads a
  whole new page, that old page is gone before it can look, so it reports nothing.
- **From the browser's own record of page loads.** That route skips every load the page itself
  started, because the click is already a step.

So the click that ends a recording by loading a new page never learns where it went. That is true on
any site, not only for sign-outs. Earlier steps were partly covered, because the *next* step's address
shows where the page went. The last step had nothing.

*Fix:* when the browser reports a page load that the page started right after the person's action,
the recorder now writes the landing straight onto that action. It is queued behind the action, so it
always arrives after it. A late report from the old page can no longer wipe it out. The landing is
written only onto the action taken on the page that was left.

**Changed:** `recorder/session.py`. New tests in `test_redirect_navigation.py`: the landing is queued,
written onto the right step, and a last step that lands on sign-in compiles with that landing.
Python: 3148 passed. One LLM-router timing test fails about one run in three on its own; this change
does not touch it. Studio restarted.

**Test 4 (new group, fresh recording) — result: PASS.**
- The recording now knows the Logout click landed on `/login`. Compile built three checks for that
  step: the address changes, it lands on the login page, and "You logged out of the secure area!"
  appears. The last one came from the single question, "How can we tell it worked?", where the
  tester ticked the address and typed the message.
- The group's saved sign-in still worked after the recording and after both tests, checked on the
  site itself.
- Both test runs passed ("Step 1: confirmed", "Step 2: confirmed"), each ending in `execute_success`
  on `/login`. There was no "sign-in expired" and no "couldn't confirm". The workflow was approved.
- No file in the package contains the password. The user name appears only as page text (AUTH-24).

Also seen: the question's tick box says "The web address looks like" without showing the address
(BUILD-93).

**Final status of WF-1 Leg B: PASSED** (P-16). **All six legs of WF-1 have now passed.**

### WF-2 Segment H — a menu that appears only when the mouse rests on a picture (assigned 2026-11-16)
On the-internet's "Hovers" page, three user pictures show a name and a "View profile" link only
while the mouse rests on them. The workflow rests on picture 2 and clicks its "View profile". The
page that opens (`/users/2`) is the proof. This failed to replay reliably in September (EXEC-29a).

**Test 1 — result: FAIL (on the questions, not the replay).** The replay itself now works: two test
runs both opened `/users/2`, and the workflow could be approved. Studio does not record the mouse
resting as its own step. On replay, when the link is hidden, it rests the mouse where a person would
until the link shows.

But Studio's one question was built on a wrong fact. The program that watches the recording video
said the person rested on the *third* picture. The browser's own record says the second (user2). The
question offered only "It can be any of the three" or "Always the third one — It stays exactly as you
recorded it". A non-technical person would trust the second answer and get a workflow that may open
the wrong profile. The tester got it right only by typing their own answer, which an ordinary
person should never need to do.

**What was really wrong:** Studio already checks the question's *quoted names* against what the
browser recorded, and swaps in its own plain wording when one was never seen. It did not check words
that say *which one* of several alike items ("first", "third", "2nd"). Those came from the video
reading and were taken on trust. *Kind of problem:* general. Any page with several alike items (rows,
cards, pictures) could get a question that names the wrong one.

*Fix:* in a "which one?" question, a position word that the browser's own record does not show
(what the video saw does not count) now makes Studio use its plain wording: "You picked “View
profile”…", with "Always this one — It stays exactly as you recorded it." That is true, because the
recording itself knows which one was picked.

**Changed:** `conversation/timeline_understanding.py`; test added to `test_timeline_understanding.py`.
Python: 3150 passed, 0 failed. Studio restarted.

Also filed (BUILD-93): the click step is named "Open the Hovers page with the profile icons"; the
"proposed change" card shows internal field names; the "web address looks like" tick box shows no
address.

**Test 2 (fresh recording; only offered answers, nothing typed) — result: PASS.**
- The video reading said "second" correctly this time, and Studio asked its own plain question:
  "You picked “View profile”. Next time, is it always this one, or a different one each time?"
  The tester picked "Always this one". The new check had nothing to catch in this run; its
  automatic test covers the misread case.
- The compiled click is tied to the picture captioned "name: user2".
- Two test runs passed with no warning. In both, the test pane went `/hovers` → `/users/2`,
  watched live, and never showed `/users/1` or `/users/3`. Both ended in `execute_success` on
  `/users/2`. Approved.

**One more fix from this test:** the "How can we tell it worked?" tick box read "The web address looks
like " with nothing after it (seen in three tests now). The address check keeps its pattern under a
different name than the label read. It now shows a readable address, such as
"the-internet.herokuapp.com/users/…". Changed `conversation/clarify.py`, with a test added. Python:
3151 passed.

**Final status of WF-2 Segment H: PASSED** (P-17).

### WF-2 Segment D — acting inside a page that sits inside another page (assigned 2026-11-16)
A frame (iframe) is a page shown inside another page. The September record says its frame steps were
"not captured in this take", so they were never really passed. None of the local practice pages has
a frame, so jQuery UI's demo site is used: every demo there runs inside a frame. On the "Tabs" demo
the workflow clicks the third tab, "Aenean lacinia", inside the frame. That tab's own text appearing
is the proof.

(A first attempt was cut off by an outside fault in the tester's tools. It is not counted.)

**Test 1 — result: FAIL.** The test ran and the third panel did show. But the tester watched the run
live and saw why: the replay never went inside a frame at all. It opened the frame's own page on its
own and clicked there. Studio also asked no "How can we tell it worked?" question, and both test runs
ended "I couldn't confirm that … finished". Approve was still offered.

**What was really wrong (two causes, both general):**

1. *The frame was dropped from the recording.* Studio remembers a frame by what its tag says: its
   name, its id, or the address it loads. This frame has no name or id, and its address is written
   as a short relative address (`/resources/demos/…`) instead of a full `https://…` one. The code
   that turns an address into a pattern accepted only full addresses, so the frame was left with no
   way to identify it. A frame with nothing to identify it is silently left out, so the click looked
   as if it were on the top page. Then the start step was taken from the click's own page address,
   which for a click inside a frame is the frame's own document. Many sites use frames like this, so
   on any site whose frame cannot load on its own, the workflow would simply break. I reproduced the
   drop in a plain browser, outside Studio.
2. *No question when there is nothing to tick.* "How can we tell it worked?" was asked only when
   Studio had a ready-made check to offer. When a step's result only shows on the screen, like a tab's
   text, there was none, so the person was never asked. Every test then had to end "couldn't confirm".

*Fix:*
- A relative frame address now counts as the frame's identity, checked against the frame's full
  address.
- A recording whose first step is inside a frame now starts on the tab's real page.
- When the last step has no proof but the recording saw the screen change, Studio now asks "How can
  we tell it worked?" with just a box to type words you see, and those words become the check.
- **Checked on the real site:** the replay opens the Tabs page, clicks inside the frame, and panel 3
  shows.

**Changed:** `recorder/frame_utils.py`, `compiler/build.py`, `conversation/clarify.py`. New tests:
`test_frame_chain_kept.py`, and one more in `test_compile_clarify.py`. Python: 3154 passed, 0 failed.

Also seen: Studio's window was minimized before the first test (the tester didn't do it), and the
size guard stopped that run with "the test browser is not showing the page at its normal size". That
is correct behavior, but the message doesn't say why.

**Test 2 (fresh recording) — result: FAIL, one last gap.** Both frame fixes worked:
- The recording kept the frame, the workflow opens the real Tabs page, and the click happens inside
  the frame. The tester watched this live in both runs.
- Studio asked "How can we tell it worked?" with a typing box, and the tester typed the panel's
  first words.

But both test runs still said "couldn't confirm".

**What was really wrong:** a check only proves a step if its answer could have been "no", so the
replay counts how often the words are on the screen before the step and again after. Both counts
read only the top page, never inside its frames. The panel's words live inside the frame, so the
count was 0 before and 0 after: never "new", so never proof. Any check on words inside a frame had
the same problem. *Fix:* the words are now counted across the page and all its frames, the same way
before and after. On the real site, the step's verdict is now "proven".

**Changed:** `runtime/app/assertions.js`; new test `runtime/test/unit/test_text_check_inside_frame.js`.
Runtime: 1398 passed, 0 failed. The test engine was rebuilt.

Also seen: the typing-only card still says "Tick what applies, then press the arrow." (BUILD-93).

**Test 3 (fresh recording) — result: PASS.**
- The recording kept the frame. The workflow opens `https://jqueryui.com/tabs/` and clicks the tab
  inside the frame. The tester watched both runs live: the first tab showed, then the third, all
  inside the frame, with the top page unchanged.
- The tester typed the panel's first words on the "How will we know it worked?" card. Both test
  runs passed with "Step 2: confirmed", no warning, and ended in `execute_success`.
- Approved.

Also seen: the first question read "The task text is empty. What should this task do each time?",
which is odd wording for a workflow with no description. The click step is again named like the
page-open step (BUILD-93).

**Final status of WF-2 Segment D: PASSED** (P-18).

### WF-2 Segment E — picking a date from a pop-up calendar inside a frame (assigned 2026-11-16)
On jQuery UI's "Datepicker" demo, which also runs inside a frame, clicking the date box opens a small
calendar. The workflow picks a day, and the box filling with that date is the proof. It passed in
September (P-6) under the older rule.

**Test 1 — result: FAIL.** The recording, the frame and the date pick all worked: the box held
"11/15/2026" in both test runs, read live. But both tests failed with "The step ran, but the page
didn't end up where it should have", on the check the tester had typed: "11/15/2026" on the screen.

**What was really wrong (two causes, both general):**
1. *A "words you see on the screen" check never looked inside text boxes.* It read only the page's
   text, and a date, name or amount sitting in a box is not part of that text. To a person it is
   plainly on the screen. So any check on a value shown in a box always failed, on every site.
   *Fix:* what a visible text box holds now counts as on the screen, the same way before and after
   the step. Passwords, hidden fields and file pickers are never read.
2. *The date question had no honest answer.* The person had already said the date stays as
   recorded. Studio then asked "Is it always 1 day before today?" and the only other answer was "No,
   it is different every time". Picking it showed the summary "Whoever uses it still fills it in",
   which was untrue. *Fix:* when the date is fixed, the other answer is now "No, always 15 Nov 2026 —
   It stays exactly as you recorded it", with a summary to match.

Checked on the real site: the date step's verdict is now "proven".

**Changed:** `runtime/app/page_scripts.js`, `runtime/app/assertions.js`, `conversation/clarify.py`.
Tests added (runtime: a filled box counts and a password box never does; Python: the date question).
Runtime: 1399 passed. Python: 3155 passed. Test engine rebuilt and Studio restarted.

Also seen (TODO BUILD-93): the "Which parts change every time?" card had "Due date" ticked in advance,
just after the person said "Always this one".

**Test 2 (fresh recording) — result: BLOCKED at the date question.** The tester stopped, rightly. The
date question still offered only "always 1 day before today" or "different every time".

**What was really wrong:** my first fix decided the second answer from whether the date was an input
*when the questions were made*. Studio makes all its questions at once. At that moment the date was
still an input, and only became fixed when the person unticked "Due date" on the card before. So the
wrong pair of answers was already on the card. *Fix:* the date question now always offers all
three honest answers, whatever was answered before:
- "Yes, always N days before/after today".
- "No, always 15 Nov 2026 — It stays exactly as you recorded it". This keeps the recorded date and
  removes it from the things a user types in.
- "No, it is different every time".

**Changed:** `conversation/clarify.py`; the test was updated. Python: 3155 passed. Studio restarted.

**Test 3 (fresh recording) — result: PASS.**
- The date question showed all three answers, and the tester picked "No, always 15 Nov 2026". The
  built workflow keeps that fixed date and asks the user to type nothing.
- The tester typed "11/15/2026" as the proof. The check now reads the date box, and it passed.
- Two test runs: "Step 1: confirmed / Step 2: confirmed", no warning, `execute_success`. Watched
  live in the second run, the box in the frame went from empty to "11/15/2026" and stayed there
  until the run ended. Approved.

Also seen (BUILD-93): two questions about the same date (the "always this one?" card about "15",
then the date card); "Due date" ticked in advance again; the first run's time includes building the
test copy.

**Final status of WF-2 Segment E: PASSED** (P-19).

### WF-2 Segment F — typing a few letters and picking a suggestion (assigned 2026-11-16)
On jQuery UI's "Autocomplete" demo (inside a frame), typing "Ja" shows a short list of suggestions
under the box. The workflow picks "Java", and the box then holding "Java" is the proof. The race:
the list appears a moment after typing, so a replay must wait for it. It passed in September (P-7)
under the older rule.

**Test 1 — result: FAIL.** The recording was right: the tester typed "Ja", the list showed "Java"
and "JavaScript", and "Java" was picked. But both test runs stopped at the pick: *"Couldn't tell
which of several matching elements to use. It matched “Java”, “JavaScript”."* Also, the tester
answered "nothing changes" about the typed letters, yet the workflow still asked for "Tags" every run.

**What was really wrong (two causes, both general):**
1. *An exact name counted no more than a longer name that contains it.* In September, three days
   after this leg passed, the replay was made to find text that merely *contains* the recorded
   words. That was on purpose, so that a button whose name gained a keyboard hint still matches. The
   plan was for the step that picks between candidates to prefer the closer one. But that step gave
   "Java" and "JavaScript" the same score. Two of its comparisons could also never agree: the
   recorded "role" was only the tag name ("li"), and the recorded "anchor" was code ("ul#ui-id-1").
   So even the right item scored only 0.33, below the 0.5 needed. Any list where one option's name
   is the start of another's ("Edit" / "Edit all", "Save" / "Save as") was affected. *Fix:*
   - The same words now score full marks, while words merely contained in a longer name score less,
     and on their own are never enough to click.
   - A keyboard hint on the live name is ignored first, so September's case still matches exactly.
   - A role that is only a tag name, and an anchor written as code, are no longer compared with
     things they can never match.
   - Checked on the real site: "Java" now scores 1.0 and "JavaScript" 0.4, the whole workflow
     replays, the box ends "Java", and the last step is proven.
2. *"Nothing changes" was silently not applied.* Keeping the typed letters fixed changes the typed
   value, but the typing step's check still said "the box holds {{Tags}}". Studio never removes
   something a step still uses, so "Tags" stayed. *Fix:* keeping a value fixed now also rewrites any
   check written from it, so it checks the recorded value and the input can be removed.

**Changed:** `runtime/app/resolver.js`, `conversation/clarify.py`; new test
`runtime/test/unit/test_resolver_exact_beats_contained.js` (the recorded element matches its own
fingerprint, exact beats contained, a look-alike alone is refused, the keyboard-hint case still
holds), and one Python test. Runtime: 1403 passed. Python: 3156 passed. Test engine rebuilt, Studio
restarted.

Also seen: the suggestion pick is listed as "Clicked “Tags:”" instead of "Java" (BUILD-93).

**Test 2 (fresh recording) — result: PASS.**
- The tester said "always the same one", unticked "Tags / Ja", and typed "Java" as the proof.
- The built workflow has no inputs. The typing step checks "Ja", and the pick checks "Java".
- Both test runs confirmed all 4 steps with no warning and ended in `execute_success`. Watched live,
  the box went from empty to "Ja" to "Java" each time, and never showed "JavaScript". Approved.

**Final status of WF-2 Segment F: PASSED** (P-20).

### WF-2 Segment J — clicking a control that lives inside a hidden-structure widget (assigned 2026-11-16)
Some sites build their controls as self-contained widgets whose insides are hidden from the page
(a "shadow DOM"). Shoelace is a library of such widgets. September's pass clicked a demo button
that does nothing visible, so its result could not be proven. This time the workflow opens a
"Details" panel on Shoelace's Details page, and the panel's text appearing is the proof.

**Test 1 — result: FAIL.** The recording worked, and the first "Toggle Me" panel opened. But both
test runs stopped with *"Couldn't tell which of several matching elements to use … It matched “Toggle
Me”, “Toggle Me”, “Disabled”."* The page has two widgets that are exactly alike, both called
"Toggle Me". The replay rightly refused to guess between them.

**What was really wrong (three causes, all general):**
1. *Nothing recorded which of two identical widgets was meant.* They have the same name, the same
   look and the same text. The only difference is the part of the page they sit in: the first is
   under the heading "Details", the second under "Customizing the Summary Icon". I checked this on
   the live page. Studio never recorded which section an element is in. Also, the list of
   hidden-structure containers the element is inside was worked out during recording and then
   thrown away before saving (since September). *Fix:*
   - Every click now records its section heading, the last heading above it on the page, through
     hidden-structure widgets. That list of containers is now saved too.
   - On replay, an element under the same heading scores higher. This is weighed only when both
     sides have a heading, so older workflows behave exactly as before.
   - Checked on the real page: the first "Toggle Me" now wins by a clear margin, and so does the
     second when that is the one recorded.
   - Two identical widgets under the *same* heading are still refused, as they must be.
2. *A check that is always true was taken as proof.* The step's only check was "“Toggle Me” is on
   the page", which is true whether the panel opened or not. Because a required check existed,
   Studio did not ask "How can we tell it worked?", and the test could only end "couldn't confirm".
   *Fix:* only an on-screen words check, the kind a test can count as "it finished", now counts as
   proof. Otherwise the question is asked.
3. *(Not yet seen to break a run.)* A promo pop-up was open while recording. Its "No, thanks" click
   was put aside as having done nothing. In a fresh browser the pop-up did not appear, so this
   retest will show whether it matters.

**Changed:** `recorder/bridge.js`, `recorder/session.py`, `compiler/build.py`,
`packages/conxa-core/.../skill_spec.py` (new `section_heading` on the fingerprint),
`runtime/app/page_scripts.js`, `runtime/app/resolver.js`, `conversation/clarify.py`. Tests added on
both sides. Python: 3160 passed. Runtime: 1407 passed. Test engine rebuilt, Studio restarted.

**Test 2 (fresh recording) — result: PASS.**
- The click now records "Details" as its section, and the list of containers it is inside is saved.
- Studio asked "How will we know it worked?". The tester typed the panel's first words, "Lorem
  ipsum dolor sit amet".
- The promo pop-up appears on every fresh load, in the test pane too. The recorded "No, thanks" step
  closes it.
- Watched live in both test runs: the pop-up closed, then the *first* "Toggle Me" opened, and the
  other stayed closed. All 3 steps were confirmed, with no warning and `execute_success`. Approved.
- The proof is real. I counted on the live page: those words show once before the click (from a
  panel that is open by default) and twice after. A click that did nothing would fail the check.

**Final status of WF-2 Segment J: PASSED** (P-21).

### WF-2 Segment C — a result that appears only after a few seconds (assigned 2026-11-16)
On the-internet's "Dynamic Loading 2" page, pressing Start shows a loading bar. After about five
seconds the words "Hello World!" are added to the page. A replay must wait for them instead of
giving up early. Those words appearing are the proof.

**Test 1 — result: PASS** (first try, no fix needed).
- Recorded one click on "Start". Studio asked "How will we know it worked?" with a typing box, and
  the tester typed "Hello World!".
- In both test runs the click step waited about 9 seconds. Watched live, the words appeared about 5
  seconds in, and the step finished only after that. Both runs were confirmed with no warning and
  `execute_success`. Approved.

Also seen: the "Tick what applies" wording on a card that has nothing to tick (BUILD-93, item 10).

**Final status of WF-2 Segment C: PASSED** (P-22).

### WF-2 Segments A + B (first half) — a button whose hidden name changes every visit, and a button whose label you change (assigned 2026-11-16)
On the UI Testing Playground, the "Dynamic ID" page has a button whose hidden internal name changes
on every visit, so a workflow must not rely on that name. The "Text Input" page has a button whose
label becomes whatever you type into a box. The workflow clicks the dynamic button, types "Conxa"
on the second page, and clicks its button. The button then reading "Conxa" is the proof. The
moving-button half of Segment B is tested separately on the local practice page.

**Test 1 — result: PASS** (no fix needed).
- 7 actions were recorded, with the second page reached through the site's own links.
- The changing internal name never got into the workflow: a search of the built package for it, or
  for anything shaped like it, found nothing.
- The typed text became an input. The proof is "you see the button name you typed".
- Three test runs passed, all steps confirmed with no warning. Watched live: the dynamic button had
  a different hidden name on every load, and the button's label became the value typed in that
  run, including "Second", not the recorded "Conxa". Approved.

Two other test runs were stopped by the size guard because Studio's window had been minimized. The
tester did not do that, and it is the second time today (Segment D too). Filed as BUILD-94. Also
seen (BUILD-93): doubled quotes in "Done when ““set new button name”” appears"; the click on "Text
Input" is tied to a neighbouring tile's name ("Client Side Delay"), which is harmless here.

**Final status of WF-2 Segments A and B (first half): PASSED** (P-23).

### WF-2 Segment B (second half) — a button that keeps moving and renaming itself (assigned 2026-11-16)
The local practice page "Shapeshifter" (`mutator.html`) changes every element's hidden name every 0.7
seconds. It also moves the one button to the bottom of the page and changes its label at random
between Go, Send, Do It and Proceed. The workflow types a name and an email and clicks that button.
The page then saying "Thanks, <name>!" is the proof.

**Test 1 — result: PASS** (no fix needed).
- The built workflow uses none of the changing hidden names. The boxes are found by their visible
  hints, and the button by its label plus its place on the page.
- Name and email became inputs, and the proof is "you see the name on the screen".
- Five test runs all confirmed all 6 steps, with no warning and no recovery used. Watched live:
  "Thanks, Mega Test Run!" with the recorded values, and "Thanks, Second Pass!" with new ones.
  Approved.

Honest limit: the button was found by waiting a few seconds for its label to come round to "Proceed"
again, which this page always does. A button whose label changed for good would test the deeper
self-healing; that was not exercised here. Also seen: doubled quotes in "Done when ““name””
appears" (BUILD-93).

**Final status of WF-2 Segment B (second half): PASSED** (P-24).

### WF-2 Segment G — a page that keeps loading more as you scroll (assigned 2026-11-16)
On the-internet's "Infinite Scroll" page, new paragraphs of filler text are added each time you
scroll near the bottom. The workflow scrolls until several blocks have loaded, then clicks a
paragraph that only exists after scrolling. A replay must scroll the same way to reach it. Proof is
harder here, because the click changes nothing on screen. The proof is that the replay reached and
clicked that late paragraph, checked independently.

**Test 1 — result: FAIL** (and a poorly chosen test on my part).
- The click on a filler paragraph was not recorded. That is correct: clicking plain text is not an
  action, and a person would never record it.
- But the test exposed a real, general defect. The three scrolls the person made, to 114, 514 and
  914 pixels down, became *one* scroll of 114 pixels in the built workflow. Studio's cleanup step
  treated scrolls in a row as duplicates and kept only the first. So a replay scrolls a fraction
  of the way. On any page that loads more as you scroll, it never loads what later steps need.

*Fix:* every scroll the person made now stays its own step, with its own distance. Scrolls that
moved nothing, or landed exactly where the last one did, are still removed earlier. They are kept
separate rather than added up, because such pages load more at each pause near the bottom.

Since the-internet's page has nothing to click after scrolling, I added a small local practice page
(`fixtures/infinite-list.html`). It is a list that loads 10 more numbered items each time you scroll
near the bottom. Each item has a "Pick" button, and picking one shows "You picked item N". An item
past 10 only exists after scrolling, so a replay that does not scroll far enough cannot pass.

**Changed:** `compiler/step_anchors.py`; new test `test_consecutive_scrolls_kept.py`; new fixture.
Python: 3159 passed. The failures were two known timing-flaky tests, filed under TEST-FLAKE.

**Test 2 — blocked by my practice page.** It was shorter than Studio's tall recording pane, so it
could not scroll at all. I made the first load 20 taller items (about 1700 px against a 992 px
pane) and checked it in a plain browser at that size.

**Test 3 (fresh recording) — result: PASS.**
- Three wheel scrolls, then "Pick item 27". The built workflow keeps **three separate scroll steps**
  (600 px each) and the click, which must show "You picked item 27".
- Five test runs all confirmed all 5 steps, with no warning. Watched live in the last run: 20 items,
  then 30, the pick, then 40, with "You picked item 27" on screen. Approved.

Also seen (BUILD-93): the click's main way of finding the button came out as "Pick item", without
the 27. It still picked item 27, because the exact-text signal names it.

**Final status of WF-2 Segment G: PASSED** (P-25). **WF-2 is now done**: every segment re-passed
under the strict rule except Segment I (drag and drop), which stays a known limitation.

### WF-3 Leg A — a 50-step warm-up on a computer database (assigned 2026-11-16)
On the public "computer database" demo (a practice site for load tests; it confirms adds and deletes
but does not keep them), one long recording does all of this:
- search "Apple" and open a result, then go back;
- filter "IBM" and open a result, then go back;
- add 5 computers, each with a name, two dates and a company;
- delete 2 computers.

That is about 50 actions. The checks: nothing the person did goes missing, the test replays it all,
and the site's own "Done!" messages appear.

**Test 1 — BLOCKED: the site is gone.** `computer-database.gatling.io` no longer has an internet
address. Lookups through two public name servers return no address, and connections fail. Nothing
could be recorded. This is not a Studio problem, except for one small one: the recording pane showed
a technical "navigation_error … Call log" banner instead of a plain "this site can't be reached"
(BUILD-93).

**Replacement:** the leg is about one long recording, not about that site. demoqa's "Web Tables" page
has the same shape:
- add 5 records, about 8 actions each;
- delete 2 particular rows;
- search the table.

The table lives only in the browser page, so nothing is written anywhere.

**Test 2 (demoqa Web Tables) — result: FAIL**, with four real defects.
- *Site quirk first:* on demoqa, any delete resets the table to its 3 starting rows. So the tester
  deleted a starting row first, then added the 5 records, then searched. That is not a Studio fault.
- **1. Typing in the Add form became "close a popup" steps.** The person opened the form, so it is
  part of the task, not an interruption. But every field was built as "close it if it shows up",
  and the replay failed at the first field every time. Answering "keep it as a normal step" 30
  times (once per field) did not help.
  *Fix:* the popup hint is dropped where it cannot mean "close it". That is any step that is not a
  click, and any window an earlier step of the person's own opened. The 30 questions go with it.
- **2. Records 2–5 lost all their typing.** Two causes:
  - The typing for a field is stamped a few milliseconds after the click on the next field. A
    time sort pushed each value after the next click, and the last one after "Submit".
  - Typing into a fresh copy of the form was merged into the first copy as a "correction".
  *Fix:* only pop-up answers are put back in time order; everything else keeps the order it
  happened in. Typing merges only in the very same box, right away. A new box, a later return to a
  box, and emptying a box are each kept as their own step.
- **3. The trash-icon click was not recorded.** The icon has no tag or attribute saying "button";
  the site wires it up in code. *Fix:* a titled or labelled element that shows the pointing hand,
  with no real button or link around it, counts as a control. A button or link around it still wins.
- **4. Two same-looking clicks on different elements merged into one.** Seen in review, not live:
  two rows' "Delete" icons, or two "Add to cart" buttons. Now both run.

Checked by rebuilding the same recording offline: no "close a popup" steps (11 before), 33 typing
steps with all 5 records, and no popup questions.

Also filed (BUILD-93): the search box is named "Add" after the nearby button.

**Test 3 (fresh recording) — result: FAIL**, but the earlier fixes held:
- the trash-icon click was recorded;
- there were no "close a popup" steps;
- all 5 records' typing was kept, in order.

New defects:
- **The "the box holds this value" check kept an old name.** Two search-box typings were both called
  `add`. The second was renamed `add_2`, but only its typed value changed, not its check. The check
  then waited for the old value and failed every time. *Fix:* a rename now changes the step's checks
  too, in both places that rename.
- **Emptying the search box became a required input.** Nobody can supply an empty value, so the
  test could not even start. *Fix:* emptying a box is a plain step, never an input.
- **Every step's name described the step before it.** The review model is not told each step's
  number, so it counts them itself, and here it counted from 1. "Type a name into the search box"
  sat on the select-all key, and the "can't be undone" questions named the wrong steps. *Fix:* each
  step now states its number (from 0). A reply that has no step 0 and one step past the last is
  moved back by one.
- **"Always enter these same 5" left the inputs in place.** A check on the last step still named
  them. *Fix:* the answer now fixes those checks too, as the values question already did.
- Also seen: before the build the model itself asked "How many entries should it add each time?". It
  understood the repeat. That answer is not yet linked to the repeat question (TODO).

**Test 4 (fresh recording; Studio and the local cloud restarted after the session ended) — result: FAIL.**
Confirmed fixed live:
- every step's name matches its step;
- clearing the box is a plain step;
- there is no stale `{{add}}` check.

New defects:
- **The Delete click could never be found.** The bin icon's only way of being found was a full path
  from the top of the page. The step works inside the row it is bound to (Kierra's), and a path
  from the page top cannot match there. The test failed at Delete three times, with and without
  the safety pause, on a page that visibly had the row. Two causes:
  - Nothing turned the icon's tooltip, "Delete", into a way to find it.
  - An attribute every tooltip shares (`data-toggle="tooltip"`) was treated as identity.

  *Fix:* an element named only by its tooltip is found by it (`span[title="Delete"]`, unique inside
  the row). Widget wiring attributes no longer count as identity.
- **"Always these same 5" left one input behind** (`department 5`). It was named inside a "the
  entry shows up" completion check, which the answer did not touch. *Fix:* restoring a recorded
  value replaces it anywhere in the step's checks.
- **The repeat question came last instead of first.** It was missing from the list of question
  kinds asked right after the build, so only the later re-check raised it. *Fix:* added.

**Test 5 (fresh recording) — result: FAIL before any step ran.**
Working now:
- the repeat question came first;
- "department 5" was gone;
- the Delete step had its tooltip signal.

But the test card said "no inputs", while the built workflow demanded one, `search term 2`, and refused to start. Here is
how it happened. "How can we tell it worked?" offered "You see the search term 2 on the screen", built
while that value was still an input. The person had already answered that nothing changes, so the
value had become fixed text. Ticking the option still wrote a check on the old input name, and the
build then declared that name as a required input again. *Fix:* the question remembers each
input's recorded words. If the input is gone when the answer is applied, the check looks for those
words ("Conxa").

Also seen: a 23-minute recording made before a network drop was saved, but then "has no video to
watch" (its video file was finalised only after the recording pane was closed). The tester recorded
again (TODO).

**Test 6 (fresh recording) — result: FAIL at Delete, twice.** The workflow now has no inputs and
the "Conxa" check works. The Delete click happened, and the row went away, but then the step failed.
Its required check said "the bin icon is still on the page", named by the icon's own page path. Several of Studio's
waiting rules fall back to the clicked element's own address. "It is there afterwards" would hold even if the
click did nothing, and after a delete it is the opposite of the result. *Fix:* where waits become
checks, a wait for the acted-on element itself never becomes one. The Delete step is now proven by
"the page changed" and its delete completion check. (Rebuilt offline from the same recording to
confirm.)

**Test 7 (fresh recording) — result: FAIL at the fifth Submit, twice.** The Delete now passes, and so
do Submits 1–4. The fifth said "couldn't confirm". But Submits 1–4 were not really proven either:
- demoqa's form saves inside the page and sends nothing to the site. Each Submit's only proof was
  "the app accepted the request". Submits 1–4 happened to coincide with an ad or analytics write
  from another website, answered OK. The fifth did not, so the hole showed.

Three general fixes:
- **A request to another website is background, not proof.** Its failure does not refuse the step
  either. (Compared by site name; noted as a shortcut.)
- **A save or send after typing always gets "what you entered now shows up on the page".** Before,
  this check was added only when the AI ranking model suggested it; now code always adds it.
- **That check now works on forms like this one:**
  - It counts the page's text only, not what the form's boxes hold. Counting boxes made the value
    "move" from box to row with no change.
  - It tries each value of the entry, nearest first, and uses the first long enough to mean
    something. The nearest one here, "QA", is too short; the salary "1001" works.

Rebuilt offline: every Submit now has that check.

**Test 8 (fresh recording) — result: PASS.** 77 actions, 79 steps, no inputs.
- Answers:
  - same five records each time;
  - always this Delete;
  - "No, always fill in these same 5";
  - no approvals before steps that can't be undone.
- Both test runs paused before the steps that can't be undone, ran them for real, and confirmed
  every step. There was no "couldn't confirm".
- Watched live: the table went from Cierra and Alden to Conxa E1…E5, and Kierra was gone.
- Every Submit was proven by "what you entered now shows up on the page", using the salary.
- Approved.

**Final status of WF-3 Leg A: PASSED** (P-26).

**Still to do (asked for by the user):** the "Yes, fill in each row of a list" answer to the
repeat question is built and unit-tested, but has not yet been run end to end. Code spots the repeat, the model writes the question in plain words, the person chooses, and
code makes the change. That comes after this leg passes without it.

Smaller notes, filed in `TODO.md`: after Enter the typed search words were saved a second time, so
Studio asked "A few things you typed are not in the workflow — is that okay?" about a duplicate.
And a fresh recording that fails on one element is called "may have been redesigned".

### WF-3 Leg B — saucedemo 6× sort/add/cart/remove loop, then checkout (2026-11-17)
Deviations (site limits): loop 6 adds 1 product (only 6 exist); sort chosen by keyboard (the native
dropdown list cannot be clicked by the test driver).

**Test 1 — FAIL at step 2 of 79** (sort pick): "Verification failed: value_equals". The check expected
the menu's label "Name (Z to A)"; the page reported its hidden code "za". Seven more defects in the
compiled workflow. Fixes (general, not site-specific):
- A dropdown check accepts the label or the code.
- A keyboard pick is one step: the recorder no longer saves the menu's typing/arrow echoes, and compile
  drops them from older recordings (this also removed six bogus required inputs).
- A removed cart row is keyed on its product name, not a price another row shares; a Remove is proven
  by "what you removed is gone from the page" (the site sends nothing on remove).
- A button drawn as an input is a button (named "continue", not nearby noscript text) and is proven by
  its page change.
- "Sign in and test again" keeps the test inputs; the sign-in card says "test again", not "recording".
- **Owner decision (BUILD-95 (1)):** buttons whose words only move forward (Continue, Next, Back,
  Sign in) are no longer "can't be undone"; pay, delete and order words still ask.

**Test 2 (fresh recording) — PASS, Approved.** 75 events → 56 steps, 37 checks, 3 inputs (first/last
name, zip). 15 questions answered on purpose (6 Remove asks, 6 row locks all by name). Test runs
7 min 33 s and 6 min 57 s, every step confirmed, no "couldn't confirm"; Human Edit not needed. Watched
live: 5 items, total $107.95, then "Thank you for your order!", then the login page.
Workflow `wf3-b-shop-loop-r2-8835a512`, session `535a5e6a-e5b3-4204-bdbd-6e30ea079e1f`.
Caveat: the workflow has no "Thank you" check of its own (BUILD-95 (4)); chat's attempt to add one
failed ("assert step has no element selector to read text from") and was undone. Manual stopwatch
time not taken. New issues filed as BUILD-96.

**Final status of WF-3 Leg B: PASSED** (P-27).

### WF-3 Leg C — compile determinism at scale (2026-11-17)
Leg B's recording (session `535a5e6a…`) was built three more times on copies of Studio's data, using
Studio's own compile path. **Result: PASS.** All three builds and the approved package match exactly
on step count and order (56), and on every element address, identity record, wait, frame/tab chain,
consequence and branch rule. Zero differences in any pair. Each build took about 108 s.

What does change between builds is the AI-written meaning (allowed to vary, but worth tightening):
step descriptions, intent names, the name of the sort-menu input (`product_sort_order` in two builds,
`name_a_to_z` in the third, which a person would see), empty phases, and one optional address check.
`scripts/recompile_session.py` is out of date: it needs a cloud token, writes into the real session
folder, and skips inputs the Studio path passes. Both filed as BUILD-97. No code was changed.

**Final status of WF-3 Leg C: PASSED** (P-28).

### WF-3 Leg D — ParaBank banking marathon (2026-11-17)
**Test 1 — FAIL at step 35 of 77** (Bill Pay account number): "Couldn't tell which of several matching
elements to use." All 9 Bill Pay boxes shared one address; each box's unique `name` was never used to
find it. The four "Find Transactions" buttons had the same problem, and a box caption climbed out of
its form to the site's tagline. *Fix:* a form box is found by its own unique name when the saved page
shows only one; a button next to a landmark keeps the landmark's uniqueness; the best address is
tried first; a caption search stops at the box's own form.

**Test 2 (fresh recording, 93 actions) — FAIL at step 10 of 88** (open the receiving account): "Bound
record could not be uniquely located on the page". The build had tied 22 clicks to "the row holding
X" without asking: two side-by-side page panels were taken for a list, a menu value "1" was found
inside balances like "$136.46", and account links were tied to a balance that changes. *Fix:* only
real data rows count; a harmless click named by its own row text needs no row lock; a recorded value
matches a whole value, never part of one. Steps are now named by their caption ("Type into 'Amount'").
The rule "never act on a different row" was kept as is (alternatives filed as BUILD-99).

**Test 3 (same recording, built again) — PASS, Approved.** 88 steps, 22 checks, 4 inputs; every step
confirmed in 1 min 06 s with no "couldn't confirm". Checked on the bank's own records: transfers of
exactly 15.15, 16.16 and 17.17 left account 12456 today, and new savings account 46755 holds its $90.
Caveat: the two bill payments cannot be seen on this demo site (they pay from a loan account that
lists no transactions); only Studio's confirmed "Bill Payment Complete" page proves them.
Workflow `wf3-d-bank-marathon-r2-067c16df`, session `a6722389-8188-4b4c-85e4-57d9c6bf14d9`, run
`r_mwk111tv_0rygn`. Recording was 93 actions, short of the 100–140 the leg suggests.

**Final status of WF-3 Leg D: PASSED** (P-29). **WF-3 is complete.**

### WF-4 — real-app journeys (2026-11-17)
**Leg D (Wikipedia search, then the same on German Wikipedia).** An early take falsely passed on the
wrong article: typing was saved after its Enter, so the run searched "Au". *Fix:* typing is saved
before the click or key that follows it, never re-sent when the page unloads; old recordings are
repaired at build. A search box with no id is now found through its form's id, which works in any
language. **PASS, Approved.** German flip reached "Automatisierung" by language-neutral signals and
then failed only its English address check (left as is, BUILD-101).

**Leg C (copy a shop price, then search Wikipedia).** The Search click was set aside as "did nothing"
(the page change was credited to a repeated typing), and ad frames hid the move to Wikipedia. *Fix:*
as above, plus only the main page's loads count, a click after which the page moved is never set
aside, and a captured value never becomes a run input. **PASS, Approved;** the price read back
(Rs. 400) matches the shop's own search page.

**Leg B (saucedemo checkout).** The typed check "Thank you for your order" went on the last step
(logout), where it is gone. *Fix:* a typed check goes on the step after which its words were on
screen; chat can now move a check through a change card. **PASS, Approved** (3 runs; total $36.69,
"Thank you for your order!"); chat move and undo verified.

**Leg A (OrangeHRM: add user, edit to Disabled, delete).** Three failures:
- The employee suggestion is picked on mouse-down and vanishes, so no click was saved. *Fix:* such a
  press is recorded as the click.
- **Safety:** the person's "Check first" for the delete was silently dropped. *Fix:* it always lands
  as an approval before the delete click.
- Boxes were found by a style they only have while focused. *Fix:* passing-state classes are dropped.
**PASS, Approved:** run paused for approval before delete; the demo's own API showed the user created,
Disabled, then gone; nothing left behind. Known: the pick keeps the fixed name (BUILD-102 (6)).

**Leg E: BLOCKED** (needs a sign-up and posts a public review).
