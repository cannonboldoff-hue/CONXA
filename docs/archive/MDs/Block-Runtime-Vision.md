# Blocks and the Execution File: The Lego Model for the Conxa Runtime

> **Status:** idea, gap analysis and decided plan. This is not a design that is already built.
> **Last updated:** 2026-10-30.
> **Based on a read of:** `runtime/app/run.js`, `runtime/app/handlers.js`, `runtime/app/step_utils.js`, `runtime/app/recovery_stage.js`, `docs/TRD.md` §10 and §11.

---

## 1. The idea in plain words

Think of Lego.

| Lego | Conxa |
|---|---|
| A box of bricks | **Blocks.** Small pieces of runtime code. Each does one job (click, fill, read a value, wait, ask a person). Written once. |
| The instruction sheet | **The execution file.** It holds no code. It says which blocks to use, in which order, with which settings. |
| The builder | **The runtime.** It reads the sheet and snaps the blocks together. |

The sheet is not fixed. It can adapt in three ways while the build happens:

1. **Inputs change it.** The runtime asks for inputs. The answers can turn blocks off or repeat them.
2. **Surprises change it.** A popup appears or a button moves. The runtime deals with it and carries on.
3. **Thinking can change it.** When a decision needs real judgement, the runtime stops and asks the AI assistant it is running inside (the MCP client). It carries on with the answer.

One sentence: **the runtime is a box of blocks, the workflow is a sheet that arranges them, and the arrangement can adapt to inputs, surprises and judgement.**

---

## 2. Why this is a good idea

| Benefit | What it means |
|---|---|
| New workflows need no new runtime code | A new workflow is a new sheet that reuses the same blocks. |
| Blocks get well tested | Each block is small, so it can be tested alone and trusted everywhere. |
| Fixes help everyone | Improve the "click" block once and every workflow gets better. |
| One sheet, many behaviours | The same sheet behaves differently for different inputs. |
| Safer | Dangerous blocks (send, delete, pay) can be fenced with rules in one place. |

---

## 3. What Conxa already has

Conxa is closer to this model than it may seem.

### 3.1 The blocks

Every compiled step has a `type`. A table (`HANDLERS` in `runtime/app/handlers.js`) maps each type to its code. A few control blocks live directly in `runtime/app/run.js`.

**Page-action blocks**

| Group | Blocks |
|---|---|
| Move around | `navigate`, `browser_back`, `browser_forward`, `scroll`, `wait` |
| Type and click | `fill`, `type`, `click`, `dblclick`, `right_click`, `hover`, `focus`, `keyboard_shortcut`, `drag_drop` |
| Pick things | `select`, `select_option`, `set_checkbox`, `set_radio`, `date_pick` |
| Files | `upload`, `download_observed` |
| Read and check | `extract`, `check`, `assert`, `screenshot` |
| Dialogs | `dialog_accept`, `dialog_dismiss` |
| Tabs | `tab_open`, `tab_switch`, `popup` |
| Conditional | `if_present`, `try_dismiss`, `wait_for_one_of` |

**Control blocks (these shape the run)**

| Block | What it does |
|---|---|
| `for_each` | Repeats a group of blocks for every row or item. It must have a `max_iterations` cap. |
| `outcome_fork` | "The app is now in one of several known states." Runs the group that matches. |
| `wait_for_one_of` with `flow: true` | A variant branch. The rest of the workflow can go several ways. |
| `ai_review` | A planned pause where the MCP client's model thinks and answers. |
| `handover` | A planned pause for a person (2FA, CAPTCHA, e-signature). |

### 3.2 The instruction sheet

The compiled **SkillPackage** is the sheet. It is a list of steps. Each step names its block (`type`), its target, its inputs (`{{placeholders}}`) and its rules.

### 3.3 Inputs changing the arrangement

- `run_if` on a step: "run this only if input X is not empty, or equals Y." It reads inputs only, never the page. A malformed condition means the step runs, so a bad rule can never silently remove a step.
- `for_each` driven by an input list (`step.items`).
- `{{placeholders}}` in values and URLs.
- `execute_sequence` chains several skills.

### 3.4 Surprises changing the arrangement

- **Recovery cascade.** Tier A (zero LLM tokens) retries, scrolls and dismisses known overlays. Tier B hands the problem to the agent, which can send back `step_overrides` (a fix for the failed step).
- **Planned branches.** `try_dismiss` and `outcome_fork` let the sheet plan for a popup that may or may not appear.
- **Advisory checks before the run.** Drift and environment checks warn but never block.

