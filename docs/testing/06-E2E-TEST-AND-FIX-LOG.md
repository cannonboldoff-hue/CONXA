# End-to-end test and fix log (started 2026-11-16)

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

| WF | What | Can it be done here? |
|---|---|---|
| WF-1 | Starter marathon (Wikipedia, the-internet, demoqa) | Yes. Sign-in leg uses the group's saved sign-in, not a recording |
| WF-2 | Rich elements (iframes, hover, drag, shadow DOM) | Yes |
| WF-3 | Scale (50 to 100+ steps) | Partly: Leg A and C yes; B and D need sign-in on shop/bank sites |
| WF-4 | Real apps (HR app, shop, two-site, language flip, reviews) | Partly: C and D yes; A/B/E need sign-in or posting on public demos |
| WF-5 | Many files | Partly: public file hosts receive uploads (ask first) |
| WF-6 | Branch steps | Yes |
| WF-7 | Recovery drill | Yes (Studio part); agent part needs an MCP client |
| WF-8 | Break tests | Yes (local) |
| WF-9 | Mega workflow | Partly (sign-in segments as above) |
| WF-10 | Chaos and odd inputs | Partly |
| WF-11 | Plan limits (cloud only) | No browser workflow; API checks only |
| WF-12 | Go / no-go checklist | Blocked for the clean-computer, billing and update phases |
| WF-13 | Sheets to Gmail, GitHub PR merge | Changes real accounts: ask the owner first |
| WF-14 | AI review step | Yes |
| WF-15 | Hand-over step (CAPTCHA) | Needs a person to solve the CAPTCHA |
| WF-16 | Intelligent compiler tour | Yes |
| WF-17 | Copilot chat | Yes |
| WF-18 | Sign-in everywhere | Partly |
| WF-19 | Conxa Execute app | Separate app, not Build Studio |

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

Smaller notes, filed in `TODO.md`: after Enter the typed search words were saved a second time, so
Studio asked "A few things you typed are not in the workflow — is that okay?" about a duplicate.
And a fresh recording that fails on one element is called "may have been redesigned".
