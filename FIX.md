# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Every question the tool asks you must now be answered, and it keeps asking until you do — 2026-11-06**
Before, a question could quietly go away: you could skip it, some were hidden until a test failed, and the ones past the first five were sometimes never shown. Now there is no skip button. If you would rather type your own answer, that counts. An unanswered question stays pinned just above the message box, comes back if you close and reopen the workflow, and stops you from testing, approving or publishing until it is answered. Things the tool is truly sure about are no longer treated as questions at all.
— 2026-11-06

**The tool now works out which questions are worth asking, and measures whether it asked the right ones — 2026-11-06**
Every question the tool asks now says what it is really about, so the same thing is never asked twice and a question whose answers change nothing is dropped. Cheap questions that the tool's default already handles can be left as they are and listed in the summary, while a popup or a step that cannot be undone always asks. After an answer changes the workflow, any new question that change makes possible is asked right away, and dropdowns and dates are asked before building. The tool also keeps a private log of how questions went, so we can see which are skipped, which guesses are wrong, and what it failed to ask. All of this starts switched off or in "report only" mode, so nothing changes for anyone until the numbers look good.
— 2026-11-06

**Added a visual guide to how the Studio chat assistant works — 2026-11-06**
There was no single place that showed what happens after you type a message in the chat next to a workflow. A new page now walks through it with diagrams: which messages are handled without any AI, when the AI is asked, how suggested edits are checked before you see them, and how many AI calls each kind of message costs. It is like a subway map for the chat, so a new teammate can see every stop and who decides at each one.
— 2026-11-06

**Made the engine redesign proposal deeper and corrected it against the code — 2026-11-06**
The page proposing six improvements to the engine was a high-level sketch. It now has a detailed section for each improvement: what happens today, what new information it would store, the order things would run in, what could go wrong, and how to build and test it. Checking it line by line against the real product showed four claims were wrong, such as saying the video step never retries when it actually retries for ten minutes. Those are now marked as corrections, and three of the six improvements turned out smaller than first estimated.
— 2026-11-06

**Opened up the "how the engine works today" map one level deeper — 2026-11-06**
The redesign page had a map of the engine with one box per stage, which hid what really happens inside each stage. A second, larger map now opens every stage into the steps it runs, colours each step by who does it (the product, an AI model, a person, the video service), and shows what each stage hands to the next. The six known weak spots now sit on the exact step where each one lives, with a table pointing to the proof.
— 2026-11-06

**The tool now keeps a count of what its checks throw away from the AI's answers — 2026-11-06**
Before, the tool checked what the AI suggested and quietly dropped anything it did not trust, so a bad prompt left no trace. Now every one of those checks writes down how many suggestions it turned down and why, and the Diagnostics panel and the chat after a build say so. Nothing the checks decide has changed; it is like putting a counter on a turnstile that used to let people through unnoticed.
— 2026-11-06

**A slow or down video service no longer ends in a dead-end button — 2026-11-06**
Watching a recording needs a video service, and when it was slow the tool gave up after ten minutes and waited for someone to press Retry. Now it remembers the job, says when it will try again, keeps trying for up to a day, and picks the same job up again if Studio is closed and reopened, without sending the video a second time. Running out of allowance or being signed out still stops straight away, because waiting would not help.
— 2026-11-06

**A safety net that checks every kind of action is handled all the way through — 2026-11-06**
The tool knows 42 kinds of action in several places, and nothing checked that the list a recording can produce matched the list a customer's computer can run. A new checklist file now says what each kind turns into, and two automatic tests fail the build if any part is missing, instead of a customer finding out at run time. Today everything lines up.
— 2026-11-06

**Authors can now hand one fuzzy part of a task to the customer's AI assistant — 2026-11-06**
Until now every step had to be exactly scripted. A new Agent span step lets the author write a goal, say which kinds of moves are allowed and how to tell the goal is reached, and the customer's AI assistant then works toward it one numbered choice at a time. It can never press a button that sends, pays or deletes, never leave the site, and never answer a popup that needs a person. A skill that uses it says so, and cannot be put on a schedule, because nobody would be there to help.
— 2026-11-06

**The redesign page now says what was built and what was not — 2026-11-06**
The engine redesign page still read like a plan, even though four of its six changes are now built. It now opens with a short status table, shows a green "As built" note on each finished change saying where the real version differs from the plan, and marks the two unfinished changes as not built. Answered questions are marked decided, so the page can be read as a record of where things stand.
— 2026-11-06

**The helper that acts on a page for the customer's AI assistant can now work like a real browsing assistant — 2026-11-06**
Before, the "Agent span" step could only click, type, pick from a list or scroll on one website, and it could not even look at the rest of the task, so authors needed a separate "AI review" step to ask a question. Now one step does both: the assistant is told what the whole task is, what has been done and what comes next, and it can press keys, go to other addresses, open and close tabs, read the page, and hand back an answer that later steps can use. It still asks the person first before pressing anything that sends, pays, deletes or publishes, and the old "AI review" step is gone. This matters because jobs that vary from one customer to the next no longer need to be scripted click by click.
— 2026-11-06
