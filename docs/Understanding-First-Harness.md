# Understanding-First Conversational Harness

**Status:** design proposal, nothing built yet (2026-10-26)
**Scope:** Build Studio's conversation-led flow, from the moment a recording stops until a workflow is tested.
**Related:** [`TRD.md`](TRD.md) (conversation-led studio section, "Understanding check"), [`Conversational-Harness-Gap-Analysis.md`](Conversational-Harness-Gap-Analysis.md), `TODO.md` BUILD-32 / BUILD-55 / ADV-1, [`../research-analysis/07-go-to-market/twelvelabs-video-strategy.md`](../research-analysis/07-go-to-market/twelvelabs-video-strategy.md).

## Goal

CONXA must work through four stages in order before it tests anything:

1. **Understand** the recording.
2. **Ask** clarifying questions.
3. **Incorporate** the answers.
4. **Create** the workflow.

Video analysis is one source of evidence for that understanding. It is never a complete or infallible record of the workflow.

Decisions already taken:

- Uploading the recording video to a third-party video model is **opt-in per workspace**. It is off by default.
- This document is the design. Phase 1 is specified closely enough to build. Phases 2 and 3 are outlined.

---

## 1. Can TwelveLabs understand a browser recording?

These are the verified facts as of 2026-10-26.

| Item | Finding |
|---|---|
| Models | **Pegasus 1.6** (`pegasus1.6`) is current. It turns video into text and adds in-segment events. **Pegasus 1.5** added async segmentation. **Pegasus 1.2** is legacy and does general analysis only. **Marengo 3.0** does embeddings and search, not analysis. |
| Timestamped output | Async `/analyze/tasks` with `analysis_mode: "time_based_metadata"` and `response_format: {type: "segment_definitions"}`. You define the segments: each has an `id`, a `description` and `fields`, which can be enums. Output is non-overlapping `{start_time, end_time, metadata}` segments. |
| Timestamp precision | Doc examples show **whole seconds**. `min_segment_duration` appears as 2–10 s. The frame-sampling rate is undocumented; a third-party AWS tutorial estimates ~1 fps for Pegasus 1.2. |
| Input limits | 4 s minimum, up to 2 h, at most 2 GB, 360p to 5K. Video can arrive as an asset, a URL or base64. |
| Cost (Developer tier) | About $0.021–0.029 per minute of input video, plus $0.0075 per 1K output tokens. A 1-minute recording costs roughly $0.03–0.05. |
| Trust | SOC 2 Type II. Shared cloud, dedicated cloud, VPC, on-prem and Amazon Bedrock are all available. |
| Screen recordings / UI | TwelveLabs publishes nothing specific. Its OCR claim is 89.2% on TextCaps. Research on video of user interfaces is weak. On the Act2Cap benchmark (GUI Action Narrator), GPT-4o narrated basic GUI actions with only 19.5% accuracy, and did better only when given the correct keyframes. Multimodal models are known to locate UI elements poorly. |

### Verdict

Pegasus **cannot reliably find individual UI actions or their before/after states** in a browser recording:

- A click lasts under 100 ms. At about 1 frame per second, with timestamps in whole seconds and segments of at least 2 s, several clicks or keystrokes inside one second merge into one.
- It returns no element identity, no cursor position and no bounding box. The compiler needs all three.

CONXA already captures the ground truth this would need, through browser instrumentation:

- Every recorded event has a millisecond `timestamp_ms` aligned with its tab's own video (`recorder/session.py`, around line 865).
- Five frames are cut per event, at −500, −250, 0, +250 and +500 ms (`recorder/frame_extractor.py`).
- `state_change.dom_diff`, deduped DOM and accessibility-tree blobs, and synthetic events for downloads, dialogs, popups and tabs are all recorded.

Video adds real information only where the DOM is blind:

- native file and OS dialogs, and the browser download bar;
- canvas, PDF and video content;
- spinners and long waits;
- hesitation or backtracking ("tried X, undid it");
- a narration of the visible outcome.

**Conclusion:** video is optional, low-trust evidence. It feeds understanding and never creates steps. Additional instrumentation is not required, because CONXA already has it.

---

## 2. Who is responsible for what

| Source | Owns | Trust |
|---|---|---|
| Recorder events + DOM / a11y | What happened, on which element, when (ms), typed values, URL, tab and frame chain, downloads, dialogs, popups | Ground truth |
| Event-aligned frames (ffmpeg) | Visual before and after each action; the final state | High (cut deterministically) |
| Multimodal LLM (existing vision proxy) | Meaning: the goal, the phases, which values vary, decisions observed, how success shows, coverage, questions to ask | Advisory, validated in code |
| Video model (opt-in, Phase 2) | Off-DOM states, waits, backtracking, outcome narration, in coarse segments of 2 s or more | Low; labelled as video evidence |
| The person (Clarify questions) | Final say on intent, values, branches and success | Authoritative |
| Compiler (`IdentityBundle` / `selector_grammar.py`) | The only author of steps and selectors; it consumes confirmed decisions | Unchanged invariant |

