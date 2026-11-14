# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**A test that really worked no longer ends as "needs a decision" or "still working" — 2026-11-14**
A test uploaded a file to Google Drive successfully every time, but Conxa ended two runs asking you to decide about a popup that was only the little preview card Drive shows when the mouse rests on a file, and parked the third run forever because a finished upload's progress bar never left the screen. Conxa now looks again after moving the pointer away before it stops on a popup, stops waiting for a progress bar that has not moved, and no longer counts a word that was already on the page as proof that a step worked. The test browser also keeps your mouse and keys off the page while a test runs, so a hover can't spoil it.
This means a working workflow is no longer reported as stuck, and a step is only called done when something actually changed because of it.
— 2026-11-14

**The browser now lets go for the person's turn, and keeps your hands off the rest of the time — 2026-11-14**
While a workflow runs in the app's browser, a stray click or a mouse resting on the page can change what the page shows and throw the run off. The test browser already ignored your clicks; now the Run window in Conxa Execute does too. When a step is meant for you, like entering a code or solving a check, the browser now opens up so you can click, then closes again once you press Done.
This means a run can't be spoiled by an accidental click, and you are never locked out of a step that needs you.
— 2026-11-14

**The helper that suggests what proves a step worked now talks to the right address — 2026-11-14**
Studio can ask an outside helper which kinds of proof fit each step. We had guessed the wrong address and the wrong way of asking, so it could never have answered. It now uses the address and format the provider documents, and it reuses the same key as the other models.
This means the next workflow you record can actually get those suggestions.
— 2026-11-14

**The earlier fixes now work for any website, not just the one that showed the problems — 2026-11-14**
Some of the first fixes only treated what we saw on one recording. They now follow general rules. When an app shows an upload or a save, Studio only trusts the "finished" message that appears in the same place as the progress, so a window notice or a tooltip can't pass for proof. It no longer builds a check out of words you just clicked or typed, since those are only true for your recording. The upload step is found by what it is (a file chooser), not by where it sits on the page. Only things you actually typed or picked are asked about as "typed". A workflow's sign-in status now follows your current sign-ins instead of a stale copy, and the recording message counts what you did, not what the page did by itself.
— 2026-11-14

**A broken build step is now shown, and a test that can't prove the upload no longer says "passed" — 2026-11-14**
When a recording was built, one helper step could crash without telling anyone. That left some steps with no description, refused your answer to "How can we tell it worked?", and still ended with a plain "passed". Now a crashed build step shows a clear "Part of the build failed" message with a Build again button, and the reason is one click away. The test card says "Couldn't confirm it finished" when nothing proved the work was done, and Approve only opens after you press "I checked — it worked".
Studio also no longer mixes up menu entries (it called "File upload" by the name of the entry above it), no longer treats on-screen clutter like "maximized" as proof an upload worked, no longer asks about "things you typed" that were really page changes, and drops old checks that only fit the file you recorded with.
— 2026-11-14

**Studio can now add smarter proof that a step really worked — 2026-11-14**
After a step that sends, saves or downloads something, Studio has to know the work really happened, not just that a button was pressed. Until now fixed rules chose how to check that. Studio can now ask a small, fast decision helper which kinds of proof fit each step, and it can add four new kinds: the page moved on and stayed there, what you entered shows up again on the page, what you entered is still there when the page is opened again, and a later step that needs the result worked. Each new check is only added when your recording really contains what it needs, none of them can stop a run on its own, and if the helper is off or unreachable everything works as before. These have been tested in isolation but not yet on a real website.
— 2026-11-14

**The test browser now fills three quarters of the window with nothing beside it — 2026-11-14**
While the test browser was open, a thin strip of two small icons sat on its right edge and took space from the browser. That strip is now hidden while the browser is open, and the browser always takes 75% of the window. The strip comes back as soon as you close or hide the browser.
— 2026-11-14
