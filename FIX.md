# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**When a workflow hands the page to a person, it now waits properly and stops in plain words — 2026-11-22**
Some steps ask a person to do something only they can do, like ticking a box or signing in. If the person closed the page, left it unfinished, or never came back, the workflow could carry on anyway or end with a confusing message. Now it checks the page again when the person says they are done, and if something is wrong it stops at that step and says what happened. Inside a repeated step it no longer restarts from the beginning and leaves a window open, and a test in Build Studio waits up to ten minutes for the person instead of clicking Submit later on its own. The Done banner also comes back after the page changes, and only shows on the workflow's own pages.
— 2026-11-22

**The to-do list is now in the order the work has to be done — 2026-11-22**
The to-do list used to mix urgent fixes with old ideas, and anything new was supposed to jump to the top. It is now split by when the work is needed: before the first pilot customers, before the public launch, after launch, and a parked pile at the very end for stale or low-value items. Inside each part the most important item comes first, the progress table is kept, and the "new items go to the top" rule is gone.
— 2026-11-22

**The to-do list only shows work that is still open — 2026-11-22**
The to-do list still carried long progress tables for test rounds that were finished days ago, which made it hard to see what was really left. Those tables and one mostly finished governance item now live in the list of finished work. The open to-do items kept their remaining notes and nothing that is still open was removed.
— 2026-11-22

**Signing in can be cancelled, and sign-in messages are in plain words — 2026-11-22**
If you opened the browser to sign in and then walked away, the Sign in screen sat there for five minutes and ended with "Something went wrong inside the app". Now there is a Cancel link while it waits, and if the time runs out you are told to click Sign in to try again. The messages when Conxa cannot confirm you are signed in to a website now say what to do next in everyday words, and the sign-in expired message no longer tells the assistant to "call again" in the same breath as saying it will carry on by itself.
— 2026-11-22

**Fixes for what the real-app test found — 2026-11-22**
The real-app test found that "Yes, always the first result" was refused, that a form check could stop a run when nothing was wrong, and that the website's own OK/Cancel box could be left open and still act after a test failed. All three are fixed: the answer now works, the check only looks at boxes on the same page, and an unanswered OK/Cancel box is now cancelled when the test ends. Reading a value from a chosen row now works for any customer, empty typing no longer shows up as a step, and a "web address changes" check is only added to the step that causes it. These are tested in the lab but not yet seen in the real app.
— 2026-11-22

**The new fixes were tried in the real app, and most work — 2026-11-22**
We ran the real Build Studio with test websites to see whether last week's fixes hold up outside the test lab. Saved sign-ins are locked, the question screens, the failure card and the Publish test button all work, and a pasted box reuses the first box's value. Three things do not work yet: answering "Yes, always the first result" is refused, the page's own OK/Cancel box confuses a test run inside Studio, and a form check can stop a run when nothing was wrong. These and a few smaller problems are now on the open list.
— 2026-11-22

**Forms check their boxes, pasted boxes follow the original, and the page's own OK/Cancel boxes are asked about — 2026-11-22**
Before a workflow sends a form, it now checks that each box still holds what it typed; if something changed a box, the run stops and says so instead of sending the wrong text. When you copy a whole box and paste it into another while recording, the second box now always gets the same value as the first. When a website pops up its own OK/Cancel box that the recording never saw, the assistant is asked which button to press; if nobody answers in time, Cancel is pressed and the run says it couldn't confirm the step, never that it worked.
— 2026-11-22

**Signing in as a different person on the same computer — 2026-11-22**
A test showed that when one person signs out of Build Studio and another signs in, the first person's workflows and saved site sign-ins are still on screen. Build Studio keeps its work per computer, not per account, and the owner decided to leave it that way for now. It is written down as a known limit and in the to-do list, so nobody is surprised by it.
— 2026-11-22
