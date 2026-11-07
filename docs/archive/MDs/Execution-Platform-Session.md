# Conxa Execution Platform — session capture

**Date:** 2026-09-03 → 2026-09-04  
**Status:** Direction plus **v0.1 scaffold in-repo** (`conxa-execute/`): form-and-run + vendored OpenCode loop (not a binary-only pin). Not a PRD amendment yet.  
**Related:** [`docs/PRD.md`](PRD.md) §7 (MCP), §11 (pricing), §14 (horizons); [`TODO.md`](../TODO.md) **PROD-19**; [`docs/CONXA_Cinematic_Script.pdf`](CONXA_Cinematic_Script.pdf); [`docs/cost_model.md`](cost_model.md)

This note records one working session: whether Conxa should ship its own execution app (instead of depending on Claude Desktop), which open-source shells to reuse, how that fits the PRD and the cinematic film, and what to build first so a small company with no AI subscription can still run skills.

---

## 1. The problem that started the conversation

Today a customer cannot run a compiled skill unless they also have an MCP host — in practice a paid Claude, Codex, or similar subscription. That:

- Makes someone else’s product a hard prerequisite for ours
- Caps the market at people who already pay an AI vendor
- Gives the most valuable part of the loop (the brain that heals a broken step at Tier B) to a competitor

The opening idea was: ship a separate **CONXA.exe**, like Claude Desktop, and use the existing cloud LLM proxy with **Kimi K3** as a cheaper company-provided model. If the company already has API keys, they should be able to use those instead.

That direction is already written as **PROD-19** (agreed 2026-09-01): own harness, hosted model via the existing proxy (starting with Kimi K3), bring-your-own key, in-app credits. This session refined *what that product actually is* and *what it must not become*.

---

## 2. What we already have (do not rebuild)

Conxa already ships an executor:

- **`conxa-runtime.exe`** — host + app layer, Playwright, resolver, recovery, MCP server
- **Installer** — registers MCP into 20+ hosts (Claude Desktop, Cursor, Codex, OpenCode, and others)
- **Cloud LLM proxy** — OpenAI-compatible pool, metering, Cashfree already wired
- **Scheduler daemon** — already an MCP *client* over stdio (proof that Claude Desktop is not required to *drive* the runtime)

Claude Desktop is only the **caller**. Skills run locally. A new Conxa app should be another caller of the same runtime, not a rewrite of recording, compile, browser, resolver, or recovery.

**Invariant (unchanged):** the cloud does not execute. Tokens may go to the proxy; clicks stay on the customer machine.

**Invariant (PRD §7):** MCP is a **distribution property**. A skill recorded once is callable from whichever agent the customer already uses. Conxa’s own app is *one* host among many. If we become “only our app,” we reintroduce the vendor-lock the MCP design was meant to avoid.

---

## 3. What not to build

| Idea | Why not |
|---|---|
| A second Claude Desktop named CONXA.exe | We would maintain a general assistant on someone else’s weights; the film and the PRD are about *work items and judgement*, not chat |
| Fork or wrap Claude Desktop | Proprietary; still Anthropic’s app, model, and terms |
| LibreChat as the product | Full chat platform (Docker, DB, accounts, plugins). Ops burden, huge attack surface, web shape, not a Windows skill installer |
| Fork Cherry Studio and rebrand | Looks right for operators; **AGPL + extra commercial terms** make shipping a modified CONXA.exe without opening source (or buying a Cherry license) a legal trap. Huge extra surface (RAG, translate, marketplace) |
| 5ire over Cherry or OpenCode | Same bet as Cherry, less polish, unclear license (“Other” on GitHub) |
| Pi as the Conxa harness | Excellent small coding-agent loop; **author refuses native MCP**. Conxa talks MCP. Community adapters exist; that is a second protocol to maintain |
| Cloud-hosted browser / Conxa-hosted execution | Explicitly rejected in PRD §14.5 (2026-08-21). Breaks security, pricing, and the film’s “empty desk, local machine” proof |
| A second Playwright/Chromium stack “for the chat app” | Studio records in Playwright; the runtime already drives Chromium. The platform **attaches a window to that session** |
| Unmetered hosted Kimi on the happy path | Creates a per-run cost while the PRD says executions are free because *their* agent pays. Needs an explicit pricing amendment (token pool / credits), not silence |

---

## 4. Open-source shells compared

