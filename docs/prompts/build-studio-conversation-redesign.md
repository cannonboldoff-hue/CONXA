# Prompt — Implement the conversation-led Build Studio redesign

> Paste everything below the line into a fresh Claude Code session at the repo root.
> It is written to be executed in phases, with a stop for approval after Phase 0.

---

You are implementing the **conversation-led Build Studio redesign** in this repo (Conxa). This is a large, multi-phase change touching the Electron renderer, the Electron main process, the Python stdio backend, the local test runtime, and a small part of the cloud backend. Work in phases, verify each one, and stop where this prompt says to stop.

## 0. Read first (do not skip)

1. `CLAUDE.md`: especially **Key Invariants**, **Working Principles**, and the doc-maintenance, `FIX.md` and `TODO.md` rules. Every rule there still applies.
2. **The design source of truth:** `docs/artifacts/build-studio-redesign/` (one `.dc.html` per screen plus `canvas.json`). Read `canvas.json` first for the screen list. The live canvas is https://claude.ai/artifact/QFK6vqjkYcReAupyoqtzx9. These are HTML mockups: copy their layout, copy text, states and behaviour, not their markup. Screens:
   - Foundations: `Main` (tokens), `Components` (conversation parts), `Sidebar`, `Inspector`.
   - Flow: `FirstLaunch` → `Chat` → `Group` → `NewWorkflow` → `Recording` → `Compiling` → `Clarify` → `HumanEdit` → `TestInputs` → `TestRunning` → `TestFailed` (auto-fix and re-test) → `TestPassed`.
   - Specs: `States` (12 system states), `Panels` (panel sizes, breakpoints, keyboard, native browser-view rules), `Settings`.
3. `conxa-builder/electron/renderer/PRODUCT.md` and `DESIGN.md`. The mockups already follow them (three charcoal steps, one clay accent per screen, Inter, no shadows). Where a mockup and `DESIGN.md` disagree, `DESIGN.md` wins; note the conflict in your plan.
4. `docs/TRD.md` (Build Studio pipeline, test sandbox, recovery cascade §10.1, `ai_review` §11.3), `docs/App-Flow.md`, `docs/UI-UX-Brief.md`.
5. Existing code you will build on rather than replace:
   - **Copilot (BUILD-26):**
     - `conxa-builder/python/handlers/copilot.py` (`cmd_copilot_turn`, `cmd_accept_copilot_proposal`, `cmd_copilot_verify`, load and save session).
     - `conxa_compile/llm/copilot.py`: stateless per call, streamed reply plus a separate `copilot_diagnose` call, and a bounded `need[]` retrieval loop.
     - `conxa_compile/editor/copilot_proposals.py` (proposal gates), `copilot_retrieval.py`, `capability_manifest.py`, `evidence.py` (`detail="digest"`), `copilot_sessions.py`.
     - Renderer side: `renderer/src/components/copilot/*` and `store/copilotStore.ts`.
   - **Editing:** `handlers/workflow_editor.py` (patch, insert, delete, undo and redo, retarget, `sign_off_workflow`) and `conxa_compile/editor/workflow_mutations.py`, including the shared `try_dismiss` builder.
   - **Flow commands:**
     - `handlers/workflows.py`: `cmd_create_workflow`, `cmd_test_workflow`, `cmd_cancel_test_workflow`, `cmd_get_usage`, publish.
     - `handlers/session.py`: `cmd_start_recording`, `cmd_stop_recording`, `cmd_cancel_recording`.
     - `handlers/compile.py`, `handlers/groups.py` (group apps plus per-group sign-in), `handlers/runs.py` (`cmd_get_failure_evidence`).
   - **Compiler judgement and clarifying questions:** BUILD-25 (`llm/workflow_semantics.py` → `compiler/second_opinion.py`) and **BUILD-32** in `TODO.md`. The Clarify screen *is* BUILD-32; implement it as BUILD-32 and move BUILD-32 to `Done.md` when finished.
   - **Embedded browser precedent:**
     - `conxa-execute/app/electron/browser_panel.js` and `browser_control.js`: a `WebContentsView` owned by Electron, plus a loopback control channel.
     - `runtime/app/host_browser.js`: Playwright `connectOverCDP` borrowing that view.
     - Read `docs/TRD.md` §4.7 and `Done.md` EXEC-41.
   - **Renderer today:** `renderer/src/pages/*` (WorkflowListPage, GroupPage, HumanEditPage, InspectorPage, PublishPage, SettingsPage…), `components/layout/AppChrome.tsx`, `store/*`, `api/*`; `electron/main.js` and `preload.js`.
   - **Cloud LLM proxy:** `conxa-cloud/backend/app/api/llm_proxy*.py` (`ProxyBody`, no `messages` field) and `app/services/entitlements.py::ALLOWED_USAGE_CLASSES = {"compile","human_edit","execute_chat"}`.

