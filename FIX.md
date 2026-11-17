# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Deleting a row and saving a form are now checked properly — 2026-11-17**
A test could wrongly fail after deleting a table row, because it waited for the bin icon you had just used to still be there. A test could also say "couldn't confirm" after saving a form that works inside the page, because the only proof was a message to the website that such forms never send. Now a delete is proven by the page changing, and a save by what you typed showing up on the page. A request from an ad or another website no longer counts as proof.
— 2026-11-17

**Long shopping runs: choosing from a dropdown, removing items and "Continue" now work — 2026-11-17**
A shop workflow that sorted products, filled a cart, removed items and checked out failed on its second step: the sort menu showed "Name (Z to A)" but Studio compared it with the hidden code behind it. Now either one counts. Picking from a menu with the keyboard is saved as one clean step, a removed item is found by its name instead of a price another item shares, and removing an item is proven by it disappearing from the page. Buttons that only move you forward, like "Continue" or "Next", no longer ask "this can't be undone"; paying, deleting and placing an order still do.
— 2026-11-17

**Banking forms and account lists now replay correctly — 2026-11-17**
On a bank site, a test stopped on the Bill Pay form because all nine boxes looked the same to Build Studio, and later because it had quietly decided some clicks must happen "only on the row holding" a value that changes, like a balance. Now each box is found by its own name, page side panels are no longer mistaken for a list, and a click that is already clear about where it goes is not tied to a row. A long banking run with three transfers, two bills, two searches and a new account now passes from start to finish.
— 2026-11-17