### 4.1 5ire vs Cherry Studio vs OpenCode

They are not three flavors of the same thing.

| | **5ire** | **Cherry Studio** | **OpenCode** |
|---|---|---|---|
| What it is | Electron chat + MCP | Electron “AI studio” (Claude Desktop lookalike) | Agent **loop** (TUI + desktop + IDE) |
| Windows exe | Yes | Yes | Yes (desktop newer; TUI is the real product) |
| Kimi / custom proxy | Yes | Yes (strong OpenAI-compatible / CN providers) | Yes |
| MCP | Yes (`mcp.json`) | Yes, mostly **inside the app**, not a file our installer already writes | Yes (`opencode.json`) — **already in `register-mcp`** |
| License if we bundle / rebrand | Unclear | **AGPL + commercial terms** (orgs >10 people / modified closed-source distribution) | **MIT** — cleanest to pin |
| In Conxa today | No | No | **Yes** |
| Fit | Skip as the platform | Support as a *customer-chosen host*; do not fork | Right **engine** if locked down (MCP only, no shell/file write) |

**Ranking for Conxa:** OpenCode to *build on* (constrained). Cherry to *support* (new host row later). 5ire: skip.

**OpenCode caveat:** default OpenCode can edit files and run a shell. Next to a runtime that drives a logged-in browser, that is a foot-gun. Bundle or pin only with Conxa MCP tools on, built-in write/shell **off**, system prompt = skills + inputs + recovery.

### 4.2 Cherry Studio vs OpenCode vs LibreChat vs Pi

Pi here is **Mario Zechner’s Pi coding agent** (`pi-mono`), not Inflection’s chatbot.

| | **Cherry Studio** | **OpenCode** | **LibreChat** | **Pi** |
|---|---|---|---|---|
| Category | Desktop AI client | Agent harness | Self-hosted ChatGPT-style **web** platform | Minimal coding-agent loop |
| UI | Native Electron | Terminal first | Browser (`localhost:3080`) | Terminal |
| Deploy | Installer | Local binary | Docker + DB + YAML | One CLI |
| MCP | Native | Native; already registered | Native (`librechat.yaml`); stdio from Docker is awkward | **Deliberately none** (context-window argument). Adapters exist, not first-party |
| Tool loop for `execute_skill` / Tier B | Chat + tools | **This is the product** | Agents + MCP; heavy | Excellent for bash/files; Conxa tools only via adapter or CLI wrap |
| Extra surface | RAG, translate, marketplace | Shell, files, LSP | Accounts, plugins, interpreter | Small; still shell + files |
| Bundle license | AGPL / commercial | MIT | MIT | Usually MIT — re-check `pi-mono` before shipping |
| Ops | Their updates vs ours | Pin a version | We operate a chat SaaS on their box | Tiny + adapter forever |
| As CONXA.exe | Looks right; **do not fork** | Right engine, wrong to dump unmodified | Wrong shape (server) | Wrong protocol |
| As supported host | Strong (add a host row) | Already supported | Internal ops console maybe | Weak |

**Verdict:** OpenCode for the harness. Cherry as an optional MCP host. Skip LibreChat and Pi for the customer execution product.

---

## 5. What the PRD actually commits to

Read horizon labels literally. Horizon 1 is what a customer can buy. Horizons 2 and 3 are direction, not a salesperson’s present tense.

**Doctrine (settled 2026-08-21):** *Conxa ships capability to where the work and the data already are. The cloud holds neither.*

| Horizon | Meaning | Implication for this app |
|---|---|---|
| **1 — Learn and Execute** *(current)* | *Let it do the work while a person is there.* Record, compile, execute on the machine, MCP into agents they already have. Human review points are a **current** requirement (§8, §14.4). | Company front door + optional cheap/BYO brain. Person starts the run. |
| **2 — Scale** *(future)* | Queue of **work items**, customer-owned **workers**, human review becomes a **list** not a paused chat. Unattended session lifetime is still **open** (§14.5). | Same runtime, same MCP. Scheduler / overnight / empty-office film ending wait on session lifetime. Do **not** build the queue as a cloud product. |
| **3 — Understand** *(long-term)* | Intelligence **on customer infrastructure**, querying execution history. It becomes **another MCP caller** of the same skills. Film marks this **roadmap**. | Do not ship Vikram’s churn chat as present-tense product. |

