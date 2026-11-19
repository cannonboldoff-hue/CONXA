# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Closing a pop-up with a plain "Close" word is now recorded — 2026-11-20**
Some pop-ups have a "Close" that is just a word, not a button. Studio ignored clicks on it, so a workflow could not learn to close that pop-up, and told you the click "does nothing". Studio now records it, and the note says plainly that it was not a button.
— 2026-11-20

**Optional "if this, do that" steps now pick the right branch — 2026-11-20**
A step that waits for one of several things (for example a success or an error message) waited the whole time and never took its branch in a real browser. It now looks once straight away and follows the branch that matches the page.
— 2026-11-20

**Recording only a sign-in now explains itself — 2026-11-20**
Studio never records sign-in pages on purpose, so a recording of only a sign-in said "No workflow actions were recorded". It now says that the sign-in was left out on purpose.
— 2026-11-20
