# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**After an upload, the app now looks again to see the file is really there — 2026-11-10**
Seeing "upload complete" does not prove the file arrived. After an upload, the app can now open the same page again in a separate tab, read it, and check that the file's name is on it, more times than before. It never clicks or types, closes that tab, and says "couldn't confirm" instead of guessing when it can't look. It only starts insisting on this after a test of your workflow showed it works on your website, so a site that shows no file list never makes a working upload stop. The cloud dashboard now also shows the steps that keep coming back unconfirmed.
— 2026-11-10

**A recording no longer loses the last few things you did — 2026-11-10**
Each thing you do in the browser is now saved the moment you do it. Before, it was held back while the app watched the page finish its work, so the last steps could be thrown away when you pressed Stop, for example choosing "File upload" and picking a file in Google Drive. The "did it finish" details now arrive afterwards and are added to the step. When you press Stop, the app first collects everything still on its way. If it still cannot account for something you did, it tells you in the chat so you can record that part again.
— 2026-11-10

**Found why a recording can lose its last few steps, and wrote down the fix — 2026-11-10**
A recording of "download a file from GitHub, then upload it to Google Drive" kept every step up to Drive's "New" button and lost the rest: the "File upload" choice and the file you picked. The recorder held each step back while it watched the page finish its work. On Drive that watch ran for 15 seconds, and every later step waited in line behind it, so pressing Stop threw away everything still waiting. This happens on any website that shows progress, not only Drive. Nothing has changed yet. The plan, now on the to-do list, is to save each step the moment you do it, add the "did it finish" details later, and warn you if anything you did was not saved.
— 2026-11-10
