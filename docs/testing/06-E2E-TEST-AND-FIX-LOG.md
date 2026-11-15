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