**§14.4 rule:** Horizon 1 wins on **scope**; later horizons win on **shape**. We do not build the queue now. We make sure what we build now can sit behind one later (work-item-shaped telemetry, recorded review points, durable pause/resume).

**§11 pricing tension:** “Pay for reach, not for runs.” Executions are unlimited because they run on the customer’s machine and Tier B historically runs on **their** Claude bill. A hosted Kimi brain **creates a per-run (per-token) cost**. That is not forbidden, but it is a **PRD/pricing amendment**: operator seat + token pool or credits — not silent unmetered Kimi. BYO key keeps the old economics (we bill $0 for tokens).

**§14.4 foundations that must exist before Neha’s swipe UI is real:**

1. Human review points recorded, compiled, and paused  
2. Durable, resumable execution state  
3. Work-item identity (today a run is still “one skill invocation”)  
4. Structured telemetry that can become an operating picture  
5. Process metadata, ownership, dependencies (partly later)

Without (1)–(2), a swipeable review stack is a demo on fake cards.

The cinematic script’s Appendix B already warns: the film is **internal-enterprise, one company, own ERP**. Vendor-distribution (branded installers) is a later channel. Keep website, pricing, and this app saying the same thing.

---

## 6. The buyer this app is for

Not “everyone should leave Claude.” The wedge:

- Manufacturing (or similar) company, automation is a **small** part of the business  
- Team of ~10; **two people** will actually operate Conxa  
- They **do not** have Claude/Codex/ChatGPT subscriptions and will not take one just to run a portal workflow  
- If they **do** have API keys (OpenAI, Azure, etc.), they should paste them and not pay Conxa for tokens  
- Later they may buy a real agent; skills must still run there via MCP with **no migration**

**Product they need:** installer → two operators can run skills → optional cheap brain from Conxa → or their own keys. Not a general assistant.

**Role split (film + PRD operator seat):**

- **Neha** — operations. Whole job can be **human review / exceptions**. Does not record or compile.  
- **Arjun** — Build Studio. Repairs, publish.  
- **Vikram** — Intelligence / deploy. Roadmap.

A cheaper **operator seat** (run + review, no Studio) is the Horizon 2 pricing sketch; it is the right commercial shape for those two of ten people even in Horizon 1.

---

## 7. North-star UX (cinematic script — Neha)

Source of truth for *feel*: [`docs/CONXA_Cinematic_Script.pdf`](CONXA_Cinematic_Script.pdf) Revision 2. UI is never a fullscreen screenshot; it is anchored to a real monitor. Execution is always shown on a **physical device**. Phone commands must cut to the **desktop doing the work** (Scene 20 / Shot 27). Cloud distributes packages; it never executes.

**Named surface in the film:** **CONXA EXECUTION PLATFORM** (Scene 17).

**Neha’s morning (Horizon 2 *shape*, honest volumes):**

- Brief: overnight complete — e.g. 340 / 322 succeeded / **12 human review** / 6 need repair (script already reduced from 1,432 so it does not claim Scale we cannot run)  
- She chooses **reviews first**  
- Queue of cards (GST, vendor mismatch, approval, document check)  
- **Chromium emerges from the Conxa UI** (Shot 10) — live page + issue + Approve  
- Card collapses; next rises  
- “Workflow resumed in the background”  
- Repairs are Arjun’s, not hers  
- High-level command: one sentence → plan → Go ahead (Scene 18)  
- She leaves; machine continues; lunch notification; thumb: hold 3 exceptions, start afternoon batch; **empty desk, running locally**  
- After hours: execute / recover / wait for review on the same workstation  

**Director’s after-image:** Human → Decision; AI → Execution; Human → Exception; AI → Execution; Human → Life.

Claude Cowork analogy: the **paused (or live) workflow’s page lives inside the product**, not a second Chrome window the operator hunts for. That is not a second browser engine; it is a window onto the runtime’s existing Playwright session.

MCP clients (Claude, Cursor, OpenCode) keep calling the same executor. They will never get the review stack, the live page, or “exceptions is my job.” That gap *is* the Conxa-owned UX advantage.

**Intelligence (Scene 13):** mandatory on-screen “available after ~6 months of execution history.” Enterprise cut of the film may omit it. Do not implement it as v1 of the chat app.

---

## 8. Architecture we agreed

