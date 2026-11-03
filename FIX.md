# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Test steps now read like your workflow's own step list — 2026-11-03**
While a test ran, each line only said "Navigate", "Scroll" or "Click", so you couldn't tell which step was which. Now each line uses the same wording as the workflow's step list, such as "Open github.com" or "Click “New”", and follow-on steps are indented under the action that caused them. Like a recipe that says "whisk the eggs" instead of just "step 3: stir".
— 2026-11-03

**Fixed a download-then-upload workflow that never actually uploaded — 2026-11-03**
A workflow that downloaded a file from one site and uploaded it to another lost its upload step, because Studio mistook your file pick for something the website did on its own. Its test still "passed", because the only check was that the page address changed. Now the upload is kept and uses the downloaded file, and the summary says the workflow is done when the file is uploaded. Studio also tidies up repeated page loads again, asks about anything it noticed but couldn't see instead of only mentioning it, merges duplicate questions, shows your actual answers, keeps email addresses out of the chat, and shows the "watching the recording" and test steps cleanly. Like a removal van that now checks the box actually reached the new house, not just that it drove off.
— 2026-11-03

**Stopped recordings from picking up clicks you never made — 2026-11-03**
A recorded workflow could contain a step like clicking a Google account button that you never touched, because the website itself pressed it. The recorder now tells apart a click from a real person and a click from a website's own code, and leaves the second kind out of the workflow. This stops stray steps from sneaking in, whatever site you record.
— 2026-11-03

