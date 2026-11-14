# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**The browser now lets go for the person's turn, and keeps your hands off the rest of the time — 2026-11-14**
While a workflow runs in the app's browser, a stray click or a mouse resting on the page can change what the page shows and throw the run off. The test browser already ignored your clicks; now the Run window in Conxa Execute does too. When a step is meant for you, like entering a code or solving a check, the browser now opens up so you can click, then closes again once you press Done.
This means a run can't be spoiled by an accidental click, and you are never locked out of a step that needs you.
— 2026-11-14

**Studio can now add smarter proof that a step really worked — 2026-11-14**
After a step that sends, saves or downloads something, Studio has to know the work really happened, not just that a button was pressed. Until now fixed rules chose how to check that. Studio can now ask a small, fast decision helper which kinds of proof fit each step, and it can add four new kinds: the page moved on and stayed there, what you entered shows up again on the page, what you entered is still there when the page is opened again, and a later step that needs the result worked. Each new check is only added when your recording really contains what it needs, none of them can stop a run on its own, and if the helper is off or unreachable everything works as before. These have been tested in isolation but not yet on a real website.
— 2026-11-14

**The test browser now fills three quarters of the window with nothing beside it — 2026-11-14**
While the test browser was open, a thin strip of two small icons sat on its right edge and took space from the browser. That strip is now hidden while the browser is open, and the browser always takes 75% of the window. The strip comes back as soon as you close or hide the browser.
— 2026-11-14