```
┌─────────────────────────────────────────────────────────────┐
│  Conxa Execution Platform (this app)                         │
│  Chat mode · Form-and-run · Review queue (later)             │
│  Embedded view of the runtime’s browser (later)              │
│  BYO key  ·  later: hosted Kimi via existing LLM proxy       │
└───────────────────────────┬─────────────────────────────────┘
                            │ MCP stdio (same as Claude Desktop)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  conxa-runtime.exe  ← THE EXECUTOR (already ships)           │
│  Playwright / Chromium · skills · Tier A recovery            │
│  Also registered into Claude, Cursor, Codex, OpenCode, …     │
└─────────────────────────────────────────────────────────────┘
                            │ telemetry / skill sync / optional tokens
                            ▼
                      Conxa Cloud (coordinate, never execute)
```

Three layers:

1. **Executor** — today’s runtime. Do not duplicate.  
2. **Company platform** — our UI.  
3. **Browser in the UI** — attach to the run’s page when watching or reviewing; do not fork Chromium.

Chat is **how you start work** (and how a model maps language → skill + inputs). The **job** is the queue and the live page.

---

## 9. What to ship as “Conxa Chat App” now (v0.1)

**Positioning:** ship a chat-capable app that **current clients can use**, with a shell we can grow into Neha’s platform. Prefer window title **Conxa** / **Conxa Execute**; Chat is a **mode**, not the category. “Chat app” in the installer trains people to expect Claude.

**v0.1 — clients can use this without Claude:**

1. Windows app beside the runtime.  
2. MCP client: `list_skills`, `get_skill_inputs`, `execute_skill`, status/cancel. Start or attach to installed `conxa-runtime.exe`. If runtime missing, say so — do not reimplement execution.  
3. **Form-and-run:** pick skill → fill declared inputs → Run. **No model, no keys.** Unblocks the plant with two operators.  
4. **Chat (optional):** natural language → model maps to skill + inputs → same `execute_skill`. **BYO key first** (OpenAI-compatible base URL + key). Hosted Kimi + credits later so we do not eat tokens on day one.  
5. Run status: executing / done / failed / “needs review” (honest: until park/resume exists, this may be a message or a failed run).  
6. **Three-pane shell from day one** (even if two panes are thin):

| Region | v0.1 | Later (film) |
|---|---|---|
| **Left** | Skill list + run history | Morning brief, review stack, Arjun’s repair handoff |
| **Center** | Chat **or** the form | Review **card** (approve / hold / reject), swipe next |
| **Right** | Placeholder: “Browser appears when a run is live or paused” | Embedded Chromium of **that run’s** page |

Do **not** ship v0.1 as a single-column ChatGPT clone. That layout will be thrown away.

**v0.1 explicitly out of scope:** full Cowork browser, overnight 340, Intelligence, swipe physics, LibreChat multi-user, OpenCode’s shell/file tools, a second executor.

---

## 10. Growth path (same app, same runtime)

| Step | What | Notes |
|---|---|---|
| **v0.1** | 3-pane + local MCP + form + optional BYO chat | This month’s product for no-Claude buyers |
| **v0.2** | Hosted cheap model (Kimi via existing proxy) + credits/QR | Company with neither Claude nor keys. Meter tokens. Finish a run if credits hit zero mid-run, then block. |
| **v0.3** | Review **inbox** (list) in the left pane | Needs EXEC-21 / recorded review points + pause |
| **v0.4** | Embed paused Playwright page on the right; Approve on the card | Cowork-shaped; one browser context |
| **v0.5** | Operator seat (Neha-only); builder stays Studio | Cheap seat class |
| **H2** | Work-item queue, overnight, phone command | Same app. Blocked on unattended session lifetime. |
| **H3** | Intelligence as another MCP caller, on **their** infra | Film: after ~6 months of history |

**Parallel, not instead of v0.1:** record human review in the skill, durable park/resume, work-item-shaped telemetry. Otherwise the future UI has nothing to swipe.

**OpenCode:** evaluate as the **optional NL / Tier B loop** behind v0.1–v0.2, not as the window chrome. Keep the loop **local**. Scope the prompt to skills + inputs + recovery.

**Cherry:** later, add a `mcp_hosts.js` row so operators who already use Cherry get one-click register — same as Claude Desktop. Do not rebrand Cherry.

---

## 11. Model, recovery, and the number to watch

The runtime has **no LLM client**. Tier B is performed by **the calling agent’s model**, which returns an override the runtime applies. Whichever model sits behind Conxa Chat **is** the self-healing brain that today is Claude on the customer’s subscription.

