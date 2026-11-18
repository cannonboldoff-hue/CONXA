# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**A run now leaves a full record on the computer that ran it — 2026-11-18**
When a workflow failed on someone's computer, the notes it left behind were too thin to say why. Each run now writes down, for every step, how sure it was about the right button and which checks proved the step worked. These notes stay on that computer and are not sent to Conxa Cloud. Two tests that failed now and then for no real reason were also made steady.
— 2026-11-18

**A fuller record of each run is now on the to-do list — 2026-11-18**
When a workflow runs on a customer's computer, the local record of what went wrong is thin, so it is hard to see exactly what happened. We added a task to make that record detailed, kept only on the computer. What is sent to Conxa Cloud stays small and limited. This lets us find the cause of a problem from one file without sending private details away.
— 2026-11-18

**The test plan now tries to break Build Studio on purpose — 2026-11-17**
The list of workflows still to test repeated the same checks in several places and mostly checked that things work, not that they fail safely. It is now one shorter list, numbered in the order we run it, with the three workflows that already passed kept as passed. Each remaining workflow now has a set of deliberate attacks, such as trying to approve with a question unanswered, making a click silently do nothing, or shuffling rows so the wrong record could be deleted. This lets us find the failures before a customer does.
— 2026-11-17

**Deleting a row and saving a form are now checked properly — 2026-11-17**
A test could wrongly fail after deleting a table row, because it waited for the bin icon you had just used to still be there. A test could also say "couldn't confirm" after saving a form that works inside the page, because the only proof was a message to the website that such forms never send. Now a delete is proven by the page changing, and a save by what you typed showing up on the page. A request from an ad or another website no longer counts as proof.
— 2026-11-17

**Long shopping runs: choosing from a dropdown, removing items and "Continue" now work — 2026-11-17**
A shop workflow that sorted products, filled a cart, removed items and checked out failed on its second step: the sort menu showed "Name (Z to A)" but Studio compared it with the hidden code behind it. Now either one counts. Picking from a menu with the keyboard is saved as one clean step, a removed item is found by its name instead of a price another item shares, and removing an item is proven by it disappearing from the page. Buttons that only move you forward, like "Continue" or "Next", no longer ask "this can't be undone"; paying, deleting and placing an order still do.
— 2026-11-17

**Banking forms and account lists now replay correctly — 2026-11-17**
On a bank site, a test stopped on the Bill Pay form because all nine boxes looked the same to Build Studio, and later because it had quietly decided some clicks must happen "only on the row holding" a value that changes, like a balance. Now each box is found by its own name, page side panels are no longer mistaken for a list, and a click that is already clear about where it goes is not tied to a row. A long banking run with three transfers, two bills, two searches and a new account now passes from start to finish.
— 2026-11-17

**Searches, suggestions and "check first" now record and replay faithfully — 2026-11-17**
Several real sites tripped up Build Studio: a Search click was thrown away, typing was saved in the wrong order so a run searched the wrong word, a name picked from a suggestion list was never recorded, and boxes were found by a look they only have while you type in them. All of these are fixed, so the steps replay as you did them. Most importantly, when you ask Studio to "check first" before a delete, it now always pauses for your approval there. A check you type in your own words also lands on the step where those words appear, and you can ask chat to move a check.
— 2026-11-17

**Downloading and uploading many files now works on more sites — 2026-11-17**
After downloading a file, Build Studio sometimes saved your next click twice, so the test failed on a click that no longer made sense. That is fixed. Pages where you choose files first and then press Upload can now pass their test: Studio checks that the files were really chosen, then checks the upload when you press the button. A first passing test also no longer gets thrown away, and a check you type must be something that appears only after the step, not words already on the screen.
— 2026-11-17

**Studio's questions can't be dodged, and "type your own answer" really does what you ask — 2026-11-18**
A workflow with unanswered questions could still be test-run from the Publish page, and typing nonsense as an answer marked a question as done, even one about a step that can't be undone. Both are closed now: an open question blocks testing, approving and releasing everywhere. The "type your own answer" box is now a real third choice: Studio turns what you write into a change you can see and apply, and the question only closes once the change is made. Saving an unchanged step no longer resets your approval, a chat rename no longer quietly changes how a step finds its button, and tests keep running while Studio is minimized.
— 2026-11-18

**A test can no longer say "it works" when nothing happened — 2026-11-18**
If a page popped up a message such as "Session expired" and saved nothing, Build Studio quietly closed the message and reported the test as passed. Now any unexpected message ends the test and shows you what it said, and a final step that never finished can never count as a pass. A click that has already sent your data to the site is never repeated, so nothing gets sent twice, and a dropped connection is reported in seconds instead of almost a minute.
— 2026-11-18

**A workflow can no longer change the wrong person's record — 2026-11-18**
In a list where every row has the same Edit button, Build Studio could click the button in the wrong row and still report success: after a search that found nobody it edited someone from a side panel, after the list was re-sorted it edited whoever sat in the old position, and with "Carol" missing it edited "Carol Ann". Now each row action is tied to the exact person or record it was recorded on. If that exact record isn't there, or there are two of them, the run stops before touching anything, and a near-match name never counts.
— 2026-11-18

**Recording now keeps exactly what you did, and keeps your passwords to itself — 2026-11-18**
Pasting or using Ctrl shortcuts in a box used to replay as stray letters, and retyping a box could send the old text, while the test still said it passed. Now a box replays the text it finally held. Middle-clicking a link no longer freezes Build Studio, and a double-click replays as one double-click. Drawing on a whiteboard and "I'm not a robot" checks are no longer turned into fake steps: Studio says plainly that they can't be repeated and, for a robot check, offers a pause where a person does it. Passwords and secret codes are now blanked out everywhere Studio saves or logs them on your computer.
— 2026-11-18
