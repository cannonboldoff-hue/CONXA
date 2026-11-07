# Live verification checklist (signed-in Studio)

CI covers the runtime half (`runtime/test/e2e/golden_path.js`) and the Studio logic half
(`conxa-cloud/tests/test_compile_flow.py`, `test_understanding.py`). Neither can drive a signed-in Electron
window on a real site. Run this once per release candidate and record the date in `Done.md`.

Closes the manual parts of BUILD-45(e) and BUILD-48(1).

1. **Sign in.** Create a workflow with a task description for a site that needs sign-in. The sign-in card
   opens in the browser panel; finish it; the card goes green.
2. **Record.** Record across a site-opened tab. Pause and resume. Mark one typed value "Changes each run".
   Finish; the compile card runs.
3. **Understand.** The assistant must not say "nothing to ask" for a recording with no typed text.
   It asks which page addresses / records change each run, and Test is not offered until you answer.
4. **Parameterize.** Tick one value. Human Edit shows `{{name}}` in the step and an input with the recorded
   default. Undo restores it.
5. **Test.** The test card is pre-filled. The guard pauses before the first step that can't be undone.
   Stop at the guard; confirm nothing was sent.
6. **Fail on purpose.** Change one input to something that does not exist. The test fails; auto-fix proposes at
   most one gated change with Undo.
7. **Sign-in expiry.** Expire the session mid-test. The card says sign-in expired; sign in again and test again.
8. **Approve.** Approve only after a pass. Any later edit revokes approval.
9. **Restart.** Quit Studio mid-test and reopen: the thread shows an honest "interrupted" state, not a spinner.