- **Kimi K3 via existing proxy:** configuration more than new router code. Verify **vision** (recovery/anchors; if text-only, keep a vision provider beside it) and **multi-turn tool calling**.  
- **BYO key:** metering may record usage but bills nothing. Decide keyring vs cloud storage and tenant isolation (overlaps CLOUD-1).  
- **Fine-tune:** only after we capture (with opt-in) the pair: broken page → what the agent picked → whether retry passed. That pair is thrown away today. Customer DOM is sensitive; consent first.  
- **The number:** recovery success rate **split by front door** — Claude Desktop vs hosted Kimi vs BYO — same skills, same sites. If Kimi heals worse, the “cheaper alternative” is a worse product under the same durability promise.

Auth for proxy: runtime already has a per-workspace bearer token; extend `/api/v1/llm/proxy` to accept it, or bake an LLM token into `pack.json` beside sync.

---

## 12. Pricing sketch (not ratified)

Today: seats, machines, compile credits, human-edit tokens, **distribution rights**. Runs free.

For this app:

- **Operator seat** — run + review, no Build Studio (2 of 10 people).  
- **Token pool or credits** — only when they use *our* hosted model.  
- **BYO key** — same product, $0 token margin.  
- Do **not** RPA-meter every execution. Do **not** give away Kimi as an unmetered feature.

This needs a short PRD §11 amendment when we commit. PROD-19 still has open founder questions: seat vs pure credits; BYO on every plan or above a tier; whether the form-and-run door ships to Claude customers too (faster repeat runs) or only the no-subscription segment; recovery-data capture opt-in vs condition of hosted tier.

---

## 13. Decisions from this session (working)

1. Yes to a Conxa-owned way to **run** skills without Claude.  
2. MCP runtime stays the **only executor**; other MCP clients remain first-class.  
3. Do not clone Claude, fork Cherry/LibreChat, or use Pi as the protocol.  
4. OpenCode = harness candidate (locked down); Cherry = supported host later.  
5. Form-and-run first (zero tokens); chat + BYO next; hosted Kimi + credits after.  
6. Freeze a **three-pane** shell so Neha’s queue + live browser can land without a rewrite.  
7. Built-in browser = **view of the runtime’s page**, especially for review — not a second Chromium product.  
8. Cinematic UX is the north star for **this app**; Intelligence and empty-office overnight wait on real foundations.  
9. Hosted model is a **reliability and pricing** decision, not a UI theme.  
10. **Do not write the chat/execute app from a blank repo.** Clone a permitted project *beside* Conxa, copy only the pieces we need, edit those copies. See §16.

---

## 14. Suggested engineering order (near term)

1. **Execution Platform v0.1** — 3-pane Windows UI, MCP client to existing runtime, form-and-run, optional BYO chat.  
2. **EXEC-21 / review points** — record, pause, resume (unblocks the film’s actual job).  
3. Work-item id on runs (shape for Horizon 2, cheap).  
4. Recovery-pair telemetry (opt-in).  
5. Hosted Kimi + metering + credits.  
6. Embed paused page + review card.  
7. Cherry host row; OpenCode constraint profile if bundled.  
8. Unattended session lifetime (Horizon 2 gate) — not a chat feature.

---

## 15. One-line summary

A small company with two operators and no AI subscription gets **Conxa Execute**: form or chat, their keys or later a cheap hosted brain; **the runtime stays the MCP executor** for this app and for Claude; the UI is already shaped for **Neha’s review queue and in-app page**, which we grow into after pause/resume is real — not after we finish a Claude clone.

---

## 16. Implementation method: clone → copy → edit (not a greenfield app)

The execute/chat window should not be invented from an empty folder. Almost everything it needs already exists — in *this* repo, or in one MIT project we pin. The rule is: **clone for mining, copy the few files we keep, leave the rest outside Conxa.**

Do **not** `git clone` a 200k-star agent into `CONXA/` as the product. That is a second monorepo, a second update train, and a coding-agent UI we then spend months deleting.

### 16.1 What we copy from *this* repo (first)

We already have the hard parts. Copy patterns, do not re-derive them.

