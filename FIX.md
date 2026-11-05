# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**The two AI checks that read a recording now see much more, and stop guessing when they miss something — 2026-11-05**
When you record a task, an AI reads it twice: once before the tool asks you questions, and once while the workflow is built. The second reader used to work blind: it never saw what the video showed, never saw what the first reader had worked out, and never saw what you had already answered. Now both readers share the same notes, so the goal is worded once and a value you already settled is not reopened. The first reader also gets the page titles, the popup's own buttons and a smarter way of trimming very long recordings, and when it skips a page address it now asks you instead of quietly treating it as "always the same". If the second reader's answer comes back unreadable, it is asked once more, and it now has twice the room to write long answers.
— 2026-11-05

**The tool now keeps a tally of the mistakes it catches when an AI first reads a recording — 2026-11-05**
Before it asks you any questions about a recording, an AI reads it and the tool double-checks the AI's answer. Until now, anything wrong in that answer was quietly thrown away, so nobody could tell whether the AI was getting better or worse. The tool now writes down what it threw out and marks each answer good, weak or unusable. When an answer is weak it asks the AI to try once more, and the workflow diagram now shows the same safety check at every place an AI is used.
— 2026-11-05

**The recorder can now spot a date picker it has never seen before — 2026-11-05**
Until now the recorder only knew a date picker if it was one of a list of well-known ones, so a custom calendar was recorded as a pile of unrelated clicks. It now also recognises a calendar by its shape: a small block of day numbers 1 to 28 or more under a "March 2007"-style heading. A page number strip or a one-week row is not mistaken for one. The existing check that the date box really changed still applies, so a click that changes nothing is not counted as a date pick.
— 2026-11-05

**The recorder now notes who decided a page change was caused by a click: the browser, or a stopwatch — 2026-11-05**
When you click a button and the page changes address, the tool has to decide whether you typed that address or the click caused it. For one kind of change the browser can say so directly, and the tool now uses that. For the rest it still uses a five-second stopwatch, but it now writes down which one it used. That way, once real recordings show the stopwatch is no longer needed, it can be safely deleted. I tested the browser's signals on a small test site, but not yet on a real Google Drive recording.
— 2026-11-05

**The tool now works out an element's name in one place instead of three — 2026-11-05**
To find the right button on a page again, the tool works out what the button is called. The recording step, the step that scores candidates and the step that recovers from a miss each had their own slightly different recipe for that name, so they could disagree about the same button. They now share one recipe, and one list of examples is checked against both the Python and the JavaScript versions.
— 2026-11-05

**One AI-model quirk and the answer-length limits moved out of the routing code into plain tables — 2026-11-05**
The part of the cloud that picks an AI provider had a rule written in the middle of its logic for one specific model, and the code that sets how long each AI answer may be was a long chain of one-off settings with a guess in a comment. Both are now short labelled tables, so adding or changing one model or one task is a one-line edit. How the tool behaves did not change.
— 2026-11-05

**Steps in the compile no longer depend on staying in the same order as the recording — 2026-11-05**
While turning a recording into a workflow, several clean-up passes matched each step to its recording event by counting positions in a list, which only worked if no earlier pass had added or removed a step, and a mismatch crashed the build. Each step already knew which recorded action it came from, so the passes now look that up directly. It is like finding a book by its catalogue number instead of by counting along the shelf. The passes can now run in any order and a stray extra step no longer shifts the others out of place.
— 2026-11-05

**The tool now knows that two company websites ending in ".co.uk" are different companies — 2026-11-05**
When the tool decides which saved logins a workflow needs, it compares website addresses. It used to look only at the last two parts of an address, so "shop.acme.co.uk" and "app.other.co.uk" looked like the same company because both end in "co.uk". It now uses the standard public list of address endings that browsers use, so those two are told apart. Three other places had their own slightly different idea of "same website"; the two that mean the same thing now share one rule.
— 2026-11-05

**"Is this a sign-in page?" now has one answer instead of nine — 2026-11-05**
The tool decides whether a page is a sign-in page in several places: while recording, when checking a saved login, while signing in, and when a run fails. Each place kept its own slightly different list of words, so they could disagree. They now all read one shared list, with a test that keeps the two programming languages in step. A run also no longer decides a customer's login has expired just because a normal settings page has "auth" in its address.
— 2026-11-05

