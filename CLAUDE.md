# CLAUDE.md

Guidance for Claude Code in this repository. Detail lives in `docs/`; this file holds only what you need on every task.

## What this is

Conxa lets SaaS vendors record browser workflows in the Build Studio, compile them locally into skill packages, publish them to Conxa Cloud, and ship them to customers as `.exe` installers. The customer's AI agent (Claude Desktop etc.) runs them locally through an MCP runtime. The cloud coordinates; it never records, compiles or executes.

| Part | Path | Role |
|---|---|---|
| Shared core | `packages/conxa-core/` | Python package `conxa_core`: settings (`SKILL_` env prefix, `config.py`), DB, models, storage, LLM router |
| Build Studio | `conxa-builder/` | Electron + React renderer, Python stdio backend (`python/backend.py`, `handlers/`). Local pipeline in `python/conxa_compile/` |
| Cloud | `conxa-cloud/` | FastAPI backend (Render) + Next.js dashboard (Vercel): LLM proxy, hosting, telemetry, billing. Tests in `conxa-cloud/tests/` |
| Runtime | `runtime/` | Node MCP server on the customer machine: host exe (`bootstrap.js`) + independently updated app layer (`server.js`, `run.js`, `resolver.js`, `cascade.js`…) |
| Execute | `conxa-execute/` | Form + chat client for the runtime, with its own metered backend |

## Who we build for

Conxa is built first for non-technical people, and lets technical people go deeper and take more control. Apply this to every user-facing change:

- **The default path needs no technical knowledge.** Record, answer questions, test, publish and install must all be finishable without reading a selector, a log or a config file.
- **Default wording is plain.** Screens, chat messages, questions and errors use everyday words. No selectors, step IDs, stack traces or internal names unless the person opens a detail view.
- **Depth is opt-in, never removed.** Technical controls (Human Edit, step details, raw values, logs, advanced settings) sit one click behind the simple view. Do not delete or hide a power-user control to simplify a screen; move it behind a "details" or "advanced" layer instead.
- **Both paths reach the same result.** Anything a technical person can do in the editor, a non-technical person should be able to ask for in plain words in chat, and the reverse. Neither path may silently skip a safety rule the other enforces.

## Docs — read the relevant one before non-trivial changes

- `docs/TRD.md` — authoritative architecture: recorder, compiler, runtime, auth, API, recovery cascade.
- `docs/App-Flow.md` — end-to-end user flows.
- `docs/Backend-Schema.md` — data models, API contracts, KV namespaces, telemetry events.
- `docs/UI-UX-Brief.md` — every screen. UI changes also read that app's `PRODUCT.md` + `DESIGN.md` (`conxa-cloud/frontend/`, `conxa-builder/electron/renderer/`).
- `docs/Security.md` — security gap tracker (SG-xx). `docs/Auth-and-Updater.md` — auth and both auto-updaters.
- `docs/Popup-and-State-Handling.md`, `docs/Understanding-First-Harness.md` — popup and understanding-first designs.
- `docs/cost_model.md` — LLM cost decisions. `SHIP-GUIDE.md` — releases.
- `TODO.md` — the backlog. `Done.md` — completed items.
- `docs/archive/` — history only. Read `docs/archive/refactors/PHASE_4_REFACTOR_REPORT.md` before trying to split `config.py` again.

## Working Principles

1. **Think before coding.** State assumptions. If multiple interpretations exist, surface them — don't silently pick one. If a simpler approach exists, call it out. If something is unclear, stop and ask.
2. **Simplicity first.** Write the minimum code needed. No speculative abstractions, no configurability for single-use code, no handling for impossible scenarios. If 200 lines could be 50, rewrite it.
3. **Surgical changes.** Touch only what the task requires. Don't refactor unrelated code; mention issues rather than fixing them. Match existing style. Every changed line must trace to the task. If your changes leave unused code, remove only what *you* introduced.
4. **Goal-driven.** Define success → implement → verify. Bug → reproduce → fix → verify. Refactor → ensure no behavior change.
5. **Subagent model is one tier below the main agent.** When spawning any subagent (Agent tool), always pass `model` explicitly: main agent on Opus → `model: "sonnet"`; main agent on Sonnet → `model: "haiku"`. Never let a subagent inherit the main agent's model.
6. **"Answer me" means explain everything in detail, in easy language.** When the user says "answer me", drop any terse or compressed style (caveman, ponytail brevity, etc.) for that reply. Explain the full picture step by step: what it is, why it works that way, and what it means for them. Use simple everyday words, short sentences, and an analogy where it helps. Define any technical term the first time you use it. Do not skip steps or assume background knowledge.

