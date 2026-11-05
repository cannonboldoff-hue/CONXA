# Clarifying Questions: architectural investigation

**Date:** 2026-11-05 · **Branch:** `fix/audit-2026-11-05` · **Scope:** how Build Studio decides what to ask before it makes a workflow, why that fails for arbitrary workflows, and what a general design looks like.
**Status:** investigation only. No code was changed. Nothing here was run against live recordings; every claim about current behaviour is read from code, with `file:line` so you can check it. Claims that are my inference are marked **[inferred]**.

---

## 0. Summary

The Clarifying Questions system is not one system. It is two question generators written at different times, joined by a filter, and both are **catalogues**: a fixed list of question types, each triggered by a hand-written pattern. A workflow gets asked about something only if it happens to match a pattern someone already thought of.

The seven gaps that matter most, in order of damage:

1. **The catalogue is the architecture.** There is no model of *what the workflow still needs to know*. So "can it handle any workflow" is answered by "add another pattern", which cannot finish.
2. **For any recording that goes through the understanding-first flow, most post-compile question types are switched off**, on the stated belief that "every gap was asked before the build". That belief is false for eight of the 15 question kinds (section 3.1). This is the clearest defect found, and it is a regression, not a design limit.
3. **Nothing re-evaluates after an answer.** The model is called once per recording. An answer is stored and the next card is shown. Answers never create, remove or re-rank other questions, except for one hand-built follow-up on popups.
4. **There is no "enough" criterion.** The loop ends when the cards run out. The cap (5) and the prompt line "most recordings need 0 to 3" are the only brakes. Nothing checks whether the compile contract is actually satisfied.
5. **Priority is self-reported by the model** (`cost_rank`), not computed from impact, reversibility or confidence.
6. **Answers that do not fit one of six effects become text and change nothing.** The `note` escape hatch is the only route for error handling, empty results, auth, branches. It is appended to the compile prompt as text and changes no step (section 3.4).
7. **Quality is not measured.** The eval script measures how often the validator had to drop something. It says of itself "it judges nothing". No metric says whether a question was useful, whether a guess was right, or whether a needed question was missed.

The proposed direction (section 5): stop asking "which pattern matched?" and start asking "which slots of the finished workflow are not yet determined, and is it worth a person's attention to fill each one?". Concretely, an **unknowns ledger** derived from the workflow's own schema, a **value-of-information rule** that decides ask / confirm / assume / defer, a **re-evaluation loop** after every answer, and **outcome metrics** fed by test and runtime failures. Existing safety invariants stay as hard floors.

---

## 1. How the system works today

### 1.1 The pipeline

```
record ─► merge (browser events + video model rows) ─► Q1: understanding call ─► person answers
      ─► decisions.json ─► compile ─► replay decisions ─► Q2: clarify.generate_questions ─► person answers
      ─► summary ─► test ─► (test_followup / input_check questions on failure)
```

### 1.2 Q1, before the compile (`conversation/timeline_understanding.py`)

- **One model call per recording** (plus one retry if the answer grades `weak` or `unusable`, `timeline_understanding.py:690-743`). Prompt is in `packages/conxa-core/conxa_core/llm/client.py:874-921`.
- **Input:** up to `MAX_ROWS = 250` merged rows (`:310`), the task description (cut to 300 characters), a `chat` field (600), a list of *candidates*, and a `must_ask` list computed by code.
- **Candidates** are only three kinds: navigated page addresses (max 8), clicked labels (max 6), typed text (max 6), plus a separate allowance for items picked from a list (`:165-203`). Select, checkbox, radio, date, drag, shortcut and extract actions are not candidates.
- **`must_ask`** is code-detected: a decision popup, a click that was followed by a decision dialog, and an upload not produced by an earlier download (`:228-256`). Plus "missed file picks" seen only in the video (`:259`).
- **Output:** a goal, per-row summary, a verdict (`fixed`/`varies`/`unsure`) per candidate, `decided` points with reasons, and `questions`.
- **Each question must use one of six `effect` values**: `value`, `upload`, `confirm_before`, `popup_dismiss`, `fork`, `note` (`RULES`, `:57-80`). Choice ids are fixed per effect. The model writes the sentence and labels only.
- **Validation** (`validate`, five stages) drops invented refs, overrides the model where the recording knows better, puts back what always needs a person, and strips unsafe guesses (`:406-475`). This part is well built.
- **Dedupe and rank:** one question per recorded value and per (row, effect), sorted by the model's `cost_rank`, first 5 open and the rest queued (`:478`, `:809-834`).
- **Typed text is never asked about** (`typed_value_question` override, `:439`). The reasoning is that typed text becomes an input at compile.

