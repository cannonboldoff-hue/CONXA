# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Very long workflows keep their step-by-step descriptions — 2026-11-09**
When a workflow had a great many steps, the assistant that describes each step could run out of room mid-answer and hand back nothing. The workflow was still built, but the descriptions were silently missing. Now the assistant is given more room the longer the workflow is, and if its answer still gets cut off, the steps are described in smaller groups and put back together. If any step still cannot be described, you are told.
— 2026-11-09