### 3.5 Thinking by the MCP client

The runtime never calls a model itself. It responds to the client, and the client resumes. This is already how `ai_review` and Tier B work:

| Part | What happens today |
|---|---|
| The block | `ai_review`: an author-placed step with a prompt, an optional `output_schema` and `on_failure` (`abort`, `use_default`, `continue`). |
| Asking | `run.js` throws a pause. `server.js` parks the page and returns a request: the prompt, a screenshot, a live page inventory and the exact call to resume with. |
| Answering | The client calls `execute_skill` again with `review_results`. Any MCP caller may answer. |
| Checking | The answer is validated against `output_schema` and the page is checked for drift. A bad answer gets up to 2 re-asks. |
| Landing | The answer is saved as a named value in the run's inputs, so later steps can use it. |
| Cost | The tokens are the customer's, not Conxa's. |

---

## 4. The gap: what is different from the full picture

Today the sheet is **mostly fixed at compile time**. Inputs and surprises only trigger branches that someone planned in advance. The runtime does not freely re-plan.

| Full picture | Today | Gap |
|---|---|---|
| Inputs reshape the plan up front | Inputs are read as each step runs (`run_if`, `for_each`, placeholders). | The runtime never says "given these inputs, my plan is X" before it starts. |
| Surprises change the arrangement | Recovery re-finds the same element, or the agent patches one step. | Recovery repairs a step. It cannot add, remove or reorder later blocks. |
| Judgement can choose a path | `ai_review` can supply a value. | Branching on an `ai_review` answer is **not built** (TRD §11.3: branches look at the page, not at the run's inputs). |
| All blocks have one shape | Page-action blocks are in one table. Control blocks are special cases inside `executeOneStep`. | Two kinds of block. A new control block means editing the core loop. |

---

## 5. Decisions made (2026-10-30)

| # | Question | Decision | Meaning |
|---|---|---|---|
| 1 | Plan before the run, or re-arrange during the run first? | **Plan first** | Before the browser opens, the runtime works out the real step list from the sheet plus the inputs, shows it, and asks for missing inputs. Live re-arranging is a later phase. |
| 2 | How much power do inputs have? | **Skip and repeat only** | An input may switch a step off (`run_if`) or repeat a group (`for_each`). It may not choose between whole alternative paths. |
| 3 | What happens when code cannot fix a surprise? | **Client's model first, then a person** | Same order as today: code (free), then the agent (two rounds, costs tokens), then a person. A real decision goes to a person at once. |
| 4 | Who sets the list of allowed live edits? | **Conxa fixes the list** | One short list in the runtime and in the TRD. Same rules for every customer. Authors cannot add to it. |

**Ruled out for now**

- Inputs choosing between paths. A "card or bank" choice stays an author-built `outcome_fork` or `wait_for_one_of`.
- An agent adding or removing blocks. It may still nominate a new target for a failed step.
- Per-workflow switches for live edits in Build Studio.
- Refactoring `executeOneStep`. The uniform block table waits until the plan view has shipped.

**Left open**

- Whether live re-arranging (phase 2) is wanted once the plan view exists. Decide after phase 1 is in use.

---

## 6. Who decides a change

Every re-arrangement is decided by one of three deciders. Use them in this order and never skip ahead.

1. **Code.** Deterministic and free. Preferred. Example: an overlay is in the way and its close button is known.
2. **The MCP client's model.** Costs the customer tokens. Used only after code has failed, or at an explicit `ai_review` block where judgement is the whole point.
3. **A person.** For anything that decides something real: a "Replace or Delete" popup, a payment, a send.

### Rules for the model as decider

- **Tier A stays zero-token.** The model is for `ai_review` and Tier B only. It is never a silent fallback inside element finding.
- **The answer must be small and checked.** Use `output_schema` with fixed fields or choices, never free text that a later step types into a page.
- **A model answer never feeds a destructive step.** `patch_gate` already refuses a destructive step straight after an `ai_review`, except a confirm-only `{confirmed: boolean}` check.
- **A model never answers a consequential decision popup.** That stays with a person or the author's recorded approval.
- **Treat everything read from a page as untrusted.** Page text goes to the model, so a malicious page can try to give it orders (prompt injection). The defence is a small schema, a validated answer, no destructive step directly after, and a person for anything consequential.

### Limits of "the client will think for us"

- **No client, no thinker.** A scheduled or unattended run (`scheduler_daemon.js`) may have nobody to answer. A thinking block in such a workflow needs a fallback: `use_default`, `abort`, or park until someone answers. The plan should warn about this before the run.
- **Quality varies by client.** The schema check catches the shape of an answer, not its wisdom.
- **Cost and delay land on the customer.** Each pause is a round trip and tokens on their side. Use a thinking block only where code cannot decide.
- **Studio tests answer differently.** The Studio sandbox has no agent. It answers through the metered `human_edit` proxy (max 5 pauses per test). A test can differ from a real run.

---

## 7. Rules that must not break

These come from the Key Invariants in `CLAUDE.md`. More freedom means more ways to do damage, so each rule matters more under this model.

1. **Tier A recovery costs zero LLM tokens.** A live change chosen by code must not secretly call a model.
2. **No blind element guessing.** The resolver never picks `candidate[0]`. A swapped block still passes the same uniqueness check.
3. **A decision popup is never answered on anyone's behalf.** No live edit may auto-click Replace, Delete or Overwrite.
4. **A step never runs on a tab other than the one it was recorded on.**
5. **An input can never switch off a guard.** `run_if` is refused on `ai_review`, `handover` and outcome forks. Any new "inputs change the plan" feature keeps that.
6. **Loops need a cap.** No uncapped `for_each`.
7. **The cloud does not compile or execute.** All re-planning happens on the customer's machine.
8. **Auth files never go into build output.**

---

## 8. Worked example

**Workflow:** create an invoice in an accounting app.

```
1. navigate  -> /invoices/new
2. fill      -> customer = {{customer}}
3. for_each  -> {{line_items}}  (max 50)
     fill  description
     fill  amount
4. fill      -> discount = {{discount}}      run_if discount not_empty
5. click     -> Save                         (destructive)
6. outcome_fork: "Duplicate invoice found" -> [stop | ask a person]
7. extract   -> invoice_number
```

**Inputs:** `customer=Acme`, `line_items="Design,Hosting,Support"`, `discount=""`.

- **Plan before the run (phase 1):** step 3 becomes 3 rounds. Step 4 is skipped because `discount` is empty. The runtime reports the plan and asks for any missing input before the browser opens.
- **Surprise A, a cookie banner covers the form:** code (Tier A) closes it. Zero tokens.
- **Surprise B, the Save button moved:** code retries and re-finds it. If that fails, the agent nominates the new element and the runtime checks it before using it.
- **Surprise C, "Duplicate invoice found. Replace / Keep both / Cancel":** the runtime does not choose. It pauses for a person, because Replace is consequential.
- **Judgement, if the author adds one:** an `ai_review` could return `{ "looks_like_duplicate": true | false }`. The answer is checked against the schema. It may gate whether step 5 runs (a confirm-only check), and it never supplies what step 5 acts on.

---

## 9. The plan

### Phase 1: Plan first (build now)

Goal: before the browser is touched, the runtime knows and can say exactly which steps this run will do.

| Step | What | Why | Risk |
|---|---|---|---|
| 1.1 | Write one pure function, the **effective plan**. Input: the step list plus the run's inputs. Output: the steps that will run, the steps skipped by `run_if` (with the input name), the loop counts that come from input lists, the `{{placeholders}}` with no value, and the thinking and hand-over pauses the run will hit. It opens no browser. | Pure addition. It reuses `runIfDecision` and the loop item splitting that already exist, so the plan and the real run cannot disagree. | Low |
| 1.2 | Put the plan in the run result and the log, in plain words ("14 steps, 3 skipped because `coupon` is empty, 2 pauses for the client to think"). | The caller sees the plan change with the inputs, and knows the cost and delay before starting. | Low |
| 1.3 | Check missing required inputs against the plan before the browser opens. Return the same "missing input" answer the runtime gives today. | Fails early instead of halfway through. | Low |
| 1.4 | Warn when a scheduled or unattended run contains a thinking or hand-over pause with no fallback. | See section 6, "No client, no thinker". | Low |
| 1.5 | Add a unit test that runs the plan function and the real step loop on the same sheet and inputs and asserts they skip and repeat the same steps. | The plan must never drift from the real run. | Low |
| 1.6 | Update the docs: `docs/TRD.md` (a short section near §4.6 or §10) and `docs/App-Flow.md` (execute flow). | Project rule: a doc that drifts is worse than none. | None |

Limits for phase 1:

- Inputs may only skip and repeat. The plan function adds no way for an input to choose a path.
- `run_if` stays refused on `ai_review`, `handover` and outcome forks.
- Loops stay capped. A loop sized by the live page (a row scan) shows as "unknown until run". It is not guessed.
- The plan only describes what the inputs decide. What the page does is found out at run time.

### Phase 2: Fixed live-edit list (document first, build later)

| Step | What | Why | Risk |
|---|---|---|---|
| 2.1 | Write the Conxa-owned list of allowed live edits into `docs/TRD.md`. Start with zero-token edits only (table below). | Agree the fence before any code. Decision 4. | None |
| 2.2 | Make sure every live edit writes one line to the recovery log and shows in the run result. Most of this exists (`appendRecoveryEvent`, `tierb_overlay_dismissed`, `overlay_dismiss_rejected`, `recovery_stagnant_stop`). Fill the gaps. | A re-arranged run must always be explainable afterwards. | Low |
| 2.3 | Keep the failure order from decision 3. Add nothing new. | Already how the cascade works. | None |

Starting list of live edits:

| Live edit | Decided by | Cost |
|---|---|---|
| Close a known overlay (`try_dismiss`, learned dismissals) | Code (Tier A) | Zero tokens |
| Skip a step whose target is provably absent (`on_missing: "skip"`) | Code | Zero tokens |
| Insert a pause for a person | Code | Zero tokens |
| Replace one failed step's target (`step_overrides`) | The client's model (Tier B) | Tokens |
| Insert or remove arbitrary blocks | **Not allowed** until a new decision is recorded | n/a |

Every live edit is logged, bounded (a cap on how many) and shown in the run result.

### Phase 3: Only if wanted after phase 1 is in use

| Step | What | Gate before starting |
|---|---|---|
| 3.1 | Give every block one shape and move the control blocks into the same table as the page-action blocks (see below). | Phase 1 shipped. `gate_replay.js` and `test_invariants.js` green. A written reason why the refactor is worth the risk. |
| 3.2 | Any live edit beyond the phase 2 list. | A new decision recorded in section 5. |
| 3.3 | Branching on an `ai_review` answer. | First check whether `run_if` on a later ordinary step covers enough. |

The uniform block shape that step 3.1 aims for:

```
block = {
  name:       "click",
  run:        async (ctx, step) => ...,
  risk:       "safe" | "retry-unsafe" | "irreversible",   // today: NON_IDEMPOTENT_STEP_TYPES + step.destructive
  needsPage:  true | false,
  canPause:   true | false
}
```

Adding a block would then mean adding one entry, with the core loop untouched. The cost is that `executeOneStep` is large and well tested, so moving the control blocks is a real refactor.

---

## 10. Does this cover every browser workflow? No.

The block model is a good **shape**. It does not guarantee that every browser workflow can be automated. Coverage depends on whether the right blocks exist and whether the website lets a machine do the job at all.

### 10.1 What it handles well

Sequences of ordinary page actions on a normal website: sign in, fill forms, click through menus, pick dates and options, upload and download files, work across tabs and iframes, repeat for a list of rows, handle known popups, and pull in a person or the client's model for a real decision. Recording captures page elements, including iframe chains and shadow-DOM paths, so these replay without a model.

### 10.2 The nine missing blocks: what was decided and built (2026-10-30)

Status: **built** unless noted. Tests: `runtime/test/unit/test_ai_review.js`, `test_wait_until.js`, `test_clipboard.js`, `test_call_workflow.js` and `conxa-cloud/tests/test_ai_review_gates_run_if.py`, `test_wait_until_step.py`, `test_clipboard_step.py`, `test_call_workflow_step.py`. Reference: `docs/TRD.md` §11.3, §11.4 and §11.6.

| Missing block | How it is covered | Notes |
|---|---|---|
| **Set or compute a value** | The MCP client's model, through `ai_review` | The answer's plain fields are now bound as `<name>_<field>`, so later steps use `{{x_value}}`. A named answer is reported in `outputs`. |
| **Read a downloaded file** | The MCP client's model, through `ai_review` | `ai_review_attach` inlines a text file (csv, tsv, txt, json, md, log; 64 KB) from this run's downloads, framed as untrusted data. PDF is **not built** (TODO). |
| **If / else on page content** | `ai_review` + `run_if` on later steps | `run_if` reads the answer field. A destructive step cannot be gated by a free-form answer (gate refuses it). Numeric comparison and branching of `if_present` / forks on an answer are **not built** (TODO). |
| **Human approve or reject** | `handover` (decision) | A person who approves clicks Continue; one who does not cancels the run. No separate reject outcome. A confirm-only `ai_review` is the model's yes/no, not a person's. |
| **Wait until a time or event** | New `wait_until` step | Events only, up to 120 s. Clock times and long waits are **not built**; they belong to the scheduler. |
| **Call another workflow** | New `call_workflow` step | Version pinned at build, circle and depth checked, no pause inside, scoped inputs and results. |
| **Clipboard** | New `clipboard_write` / `clipboard_read` steps | Browser clipboard only, which is the computer's clipboard (see `docs/Security.md` SG-22). |
| **Call an API** | Not decided | Brings in secrets; needs its own decision. |
| **Read a code from email or SMS** | Not decided | Today it goes to `handover`; a model with its own mailbox tool could answer an `ai_review`. |

### 10.3 Limits that blocks cannot fix

| Situation | Why blocks do not help | What to do |
|---|---|---|
| **CAPTCHA and bot detection** | The site is built to stop machines. | Hand over to a person. Do not try to defeat it. |
| **Apps drawn on a canvas** (some design tools, maps, games, remote desktops) | There are no page elements to target. | Not automatable this way. Coordinate clicking is fragile. |
| **Native desktop windows outside the browser** | The runtime drives the browser, not the operating system. | Out of scope unless a separate desktop product is planned. |
| **Sites that change constantly or A/B test heavily** | Recorded identity goes stale. | Recovery and drift detection reduce the cost. They do not remove it. |
| **Unattended runs that need judgement** | There is no client to think. | Use a fallback (`use_default`, `abort`) or run with a client present. |
| **Terms of service that forbid automation** | A legal limit, not a technical one. | The customer's call. Say so in sales material. |
| **Actions that cannot be undone** | A wrong run does real damage. | Keep dry run, the destructive guard and `outcome_fork` author approval. |

Judgement-heavy work is **not** on this list when a client is present. See section 3.5.

### 10.4 What we can claim

**Safe:** "Most routine, repeatable workflows on normal websites can be recorded and replayed locally, with a person or the connected AI assistant brought in for sign-in challenges and real decisions."

**Not safe:** "Every workflow in a browser can be automated." CAPTCHA, canvas apps, desktop apps and unattended judgement are outside it, and the missing blocks in 10.2 still limit what can be built today.

### 10.5 Suggestions to add (none built; each is a candidate for `TODO.md`)

In priority order:

1. **A coverage check at record time.** Flag steps likely to be a problem (a CAPTCHA, a canvas, an OS-level dialog) as soon as someone records them, so promises stay honest.
2. **A "set value" block.** Cheap, code-only and safe. It makes the phase 1 plan view much more useful.
3. **A "call another workflow" block.** Real reuse is the strongest argument for the block model.
4. **A "read downloaded file" block.** CSV first, PDF later.
5. **A safety label on every block** (safe, retry-unsafe, irreversible), added before any new block so each arrives with its risk declared.
6. **A public "what we can and cannot automate" page** built from 10.3 and 10.4.

Decide which belong in phase 1 or phase 2. Until then they are suggestions, not commitments.

---

## 11. Open questions

1. Is live re-arranging (phase 2 and beyond) still wanted once the plan view is in use?
2. For scheduled runs with a thinking or hand-over pause, which default fallback: `use_default`, `abort`, or park until someone answers?
3. Which of the suggestions in 10.5 go into phase 1 or phase 2?
4. Can `run_if` on a later ordinary step cover "choose by an `ai_review` answer", or does that need real branching?

---

## 12. Files to read first

| Topic | File |
|---|---|
| Step loop and control blocks | `runtime/app/run.js` (`executeOneStep`, `runPlan`, `runForEachStep`, `runForkStep`) |
| Page-action block table | `runtime/app/handlers.js` (`HANDLERS`) |
| Retry safety of blocks | `runtime/app/step_utils.js` (`NON_IDEMPOTENT_STEP_TYPES`) |
| Recovery cascade | `runtime/app/cascade.js`, `runtime/app/recovery_stage.js`, `docs/TRD.md` §10.1 |
| Thinking pause (`ai_review`) | `runtime/app/review_pause.js`, `docs/TRD.md` §11.3 |
| Person pause (`handover`) | `runtime/app/handover.js`, `docs/TRD.md` §11.4 |
| Decision popups | `runtime/app/fork.js`, `docs/TRD.md` §11 |
| Scheduling | `runtime/app/scheduler_daemon.js`, `runtime/app/cron_lite.js` |
