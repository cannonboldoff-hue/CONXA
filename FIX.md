# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Rebuilding the GitHub tour workflow still fails its test — 2026-11-25**
We rebuilt the GitHub tour workflow in Build Studio to check the earlier fix. The test still stops early: it cannot find the Issues tab, because the rebuild turned the tab names into answers to fill in and they no longer match what GitHub shows. Nothing was published and nothing was moved to the finished list. This is now written down as open work. Separately, the "Build again" button is missing from the failed-test screen, so rebuilding needed a back-door command.
— 2026-11-25

**Each account now keeps its own work on the computer — 2026-11-25**
Before, if you signed out and someone else signed in on the same computer, they saw your workflows, chats and saved sign-ins, and could publish them to their own company. Now every account has its own private space on the computer, and the sign-in screen shows nothing at all. The first account to sign in after this update takes over the work that was already there, and nothing is deleted. You can't sign out while a recording is running, so a recording is never cut off.
— 2026-11-25

**Nothing you record goes missing without a word — 2026-11-25**
Before, a recording could lose a step while being turned into a workflow, and nobody was told until the workflow failed for a customer. Now every action you recorded is checked. If any could not be kept, Build Studio names it in plain words and asks you to confirm before you can test, approve or publish. A choice you made from a dropdown list is now always kept, together with the value it asks for.
— 2026-11-25

**Cleaner chat and better names for captured values — 2026-11-25**
When you went back to an earlier question, a small note kept saying it had been asked again below. That note is gone, so the chat reads cleaner. A value you capture, such as a price, is now named after the label next to it ("Total", "Price") and not after the page's heading, so its name makes sense when you see it later.
— 2026-11-25

**A page's OK/Cancel question no longer leaves a stuck box behind — 2026-11-25**
When a page asked an OK/Cancel question during a test, Studio showed its own card, but the browser's own box could stay on screen and freeze Studio's window. Now the page is told "Cancel" at once, Studio asks you in the card, and if you pick OK the step is simply tried again with your answer. Nothing is left on screen and Studio stays usable.
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

**Update publishing and the old installer slot are cleaned up — 2026-11-25**
Two chores that only a person could do are finished: new releases now reach the cloud so customers can get updates, and the old shared spot where one company's installer could be handed to another was emptied. We also tried the planned fix for the grey OK/Cancel box that stays on screen after a test answers a website's popup. It does not work the simple way, because the box would then answer itself before a person could choose, so that fix is waiting for a design choice.
— 2026-11-25