---

## 3. Gaps in today's architecture

**Today's order:** record → compile (spends the credit, runs vision anchors and one `workflow_review` call that also produces "understanding") → questions → answers patch the compiled document → test.

1. **The order is backwards.** Understanding is a side output of the compile's final review call (`compiler/build.py`, around lines 1960–1994). Questions exist only after the compile finishes and the credit has been committed (`handlers/compile.py`, around lines 119–127; `handlers/compile_flow.py::_flow_after_compile`). Answers patch the compiled document, and nothing feeds them back into the model.
2. **No description means no check.** Without a `task_description` the review gets no candidates (`build.py`, around line 1965), and `understanding.understand` asks nothing.
3. **The input is thin.** The model sees step names, candidates and at most one frame per step. It never sees before/after pairs, `dom_diff`, synthetic events, idle gaps or the pre-record plan. Typed values are never judged (BUILD-55 known gap).
4. **The video is used only as a frame source**, and only within ±500 ms. A slow page reaction is missed.
5. **Questions are fixed templates.** The model labels candidates and rewords text. It never writes a question from the evidence.
6. **"Coverage incomplete" leads nowhere useful.** It only points to Human Edit; no re-record is offered.
7. **Re-understanding is blocked by the review cache.** The cache and sidecar replay the same answer (`llm/workflow_review.py`, around lines 118–136), so asking again needs a new cache key.
8. **The plan is never checked.** The pre-record plan card is never compared with what was actually recorded.

---

## 4. Target pipeline

```
record ─▶ S1 evidence timeline (code) ─┬▶ S2 understand (1 multimodal call) ─▶ S3 ask (pre-compile Clarify)
          frames cut at stop           │      validated; understanding.json        │
          [P2: video observations] ────┘                                          ▼
                                                                    S4 incorporate: answers become decisions
                                                                                  │ (stored, not yet applied)
                                                                                  ▼
          S7 test ◀─ S6 post-compile Clarify (compile-only kinds) ◀─ S5 compile with the confirmed understanding,
                                                                       then replay stored decisions through the
                                                                       existing gated mutations
```

| Stage | What happens | LLM? |
|---|---|---|
| S1 Evidence | Turn events, frames, `dom_diff`, synthetic events, idle gaps and hints into one timeline | No |
| S2 Understand | One multimodal call: goal, phases, value verdicts, outcome, coverage | Yes (vision) |
| S3 Ask | Questions about varying values, coverage and typed values, before compile | Wording only |
| S4 Incorporate | Each answer becomes a stored decision; nothing is applied yet | No |
| S5 Compile | The compiler runs with the confirmed understanding as meaning-only context; stored decisions are then replayed | Yes (existing calls) |
| S6 Post-compile ask | Only kinds that need a compiled step: file upload, irreversible confirm, popups, success check, forks | Wording only |
| S7 Test | Unchanged test flow and its failure follow-ups | As today |

**Fallback:** if S1–S2 fail, or there are no events, the flow goes straight to today's path (compile, then post-compile questions). Understanding never blocks a compile.

---

## 5. Phase 1: understand before compile (buildable spec)

### 5.1 Evidence timeline

Add a new pure module, `conxa_compile/conversation/evidence.py`. It makes no LLM calls.

- `build_timeline(session_id) -> dict` reads `read_session_events`, `videos.json`, the `frames/` directory and `recorder/hints.load`. Each event row carries:
  - `seq`, `t_ms`, `tab`, `action`, the target label (semantic or target text), `url` and the typed value;
  - a `dom_diff` summary: counts plus up to 3 labels;
  - synthetic flags for download, dialog, popup and tab;
  - the frame paths `before_near` and `after_far`.

  Idle gaps longer than 3 s are marked.
- `candidates_from_events(events)` returns candidates keyed `kind:literal`, of three kinds:
  - `url`: the path and query of a navigate;
  - `click`: the target text;
  - `typed`: an input value.

  The `row` kind stays post-compile, because `entity_binding` exists only after compile. Reuse the helpers and caps already in `conversation/understanding.py` (`_snake`, `_unique_name`, 12 non-typed / 6 click / 6 typed).
- `pick_frames(timeline, budget=8)` chooses before/after pairs for code-ranked steps:
  - the largest `dom_diff`;
  - navigations, downloads and dialogs;
  - the final state;
  - steps marked `varies` in hints.

### 5.2 Cut frames at stop, not at compile