## 1. Non-negotiables

These are restated so you cannot miss them. Full text is in `CLAUDE.md`.

- **The LLM never writes selector strings on the primary compile path.** Clarify answers, auto-fix and chat may only change a workflow through the **existing gated mutations**: `patch_gate`, `gate_proposals` / `gate_overlay_proposals` / `gate_structural_proposals`, `workflow_mutations`, and the shared `build_try_dismiss_from_hint`.
  - A Clarify answer that changes a target may only choose among selector options `IdentityBundle` / `selector_grammar.py` already produced (BUILD-32 constraint).
  - "Done when" checks may only add the text, URL or state assertion types BUILD-25 allows, always `required=False` unless the user ticks them.
- **Tier A recovery stays zero-token.** Auto-fix is not recovery. It runs *after* a test has failed, outside the cascade, through the copilot.
- **Auth files never enter build output.** Recording, sign-in and test sessions stay local runtime state.
- **The cloud does not compile or execute.** The chat harness lives in the Studio's Python backend. The cloud only proxies and meters LLM calls.
- **Iframe chains, tab identity (`resolveStepPage`) and the `frame_enter` / `frame_exit` no-recovery rule are preserved.** Moving the browser into an embedded panel must not change what is recorded.
- **Approval is always a human action.** Nothing auto-approves or auto-publishes.
- **Tests never perform an irreversible step without a person allowing it** (see §3.5). This includes automatic re-tests.
- **Surgical changes.** Restyle and restructure the renderer, but reuse backend commands wherever they already do the job; add new commands only where the design needs behaviour that does not exist.

## 2. The target experience (summary — the mockups are authoritative)

- **Layout:** three columns.
  - Navigation sidebar (264 px).
  - Workspace: a conversation column with a composer at the bottom.
  - Inspector (320 px, workflow screens only).
  - A native browser panel takes the inspector's place during sign-in, recording and tests; the inspector folds to a 44 px strip.
  - Breakpoints, sizes, keyboard shortcuts and divider behaviour are exactly as in `Panels`.
- **Sidebar:**
  - Top: Chat, New workflow (Ctrl N), and Publish with a neutral count badge.
  - Search (Ctrl K).
  - **Chats** holds general conversations only.
  - **Groups** lists every workflow in its group from the moment it is created, with a status glyph (● ok, ▲ needs you, ✕ failed, ◐ running, ○ not started) and a short note for unfinished ones.
  - Footer: workspace, credits and connection state.
- **Glyphs:** status glyphs always pair shape with colour. Clay orange is never a status colour and never a step-type colour (click and select use pink `#e88fb4`).
- **The workflow conversation is the backbone of each workflow.** One continuous thread per workflow carries everything:
  - Created, sign-in, recording, compile, Clarify, test, auto-fix, approve.
  - System event rows (one line, glyph, timestamp, threaded on a hairline).
  - Assistant prose and user messages.
  - Stateful cards: live recording, compile progress, question, receipt, summary, test inputs, test run, failure, auto-fix, passed, approved.

  A card shows a finished state only after the backend command behind it has returned (rule from `Components`).
- **General Chat:**
  - Read-only over groups and workflow names; can draft a workflow ("Create and start recording" / "Edit details first") but changes nothing until confirmed.
  - Uses the workspace AI allowance, with a footer telling the user so.
- **Steps are referred to by their name in all user-facing text** ("Choose the customer", "Send invoice"); a number is secondary. Counts must add up across screens (e.g. 14 actions → 13 steps → 15 after answers added 2).
- **Human Edit** keeps its job but adopts the shell:
  - "Back to conversation" in the header.
  - Its wizard steps are Element → **How it's found** → Validation (the word "Selectors" must not appear).
  - Its primary button is **Test changes**; there is no Approve button outside the conversation.

## 3. Architecture to build

### 3.1 Workflow lifecycle orchestrator (Python, deterministic)

- Add a per-workflow lifecycle state machine in the backend: `describe → configure (sign-in) → record → compile → clarify → edit (optional) → test → approve`, plus terminal and side states from `States` (offline, engine stopped, compile failed, out of credits, queued compile, sign-in expired mid-test, test stopped, recording discarded, browser closed mid-recording).
- **Derive the state from data that already exists** (workflow store, compile output, test results, sign-off), not from a new parallel source of truth. Persist only what cannot be derived.
- **Transitions happen only through backend commands, never because the model said so.** The LLM writes prose, asks and interprets questions, and proposes edits; the orchestrator decides which card appears next.
- Emit typed events over the existing JSON-RPC event channel (`handlers/protocol.py` `_event_sink`) so the renderer renders cards from events and never infers state from prose.

