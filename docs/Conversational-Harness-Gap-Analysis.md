# Conversational Harness — Gap Analysis

**Question:** what still stands between today's Build Studio and a conversation-only experience where a person describes a task, records when needed, answers questions, refines, and tests — producing a deterministic, reusable workflow that handles dynamic pages, branching, authentication, popups, and variable inputs?

**Date:** 2026-10-24 · **Branch audited:** `feat/conversation-led-studio` (working tree, including uncommitted changes) · **Scope:** read-only audit. No code was changed.

---

## 0. Summary

The conversational backbone **already exists**, and most of the brief describes it. The conversation-led redesign (BUILD-36 → BUILD-46, all in `Done.md`) shipped:

- a thread per workflow,
- a deterministic lifecycle,
- a chat harness with gated edit proposals,
- in-panel recording with pause/resume and "changes each run?" hints,
- a compile card with a queue,
- Clarify questions,
- a guarded test with auto-fix, and approval.

Don't rebuild any of it. The gaps sit in five places:

1. **None of the shipped loop has run end to end on a real site.** Every phase entry in `Done.md` says "Not exercised" for the live path. The runtime half of the test guard has never met the Studio half in one run, and it needs an `app-v` release.
2. **The conversation can answer cards, but free text can't drive it.** Typing "record that again", "test it with Acme" or "approve it" gets a reply in prose and nothing else happens. The lifecycle already computes `allowed_actions`, but nothing reads them.
3. **The task description is collected and then ignored.** It reaches no compile, no Clarify question and no workflow-chat context. So nothing checks that the recording actually does what the person said.
4. **A workflow can't return data.** There is no deterministic "read this value" step, and `execute_skill` returns no outputs. For an agent-called skill this is a basic contract gap.
5. **Branching is popup-only.** The compiler only produces `try_dismiss` and `outcome_fork`. A second recording ("variant") can only teach the workflow about a popup. Every other difference is reported and thrown away. There is also no step that runs or skips based on an input's value, and no way to re-record just one part of a workflow.

Everything else on the list is a known, filed residual, mostly in `TODO.md`: determinism of the second opinion, Clarify coverage, auth and popup edge cases.

---

## Implementation status (2026-10-24)

This audit has been worked through on branch `feat/conversation-led-studio`. What is built, tested and documented in `docs/TRD.md` (§ "Understanding check", "Conversation drives the flow", "Compile accounting and determinism", "Reading values back…", "Refining one part…", "Test-loop resilience"), and what is deliberately left:

| Gap | State | Where the rest lives |
|---|---|---|
| G1 live golden path | **Runtime half built and in CI** (`runtime/test/e2e/golden_path.js` over `runtime/test/fixture-site`); manual signed-in checklist in `docs/Live-Verification-Checklist.md`. The `app-v` release is yours to cut. | — |
| G2 free text drives the flow | **Built** (action table, constrained router, confirm cards, action strip) | — |
| G3 task description | **Built**: pinned fact, second-opinion context, understanding check (coverage + which values change). Not built: pre-record plan card, test → Clarify follow-up | BUILD-49 |
| G4 returning values | **Built**: capture gesture, `extract` step, `outputs` in the result and on the test card. Not built: values across a pause, `outputSchema`, Human Edit form | BUILD-51 |
| G5 branching | **Built**: `run_if`, `on_missing: "skip"`. Not built: general variant branch learner, "ask a person" mode | BUILD-51, BUILD-53 |
| G6 re-record one part | **Built**: `_stop_before_step`, segment compile, `replace_step_range`, `cmd_splice_segment`, stale answers. **Not built**: handing the parked page to the recorder (needs a live Electron pass) | BUILD-52 |
| G7 Clarify coverage | **Built**: choice/date controls, no dropped questions (queued). Not built: relative dates | BUILD-50 |
| G8 determinism | **Built**: drop ledger, unused-input pruning, second-opinion pin, download-order fix. Not built: popup-tab race, cache key | BUILD-50 |
| G9 General Chat context | **Built** (workflows, groups' apps, app maps; open-workflow routing) | — |
| G10 vision | **Built** for the failure screenshot (diagnosis + card). Point-to-anchor in the panel not built | — |
| G11 test-loop resilience | **Built**: sign in and carry on, interrupted tests, confirmation can't be loosened. Not built: guard inside a loop, durable pause state | BUILD-53 |

---

## 1. Method and confidence legend

| Tag | Meaning |
|---|---|
| **[V]** | **Verified.** I read the code at the reference given. |
| **[D]** | **Doc-reported.** Stated in `Done.md` / `TODO.md` / `docs/`. I did not re-verify it in code. |
| **[A]** | **Assumption or inference.** Not confirmed. Check it before acting on it. |

Areas read in full or in part:

- `conxa_compile/conversation/` (harness, lifecycle, clarify, context_pack)
- `handlers/{conversation,workflow_flow,compile_flow,test_flow}.py`
- `editor/capability_manifest.py`, `editor/action_registry.py`
- `compiler/{variant_align,input_binding}.py`
- `runtime/app/{run.js,server.js,tool_defs.js}` (searched with grep)
- renderer `pages/WorkflowConversationPage.tsx`, `components/conversation/cards/*` (grep)
- `docs/Build-Studio-Conversation-Plan.md`, `Done.md` BUILD-36..46, open `TODO.md` items

Some parts were **not** audited line by line: the recorder's `bridge.js`, `resolver.js` scoring, the `cascade.js` internals, and the cloud side. For those I rely on the `TRD.md` and `TODO.md` descriptions.

---

## 2. What already exists and works (don't rebuild)

| Capability | Where | Status |
|---|---|---|
| One thread per workflow + general chats, append-only, card revisions, rolling summary | `conversation/thread_store.py`, `harness.py:459` `_summarize` | [V] built; unit-tested [D] |
| Deterministic lifecycle (describe → configure → record → compile → clarify → edit → test → approve) derived from existing data, never set by a model | `conversation/lifecycle.py:31` `derive_lifecycle` | [V] |
| Turn loop: card answers routed deterministically; free text mapped to a card choice by a constrained LLM call ("unclear" when unsure) | `harness.py:250-285`, `map_free_text_to_choice` `:391` | [V] |
| Edit proposals always pass the existing gates before becoming Apply/Undo cards; the model never writes selectors | `harness.py:331-337`, `handlers/conversation.py:228` `gate` | [V] |
| Context pack under a token budget with pinned facts (Clarify answers, irreversible steps, review points, inputs) | `context_pack.py`, `harness.py:115` `pinned_facts` | [V] |
| Per-context LLM scope (usage class + sink) so a concurrent compile and chat don't swap meters | BUILD-37 | [D] |
| General Chat → draft-workflow card → Create & record | `harness.py:435` `_maybe_draft`, `conversation.py:337` | [V] |
| In-panel recording + sign-in (screencast video, per-page bridge, CDP cookie capture), pause/resume, live plain-language rows, "changes each run?" hints | BUILD-46 / BUILD-38, `workflow_flow.py:326-502` | [V] commands exist; live run not exercised [D] |
| Compile card with real stages, single-slot queue, out-of-credits / failed / deferred states, refund on infra failure | `compile_flow.py:51-133` | [V] |
| Clarify: upload (ask vs. fixed), confirm before an irreversible step, typed values → inputs, recorder-flagged popups, outcome-fork policy, known popups from the app map, "how do we know it worked" | `clarify.py:140-277` | [V] |
| Variant recording for an outcome fork (the "no popup" case), compared by rule with no compile and no credit | `workflow_flow.py:389-468`, `compiler/variant_align.py` | [V] |
| Test guard (pauses before irreversible steps), auto-fix loop (≤3 attempts, gated, Undo, `source="auto_fix"`), human-only Approve, `signed_off` revoked by any later write | `handlers/test_flow.py`, BUILD-40 | [V] commands; runtime half unit-tested only [D] |
| Element identity: IdentityBundle + deterministic selector grammar, uniqueness-margin resolver, zero-token Tier A recovery, Tier B agent handoff | `compiler/identity_bundle.py`, `runtime/app/resolver.js`, `cascade.js` | [D] (TRD) |
| Branch and loop primitives in the runtime: `if_present`, `try_dismiss`, `wait_for_one_of`, `outcome_fork`, `for_each` | `editor/capability_manifest.py:79-86`, `runtime/app/run.js:98` | [V] |
| Human checkpoints: `ai_review` (reasoning pause), `handover` (2FA/CAPTCHA) | `capability_manifest.py:64-73` | [V] |
| Popups: known-pattern dismissal, learned dismissals, decision forks that fail closed to a person, app map | `runtime/app/{dismiss_patterns,learned_dismissals,fork,app_knowledge}.js` | [D] |
| Drift detection before step 0 | `runtime/app/drift.js` | [D] |

---

## 3. Gaps, by priority

### P0: blocks the core promise

#### G1. Nobody has run the loop end to end
- **Evidence [D]:** each conversation phase in `Done.md` records the live path as unexercised:
  - BUILD-37: "Not yet run against a live model".
  - BUILD-38: "Not exercised: a real sign-in and a real recording against a live site".
  - BUILD-39: "Not exercised: a real compile against the cloud".
  - BUILD-40: "the two have not yet met in one live run. Requires a new `app-vX.Y.Z` release".
  - BUILD-43 and BUILD-46: "Not exercised… in a running, signed-in Studio".
  - Tracked as BUILD-45(e) and BUILD-48(1).
- **Why it matters:** the guard, the in-panel recorder, Clarify and auto-fix are each correct in isolation. A seam problem between them, like BUILD-48 item 6 (a slow download hand-off lost a popup click), only shows up in a full run.
- **Needed:**
  1. Cut the `app-v` release that carries `_pause_before_irreversible`.
  2. Add one scripted golden-path test against a fixture site the team controls: sign-in → record (with an iframe, a popup, a download and an irreversible submit) → compile → answer Clarify → guarded test → auto-fix on an injected failure → approve.
  3. Run it in CI next to `gate_replay.js`.
- **Size:** M.

#### G2. Free text can't drive the workflow
- **Evidence [V]:**
  - `harness.run_turn` has only three outcomes: (a) an answer to an open card (`harness.py:251-285`), (b) a streamed prose reply plus optional gated edit proposals (`:323-356`), and (c) in General Chat only, a draft-workflow card (`:340-342`).
  - No path turns "record it again", "compile again", "test it with customer = Acme", "stop the test", "approve" or "sign in again" into the matching command.
  - `derive_lifecycle` computes `allowed_actions` (`lifecycle.py:120-127`). In the renderer, `allowed_actions` appears only as a type declaration (`api/conversationApi.ts:12`). Nothing reads it.
  - `WorkflowConversationPage.tsx:94-103` hard-codes a single tail card, and only for the record/configure stages. `flowCompile` is exported (`conversationApi.ts:110`) but never called from a component. Retrying a compile goes through the card's own `retry` action.
- **Why it matters:** this is the difference between "a chat with buttons" and "the whole experience happens through conversation". Today the person still has to find the right card or button for every step.
- **Needed (reuses existing pieces, no new LLM authority):**
  - Add an **intent router step** before the prose model. It maps free text to *one of the lifecycle's current `allowed_actions`*, using the same constrained-choice pattern as `map_free_text_to_choice`: an id not in the allowed set counts as "unclear".
  - Run the chosen action through the existing `flow_*` command.
  - Show a confirm card for anything with side effects: recording, testing (which sends real data past the guard), approving.
  - Pull slot values like test inputs into the existing `TestInputsCard` pre-filled, never straight into a run.
  - In the renderer, show `allowed_actions` as a persistent action strip so every stage has a visible next step, not just record/configure.
- **Size:** M.

#### G3. The task description never informs understanding
- **Evidence [V]:**
  - `Workflow.task_description` exists (`packages/conxa-core/conxa_core/models/workflow.py:77`). It is written at create (`handlers/workflows.py:347`, `conversation.py:363`).
  - Nothing reads it. `_flow_compile` calls `cmd_compile` with only `session_id` + `workflow_id` (`compile_flow.py:98`).
  - The workflow-thread context passes `{"id", "name"}` only (`conversation.py:245`). `clarify.generate_questions` works only from the compiled document (`clarify.py:140`).
- **Consequences:**
  - (a) Nobody checks that the recording covers the described task, for example "send the invoice" when the recording stopped at "save draft".
  - (b) The second opinion names inputs and phases without knowing the stated goal.
  - (c) The chat model in a workflow thread doesn't know what the workflow is *for*.
  - (d) There's no "here's what I'll ask you to do" plan before recording, which is the Claude-Desktop-like part of the brief.
- **Needed:**
  1. Put `task_description` in the context pack as a pinned fact. Small change.
  2. Pass it into the second-opinion payload as *meaning-only* context. This keeps the invariant: that pass never touches targets or selectors.
  3. Add a **coverage check** after compile: one LLM call compares the intent graph and final assertions with the description. Its only output is a Clarify question, for example "You said 'send'. The recording ends at 'Save draft'. Record the rest, or is that right?", answered through existing actions such as re-record or segment record (G6).
  4. Add an optional **pre-record plan card** from the description (expected pages, inputs to watch for, sign-in needed). It is advisory and never compiled.
- **Size:** S for (1)–(2), M for (3)–(4).

#### G4. A workflow can't return what it found
- **Evidence [V]:**
  - The action set (`editor/action_registry.py:16-85`) has no extract/read step. `screenshot` is "capture-and-discard — produces no artifact a later step can read" (`capability_manifest.py:63`).
  - The only thing that produces a value is `ai_review`, which binds an LLM answer into `inputs` (`:64-67`).
  - A grep of `runtime/app/server.js` and `tool_defs.js` finds no `outputs` / `output_name` / `structuredContent` in the `execute_skill` result.
- **Why it matters:** reusable workflows called by agents need results: "create the ticket and give me its ID", "look up the order status". Without results, every read-back is either an LLM call (not deterministic, costs tokens) or impossible.
- **Needed:**
  - (a) A recorder "capture this value" gesture (an extract mode in `bridge.js`). It records a target with a full IdentityBundle, like any click.
  - (b) An `extract` step kind with deterministic resolution: text, attribute or input value, plus an optional regex.
  - (c) An `outputs` schema on the SkillPackage, returned in `execute_skill`'s result and readable by later steps as `{{var}}`.
  - (d) A Clarify question: "Should the run report this value back?".
  - This keeps "LLM never writes selectors" intact, because the target comes from the recording.
- **Size:** L (recorder + compiler + runtime + package schema). Check `docs/Backend-Schema.md` before scoping.

### P1: needed for "handles branching and variable inputs reliably"

#### G5. Branching only comes from popups
- **Evidence [V]:**
  - The compiler references `if_present`/`wait_for_one_of` only in `compiler/action_policy.py`. The branches it actually emits come from `optional_hint` (`try_dismiss`) and decision dialogs (`outcome_fork`).
  - Variant recordings are deliberately fork-only. `variant_align.py`'s docstring: "Anything else that differs -> `unexplained` — reported to the author, NEVER merged". `cmd_apply_variant` needs a `fork_step_key` (`workflow_flow.py:404`).
  - No step runs or skips based on an input's value. Grepping `runtime/app` for `skip_if|run_if|if_input|optional_input` finds nothing.
  - BUILD-32(c), "if the item is missing", is open [D].
- **Missing cases:**
  - "If the customer exists, update it; otherwise create it."
  - "If an optional input is empty, skip that field."
  - "If the search returns no rows, stop and report."
- **Needed (the principle stays: branches come only from observed states plus human confirmation):**
  1. **Generalize variant takes.** When two recordings split at step k and rejoin at step m, offer a `wait_for_one_of` whose options are the two recorded bodies. Each option's probe comes from the IdentityBundle of the first step that differs. The person confirms in the conversation. This reuses the `variant_align` LCS and the existing `BRANCH_ALLOWED_KEYS` gate.
  2. Add an **input-conditional primitive** (`if_input` / `skip_when_empty`) that branches on the inputs map only, never on the page. It is cheap and fully deterministic.
  3. Add a Clarify kind for "what if X isn't there": stop / skip / ask a person, built on `if_present` + `handover`.
- **Size:** M for (2)–(3), L for (1).

#### G6. You can't re-record just one part
- **Evidence:**
  - [V] The only re-record is a full one: `re_record` exists only as a lifecycle action label (`lifecycle.py:86`). Grepping the builder for `splice|append_record|record_from_step` finds only unrelated editor list splicing.
  - [D] PROD-11's "fast diff-based re-record" is open.
- **Why it matters:** the natural refinement in a conversation is "step 7 is wrong, let me show you", and today that throws away the whole recording plus every Clarify answer.
- **Needed:** a **segment recording** flow.
  1. Replay the compiled workflow up to step N−1 in the panel. The runtime already supports `resume_from` and parks.
  2. Hand the same page to the recorder (embedded mode already adopts panel pages: `RecordingSession.embedded`).
  3. Compile only the segment.
  4. Splice it in with the existing structural ops through the gates.
  5. Keep the other steps' `step_key`s and Clarify answers.
- **Size:** L. BUILD-1 (CIR) would make the splice cleaner but isn't required.

#### G7. Clarify misses most kinds of variable input
- **Evidence [V]:**
  - The "values" question covers only `type`/`fill` steps and skips anything with `handler_hints.control_kind` (`clarify.py:203`). Dropdowns, radios, checkboxes and dates are never asked about, even though `input_binding.py` can bind a `<select>` (`:176`).
  - The date question (fixed / today / ask each run) and the row question are open (BUILD-32 a/b [D]).
  - `MAX_QUESTIONS = 5` (`clarify.py:31`) and `out[:MAX_QUESTIONS]` (`:277`) drop every question past the fifth. [A] I found no follow-up path that asks the dropped ones later.
  - No derived values exist (today + 7 days, "last month").
  - Templatizer defects are open: BUILD-22 (an unrelated placeholder rewrote a selector) and BUILD-24 (a react-select step dropped while its input stays required) [D].
- **Needed:**
  - Extend the values question to choice and date controls. Answers already map through `cmd_update_workflow_inputs` / `cmd_patch_step`.
  - Add BUILD-32(b) dates with relative-date tokens resolved by the runtime's interpolation (`runtime/app/interpolate.js`). [A] Check whether it supports expressions.
  - Keep a "more questions" queue so nothing is silently dropped.
  - Fix BUILD-22 and BUILD-24 before widening coverage.
- **Size:** M.

#### G8. Determinism of compile output
- **Evidence [D]:**
  - The BUILD-25 second opinion means "two compiles of the same recording can differ" (`TODO.md:1062`). The content-hash cache keeps them stable only while it hits.
  - BUILD-1 (pinned and reproducible compiles) is open.
  - BUILD-47: recorder event ordering races mean "recordings of the same actions compile differently from run to run", and a download can attach to the wrong click.
  - BUILD-23: a failed compile drops steps silently.
- **Why it matters:** the USP is *deterministic* workflows. Replay is deterministic once a package exists. Package *generation* is not.
- **Needed:**
  1. Fix BUILD-47 first. It is a correctness bug, not a nice-to-have.
  2. Fix BUILD-23 so dropped steps are surfaced as warnings and Clarify questions.
  3. Persist the second-opinion findings that were applied as a sidecar keyed by recording hash, and **replay them** on recompile instead of re-asking the model. Recompiles then become deterministic without the full CIR.
  4. Keep BUILD-1 for later.
- **Size:** M for (1)–(3).

#### G9. General Chat knows almost nothing
- **Evidence [V]:** `_thread_context` for chats returns only `group_names` (`conversation.py:200-203`). The plan (`Build-Studio-Conversation-Plan.md` §2) called for "groups, workflow names, group apps". General Chat therefore can't answer "which of my workflows touch Salesforce?", can't route "fix my invoice workflow" to that workflow's thread, and can't use the app map.
- **Needed:** add workflow names + lifecycle notes + group apps (read-only) to the chat context. Add a "go to / act on workflow X" card. G2's router can then hand off into that workflow's thread.
- **Size:** S.

#### G10. The conversation can't see the page
- **Evidence [D]:**
  - BUILD-44: `get_step_screenshot` "returns a path that is pasted into the prompt but the image is never attached".
  - The failure card can't show the page at the moment of failure, because `conxa-asset://` doesn't serve the sandbox folder.
  - [V] The harness sends text only (`stream_llm("studio_chat_reply", {"user_text": …})`, `harness.py:324`).
- **Why it matters:** "that button" or "why did it fail here?" needs vision. Diagnosis and auto-fix are working from text alone.
- **Needed:**
  - Attach step and failure screenshots to the diagnose call. The vision proxy route already exists (`/api/v1/llm/proxy/vision`).
  - Serve sandbox evidence through `conxa-asset://`.
  - Let the person click a point in the panel to anchor a message to a step.
- **Size:** M.

#### G11. Test-loop resilience
- **Evidence [D]:**
  - A test paused by an expired sign-in doesn't resume by itself (BUILD-45a).
  - The guard can't pause inside a `for_each` body, so it fails the test instead (BUILD-44).
  - Pause decisions live only in memory: restarting Studio mid-pause loses them (BUILD-44).
  - A confirm-only review can be edited into a free-form one without the gate re-checking (BUILD-44).
  - Durable resumable runs are open (EXEC-22).
- **Needed:** close BUILD-45(a), persist the guard/park decision beside the run, and re-run the gate on review-schema edits. EXEC-22 can wait for the customer-side story.
- **Size:** S–M each.

### P2: robustness residuals already filed (finish them, don't redesign)

| Area | Open items | Note |
|---|---|---|
| Popups | EXEC-44 #1 native `alert/confirm` forks, #2 late popups after the watch window, #3 insertable forks; EXEC-25 `beforeunload`; EXEC-26 native dialogs in the auth-capture browser | The safe default is already there (fail closed to a person); these reduce interruptions |
| Auth | BUILD-48 (live in-panel sign-in pass, localStorage of a closed tab), AUTH-10, AUTH-17, RT-10 (handover banner leaks to the whole host browser), PROD-4 vendor-session half | Group sign-in, live checks, mid-test expiry and handover exist |
| Dynamic pages | EXEC-28 (renamed accessible name can't heal), EXEC-29a (hover-reveal), EXEC-27 (unbounded branch polling), PROD-2 (recordability score) | [V] `wait` is clamped to 1000 ms (`capability_manifest.py:62`) and assertion types outside the eight listed silently pass (`:104-105`). Both are worth surfacing in the editor so authors aren't misled |
| Loops | No for_each / try_dismiss / wait_for_one_of authoring UI (those backlog items were dropped when Studio went conversation-first) | "Do this for each row in my CSV" has no conversational route at all; fits naturally after G2 |
| Skill contract | BUILD-27 (name/description/input prose never reviewed), BUILD-28 (drift signal has no response path) | Directly affects whether an agent picks and fills the reusable workflow correctly |

### P3: polish

- BUILD-45(b): queue messages while offline instead of disabling the composer.
- BUILD-45(c): retire the legacy Approve button and the old group-page record/compile/test rows, which now duplicate the conversation.
- BUILD-32(e): "Change" on an answered question should re-ask instead of opening Human Edit.
- BUILD-44: one overlay accept leaves several undo entries.

---

## 4. Architecture: what to add

Everything below plugs into existing seams. Nothing replaces a working component.

```
 person ──text──▶ ┌──────────────────────────── harness.run_turn ───────────────────────────┐
                  │ 1 open card?  → map_free_text_to_choice             (exists)            │
                  │ 2 NEW intent router → one of lifecycle.allowed_actions → flow_* cmd     │  G2
                  │      (constrained choice, confirm card for side effects)                │
                  │ 3 prose reply + gated proposals (+ NEW screenshots on diagnose)  (exists)│  G10
                  └──────────────────────────────────────────────────────────────────────────┘
                           │ context_pack + NEW pinned task_description (G3)
                           ▼
 describe ─▶ NEW plan card ─▶ record (panel) ─▶ compile ─▶ NEW coverage check ─▶ Clarify ─▶ test ─▶ approve
              (advisory)        │   ▲                      (task vs. intent graph,  │ + choice/date
                                │   └── NEW segment record   asks via Clarify)      │   questions (G7)
                                │       from step N (G6)                            │ + "if missing" (G5)
                                └── NEW "capture value" gesture ─▶ extract step + outputs (G4)
                                └── variant take ─▶ NEW general branch learner (G5) ─▶ wait_for_one_of
 compile determinism: NEW applied-findings sidecar replayed on recompile; BUILD-47/23 fixed first (G8)
```

| New component | Plugs into | Keeps which invariant |
|---|---|---|
| Intent router (lifecycle-constrained) | `harness.py` step 1.5; `lifecycle.allowed_actions` | Cards still reflect only finished backend commands; the model picks only from what the lifecycle allows |
| Task spec in context + coverage check | `context_pack.py`, second-opinion payload, `clarify.py` new kind | Second opinion stays meaning-only; nothing auto-applies |
| Extract step + `outputs` | `bridge.js`, `action_registry.py`, `patch_gate.py`, `run.js`, `server.js` result, Backend-Schema | Target from IdentityBundle; no LLM selectors |
| Segment recorder + splice | runtime `resume_from` park → `RecordingSession.embedded` → structural ops | Iframe/tab chain preserved; edits through gates |
| General branch learner | `variant_align.py` LCS → `wait_for_one_of` | Branches only from observed states + human confirmation |
| Input-conditional primitive | `run.js` dispatch, `patch_gate.py` | Deterministic; never page-guessing |
| Second-opinion replay sidecar | `compiler/second_opinion.py` cache | Recompile byte-stable for the same recording |

---

## 5. Suggested order

1. **G1** live golden path plus the `app-v` release. Nothing else is trustworthy until this passes.
2. **G2** intent router, **G3**(1–2) task description in context, **G9** chat context. Together these make the experience actually conversational, at a cost of a few days.
3. **G8**(1–3) determinism fixes and **G7** Clarify coverage. These protect the "deterministic" claim.
4. **G4** extract/outputs and **G5**(2–3) input-conditional and missing-item handling. These make workflows reusable for agents.
5. **G6** segment re-record, **G3**(3–4) coverage check and plan card, **G10** vision in the conversation.
6. **G5**(1) the general branch learner, then the P2 residuals.

---

## 6. Assumptions to check before acting

- [A] Questions that Clarify drops past the cap of five are never asked later. I found no follow-up path, but I didn't trace every caller of `clarify_store`.
- [A] `runtime/app/interpolate.js` can't evaluate relative-date expressions. I didn't open the file.
- [A] The `execute_skill` result shape: I relied on a grep that found no `outputs` in `server.js`/`tool_defs.js`. Read `failure_response.js` and the success path to confirm nothing else carries values back.
- [A] Segment recording assumes a parked runtime page can be handed to the recorder's embedded mode. Both sides run on Electron panel views, so it's plausible, but nobody has tried it.
- [D] Everything tagged [D] comes from `Done.md` / `TODO.md` / `docs/` and was not re-checked against the code in this audit.
