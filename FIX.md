# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Forms with an email box can be tested again — 2026-11-16**
A simple practice form with an Email box could not be tested at all: Build Studio said the workflow "contains someone's account details" and stopped, with no way forward. The address it found was only the form's own hint, "name@example.com", which can never belong to a real person. Build Studio now tells sample addresses apart from real ones, and it removes a real address everywhere while it builds, not only at the very end. Two smaller problems from the same test are fixed too: typing your own "this is how I know it worked" words is no longer refused when you scrolled at the end, and an email or password box now keeps your recorded value in the test form and is offered in "which of these change every time".
— 2026-11-16

**Recording a box that changes when you click it, and pressing Enter in a form — 2026-11-16**
Some boxes change the moment you click them; Wikipedia's search box is one. Build Studio used to remember the box as it looked after the click, so a test started on a fresh page could not find it. It now remembers the box as it was when you pressed it. Also, pressing Enter in a form used to be saved twice: once as the key, and once as a click on the Search button that the browser makes for you. A test would then search and click Search again on the next page. Now it is saved once.
— 2026-11-16

**A search box the page replaces when you click it now records correctly — 2026-11-16**
On Wikipedia, clicking the search box makes the page throw that box away and put a new one in its place, and what you type goes into the new one. Build Studio thought these were two different boxes, so it made up an extra "click into the box" step from the new box's look and set your real click aside. A test then failed straight away, because a fresh page only has the original box. Build Studio now keeps track of which box replaced which, so your click and your typing stay one box. It also stops asking "a few things you typed are not in the workflow" about words that were only recorded twice.
— 2026-11-16

**Your own "this is how I know it worked" words now count — 2026-11-16**
On a form that shows its result right on the page without contacting a server, a test could never pass: Build Studio waited for the server to answer, which never happens there, and stopped with "couldn't confirm". On top of that, the words you typed as proof had to match a whole line exactly, so "Asha Rao" was not found inside "Name:Asha Rao". Now your words are found anywhere in the page's text, and when they were not on the page before the step and are there after it, that counts as proof the step finished. Studio also offers "You see the name you typed on the screen" as a choice whenever a step needs proof.
— 2026-11-16