| Need | Source in Conxa | What to copy / reuse |
|---|---|---|
| Windows desktop shell (Electron + React + Vite) | `conxa-builder/electron/` | Window, preload, IPC, packaging habits — **not** the recorder/compiler screens |
| MCP **client** over stdio | `runtime/app/scheduler_daemon.js` (already spawns the runtime like Claude Desktop) | Client transport, tool call loop, process lifetime |
| MCP **server** / executor | `conxa-runtime.exe` as installed | Call it. Do not copy `run.js` into the chat app |
| Hosted / BYO model HTTP | Cloud proxy + Studio’s LLM proxy client | Same OpenAI-compatible POST, new caller |
| Installer / register-mcp | Existing NSIS + `mcp_hosts.js` | Register **this** app as another host later; runtime registration stays |

v0.1 is mostly: **new thin renderer** (three panes) + **copy the scheduler’s MCP client** + **reuse Electron from Studio**. That is “copy and edit,” and it never leaves the company git history.

### 16.2 What we clone *beside* the repo (second)

Clone **outside** the Conxa tree (e.g. `Desktop/vendor-src/opencode`), pin a commit, copy **named packages** in, keep their **LICENSE** and copyright.

| Clone | License | Copy into Conxa? | Keep | Throw away |
|---|---|---|---|---|
| **[anomalyco/opencode](https://github.com/anomalyco/opencode)** (MIT) | MIT | **Pieces only**, or pin the **released binary** and configure it | Agent loop, OpenAI-compatible provider, MCP *client* wiring, session/tool-result plumbing | TUI, IDE, LSP, default shell/file tools, their desktop chrome, marketplace, Copilot login |
| Cherry Studio | AGPL + extra commercial terms | **No** — not even “just the chat bubbles” | — | Entire tree. Register as a *host* later, like Claude Desktop |
| LibreChat | MIT | **No** as the app | Maybe one YAML MCP snippet as reference | Docker, Mongo, auth, agents marketplace |
| 5ire | Unclear | **No** until license is MIT/Apache in writing | — | — |
| Pi | MIT-ish; no native MCP | **No** | — | — |

**OpenCode two legal ways (both fine):**

1. **Pin the official Windows build** (or `opencode` npm) next to the runtime. Our UI talks to it, or we ship a locked `opencode.json` (Conxa MCP only, tools write/bash off). Fastest; we do not maintain a fork.  
2. **Sparse copy:** from a pinned commit, take only the loop/provider/MCP-client packages into e.g. `conxa-execute/vendor/opencode/` with `LICENSE` intact. Edit the copy to refuse non-Conxa tools. We then own merge pain on upgrades.

Prefer (1) until the loop is proven. Prefer (2) only if we must change the loop itself.

### 16.3 How to copy (practical)

```
# beside CONXA, not inside it
git clone --depth 1 https://github.com/anomalyco/opencode.git ../vendor-src/opencode
# pin: git -C ../vendor-src/opencode rev-parse HEAD  → write into our NOTICE file

# then copy only what we named, e.g. provider + mcp client packages
# never copy packages/app desktop UI or the coding TUI
```

Inside Conxa, new folder **`conxa-execute/`** (sibling of `conxa-builder/` and `runtime/`):

- `electron/` — **copied from Build Studio’s electron skeleton**, stripped of record/compile  
- `src/` — three-pane UI (ours; Neha-shaped)  
- `mcp-client/` — **copied from `scheduler_daemon.js` + MCP SDK** (already a dependency of runtime)  
- `vendor/` — optional OpenCode slices + LICENSE  
- `NOTICE` — OpenCode copyright + commit hash

Form-and-run in v0.1 can skip OpenCode entirely: scheduler-style MCP client + no model.

### 16.4 What “edit” means

| After copy | Edit |
|---|---|
| Electron shell | Three panes; Chat is a center **mode**; title Conxa Execute |
| MCP client | Command = installed `conxa-runtime.exe`; same tools Claude uses |
| OpenCode (if present) | System prompt = skills + inputs + recovery; disable bash/edit; custom provider = BYO URL or our proxy |
| Cherry / LibreChat | Do not edit. Do not copy. |

### 16.5 Why this is faster than scratch *and* faster than a full fork

- Scratch: we would re-write MCP stdio, windowing, and an agent loop — all already written.  
- Full OpenCode fork: we inherit a coding agent and 5,000 issues.  
- Copy Studio + scheduler + optional pinned OpenCode: v0.1 is a **new screen** on machinery we already ship.

Cherry remains the pretty operator UI **customers already installed** — we add a host row; we do not copy its source into Conxa.
