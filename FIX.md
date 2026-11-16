# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Forms with an email box can be tested again — 2026-11-16**
A simple practice form with an Email box could not be tested at all: Build Studio said the workflow "contains someone's account details" and stopped, with no way forward. The address it found was only the form's own hint, "name@example.com", which can never belong to a real person. Build Studio now tells sample addresses apart from real ones, and it removes a real address everywhere while it builds, not only at the very end. Two smaller problems from the same test are fixed too: typing your own "this is how I know it worked" words is no longer refused when you scrolled at the end, and an email or password box now keeps your recorded value in the test form and is offered in "which of these change every time".
— 2026-11-16

**Recording a box that changes when you click it, and pressing Enter in a form — 2026-11-16**
Some boxes change the moment you click them; Wikipedia's search box is one. Build Studio used to remember the box as it looked after the click, so a test started on a fresh page could not find it. It now remembers the box as it was when you pressed it. Also, pressing Enter in a form used to be saved twice: once as the key, and once as a click on the Search button that the browser makes for you. A test would then search and click Search again on the next page. Now it is saved once.
— 2026-11-16

**"You see this on the screen" now counts what is in a box — 2026-11-16**
If you said "I know it worked when I see 11/15/2026", and that date was showing inside a date box, the test always failed: Build Studio only read the page's own text, never what boxes hold. It now counts what a visible box shows, the way you see it. Passwords and hidden fields are never read. Also, when you keep a picked date exactly as you recorded it, the question "is it always 1 day before today?" now offers "No, always the date I picked", instead of only "different every time".
— 2026-11-16

**Clicks inside a framed part of a page are now recorded and replayed in place — 2026-11-16**
Some pages show another small page inside them, like a demo or a form in a frame. When that frame had no name and pointed to a short address, Build Studio forgot the frame was there. The workflow then opened the inner page on its own instead of the real page, and would break on any site where the inner page can't open by itself. Build Studio now keeps such frames and starts the workflow on the real page. Also, when a step's result only shows on the screen and Studio has nothing to offer, it now still asks "How can we tell it worked?" so you can type the words you see. Before, every test ended with "couldn't confirm".
— 2026-11-16

**"How can we tell it worked?" now shows the web address it means — 2026-11-16**
One of the answers to "How can we tell it worked?" read "The web address looks like" and then stopped, so you couldn't tell what you were agreeing to. It now shows the address in a readable form, for example "the-internet.herokuapp.com/users/…".
— 2026-11-16

**Questions no longer name the wrong item from a misread video — 2026-11-16**
On a page with several items that look alike (three profile pictures), Build Studio asked "which one should it open?" and offered "Always the third one — as you recorded it", even though you had picked the second. The mistake came from the part that watches the recording video. Picking that answer could have built a workflow that opens the wrong item. Now, if a question says "first", "third" and so on but the recording itself never shows that, Build Studio uses its own plain wording, "Always this one, as you recorded it", which is always true.
— 2026-11-16

**Workflows that end by signing out can now be tested — 2026-11-16**
A workflow whose last click was "Log out" always failed its test with "your saved sign-in expired", even though the sign-in was fine. When a click loads a new page, Build Studio didn't record where that click led, so a test treated landing on the sign-in page as a broken sign-in. Build Studio now records where such a click lands, on any site. A test then accepts that page, and the "you logged out" message, as the result of that one step. Landing on a sign-in page anywhere else still counts as an expired sign-in.
— 2026-11-16

**Recording a sign-out no longer signs out the whole group — 2026-11-16**
When a recording ends, Build Studio copies the browser's sign-in back into the group, so a refreshed sign-in is kept. If the recording signed out (clicking "Log out" on a site), that signed-out state could replace the good one, and every workflow in the group then failed with "sign-in expired". Build Studio now keeps the recording's sign-in only after checking it is still signed in. Also, after you sign in again, "Run test" now rebuilds its test copy by itself, instead of saying "rebuild the skill package" with no way to do it.
— 2026-11-16

**Pop-up boxes that ask you to type something now replay inside Build Studio — 2026-11-16**
Some websites pop up a small box that asks you to type an answer. Build Studio could record your answer, but a test inside Build Studio (and a run inside Conxa Execute) could never give it back. The browser these apps are built on does not support that kind of box at all, so the test waited two minutes and the answer never arrived. A replay now hands the page your recorded answer (or the value you entered for this run) directly, in the same way the recording does. In a normal browser nothing changes.
— 2026-11-16

**"The screen changes" now counts a new tab the step opened — 2026-11-16**
If a click's whole job is to open a new tab, the page you clicked on stays exactly the same. The "the screen changes" check only looked at that page, so it said nothing happened and the test failed every time. A new tab that the page itself opened during the step now counts as a change, and a click that really does nothing still fails the check.
— 2026-11-16

**Build Studio no longer closes when a website opens a new tab while you record — 2026-11-16**
Clicking a link that opens a new tab (like "Click Here" on a "multiple windows" page, a sign-in popup or a share window) while recording made the whole Build Studio app close with no message. Studio was setting up its connection to the new tab before the tab had loaded anything, which crashes the app. It now waits until the new tab's first page has started, and never waits more than a few seconds, so recording carries on and the new tab shows up inside the recording.
— 2026-11-16

**A search box the page replaces when you click it now records correctly — 2026-11-16**
On Wikipedia, clicking the search box makes the page throw that box away and put a new one in its place, and what you type goes into the new one. Build Studio thought these were two different boxes, so it made up an extra "click into the box" step from the new box's look and set your real click aside. A test then failed straight away, because a fresh page only has the original box. Build Studio now keeps track of which box replaced which, so your click and your typing stay one box. It also stops asking "a few things you typed are not in the workflow" about words that were only recorded twice.
— 2026-11-16

**Your own "this is how I know it worked" words now count — 2026-11-16**
On a form that shows its result right on the page without contacting a server, a test could never pass: Build Studio waited for the server to answer, which never happens there, and stopped with "couldn't confirm". On top of that, the words you typed as proof had to match a whole line exactly, so "Asha Rao" was not found inside "Name:Asha Rao". Now your words are found anywhere in the page's text, and when they were not on the page before the step and are there after it, that counts as proof the step finished. Studio also offers "You see the name you typed on the screen" as a choice whenever a step needs proof.
— 2026-11-16

