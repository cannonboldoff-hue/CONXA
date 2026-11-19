# CONXA E2E Testing and Continuous Improvement

You have control of `conxa-build-studio-dev`: CONXA (Build Studio), its built-in Chromium browser, and the codebase.

**Mission.** Test the workflows listed in `docs\testing\01-WORKFLOWS-TO-TEST.md` one at a time, in order. Fix failures at the root cause, improve the product where genuinely useful, and prepare CONXA for publication. Commit each workflow once it passes. Stop after the workflow the owner names; if none is named, do one workflow per session.

## 1. Roles

| Role | Model and effort | Job |
|---|---|---|
| **Orchestrator** (the main session) | Sonnet, medium | Owns the workflow queue, progress, handoff files, logs, agent lifecycles and commits. Keeps its own context small: it delegates and summarizes, and does not read code or logs. |
| **E2E Tester** | Sonnet, medium | Tests through CONXA and its built-in Chromium. Records workflows, runs all cases, verifies the real outcome, captures evidence, reports failures, and independently retests after fixes. **Never edits application code.** |
| **Engineering Owner** | Opusplan at **high** effort: Opus for planning, Sonnet for implementing (`/model opusplan`, `/effort high`) | Opus, read-only, investigates failures (architecture, git history), finds root causes and writes one implementation plan. Sonnet implements the approved plan, adds or updates regression tests, and runs unit, integration and regression checks. Opus is used only when the cause is not obvious; simple failures go straight to Sonnet. |

If the owner's prompt names a model for a role, use that model.

## 2. Cost and context rules (these beat every other rule)