- Move the frame-extraction block from `handlers/compile.py` (around lines 87–106) into a helper. Both the understand step and `cmd_compile` call it.
- Skip extraction when `frames/` is already complete. Check whether `extract_frames_for_session` is idempotent. If it is not, test for the files before cutting.

### 5.3 The understanding call

- Revive the existing `workflow_understanding` prompt (`conxa_core/llm/client.py`, around lines 180–198) as a **vision** task. Add it to all three vision-task lists (BUILD-55 notes they are triplicated; one is `VISION_TASKS` in `conxa_compile/llm/client.py`).
- **Input:**
  - the task description;
  - a thread summary (existing `thread_summarize`);
  - the plan card text (`record_plan`);
  - the timeline as text;
  - the candidates;
  - up to 8 before/after image pairs.
- **Output:** `{goal_summary, phases[{from_seq, to_seq, purpose}], values[{ref, verdict, name, literal}], outcome{description, seq}, coverage{ok, note}}`.
- Extend `understanding.validate` to check phases and the outcome, and drop unknown `seq`s and `ref`s.
- **Invariant kept:** the model labels meaning only. It never sees or writes a selector.
- Store the validated result in `data/workflows/<id>/understanding.json`, tagged with `for_task` and `session_id`.

### 5.4 New flow stage: understand, then ask, before compile

`cmd_flow_finish_recording` (`handlers/workflow_flow.py`) calls a new `_flow_understand` instead of `_flow_start_compile`. `_flow_understand` lives in `handlers/compile_flow.py`, beside `_flow_compile`. It runs these steps in this order:

1. Set the existing `understanding` runtime flag. Post a "Looking at your recording" event.
2. Cut the frames, build the timeline, make the call, and validate the answer.
3. Build the pre-compile questions with a new `clarify.generate_understanding_questions(understood)`. It reuses the existing `coverage` and `chosen_values` builders without needing a compiled document, and adds typed values to the tick list.
4. Save `clarify.json` with `phase: "pre_compile"`, then post question 1. The existing background rewording is unchanged.
5. If there are no questions, or anything failed, call `_flow_start_compile` directly.

**Answers:**

- `_card_clarify_question` checks `phase == "pre_compile"`. It runs `plan_answer` as today, but stores the resulting ops on the question as `deferred_ops` instead of calling `_run_clarify_ops`.
- When no question is left open, it starts `_flow_start_compile`.
- A "coverage incomplete" answer offers two choices: **Record again** (the existing record action) or **Compile anyway**.

**Lifecycle** (`conversation/lifecycle.py`):

- Add a stage `understand` between `record` and `compile`.
- While pre-compile questions are open, only three actions are allowed: `answer_clarify`, `record` (re-record) and a new `compile_now` (skip the questions).
- Add `compile_now` to `conversation/actions.py` and mirror it in the renderer's `lib/actions.ts`; an existing test keeps the two in sync. Update the stage label in `StageDivider.tsx`.

### 5.5 Compile with the confirmed understanding, then replay decisions

- `compile_skill_package(..., understanding=confirmed)`. When `confirmed` is present:
  - `review_inputs` stays empty, so nothing is re-judged and fewer tokens are spent;
  - the confirmed goal, phases, outcome and decisions reach `_apply_second_opinion` as meaning-only context.
- Fold a hash of the confirmed understanding into the review cache key, so changing an answer forces a fresh review.
- In `_flow_after_compile`, first replay every `deferred_ops` through the existing all-or-nothing `_run_clarify_ops`. This works because the ops are keyed by literal value:
  - `parameterize` goes to `workflow_mutations.parameterize_literal` or `parameterize_click_text`;
  - typed decisions go to `drop_inputs` or `patch`.

  An op that fails with `literal_not_found` is re-posted as an ordinary post-compile question. It is never silently dropped.
- Then run `clarify.generate_questions` without the kinds already decided (`chosen_values`, `coverage`, typed values). The compile-only kinds remain.
- `understanding.understand(...)` reads `understanding.json` first, then falls back to `compile_report["understanding"]` (the old path).

### 5.6 Switch and doc updates

- Add `SKILL_UNDERSTAND_BEFORE_COMPILE` to `conxa_core/config.py`, default `true`. When it is off, today's path runs unchanged.
- When this ships, update:
  - `TRD.md`: the conversation-led studio section and the "Understanding check" paragraph;
  - `App-Flow.md`: record → understand → ask → compile;
  - the `CLAUDE.md` invariant "The understanding check…", because compile now also waits for it;
  - `TODO.md`: close the BUILD-55 typed-value gap and file the Phase 2–3 items.

### 5.7 Cost and metering

- Phase 1 adds one multimodal call before compile. In return, the review call no longer judges candidates, so total tokens stay roughly flat.
- The understanding call is metered as LLM usage through the proxy, like every other call. No compile credit is reserved until the compile starts.

---