### 1.3 Answers (`handlers/understand_flow.py`, `conversation/decisions.py`)

An answer is stored per recorded event id in `decisions.json`. **Nothing is applied yet.** After the last shown question is answered, `_flow_after_understand` starts the compile (`understand_flow.py:315-331`). There is no model call between answers.

### 1.4 Q2, after the compile (`conversation/clarify.py`)

`generate_questions` (`:439-725`) produces 15 question kinds, each an independent hand-written pattern over the compiled document:

| Kind | Fires when |
|---|---|
| `upload` | step is an upload bound to `file_path` |
| `confirm` | `step.consequence == "irreversible"` |
| `values` | typed step with `input_binding` and no recording-time hint |
| `output` | an `extract` step exists (max 2) |
| `relative_date` | a date pick within −31…+366 days of today (max 2) |
| `skip_when_empty` | optional-looking typed input (max 2) |
| `missing_item` | `entity_binding` with a `{{...}}` identifier (max 2) |
| `fork`, `fork_then`, `known_popup` | outcome forks and app-map popups |
| `popup` | recorder set `optional_hint` |
| `done_when` | compiler wrote not-yet-required assertions |
| `dropped_value`, `coverage`, `chosen_values` | ledger/understanding signals |

Order comes from a static per-kind table, `_PRIORITY` (`:38-41`). After the compile, a stored decision is replayed through `clarify.plan_answer`, the one place an answer becomes edits (`replay.py`). That reuse is the best structural decision in the system: there is no second editing path.

### 1.5 What sits around it

Question wording is rewritten by a background model call and checked field by field (`clarify_question_copy`, `compile_flow.py:298-329`). After a failed test, two extra questions can appear (`test_followup_question`, `input_check_question`, `clarify.py:265-328`). That is the only place the system learns from an outcome.

---

## 2. What the system can and cannot represent

Today the system's whole notion of uncertainty is: a **verdict** on each candidate, a **best guess** on each question, and a **rank** on each question. That is all. There is no object that says "this thing is unknown, here is the evidence, here is what depends on it, here is what a wrong guess costs, here is what would resolve it".

Without that object, the following are not expressible, so they cannot be reasoned about:

- Unknowns that are not a property of one recorded value (what the run should return, what must be true before it starts, what happens when a list is empty).
- Dependencies between unknowns (the answer to "this item varies" changes what else must be asked).
- Confidence: a verdict is one of three words, not a probability, and there is no "how sure and why".
- Impact: nothing links an unknown to the part of the finished workflow it decides.
- Status over time: inferred, confirmed, contradicted, stale.

Everything below follows from this.

---

## 3. Where and why it fails

### 3.1 Q2 is mostly switched off for the main path (defect)

`compile_flow.py:31`:

```python
_ASKED_AFTER_UNDERSTANDING = {"fork", "fork_then", "known_popup", "popup", "confirm"}
```

At `compile_flow.py:218-233`, if `understanding.json` exists for the recording, Q2 questions are filtered to that set. The comment in `understand_flow.py:200` justifies it: "Every gap was asked before the build". Compare with what Q1 can actually ask:

| Q2 kind | Asked in the understanding-first path? | Why not |
|---|---|---|
| `values` for dropdown, radio, checkbox, date controls | **No** | Not Q1 candidates (`candidates_from_timeline` only reads navigate, click, type/fill) |
| `values` for typed text | **No** | Q1 refuses typed questions; Q2 `values` is filtered out |
| `relative_date` | **No** | Filtered; Q1 has no date concept |
| `skip_when_empty` | **No** | Filtered |
| `missing_item` ("what if the item is not on the page?") | **No** | Filtered; Q1 reaches it only if the model spontaneously uses `note` |
| `output` ("report this value back?") | **No** | Filtered; Q1 has no output concept |
| `done_when` ("how do we know it worked?") | **No** | Filtered; Q1 has no success-criteria concept |
| `dropped_value` (typed but left out of the workflow) | **No** | Filtered |
| `coverage` (does the recording do what you said?) | **No** | Hard-coded `{"ok": True}` at `understand_flow.py:123` and `:202`; the old check (`understanding.understand`) only runs when there is no prior |

The effect: a recording with a date picker, a dropdown, an extract, an optional field and a record lookup produces **no question about any of them**, and the "nothing needs deciding" message may be shown (`compile_flow.py:276`). The typed-text verdict the model writes is, as far as I found, consumed only by the old path (`fixed_typed` in `clarify.py:559`, which runs only when `understanding` is passed, and it is not when `prior` is set, `compile_flow.py:225`). The docs list one slice of this as open ("typed-text verdicts do not pre-tick the compile-time values question"); the filter makes the whole class unreachable.

This is the strongest evidence for gap 1: two catalogues were split by a hand-written list, and the list drifted from the truth.

### 3.2 The model is told about things the caller does not give it

`understand_flow.py:117` calls `tu.understand_timeline(merged, workflow.task_description, existing_inputs=..., hints=...)`. The `chat` argument is never passed, though the prompt describes `chat` as "what they said while recording" (`client.py:889`). Conversation history is therefore not an input to Q1.

Other inputs the model never sees, so it cannot reason from them:

- Page structure: input types, required/pattern attributes, option counts of a select, whether a field is validated.
- Network behaviour: status codes, redirects, loading states (redirects are folded away upstream by `_caused_by_action`).
- Sibling workflows and answers given earlier on the same app (`existing_inputs` carries names only; known popups feed Q2 only).
- A second observation of the same flow. There is one recording and no replay, so "this changed between two looks" is never evidence.

### 3.3 Silent truncation produces silent under-questioning

- Only the first 250 rows are shown (`MAX_ROWS`). A long workflow's tail is invisible to Q1 with no signal that it was cut.
- Candidate caps (8 addresses, 6 clicks, 6 typed) mean candidate 9 is never judged. The grade check treats "no verdict" as weak only for candidates that exist.
- Q2 caps several kinds at 2 (`output`, `relative_date`, `skip_when_empty`, `missing_item`), with a `break` and no "and N more".
- The validation ledger counts what the model got wrong; it does not count what the caps hid.

### 3.4 Answers that do not fit six effects change nothing

`note` is the catch-all. Its answer is stored as text (`decisions.py:52-65`) and, at compile time, appended to the task description:

```python
# handlers/compile.py:150-152
notes = self._setup_notes(session_id)
if task_text and notes:
    task_text += "\n\nAnswers given before the build: " + " | ".join(notes)
```

Three consequences, all verified in code:

- Notes are appended only `if task_text and notes`. On the understanding path an empty description is filled from the summary first (`_fill_description`), so nothing is lost in practice. *(Corrected 2026-11-05: an earlier draft of this report called this a defect.)*
- At most 5 notes, 300 characters each.
- The code comment says it plainly: "no step changes because of one". The replay table (`replay.py:21-28`) has no entry for `note`.