### 3.2 Conversation thread store (Python, local)

- One append-only thread per workflow at `data/skills/<id>/thread.jsonl`, or next to the workflow if that fits the store better; justify the choice. Same fail-silent contract as `copilot_sessions.py`.
- General chats are stored under a workspace-level folder.
- Item kinds: `user_message`, `assistant_message`, `event`, and `card` (with a `card_type`, a `state`, and the ids of the backend objects it reflects).
- Cards are *updated* by appending a new revision with the same `card_id`, so history is never rewritten. Answered questions collapse to a receipt with a **Change** action that reopens them.
- Older history collapses behind "Earlier · …" (see `Clarify` / `Compiling`) and loads newest-first, with skeletons, never a spinner over a blank page.
- Migrate the existing per-skill `copilot_sessions.jsonl` into the thread (read-only import) so reviewers don't lose history. The Human Edit copilot becomes this same conversation, scoped to the selected step.

### 3.3 Chat harness — the continuous loop and context management

The cloud proxy is stateless (no `messages` field; every task sends one `[system, user]` pair). The harness must therefore rebuild context on every turn. Build it as one module, e.g. `conxa_compile/conversation/harness.py`, used by both general Chat and workflow conversations.

**Turn loop**
1. Receive the user message plus `thread_id` and an optional anchor (the selected step or the open question).
2. **Route deterministically first.**
   - Is it an answer to the open question card? Is it a confirm or cancel of a pending action?
   - Only fall through to the model when it is free-form.
   - Free-text answers to a question go through one short model call that maps the text to one of the card's allowed choices, or to "unclear, ask again". The mapped choice is applied by the same code path as clicking it.
3. **Build the context pack** under an explicit token budget (configurable; start around 12k input tokens for a workflow turn and 6k for general chat). Order of priority, highest first:
   1. System prompt: role, the capability manifest (`capability_manifest.py`), the hard rules from §1 in plain language, and the user-facing rules (name steps, never claim something happened unless an event says so).
   2. Lifecycle state, open cards and their allowed actions.
   3. **Pinned facts:** every answered Clarify decision, the inputs, the "done when" checks, the confirmation points, and the steps marked irreversible. These are never summarized away.
   4. Workflow digest: `build_evidence_bundle(..., detail="digest")`, plus the latest failure evidence when one exists.
   5. A rolling summary of older turns.
   6. The last N turns verbatim, trimmed oldest-first to fit.
   7. Retrieved detail via the existing bounded `need[]` loop (`MAX_RETRIEVAL_ROUNDS`).
4. **Stream the reply** (`stream_llm`, the `copilot_delta`-style event). If the turn may produce edits, run the proposal call separately, as `copilot.py` does today, and pass every proposal through the existing gates.
5. Append everything to the thread.
6. Re-summarize when the verbatim window overflows: one short metered call that folds the oldest turns into the rolling summary, stored as a thread item so it survives restarts.

**Pending changes**
- A proposal that would change the workflow appears as a card with the exact change, **Apply** and **Undo**.
- In the auto-fix loop (§3.6) changes apply automatically but are still listed with **View change** and **Undo**. Undo maps onto the existing `cmd_undo_workflow` / `edit_log`.

**Metering**
- Decide between reusing `human_edit` or adding a `studio_chat` usage class (which needs a cloud change: `ALLOWED_USAGE_CLASSES`, metering, `docs/Backend-Schema.md`). Recommend one in your Phase 0 plan.
- Surface quota and entitlement errors (the existing `EntitlementBlocked` / `QuotaExceeded` / `ProxyUnavailable` / `CloudUnreachable` handling) as the error cards in `States`.

**Concurrency**
- Turns are serialized per thread.
- A second message while a turn streams is queued, not interleaved.
- Cancelling a turn stops the stream and appends nothing half-finished.

**Tests (unit-level, no network)**
- Context-pack assembly under a budget: pinned facts survive, oldest turns trimmed first, the summary is used.
- Deterministic routing of answers.
- Proposals still gated.
- Thread append, revise and reload round-trip.

### 3.4 Embedded browser panel (Electron main + recorder + test sandbox)

