# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Wrote a plain list of what to fix in the questions and the chat — 2026-10-26**
We read through how the Studio asks its follow-up questions and how the chat answers, looking for weak spots. We found 23, including a chat reply that can vanish after you have read it, an answer that can be applied twice, and a "checking" step that can hold up testing for minutes. The review is a shareable page with a step-by-step plan to fix them and use fewer AI calls, so the Studio gets faster and cheaper to run. Nothing in the product has changed yet.
— 2026-10-26

**Cut the number of AI calls after recording, and made answers safe to retry — 2026-10-26**
After a recording was compiled, the Studio asked the AI several separate questions in a row before showing you anything, and a failure part-way through could lose a chat reply or apply one of your answers twice. Now the compile asks the AI once for everything it needs, the first question appears straight away in plain wording, and the friendlier wording follows a moment later. If an answer fails half way, the workflow goes back to how it was, and a question that was lost because the app closed is rebuilt when you reopen the workflow. Like a clerk who asks the manager one list of questions instead of walking back and forth, and who never files a form twice.
— 2026-10-26
