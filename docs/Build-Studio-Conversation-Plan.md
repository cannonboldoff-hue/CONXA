# Build Studio — conversation-led redesign: Phase 0 plan

## Context

Build Studio today is a set of pages (Workflows grid → Group page → Human Edit → Publish) with a floating copilot inside Human Edit. The redesign (`docs/artifacts/build-studio-redesign/`, 19 mockups) makes **one continuous conversation per workflow** the backbone: created → sign-in → record → compile → Clarify → (edit) → test → auto-fix → approve. All of it lives in a three-column shell: nav, conversation, and an inspector or embedded browser. Goal: a mixed-skill author never has to know which page to go to next; the conversation shows exactly what happened (cards reflect finished backend commands, never prose claims).

**On approval, step one is bookkeeping, not code:**
- copy this plan to `docs/Build-Studio-Conversation-Plan.md`;
- file the TODO items listed in §8;
- append the FIX.md entry.

Then Phase 1 starts, with a report at the end of each phase.

## 1. Decisions taken (with the user, 2026-10-18)

| Topic | Decision | Why |
|---|---|---|
| Thread storage | `data/workflows/<workflow_id>/thread.jsonl`; general chats in `data/chats/<chat_id>.jsonl` plus `data/chats/index.json` | A workflow exists from creation, but `skill_id` (`skill_{session_id}`) is born at compile and changes on every re-record. That folder already exists (`merged_group_state.json`). Fail-silent contract copied from `editor/copilot_sessions.py`. |
| Metering | **All** Studio LLM calls except compile — chat, new-workflow drafting, Clarify mapping, auto-fix, copilot, the sandbox's `ai_review` answers, retarget — use one class. The internal id `human_edit` is renamed to **`ai_credits`** everywhere, with a back-compat alias (CLOUD-25, §6). | User decision: one "AI credits" pool, one name. `execute_chat` stays a separate counter in the same pool, as today. |
| Irreversible steps | **Reuse the existing `consequence: "irreversible"` → `step.destructive`** (PROD-3). No new field. | Already compiled (`compiler/destructive_semantics.py::classify_consequence`), exported (`skill_package_builder_saved_skill.py:284`) and honoured by the runtime (`cascade.js`, `run.js` dry-run). Fix the known gap first: patching `consequence` doesn't re-sync `identity_bundle.destructive`, so a reviewer's flag change is ignored at runtime. |
| Confirm before an irreversible step | **Narrow `patch_gate` exemption:** a *confirm-only* `ai_review` (output schema exactly `{confirmed: boolean}`, `on_failure: abort`) may directly precede a destructive step. | Today `destructive_step_cannot_directly_follow_ai_review` blocks Clarify Q5. The rule exists so an answer never supplies *what* the step acts on, and a yes/no only gates *whether* it runs. Documented in TRD §11.3. |
| Design conflicts | **Amend `DESIGN.md` to match the mockups** in Phase 1: pink `#e88fb4` click/select chips; fourth "raised" surface `#2c2c2c`; slate `#8e8e8e` as the text floor (iron gray `#5a5a5a` disabled-only); in-progress `◐ #93b4e6`; selected = 7% neutral tint, no clay. | User decision. All five tighten DESIGN.md's own One Accent Rule and AA contrast. |
| Approval | `signed_off` becomes the only approval truth. **Any later skill write clears it.** The package build gate checks `signed_off` instead of `edited_at`. | Today `derive_workflow_stage` and `skill_package_builder.py:80` read `edited_at` as "approved", so any edit, including an automatic fix, looks approved. |
| Runtime pause for the test guard | App-layer change (no host change): caller-only arg `_pause_before_irreversible`, parked via the existing `_parks` primitive (§5). | Dry-run skips *every* destructive step and keeps going, so it can't do "pause, then run the last N for real". |
| Recording in the embedded view | **Spike in Phase 7. Expected outcome: recording keeps its separate Chromium window**; tests embed first, sign-in second. | The recorder needs `new_context` plus `record_video_dir`, context-level `expose_binding`/`add_init_script` and `storage_state`. Electron over CDP offers one default context and breaks `storageState()` (TRD §4.7, AUTH-20/21). The mockups already say "Recording in a Chromium window". |

## 2. Screen → backend map, and gaps

