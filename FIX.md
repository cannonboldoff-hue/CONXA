# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**The recorder no longer throws away real steps from pages that only look like sign-in pages — 2026-11-04**
The recorder skipped anything done on a page with words like "auth" or "sso" in its address, even when that page was a normal settings screen inside the app. Now it only skips pages that really are sign-in pages, and only follows the extra hops of a sign-in while one is actually happening. Anything it skips is listed in the compile report, so nothing disappears quietly.
— 2026-11-04

**One master tidy-up switch became a switch for each step, and popup decisions are always handled — 2026-11-04**
A single setting turned off about a dozen tidy-up steps at once, including the one that handles popups that ask a question like "Replace or keep both?". Each step now has its own switch, and the popup step stays on no matter what, so a recorded "Replace" can never quietly replay on its own. Think of it as giving every light its own switch, with the smoke alarm wired separately.
— 2026-11-04

**The recorder checks embedded panels far less often, and a few labels and warnings got smarter — 2026-11-04**
The recorder used to keep asking every embedded panel on a page whether it was ready, which could make it stall on busy pages. Now each panel announces itself when ready, and the recorder only double-checks occasionally. Menu items with shortcut hints like "Alt+C" are now named the same way everywhere, ordinary labels such as "Notification settings" are no longer mistaken for pop-ups, and the AI can no longer ask the person about idle gaps in the video.
— 2026-11-04
