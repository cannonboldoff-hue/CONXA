# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Scrolls that move nothing are no longer saved as steps — 2026-11-13**
While recording, the page sometimes “scrolled” by zero, for example when you were already at the top. Those showed up as empty scroll steps in your workflow. They are now left out, and only scrolls that really moved the page are kept.
— 2026-11-13

**A test no longer fails when the app shows a “done” panel after the last step — 2026-11-13**
A test uploaded a file to Google Drive and the upload worked, but Drive showed a small “1 upload complete” panel, and the test failed saying a popup was asking a question. Now the test first checks that the work really happened, and only stops for a popup that actually covers the page. A panel that leaves the page usable is noted as a plain warning instead of failing a step that worked.
— 2026-11-13

**Words like “complete” are no longer learned as “still working” — 2026-11-13**
While recording an upload, the app’s final message was remembered as a sign that work was still going, so the test kept waiting on a message that meant “finished”. A message that is still on screen when the work ends is now remembered as the result, and the name of the file you picked is never remembered at all.
— 2026-11-13

**When a test fails, you now get one clear message, not three that disagree — 2026-11-13**
After a failed test the chat showed a vague “something went wrong”, then a long guess, a promise to open an editor that never opened, and “I’m not sure how to fix this”. Now the reason for the stop is recorded exactly, the card says so in plain words, and it no longer claims “nothing was saved or sent” when the step may already have run. When the reason is already known, such as a popup with no recorded answer, you get one short message instead of a guess.
— 2026-11-13

**A false “this app may have been redesigned” warning is gone — 2026-11-13**
The test checked for a button that only exists on a later page while it was still on the first page, then warned that the app had changed. It now only checks things that are on the page where the test starts.
— 2026-11-13

**Your account name and email are no longer saved inside a workflow’s way of finding a button — 2026-11-13**
Recording on a signed-in site could save the name and email shown on your account button inside the workflow, which is shared with customers. That text is now left out when a workflow is compiled, and building a package stops with a plain message if an older workflow still has it.
— 2026-11-13

**Build Studio tells you when its test engine is older than your code — 2026-11-13**
Tests quietly ran an older copy of the test engine than the code on this computer, so fixes looked like they had not worked. The test now says so before it starts and tells you which command refreshes it.
— 2026-11-13