So the questions that matter most for generality ("what if nothing matches?", "what if this fails?", "what if they are not signed in?") are exactly the ones whose answers are decorative. A person who answers one is told the answer was "noted", but the deterministic program is the same.

### 3.5 No re-evaluation, no dependencies, no stop rule

- After Q1, `_pre_compile_answer` stores a decision and calls `_flow_post_next_question`. No code revisits what remains. If the answer to "does the invoice number change?" is "yes, it comes from the user's email", no further question about format, validity or empty results is generated.
- The only dependent question is `fork_then` (a follow-up about popup choices the recording did not take). It is hand-written and unique to popups.
- Stopping is "no open cards left" (`compile_flow.py:343-348`). Nothing asks "is the workflow's contract complete?". The prompt's brake is a sentence: "Most recordings need 0 to 3." A model told to ask few will ask few, whether or not few is enough.

### 3.6 Priority and confidence are not computed

- Q1 order: model's `cost_rank` (1 = costliest to guess wrong), default 99 if missing or non-integer (`:470-473`). The model is asked to rank its own questions with no definition of cost and no calibration.
- Q2 order: a constant table by kind. A one-in-a-hundred popup outranks a free-text input that decides every run.
- `best_guess` is a pre-selected choice with no probability and no evidence. It is never checked against outcomes, so no one can know how often it is right.

### 3.7 Instrumentation dependence (Q2)

Each Q2 detector reads a field the compiler or recorder must have set (`consequence`, `optional_hint`, `handler_hints.control_kind`, `entity_binding`). If the compiler does not tag a situation, no question can fire. Unknown unknowns are therefore bounded by compiler instrumentation, and adding coverage means adding both a compiler tag and a detector. **[inferred]** this is why the system has grown one kind per incident rather than converging.

### 3.8 The requested scenarios, one by one

| Scenario | What exists | Gap |
|---|---|---|
| Dynamic inputs | Q1 `value` for addresses/clicks/list picks; Q2 `values` for typed (now filtered); `chosen_values` | No notion of an input's **domain**: type, allowed values, format, required vs optional, where the run gets it. Typed text is never asked in the main path. |
| Hardcoded values | A "fixed" verdict leaves the recording as is | Nothing records *why* it is fixed, so a later contradiction (a test failure on that value) cannot be traced to the assumption. |
| Conditional branches | `popup`/`fork`, `skip_when_empty`, `missing_item` | Only popups and two narrow cases. A page that shows different content by account, plan, locale or data state is never asked about. |
| Popups | Strongest area: forks, per-button choices, app-map, `fork_then`, hard safety invariants | Good. The invariants here are the model to copy for other facets. |
| Authentication | Login pages are dropped from the workflow (`login_page` pass) and sign-in is saved separately | No question about expiry, MFA, device trust, which account/tenant, or "this needs a different login per customer". |
| Downloads | Used to suppress an upload question | Which file, naming, location, what is returned to the caller, what happens if no file arrives: none asked. |
| Redirects | Folded into the causing click | A redirect that depends on state (first login vs returning) is invisible. |
| Errors | Runtime has typed fault detection (`detectFault`) | No authoring-time question about what the author wants to happen on an error. The `note` escape hatch is decorative (3.4). |
| Alternative states (empty list, no results, already done) | `missing_item` (narrow) | The general class "page is in a state the recording never saw" is unrepresented. |
| Human-decision points | `confirm_before`, forks, `ai_review` | Good for the irreversible cases; not generalised to "a judgment call that is not irreversible". |

---

## 4. Why a better prompt will not fix this

The failures in section 3 are not wording failures:

- 3.1 is a filter list that is out of date.
- 3.2 is missing inputs.
- 3.3 is caps with no accounting.
- 3.4 is an answer type with no way to change the program.
- 3.5 and 3.6 are the absence of state and of a decision rule.

A better prompt makes the single call better at the six effects. It cannot make the system ask about a seventh thing, remember what it learned, or know when it is done. The structure has to change; the prompt becomes one component.

