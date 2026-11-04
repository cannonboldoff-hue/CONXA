# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

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
