# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

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