---

## 5. A general architecture

### 5.1 The principle

**Derive questions from what the finished workflow needs, not from what patterns the recording shows.**

A reusable, deterministic workflow has a fixed anatomy. Everything in it is either (a) determined by the recording, (b) determined by a rule, or (c) a **slot** that only a person can fill or approve. Questions are the way to fill (c). If you enumerate the slots from the workflow's schema, completeness stops being "did we think of this pattern" and becomes "are all required slots determined".

### 5.2 The slot taxonomy (small, closed, workflow-independent)

About a dozen *facets* cover what a compiled skill needs. They describe kinds of knowledge, not kinds of workflow.

| Facet | The slot it fills | Compiled field it determines |
|---|---|---|
| Variability | fixed value vs input (and which) | `inputs[]`, step `value` |
| Input domain | type, format, allowed values, required, default, source | input schema, validation |
| Provenance | where a value comes from (user, previous step, environment, clock) | bindings, `run_if`, date offsets |
| Cardinality | one thing or "each of N" | loops, entity binding |
| Precondition | what must be true before start (signed in as whom, data exists) | entry checks, sign-in requirement |
| Postcondition | what "done" means and what is returned | assertions, `extract`/outputs |
| Alternatives | other states the page can be in and what to do | branches, fork, `on_missing` |
| Failure policy | stop, skip, retry, ask a person | recovery policy, handover |
| Consequence | reversible or not, who approves | `consequence`, confirm gates |
| Environment | tenant, locale, timezone, account tier | environment binding |
| Scope | what belongs in this workflow | step inclusion, noise flags |
| Timing | what to wait for | waits, success conditions |

Today's six effects are six cells of this table. The other cells are the structural gaps. A facet is not a question: it is a slot with a location (event, step, edge or whole workflow).

### 5.3 The unknown, as a record

```jsonc
{
  "id": "u17",
  "facet": "variability",
  "subject": {"kind": "event", "ref": "click:42"},          // where in the workflow it lives
  "slot": "step.value or inputs[]",                          // what compiled field it decides
  "hypotheses": [
    {"value": "varies", "p": 0.80, "evidence": [e1, e3]},
    {"value": "fixed",  "p": 0.20, "evidence": [e2]}
  ],
  "evidence": [
    {"id": "e1", "source": "task_text",  "strength": "strong", "text": "'download an invoice' names no number"},
    {"id": "e2", "source": "recording",  "strength": "weak",   "text": "one value seen once"},
    {"id": "e3", "source": "dom",        "strength": "medium", "text": "target is one of 14 like rows"}
  ],
  "impact": {"decides": ["inputs", "step.value"], "reach": "every run", "reversible": true,
             "cost_if_wrong": {"varies→fixed": "wrong item every run", "fixed→varies": "one extra prompt"}},
  "depends_on": [], "unlocks": ["u18", "u19"],
  "status": "open",                                          // open | inferred | confirmed | contradicted | deferred | stale
  "resolution_plan": "clarify.plan_answer ops"               // reuses the existing safe mutations
}
```

Why each part earns its place: `hypotheses` with probabilities makes "infer or ask" computable. `evidence` with a source makes a wrong inference traceable and lets a later test failure demote exactly the assumption it contradicts. `impact` makes priority computable. `depends_on/unlocks` makes ordering and re-evaluation possible. `status` makes "done" definable.

### 5.4 Where the evidence comes from

Only the first two exist today:

1. Recording rows and the video model's account.
2. Task description (truncated) and hints.
3. **Conversation history** (not passed today, 3.2).
4. **Page facts** from the DOM snapshot already captured: input `type`, `required`, `pattern`, option lists, list sizes (`peers` exists but is the only one used).
5. **Network facts**: status, redirects, response shape.
6. **Cross-workflow memory**: the app map, earlier workflows on the same app, and the workspace's earlier answers ("this workspace always keeps approvals on").
7. **A replay of the compiled draft in the sandbox** before asking anything. A flow that behaves differently on a second run, or lands in a different state, is direct evidence of dynamism that no amount of reading the first recording provides. **[inferred]** this is the highest-value new evidence source: it is a cheap oracle the system already has the machinery for (the Studio sandbox).
8. **Test and runtime outcomes** (failures, edits, Tier B escalations), mapped back to the slot that would have prevented them.

### 5.5 Who does what

Keep the existing division, and extend it. The repo's rule is "the model labels meaning; code does every rewrite". Extend that to *decisions about asking*.

| Role | Does | Never does |
|---|---|---|
| **Proposer** (model, reads rows + evidence) | Emits unknowns with hypotheses and cited evidence; may use an `other` facet, but must name the slot it affects | Decide whether to ask; write selectors |
| **Auditor** (model, separate call, adversarial) | "Premortem": given the draft, name ways this fails on another day, account or data; each must map to a slot | Duplicate the proposer; ask the person |
| **Code: scorer and selector** | Computes value of asking; applies safety floors; batches; orders by dependency | Guess meaning |
| **Code: applier** | Turns an answer into the existing safe mutations; re-runs detection | Apply anything without the all-or-nothing gate |
| **Wording model** (exists) | Phrases the question | Change choice ids or meaning |

The model supplies probabilities and evidence. Code decides. This is the direct generalisation of the `told`/`person_hint` overrides already in `validate`.

### 5.6 The decision: ask, confirm, assume, or defer

For each open unknown, with *p* = probability the best hypothesis is right, *reach* = how many runs it affects, *cost* = harm of a wrong guess and *undo* = how cheaply it can be fixed later:

| Condition | Action |
|---|---|
| Safety floor (irreversible step, decision popup, credential, anything the invariants forbid answering) | **Always ask.** No guess pre-filled. Today's rule, kept as a floor. |
| *p* high and *cost × reach* low | **Assume** silently; record it in an "Assumptions" list. |
| *p* high and *cost × reach* moderate | **Assume and surface**: one line in the summary, one click to change. |
| *p* moderate and *cost × reach* moderate | **Confirm**: one tap with the guess pre-selected (as the cards do today). |
| *p* low, or *cost × reach* high | **Ask** with the hypotheses as choices. |
| Cheap to fix after a test shows it | **Defer**: do not ask now; attach to the test; ask only if the test points at it. |

Written as one rule: ask when `P(wrong) × cost × reach` exceeds the cost of interrupting. The interruption cost is not a constant; it rises with every question already asked (a fatigue term). That is what turns "at most 5" into something principled: critical questions exempt, marginal ones fall out first.

**"Defer" is the biggest anti-annoyance lever.** The system already has the pieces (`test_followup_question`, `input_check_question`); this makes them the normal route for low-stakes unknowns instead of a special case.

### 5.7 Answers change the model

An answer is not stored text; it is a typed assertion with consequences:

1. **Apply**: through the existing `plan_answer` ops (no new edit path, the repo's invariant holds).
2. **Propagate**: the answer updates dependent unknowns. "Varies, from the user" unlocks input-domain and empty-result unknowns for that input; "fixed" retires them. Same facet at many sites (four date pickers) collapses to one scoped question the first time, and the answer is offered as the default for the rest.
3. **Detect conflicts**: an answer that contradicts earlier evidence (the person said "fixed" but the task text names no value) marks the earlier evidence and asks once, rather than silently picking one.
4. **Re-run detection** on the changed model. Deterministic detectors run first; the proposer is re-called only when new evidence exists or a facet opened that code cannot judge. This is an incremental loop, not a second full call.
5. **Remember**: store the answer at workspace level as a default for the same facet on the same app.

`note` stops being text. A free-text answer must be mapped to a slot (by the proposer, validated by code) or it is rejected with "I couldn't turn that into a change; here are the options". The person is never told "noted" about something that changes nothing.

### 5.8 The loop and when it stops

```
 evidence ─► propose unknowns ─► score ─► select (ask / confirm / assume / defer)
     ▲                                          │
     │                                          ▼
     └──── re-evaluate ◄── apply answer ◄── person
```

It stops when all of these hold:

- Every **required slot** of the compile contract is `confirmed`, `inferred` above its threshold, or `deferred` to a named test.
- The best remaining unknown has value-of-asking below the interruption cost.
- No unresolved conflict.

It ends by emitting a **readiness report** the person can read: slots decided and how (told / inferred / assumed), assumptions made, items deferred to testing. This replaces "no more cards" as the definition of done, and it is what makes under-questioning visible instead of silent.

### 5.9 Preventing over-questioning

- **Vacuity test (code, cheap).** For each choice, compute the ops `plan_answer` would produce. If every choice yields identical compiled output, the question is vacuous: drop it. This catches a large class of "annoying" questions mechanically.
- **Ask once per concept**, keyed by slot, not by (row, effect). Today's dedupe (`_dedupe`) misses the same concept asked from two rows.
- **Never ask what was said.** Generalise `told`: a ledger of statements from task text, chat, hints and prior answers, each supplying evidence to hypotheses.
- **Prefer the guess.** Confirm-with-default beats open questions; batch confirmations of one facet.
- **Defer what a test can reveal safely.**
- **Workspace memory** removes repeats across workflows on one app.
- **Budget by fatigue, not count.**

### 5.10 Preventing under-questioning

- **Slot coverage**: completeness is "every required slot determined", not "no pattern fired".
- **The auditor pass** (premortem) for unknown unknowns that no slot-walk predicts.
- **Replay evidence** (5.4 #7).
- **A miss log**: every post-compile edit, test failure and runtime Tier B escalation is tagged with the slot that would have prevented it. Misses become test fixtures and prompt/detector changes.
- **No silent caps**: anything cut by a cap is counted, shown ("12 more rows not examined") and treated as an open unknown.

---

## 6. Measuring quality and completeness

The current eval (`conxa-cloud/scripts/eval_understanding.py`) counts validator drops per prompt version and "judges nothing". That measures output hygiene, not usefulness. A usable set:

**Per question**

| Metric | How | What it catches |
|---|---|---|
| Vacuity rate | answers whose ops are identical across choices | questions that cannot matter |
| Guess accuracy and calibration | pre-selected choice vs final answer; bin by stated probability (Brier or ECE) | over/under-confident inference |
| Skip and abandon rate; time to answer | card events | low-value or unclear questions |
| Edit-after-answer rate | person reverses within the session | confusing wording |

**Per workflow**

| Metric | How | What it catches |
|---|---|---|
| Slot recall / precision against a gold set | annotate a corpus of real recordings with the slots a correct workflow needs; compare | **missed** and **extra** questions, directly |
| Miss rate | post-compile edits and test failures that a question could have prevented | under-questioning |
| Escape rate | runtime failures traced to an undetermined slot | under-questioning that reached customers |
| Questions per workflow vs complexity | count against steps/inputs | over-questioning |
| First-test pass rate; recompiles per workflow | existing events | end-to-end effect |

**Offline checks**

- Golden-case fixtures exist for the validator (`understanding_validate_cases.json`). Add one set for *selection*: given these unknowns, these are asked, these assumed, these deferred.
- Mutation tests: take a finished workflow, perturb the page or input, and check that a detector would have flagged the exposed slot.

The corpus is the long pole. Without recordings labelled with gold slots, none of the recall numbers exist.

---

## 7. What must not change

The architecture above is built to sit inside the existing invariants (`CLAUDE.md`), not around them:

- No model writes selector strings. The proposer emits meaning and evidence only.
- A popup that asks for a decision is never answered on anyone's behalf. In the scorer this is a **safety floor**: such an unknown has no pre-filled hypothesis and always asks.
- `no_confirm` is never pre-filled for an irreversible step. Same floor.
- Answers become existing mutations through `plan_answer`; no second edit path.
- Testing waits while any question is open. A *deferred* unknown does not block testing (it is asked from the test), so the lifecycle rule needs one clarification: "open and blocking" vs "deferred".
- Nothing pauses a **run** by default. This design adds authoring-time questions only; "ask a person at run time" is one possible resolution for a failure-policy slot, and only when the author chose it.
- Human Review stays the gate. The Assumptions list is a review surface, not an approval bypass.

---

## 8. Suggested order of work

**Phase 0: fix defects that need no design (small, safe)**

1. Pass `chat` into `understand_timeline` (`understand_flow.py:117`).
2. ~~Make notes reach the compile when the task description is empty~~ (dropped: not a real gap, see 3.4).
3. Re-open the Q2 kinds that Q1 cannot ask (control-type `values`, `relative_date`, `skip_when_empty`, `missing_item`, `output`, `done_when`, `dropped_value`) in the understanding-first path, or move them into Q1. Do not leave a filter whose comment is untrue.
4. Count and surface truncation (`MAX_ROWS`, candidate caps, the `break`s in Q2).
5. Either use the typed-text verdict or stop collecting it.

**Phase 1: one ledger.** Introduce the `Unknown` record; express today's six effects and 15 question kinds as facets that produce it; keep existing question cards as the renderer. No behaviour change except the vacuity check and slot-keyed dedupe.

**Phase 2: decide by value.** Add scoring, the four-way action (ask / confirm / assume / defer), the Assumptions panel and the readiness report. Retire `cost_rank` and `_PRIORITY`.

**Phase 3: close the loop.** Re-evaluation after each answer, dependency propagation, workspace answer memory, real mapping of free text to slots, the auditor pass, replay as evidence.

**Phase 4: measure.** Gold-slot corpus, selection fixtures, miss log wired to test and runtime telemetry.

Phase 0 is independent and worth doing regardless of what is decided about the rest.

---

## 9. Open decisions for you

1. **Is the Q2 filter (3.1) intentional?** I read it as drift, not design. If a different split of responsibility between Q1 and Q2 was intended, say so before phase 0 item 3.
2. **How much interruption is acceptable?** The scorer needs an explicit fatigue budget; today it is implicit in "5".
3. **Is a pre-ask sandbox replay acceptable?** It is the strongest evidence source and costs a browser run per recording. It touches the "testing waits for answers" rule.
4. **Who labels the gold corpus?** Phase 4 needs real recordings with the slots a correct workflow requires.

---

## 10. What I did and did not check

**Read in full or in the relevant parts:** `conversation/clarify.py` (detectors, ranking, reword), `conversation/timeline_understanding.py` (RULES, candidates, must_ask, validation, grade, questions), `conversation/replay.py`, `conversation/decisions.py`, `handlers/understand_flow.py`, the question flow in `handlers/compile_flow.py`, `handlers/compile.py` (notes), the Q1 prompt in `conxa_core/llm/client.py`, `scripts/eval_understanding.py` header, and the Workflow Engine artifact (graph data from the top through the Q2/replay nodes; the later nodes and the reference tab I did not read in full).

**Not read or not run:** `conversation/understanding.py` body (the old coverage check), `clarify.plan_answer` (answer to ops), `clarify_question_copy`, the renderer cards, the test files beyond names. I did not run the system on any recording, so nothing here is measured behaviour. Section 3.1 is the finding to verify first: it is the most consequential and rests on three short code locations (`compile_flow.py:31`, `:218-233`, `understand_flow.py:188-202`).

The existing docs `Conversational-Harness-Gap-Analysis.md` (G7) and `Understanding-First-Harness.md` cover parts of this ground; this report does not repeat them and does not check them against the code beyond the points above.