- The design puts the real browser *inside* Build Studio for sign-in, recording and tests.
- Follow the Conxa Execute precedent: an Electron `WebContentsView` owned by the main process, resized on every divider move and window resize, with Playwright attaching via `connectOverCDP`.
- Rules from `Panels`:
  - Nothing from the app can draw over the browser region; menus open toward the workspace.
  - Dialogs hide the region with a placeholder.
  - Site-opened popups and tabs become footer tabs.
  - If attaching fails, fall back to a separate window and say so in the panel.
- **This is the highest-risk part of the redesign. Do a spike first:** the recorder in `conxa_compile/recorder/session.py` launches its own Playwright Chromium (`launch(headless=False, args=...)`), injects `bridge.js`, and tracks iframes and tabs. Electron's Chromium is a different build.
  - Prove that recording through an attached `WebContentsView` produces identical `events.jsonl` (frame chain, tab ids, bounding boxes) on one real recording before migrating.
  - If it can't be made identical, keep the separate window for recording, embed only for tests and sign-in, and report why.
- The test sandbox (`conxa_runtime.py`, `cmd_test_workflow`) needs the same attach path. Reuse `runtime/app/host_browser.js` behaviour (inert unless a host view is offered) rather than writing a second implementation.

### 3.5 Test safety: never cross an irreversible step without a person

