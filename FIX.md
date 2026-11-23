# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Conxa Execute can run skills at its normal window size, and its replies are no longer doubled — 2026-11-23**
In Conxa Execute, no skill could run in a normal-sized window: the little browser was drawn smaller than it really was, so the clicks missed and the run stopped before the first step with a message about the wrong app. The browser now shrinks the way Build Studio's does, keeps its size when hidden or minimized, and the message no longer names Build Studio. When the AI answered with only an action and no words, Execute asked three times and charged for all three; it now asks once and counts the action in the usage. Signing in now shows a waiting message with a Cancel link, error codes in the chat read as plain sentences, and an old skill's result no longer shows under another skill.
— 2026-11-23

**One company can no longer touch another company's installer, policy or plan — 2026-11-23**
Two companies using Conxa at the same time could reach each other's things: one could replace another's downloadable installer, change another's safety rules for its workflows, read or stop another's video analysis, or upgrade its own plan using someone else's payment. Now every one of those actions only works on your own company, and the payment-confirmation messages from the payment provider are accepted only when they carry a valid signature. Heavy use of AI credits no longer stops you from building workflows, a large plan with custom limits can now be set up for each customer, and a person over the seat limit now sees the seat-limit message instead of "try again in a moment".
— 2026-11-23
