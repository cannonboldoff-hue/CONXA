# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Build Studio now says clearly when something is unavailable — 2026-10-18**
If your internet drops, a thin banner says what waits and what still works, and the buttons that need the cloud are switched off until it returns. If Build Studio's helper program stops, you get a plain notice with a Restart button instead of an error box, and your workflows are safe. A test that stops because a saved sign-in ran out is now paused rather than marked as failed, so nothing tries to "fix" a workflow that was never broken.
— 2026-10-18

**Settings is now a pop-up, new users get a welcome page, and leaving an edit asks first — 2026-10-18**
Settings opens over whatever you were doing instead of taking you to another page, and shows your usage as AI credits. A brand-new workspace now opens on a short welcome page that explains the three steps and checks that this computer is ready. If you try to leave the step editor with a change you haven't saved, it asks whether to save it, throw it away, or keep editing.
— 2026-10-18

**Test runs now show their browser inside Build Studio — 2026-10-18**
When you test a workflow with the browser visible, the page now appears in a panel beside the conversation instead of in a separate window. When the test ends, the last page stays on screen so you can check the result yourself, and you close it when you are done. Recording and signing in still open their own window, because they need things the built-in panel cannot give them.
— 2026-10-18

**The editing screen now sits inside the conversation — 2026-10-18**
When you open a workflow's step editor from its conversation, you can chat about the selected step right beside it, and the assistant's suggested changes appear as cards you can apply or undo. The steps list now leads with each step's name and a small coloured tag for what kind of action it is. The main button there now says Test changes, because approving happens after a test passes, back in the conversation.
— 2026-10-18

**Tests now stop before anything real happens, and can fix themselves — 2026-10-18**
A test run now pauses before any step that can't be undone, such as sending a real invoice, and waits for you to either run those last steps for real or end the test there. If a test fails, Build Studio can make one safe change, show it to you with an Undo, and try again up to three times before asking for help. Approving a workflow is still your decision, and approving never publishes anything. We also repaired a broken file in the part that runs workflows, which had stopped it from starting at all.
— 2026-10-18

**After recording, Build Studio now compiles on its own and asks you a few questions — 2026-10-18**
When you finish recording, the workflow is turned into steps automatically, with a progress card that only shows real progress. If another workflow is already compiling, yours waits its turn and says so. Then the assistant asks up to five short questions it can't answer by itself, like whether to ask before sending something that can't be undone, and every answer is applied through the same safe editing tools a person would use.
— 2026-10-18

**You can now sign in and record a workflow from its conversation — 2026-10-18**
Starting a workflow now opens its conversation, which signs you in first if the app needs it and then records, showing each action in plain words as you go. You can pause recording, discard it, or save what you have if the browser closes by accident. When you type a value you can say whether it changes each time, so you are asked less later.
— 2026-10-18

**Gave every workflow its own conversation, and made approved mean approved — 2026-10-18**
Each workflow now has one running conversation that remembers what happened to it, and there is a general chat for planning a workflow before recording it. The assistant can suggest a change as a card you can apply or undo, and nothing changes until you press the button. We also fixed a mix-up where any edit, even an automatic one, made a workflow look approved; now an edit takes the approval away until a person approves again.
— 2026-10-18

**Gave Build Studio its new look and layout — 2026-10-18**
Build Studio now has a side menu that lists every group and every workflow inside it, each with a small symbol showing whether it is fine, needs you, failed, running or not started. A details panel on the right shows where the open workflow is in its journey, and both side panels can be resized or folded away with the keyboard. It is like moving from a pile of separate pages to one workbench where everything is within reach. The conversation itself, which is the next big piece, is not built yet.
— 2026-10-18

**Planned the new conversation-style Build Studio, step by step — 2026-10-18**
Before changing anything, we read how the app works today and turned the new design into a phase-by-phase plan that was approved. The plan keeps every existing safety rule, and it adds a pause that stops a test just before a step that can't be undone, like sending a real invoice. It also gives one clear name, "AI credits", to the allowance that chat and editing both use. Think of it as the building plans signed off before the first wall goes up.
— 2026-10-18

**Made the Build Studio design safer and easier to follow — 2026-10-18**
Tests no longer do anything that can't be undone, like sending a real invoice, without asking first, and that includes the automatic retries after a failure. Unfinished workflows now show up inside their group from the start, instead of hiding in a separate list. Messages now name steps ("Send invoice") instead of numbers that change as steps are added. We also added a pause button and a way to flag changing values while recording, plus a few smaller clean-ups.
— 2026-10-18

**Wrote the build plan for the new Build Studio — 2026-10-18**
We wrote a detailed set of instructions that an AI assistant can follow to build the new conversation-style Build Studio. It covers the screens, the chat that remembers each workflow's history, the safety rule that tests never send anything real without asking, and the automatic fix-and-retest loop. It is split into steps, and the assistant must stop and get approval on its plan before writing any code.
— 2026-10-18