- **Definition:** a step is *irreversible* if it sends, submits, pays, deletes, publishes or emails something outside the browser.
  - Mark it at compile time (BUILD-25's second opinion can flag it). The reviewer can change the flag in Human Edit, and Clarify can confirm it.
  - Store it on the step as plain data; it never touches `target` or selectors.
- Test inputs carry an **"Ask before steps that can't be undone"** switch, on by default.
- When it is on, the Studio test run pauses before the first irreversible step:
  - Show "Paused before *Send invoice*", plus **Run the last N steps for real** and **End the test here**.
  - Ending there counts as "passed up to *X*", not as a pass.
- **Implementation preference:**
  - Reuse the existing `ai_review`-style pause plumbing in the runtime as a *Studio-only* run option (an MCP tool argument the Studio sends).
  - It must be inert for customer runs, must not touch `recovery_stage.js` or `retry_budget.js`, and must follow the app-layer versioning rules (`min_host`, gate replay).
  - If that requires an app-layer runtime change, call it out in the plan with the version bump it needs.
- The workflow's own confirmation step (`ai_review`, e.g. "Ask a person before sending") and this guard must not ask twice in a row: if a confirmation step immediately precedes the irreversible step, one answer covers both. Say so in the result ("you confirmed, which also let the 3 steps that can't be undone run").

### 3.6 Auto-fix and re-test loop (`TestFailed`)

- On a failed test, the conversation shows the failure card: plain-language cause, what ran and what didn't, "Nothing was saved or sent" when true, and the page at the moment of failure.
- Then it runs the loop, up to 3 attempts:
  1. Find the cause (`cmd_get_failure_evidence` plus the copilot diagnose call).
  2. Apply **one** gated change, listed with View change and Undo.
  3. Re-test with the §3.5 guard always on.
- Stop and ask the user after 3 failed attempts, when the user presses Stop, or when the only possible fix is outside the gates. Offer "Resume auto-fix" and "Fix it myself in Human Edit".
- This loop applies changes without a per-change click. That is a deliberate shift from "the reviewer accepts each proposal", so:
  - Add a Settings toggle for it (default on).
  - Record every automatic change in `edit_log` with an `actor: "auto_fix"` marker.
  - Mention the trade-off in `docs/TRD.md`, next to the BUILD-25 "Human Review is the gate" text.
- Approval stays a separate human action after a pass ("Approve for packaging"; approving does not publish).

### 3.7 Recording, Clarify and compile in the conversation

- **Sign-in:**
  - When the group has no saved sign-in for the start page's app, the browser opens on the sign-in page.
  - Nothing is recorded until **I'm signed in — Done**.
  - The sign-in is saved for the whole group (`groups.py` app auth).
- **Recording:**
  - A live "Captured so far" list (plain-language action rows with type chips).
  - **Pause / Resume**: nothing is recorded while paused; this needs recorder support.
  - Per typed value, a **"Changes each run?"** toggle that becomes an input-binding hint for the compiler, so Clarify asks less.
  - Finish and save / Discard.
  - "Browser closed mid-recording" offers to save what was captured.
- **Compile:** a progress card with real stages from the pipeline, credits used and left, "You can leave this screen", and a queued state when another compile runs. Do not show fake progress.
- **Clarify (BUILD-32):**
  - One question at a time.
  - Each answer applies through existing mutations, shows "applying…" until the command returns, then collapses to a receipt.
  - Cap at about 5 questions.
  - Unanswered questions keep the compiler default and never block testing.
  - Finish with the workflow summary card: name, plain steps, counts that add up, "Done when…", **Test it**, and **Review the N uncertain steps**.

### 3.8 Groups-first navigation and the group page

- A workflow belongs to exactly one group from creation; there are no un-grouped workflows.
- The group page has:
  - Sign-ins with a "checked N ago" status and **Check now**.
  - A workflow table with status, apps, last test and updated time, with filters All / In progress / Needs a fix / Approved.
  - Rename or delete.
- If the backend (`groups.py`, workflow store) cannot express "in progress" without a schema change, add the minimal field and document it in `docs/Backend-Schema.md`.

## 4. Phases (verify each before moving on)

**Phase 0 — Plan, then STOP.**
- Map every screen and card in the mockups to existing backend commands, and list each **gap**: new command, new event, new field, runtime change, cloud change.
- Decide and justify:
  - thread storage location;
  - the usage class;
  - the irreversible-step marking;
  - the runtime pause mechanism;
  - whether recording can move into the embedded view (plan the spike).
- List every invariant each phase touches and how it stays intact.
- Write the plan to `docs/Build-Studio-Conversation-Plan.md`.
- Add new `TODO.md` entries with BUILD-* ids for each phase.
- **Stop and wait for approval.**

**Phase 1 — Shell.**
- Sidebar (groups-first, Chats, Publish badge, search, footer), inspector, resizable panels with remembered widths, breakpoints, keyboard map.
- Tokens from `DESIGN.md`, and status glyph and step-type colour constants in one place.
- Routing between Chat, Group, workflow conversation, Human Edit, Publish and Settings.
- Existing pages keep working behind the new shell until replaced.
- Verify: `npm run typecheck`, `npm run lint`, `npm test` in `conxa-builder/electron`; run the app and walk the sidebar.

**Phase 2 — Thread store + orchestrator + harness.**
- Backend modules and RPC commands; renderer conversation view rendering items and cards from events; streaming; general Chat.
- Port the Human Edit copilot onto the same thread.
- Verify: new pytest unit tests (see §3.3) under the existing `conxa-cloud/pytest.ini` paths, plus a manual multi-turn chat that survives an app restart.

**Phase 3 — New workflow → sign-in → record in the conversation.**
- Keep the current separate recording window for now.
- Add Pause and "Changes each run?" with recorder support.
- Verify: record a real workflow end to end; `events.jsonl` is unchanged in shape apart from the new hints.

**Phase 4 — Compile + Clarify (BUILD-32 first slice, then the rest) + summary card.**
- Verify: the compile output is byte-identical when no question is answered (the BUILD-25 rule); answered questions change only allowed fields.

**Phase 5 — Test inputs, test running, failed + auto-fix loop, passed + approve, with the §3.5 guard.**
- Verify:
  - The guard pauses before an irreversible step in a real sandbox run.
  - Auto-fix stops at 3 attempts.
  - Undo reverts an automatic change.
  - Approve never publishes.

**Phase 6 — Human Edit restyle** inside the shell (wizard labels, Test changes, back to conversation, pink step-type chips, step names).

**Phase 7 — Embedded browser panel.**
- Spike first; then sign-in and tests; then recording only if the spike proves identical output.
- If the runtime changes: run `runtime/test/` unit tests and the gate replay as CI would.

**Phase 8 — States, offline, engine stopped, Settings, first launch** (including the failing-check variant). Then documentation (§5).

At the end of every phase:
- List what changed, what was verified and how, and what is left.
- Note anything you were unsure about rather than guessing.

## 5. Documentation and bookkeeping (required by `CLAUDE.md`)

- `docs/TRD.md`: the conversation harness, orchestrator, thread store, embedded browser path, test-safety guard and auto-fix trade-off.
- `docs/App-Flow.md`: the new end-to-end flow.
- `docs/UI-UX-Brief.md`: every new or redesigned screen.
- `docs/Backend-Schema.md`: new RPC commands, events, fields and usage class.
- `docs/Implementation-Plan.md`: status.
- Do not edit `docs/PRD.md`.
- Move finished `TODO.md` items to `Done.md` with `**Resolved:**` lines. Add any new open work you discover to `TODO.md`.
- After every prompt, append a plain-language `FIX.md` entry (2–4 sentences, no file or function names, ending with `— YYYY-MM-DD`). Rotate it on a new day as `CLAUDE.md` describes.

## 6. How to work

- State assumptions; when two readings of the design exist, ask.
- Prefer extending existing modules over parallel new ones; delete only code that your change made unused.
- Never claim something works without running it. If you could not verify a step (for example a real sign-in), say so plainly.
- Do not commit or push unless asked.