- Don't read files over ~25 KB whole; use offset/limit or grep. Session artifacts under `data/sessions/` are huge — always scope reads.
- When asked to create an artifact, also save a copy in `docs/artifacts/<artifact_name>`.

## Commands

```bash
.\scripts\setup.ps1        # first-time setup (Windows); ./scripts/setup.sh on macOS/Linux
cd conxa-builder/electron && npm run dev             # Build Studio
cd conxa-cloud && pytest -q tests                    # Python tests (core + compile + cloud)
cd conxa-cloud/backend && uvicorn app.main:app --reload --port 8000
cd conxa-cloud/frontend && npm run dev | npm run lint | npm run build
cd runtime && npm test | npm run build:win
```

Config: copy `.env.example` to `.env`. Provider keys: `conxa-cloud/backend/ROUTER_SETUP.md`.

## Key invariants — do not work around these

Each is explained in full in `docs/TRD.md`.

**Boundaries**
- The cloud never records, compiles or executes.
- Auth files (`auth/auth.json`, storageState, credentials) never enter build output; `skill_package_builder.py` enforces it.
- All API routes live under `/api/v1`. The only exception is the bare `/api/tracking/{company}/events` alias for already-installed runtimes. Do not add others.
- One workspace has one skill pack and one installer. `workspace_id` is the only identity key; never reintroduce a separately owned slug. The installer's domain name is cosmetic only.
- The installer stages only `pack.json`; skills arrive later through `runtime/sync.js` delta sync.
- Only the runtime (`register-mcp` / `unregister-mcp`) edits an agent host's MCP config. The NSIS installer just calls it.
- Host exe is built `--no-bytecode` (bytecode crashes Playwright). `bootstrap.js` enforces the app layer's `min_host`; never bypass it.

**Compile**
- A step comes only from something the person did. Script events (`origin == "script"`) are dropped in `prepare_compile_events`; never add name- or site-based filters for this.
- No LLM writes selector strings on the primary compile path: `IdentityBundle` + `selector_grammar.py` only. One user-initiated exception: the 1-click fix (`compiler/patch.py`).
- The second opinion (`compiler/second_opinion.py`) may write meaning (input bindings, placeholders, phase, `try_dismiss` branches, optional text/URL assertions, archiving no-effect steps). It never touches `target`, `identity_bundle`, `compiled_selectors`, waits or the frame/tab chain. Human Review is the gate.
- `try_dismiss` branches are built only by `second_opinion.py::build_try_dismiss_from_hint`.
- Chat edits never touch address keys (`ADDRESS_KEYS` in `editor/capability_manifest.py`). Chat never re-points a step; a person does it in Human Edit.
- Iframe chain is kept verbatim from recording to execution; bounding boxes are page-level. `frame_enter`/`frame_exit` get `no_recovery_block`.
- An app map (`app_graph/`) stores app shape, never data (all text through `scrub.py`), uses no LLM, and is always rebuilt, never edited.

