# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Three small Studio and runtime gaps closed — 2026-10-25**
An optional input no longer shows as required on the test screen. A value that breaks an input's own format rule is now turned away with a plain message before anything runs, instead of being typed into the page. The helper's "wait for the file link" fix can now be added next to a conditional step instead of being rejected. Like a form that finally checks the box labelled optional, and a bouncer who checks the ID before the door opens.
— 2026-10-25

**A wrong test value is now questioned before the workflow gets rewritten — 2026-10-25**
When a test couldn't find something on the page because the typed value was wrong, the helper assumed the workflow was broken and started changing its steps. Now it checks first: if a value you typed differs from the recorded one, is empty, or doesn't fit its own rules, it asks "is this value right?" and waits. It's like a mechanic checking you put in the right fuel before taking the engine apart.
— 2026-10-25

**A "couldn't tell which button" failure now says which buttons it was torn between — 2026-10-25**
When a test stopped because two things on the page looked equally right, the message only said "couldn't decide". Now it lists the look-alikes it found, what each one is called, and how close the scores were. It's like a referee saying "it was a tie between these two players" instead of just blowing the whistle, so the real cause is easy to see.
— 2026-10-25

**Cleared out to-do items for screens we no longer use — 2026-10-25**
Our to-do list still had nine jobs about polishing the old page-by-page screens, like a repair list for rooms that were knocked through when the building became one open space. We removed them, since the chat-led Studio replaced those screens. This keeps the list honest about what is actually left to do.
— 2026-10-25

**A value you mark as "changes each run" now disappears from every sentence, not just one step — 2026-10-25**
After you chose a file as something that changes each run, some steps still named the example file from the recording, and one read "the the". Now there is a single rule that runs whenever a workflow is saved: any sentence that mentions the example value says the input's name instead, and the wording no longer doubles up. It's like a find-and-replace that happens automatically everywhere, instead of someone fixing each page by hand.
— 2026-10-25

**Step wording is plainer, follows your inputs, and "did it work?" lets you write your own check — 2026-10-25**
Steps used to read like a manual ("initiate the download", "observe that…"), and a file you marked as "changes each run" was still named in the step text and in the "did it work?" question. Now steps use everyday words, the file shows up as "the file you choose", and that question can offer "the file shows up on the page". There is also a box where you can type the exact text that should appear when the workflow worked. It's like a form that now says "your file" instead of a name someone typed last week.
— 2026-10-25

**The workflow summary can now show all of its steps — 2026-10-25**
The summary card listed the first six steps and then just said "and 5 more", with no way to see them. Now that line is a button: click it to see every step, and click again to fold the list back up. It's like a "show more" link on a long list instead of a note you can only read.
— 2026-10-25

**Fixed the downloads list getting stuck over the page while recording — 2026-10-25**
After a download, the list popped open by itself and left an old picture of the page stuck on top of the real one. Now it only opens when you click the download icon, and it closes on its own if you move to another page or tab. Like a window that used to open itself and stay in your way, it now waits until you ask for it.
— 2026-10-25

**Questions now always come before testing, and chat changes now do what they say — 2026-10-25**
After a workflow finished compiling, the chat offered "Test it" for a few seconds before it had even thought of its questions, like a waiter bringing the bill before the meal. Now the test button stays hidden until every question is answered or skipped, and until any change you were offered has been applied or dismissed. The app also refuses to start a test any other way until then. When you ask the chat to make a value change on every run, it now offers one clear change that Studio itself carries out everywhere that value appears, instead of two half-finished edits where one failed. The change card now explains itself in plain words, applies all at once or not at all, and no longer spills off the side of the screen.
— 2026-10-25

**Removed the Workflows overview page — 2026-10-25**
There was a separate "Workflows" screen that only showed a card for each group, and everything on it was already available in the sidebar. We deleted it. Anyone who lands where that screen used to be now goes straight to the chat instead, like a hallway that used to lead to an empty lobby now opening into the main room.
— 2026-10-25

**Removed a small note from the New workflow page — 2026-10-25**
Next to the "Create and start recording" button there was a line of grey text saying where the recording would open. It repeated what the box above it already explains, so we took it out. The page is a little cleaner and nothing else about it changed.
— 2026-10-25

**Fully removed the Workflows page address — 2026-10-25**
The old Workflows screen was already gone, but its address still quietly sent people to the chat, and several buttons still pointed at it. Now no address for it exists, and every "go back" button that used to lead there takes you to the chat. Nothing is left pointing at a room that was demolished.
— 2026-10-25

**Drew a simpler design for the workflow chat page — 2026-10-25**
The chat page for a workflow showed every event twice, long technical error messages, and each test attempt as its own big card, so it was hard to tell where things stood. The new design puts the one next step at the top, shows a five-step progress bar (Record, Check, Test, Approve, Publish), and folds the history into short plain-language lines. Technical details are still there, but hidden behind a "Technical details" link. This is a design mockup only; the app itself has not changed yet.
— 2026-10-25

**Turned the chat page design into one long scrolling page — 2026-10-25**
The first design split the workflow chat into two separate screens, which made it hard to hold up against the real app. Now it is one page that scrolls from top to bottom in the same order as the real chat: record, check, test, approve, publish. That makes it easy to compare the old and new versions side by side. It is still a design mockup; the app itself has not changed.
— 2026-10-25

**Put everything back into the chat page design, in one consistent style — 2026-10-25**
The last version of the design had cut out many of the real chat's messages and cards, so it wasn't a fair comparison. Every message, test try, fix and button from the real chat is back, in the same order. Now everything on the page is one of four things that always look the same: a message from Conxa, a one-line event with a time, a card with a status mark and buttons, or a stage heading. Long error text is rewritten in plain words, and the original wording is kept behind a "Technical details" link.
— 2026-10-25

**Moved the Publish, Test it and Human Edit buttons into the chat — 2026-10-25**
In the chat page design, these three buttons were stuck above the message box all the time, even while you scrolled back through old messages. Now they sit under Conxa's latest message, where the next step is offered, and scroll away with the conversation. The message box at the bottom is just the message box again.
— 2026-10-25

**Removed the extra Human Edit buttons from the chat page design — 2026-10-25**
The design had an "Open Human Edit" button under every failed test and again at the end of the chat. Human Edit is already always open on the right side of the screen, so those buttons only repeated it. They are gone, which leaves less clutter in the conversation.
— 2026-10-25

**Renamed the second stage from "Check" to "Compile" in the chat page design — 2026-10-25**
The progress bar and the section heading called the second stage "Check", but that is where the recording gets turned into steps. It now says "Compile", the word used everywhere else in the app. People see the same name for the same thing.
— 2026-10-25

**Built the simpler chat page into the real Build Studio — 2026-10-25**
The workflow chat now shows each moment once instead of twice, so a saved recording, a finished compile or a failed test is a single item rather than a card plus a repeat line. Failed tests explain what went wrong in one plain sentence, with the long technical text tucked behind a "Technical details" link. A progress bar at the top shows Record, Compile, Test, Approve and Publish, and the chat has a heading for each part. Older chats are tidied up the first time they are opened, and a backup copy is kept.
— 2026-10-25