**Risky-button detection no longer depends on a short English word list buried in the code — 2026-11-05**
The tool decides whether a button does something permanent (like deleting) by looking for certain words, and that list lived inside the program. The list now sits in a settings file with a test that checks it, and it knows a few more words such as "terminate" and "unsubscribe". When a button is followed by an "are you sure?" popup, the tool now asks the person who recorded it whether someone should approve that step first, even if the button's label is in another language. Nothing pauses on its own while a workflow runs.
— 2026-11-05

**Wrote a second health check of the product's foundations — 2026-11-05**
Two days ago we fixed a list of shortcuts that were tuned to one website. This new review checks whether those fixes really solved the problems or only moved them. It found that a few "guess by the clock" rules just changed rooms, that the same "is this a sign-in page" question is still answered in many separate places, and that one safety check for risky clicks relies on six English words. It also lists the parts that are solid, so nobody rebuilds them. Nothing in the product changed; this is a report for the team.
— 2026-11-05

**Brought back the step-by-step note the AI writes before it decides — 2026-11-05**
The AI that reads your recording used to jot one short line per step before deciding what changes each run. We had cut it to save words, but it works like showing your working in maths: it makes the answer more careful. It is back, written first, so the AI looks at every step before it judges anything.
— 2026-11-05

**The tool now listens to what you said while recording, and double-checks its own reading — 2026-11-05**
When you marked a typed value as "changes each run" or "stays the same" during recording, the step that reads your recording ignored it and could ask you again. Now it is told up front, treats your word as final, and also knows which input names are already taken. If the AI's reply comes back garbled, it is asked once more before the tool gives up, and its instructions now include one worked example.
— 2026-11-05

**Removed the "What does the task do?" box from the new workflow page — 2026-11-05**
Creating a workflow used to stop you until you typed a sentence describing the task. That box is gone, so you only give a name, a group and a starting page. The tool works out what the task is from your recording instead.
— 2026-11-05

**The behind-the-scenes side no longer takes a typed task description either — 2026-11-05**
Even after the box was removed from the new workflow page, the engine still had a spot to receive that sentence, and the chat assistant still offered one when it drafted a workflow. Both are gone. The tool still writes its own short description after it watches your recording.
— 2026-11-05

**A written plan for handling surprise popups and unexpected screens — 2026-11-05**
Workflows are recorded once, but real apps do not always show the same screens: a popup can appear only sometimes, an error can show up, or a sign-in can run out. We wrote down one shared plan for all of these instead of fixing them one by one. Every surprise is sorted into a small set of kinds (in the way, just a notice, a question, a risky question, asks for information, an error, or a quiet change), and each kind has one clear rule: close it, ignore it, follow the author's answer, hand it to a person, or stop safely. The AI helper can explain what it sees, but it can only ever make a run more careful, never answer a risky question by itself.
— 2026-11-05

**A research report on where Conxa wins for a business, and where it does not — 2026-11-05**
We studied how Conxa compares with AI assistants that click through websites, older office robots, and tools that connect apps through their back doors. The report explains, in plain business terms, why recording a task once and letting a company hand it to all its customers can cost less and be safer than paying per run or per robot. It also lists the honest limits, such as having no paying customers yet and only working on Windows today, so sales claims stay truthful.
— 2026-11-05

**Rewrote the Conxa business-advantages report in plain language — 2026-11-05**
The research report on where Conxa wins and where it does not was written for specialists and was hard to read. It now explains every idea in everyday words, with a small glossary, simple comparisons (a taxi versus a train track), and clear warnings about which claims are still unproven. The facts and sources are unchanged, so anyone in the company can read it and use it with customers.
— 2026-11-05

**Unexpected popups and app errors are now handled instead of derailing a run — 2026-11-05**
Before, a popup nobody had recorded was either ignored until the next click broke, or an app error such as "no access" was treated like a button that had moved and sent to the AI for nothing. Now the run stops at once with a plain reason when the app itself refuses, a popup that keeps coming back stops the run, and a popup that opens a moment late is still recognised. An unexpected question popup is put to the AI assistant to answer, but never when one of its buttons could not be undone, such as delete or pay.
— 2026-11-05

**You can now answer a popup question by naming the button, and say a popup always shows up — 2026-11-05**
When a recording showed a popup with several buttons, the question used to offer only vague choices such as "stop" or "let someone decide". Now it lists the popup's own buttons, so you can say "always click Keep both" before the workflow is even built. After building, you can also say the popup shows up every time, and the workflow then treats that click as a normal step and stops if the popup is missing.
— 2026-11-05
