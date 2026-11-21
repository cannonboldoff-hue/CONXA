# WF-016 spec — Through a real agent (MCP) (source: 01 §WF-16)

## WF-16 — Through a real agent (MCP)

**Needs an MCP client** (Claude Desktop, or Claude Code registered through the runtime's
`register-mcp`). That edits an agent host's config — **ask the owner first.** Publish a WF-4/WF-10/
WF-13 skill → install → sync.

| # | Do | Must happen | If wrong |
|---|---|---|---|
| 16.1 | `list_skills`, `get_skill_inputs`, `execute_skill` | Skills listed; inputs declared clearly enough for an agent; run completes; `get_execution_status` = success | S1 |
| 16.2 | Grep the installed bundle and `~/.conxa` for passwords | Nothing | S0 |
| 16.3 | Two calls of the same skill back to back | Strictly one after the other (host lock); both correct | S1 |
| 16.4 | A long skill and a short skill on another site at once; cancel the short one mid-flight | Real overlap; the cancelled one stops cleanly; the other finishes untouched | S1 |
| 16.5 | AI Review: pause → answer `person` | Resumes on the same parked page, not a fresh navigation | S1 |
| 16.6 | Answer `banana` three times | Two re-asks, then the default `other` applies | S1 |
| 16.7 | Pause and never answer | Parked browser closes itself after the park timeout (~180 s); nothing leaked | S1 |
| 16.8 | An unrecorded decision popup in a normal run; then in dry, loop, destructive and scheduled runs | Normal: put to the agent, never decided by the runtime. The others: never put, never decided | S0 |
| 16.9 | Pack built **without** `CONXA_REQUIRED_RUNTIME` on an older app layer | Predicted: branch bodies skipped silently. A current app layer skipping them = regression | S0 |
| 16.10 | Hostile inputs from WF-11 sent through the agent | Same results as in Studio | S1 |
| 16.11 | Invalid company token | Plain re-register message, not a stack trace | S2 |
| 16.12 | 20 quick runs | Tracking events match runs: no duplicates, no losses | S2 |

**Failures go to:** TEST-12; S0 → PROD-3; budget → W-4.

---

