# Popups, Dialogs and Conditional App States — General Model

> **Status:** mostly built (2026-11-05, EXEC-49; what is still open is listed in `TODO.md` EXEC-49 and in §9 below). Decisions the owner took are in §10. Builds on what is already shipped: outcome forks (`docs/TRD.md` §11.4), branches (§11.1), the recovery cascade (§10), app maps (§11.5), the understanding check (CLAUDE.md "Key Invariants") and hand-over steps (§11.4 "Hand-Over Step"). Work items: `TODO.md` EXEC-49.
>
> **One sentence:** every surface the app shows is put into one of six classes by code, its provenance (recorded / known / unknown) is looked up, and a fixed table decides what happens; a model may explain a surface or find an element, but it can only ever make a run *more* cautious, never less.

---

## 1. Why a model, not rules

A recording is one path through an app that has many valid states. Popups are only the visible part of that: the same action can also land on a toast, an inline error, a redirect, a disabled button or nothing at all. Per-popup or per-site rules ("if Drive says Replace…") do not scale and drift apart between the recorder, compiler and runtime.

The model below replaces "which popup is this?" with two questions every layer answers the same way:

1. **What kind of thing is it?** (a closed set of six classes, decided by code from the browser's own signals)
2. **What do we know about it?** (recorded in this workflow, known from the app map, or never seen)

The answer to both picks one row of the decision table (§5). Recording, understanding, clarification, compile and runtime all read the same table.

---

## 2. What already exists (reused, not replaced)

| Need | Already built | Where |
|---|---|---|
| Capture a popup's title, message and every button | `dialog_context` on `RecordedEvent` | `bridge.js::buildDialogContext`, `session.py::_native_dialog_context` |
| Tell a decision popup from a nuisance | `classify_dialog` (decision / dismissable / consent / form) and its runtime twin `isDecisionDialog`, pinned by one fixture | `compiler/outcome_fork.py`, `runtime/app/fork.js`, `runtime/test/fixtures/decision_surface_cases.json` |
| "Popup may or may not appear" branch | `outcome_fork` with `clean.observed`, per-state policy, `unknown_policy` | `outcome_fork.py`, `run.js::runForkStep` |
| Optional nuisance popup | `try_dismiss` from the one shared builder | `second_opinion.py::build_try_dismiss_from_hint` |
| Prove a popup is optional | variant recording, rule-based comparison | `compiler/variant_align.py`, `cmd_apply_variant` |
| Popups other workflows met | app map hints, `known_popup` question, shipped `app_knowledge.json`, author `app_policy` | `app_graph/`, `app_knowledge.js` |
| Popups customers met that nobody recorded | scrubbed `app_obs` telemetry → field-only popups | `run.js::emitAppObs`, `app_graph/field.py` |
| Clear an obstruction for free | Tier A dismiss ladder (Escape, consent toolkits, learned dismissals); refuses decision dialogs | `cascade.js::layer1Ladder`, `dismiss_patterns.js`, `learned_dismissals.js` |
| Clear an unknown obstruction | Tier B `dismiss` nomination behind `isSafeDismissLabel` | `failure_response.js`, `run.js::dismissAgentNominated` |
| Detect a silent state change | VERIFY (one required assertion per consequential action) | `run.js::verifyStep`, `assertions.js` |
| Sign-in expiry mid-run | `isAuthFailure` before the cascade | `run.js`, TRD §5.3.7 |
| Ask a person | `handover` step, reviewed fork `ask_human`, author `app_policy: ask_human` | `handover.js`, `fork_pause.js` |
| Destructive safety | `destructive` / `consequence`, dry run, entity binding | TRD §9.6 |

Most of the machinery exists. What is missing is (a) one classifier for **every** surface, not just dialogs, (b) the fault and silent-change classes, (c) a clarification flow that skips what the evidence already answers and can express "always pick this option" and "this option changes the rest", (d) a Tier B contract that returns a classification, and (e) loop and late-popup guards. §9 lists them.

---

## 3. The six surface classes

Classified by code only, from the probe (`page_scripts.dialogProbe` plus the new fault probe in §6.2) and from `dialog_context` at record time. No LLM decides the class. A model's description may be *attached* to a class; it never *sets* one.

| Class | What it is | Signals (deterministic) | Examples |
|---|---|---|---|
| **C1 Obstruction** | In the way, carries no question | dialog/overlay whose buttons are all plain dismiss labels, or a consent banner, or a single "Got it" | cookie banner, product tour, "What's new", promo |
| **C2 Notice** | Information only, does not block | `aria-live`/`role=status`/`role=log`, progress panel, toast without modal | "Uploading 1 item", "Saved" toast |
| **C3a Benign decision** | Asks a choice; no option changes or loses data | `isDecisionDialog` and no option has `role: consequential` | "Open in app / Stay in browser", "Show tips? Yes / No" |
| **C3b Consequential decision** | Asks a choice; at least one option changes, replaces, deletes, pays or sends | `isDecisionDialog` and any option matches the fork-local consequential word list or the recorded step's `consequence` | "File exists: Replace / Keep both / Cancel", "Discard changes?" |
| **C4 Information request** | Needs a value the recording may not have | dialog with input fields (`hasFields`) | "Enter a reason", OTP box, rename dialog |
| **C5 Fault** | The app says it cannot go on as asked | see §6.2: login redirect, HTTP ≥ 400 on navigation, offline, `role=alert` / `aria-invalid` text with error words, permission/quota words | session expired, "You don't have access", network error, "already exists" error, validation error |
| **C6 Silent change** | Nothing visible popped up, but the page is not in the expected state | a required VERIFY assertion failed and no C1–C5 surface is present | redirect to another screen, empty list, button disabled, wrong tab state |

Rules that keep the classes honest:

- **Most cautious class wins.** A surface that matches several is filed under the later row in this order: C2 < C1 < C3a < C4 < C3b < C5. A modal with an error message and a "Retry" button is C5, not C3a.
- **Ambiguous is not benign.** If the consequential word list cannot rule an option out (no label, icon only, unknown language), the option is consequential.
- **One definition, two runtimes.** The classifier exists once in Python (`outcome_fork.py::classify_dialog`, extended) and once in JS (`fork.js`, extended) and both are tested against the same fixture, as today.

---

## 4. Provenance

| Provenance | Meaning | Source |
|---|---|---|
| **Recorded** | This workflow has a fork state, `try_dismiss` or plain step for this surface | the compiled skill |
| **Known** | Another workflow in the same app met it, or customers met it (field-only) | `app_knowledge.json`, with or without an author `app_policy` |
| **Unknown** | Nothing has seen it | — |

Matching a live surface to a recorded or known one uses the existing strict matcher (`fork.js::matchState`: every expected button present, compatible title, a tie is unknown). Signatures must be value-free on **both** title and message (today only the message is: see gap G2).

---

## 5. The decision table (runtime)

Read row = class, column = provenance. "Stop" always means: end the run cleanly, nothing changed after the last verified step, a plain-language reason, and "tell Conxa in the chat how to handle this" when the author could fix it.

| Class | Recorded | Known (map) | Unknown |
|---|---|---|---|
| **C1 Obstruction** | follow the compiled `try_dismiss` | Tier A dismiss (known close button) | leave alone; **close only if it blocks the next action**, with its own close control (×, Close, Escape) via Tier A, then Tier B safe-dismiss |
| **C2 Notice** | ignore | ignore | ignore (never clicked, never paused for) |
| **C3a Benign decision** | follow the fork policy (`auto` runs the approved option) | follow `app_policy`; none → stop | **stop**, reason "an unexpected question appeared"; logged to the field map |
| **C3b Consequential decision** | follow the fork policy; an option never runs unless `author_approved`; `ask_human`/`handover` only when the author chose it | `app_policy` with `author_approved`; none → stop | **put to the MCP client (Tier B)** unless a button looks irreversible, then **stop**; never closed by the runtime (§10 decision 1) |
| **C4 Information request** | fill from the bound input; missing input → `badInput` before the run starts | stop ("needs: <field labels>") | stop ("needs: <field labels>") |
| **C5 Fault** | **cancel the run** with a typed reason (`auth_expired`, `no_permission`, `network`, `rate_limited`, `duplicate`, `validation`, `not_found`) — unless the author recorded this exact error as an expected fork state | same | same; no Tier B round is spent |
| **C6 Silent change** | — | — | Tier A (timing) → Tier B (identity) as today; if Tier B also fails, stop naming the expected vs. observed state |

Three consequences worth stating:

- **Nothing pauses by default.** A pause happens only where a person asked for one while building the workflow (a reviewed fork `ask_human`, a `handover` step, an author `app_policy: ask_human`). Everything else continues, closes a blocking nuisance, or stops. (Unchanged rule; see memory "nothing pauses by default".)
- **"Unknown meaning" is resolved by the class, not by a guess.** An unknown surface that is only in the way is closed with its own close control. An unknown surface that asks a question is not closed, because Cancel, × or Escape is itself an answer in many apps ("Cancel" on "Replace?" means "don't upload").
- **Faults cancel.** Sign-in expiry, missing permission, session conflict, network error, duplicate-data error and changed account state end the run. Recovery is pointless (the element is fine; the world changed) and an LLM round would only burn tokens.

---

## 6. The pipeline, stage by stage

### 6.1 Recording

Unchanged capture, two additions:

1. **Every surface, not only dialogs.** When a step's `dom_diff` adds a `role=alert`/`aria-live`/toast/inline-error node, the recorder stamps a value-free `surface_context` (class hint, title words, button labels, `hasFields`) the same way `dialog_context` is stamped today. This is what lets C2/C5/C6 be recognised as *recorded* later.
2. **Observation, never inference.** The recorder still never writes branches. It only reports what the browser showed and whether the person's events were trusted (`origin`), as today.

### 6.2 Workflow understanding (before compile)

The merged video + browser timeline already flags `dialog: "decision"` rows (`timeline_understanding.py::must_ask`). Extend the row with the class from §3 so the understanding call gets "C3b decision popup with Replace / Keep both / Cancel" instead of only "decision".

The model's job here is **meaning only**: what the popup is about, what each button would do in plain words, and whether the workflow's goal implies one answer. It never sets the class or the policy. Its words go into the question text.

### 6.3 Clarification — ask only what evidence cannot answer

For each recorded surface, code first tries to answer the user's four questions from evidence. A question is shown only when the evidence leaves it open.

| Question | Answered without asking when… | Otherwise asked as |
|---|---|---|
| **Does it always appear?** | class C1/C2 (it does not matter); a variant take ran without it (`clean.observed`); the app map has seen the same action both with and without it | "Does this message box show up every time, or only sometimes?" → *Every time* / *Only sometimes* / *Not sure* (*Not sure* = *Only sometimes*) |
| **When does it appear?** | never asked as free text; only used as the question's explanation if the model can say it ("when a file with that name already exists") | — |
| **What does each option mean?** | the model's plain-word meaning, shown in the choices; never asked back to the person | — |
| **Does a different option change the rest?** | C1/C2/C3a with one recorded option: assume it rejoins; any option whose role is consequential: asked | "If someone picked *Keep both* instead, would the rest of the steps still be right?" → *Yes, same steps* / *No, stop there* / *I'll record that path* |
| **What should happen?** (the policy) | C1 → close it if it shows up (existing `popup_dismiss` guess); C2 → nothing | see choices below |

Choices for the policy question, fixed by code (the model only words them):

- **C3a / C3b** — one choice per option ("Always pick *Keep both*"), plus *Let whoever runs it decide each time* (`by_input`), *Hand it to a person* (`handover`), *Stop the run*.
  - Picking an option **is** the author's approval of it (same rule as `set_fork_policy` today). For a C3b option the card names what it does ("This replaces the existing file") before Confirm.
  - **Never pre-filled for C3b.** For C3a the recorded option may be shown as the suggestion; it still needs Confirm.
  - For C3b the harness lists *Hand it to a person* first and marks it recommended, so a consequential choice defaults toward a person — but it is added only when the person confirms it (this keeps "a pause exists only because someone asked for it").
- **C4** — becomes the existing value question per field ("Different every time / Always this one").
- **C5 recorded on purpose** (the author recorded an error path) — *This is expected, continue* / *Stop the run when this happens* (default).

The question cap (five) keeps its priority order: C3b first, then "does it always appear" for C3a, then C4 values. A question whose answer the evidence gives is not counted.

### 6.4 Compilation — what becomes deterministic

| Answer | Compiled shape |
|---|---|
| **Always appears** + an option chosen | plain recorded steps, **no fork**; the opener keeps its required "dialog present" assertion, so a run where it did *not* appear fails VERIFY as a C6 change instead of carrying on blind. This is Scenario 3. |
| **Sometimes** + option chosen | `outcome_fork`: state policy `auto` with `author_approved: true` for that option; `clean.observed: true`; the "no popup" path continues with the next recorded step. This is Scenario 2. |
| **Sometimes** + decide each time | fork state `by_input`; the option list becomes a required `select` input |
| **Hand it to a person** | fork state policy `ask_human` with `reviewed: true` (pauses only on that state); or a `handover` step when the person asked for a hand-over regardless of the popup |
| **Stop** | fork state policy `stop` with the author's message |
| **Other option changes the rest → stop there** | that option gets `then: "stop"` (new, §9 G4); the runtime ends the run cleanly after a person or input picks it |
| **Other option changes the rest → record it** | existing variant recording; a path that diverges for more than the popup is kept as its own state's `steps` and must rejoin at a recorded step key, or ends the run (`then: "end"`) |
| C1 nuisance | `try_dismiss` from the shared builder (unchanged) |
| C4 information | steps with input bindings via the understanding check (unchanged) |

Every option's tail is one of: **rejoin** (default: continue with the next recorded step), **end** (the run finished successfully on this path), **stop** (the run ends; nothing after it is safe). No option may jump to an arbitrary later step; jumps are what make branching programs hard to review.

### 6.5 Deterministic runtime

Per step, in this order (all zero-token):

1. **Before the action** — a pending native dialog blocks the step (existing). A C5 fault probe runs only on navigation steps and after failures, not before every step, to keep runs fast.
2. **After the action** — the fork watch window (existing, 1.5–8 s) for steps that have a fork; for every other consequential step, the required VERIFY assertion.
3. **Armed signatures** — a fork's states stay armed for the next two steps (gap G5), so a popup that opens late is still a *recorded* state rather than an unknown that blocks a click.
4. **On failure** — classify the page (§3), look up provenance (§4), apply the table (§5). Only C1 (blocking) and C6 reach Tier A; only C1-unknown and C6 reach Tier B.
5. **Loop guard** — a per-run counter keyed by the surface's value-free signature. The same surface handled (closed or answered) more than twice in one run stops the run as `surface_loop` (gap G6). The Tier B stagnation cap (2 rounds, unchanged fingerprint) and the retry budget (3 per step) stay as they are.

### 6.6 Tier B — reasoning, with a fence

Tier B is the agent-mediated round in `failure_response.js`. Its payload gains one block: the surface class and provenance the runtime already computed, plus the popup's title and buttons (value-free).

**What Tier B may return** (closed set, validated by the runtime):

| Field | Meaning | Runtime check |
|---|---|---|
| `candidate_index` / `selector` | "the recorded element is now this one" (C6 identity) | existing fingerprint + uniqueness margin gate |
| `dismiss` | "close this obstruction" (C1) | existing `isSafeDismissLabel`; refused on any `isDecisionDialog` surface |
| `verdict` *(new)* | its reading of the surface: `obstruction` / `notice` / `decision` / `fault` / `drift` plus one sentence | **may only move the class toward caution** (§3 order). A verdict of `fault` or `decision` on a C1 surface is honoured (stop). A verdict of `obstruction` on a C3 surface is ignored and logged. |

**What Tier B never does** (each one is a runtime refusal, not a prompt instruction):

- answer a decision popup, by label, index, Escape or ×;
- type into a C4 popup (it has no source of truth for the value);
- dismiss anything during a fault;
- act on a different tab than the step's recorded tab;
- run a consequential step the compiled workflow does not contain;
- get a third round on an unchanged page.

So Scenario 1 resolves as: an unrecorded popup that is only in the way is closed (Tier A for free, Tier B with the safe-label gate); an element that moved is re-found (Tier B, existing); an unrecorded question or fault stops the run with Tier B's sentence as the explanation, and the popup is reported to the app map so the author can give it a standing answer.

### 6.7 Human intervention

| When | Mechanism |
|---|---|
| Building the workflow | clarification questions (§6.3), Human Edit, chat |
| Running, and the author asked for it | reviewed fork `ask_human`, `handover` step, author `app_policy: ask_human` |
| Running, and nobody asked | **no pause.** Stop with the reason. The person who runs it sees "This popup needs a decision: … Tell Conxa in the chat how to handle it", and the author sees it in the field map. |

### 6.8 Learning loop (existing, wired to the classes)

`app_obs` telemetry already reports unplaced popups → field-only popups in the app map → the author sets an `app_policy` in Studio → the next publish ships it in `app_knowledge.json`. Additions: report the class with each observation, and count C5 faults per workflow step so a "this step keeps failing with *no permission*" pattern shows on the dashboard instead of looking like flakiness. Nothing learned in the field changes a runtime until an author publishes.

---

## 7. Worked scenarios

| Scenario | Class / provenance | Result |
|---|---|---|
| 1. Popup only at runtime, a promo modal | C1 / unknown | blocks a click → Tier A Escape/close → else Tier B safe dismiss → learned per host |
| 1b. Popup only at runtime, "Replace existing file?" | C3b / unknown (or known) | stop, nothing replaced; field map gets it; author sets a policy |
| 2. Recorded "Replace?" that sometimes appears | C3b / recorded | asked: sometimes? which option? other option changes rest? → fork, approved option, clean path continues |
| 3. Recorded "Confirm send?" that always appears, author picks Send | C3b / recorded | plain step with the opener's required "dialog present" assertion |
| Several popups possible after one action | C3 / recorded, several states | one fork with several states; strict matcher; a tie is unknown → stop |
| Popup asks for a reason not in the recording | C4 | value question → input; unrecorded at runtime → stop naming the field |
| Error toast "Name already taken" | C5 / any | cancel run as `duplicate` |
| Session expired | C5 | cancel run as `auth_expired` (existing fast path) |
| Popup keeps coming back after closing | C1 / any | third time in one run → stop as `surface_loop` |
| No popup, but the list is empty / redirected | C6 | Tier A → Tier B → stop naming expected vs. seen |
| Alternate legitimate state never recorded (e.g. "Keep both" path) | C3 / unknown at the option level | stop on that option (`then: stop`) until the author records the variant |

---

## 8. Safety invariants (new rules in **bold**, the rest already hold)

1. A decision popup is never answered on anyone's behalf (CLAUDE.md, unchanged).
2. A consequential option never runs without `author_approved`, written only by `set_fork_policy` or `cmd_set_app_popup_policy`.
3. **The surface class is decided by code. A model may only move it toward caution.**
4. **A fault (C5) cancels the run before any recovery and never spends a Tier B round.**
5. **An unknown surface is closed only when it blocks the next action, only with its own close control, and never when it is a decision, a form or a fault.**
6. **No option tail jumps to an arbitrary step: rejoin, end or stop.**
7. **The same surface handled more than twice in one run stops the run.**
8. Nothing pauses unless a person asked for that pause while building the workflow.
9. Everything that leaves the machine about a popup is value-free (`scrub.js` / `scrub.py`, SG-20).

---

## 9. Gaps to build (tracked as `TODO.md` EXEC-49)

| # | Gap | Layer | Main files | Size |
|---|---|---|---|---|
| G1 | One classifier for every surface (C1–C6, most-cautious-wins), Python + JS against one fixture; extend `decision_surface_cases.json` | compile + runtime | `outcome_fork.py::classify_dialog`, `fork.js::isDecisionDialog`, `page_scripts.js::dialogProbe` | M |
| G2 | Value-free **titles** in fork signatures: today `fork.js::titleCompatible` compares the raw title, so "Replace 'report.pdf'?" vs "Replace 'q3.xlsx'?" is *unknown*; strip quotes and digits like `messageTokens` already does, on both sides | compile + runtime | `fork.js`, `outcome_fork.py` | S |
| G3 | Clarification: "does it always appear?", option-by-option policy choices (an explicit pick is the approval), *Hand it to a person* first for C3b, evidence-based skipping. Changes the current rule that the pre-compile fork question offers generic choices only and replay never selects an `option:*` button — replay maps the picked label to the compiled option id after compile | understanding + replay | `timeline_understanding.py`, `clarify.py`, `conversation/replay.py`, `decisions.py` | M |
| G4 | Option tails `then: rejoin / end / stop`; "always appears" compiles to plain steps keeping the opener's required assertion | compile + runtime | `outcome_fork.py`, `run.js::runForkStep`, Backend-Schema §3.4j | M |
| G5 | Late popups: keep fork signatures armed for the next two steps (EXEC-44 item 2) | runtime | `run.js` | S |
| G6 | Per-run surface loop guard | runtime | `run.js` | S |
| G7 | Fault probe (C5): login redirect (existing), navigation status ≥ 400, offline, `role=alert`/`aria-invalid` with fault words; typed stop reasons; skipped Tier B | runtime | new `fault_probe.js`, `run.js`, `failure_response.js` | M |
| G8 | Tier B `verdict` field, caution-only validation, class block in the payload | runtime | `failure_response.js`, `server.js` (step_overrides parsing) | S |
| G9 | Record-time `surface_context` for toasts / inline errors | recorder | `bridge.js`, `session.py` allow-list, `RecordedEvent` | M |
| G10 | Field telemetry carries the class; C5 counts per step on the dashboard | runtime + cloud | `app_knowledge.js::observation`, `tracking_routes.py` | S |

Suggested order: G2 → G1 → G7 → G6/G5 → G3/G4 → G8 → G9/G10. G2 alone fixes a real miss on every popup whose title contains a file or record name.

---

## 10. Decisions taken (owner, 2026-11-05)

1. **Unrecorded consequential popup → Tier B decides.** An unrecorded question popup is put to the MCP client, which picks one of the popup's own visible buttons from the user's goal and asks the user in chat when unsure. Exception: if **any** button looks irreversible (delete, remove, erase, permanently, pay, buy, purchase, order, checkout, send, submit, publish, transfer, revoke) the run **stops**. This amends the old "never answered on anyone's behalf" rule for unrecorded popups only; recorded popups keep it. Build Studio's test uses the "let AI answer" switch for the same job. See SG-23 and TRD §11.4.
2. **Unknown or ambiguous popup → close only a nuisance that blocks.** Closed only when it is in the way (class C1), only with its own close control. An unknown popup that asks a question is never closed this way.
3. **Faults cancel the run.** Auth expiry, no permission, network, rate limit, duplicate data and rejected input end the run with a typed reason before any recovery. The existing mid-run re-sign-in stays.

## 11. What is built, and what is not

Built: P0 stop-flag fix; value-free titles (G2); one classifier (G1); fault stop (G7); loop guard and late popups (G5, G6); agent-answered popups and the caution-only verdict (G8); option tails and the follow-up question (G4, part of G3); record-time `surface_context` (G9); the class in field telemetry (G10).

Not built: the pre-compile fork question offering one choice per button; a "this popup always appears" answer that compiles to plain steps; counting Tier A dismissals toward the loop guard; a live end-to-end run against a real app.
