# Fix Log

> Rotated daily into `docs/archive/fix-log/` — see [INDEX.md](docs/archive/fix-log/INDEX.md) for older entries.

**Testing now has a simple "Dry Run" switch — 2026-11-09**
The test screen used to offer "Let AI answer agent steps and unexpected popups", which was hard to understand. It is now called "Dry Run" and is on by default, which means a safe test that uses no AI credits. If the workflow has a step that needs AI, Dry Run turns itself off and stays off, so that step can actually run. Otherwise you can switch it off yourself to let the AI helper answer an unexpected popup.
— 2026-11-09

**Very long workflows keep their step-by-step descriptions — 2026-11-09**
When a workflow had a great many steps, the assistant that describes each step could run out of room mid-answer and hand back nothing. The workflow was still built, but the descriptions were silently missing. Now the assistant is given more room the longer the workflow is, and if its answer still gets cut off, the steps are described in smaller groups and put back together. If any step still cannot be described, you are told.
— 2026-11-09