## 6. Phase 2: opt-in video evidence (outline)

1. **Run a spike first.** A script under `conxa-cloud/scripts/` runs Pegasus 1.6 segmentation on 5 real CONXA recordings, with segment fields `{action_kind (enum), visible_target, outcome, off_page_state}`.
   - Score the output against `events.jsonl` on four measures:
     - recall within ±1.5 s;
     - timestamp error;
     - false segments;
     - observations that no event explains.
   - **Go only if** it adds a useful off-DOM observation in roughly 1 of every 3 recordings, or measurably improves the questions asked.
2. **Cloud side.** Add `POST /api/v1/video/analyze`.
   - The cloud holds the TwelveLabs key and meters usage.
   - The endpoint is gated by a workspace setting, `video_understanding`, which is off by default.
   - It deletes the TwelveLabs asset after analysis.
   - Add a `Security.md` SG entry for the new data processor, and the endpoint to `Backend-Schema.md`.
3. **Studio side.** Add `services/video_analysis_client.py`.
   - It runs in parallel with frame cutting and writes `sessions/<id>/video_observations.json`.
   - Observations are aligned to event seqs through `videos.json.start_wall_ms`.
   - Each tab's video is sent as its own request. Videos shorter than 4 s are skipped.
   - Only segments that no event explains, plus the phase narration, enter S2, labelled `source: video`.

## 7. Phase 3: questions written by the model, and re-understanding (outline)

- S2 returns `open_questions[{seq, question, why, options}]`. These become a new Clarify kind, `intent_question`, which accepts free text. A text call maps the answer onto one validated decision type:
  - a value verdict;
  - "record another way" (variant branch, BUILD-54);
  - a segment re-record;
  - an extra non-required check (one of the `suggest_assertion` types).
- An answer of "that's not what I meant" re-runs S2 with the answer as added context. This needs a `CLAUDE.md` invariant update, because the model would then write questions, not only labels.
- Compare the plan card with the recording.
- Add the remaining BUILD-32 question kinds: row, date and if-missing.
- After a test, compare the result with the confirmed `outcome`.

---

## 8. Verification plan (for whoever builds Phase 1)

**Unit tests:**

- New `conxa-cloud/tests/test_evidence_timeline.py`: timeline rows, idle gaps, candidates, frame choice.
- Extend `test_compile_clarify.py`: pre-compile questions; deferred ops stored, not applied.
- Extend `test_compile_flow.py`:
  - the happy path: finish recording, understand, answer, compile starts, deferred op applied, lifecycle allows test;
  - the understanding-failure fallback;
  - the `compile_now` skip.
- Extend `test_conversation.py`: the lifecycle stage and actions.
- Keep the existing actions sync test passing.

**Run:**

```bash
cd conxa-cloud
pytest -q tests/test_evidence_timeline.py tests/test_compile_clarify.py tests/test_compile_flow.py tests/test_conversation.py tests/test_extract_step.py
pytest -q tests
```

**Live check:**

```bash
cd conxa-builder/electron && npm run dev
```

Record a 3–4 step search-and-download workflow with a description, then confirm three things:

- The questions appear **before** the compile card.
- Answering "varies" produces a `{{name}}` input in the compiled workflow.
- Test unlocks only after that.

---

## Sources

- [TwelveLabs: Pegasus model page](https://www.twelvelabs.io/pegasus)
- [TwelveLabs docs: Pegasus concepts](https://docs.twelvelabs.io/docs/concepts/models/pegasus)
- [TwelveLabs docs: analyze / summarize reference](https://docs.twelvelabs.io/api-reference/generate-text-from-video/summarize)
- [TwelveLabs docs v1.2 reference (limits)](https://docs.twelvelabs.io/v1.2/reference/summarize)
- [TwelveLabs llms-full.txt (pricing, limits, deployment)](https://www.twelvelabs.io/llms-full.txt)
- [TwelveLabs pricing calculator](https://www.twelvelabs.io/pricing-calculator)
- [TwelveLabs blog: Introducing Pegasus 1.2](https://www.twelvelabs.io/blog/introducing-pegasus-1-2)
- [Amazon Bedrock: TwelveLabs Pegasus 1.2 parameters](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-pegasus.html)
- [DEV Community: agentic video analysis with Pegasus (~1 fps estimate)](https://dev.to/aws/build-agentic-video-analysis-with-twelvelabs-pegasus-and-strands-agents-sdk-5a0m)
- [GUI Action Narrator / Act2Cap (arXiv 2406.13719)](https://arxiv.org/html/2406.13719)
- [Act2Cap benchmark page](https://www.alphaxiv.org/benchmarks/chinese-academy-of-sciences/act2cap)
- [ClickAgent: UI localization limits of MLLMs](https://arxiv.org/html/2410.11872v2)