Legend: ✅ existing command reused · ➕ new · 🔧 change to existing.

| Screen / card | Backend | Gaps |
|---|---|---|
| Sidebar: groups, workflows, status glyphs, Publish badge, footer credits / connection | ✅ `list_groups`, `list_workflows` (+stage), `get_usage`, `whoami` | 🔧 `derive_workflow_stage` → lifecycle (§3.1). ➕ `count_publishable` (signed-off, unpublished). ➕ `lifecycle` event so glyphs update live. |
| Chats list / General Chat + draft card | — | ➕ `chat_list`, `chat_turn`, `chat_cancel`; read-only context (groups, workflow names, group apps). "Create and start recording" → ✅ `create_workflow` then the sign-in/record flow. |
| Group page (sign-ins "checked N ago", Check now, table + filters, rename/delete) | ✅ `get_group`, `check_group_app_auth`, `group_auth_status`, rename/delete | 🔧 `group_auth_status` returns `verified_at` (the probe TTL is already tracked). Filters derive from lifecycle; no schema change expected. If a field is needed it gets documented in Backend-Schema. |
| New workflow form (name, group, start URL, "already signed in" hint, task) | ✅ `create_workflow{name, group_id, target_url}` | 🔧 add optional `task_description` to `Workflow`, used by Clarify / chat context. ➕ `match_group_app(group_id, url)` for the inline sign-in hint (or derive client-side from `get_group`). |
| Sign-in in the conversation | ✅ `start_group_app_auth` / `finish_group_app_auth` / `cancel_group_app_auth` | Thread events only. |
| Live recording card (captured list, Pause/Resume, "Changes each run?", Finish/Discard, browser closed) | ✅ `start/stop/cancel_recording`, `get_recording_status` (`browser_open`, `ended_by_user`) | ➕ `recording_event` stream (plain-language row per accepted event). ➕ `pause_recording` / `resume_recording`. ➕ `set_recording_hint{session_id, sequence, varies}` → `data/sessions/<id>/hints.json` (events.jsonl unchanged). ➕ "save what was captured" after a browser close = ✅ `stop_recording`. |
| Compile progress card (real stages, credits, queued) | ✅ `compile` + `compile_step` / `compile_log` / `quota` events | ➕ single-slot compile queue + `compile_queued{position, ahead_workflow_id}`. 🔧 structured `compile_stage` events for the 5 mockup rows, with real `n of m` from the existing step_index/step_count. Second opinion gets a stage event instead of only `compile_log`. |
| Compile failed / out of credits | ✅ existing error codes (`EntitlementBlocked`, `CloudUnreachable`, credit refund) | Error cards only. |
| Clarify questions → receipts → summary card | — (BUILD-32) | ➕ `clarify_questions(workflow_id)` (deterministic generation, ranked by BUILD-25 confidence, cap 5, stored in `data/workflows/<id>/clarify.json`, **not** the skill doc). ➕ `clarify_answer{question_id, choice}` → existing mutations. ➕ `clarify_map_text` (one LLM call → a choice id or "unclear"). ➕ `workflow_summary` (plain steps, counts, done-when). |
| Human Edit (restyle) | ✅ all editor commands | Copilot panel → the workflow thread scoped to the selected step. Wizard labels: Element → How it's found → Validation. "Test changes" → the same test flow. |
| Test inputs (guard switch, "show browser") | ✅ `test_workflow{workflow_id, inputs, headless}` | 🔧 `guard: bool` payload (§5). |
| Test running (plain progress, Stop) | ✅ `workflow_test` log events, ✅ `cancel_test_workflow` | ➕ structured `test_step{index, name, status}` events (today only free-text logs). |
| Paused before an irreversible step | — | ➕ `test_guard_decision{run_id, decision: run_rest \| end_here}`; runtime change (§5). |
| Failed card + auto-fix loop + View change / Undo | ✅ `get_failure_evidence`, copilot diagnose, gates, `accept_copilot_proposal`, build+test (as `copilot_verify` does) | ➕ `autofix_start/stop/resume`. ➕ persistent change snapshots for Undo (`data/skills/<id>/changes/<change_id>.json`; Undo only when `meta.version` is unchanged since, else "changed since — open Human Edit"). 🔧 `edit_log.source = "auto_fix"` (existing `source` field; the prompt's `actor` maps to it). |
| Passed → Approve | ✅ `sign_off_workflow` | 🔧 approval semantics (§1). Approve never publishes, as today. |
| States (offline, engine stopped, …) | ✅ backend restart in `main.js`, error codes | ➕ connection-state event (cloud reachability ping); renderer banners. |
| Settings popup (Account / Usage / About; auto-fix toggle) | ✅ `get_usage`, `whoami`, updater IPC | ➕ local Studio setting `autofix_enabled` (default on). Meter label "AI credits". |
| First launch (checks: signed in, Chromium installed, runner ready) | ✅ deps check / bootstrap / `whoami` | Reuse; failing-check variant. |

## 3. Backend architecture (Python, `conxa_compile/conversation/`)

### 3.1 `lifecycle.py` — deterministic orchestrator
- `derive_lifecycle(workflow, skill, clarify, runtime_state) → {stage, substate, glyph, note, allowed_actions}`.
  - Stages: `describe | configure | record | compile | clarify | edit | test | approve`.
  - Side states from `States`: offline, engine stopped, compile failed, out of credits, queued, sign-in expired mid-test, test stopped, recording discarded, browser closed.
- **Inputs are existing data:** Workflow fields (`recording_status`, `compile_status`, `last_test_*`, `signed_off`, `group_id`) and skill `compile_report`.
- **Persisted only:** clarify answers (`clarify.json`) and auto-fix run state (thread cards).
- **In-memory only:** recording paused, compile queue, active test.
- Replaces `handlers/status.py::derive_workflow_stage` (same callers, extended return).
- Fixes the bug that `queued`/`compiling`/`recording` are never produced: the in-memory registries are passed in.
- Transitions happen only inside command handlers. After every lifecycle-relevant command the handler emits `{phase:"lifecycle", workflow_id, …}` and appends the matching `event`/`card` thread item.

### 3.2 `thread_store.py`
- Append-only JSONL. Item = `{id, ts, kind: user_message|assistant_message|event|card|summary|import_marker, …}`.
- Cards: `{card_id, card_type, state, refs:{skill_id, run_id, change_id, question_id…}, revision}`. A revision is a new line with the same `card_id`, and the reader folds to the latest.
- `load(thread_id, before=None, limit=N)` reads newest-first pages (tail-read, no full parse for big threads).
- Fail-silent on `OSError`, like `copilot_sessions.py`.
- One-time read-only import of `data/skills/<skill_id>/copilot_sessions.jsonl` (current skill), wrapped in an `import_marker`.
- Emits `{phase:"thread_item", thread_id, item}`.

### 3.3 `harness.py` — the turn loop (shared by chat and workflow threads)
1. **Route deterministically.** If a choice id is given, apply it via the card's action. If free text and a question card is open, use `clarify_map_text` → choice or "unclear, ask again", applied by the same code path as a click. Confirm/cancel of a pending action is handled the same way. Otherwise go to the model.
2. **Context pack** (`context_pack.py`, token budget configurable; default 12k workflow / 6k chat, estimated at chars/4), in priority order:
   1. system prompt (role, `build_capability_manifest()`, §1 rules in plain language, naming rules);
   2. lifecycle, open cards and their allowed actions;
   3. **pinned facts** (Clarify decisions, inputs, done-when checks, confirmation points, irreversible steps) — never dropped;
   4. `build_evidence_bundle(..., detail="digest")` plus the latest failure evidence;
   5. rolling summary;
   6. last N turns, trimmed oldest-first;
   7. `need[]` retrieval (`MAX_RETRIEVAL_ROUNDS`).
3. **Stream** via `stream_llm` → `{phase:"thread_delta", thread_id, turn_id, text}`. Edit proposals use the separate diagnose call as `llm/copilot.py` does today, then `gate_proposals` / `gate_overlay_proposals` / `gate_structural_proposals`. Each proposal becomes a "pending change" card (Apply / Undo).
4. Append to the thread. When the verbatim window overflows, one short `thread_summarize` call stores a `summary` item.
- **Concurrency:** a per-thread lock and queue, with `turn_id`. `chat_cancel{turn_id}` sets a cancel flag checked by the stream callback; a cancelled turn appends nothing (the delta buffer is discarded).
- **Prerequisite fix:** the LLM router is process-global (`conxa_core.llm.set_router`) and every RPC runs on its own thread, so a concurrent compile and chat would swap `usage_class` and event sinks. Move `usage_class` and the sink to a `contextvars` scope (`with llm_scope(usage_class, sink):`) read by `LLMProxyClient`. `_install_proxy_router` keeps working as a thin wrapper.
- `cmd_copilot_turn` stays as a compatibility shim over the harness until Phase 6 removes the floating panel.
- Error mapping reuses `handlers/copilot.py:80-90` and becomes error cards.

### 3.4 Auto-fix (`autofix.py`, Phase 5)
- Loop of at most 3 attempts per run:
  1. failure evidence + diagnose;
  2. take **one** gated proposal;
  3. snapshot → apply through `cmd_accept_copilot_proposal` with `source="auto_fix"`;
  4. rebuild + re-test **with the guard forced on**.
- Stops on: 3 failures, the user pressing Stop, no in-gate proposal, or the Settings toggle being off.
- Offers "Resume auto-fix" and "Fix it myself in Human Edit".
- The TRD gets a note beside §7.3 "Human Review is the gate": auto-fix applies without a per-change click; this is mitigated by the listing, Undo, `edit_log` source, the guard, and approval staying human and being revoked by any edit.

## 4. Recording changes (Phase 3, `recorder/session.py`)
- **Pause:**
  - A `_paused` flag checked at both enqueue points (`_binding_sink_sync`, `_enqueue_synthetic`), so paused payloads are dropped, not replayed.
  - On resume, reset bridge value baselines via `page.evaluate` on the pump thread (`lastEditableValueByElement`), so typing during the pause doesn't fold into the next event.
  - Video keeps running; the paused span is noted in `hints.json`.
- **Live rows:** `_consume_payload_sync` emits `{phase:"recording_event", workflow_id, sequence, kind, text, tab}` through the sink (stdout is lock-protected).
- **"Changes each run?":** stored in `hints.json` keyed by event `sequence`. The compiler reads it to pre-set `input_binding` through the **same parameterize path the second opinion already uses**, and Clarify skips those values. `events.jsonl` shape is untouched.

## 5. Test-safety guard (Phase 5) — runtime app-layer change

**Runtime** (`runtime/app/run.js`, `server.js`, `tool_defs.js`, `failure_response`-free):
- New caller-only top-level arg `_pause_before_irreversible: true`, documented "Studio only — do not set".
- In `executeOneStep`, before dispatch: if set, `step.destructive === true`, and not released → throw `{guardPause, stepIndex, remaining}`.
- `server.js` parks it exactly like `reviewPause` (same `_parks`, TTL, fingerprint). It returns `_meta["conxa/guard"] = {step_index, step_intent, remaining_irreversible}`.
- **Resume:** `resume_from: i` + `_guard_release: true` releases all later destructive steps in the run. **End here:** `cancel_execution{run_id}`, which discards the park; the Studio reports "passed up to *X*".
- Never touches `recovery_stage.js` or `retry_budget.js`, and is inert when the arg is absent, so customer runs and `gate_replay.js` are unchanged.
- **Version:** app-layer only → new `app-vX.Y.Z` tag. `MIN_HOST` is unchanged (no host API used). Runtime unit tests plus the gate replay run locally as CI would.

**Studio side:**
- `cmd_test_workflow` gains `guard: bool` (default true).
- It reads the staged app layer's `version.json`. **If the app layer predates the guard (unknown args are silently ignored), it falls back to `dry_run: true`** and the card says the last steps were previewed, not run. Without this, an old engine would send real data.
- **Confirm + guard merge:** in Studio tests a *confirm-only* `ai_review` is answered by the **person**, never the sandbox LLM, through the same guard card. If it immediately precedes the first destructive step, a "yes" resumes with `review_results` + `_guard_release` together ("you confirmed, which also let the 3 steps that can't be undone run").

## 6. CLOUD-25 — rename `human_edit` → `ai_credits` (ships before Phase 2)
- **Cloud:**
  - `ALLOWED_USAGE_CLASSES` accepts `ai_credits` and keeps `human_edit` as a permanent alias for installed Studios.
  - `_POOL_USAGE_CLASSES` and the `llm_proxy_routes.py` pre-flight branches (sync and stream twins) apply to both names.
  - New counters are written as `ai_credits_*`. Reads sum legacy `human_edit_*` and new keys, so there is no batch migration; legacy keys age out with the monthly row.
  - Plan limit and wallet key: `ai_usage_credits` becomes canonical (already dual-emitted in `current_entitlements`), with `human_edit_tokens` still emitted for old clients.
  - Error code `human_edit_pool_exceeded` gets an `ai_credits_exceeded` twin; the Studio maps both.
- **Studio:** every `usage_class="human_edit"` becomes `ai_credits` (copilot, visual ×3, retarget, ai_review answerer, harness). On `invalid_usage_class` it retries once with `human_edit`, for an older cloud.
- **Labels everywhere:** `cashfree_routes.py` plan text ("Human Edit tokens" → "AI credits"), the Studio Settings and footer meters, frontend billing, `EntitlementMeters.tsx`.
- **Docs:** Backend-Schema §5.5 (already stale: it omits `execute_chat`), TRD §13, cost_model.
- **Tests:** `test_entitlements.py`, `test_llm_proxy_and_publish.py`, `test_ai_review_studio_answer.py`.

## 7. Phases, invariants touched, verification

| Phase | Scope | Invariants touched → how kept | Verify |
|---|---|---|---|
| 1 Shell | See notes below the table | None (renderer only) | `npm run typecheck && npm run lint && npm test` in `conxa-builder/electron`; run the app and walk the sidebar, breakpoints, keys |
| 1.5 CLOUD-25 | §6 | `/api/v1` prefix unchanged | `pytest -q tests` from `conxa-cloud/` |
| 2 Thread + orchestrator + harness | See notes below the table | LLM never writes selectors → proposals go through the existing gates only. Cloud doesn't compile → harness in the Studio. | New pytest: context pack under budget (pinned survive, oldest trimmed, summary used); deterministic routing; proposals still gated; thread append/revise/reload; concurrent `llm_scope` isolation; approval revocation. Manual multi-turn chat survives an app restart. |
| 3 New workflow → sign-in → record | Conversation flow; separate recorder window kept; §4 | Iframe chain, tab ids, bounding boxes → pause gates enqueue only; `_finalize_payload_sync` untouched. Auth files local → hints file holds no values beyond what's already in events. | Record a real workflow end to end; diff `events.jsonl` shape vs. a pre-change recording; existing `test_cancel_recording.py`, `test_start_recording_session_gate.py` |
| 4 Compile card + Clarify (BUILD-32) + summary | Queue, stage events, questions first slice (typed value / upload: input vs fixed), then optional popup, done-when, confirm-before-irreversible, missing-item behaviour, row choice | Selectors: the row question offers only `IdentityBundle` / `selector_grammar` candidates. Assertions: text/URL/state types only, `required` only when ticked. `try_dismiss` via `confirm_optional_interstitial` → `build_try_dismiss_from_hint`. Byte-identical: questions live in `clarify.json`, not the skill doc. | Unit: unanswered → skill bytes identical; each answer changes only allowed fields; patch_gate exemption accepts confirm-only and rejects other schemas. Real compile in the app. |
| 5 Test inputs/running/failed/auto-fix/passed/approve | §3.4, §5 | Tier A zero-token → auto-fix runs after failure, outside the cascade. Approval human-only. Tests never cross irreversible steps unasked. | Real sandbox run pauses before an irreversible step; End-here → "passed up to"; auto-fix stops at 3; Undo reverts an automatic change; Approve never publishes; old-app-layer fallback uses dry_run; `runtime/test` + gate replay |
| 6 Human Edit restyle | Wizard labels, Test changes, Back to conversation, pink chips, step names, copilot → thread | None (UI) | typecheck/lint/test; manual edit + undo |
| 7 Embedded browser | See notes below the table | Iframe/tab/`frame_enter` rules → recording only moves if the output is identical | Spike report; runtime unit tests + gate replay if the runtime changes |
| 8 States, offline, engine stopped, Settings, first launch; docs | Banners/cards per `States`; Settings popup with auto-fix toggle; failing-check first launch | — | Manual: kill the backend, go offline, exhaust credits (mock) |

**Phase 1 scope**
- Sidebar (groups-first, Chats, Publish badge, Ctrl K search, footer), inspector, resizable panels with remembered widths (localStorage, try/catch), breakpoints and keyboard map from `Panels`.
- Tokens in `globals.css`, plus a DESIGN.md amendment.
- `lib/status.ts`: one home for glyph/colour and step-type colours.
- Routes `/chat/:id?`, `/groups/:id`, `/w/:workflowId`, `/edit/:skillId`, `/publish`; Settings as a dialog.
- Old pages render inside the new shell until replaced. Inter replaces `system-ui`.

**Phase 2 scope**
- §3.1–3.3: the `llm_scope` fix, approval fix, `consequence` → `destructive` sync, new RPCs and events.
- Renderer `components/conversation/*` (EventRow, Message, card registry), `threadStore`, streaming, skeleton loading.
- General Chat. Human Edit copilot moved onto the thread.

**Phase 7 scope**
- **Spike:** a fixture page with an iframe, a popup and typing, recorded via Playwright-launched Chromium and via an Electron `WebContentsView` attached over CDP; diff the normalized events.
- Then tests: Studio `main.js` sets `CONXA_HOST_BROWSER_CDP` / `CONTROL_URL` before spawning the backend; `runtime_tool.py` cache key includes those env vars; a port of `browser_panel.js` / `browser_control.js`.
- Then sign-in, saving cookies via a page CDP session.
- Recording only if the spike output is identical; otherwise documented "why not".

At the end of every phase I report what changed, what was verified and how, what's left, and anything unsure. Each phase also gets a FIX.md entry, doc updates, and its TODO item moved to Done.md.

Renderer has no component-test framework. Pure logic (card fold/reducer, lifecycle → glyph, panel-width clamping) goes in plain `.ts` modules tested with `node:test`, like the existing `test/releaseState.test.mjs`. No new test framework.

## 8. TODO.md entries to file (on approval)
- **BUILD-36** Shell (Phase 1)
- **BUILD-37** Thread store + lifecycle + harness (Phase 2; includes the `llm_scope` router fix, approval-revocation fix, `consequence` → `destructive` sync)
- **BUILD-38** Conversation recording: pause, hints, live rows (Phase 3)
- **BUILD-32** existing, plus **BUILD-39** compile card/queue + summary card (Phase 4)
- **BUILD-40** Test guard + auto-fix + approve flow (Phase 5)
- **RT-next** `_pause_before_irreversible` app-layer pause
- **BUILD-41** Human Edit restyle (Phase 6)
- **BUILD-42** Embedded browser panel + spike (Phase 7)
- **BUILD-43** States / Settings / first launch (Phase 8)
- **CLOUD-25** `human_edit` → `ai_credits` rename
- **Small open items found while mapping:**
  - `get_step_screenshot` never attaches the image to the model;
  - one overlay accept makes several undo entries;
  - Backend-Schema §5.5 is stale;
  - `Done.md` has duplicate ids BUILD-31 and BUILD-33;
  - possible double bounding-box offset for relayed child-frame events (unverified).

## 9. Docs (Phase 8, plus per phase as touched)
- **TRD:** harness, lifecycle, thread store, embedded path, guard (§11.3 exemption plus the new pause), auto-fix trade-off next to §7.3.
- **App-Flow:** new end-to-end flow.
- **UI-UX-Brief:** every screen.
- **Backend-Schema:** RPCs, events, `Workflow.task_description`, `clarify.json`, `hints.json`, `ai_credits`.
- **Implementation-Plan:** status.
- **DESIGN.md:** the five amendments.
- PRD untouched.

## 10. Unsure / to confirm during the work
- Clarify "stop if the customer is missing" needs an existing insertable kind that fails the run when an item is absent. If the capability manifest has none, that question is dropped rather than adding a new runtime step kind.
- Inspector "Package" row semantics come from the Publish page data. To confirm in Phase 1.
- Group "checked N ago" needs `verified_at` exposed. If it's only in-memory today, it gets persisted beside the app session (documented).
- Real sign-in and real irreversible-step tests need a site the user controls. I'll say so plainly where I can't verify.
