# Conxa Cost & Revenue Model

**Last Updated:** September 24, 2026
**Status:** Living document — iterate as assumptions change

**Contents:** [What Conxa Actually Does](#what-conxa-actually-does) · [Cost Structure](#cost-structure) (what Conxa pays for) · [Revenue Model](#revenue-model) (what customers pay, pricing tiers, value proposition, Conxa's cost per tier) · [Unit Economics](#unit-economics) · [Growth Milestones](#growth-milestones) · [Future Horizons](#future-horizons--revenue-projections--cost-posture) · [Cost Levers](#cost-levers) · [Risks](#risks) · [What to Measure](#what-to-measure) · [Next Steps](#next-steps)

---

## What Conxa Actually Does

A company records their browser workflows in the Build Studio. Conxa compiles those recordings into a signed `.exe` installer. The company distributes that installer to their customers however they want (download page, onboarding email, their own app store).

When a customer installs it, their Claude Desktop gains the ability to run those workflows as MCP tools. Execution happens entirely on the customer's machine — Conxa is not in the execution path at all.

What does flow back to Conxa is telemetry: every run, every recovery attempt, every success or failure. Companies see this in the Conxa dashboard.

```
Company builds once               Customer runs forever
─────────────────                 ─────────────────────
Build Studio                      Customer machine
  └─ Record workflows               └─ .exe installed
  └─ Compile to plugin              └─ Claude Desktop
  └─ Generate .exe ──── distribute ──► └─ MCP runtime
  └─ Push update                          └─ executes workflow
                                          └─ telemetry ──► Conxa Dashboard
                                                            └─ Company sees it
```

**What Conxa pays for:**
- Compilation LLM (one-time per workflow compilation, plus Human Edit repair calls)
- Dashboard hosting (companies checking analytics)
- Telemetry ingestion (from customer machines worldwide)
- Signing, release management, and update-channel infrastructure
- Plugin update sync (when company ships an update, customers pull it)
- Conxa runtime and healing updates that keep installed plugins working

**What Conxa does NOT pay for:**
- Execution (runs on customer's machine)
- LLM recovery during execution (customer's Claude Desktop subscription)
- Customer infrastructure of any kind

---

## Cost Structure

### 1. Plugin Compilation (One-Time Per Workflow Compilation)

Every time a company records workflows and compiles them into a new plugin version, Conxa runs LLM calls per step to generate selectors, anchors, and intent.

#### LLM Calls Per Step

| Call | Prompt size | When | Count/step |
|------|-------------|------|------------|
| **Vision anchor generation** (`generate_anchors_for_step_or_raise`) | ~15K input + ~500 output tokens (screenshot JPEG as base64 + prompt) | Every step — but **cached** by screenshot hash | 1 (0 on cache hit) |

Intent is a byproduct of the single per-compile workflow-intent call (below), at zero marginal per-step cost. There is no per-step intent fallback (removed 2026-09-09): a step the graph leaves tokenless, or every step if the graph call fails outright, just compiles with a blank/heuristic `intent` (flagged in Human Edit) — no extra LLM call, no retry cost.

Normalize no longer calls `semantic_enrichment` per event (removed 2026-09-06). That pass was leftover: the compiler overwrote its guess with the workflow-intent token. Missing field types are inferred with local regex only. `enrich_semantic` remains for Human Edit 1-click fix, not compile.

**All steps, all DOM conditions:** ~1 LLM call/step (vision anchor) + ONE workflow-intent call per compile.
**Recompilation (same DOM, cached):** 0–1 calls/step — caching absorbs most of the cost.

Selector strings are generated deterministically by `IdentityBundle` + `selector_grammar.py`. No LLM calls are made for selector generation regardless of DOM quality or `data-testid` coverage.

#### Corrected Cost Per Workflow Compilation

#### LLM Provider Strategy — Three Tiered Pools (revised 2026-09-23)

**Every plan, including Free, now routes through a real, billed provider pool — the free-tier key-rotation strategy (Groq/Google AI Studio/NVIDIA NIM) is retired.** Free is no longer $0 real cost to Conxa; it now runs on cheap-but-paid OpenRouter models, priced deliberately low enough to keep the trial's COGS negligible without needing rate-limit gymnastics. Pro runs entirely on Anthropic directly — Claude Sonnet 5 for text, Claude Opus 5.5 for vision and Execute chat — one provider, one bill. Enterprise stays BYOK/negotiated, priced per contract to hit the net-profit floor below — see "Planning Enterprise to the margin target."

| Plan | Text / intent model | Vision (compile-time anchor) model | Execute multimodal model | Routing |
|------|---------------------|-------------------------------------|---------------------------|---------|
| **Free** | GLM 5.3 Flash | Qwen3 VL 235B A22B Instruct | GLM 5.3 Flash (same as text) | OpenRouter |
| **Starter** | Kimi K3 | Qwen3 VL 235B A22B Instruct | Kimi K3 (same as text) | OpenRouter |
| **Pro** | Claude Sonnet 5 | Claude Opus 5.5 | Claude Opus 5.5 | Direct (Anthropic) |
| **Enterprise** | Whatever the company requires (BYOK or negotiated) | Same | Same | Direct or BYOK, contract-priced |

**Why this mix:** Free/Starter move to OpenRouter's cheapest usable text + vision models — this trades the old free-tier rate-limit ceiling for a small but real per-compile COGS, which is the deliberate tradeoff (no more burst queuing, no more "Flash-only or it fails" constraint). Pro moves to top-of-market models on both sides (Sonnet 5 for text, Opus 5.5 for vision and Execute chat) because Pro is the highest self-serve tier and where distribution-quality compiles matter most — this is also the most expensive pool by a wide margin (see below), so it's the tier to watch for margin compression.

**OpenRouter platform fee:** OpenRouter's pay-as-you-go plan adds a 5.5% fee on top of list price. All Free/Starter figures below already include it. Direct Anthropic calls (Pro) carry no such fee.

**How it routes in the existing code:**
`router.py` builds a `PoolEntry` per key. The Build Studio backend reads the workspace billing tier from the cloud API and passes `paid_tier`/pool selection to route Free/Starter/Pro to their respective pools at compile time. Three pool configs loaded from env, up from two.

---

#### Current Provider Prices Used

Pricing checked against provider/OpenRouter docs on September 23, 2026:

| Provider / model | Input | Output | Note | Source |
|------------------|-------|--------|------|--------|
| GLM 5.3 Flash (OpenRouter) | $0.15 / 1M tokens | $0.50 / 1M tokens | List price — the 50% launch promo ended Sept 9, 2026. +5.5% OpenRouter fee applies | [OpenRouter GLM 5.3 Flash](https://openrouter.ai/z-ai/glm-5.3-flash) |
| Qwen3 VL 235B A22B Instruct (OpenRouter) | $0.20 / 1M tokens | $0.88 / 1M tokens | 262K context, 5 upstream hosts on OpenRouter (DeepInfra, Venice, Parasail, Alibaba Cloud Int., NovitaAI); +5.5% OpenRouter fee applies | [OpenRouter Qwen3 VL 235B A22B Instruct](https://openrouter.ai/qwen/qwen3-vl-235b-a22b-instruct) |
| Kimi K3 (OpenRouter) | $1.50 / 1M tokens | $7.50 / 1M tokens | Some sources report $3.00/$15.00 instead — re-verify before locking Starter pricing; +5.5% OpenRouter fee applies either way | [OpenRouter Kimi K3](https://openrouter.ai/moonshotai/kimi-k3) |
| Claude Sonnet 5 | $2.00 / 1M tokens | $10.00 / 1M tokens | Confirmed permanent (2026-09-23 correction, above) — not an introductory rate | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Claude Opus 5.5 | $4.00 / 1M tokens | $20.00 / 1M tokens | Batch API half price ($2/$10), Fast mode double ($8/$40); cache reads $0.20 / 1M. Replaces GPT-6 Astra and GPT-5.6 Sol on Pro (2026-09-24) | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |

**Opus 5.5 vision is now the single biggest cost lever in Pro's mix.** At $4/$20 per MTok it is 2.5x cheaper than the retired GPT-6 Astra ($10/$50), but vision-anchor calls are 15K-token-input-heavy, so it is still ~20x the per-step cost of Qwen3 VL on Free/Starter. If Pro's build-heavy-month margin ever gets tight, the levers to pull before touching price are (1) the Batch API (half price) for non-urgent recompiles and (2) moving Pro's vision-anchor calls to Claude Sonnet 5 ($2/$10, half of Opus 5.5) — same provider, no new relationship.

#### Cost Per Compilation by Plan

Same token-shape assumptions as before (~200 input + 50 output for intent, ~15K input + 500 output for vision anchor; selectors remain deterministic at $0 regardless of plan).

**Free (GLM 5.3 Flash + Qwen3 VL 235B A22B Instruct, via OpenRouter, +5.5% fee):**
- Intent (GLM 5.3 Flash): **~$0.000058/step**
- Vision anchor (Qwen3 VL 235B A22B Instruct): **~$0.00363/step**
- Selectors: **$0**
- **Total/step: ~$0.00369 | Fresh 15-step workflow: ~$0.055**
- **Cached recompilation (3 changed steps): ~$0.011**
- **Blended monthly average (20% fresh, 80% cached): ~$0.020/compilation** — real cost, billed to Conxa (no longer $0; the free-tier-rotation strategy that made Free genuinely free is retired)

**Starter (Kimi K3 + Qwen3 VL 235B A22B Instruct, via OpenRouter, +5.5% fee):**
- Intent (Kimi K3): **~$0.00071/step**
- Vision anchor (Qwen3 VL 235B A22B Instruct): **~$0.00363/step**
- Selectors: **$0**
- **Total/step: ~$0.00434 | Fresh 15-step workflow: ~$0.065**
- **Cached recompilation (3 changed steps): ~$0.013**
- **Blended monthly average (20% fresh, 80% cached): ~$0.023/compilation** — down ~3.2x from the old GPT-5.4-mini/Gemma pool (~$0.075)

**Pro (Claude Sonnet 5 + Claude Opus 5.5, direct):**
- Intent (Claude Sonnet 5): **~$0.0009/step**
- Vision anchor (Claude Opus 5.5): **~$0.07/step**
- Selectors: **$0**
- **Total/step: ~$0.071 | Fresh 15-step workflow: ~$1.06**
- **Cached recompilation (3 changed steps): ~$0.21**
- **Blended monthly average (20% fresh, 80% cached): ~$0.38/compilation** — ~1.3x the old GPT-5.4/Sonnet-4.6-Vision pool (~$0.292), driven by Opus 5.5's vision price; 2.5x cheaper than the GPT-6 Astra mix this replaced (~$0.95, one day earlier)

**Enterprise:** cost-per-compilation is whatever the contracted model mix bills at — see "Planning Enterprise to the margin target" below.

| Scenario | Free cost (real, billed) | Starter cost | Pro cost |
|----------|---------------------------|---------------|----------|
| Short workflow (5 steps) | ~$0.018 | ~$0.022 | ~$0.36 |
| Medium workflow (15 steps) | ~$0.055 | **~$0.065** | **~$1.06** |
| Long workflow (30 steps) | ~$0.111 | ~$0.130 | ~$2.13 |
| Recompilation (cached, 3 changed steps) | ~$0.011 | **~$0.013** | **~$0.21** |
| **Blended (80% recompiles, 15 steps avg)** | **~$0.020** | **~$0.023** | **~$0.38** |

**Key insight on continuous iteration:** Vision anchor calls are cached by element hash (`anchor_vision_llm.py`); the workflow-intent call is cached by a steps-summary+URLs hash (`workflow_intent.py`). Recompiling a workflow where only 2–3 steps changed fires LLM only for those steps' vision anchors — the rest are cache hits. This still makes daily iteration far cheaper than a fresh compile on every plan, but on Pro each fresh-compile step now costs ~19x what a Free step costs, almost entirely due to Opus 5.5 vision — cache-hit rate matters more for Pro's margin than it ever did under the old pool.

**AI Usage Credits:** Human Edit can trigger extra LLM calls after the initial compile: step repair, selector or anchor regeneration, validation, and recovery artifact updates. Each plan gets a monthly AI Usage Credits pool that applies to both text and vision repair calls:

| Plan | Monthly AI Usage Credits |
|------|----------------------------|
| Free | 500K text + vision tokens |
| Starter | 2.5M text + vision tokens |
| Pro | 10M text + vision tokens |
| Enterprise | Contracted text + vision reserve |

These are compilation/recompilation costs, not execution costs, because customer-side workflow execution still runs locally. The pool is tracked as a visible monthly customer meter but should not become surprise per-token billing.

**Human Review Conxa Copilot turns cost two `human_edit` LLM calls, not one (BUILD-26 stage c):**
a streamed `copilot_reply` call for the prose the reviewer reads live, plus the existing
`copilot_diagnose` call for gated proposals — added so the reply can stream without showing raw
JSON mid-generation (see `docs/TRD.md` §7.4). Both draw from the same AI Usage Credits pool above;
this roughly doubles the token cost of asking the copilot a question compared to before
streaming shipped. No pooling/caching applies across turns — each turn's evidence bundle is
rebuilt fresh and both calls fire every time, same as the single call did previously.

**Testing a workflow containing an AI Review step (EXEC-13) costs one `human_edit` call per
checkpoint the test run pauses at, capped at 5 pauses per Run Test.** An `ai_review` step is a
reasoning checkpoint an author drops into a workflow — "is there an error banner on this page?"
— and on a customer's own machine it's answered by their own Claude subscription at **zero cost
to Conxa**. In the Build Studio's local test sandbox there is no such agent to ask, so Build
Studio answers it itself through the same metered proxy every other Human Edit call uses (one
`ai_review` call per pause, image included, same as a vision-repair call). This only meters
*testing* a workflow that has one of these steps — real customer runs stay free to Conxa
regardless of how many review steps a workflow has.

**Example — Company on Starter (paid plan), 1 plugin, 50 workflows:**
- Initial build: 50 × $0.065 = **$3.25 one-time** (50 compile credits spent)
- Monthly iteration (recompile 10 workflows × 3 times, 3 changed steps): 30 compilations × $0.013 = **$0.39/month** (30 more compile credits spent — recompiles meter the same as first compiles)
- Selector/anchor repair on existing workflows: draws from the included **2.5M text + vision token Human Edit reserve**

| Component | Free | Starter | Pro | Enterprise |
|-----------|-------|---------|-----|------------|
| LLM per compilation (first build) | ~$0.055 | ~$0.065 | ~$1.06 | Contracted |
| LLM per recompilation (cached 3-step change) | ~$0.011 | ~$0.013 | ~$0.21 | Contracted |
| Build infrastructure | $0.10 | $0.10 | $0.10 | $0.10 |
| Human Edit LLM reserve | 500K text + vision tokens | 2.5M text + vision tokens | 10M text + vision tokens | Contracted |
| **Blended per compilation** | **~$0.020** | **~$0.023** | **~$0.38** | Contracted |

---

### 2. Burst Capacity and Throughput

Conxa does not need maximum LLM capacity all day. Compilation demand comes in bursts when teams record, edit, and publish workflows. The paid pool should therefore buy high TPM and throughput for burst windows, not idle 24/7 capacity.

| Pool | Normal configuration | Burst configuration | Why |
|------|----------------------|---------------------|-----|
| Free / Starter OpenRouter | Standard pay-as-you-go routing across GLM 5.3 Flash, Qwen3 VL, Kimi K3 | OpenRouter's provider fallback/load-balancing across upstream hosts, or a dedicated-throughput agreement if volume justifies it | OpenRouter abstracts away individual upstream rate limits by routing across multiple hosts per model; the main risk at burst scale is upstream host queuing, not a single provider's TPM cap |
| Pro Anthropic (Sonnet 5 text + Opus 5.5 vision/Execute) | Standard limits | Priority Tier or custom Enterprise limits; Fast mode (2x price) for latency-sensitive bursts | Standard Anthropic limits are caps, not guaranteed minimum throughput; bursty workloads should use priority or negotiated limits. Both text and vision now share one provider's limits |
| Pro non-urgent recompiles | Opus 5.5 Standard | Message Batches API (half price, asynchronous) | Routing overnight or low-priority recompiles through Batch halves Opus 5.5's share of Pro's COGS |
| Enterprise | Whatever the company requires — BYOK or negotiated | Priority Tier / Reserved Capacity per contract | Burst provisioning is part of the same per-contract plan as pricing — see "Planning Enterprise to the margin target" above |

Anthropic Priority Tier and Fast mode (Pro/Opus 5.5) should be used only for latency-sensitive compile jobs. The Batch API (half price) is the more relevant lever here: routing non-urgent Pro recompiles through it directly protects the build-heavy-month margin discussed above.

---

### 3. Dashboard Hosting (Monthly Fixed)

Companies log into the Conxa dashboard to:
- View analytics (runs, success rate, recovery rate, who used what)
- Manage plugin versions (publish, rollback, deprecate)
- Download installer artifacts (.exe per platform)
- Configure billing and team access

Traffic is low — these are **companies**, not millions of end users. A company might check the dashboard 5–10 times a day, not 5 times a second.

| Scale | Companies | Dashboard Requests/Day | Backend Cost | DB Cost | Total |
|-------|-----------|------------------------|-------------|---------|-------|
| MVP | 10 | ~500 | $20 | $15 | **$35** |
| Growth | 100 | ~5,000 | $50 | $30 | **$80** |
| Scale | 500 | ~25,000 | $150 | $80 | **$230** |
| Enterprise | 2,000 | ~100,000 | $500 | $200 | **$700** |

Dashboard is not a cost problem. It scales gracefully because it's company-facing, not end-user-facing.

---

### 4. Telemetry Ingestion (Scales With Customer Base)

Every time a customer runs a workflow anywhere in the world, a telemetry event flows back to Conxa. This is where costs actually scale — not with companies, but with the combined size of all their customer bases.

**Telemetry payload per execution:** ~1–2KB (run ID, plugin ID, step outcomes, recovery tiers reached, timestamps)

| Scale | Companies | Avg Customers/Company | Daily Runs | Monthly Telemetry Events | Ingestion Cost |
|-------|-----------|----------------------|------------|--------------------------|---------------|
| MVP | 10 | 100 | 1,000 | 30K | $5 |
| Growth | 100 | 500 | 50,000 | 1.5M | $50 |
| Scale | 500 | 2,000 | 1,000,000 | 30M | $500 |
| Enterprise | 2,000 | 5,000 | 10,000,000 | 300M | $3,000 |

**Telemetry stack:** Events hit the `/api/v1/tracking` endpoint → write to append-only log → aggregate into analytics tables daily. No real-time processing needed; companies are fine seeing yesterday's data.

**Retention policy:** Because Conxa already tracks runs, recovery attempts, success/failure outcomes, and adoption telemetry, data retention is both a product feature and a storage-control lever. Shorter retention keeps Free/Starter storage small; longer Pro/Enterprise retention gives companies more historical analytics without changing the execution model.

---

### 5. Plugin Update Sync (Per Update Release)

When a company ships a plugin update, customers pull the new version. The `/skill-packs/*` endpoint serves the updated plugin package.

| Component | Cost | Notes |
|-----------|------|-------|
| Storage per plugin version | ~$0.01/GB | Compiled plugin packages are small (~5–50MB) |
| CDN bandwidth per update rollout | ~$0.01/GB | 100 customers × 10MB = 1GB = $0.01 |
| **Total per update** | **~$0.02–0.10** | Negligible |

---

### 6. Execution-Time Recovery Tiers (Customer-Side, Not Billed to Conxa)

Recovery cost during execution is paid by the **customer's own Claude Desktop subscription or API key**, never by Conxa (see "What Conxa Actually Does" above). It's documented here anyway because it drives the cost/latency the *customer* experiences per step, and because compile-time decisions (selector quality, `recovery.json` fallback richness) directly change how often a run reaches the expensive tiers.

The runtime has two behavioural tiers (`docs/TRD.md` §10.1): **Tier A** (in-process: exception ladder, then a11y / re-hover / dialog-scope — zero tokens) and **Tier B** (agent-mediated, every round armed with ranked digest + screenshots). Studio caps `CONXA_MAX_RECOVERY_TIER=2` so a step that survives Tier A fails without a model. Internal numbers 1–4 remain on the env var and telemetry; they are not the product names. Tier A is free by design; Tier B is priced per round below.

| Outcome | LLM tokens | Who pays | Added wall time vs. a normal step | Basis |
|---|---|---|---|---|
| **No recovery needed** (primary selector hits) | 0 | — | none — baseline step time (~6–8s, mostly page interaction/wait) | Observed baseline from `transient_recovered`/normal steps in a live run |
| **Tier A** (timing/obstruction — same element) | 0 | — | ~none — absorbed into the same step timeout | `cascade.js::recoverStep`, in-process |
| **Tier B** (armed agent round — digest + screenshots) | ~2,500–3,500 tokens/round (ranked digest ≈700–2,000 + screenshot(s) ≈1,300–1,400 + nomination ≈150–300) | **Customer's own Claude subscription/API — never Conxa** | +5–8s per round (Tier A exhaustion ~10–17s before escalating, then agent reasoning latency) | Every round is armed; there is no cheaper text-only first round |

A failed step gets at most two Tier B rounds (`STAGNATION_LIMIT`). Round two is not a re-roll: it carries what round one nominated. A stagnation hard cap stops further paid rounds when the page fingerprint is unchanged. The old "Tier 3 text then Tier 4 vision as separate shapes" split is gone — withholding screenshots from the first round saved tokens on attempts that were going to work and wasted a whole round-trip on the ones that needed pictures.

**Caveats on the token figures:** these are estimates, not exact counts. Measuring the *real* number requires either an Anthropic API key (to run `messages.count_tokens` against the reconstructed recovery payload) or Console usage access — neither was available when this was measured, and the recovery screenshot/DOM payload isn't persisted to disk, so it can't be re-measured after the fact. The wall-clock timings, by contrast, are exact — pulled directly from `~/.conxa/logs/recovery.log` timestamps (`terminal_failure` → `agent_recovery_requested` → `agent_override_applied` → `recovery_park_resumed`).

#### What's in a Tier B round

| Signal | What's in it | ~Tokens/occurrence |
|---|---|---|
| **Armed round (current)** | Ranked indexed candidate digest (~700–2,000) + screenshot(s) (~1,300–1,400) + nomination (~150–300) | **~2,500–3,500** (typical ~3,000) |
| Historical: text-only first round (pre-2026-09) | Digest + nomination, no images | ~900–2,300 |
| Historical: vision-only second round (pre-2026-09) | Screenshot + refreshed digest + nomination | ~1,450–1,700+ |

#### Worked Examples — Full Workflow Run

Using the actual 8-step workflow this cascade was tested against. **Baseline** (~1,200 tokens) is the one-time cost of a clean `execute_skill` round trip: the MCP tool schemas (mostly a cache-read after the first call in a conversation), the tool call itself, and a short "Done." result — no recovery payload at all. Each Tier B occurrence is additive on top of that:

| Scenario | Tier B occurrences | Token math | **Total tokens** |
|---|---|---|---|
| Perfectly run workflow (no recovery) | 0 | ~1,200 baseline | **~1,200** |
| 1 LLM recovery | 1 | 1,200 + (1 × 3,000) | **~4,200** |
| 2 LLM recoveries | 2 | 1,200 + (2 × 3,000) | **~7,200** |
| 3 LLM recoveries | 3 | 1,200 + (3 × 3,000) | **~10,200** |

These are per-workflow-run figures, not per-step — most steps in a healthy workflow resolve via Tier A (zero tokens) and never show up in this table at all. The number that matters for a given workflow is simply how many of its steps are weak enough to fall through to Tier B on a given run; each one adds roughly one more ~3,000-token increment above.

**Why this matters for compile-time decisions:** every step that reaches Tier B costs the *customer* real tokens and extra seconds, on top of Conxa's own compile-time incentive to keep selectors strong. A workflow with weak `IdentityBundle` signals and a thin `recovery.json` will lean on Tier B more often in production — worse customer experience, even though it costs Conxa nothing directly. Selector/anchor quality at compile time is the only lever that controls this.

#### What This Costs the End Customer Running a Plugin Locally

The end customer (the person running the installed `.exe` via Claude Desktop) is not billed by Conxa at all for execution — it draws entirely against **their own Claude Pro/Max subscription usage allowance** (or their own API key, if that's how their Claude Desktop is configured). There is no incremental dollar cost as long as they're inside their plan's existing session limit — the only "cost" is how many of their allotted messages a workflow run consumes.

Anthropic doesn't publish exact message counts (they vary by message/attachment length, conversation length, and model — see the Claude Help Center's ["How do usage and length limits work?"](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)), and that remains true as of the August 2026 re-check — the support article describes the mechanism, not fixed counts. Commonly-cited approximate 5-hour session allowances (re-confirmed against current community reporting, which puts Pro at roughly 10–45, Max 5x at 50–225, and Max 20x at 200–900) are:

| Plan | Price | ~Messages / 5-hour session |
|---|---|---|
| Pro | $20/mo | ~45 |
| Max 5x | $100/mo | ~225 |
| Max 20x | $200/mo | ~900 |

A Conxa `execute_skill` call maps roughly to **1 message-equivalent per attempt**: a clean run (no recovery) is 1 message; each Tier B occurrence needs one more (the runtime's recovery request + the agent's follow-up `execute_skill` call with `step_overrides`) — Tier A recoveries are free, in-process, and don't add a message. So:

| Plan | ~Clean runs / 5hr (0 recoveries) | ~Runs / 5hr (avg. 1 recovery/run) | ~Runs / 5hr (avg. 2 recoveries/run) |
|---|---|---|---|
| Pro | ~45 | ~22 | ~15 |
| Max 5x | ~225 | ~112 | ~75 |
| Max 20x | ~900 | ~450 | ~300 |

**Bottom line for customer-facing messaging:** running Conxa-built workflows costs a Pro/Max subscriber $0 extra — it just draws down their existing 5-hour message allowance, same as any other Claude Desktop conversation. The number of runs they can fit in a session depends almost entirely on how often the workflow needs Tier B recovery, which is why compile-time selector/anchor quality (above) is the thing that actually protects their usage budget, not anything Conxa charges for.

---

### Total Monthly Operating Cost

Assumes blended compilation cost per company mix (Starter ~$0.023/compile, Pro ~$0.38/compile — see above), weighted at the same Starter:Pro ratios used in Unit Economics below (roughly 70:30 at MVP/Growth, 60:40 at Scale): **~$0.13/compile** blended at MVP/Growth, **~$0.17/compile** blended at Scale. Enterprise compilation cost is contracted, not a pool rate — see "Planning Enterprise to the margin target" below. Plugin packaging itself is treated as materially free.

| Scale | Companies | Workflow Compilations/Month | Compilation | Dashboard | Telemetry | Updates | **Total/Month** |
|-------|-----------|--------------|-------------|-----------|-----------|---------|-----------------|
| MVP | 10 | 20 Starter/Pro | ~$3 | $35 | $5 | $2 | **~$45** |
| Growth | 100 | 200 Starter/Pro | ~$26 | $80 | $50 | $20 | **~$176** |
| Scale | 500 | 1,000 Starter/Pro | ~$166 | $230 | $500 | $100 | **~$996** |
| Enterprise | 2,000 | 5,000 Enterprise-grade | Contracted | $700 | $3,000 | $400 | **Contracted + ~$4,100** |

**Cost note:** Selector generation is deterministic and costs zero tokens. Compilation LLM cost is driven solely by intent + vision anchor calls. Vision anchor cache hit rate (same screenshot hash on recompile) is the primary cost lever — high-cache-hit recompilations cost ~80% less than fresh compilations, and this now matters far more on Pro than before because Opus 5.5's per-call vision cost is ~20x Qwen3 VL's.

---

## Revenue Model

**Revision note (2026-08-08):** Repriced and repositioned following the Centelon pilot demo (7 Aug 2026)
around a capability ladder rather than a flat feature list — see `docs/PRD.md` §11 and
`docs/Implementation-Plan.md`. Pricing moved from USD to INR; the installer-slot meter was removed
entirely (a workspace may publish under unlimited product slugs on any tier); a machines meter replaced
it as the trial-abuse control; and Free became a 30-day trial rather than a permanent tier. Revenue
figures below are in INR; LLM provider COGS is still billed in USD by the providers, converted here at
an indicative ₹83/USD — recompute against the live rate before using these numbers for planning.

Companies pay Conxa to **build and maintain** their Claude-compatible plugin. They think about it the
same way they think about their mobile app on the App Store — there's a platform fee to be listed and
maintained, not a per-download fee.

### Tier Design Principles

**What companies actually look like:**
- They buy Conxa like a subscription product, not like an API meter.
- They build a small number of live plugins - each plugin maps to a product, customer segment, or branded installer.
- Each live plugin contains many workflows covering the tasks their customers ask Claude to perform.
- They iterate heavily during the first month, then settle into lower-volume maintenance.
- They do not want to think in tokens, credits, or per-compilation billing.

**Pricing is a capability ladder, not just four bigger numbers.** Each tier unlocks what a workspace can
*do*, not only how much: Free proves the product works on one machine before a 30-day trial ends,
Starter and Pro run it internally across a team, and Pro and Enterprise can distribute externally to
customers (Conxa-branded on Pro, white-label on Enterprise). See the capability ladder in
`docs/PRD.md` §11.

**The public pricing axis is subscription tier + four visible meters.** Customers see seats, machines,
monthly compile credits, and the monthly AI Usage Credits pool. Local plugin creation and workflow recording
are unlimited; workflow count and product-slug count are no longer visible quotas — reach is gated by
distribution capability, not a count.

This keeps the buying motion close to "Claude Pro / Max" instead of "cloud API usage." A customer
chooses the plan that matches how far they need to reach, then Conxa quietly manages compile cost
behind the scenes.

---

### Build Lifecycle Economics

The highest LLM cost is usually concentrated in the first month after a company creates a plugin. That is when teams record many workflows, use Human Edit heavily, and recompile repeatedly while polishing the installer.

After that, the same company often updates only 1–2 workflows per month. At that point Conxa's direct LLM cost drops sharply, but the customer still pays the full monthly subscription because Conxa is still providing the platform surface that keeps the plugin useful.

| Phase | Typical behavior | Conxa cost pattern | Why monthly billing still applies |
|-------|------------------|--------------------|-----------------------------------|
| Initial build month | 50–300+ compilations, frequent Human Edit, repeated plugin builds and tests | High LLM usage; full tier cap matters | Customer is creating and stabilizing the product |
| Maintenance months | 1–2 workflow updates/month, occasional plugin rebuilds, Execute chat and Human Edit in daily use | Compile cost falls to near zero; **AI Usage Credits (Execute chat + Human Edit) become the main LLM cost**, alongside dashboard/telemetry/signing | Plugin remains live, signed, tracked, updateable, and supported |
| Conxa platform updates | Conxa ships runtime, healing, recovery, or signing updates | Mostly platform engineering and update-sync cost | Customers benefit even without recompiling their workflows |

Example: a Starter customer with 1 live plugin and 50 workflows might recompile heavily in month 1 while polishing the installer. That build-heavy month is bounded directly by the plan's monthly compile-credit allowance (recompiles spend a credit just like first compiles), plus add-on credit packs if they run out. In month 2, the same customer might update only 1-2 workflows, while still paying for dashboard analytics, signing, telemetry retention, update delivery, and Conxa runtime/healing improvements.

This is why margins improve after the first build month. The cap protects Conxa during build-heavy periods; the subscription captures the ongoing value after the plugin is live.

**The LLM cost driver flips after month 1: compile credits dominate the build month, AI Usage Credits dominate every month after.** Once workflows are compiled, teams spend tokens on Execute chat (every Execute seat draws from the shared pool) and Human Edit repairs, not on compiles. The pool is a hard cap, so the worst case is a fully drained pool. Indicative COGS of draining it (assumes an 85% input / 15% output token mix, no prompt caching, OpenRouter's 5.5% fee on Free/Starter):

| | Free | Starter | Pro |
|--|------|---------|-----|
| Pool | 500K | 2.5M | 10M |
| Chat model | GLM 5.3 Flash | Kimi K3 | Claude Opus 5.5 |
| **Cost if fully drained** | **~$0.11** | **~$6.30** | **~$64** |
| Maintenance-month net margin, pool fully drained (infra reserve at its build-month upper bound) | — | **~81–87%** | **~72–79%** |

Read this against the ≥90% maintenance target in the Cost Per Tier table: Starter's pool is small enough to stay near it, but **Pro misses it if its 10M pool is used heavily** — Opus 5.5 chat is the reason. Pro's maintenance margin is only ~85–90% while the pool is lightly used. Levers, in order: prompt caching on Execute chat (cache reads are $0.20/MTok vs. $4 input), routing Pro chat to Sonnet 5 (half of Opus 5.5's price), then resizing the Pro pool. Watch pool utilisation per workspace in maintenance months, not just compile credits (see "What to Measure").

---

### Pricing Tiers

**Customer-facing meters:**
- **Seats** - people who can use the dashboard / Build Studio for the workspace.
- **Machines** - distinct devices a workspace can build from. Replaces the old installer-slot meter (removed 2026-08-08 — a workspace may now publish under unlimited product slugs on every tier); this is the control that keeps a single-machine free trial from quietly becoming a free Pro seat.
- **Execute seats** (added 2026-09-16) - people a workspace has granted Conxa Execute access to, independent of Build Studio membership. Their chat usage draws from the workspace's AI Usage Credits pool below, tracked as its own line item (`execute_chat_tokens`) but not a separate quota. Since the 2026-09-17 merge, Conxa Execute has no dedicated LLM provider keys of its own — its chat calls run on the same per-plan managed pool that compile and Human Edit draw from (as of 2026-09-24: GLM 5.3 Flash on Free and Kimi K3 on Starter, both via OpenRouter; Claude Opus 5.5 on Pro, direct from Anthropic), not a separate rate-limit budget.
- **Compile credits** - monthly UTC fresh-compile credits. A fresh workflow compile consumes 1 credit. Starter and Pro can top up with one-time add-on packs (each also adds Human Edit tokens) that never expire and are consumed only after the monthly allowance runs out: +20 credits at ₹3,999, +50 at ₹9,999, +100 at ₹19,999, and +250 at ₹49,999.
- **AI Usage Credits** - monthly UTC token pool for editor-triggered LLM work only: selector repair (1-click fix), semantic repair and visual re-anchor. Recompiling a whole workflow is billed like a first compile — see Compile credits above — not from this pool.

Local plugin creation, workflow recording, plugin package builds before testing, deterministic Human Edit patches, reorder/delete/input edits, validation edits, and sign-off remain unlimited.

**Capability gates (not numeric meters):**
- **Distribution** - internal-only (Free, Starter) vs. external distribution to customers (Pro, Enterprise). Free additionally runs on a single build machine.
- **Installer branding** - custom installer icon from Starter upward; full white-label (Conxa branding removed) is Enterprise only.
- **Ops tier** - none (Free) / basic, runs list only (Starter) / full, + drift detection, healing, audit export (Pro, Enterprise).
- **BYOK** - Enterprise only, Azure OpenAI. Compile credits still apply on BYOK — credits meter reach, not Conxa's token cost.

**Internal controls, not public meters:**
- Active installs
- Monthly customer-side runs
- Telemetry events and retention cost
- Burst queue priority
- Per-provider COGS and cache hit rate

| | **Free** | **Starter** | **Pro** | **Enterprise** |
|--|----------|-------------|---------|----------------|
| **Price** | **₹0 · 30-day trial** | **₹19,999/mo** | **₹49,999/mo** | Custom, from ₹99,999/mo |
| **Seats** | 1 | 3 | 10 | Custom override |
| **Machines** | 1 | 3 | 10 | Custom override |
| **Execute seats** | 1 | 25 | 100 | Custom override |
| **Compile credits / month** | 25 | 200 | 500 | Unlimited |
| **AI Usage Credits / month** | 500K tokens | 2.5M tokens | 10M tokens | Custom override |
| **Distribution** | Internal, 1 machine | Internal, unbranded | External, Conxa-branded | External, white-label |
| **Installer icon** | No | Yes | Yes | Yes |
| **Ops tier** | None | Basic | Full | Full + SSO |
| **Analytics retention** | None | 90 days | 1 year | Custom |
| **Build speed** | Standard queue | Priority | Highest priority | SLA-backed |
| **BYOK** | No | No | No | Yes (Azure OpenAI) |
| **Support** | Community | Priority email | Priority + onboarding | SLA / private channel |

**Why compile credits are visible:**
Compiling is the clearest proxy for expensive extraction work. Customers can record and re-record as many workflows as they need locally at no cost, but every time they ask Conxa to turn a workflow into execution data — whether that's the first compile or a recompile after re-recording — it consumes 1 monthly compile credit.

**Why Human Edit is separate:**
Editing an already-compiled workflow (selector repair, semantic repair, visual re-anchor) is a different product action from compiling — it's a targeted quality-improvement loop on an existing skill, not a full recompile, so it draws from the AI Usage Credits pool instead. Deterministic edits stay available even when the pool is exhausted.

**Recommended hard gates:**
- Compile is blocked — first compile or recompile alike — when monthly compile credits are exhausted, or when a free trial has passed its 30-day window.
- LLM-assisted Human Edit actions (selector/semantic repair, visual re-anchor) are blocked when the AI Usage Credits pool is exhausted.
- A new build machine is blocked once the plan's machine limit is reached; a machine already registered keeps working.
- Publishing an external, customer-facing installer is blocked below Pro; custom branding is blocked below Enterprise.
- Seat usage is metered immediately. Hard enforcement requires a Conxa-controlled invite API or Clerk webhook cleanup.

**Upgrade path:** Free proves the product works, on one machine, for 30 days. Starter is the first paid tier — one team automating its own processes internally. Pro adds external distribution, the full ops dashboard, and drift detection — an enterprise running its own org, or a vendor scaling as a channel. Enterprise adds white-label distribution, BYOK, SSO, and explicit custom usage overrides — a SaaS vendor or consultancy shipping to its own customers.

---

## What Companies Actually Get

It's worth being explicit about the value proposition so pricing feels justified.

**Without Conxa:**
- Build a custom MCP server from scratch
- Write and maintain Playwright automation scripts
- Handle selector drift when websites update
- Build telemetry and analytics from scratch
- Maintain installers for Windows and Mac
- Manage distribution and updates

**With Conxa:**
- Record workflows in the Build Studio
- Build a signed `.exe` to distribute to customers
- See dashboard analytics for usage, success, failure, and recovery
- Push plugin updates without customers reinstalling
- Give Claude Desktop local MCP tools backed by precompiled workflows
- Keep the plugin signed, trackable, updateable, and supported after the build-heavy first month
- Receive Conxa runtime/healing improvements without rebuilding the product from scratch

**At ₹0, 30-day trial (Free):** A company gets 1 seat, 1 build machine, 25 monthly compile credits, and 500K monthly Human Edit tokens — full self-healing, internal distribution only — enough to prove that Conxa works on their product before paying.

**At ₹19,999/month (Starter):** A company gets 3 seats, 3 machines, 200 monthly compile credits, 2.5M monthly Human Edit tokens, priority builds, and 90-day analytics retention. Distribution stays internal-only. This is the first paid, one-team tier.

**At ₹49,999/month (Pro):** A company gets 10 seats, 10 machines, 500 monthly compile credits, 10M monthly Human Edit tokens, external distribution with a Conxa-branded installer, the full ops dashboard with drift detection, highest-priority self-serve builds, and 1-year retention. This is the highest self-serve tier — an enterprise running its own org, or a vendor scaling as a channel.

---

### Cost Per Tier (What Conxa Spends)

Recalculated for the 2026-09-23 provider swap (Free/Starter → OpenRouter) and the 2026-09-24 Pro
change (Claude Sonnet 5 text + Claude Opus 5.5 vision/Execute, replacing GPT-6 Astra). The customer sees subscriptions; Conxa models an internal build-heavy month for margin
planning. Revenue is INR (what's actually charged); COGS is estimated in USD and converted at an
indicative ₹83/USD for the margin line — **recompute against the live rate and current provider
pricing before using these numbers for planning.** These figures apply the same build-heavy-month
utilization assumption as the prior pricing pass (not the full compile-credit ceiling — realistic
usage during an active first month), scaled by each tier's new-vs-old per-compile cost ratio: Free
~1.8x, Starter ~0.31x (~3.2x cheaper), Pro ~1.3x (driven almost entirely by Opus 5.5 vision). This is a first
pass, not a token-by-token re-derivation.

**The target this table is built around: ≥25% net profit margin in a build-heavy month, rising to
≥90% in a maintenance month** — "net" meaning revenue minus everything (COGS *and* infra/telemetry/
support opex), not gross margin. Every figure below is already net in that sense.

| | **Free** | **Starter** | **Pro** | **Enterprise** |
|--|-----------|-------------|---------|----------------|
| LLM provider | GLM 5.3 Flash + Qwen3 VL, via OpenRouter | Kimi K3 + Qwen3 VL, via OpenRouter | Claude Sonnet 5 + Claude Opus 5.5, direct | Whatever the company requires — BYOK or negotiated |
| Seats | 1 | 3 | 10 | Contracted |
| Machines | 1 | 3 | 10 | Contracted |
| Compile credits / month | 25 | 200 | 500 | Unlimited |
| AI Usage Credits | 500K tokens | 2.5M tokens | 10M tokens | Contracted |
| Internal build-month envelope | ~25 fresh compiles | ~200 fresh compiles | ~500 fresh compiles | Contracted |
| Compile + Human Edit planning cost (indicative) | **~$2–$9** | **~$4–$7** | **~$58–$78** | Contracted — see below |
| Infra, telemetry, installer, payment-fee reserve | **~$10–$20** | **~$25–$40** | **~$65–$105** | Contracted |
| **Total cost/company in build-heavy month (indicative)** | **~$12–$29 CAC** | **~$29–$47** | **~$123–$183** | Contracted |
| **Revenue** | ₹0 | **₹19,999 (~$241)** | **₹49,999 (~$602)** | Custom, from ₹99,999 |
| **Build-heavy net profit margin (indicative)** | — | **~80–88%** | **~70–80%** | Priced to ≥25% floor (see below) |
| **Maintenance-month net profit margin** | — | **~90%+** | **~85–90%** | Priced to ≥90% target |
| **Pro worst case: all 500 credits used on fresh 15-step compiles** | — | — | **~$530 LLM + ~$65–105 reserve ≈ $595–635 → net margin ≈ −5% to +1% (break-even)** | — |

**Pro worst-case risk (kept visible on purpose):** the typical Pro range above assumes a realistic
build-heavy month (mostly cached recompiles), not the full 500-credit ceiling. If a Pro workspace spends
every credit on fresh 15-step compiles (500 × ~$1.06 ≈ $530), the month is roughly break-even, not 70–80%.
Add-on packs stay profitable (~₹200 ≈ $2.40 revenue per credit vs. ~$1.06 fresh-compile cost). The
signal to watch is a Pro workspace sustaining fresh-compile usage well above ~250 credits/month
(see "What to Measure" → Subscription Capacity).

Both self-serve tiers clear the 25% build-heavy floor with wide margin in the typical case — Pro is the
tightest at ~70% low end, nearly triple the floor. Starter got materially *cheaper* to serve (Kimi K3 +
Qwen3 VL 235B A22B Instruct undercut the old GPT-5.4-mini/Gemma pool by ~3.2x); Pro got moderately more
expensive to serve (Opus 5.5 vision at $4/$20 vs. the old Sonnet-4.6-Vision $3/$15), but the Pro
subscription price absorbs it comfortably. **If Pro's build-heavy margin ever compresses toward the
floor** — e.g. a cohort with much lower cache-hit rates than assumed — the levers are the Batch API
(half price) for non-urgent recompiles, then moving Pro's vision anchors to Claude Sonnet 5 (half of
Opus 5.5's price, same provider), before raising price.

**Planning Enterprise to the margin target:** Enterprise is BYOK/negotiated model choice per contract
("whatever the company requires"), so there's no fixed pool cost to table here. Price every Enterprise
contract by plugging the customer's actual chosen models into the same per-step formula used above
(intent tokens × text-model rate + vision-anchor tokens × vision-model rate, scaled by expected
compile volume and cache-hit rate), add the infra/telemetry/support reserve for their scale, and set
the contract price so build-heavy net margin clears 25% and maintenance-month net margin clears 90% —
the same two-number target as Starter/Pro, just solved per-contract instead of read off a fixed table.
A customer requesting an expensive model mix (e.g., Opus-5.5-tier vision at high volume) should see that
reflected in contract price, not absorbed as margin compression.

**Pricing implication:** The four visible meters keep expectations clear while protecting Conxa from
unbounded compile and repair loops. The 30-day trial replaces the old "free forever" framing as the
qualification motion; revenue now comes entirely from Starter, Pro, and Enterprise. Pro is the highest
self-serve tier and where external distribution unlocks; anything beyond Pro — white-label, BYOK, SSO,
explicit usage overrides — moves to Enterprise.

---

## Unit Economics

These scenarios assume a conservative build-heavy month using midpoint per-company cost estimates
from the tier table above (Starter ~₹3,154/company, Pro ~₹12,700/company at ₹83/USD), recalculated
for the 2026-09-23 provider swap and the 2026-09-24 Pro change to Claude Opus 5.5 vision. Margins below are **net profit**, not gross — COGS plus the full
infra/telemetry/support reserve is already subtracted. Maintenance months are materially cheaper
because live plugins usually receive only 1-2 workflow updates while still paying for dashboard,
signing, telemetry, support, update delivery, and Conxa healing/runtime improvements — maintenance-
month net margin stays ~90%+ across tiers regardless of which compile-time models are in the pool,
since maintenance months barely touch the compile pool at all.

### Scenario A: MVP (10 Companies)
Mix: 7 Starter, 3 Pro

| | Value |
|-|-------|
| **Monthly Revenue** | (7 x ₹19,999) + (3 x ₹49,999) = **₹2,89,990** (~$3,494) |
| Build-heavy net cost (indicative) | **~₹60,180** (~$725) |
| **Build-heavy net profit margin (indicative)** | **~79%** |
| **Monthly Profit (indicative)** | **~+₹2,29,810** |

**Break-even:** 1-2 paying companies covers baseline cloud infrastructure. The 30-day free trial's
costs should be treated as acquisition spend, bounded by the machine-limit control rather than an
open-ended free tier. Comfortably above the 25% build-heavy floor even at MVP scale.

---

### Scenario B: Growth (100 Companies)
Mix: 70 Starter, 30 Pro

| | Value |
|-|-------|
| **Monthly Revenue** | (70 x ₹19,999) + (30 x ₹49,999) = **₹28,99,900** (~$34,938) |
| Build-heavy net cost (indicative) | **~₹6,01,800** (~$7,250) |
| **Build-heavy net profit margin (indicative)** | **~79%** |
| **Monthly Profit (indicative)** | **~+₹22,98,100** |

---

### Scenario C: Scale (500 Companies)
Mix: 300 Starter, 200 Pro

| | Value |
|-|-------|
| **Monthly Revenue** | (300 x ₹19,999) + (200 x ₹49,999) = **₹1,59,99,500** (~$192,765) |
| Build-heavy net cost (indicative) | **~₹34,86,200** (~$42,000) |
| **Build-heavy net profit margin (indicative)** | **~78%** |
| **Monthly Profit (indicative)** | **~+₹1,25,13,300** |

---

### Scenario D: Enterprise-Heavy (2,000 Companies)
Mix: 1,000 Starter, 800 Pro, 200 Enterprise at ₹8,30,000 (~$10K) average contract value

| | Value |
|-|-------|
| **Monthly Revenue (indicative)** | **~₹22.60 crore** (~$2.72M) — corrected from a prior arithmetic error (was listed as ₹18.9 crore, which doesn't sum from this mix; ₹1.9999cr + ₹3.9999cr + ₹16.6cr = ₹22.5998cr) |
| Build-heavy net cost (indicative) | **~₹6.31 crore** (~$760K) — Starter/Pro at the per-company figures above, Enterprise assumed priced to the same ~70% net margin as self-serve |
| **Build-heavy net profit margin (indicative)** | **~72%** |
| **Monthly Profit (indicative)** | **~+₹16.29 crore** (~$1.96M, ~$23.6M/year) |

Enterprise contracts should be priced from the customer's requested seats, machines, compile credits,
AI Usage Credits, active installs, telemetry retention, support SLA, BYOK, and SSO — and from the
customer's actual chosen model mix, per "Planning Enterprise to the margin target" above. Do not sell
"unlimited" Enterprise unless the contract has a negotiated usage envelope behind it.

**All four scenarios land at 72–79% build-heavy net profit — well clear of the 25% floor.** Pro's
higher per-company COGS (Opus 5.5 vision) is absorbed by its higher price point; the blended fleet margin
moves inversely with Pro's share of the mix (more Pro companies pulls the blend down toward Pro's own
~70–80% standalone range, more Starter pulls it up toward Starter's ~80–88%). These scenarios use
typical build-heavy usage — a Pro fleet that burns its full 500 credits fresh every month is
~break-even per company (see "Pro worst case" above).

---

## Growth Milestones

| Milestone | Companies | Monthly Revenue | Monthly Net Cost | Profit | Key Actions |
|-----------|-----------|-----------------|--------------|--------|-------------|
| **MVP live** | 10 | ~₹2.9L | ~₹0.60L | +₹2.30L | Ship subscription billing; enforce compile credits, AI Usage Credits, machine limit, and trial expiry |
| **Beta** | 50 | ~₹14.5L | ~₹3.01L | +₹11.49L | Dashboard usage meters for seats, machines, compile credits, and AI Usage Credits |
| **Growth** | 100 | ~₹29.0L | ~₹6.02L | +₹22.98L | Priority build queue, internal COGS alerts, fair-use throttles |
| **Scale** | 500 | ~₹1.6Cr | ~₹34.86L | +₹1.25Cr | Negotiate provider discounts; add Enterprise sales motion |
| **Enterprise** | 2,000 | ~₹22.60Cr | ~₹6.31Cr | +₹16.29Cr | SLA support, custom retention, reserved provider capacity |

---

## Future Horizons — Revenue Projections & Cost Posture

Everything above models Horizon 1, which is what a customer can buy today (`docs/PRD.md` §14.1).
This section projects how the cost and revenue picture extends across Horizon 2 (Scale) and
Horizon 3 (Understand and Optimise). **These are direction, not commitments** — nothing here is a
current requirement, a roadmap date, or ratified pricing (`docs/PRD.md` §14.5 keeps the specific
metrics deliberately unsettled).

### The one rule that survives every horizon

**"Pay for reach, not for runs" holds at every stage**, because at no horizon does Conxa execute or
store customer work (`docs/PRD.md` §14.5, decided 2026-08-21). No marginal cost ever appears that
would force per-run or per-item metering. What changes is only what *reach* means:

| Horizon | What "reach" measures | Pricing status | Conxa marginal cost shape |
|---|---|---|---|
| **1 — Learn and Execute** *(current)* | How far a skill travels: seats, machines, compile credits, distribution rights | Shipped — the tiers above | Compile-time LLM only; execution free forever |
| **2 — Scale** *(future)* | How much work can be in flight: concurrency capacity, plus a new cheaper seat class for people who resolve human reviews without building anything | Direction — not ratified pricing | Still ~zero: workers run on customer infrastructure |
| **3 — Understand and Optimise** *(long-term)* | How much of the organisation is instrumented | Direction — not ratified pricing | ~zero: intelligence layer runs on customer infrastructure too |

Three properties are preserved by construction at every stage: the axis is structural rather than
consumption-based, a customer can plan around it, and nobody is ever charged more for a good month.

### Horizon 2 — Scale (future): where the money and costs go

Horizon 2 shifts from *a workflow a person runs* to *a queue of work the organisation gets through*
— work items, a human-review queue, and concurrent execution workers (`docs/PRD.md` §14.2).

**Cost posture (projection):**
- **Execution COGS stays at zero.** The decision is settled: the workers and the queue run on
  infrastructure the customer owns. Conxa never hosts execution, so the margin structure in this doc
  does not degrade as run volume grows — it is the same reason per-run pricing never becomes necessary.
- **What Conxa actually pays:** platform engineering (queue-aware runtime, review routing),
  update-sync bandwidth, and support surface. No new per-customer variable cost line.
- **The honest trade-off, stated:** customer-owned workers are a heavier ask than a hosted
  alternative and will lose some deals to competitors willing to run everything. That trade was made
  knowingly — the data boundary it protects is what clears regulated-industry security reviews.

**Revenue projection (direction):**
- Two additive meters extend the ladder: **concurrency capacity** (how many work items can be in
  flight) and a **review-resolver seat class** — cheaper than a builder seat, for people who only
  approve/judge/hand-off inside human review points without ever recording or compiling.
- These are **additive layers on the one ladder, not parallel products** — nobody buys Scale without
  Build. Expansion revenue from this layer is not a side effect; it is a substantial part of why
  Horizon 2 needs to exist (see the renewal-risk note below).
- **Gating prediction:** none of this can be sold until unattended session lifetime is solved
  (`docs/PRD.md` §14.5, still open) — a worker nobody signs in cannot survive login-gated systems.
  Do not price or pre-announce Horizon 2 meters before that lands.

### Horizon 3 — Understand and Optimise (long-term): near-zero capex by construction

Horizon 3 builds an operational-intelligence layer over the accumulated execution record
(`docs/PRD.md` §14.3) — deployed **on the customer's own infrastructure**: Conxa ships the framework,
orchestrates retraining, and the customer's hardware executes and stores.

**Cost posture (projection):**
- **Conxa's hosting bill stays flat.** The third application of the locality doctrine means no data
  warehouse build-out, no per-customer storage growth, no GPU spend by Conxa — stage one of the
  intelligence layer explicitly needs no GPU, and stage two's retraining is orchestrated by Conxa but
  executed on the customer's estate.
- **The cost that does grow is upstream, and already paid:** Horizon 3's only input is the structured
  telemetry Horizon 1 captures today. Extending telemetry capture now (per `docs/PRD.md` §14.4) is
  cheap; reconstructing it later is impossible. This is an argument for keeping the §14.4 foundation
  items funded in the current roadmap rather than a new cost line later.
- **Two loops, kept separate on purpose:** the interface-drift telemetry loop stays pooled in the
  cloud (it is more valuable aggregated), while per-company operational intelligence stays local. No
  new cross-tenant data costs appear.

**Revenue projection (direction):**
- Reach becomes *how much of the organisation is instrumented* — an expansion motion aimed at
  executives, priced structurally (per the same three preserved properties), not per query.
- **One open commercial question blocks any concrete pricing:** when a Rung 3 vendor or consultancy
  distributes skills to thirty customers, whose operations does the intelligence describe — the
  distributor's, each end customer's, or neither by default? (`docs/PRD.md` §14.5.) Until that is
  decided, Horizon 3 revenue should not be modelled into these unit economics at all.

### What each horizon earns the next (the funding loop)

From `docs/PRD.md` §14.0 — why investing through the Horizon 1 margins above compounds:

| Asset Horizon 1 earns | Feeds | Accrual speed |
|---|---|---|
| **Market** | Customers who automated one process become accounts needing throughput; Rung 3 multiplies reach (one services relationship → many enterprises) | Per deal, immediately |
| **Money** | Funds the Horizon 2/3 build instead of raising against a vision | Per deal, immediately |
| **Data** | The structured operating record — Horizon 3's only possible input, unrecoverable after the fact | Needs volume; slow at first |
| **Recognition** | Permission for more senior conversations; each horizon must be boring before the next is trusted | Needs volume *and* time — the long pole |

### Where this model is exposed (pricing risks to watch as horizons land)

From `docs/PRD.md` §11:

- **A workspace that compiles constantly and distributes little** inverts the model — all cost, no
  reach. Compile credits exist for exactly this; their calibration is the number to watch as usage
  grows (tracked in "Subscription Capacity" below).
- **Value created at build time but felt at run time.** A customer who records ten skills in month
  one and nothing afterwards keeps receiving the benefit while spend flattens — a renewal-conversation
  risk. Today's partial answer is the operations dashboard; the structural answer is Horizons 2 and 3,
  which is another reason expansion revenue there matters.

### Planning guidance

- Model all current revenue and COGS on the Horizon 1 tables above. Do **not** fold Horizon 2/3
  figures into forecasts until their metrics are ratified and the open questions (§14.5) close.
- Treat Horizon 2/3 engineering as investment against the four assets above, funded from Horizon 1
  margins — the milestones table above shows those margins turning positive from MVP scale (~10
  companies).
- When Horizon 2 pricing work starts, start from the preserved properties (structural axis,
  plannable, success never punished) and reject any meter that violates them — concurrency capacity
  and review-resolver seats both pass; per-run pricing never will.

---

## Cost Levers

### Biggest Impact

**1. Caching is your biggest natural lever (already built)**  
Vision anchor calls are cached by element hash and the workflow-intent call by a steps-summary+URLs hash (`anchor_vision_llm.py`, `workflow_intent.py`). A Starter recompile where 3 steps changed costs ~$0.013, not ~$0.065 fresh; on Pro (Opus 5.5 vision), the same 3-step recompile costs ~$0.21 instead of ~$1.06 fresh — cache hits matter far more on Pro, since Opus 5.5 makes a fresh vision-anchor call ~19x pricier than Free's. Companies iterating daily are still cheap on every tier, but internal fair-use alerts should watch customers that repeatedly hit build-heavy usage patterns, especially on Pro.

**2. Usage naturally drops after launch**
Most companies spend the first month building and polishing the plugin, then move to 1–2 updates per month. This makes ongoing LLM cost much lower than the full-cap build-month model while subscription revenue continues for dashboard, signing, telemetry retention, support, update delivery, and Conxa healing/runtime updates.

**3. Four visible meters, plus capability gates**
Seats, machines, compile credits, and AI Usage Credits are the cleanest customer-visible numeric controls. Workflow recording and plugin creation stay unlimited — there is no limit on how many product slugs a workspace can publish under — while expensive LLM-heavy extraction/repair loops are bounded by the meters, and external distribution reach is bounded by which tier a workspace is on rather than a count.

**4. Vision anchor cache hit rate**
Vision anchor calls dominate compilation cost. Cache hits (same screenshot hash) cost zero tokens. Apps that recompile with minimal visual DOM change will have high anchor cache hit rates, making recompiles near-free. The cache key is the screenshot hash — stable page designs recompile at ~80% lower cost. Adding a "cache hit %" column to the build report gives companies visibility into their recompile efficiency.

**5. Provider volume discounts at scale**
At $10K+/month provider spend (~500 companies), negotiate committed-use pricing for GLM 5.3 Flash, Qwen3 VL, and Kimi K3 via OpenRouter, and direct volume terms with Anthropic (Sonnet 5 and Opus 5.5 — one relationship covers all of Pro). Opus 5.5 vision is the highest-leverage target — it's the single biggest line item in Pro's COGS, so even a modest negotiated discount there moves Pro's build-heavy margin more than the same discount anywhere else in the pool.

**6. Telemetry storage efficiency**
At Enterprise scale (300M events/month), aggregation is important. Roll up raw events into daily summaries after 7 days. Companies rarely need to query individual run-level data older than 1 week. Reduces storage cost by 70–80%.

**7. Free tier is customer acquisition cost**
Free is now a 30-day trial capped at 1 machine, 25 compile credits, and 500K Human Edit tokens, protected by the machine-limit enforcement. As of the 2026-09-23 provider swap, Free is no longer $0 real COGS — it runs on GLM 5.3 Flash + Qwen3 VL via OpenRouter (~$0.020/compilation blended, real money, not a free-tier-rotation shadow price) — so the machine limit and trial expiry now bound genuine CAC, not just a rate-limit nuisance. It's still worth offering: the per-trial cost is a few dollars at most (25 compile credits × ~$0.020 ≈ $0.50 blended, at most ~$1.40 if all 25 are fresh 15-step compiles, plus a small Human Edit token cost), trivial next to the value of proving the product before procurement.

**8. Update CDN costs**
Already negligible. Only matters if plugins become large (>100MB). Keep plugin packages data-only (no embedded browser binaries). Currently well-controlled.

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Customers expect unlimited fresh compiles because recording is unlimited | Support and compile surface grows without matching revenue | Show compile credits explicitly; block first compile when credits are exhausted |
| A Starter/Free workspace tries to distribute externally to bypass the ladder | Undermines the Pro/Enterprise revenue tier | Server-side distribution gate on installer upload and publish, independent of Studio UI hiding |
| AI Usage Credits pool is exhausted by repeated repair loops | Margin erosion and degraded edit experience | Track text + vision token usage by workspace; block only LLM-assisted edits when the pool is exhausted |
| Telemetry volume explodes unexpectedly | $500 -> $5K/month infra cost | Implement event sampling for healthy runs; keep 100% of failures and recovery events |
| Enterprise customer asks for "unlimited" under a fixed price | Contract becomes negative margin | Sell Enterprise as custom usage envelope: seats, machines, compile credits, AI Usage Credits, active installs, retention, SLA, BYOK |
| High churn because customers don't adopt `.exe` | Companies cancel from low ROI | Instrument adoption rate; alert company when <20% of target customers installed |
| A Pro workspace burns most of its 500 compile credits on fresh compiles every month | Opus 5.5 vision makes ~500 fresh 15-step compiles cost ~$530, roughly the whole ₹49,999 fee — Pro margin drops to ~break-even | Track Pro compile credits used and fresh-vs-cached ratio; alert above ~250 fresh credits/month; route non-urgent recompiles through the Batch API (half price); fall back to Sonnet 5 vision if a cohort trends this way |
| Concurrency spikes during compilation | Build queue backs up or providers return 429 | Async compilation with job queue (`/api/v1/jobs`); use provider priority tiers and reserved capacity only when cohort demand proves it |

---

## What to Measure

### Company Health (Track Weekly)
- Active companies (logged in past 7 days)
- New companies added
- Churned companies (cancelled or unpaid)
- Net Revenue Retention (NRR) — are existing companies upgrading or downgrading?

### Build Pipeline (Track Per-Build)
- Compilation success/failure rate
- LLM cost per build
- Build time (p50/p95)
- Selector confidence score (quality proxy)
- First-month vs maintenance-month compilation volume

### Subscription Capacity (Track Daily)
- Seats used vs. plan limit
- Machines used vs. plan limit
- Compile credits used/reserved vs. monthly limit (including add-on packs)
- AI Usage Credits used vs. monthly limit
- Blocked fresh compile, LLM-assisted Human Edit, new-machine, external-distribution, and trial-expired attempts
- Upgrade prompts shown and conversion rate

### Plugin Adoption (Track Daily)
- Installs per plugin (how many customers installed the .exe)
- Active installs (ran at least once in last 7 days)
- Adoption rate = active installs / total installs
- Plugin age and update cadence (build-heavy launch month vs 1–2 updates/month maintenance mode)

### Telemetry Quality (Track Daily)
- Total runs reported
- Success rate (primary selector hit, no recovery)
- Recovery rate (needed Tier A or B — two behavioural tiers; see `docs/TRD.md` §10.1)
- Free-repair share: steps that finished **without ever** reaching a paid Tier B LLM call. Count
  outcomes per step, not attempts — counting attempts double-counts a step that tried two free
  methods and wrongly counts a step that tried a free method, failed, then paid for an LLM call
- Unresolved failures (both tiers exhausted)

As of 2026-08-07 these are no longer manual queries — the operations dashboard computes them
(`app/services/tracking_analytics.py`, surfaced on `/dashboard/healing` and the overview health
score), so the figures in this doc can be checked against measured fleet data rather than estimates.

### Infrastructure Cost (Track Monthly)
- Compilation LLM cost vs. forecast
- Telemetry ingestion cost vs. forecast
- Dashboard hosting vs. forecast
- Total cost as % of revenue by tier (watch repeated fair-use outliers and Enterprise custom contracts)
- Maintenance-month margin after the initial build period

---

## Next Steps

### Week 1–2: Pricing & Billing
- [x] Confirm final public tier limits: Free = 1 seat / 1 installer slot / 50 compile credits / 1M Human Edit tokens; Starter = 3 / 3 / 300 / 10M; Pro = 10 / 10 / 1,000 / 50M; Enterprise = explicit overrides. *(Superseded by the 2026-08-08 repricing — see the Pricing Tiers table above for current limits.)*
- [x] Create Razorpay subscription plan names with `basic` mapped to `starter`.
- [x] Add entitlement fields to subscription state through plan defaults plus `entitlement_overrides`.
- [x] Build tier enforcement in `conxa-cloud/backend/app/services/entitlements.py`:
  - Track monthly compile credit usage and active reservations per workspace.
  - Track Human Edit text + vision token usage by `usage_class`.
  - Derive installer slots from hosted installer metadata and plugin installer records.
  - Leave plugin creation, workflow recording, and local package builds unlimited.
  - Return stable limit codes such as `compile_credit_limit_exceeded`, `human_edit_pool_exceeded`, and `installer_limit_exceeded`.

### Week 3–4: Instrumentation & Dashboard
- [x] Track per-compilation LLM usage separately from Human Edit usage.
- [x] Track Human Edit text + vision token usage against the plan pool.
- [x] Show companies simple subscription usage: seats, installer slots, compile credits, AI Usage Credits.
- [ ] Add internal fair-use alerts for repeated compile/Human Edit outliers and reserve exhaustion.
- [ ] Build customer alerts at 80% of compile credits, AI Usage Credits, machines, and seats.
- [ ] Test billing end-to-end: Free trial -> Starter -> visible limit hit -> upgrade -> limits reset.
- [x] Repriced to the capability ladder (2026-08-08): removed the installer-slot limit, added machines,
  30-day trial expiry, distribution/white-label/ops_tier/BYOK capability gates, and a compile-credit
  add-on. See `docs/PRD.md` §11 and `docs/Implementation-Plan.md`.

### Month 2: Validation
- [ ] Onboard 5–10 pilot companies on the Free trial
- [ ] Measure workflows per plugin, product slugs per workspace, installer rebuild frequency, fresh compiles, Human Edit use, and active installs.
- [ ] Measure internal P50/P95 compilations per active workflow so compile-credit envelopes can be tuned.
- [ ] Validate whether Starter's 200 compile credits and 2.5M Human Edit tokens feel like the natural product-team tier.
- [ ] Collect feedback: do customers understand the four meters without asking for a workflow-count meter?

### Ongoing
- [ ] Monthly: actual cost vs. forecast per tier and per build-heavy cohort
- [ ] Quarterly: pricing review based on cohort usage data
- [ ] At 5,000 internal compilations/month: review provider volume discounts and burst-capacity tier upgrades

---

## Related Documents

- `docs/TRD.md` — Technical deep-dive (compilation pipeline, runtime, recovery cascade)
- `docs/App-Flow.md` — End-to-end product flows
- `docs/Backend-Schema.md` — Billing, telemetry, and storage contracts
- `conxa-cloud/backend/ROUTER_SETUP.md` — Multi-provider LLM setup
- `AGENTS.md` — Repository layout and development instructions

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-09-26 | Kiran | v29: Corrected the build-lifecycle framing. Maintenance months are no longer described as "low LLM usage": after the build month compile spend collapses and AI Usage Credits (Execute chat + Human Edit) become the main LLM cost. Added the fully-drained-pool COGS per tier (Free ~$0.11, Starter ~$6.30, Pro ~$64) and the resulting maintenance-month margin (Starter ~81–87%, Pro ~72–79% — Pro can miss the ≥90% target if its pool is heavily used, driven by Opus 5.5 chat), with levers. Indicative; no plan prices or pool sizes changed. |
| 2026-09-24 | Kiran | v28: Changed Pro to an all-Anthropic pool — Claude Sonnet 5 text, **Claude Opus 5.5** ($4/$20 per MTok, verified against Anthropic pricing) for compile vision and Execute chat — replacing GPT-6 Astra and GPT-5.6 Sol. Set Starter's Execute chat model to Kimi K3 (was Qwen3 VL); Free's GLM 5.3 Flash text/Execute unchanged. Pro compile cost drops to ~$1.06 per fresh 15-step workflow, ~$0.21 per cached recompile, ~$0.38 blended (was ~$2.64 / ~$0.53 / ~$0.95). Recomputed Pro planning cost (~$58–78, total ~$123–183, margin ~70–80%), Unit Economics scenarios (now 72–79%), Growth Milestones, and Total Monthly Operating Cost. Added the Pro worst case (all 500 credits fresh ≈ $530 → ~break-even) to the Cost Per Tier table and Risks. Fixed stale figures: Execute no longer described as sharing the retired Groq/Google/NVIDIA pool; blended Starter $0.023 (not $0.015); Starter 3-step recompile $0.013 (not $0.0084); Free blended $0.020 (not $0.012). |
| 2026-09-23 | Kiran | v27: Corrected the Free/Starter vision model from an assumed "Qwen3 VL 8B Instruct" to the actual model in use, **Qwen3 VL 235B A22B Instruct** ($0.20/$0.88 per MTok on OpenRouter vs. the 8B variant's $0.117/$0.455 — roughly 1.9x pricier). Recomputed every figure that depended on it: Free's blended compile cost moves $0.012→$0.020/compilation (real cost/CAC ratio ~1.8x the old free-tier-rotation baseline, not ~1.1x), Starter's moves $0.015→$0.023/compilation (~3.2x cheaper than the old GPT-5.4-mini/Gemma pool, not ~5x). Updated the per-step/scenario tables, the Cost Per Tier table's planning-cost ranges and totals (Free ~$12–$29, Starter ~$29–$47, both still comfortably above the 25%/90% net-profit targets — Starter's margin barely moves, ~80–88%), and the per-company Starter figure feeding Unit Economics (₹3,030→₹3,154; left the four scenario tables' own numbers unchanged since the resulting margin shift is sub-1% and within existing rounding). Pro's numbers (Sonnet 5 + Astra) are unaffected — this was a Free/Starter-only correction. |
| 2026-09-23 | Kiran | v26: Swapped the compile-time LLM provider pools. Free and Starter move off the free-tier Groq/Google AI Studio/NVIDIA NIM rotation onto real, billed OpenRouter models — Free: GLM 5.3 Flash (text) + Qwen3 VL 8B (vision); Starter: Kimi K3 (text) + Qwen3 VL 8B (vision) — both +5.5% OpenRouter platform fee. Pro moves to Claude Sonnet 5 (text, direct Anthropic) + GPT-6 Astra (vision, direct OpenAI); Execute's multimodal model is GPT-5.6 Sol on Pro, GLM 5.3 Flash on Free. Enterprise stays "whatever the company requires" (BYOK or negotiated), now priced per-contract against an explicit target instead of a fixed table. Recomputed every downstream number: blended per-compilation cost (Free ~$0.011→~$0.012, Starter ~$0.075→~$0.015 [5x cheaper], Pro ~$0.292→~$0.95 [3.25x pricier, driven almost entirely by Astra's $10/$50 vs. the old Sonnet-4.6-Vision $3/$15]), the Cost Per Tier table, all four Unit Economics scenarios, and Growth Milestones. Reframed margin figures from "gross margin" to **net profit margin** (revenue minus COGS *and* infra/opex — the old table was already computing this despite the gross-margin label) against the requested target of **≥25% net profit in a build-heavy month, ≥90% in a maintenance month**; every tier and scenario clears both comfortably (self-serve build-heavy net margins now run ~50–88%, fleet blends ~67–71%), with Pro identified as the tier to watch since Astra is the dominant cost driver — the documented mitigation is routing vision-anchor calls to GPT-5.6 Sol instead of Astra before touching price. Added an explicit "Planning Enterprise to the margin target" method for pricing BYOK contracts against the same two-number target. Also corrected a pre-existing arithmetic error in Unit Economics Scenario D (revenue was listed as ₹18.9 crore but the stated company mix sums to ₹22.6 crore) while recomputing that table anyway. |
| 2026-09-23 | Kiran | v25: Restructured for navigability — no data or figures changed. Moved "What Companies Actually Get" from its orphaned spot after Future Horizons to sit directly under Pricing Tiers/Revenue Model (its natural companion — the value prop that justifies the price a paragraph above it), immediately before "Cost Per Tier (What Conxa Spends)". Added a Contents line under the header linking to every top-level section, since the doc runs ~850 lines with no prior navigation aid. Heading levels and all other section order left untouched to avoid breaking existing anchor links from other docs. |
| 2026-09-23 | Kiran | v24: Re-checked LLM provider pricing/limits against current docs (previously checked August 9, 2026). GPT-5.4/GPT-5.4-mini, Together AI Gemma 4 31B, Groq free tier (30 RPM/6K TPM/14,400 req/day), Google AI Studio free tier (Flash/Flash-Lite only since the April 2026 Pro removal), and NVIDIA NIM free tier (~40 RPM, 1,000 credits, forever-free) are all still unchanged. One real change: Claude Sonnet 5's $2/$10 per MTok pricing, previously logged here as a temporary introductory rate reverting to $3/$15 on September 1, 2026, is now confirmed **permanent** — that reversion was cancelled. Corrected the stale paragraph accordingly; this is a standing discount vs. Sonnet 4.6 Vision worth an actual Enterprise-pool router migration evaluation, not something to dismiss as an expiring window. No changes to Conxa's own subscription pricing or unit economics this pass. |
| 2026-09-17 | Kiran | v23: Conxa Execute's standalone backend, dedicated LLM provider keys, and personal wallet/subscription (BYOK) were deleted — it now shares conxa-cloud's backend, Clerk app, and managed provider pool entirely, with access/billing folded into the existing team plan (`docs/TRD.md` §3.6). No underlying token pricing changes; the practical effect is that Execute chat now competes for the same free-tier provider rate limits as compile/Human Edit rather than having its own separate budget — worth watching if Execute usage grows. |
| 2026-09-16 | Kiran | v22: Renamed "Human Edit pool" to "AI Usage Credits" throughout forward-looking body text and tables — the pool is no longer a Build Studio-only concept now that Conxa Execute seat grants (a workspace can hand out Execute access, billed against this same shared pool) draw from it too. Added the `execute_seats` meter (Free 1 / Starter 25 / Pro 100 / Enterprise contracted) alongside the existing seats/machines/compile-credits meters. No underlying token pricing or reserve-size changes — display rename plus one new capability meter. See `docs/Implementation-Plan.md` 1.15 and `docs/TRD.md` §13.7. |
| 2026-09-02 | — | v21: Re-synced execution-time recovery costs with `docs/TRD.md` §10.1. The product now has two behavioural tiers — A (in-process, zero tokens) and B (armed agent round, digest + screenshots). The old four-number ladder and the "text-only first, vision later" split are gone; ceiling env `CONXA_MAX_RECOVERY_TIER` still uses 2 vs 4. |
| 2026-08-22 | Kiran | v20: Re-synced the doc with `docs/PRD.md` §11 and the enforced tier table (`PLAN_LIMITS`/`ADDON_TIERS` in `conxa-cloud/backend/app/services/entitlements.py`). Fixed Human Edit pool figures that still carried pre-repricing numbers (1M/10M/50M → 500K/2.5M/10M for Free/Starter/Pro) and the stale per-compilation LLM costs that contradicted the doc's own per-step math ($0.54/$0.11/$1.93/$0.39 → $0.21/$0.042/$0.81/$0.162, blended $0.195/$0.695 → $0.075/$0.292). Replaced the fictional "+25 credits/mo add-on at ₹4,999" with the real add-on catalog: +20 @ ₹3,999, +50 @ ₹9,999, +100 @ ₹19,999, +250 @ ₹49,999, each stacking Human Edit tokens too. Aligned Free's distribution wording with PRD §11 — installs are unlimited on every tier; what Free is capped at is one build machine ("capped at 1 install" was stale). Added the installer-icon capability row (Starter and up, per PRD 2026-08-09). Marked the old Week-1–2 limits confirmation as superseded; updated the Month-2 validation item to current Starter numbers. Added "Future Horizons — Revenue Projections & Cost Posture" from PRD §11/§14: how "pay for reach, not for runs" extends across Horizon 2 (concurrency capacity + review-resolver seats on customer-owned workers) and Horizon 3 (instrumentation-based reach on customer infrastructure), what each horizon earns the next, the §14.5 open questions that gate pricing work, and explicit planning guidance not to fold unratified horizon figures into forecasts. |
| 2026-08-09 | Kiran | v19: Re-verified all LLM provider pricing against current provider docs (previously checked June 3, 2026) — GPT-5.4-mini, GPT-5.4, Together AI Gemma 4 31B, and Claude Sonnet 4.6 are all **unchanged**. Added real Claude Opus 4.8/5 pricing ($5/$25 per MTok, down from the retired Opus 4.1's $15/$75) in place of the old unpriced "quality upgrade path" note. Flagged new Claude Sonnet 5 introductory pricing ($2/$10 through Aug 31, 2026, converging to Sonnet 4.6's $3/$15 on Sept 1) — not worth a router migration for the discount alone. Replaced the free-tier plan's vague "$0.075/1M" shadow price with the actual current Gemini Flash-Lite rate ($0.10/1M) and recomputed the notional free-plan cost figures accordingly (real Conxa cost remains $0). Documented concrete, sourced free-tier rate limits for Groq (30 req/min, 6,000 TPM, 14,400 req/day), Google AI Studio (5–15 req/min, 1,000 req/day — noting Google pulled Pro-family Gemini models from the free tier on April 1, 2026, leaving only Flash/Flash-Lite eligible), and NVIDIA NIM (~40 req/min, 1,000 free credits). Fixed the stale "Last Updated" header, which still read July 2 despite the doc's own revision history extending to Aug 8. No changes to Conxa's own subscription pricing, tier structure, or unit economics — this pass only re-verified upstream LLM provider costs. |
| 2026-08-08 | Kiran | v18: Repriced around the capability ladder following the Centelon pilot demo (docs/PRD.md §11). Switched headline pricing from USD to INR (Starter ₹19,999/mo, Pro ₹49,999/mo, Enterprise from ₹99,999/mo). Removed the installer-slot meter entirely — no limit on how many product slugs a workspace publishes under; added a machines meter (1/3/10) as the trial-abuse and seat-integrity control instead. Free became a 30-day trial, capped at 1 install, rather than a permanent tier. Lowered compile credits (25/200/500) and Human Edit pool (500K/2.5M/10M) from the prior numbers. Added capability gates that aren't numeric meters: distribution (internal-only on Free/Starter, external on Pro/Enterprise), installer branding (Conxa on Pro, white-label on Enterprise), ops tier (none/basic/full), and BYOK (Azure OpenAI, Enterprise only). Added a compile-credit add-on (+25/mo for ₹4,999, Starter/Pro). Unit-economics scenarios and cost-per-tier tables recomputed at the new numbers; margins are marked indicative pending a full re-derivation from current per-token provider pricing. |
| 2026-08-07 | Kiran | v17: No economics changes. Corrected the "Telemetry Quality" metric list, which still referred to a five-tier cascade (there are four), and added the free-repair-share metric with its per-outcome counting rule. Noted that these metrics are now computed by the operations dashboard rather than estimated. |
| 2026-07-02 | Kiran | v16: Added "Execution-Time Recovery Tiers" section documenting per-tier token/time cost during customer-side execution (Tier 1/2 zero-token, Tier 3/4 ~2,500–3,500 tokens + 10–15s added per occurrence), based on a live manual test of the recovery cascade. Added a Tier 3-only vs Tier 4-only cost breakdown (~1,625 / ~1,575 tokens respectively vs. ~3,000 combined — they always fire bundled today) and worked full-workflow examples at 0/1/2/3 recovery occurrences (~1,200 / ~4,200 / ~7,200 / ~10,200 tokens). Also added "What This Costs the End Customer Running a Plugin Locally" — translates that into Claude Pro/Max 5-hour session message allowances (~45/225/900 msgs) and approximate workflow-runs-per-session at 0/1/2 avg. recoveries. All of this is customer-paid, not a Conxa cost — included for compile-time decision guidance and customer-facing messaging. |
| 2026-06-30 | Kiran | v15: Removed LLM selector generation from cost model — `IdentityBundle` + `selector_grammar.py` are now the sole selector generators (deterministic, zero tokens). Per-step LLM calls drop from 2–7 to a fixed 2 (intent + vision anchor). All cost tables, tier margins, scenario COGS, and cost levers updated accordingly. Build-heavy gross margin improves from ~56–67% to ~68–81% for Starter. |
| 2026-06-10 | Kiran | v14: Replaced visible workflow/plugin caps with four customer meters: seats, installer slots, monthly compile credits, and monthly Human Edit pool |
| 2026-06-07 | Kiran | v13: Added monthly Human Edit text + vision token reserves: Trial 1M, Starter 10M, Pro 50M, Enterprise custom |
| 2026-06-07 | Kiran | v12: Adjusted team seats to 1 / 3 / 10 across Trial, Starter, and Pro |
| 2026-06-07 | Kiran | v11: Removed monthly installer-build limits; plugin package builds are unlimited for testing, live installers follow the live-plugin caps, and analytics retention is now 14 days / 90 days / 1 year |
| 2026-06-07 | Kiran | v10: Removed the Max self-serve plan and shifted limits down: Trial receives the former Starter benefits, Starter receives the former Pro benefits at $299/month, and Pro receives the former Max benefits at $799/month |
| 2026-06-07 | Kiran | v9: Switched revenue model from public compilation credits to subscription-style Starter/Pro/Max tiers with visible live-plugin, workflow, installer-build, seat, and retention limits; added hard gates for `.exe` builds when plugin caps are reached |
| 2026-06-03 | Kiran | v8: Recalculated paid-plan compilation costs using current GPT-5.4-mini, GPT-5.4, Together AI Gemma 4 31B, and Claude Sonnet 4.6 pricing; added burst-capacity guidance for OpenAI tiers, Anthropic Priority/custom limits, and Together dedicated endpoints |
| 2026-06-03 | Kiran | v7: Updated paid provider pools to GPT-5.4-mini + Gemma 4 31B for Starter/Pro and GPT-5.4 + Claude Vision for Enterprise; matched plugin builds to workflow compilations; added Human Edit LLM costs, retention positioning, 24/7 support, and Pro+ white-label `.exe` |
| 2026-05-30 | Kiran | v6: Two-pool LLM strategy — Trial uses free-tier rotation, paid plans use two paid keys for high TPM burst; updated blended paid-plan cost (superseded) |
| 2026-05-30 | Kiran | v5: Compilations as hero metric; 300/Starter, 1,500/Pro; plugin count not the constraint (superseded) |
| 2026-05-30 | Kiran | v4: Real tier limits (3 plugins / 100 compilations / 30 builds / 3 .exe for Starter); corrected LLM calls to per-step not per-workflow (superseded) |
| 2026-05-30 | Kiran | v3: Corrected model — Conxa builds .exe, companies distribute, execution is on customer machines |
| 2026-05-30 | Kiran | v2: B2B marketplace model (superseded) |
| 2026-05-30 | Kiran | v1: Per-user SaaS model (superseded) |