**Questions and chat (Build Studio)**
- The understanding check labels meaning only; code does every rewrite (`parameterize_literal`, and `parameterize_click_text` only on a person's explicit act).
- Code decides which questions to ask (`conversation/unknowns.py`, `scoring.py`). Popups, irreversible steps and unsafe guesses always ask.
- Every asked question must be answered: no skip, no defer. An unanswered question blocks test, approve, publish and Build Installer.
- Chat can only run actions in `lifecycle.allowed_actions`; anything with a side effect waits for a Confirm card (`conversation/actions.py`, mirrored in renderer `lib/actions.ts`).

**Runtime**
- A step runs only on the tab it was recorded on (`tabs.js::resolveStepPage`). If that tab can't be found, the step fails; no fallback.
- The resolver never picks `candidate[0]` blindly: the winner must beat the runner-up by `uniqueMargin` (0.15).
- An app fault (offline, 4xx/5xx, alert, invalid field) ends the run before any recovery (`run.js::detectFault`).
- In a host panel, the person's mouse never reaches a page the runtime is driving: a transparent shield sits over it, and only a hand-over lifts it (`handover.js::arm`/`disarm`, control op `input`; the runtime asks for it at run start). Never block input with CDP `Input.setIgnoreInputEvents`; it also drops the run's own clicks.
- Only a host panel (`browser_panel.js`) sets the page size; every runtime screenshot goes through `page_geometry.capture()`, never `page.screenshot()` (its clip resets the size). A host-owned page at the wrong width ends the run before the step (`page_geometry.js::checkGeometry`); the width is pinned at 1280, the height follows the pane.
- A run's page behaves the same whether or not it is on screen: a host panel hides a view with `setVisible(false)` and never shrinks it to 0×0, places every new tab itself, and a still of a hidden page comes from the host's `capture` op, not the CDP screenshot (`browser_panel.js`).
- Tier A recovery costs zero LLM tokens. LLM is used only at Tier B. No silent LLM fallback in selector or a11y resolution.
- A step is proven only by a witness that would have answered differently had the action done nothing: background requests never count, and when none of the results the recording saw reappear, only a witness that found the result itself proves it. A test run never retires a check it could not read, and never learns from a pass that rested on "the step ran". The function an `evalOn()` runs in the page comes from `page_scripts.js`, never an inline or module-local function.
- `extract` resolves like a click; no match or empty value fails. `run_if` reads inputs only and can never skip a guard. `on_missing: "skip"` acts only on provable absence.
- `agent_span` is the one author-placed step where an agent drives the browser. The agent picks numbered actions, never selectors. Irreversible clicks need the person's confirmation. Spans fail closed on scheduled, dry, loop and called runs. Manifest says `uses_agent: true`.

**Popups**
- A decision popup is never answered on anyone's behalf. A recorded one compiles to an `outcome_fork`; a consequential option runs only with `policy.author_approved`, written only by `set_fork_policy`.
- An unrecorded decision popup may be put to the MCP client, never decided by the runtime. This never happens when a button looks irreversible, or in dry, loop, destructive or scheduled runs.
- Nothing pauses by default. A person is asked only where an author asked for it.
- Shared fixture: `runtime/test/fixtures/decision_surface_cases.json`.

## Keeping docs current

After significant changes, update the matching doc: architecture, auth or recovery → `TRD.md`; endpoints or data models → `TRD.md` + `Backend-Schema.md`; user flows → `App-Flow.md`; screens → `UI-UX-Brief.md`. Bug fixes, refactors, config and test changes need no doc update. Never edit `docs/PRD.md` for features; it changes only for company-level strategy shifts.

**TODO.md:** when an item is done, cut it out of `TODO.md` and file it in `Done.md` under its area (BUILD, EXEC, RT…) in ID order, with a `**Resolved:** YYYY-MM-DD` line and one line on the fix; update the summary table and any cross-references. Add newly found open work to `TODO.md`.

**FIX.md:** after every prompt, add an entry a non-technical person understands on first read:
- Bold title ending in ` — YYYY-MM-DD`, then 2–4 short plain sentences: the problem in real-world terms, what changed, why it matters to a user.
- No code names, filenames, function names, jargon or acronyms. Use plain product names ("the Publish page").
- End with a line `— YYYY-MM-DD`.

When `FIX.md` spans more than one day, move older entries into `docs/archive/fix-log/FIX-<YYYY-MM-DD>.md` (split by each entry's own date), add a row to that folder's `INDEX.md`, and keep only today's entries live.
