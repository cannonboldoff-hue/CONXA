# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**The to-do list's "must fix before pilot" section now has a work plan — 2026-11-24**
The most urgent list had thirteen items, some of them huge, and it was not clear which could be worked on at the same time, which had to wait for another, and which only a person can do. Each item now has an owner, a clear "done when" line, and a place in one of three waves, and the work that needs a human (setting secrets, cleaning the live server, running the long test passes) is marked as such. Nothing was fixed yet; the plan only makes it possible to work on several items safely at once.
— 2026-11-24

**Recovery now knows where a button used to sit — 2026-11-24**
When a workflow can't find a button because the website changed, the helper that fixes it now gets a short description of where that button sat when it was recorded: which sections of the page it was inside, a few words of nearby text, and so on. Personal details, typed values and record numbers are removed first. This helps the helper find a button that moved instead of guessing. Workflows get this the next time they are rebuilt.
— 2026-11-24

**A login button can no longer quietly pick up a password — 2026-11-24**
Months ago a bug let a saved workflow look for the Login button by the password typed just before it, so the button was never found. A rebuilt login recording shows it no longer happens. Building an installer now also warns you, in plain words, if any step looks for something by a value the recording never showed, so a broken step is caught at build time and not when a customer runs it.
— 2026-11-24

**GitHub tab clicks and an old logout workflow work again — 2026-11-24**
Workflows that clicked a GitHub tab such as "Pull requests" stopped when the number next to the tab changed, because the workflow had remembered the number as part of the tab. Workflows now ignore those changing numbers, so the tab is found right away (about 1.5 seconds per click instead of about 9.5). An older approved logout workflow that stopped at the Logout button finds it again without being rebuilt.
— 2026-11-24

**Nothing you record goes missing without a word — 2026-11-25**
Before, a recording could lose a step while being turned into a workflow, and nobody was told until the workflow failed for a customer. Now every action you recorded is checked. If any could not be kept, Build Studio names it in plain words and asks you to confirm before you can test, approve or publish. A choice you made from a dropdown list is now always kept, together with the value it asks for.
— 2026-11-25

**Fewer false alarms and wrong checks while testing — 2026-11-25**
A test no longer mistakes a workflow's own "Log out" for an expired sign-in. When the page pops up its own OK/Cancel question during a test, Studio now asks you in the chat instead of failing, and your answer is counted properly. Studio stops suggesting "it worked" checks with words that never appeared on the page. Chat no longer says testing is closed when it is open.
— 2026-11-25

**Multi-tab workflows keep their place after a pause — 2026-11-25**
When a test paused before a step that can't be undone and then carried on, it lost track of the browser tabs it had already opened. It opened an empty tab and failed, and it also forgot files it had downloaded before the pause. Now a workflow that pauses and carries on goes back to the right tab and still has its downloaded files. An automatic check now runs a two-site, multi-tab workflow on every build, so this can't quietly break again.
— 2026-11-25

**A plain to-do list for the work only you can do — 2026-11-25**
The most urgent items on the backlog need a person, not more code: setting up update publishing, cleaning old installers off the live server, a few choices, and the big test passes. There was no single place that listed them. A new step-by-step guide now explains each one in plain words, with the order to do them in.
— 2026-11-25