1. **Cost is context size times number of calls.** An agent that is resumed many times re-reads its whole history on every call. Prefer a **new** agent over resuming one whose history is over about 300k tokens. Never resume a tester more than once.
2. **Persist, do not re-derive.** Every agent writes findings to its workflow handoff file as it goes, one line per case. A new agent reads that file, not the old conversation. Never pass whole conversations, raw logs, screenshots or browser traces between agents.
3. **No big reads.** Never read `01`, `02`, `05` or `06` in `docs\testing` whole (48 to 137 KB each), and never read whole files under `data\sessions\`. The orchestrator writes a small per-workflow spec (section 4) once; agents read only that and the handoff file, then grep or offset-read code.
4. **Short outputs.** Subagent final replies and script output stay concise. No raw logs and no screenshots in chat. Screenshots over CDP hang in this setup, so read DOM text and files instead.
5. **Batch, do not trickle.** The tester runs all cases of a workflow in one pass and reports every failure in one batch. The engineering owner fixes the whole batch in one round.
6. **Right-size the effort.** Not every failure needs Opus. Mechanical jobs (restart, rebuild, grep a log) are one script call from the orchestrator, or Haiku.
7. **Do not create unnecessary agents or sessions**, but never keep an agent alive just to preserve its context.

## 3. One-time setup (build once, reuse for every workflow)

- `scripts\e2e-up.ps1`, one command that: stops old Studio, vite and backend; rebuilds the test runtime (`scripts\build-app-local.ps1` with `CONXA_STUDIO_HOME=%USERPROFILE%\.conxa-build-studio-dev`); starts the cloud backend with `CONXA_ENV=dev CONXA_DEV_SKIP_AUTH=1 SKILL_ALLOW_NO_PROVIDERS=1` and **no** `--reload`; starts Studio with `scripts\conxa.ps1 dev studio` (plain `npm run dev:electron` loses the sign-in); starts the fixture server `python -m http.server 8099 --directory docs\testing\fixtures`; prints one line `CDP=<port> backend=200 fixtures=200`. The CDP port changes on every Studio start, so the tester must be given the new port each time.
- `docs\testing\tools\studio_driver.mjs`, Playwright `connectOverCDP` helpers (open workflow, record, answer a question, compile, run a test, read result text) that print compact JSON, so each test action is one short call and not five exploratory ones.
- `docs\testing\workflow-handoffs\TEMPLATE.md`, the handoff template (section 4).

**Stale-code rule (this cost a whole round before).** Re-run `e2e-up.ps1` after any edit under `packages\conxa-core`, `conxa-builder\python`, `conxa-cloud\backend` or `runtime`. The backend does not hot-reload `conxa_core`: an old backend keeps sending the old compile prompt and every live result is then wrong. Never record, or leave idle, a recording across a pause: a recording left open for 143 minutes saved with no video and hung Studio.

## 4. Shared records

One handoff file per workflow, `docs\testing\workflow-handoffs\WF-0NN.md`, is the source of truth between testing and engineering. It holds:

- requirements and expected outcomes, with inputs, authentication needs and conditions;
- the case table: id, PASS / FAIL / BLOCKED, severity (S0 to S3), evidence path, one-line repro;
- root causes and engineering decisions;
- files changed and code-level test results;
- retest results, final status, and the token use of each subagent run (for example `tester run 2: 150k`).

Alongside it, the orchestrator writes `WF-0NN-spec.md` (2 KB or less): the cases, sites, inputs and the pass rule for that workflow, extracted from `01-WORKFLOWS-TO-TEST.md`.

Store large logs, screenshots and traces under `docs\testing\evidence\WF-0NN\` and reference the path.

**The handoff file is the only detailed record.** `06-E2E-TEST-AND-FIX-LOG.md` and `02-WORKFLOWS-PASSED.md` are thin indexes, never diaries, and nobody copies handoff detail into them:

- `06-E2E-TEST-AND-FIX-LOG.md`: one short entry per workflow: date, result (PASSED / OPEN / BLOCKED), number of engineering rounds, commit hash, link to the handoff file.
- `02-WORKFLOWS-PASSED.md`: one line per passed workflow: date, what the pass proves in one sentence, link to the handoff file.

One-time cleanup before the first workflow: move the existing long diary body of `06` (92 KB) and the existing body of `02` (74 KB) to `docs\archive\testing-history\`, keep the files themselves as short indexes that link to the archived copies, and never read the archived copies unless the owner asks.

## 5. Per-workflow loop

1. **Spec.** The orchestrator writes the spec and the handoff file.
2. **Static pass (nearly free).** An implementer reads the code the workflow attacks, runs the targeted pytest and npm tests, and lists likely failures. Fix the obvious ones before any live test.
3. **One live pass.** A fresh tester runs **all** cases and records one line per case. It stops at the first S0 and reports. It does not fix anything.
4. **One engineering round per batch.** A fresh planner (Opus only if the cause is unclear) reads the handoff and only the failing evidence, and returns one concise plan that covers **all** failures: root cause with file and line, fix, tests, commands, and any decision the owner must make. A Sonnet implementer applies it, adds regression tests, and runs **targeted** tests.
5. **Retest only what failed**, plus one smoke case, with a fresh tester. **Reuse saved recordings:** when the fix is in compiler, runtime or conversation code, recompile the saved session with `conxa-cloud\scripts\recompile_session.py` (needs `CONXA_TOKEN=dev-skip-auth-token` and `SKILL_ALLOW_NO_PROVIDERS=1`) instead of re-recording. Re-record only when the fix is in the recorder (`bridge.js`, `session.py`).
6. **Loop cap: three engineering rounds per workflow.** After round 3, mark the still-failing cases `OPEN` with severity in the handoff and in `TODO.md`, and ask the owner whether to continue. Do not loop silently.
7. **Once, at the end:** the full unit suites (`cd conxa-cloud && pytest -q tests`, `cd runtime && npm test`) and one complete regression pass of the workflow. Not after every fix.
8. **Close.** Add the index entries to `06` and `02` (section 4), update `FIX.md`, then commit once on the current branch (never main). Do not push. Then stop, unless the owner asked for more.


## 6. Engineering principles and product priorities

- Investigate before implementing. Read the architecture and the git history where useful.
- Fix root causes, never symptoms. Prefer general solutions over fixes for one site, one workflow or one recorded value; do not hardcode them.
- Respect the `CLAUDE.md` invariants (for example: the second opinion never touches address keys; chat never re-points a step; unsure counts as cannot be undone; secrets never enter a package). If the spec and an invariant disagree, ask the owner. Never edit the spec to make a test pass without saying so in the log.
- Add or update regression tests for every meaningful fix. Do not weaken any existing behavior or quality gate to make a test pass. Preserve unrelated changes in the working tree.
- Worth fixing when found: reliable authentication and execution; dynamic inputs and conditional branches; downloads, uploads and async work with final-outcome verification; clear UI feedback, navigation, progress and errors; conversational editing, clarification and recompilation without needless re-recording.
- No redesign or overengineering.

## 7. Orchestrator discipline

- Record each subagent's token use in the handoff header. If one workflow passes 1.5M subagent tokens, stop and report to the owner.
- Never claim a result you have not read in a handoff file or tool output. Report failures plainly, including S0 findings and any gate or spec wording you changed.
- Do not start the next workflow unless the owner asked for it.

---

## Why these rules exist (measured 2026-11-19, WF-9 only)

From the three subagent transcripts of that session:

| Agent | API calls | Average context per call | Cache-read tokens |
|---|---|---|---|
| Tester (resumed 7 times) | 610 | about 258k | about 158M |
| Implementer (resumed 3 times) | 348 | about 196k | about 68M |
| Opus planner (resumed 3 times) | 263 | about 157k | about 41M |
| **Total** | **1,221** | | **about 267M** |

Causes, in order of cost: (1) long agents were resumed instead of replaced, which alone was about 60% of the total; (2) driving Studio by hand over CDP took 300+ tool calls for one workflow; (3) every fix round re-recorded and re-tested every case, even when only compiler code changed; (4) a stale backend made one whole retest check the wrong code; (5) "repeat until all cases pass" had no limit, and one bug took three rounds; (6) the Opus planner was resumed with a large context; (7) an idle recording across a usage-limit pause was lost; (8) agents were allowed to read the 48 to 137 KB test docs and session data whole.
