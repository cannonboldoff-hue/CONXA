# CONXA internal ground truth: capabilities, business model, design decisions, gaps (as of 2026-11-05)

Method: internal-source read of repo docs (grep + offset reads; no web). "Source" = file path under C:\Users\kiran\Desktop\Conxa. Status tags: [SHIPPED] per TRD/Done/CLAUDE.md; [PROPOSED]; [NOT BUILT]. Where docs disagree, flagged as DRIFT. Nothing here is externally validated; no paying-customer evidence was found in the repo (only the 7 Aug 2026 Centelon pilot demo).

## 1. Business model: who pays whom, and what distribution implies

### Takeaway
Conxa sells to the workspace that BUILDS skills (vendor, enterprise ops team, or consultancy), never to the people who run them. Pricing thesis: "pay for reach, not for runs." Executions and installs are unlimited on every tier because they run on the customer's machine at zero marginal cost to Conxa; Conxa's COGS is compile-time LLM plus small hosting/telemetry.

### Cited Findings
- Thesis and unlimited runs: "Conxa sells to the workspace that builds skills, never to the people who run them... pay for reach, not for runs. Installs and executions are unlimited on every tier, including the free one" — docs/PRD.md §11.
- Three properties claimed: costs sit at build time; Tier B recovery runs on the customer's own AI subscription so reliability gains do not raise Conxa COGS; "success is never punished" (a million runs cost Conxa the same as two) — docs/PRD.md §11.
- Price ladder (INR, billed via Cashfree): Free ₹0 / 30-day trial; Starter ₹19,999/mo; Pro ₹49,999/mo; Enterprise custom from ₹99,999/mo — docs/cost_model.md "Pricing Tiers".
- Limits per tier (Free/Starter/Pro): seats 1/3/10; machines 1/3/10; Execute seats 1/25/100; compile credits/mo 25/200/500 (Enterprise unlimited); AI Usage Credits/mo 500K/2.5M/10M tokens; BYOK (Azure OpenAI) Enterprise only — docs/cost_model.md "Pricing Tiers".
- Capability gates, not just numbers: Free/Starter = internal distribution only; Pro = external, Conxa-branded; Enterprise = external white-label + SSO + BYOK; custom installer icon from Starter up; ops dashboard none/basic/full — docs/PRD.md §11 ladder table; docs/cost_model.md.
- Compile credit add-on packs: +20 at ₹3,999, +50 at ₹9,999, +100 at ₹19,999, +250 at ₹49,999; never expire — docs/cost_model.md.
- Metered axes: seats, machines (trial-abuse control), compile credits, human-edit tokens. Recording, local builds, deterministic edits unlimited — docs/PRD.md §11; docs/cost_model.md.
- Free keeps full self-healing deliberately ("a free plan without it teaches people the product breaks") but is capped at 1 machine / 30 days — docs/PRD.md §11.
- Distribution flow: vendor records in Build Studio, publishes a skill pack (primary, versioned release action) to Conxa Cloud; installer is a thin static artifact holding only `pack.json` (identity, installer_version, sync/tracking endpoints/tokens), no skill files; runtime delta-syncs every skill on first run and each update — CLAUDE.md "Deployment"; docs/TRD.md §5, §17.
- Delta sync is per-skill granular (republish one skill does not re-ship the pack); within a changed skill all ~5 JSON files ship — docs/Sales-Blockers.md 1.4; docs/TRD.md §17.
- Runtime self-updates via signed manifest (Ed25519, manifest_version 2; deps `conxa-runtime` + `conxa-app`, SHA-256 required); two-layer exe+app design means app logic updates without reinstalling — CLAUDE.md; docs/Auth-and-Updater.md; docs/Sales-Blockers.md (manifest signing enforced in prod).
- ICP ladder: Rung 1 engineer proving it (conviction, not revenue); Rung 2 enterprise running own ops ("the primary revenue customer"; hypothesis band 200–5,000 employees, flagged "working hypothesis from the pilot, not validated across closed deals"); Rung 3 SaaS vendor/IT-services channel ("highest-leverage account", compounds) — docs/PRD.md §6.
- Qualifying shape: process crosses 3+ browser systems, repeats daily/weekly, person does it in a browser, no realistic API path, stable login (not per-login OTP), Windows machines — docs/PRD.md §6.
- Cost model unit economics (indicative, build-heavy month): MVP 10 cos (7 Starter, 3 Pro) revenue ₹2,89,990, net margin ~79%; 100 cos ~79%; 500 cos ~78%; 2,000 cos incl. 200 Enterprise at ₹8.3L ACV ~72%. Monthly opex: ~$45 (10 cos), ~$176 (100), ~$996 (500) — docs/cost_model.md "Unit Economics", "Total Monthly Operating Cost". These are modelled projections, not actuals.
- Compile COGS per plan (model prices as of 2026-09-23/24 in the doc): Free ~$0.055 fresh 15-step workflow (~$0.020 blended); Starter ~$0.065 (~$0.023 blended); Pro ~$1.06 fresh (~$0.38 blended; Opus 5.5 vision is the biggest lever); selectors cost $0 — docs/cost_model.md "Cost Per Compilation by Plan".
- Stated exposure: a workspace that compiles constantly and distributes little inverts the model; value created at build time but felt at run time = renewal risk (ops dashboard is "a partial" answer) — docs/PRD.md §11 "Where this model is exposed". Pro worst case (all 500 credits fresh each month) ~break-even per company — docs/cost_model.md.
- Future horizons: H2 Scale (workers/queue on customer infra, concurrency and cheaper "review seats" as reach), H3 Understand/Optimise (intelligence on customer infra). "Direction, not a commitment." Doctrine: "cloud holds neither work nor data" — docs/PRD.md §14.
- Standalone scheduler/runner exists locally (cron, tray, daemon, no cloud scheduler) — docs/TRD.md §4.8 [SHIPPED]; per-machine host locks mean two runner VMs can overlap.

### Inferences
- Business consequence of local execution: no per-run revenue means no incentive conflict with heavy use, and a pricing story iPaaS/RPA/cloud-agent vendors cannot copy without metering runs. But revenue is decoupled from realised value, so renewal depends on proving ongoing value (dashboard) — a real SaaS-expansion weakness the repo admits.
- Vendor-publishes/customer-installs makes the vendor the distribution channel (customer acquisition cost partly borne by vendors/consultancies), and the delta-sync means one fix propagates to every install; the cost is that Conxa must run a signed update/telemetry fleet (hosting cost ~$500/mo at 500 cos modelled).
- Pricing is Indian-rupee anchored (Cashfree billing); Enterprise "unlimited" is flagged as not to be sold without a negotiated envelope.

### Gaps
- No actual revenue, customer count, or paid conversion data anywhere in the repo; all scenarios are modelled. ICP band is unvalidated (PRD §6). Only one named pilot (Centelon demo, 2026-08-07); the referenced Conxa-Pilot-Conclusions.pdf was not read.
- DRIFT on LLM providers: PRD §11 says free tier routes to free-tier providers; cost_model (revised 2026-09-23) says all tiers use billed pools (OpenRouter for Free/Starter, Anthropic direct for Pro); CLAUDE.md says Groq/Google AI Studio/NVIDIA NIM are default. Treat provider/COGS figures as time-sensitive.
- DRIFT: cost_model still prices a per-step "vision anchor" call (~$0.00363/step Free, ~$0.07/step Pro) whereas CLAUDE.md Key Invariants and TODO BUILD-58 say the compile no longer generates vision anchors (video model words replace them). Real COGS per compile may be lower than modelled; Pegasus/TwelveLabs video cost (~$0.03–0.05 per recorded minute, docs/Understanding-First-Harness.md) is not in the cost model totals I read.
- Margin and price assumptions are not validated against real willingness to pay ("Nobody has said yes" — docs/Sales-Blockers.md).

## 2. Distinctive design decisions, with business consequence and cost

### Takeaway
The core pattern is compile-once, replay-deterministically: LLM spend is concentrated at author time and at rare Tier B recovery (paid by the end user's own agent subscription), with several safety invariants that trade speed/convenience for predictability. Many pieces are shipped; some (popup general model, cross-customer app-map sharing, fleet flywheel automation) are proposals.

### Cited Findings (each: decision | status | business outcome | cost/limit)

1. Compile-once, deterministic replay; Tier A recovery = zero LLM tokens [SHIPPED]
 - Tier A (exception ladder + a11y re-probe, transient retry, re-hover, dialog scope) is in-process zero-token; Tier B is "agent-mediated": runtime never calls the model itself, it hands a ranked candidate digest + screenshots to the customer's agent which nominates; runtime verifies — docs/TRD.md §10.1; CLAUDE.md Key Invariants.
 - Outcome: bounded, predictable run cost and latency; reliability improvements do not raise Conxa COGS. Tier B costs ~2,500–3,500 tokens/round (~3,000 typical), +5–8s per round; clean run ~1,200 tokens total (MCP baseline); 1 recovery ~4,200; 2 ~7,200; 3 ~10,200 — docs/cost_model.md §6 (estimates; doc says real counts were not measured because no API key/Console access at the time).
 - Cost/limit: max 2 Tier B rounds per step (STAGNATION_LIMIT=2), RETRY_BUDGET_MAX=3; Tier A never changes WHICH element — deleted stages that guessed by text affinity — docs/TRD.md §10.1. Tier B burden lands on the end customer's Claude allowance (docs/cost_model.md: Pro ~45 messages/5hr, so ~22 runs/5hr at 1 recovery/run). Known hole: EXEC-28 — no Tier A stage heals an element whose accessible name changed (renames escalate to paid LLM round or hard failure in Studio) — TODO.md EXEC-28. Hover-reveal steps still fail at live replay — TODO.md EXEC-29a.
2. Multi-signal IdentityBundle + deterministic selector grammar [SHIPPED]
 - IdentityBundle/selector_grammar.py are sole selector generators on primary compile path; no LLM writes selector strings (narrow exceptions: 1-click fix, "draw a new region") — CLAUDE.md; docs/cost_model.md.
 - Outcome: selectors cost $0 to compile; skills degrade gracefully across UI drift (PRD claim vs RPA single-selector scripts) — docs/PRD.md §10.
 - Cost/limit: durability scores are hand-set priors, never measured against real page mutations; orthogonality of "independent" signals unverified — TODO.md BUILD-31. Dated 2026-07 audit also called "multi-signal compile" the real moat but said it "works" — research-analysis/07-go-to-market/pre-sales-readiness.md.
3. Resolver margin gate (never blindly picks candidate[0]) [SHIPPED]
 - Winning candidate must beat runner-up by uniqueMargin (default 0.15) else falls to next signal; override selectors from agents are validated against the recorded fingerprint and rejected on tie, never `.first()` — CLAUDE.md; docs/TRD.md §10.1.
 - Outcome: fewer wrong-element clicks (the trust-ending failure); the July audit calls silent wrong action "the showstopper" — research-analysis/07-go-to-market/pre-sales-readiness.md. Cost: more fail-closed runs (a clean failure rather than a guess).
4. Outcome forks: decision popups never answered on anyone's behalf [SHIPPED]
 - Recorded decision dialogs ("Replace / Keep both / Cancel") compile to `outcome_fork`, not `try_dismiss`; consequential option never runs under `auto` without author approval (`policy.author_approved`, written only by `set_fork_policy`); runtime stops rather than guesses; nothing pauses by default (unknown_policy: continue); Tier 1 ladder, learned dismissals and agent dismissals refuse to dismiss a decision dialog — docs/TRD.md §11.4; CLAUDE.md Key Invariants.
 - Outcome: auditable "won't confirm a destructive overwrite for you" answer to finance/HR/payroll buyers; Cost: limits ("Not built": native-dialog forks, insertable forks in Human Edit, learning forks from telemetry) — docs/TRD.md §11.4. Generalised popup/state model is [PROPOSED] only (design proposal 2026-11-05, TODO EXEC-49) — docs/Popup-and-State-Handling.md.
5. Safe-action system / destructive-step guardrails [SHIPPED 2026-08-29, PROD-3]
 - Destructive steps (pay/delete/submit) classified at record time, scoped to one record, self-repair never substitutes a different-looking button — docs/Sales-Blockers.md; patch_gate rules for ai_review/run_if — docs/TRD.md §11.3, CLAUDE.md. No Studio UI yet for entity-binding confirmation/Strict Mode/safety score (PROD-3-UI) — docs/TRD.md §17.
6. Understanding-first pipeline [SHIPPED in code, doc DRIFT]
 - Understand→ask→incorporate→compile→test: TwelveLabs Pegasus video rows + browser events merged on one clock; one text call writes summaries and ranked questions with best guesses that need author Confirm; code validates the model's answer (drops bad refs/literals); testing blocked until questions answered; Pegasus unavailable blocks rather than falling back — CLAUDE.md Key Invariants; TODO.md BUILD-58 (done 2026-10-27, left: Human Edit still depends on stored frames, several known gaps); docs/TRD.md §7 (timeline_understanding.py).
 - Video upload is opt-in per workspace, off by default; SG-21 flags that the recording video is sent to TwelveLabs via the Conxa Cloud (Medium, mitigated); cost ~$0.03–0.05 per recorded minute — docs/Understanding-First-Harness.md; docs/Security.md SG-21.
 - Outcome: fewer wrong guesses baked into skills, author-side effort moves to answering ≤5 questions (cost: authoring friction and a third-party dependency; blocked if Pegasus down). DRIFT: docs/Understanding-First-Harness.md header still says "design proposal, nothing built yet (2026-10-26)" while TODO.md BUILD-58 says built; its own evidence on video models for UIs is weak (cites GPT-4o 19.5% on Act2Cap GUI narration) — same doc.
7. Human Review gate / Human Edit [SHIPPED]
 - Compiler's second-opinion pass writes meaning (input bindings, placeholders, phase, try_dismiss, additive non-selector assertions, flags noise) into steps; "Human Review is the gate"; compile no longer byte-identical — CLAUDE.md Key Invariants; TODO.md BUILD-25. Editor exposes recovery view, fingerprint, diagnostics (BUILD-5) — Done.md.
 - Cost: authoring effort; no authoring UI for try_dismiss/wait_for_one_of/for_each bodies (PROD-3-UI) — docs/TRD.md §17.
8. Parameterisation, extract, run_if, for_each, ai_review, handover [SHIPPED mostly]
 - `parameterize_literal`/`parameterize_click_text` rewrite only URLs, bindings, text signals in code; `extract` uses the same IdentityBundle walk and writes only `inputs[output_name]`, empty = non-retryable failure; `run_if` reads inputs only and is refused on ai_review/handover/forks; `on_missing: skip` only on provable absence — CLAUDE.md Key Invariants; docs/TRD.md §11.2.
 - `ai_review`: explicit author-placed reasoning checkpoint outside the recovery cascade, answered by any MCP caller (or Studio's metered LLM); cannot directly precede or gate a destructive step except confirm-only — docs/TRD.md §11.3. Not built: reference screenshot for inserted ai_review, PDF attachments, numeric run_if comparison, if_present/outcome_fork branching on an answer — same section.
 - `handover` step yields the live page to a person (2FA, CAPTCHA, e-signature); three resume signals; in-call 120s then park (30 min TTL); not durable across runtime restart, not resumable inside for_each (EXEC-22) — docs/TRD.md §11.4 Hand-Over. Outcome: turns the PRD's former "per-login OTP is a blocker" partially into a designed human step, but PRD §6 still lists per-login OTP as a hard disqualifier.
9. App maps [SHIPPED phases 1–7; phase 8 NOT BUILT]
 - Derived, LLM-free, scrubbed map (screens/actions/popups) per host per workspace; warns authors of popups other workflows met; agents see "(works in host)"; author's `app_policy` is the only standing answer; shared only inside one workspace; cross-customer sharing not built and needs opt-in, k-anonymity and SG-20 re-review — docs/TRD.md §11.5; docs/Security.md SG-20; TODO APP-1.
 - Outcome: institutional knowledge accumulates per workspace and helps authors. Cost/limit: SPAs that keep the URL collapse into one screen (v1).
10. Local execution; auth never leaves the machine [SHIPPED]
 - Auth state excluded from build output; per-company token via keytar, AES-256-GCM session encryption; cloud "does not record, compile or execute"; execution and credentials stay on customer machine — CLAUDE.md; docs/PRD.md §4, §14.5.
 - Outcome: shortest path through IT/security gate; regulated-industry fit. Cost: heavier ask than hosted competitors ("will lose deals to a competitor willing to run everything" — docs/PRD.md §14.5); Windows-only recorder, Windows runtime today; no per-end-user identity at runtime (SG-13, one identity per workspace) — docs/TRD.md §17; docs/Security.md.
11. Concurrency with per-platform host locks [SHIPPED RT-3]
 - Up to CONXA_MAX_CONCURRENT_RUNS (default 5, flat) parallel runs; mutex per hostname so two runs never mutate the same external platform; cross-process file lock with heartbeat; different platforms fully parallel — docs/TRD.md §4.6. Cost: flat cap (RT-3-CAP-SIZING), per-skill retry budget shared across concurrent same-skill runs (RT-3-RETRY-BUDGET), host locks per machine only — TODO.md; docs/TRD.md §4.8.
12. Telemetry, drift detection, ops dashboard [SHIPPED; flywheel automation NOT BUILT]
 - Runtime posts run/recovery/failure events; dashboard: health score, risk queue, per-workflow reliability, self-healing analytics, ROI view with editable time-saved assumption (labelled estimate), drift detection, runtime/fleet registry — docs/Sales-Blockers.md; docs/PRD.md §8.
 - PRD claims a "telemetry loop" is the moat, then immediately says "Right now this is a diagram, not a moat... becomes real somewhere around customer ten" — docs/PRD.md "What Our IP Actually Is". `drift_detected` is emitted but "nothing ever acts on it" (BUILD-28); durability flywheel (classify, CIR patch, canary, auto-rollback) not built (EXEC-2) — TODO.md.
13. Auto-updating two-layer runtime [SHIPPED]
 - Host exe stays small/rarely changed (built --no-bytecode), obfuscated app layer updates independently, min_host gate, versioned-dir rollback, CI execution gate replays a real skill against MIN_HOST exe before release — CLAUDE.md; docs/Sales-Blockers.md. Outcome: ship fixes to every install without reinstall; cost: obfuscation/complexity; skill packs themselves not cryptographically signed (bearer sync token only; UPD-1) — TODO.md UPD-1; docs/Security.md SG-08.
14. Multi-agent-host MCP registration [SHIPPED]
 - `register-mcp` writes the runtime's MCP entry into ~20+ agent hosts (Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, Gemini CLI, Cline, Zed, Copilot CLI, Codex CLI, Goose, OpenCode, etc.) with atomic ownership-checked edits and `durable_context` discoverability files; config editing lives only in the runtime, not NSIS — docs/TRD.md §4.3; CLAUDE.md. Outcome: not locked to one agent vendor (answers "everything depends on Claude Desktop", rated Medium-High in research-analysis/conxa-critical-analysis.md). Cost: breadth of host table to maintain; exact per-host support quality untested by me.
15. Conxa Execute (form + BYO chat front end) [SHIPPED v0.1, partly in flux]
 - Desktop app, thin client of conxa-cloud; Clerk PKCE; chat via metered proxy on the workspace's tier model pool (`execute_chat`, drawn from AI Usage Credits); in-app browser panel borrowed by the runtime via CDP; local sessions only — docs/TRD.md §3.6, §4.7. Execute seats priced into plans (1/25/100). Outcome: lets non-technical staff run skills without owning a Claude/agent seat. Cost: introduces a metered LLM cost to Conxa (Execute chat) that contradicts the "no marginal cost" story for that surface; CLAUDE.md also mentions a separate conxa-execute/backend with its own Postgres and token-pack billing, while TRD §3.6 says "no backend of its own" and TODO EXEC-43 says the merge is unfinished — DRIFT / in flux.
16. Skill-pack-as-artifact discovery: skills become MCP tools; agentic-discovery strategy argues deterministic skills complement ARD/agents ("agent decides what; skill is how") — research-analysis/07-go-to-market/agentic-discovery-strategy.md; docs/PRD.md §10.

### Inferences
- The cost-structure story is internally coherent: Conxa pays compile and hosting; customers pay for Tier B only in their own agent allowance. The weak links are (a) Execute chat and Human-Edit repair, which ARE metered LLM costs to Conxa, and (b) any customer whose skills lean on Tier B often (cost/latency shift to customer, harming the experience).
- The strongest business-value pairings are: local execution -> security/regulated sales; margin gate + forks + guardrails -> "won't touch the wrong record" trust; delta-sync + signed updates -> low-touch fleet maintenance; multi-host MCP -> distribution not tied to one AI vendor.
- Authoring effort is real (understanding questions, Human Review, optional forks, variant recording), so time-to-first-skill is a hidden cost not quantified in repo.

### Gaps
- No measured Tier A/B resolution rates, no measured run success rate on real customer workloads, no latency benchmark vs agent-per-run; cost_model Tier B tokens are estimates. No A/B vs browser agents in repo with numbers (research-analysis/browser-use-dynamic-content-comparison.md and recorder-architecture-comparison.md exist but were not read).
- July-2026 audit (2026-07-03, research-analysis/07-go-to-market/pre-sales-readiness.md) claimed `verifyAssertions()` dead code, Tier 3-5 not autonomous, conditional logic missing; later work shipped branches/for_each/forks/safe actions and runtime/app/assertions.js exists, but I did not verify assertion wiring end-to-end. Treat the audit as dated, not refuted.

## 3. Positioning, personas, competitive claims, GTM (what the repo says)

### Takeaway
Repo positions Conxa as the only combination of no-code, no vendor cooperation, deterministic replay, self-heal, distributable-to-others'-machines, and zero per-run cost; it explicitly concedes where RPA, iPaaS, APIs and agents win. GTM is pre-revenue: target lists are hypotheses, with code-signing the named single hard sales blocker.

### Cited Findings
- One-liner: "turns any human-performed software workflow into a precompiled, self-healing skill that AI agents can execute reliably — without writing code or touching the target application" — docs/PRD.md §1. Sentence for the room: "anyone can record a workflow — we're the ones whose recordings still work six months later, and we don't charge you per run" — docs/PRD.md "What Our IP Actually Is".
- Competitive matrix (Conxa Yes on all five properties, cost/run "None"; RPA licence per bot; Playwright no-code=No; iPaaS needs an API, per task; agents-on-live-UI non-deterministic, tokens every run; native APIs build+maintain) — docs/PRD.md §10. Repo concedes RPA wins on connector breadth, native desktop/mainframe, scheduling maturity; iPaaS wins anything API-shaped; agents win one-offs; "complements, not substitutes" — docs/PRD.md §10.
- "The compiler is the engine, not the moat"; a strong team of ten could build a recorder-to-script compiler in six months; large AI labs actively ship in this space; defensibility claimed in recovery ladder, telemetry loop, cost structure — docs/PRD.md "What Our IP Actually Is".
- Who not to sell to: consumers/one-offs, single-system with good API, non-browser (native desktop, mainframe, Citrix/VDI), per-login OTP/aggressive bot protection, Mac/Linux-only fleets, adversarial scraping, one-time migrations — docs/PRD.md §6.
- Workflow Qualification Checklist: MFA policy, bot protection, ToS, session lifetime — docs/Sales-Blockers.md; docs/PRD.md.
- Critical analysis (2026-07-03) strategy: Option A, small/mid SaaS vendors first; ten problems, severities: big vendors build own connectors (High), squeeze from official connectors above and smarter AI below (High), per-account variance (Medium-High), IT distrust of installer (High), Claude Desktop dependence (Medium-High), expired logins/MFA (Medium-High), silent wrong click (Medium-High) — research-analysis/conxa-critical-analysis.md.
- Target lists: Global enterprise targets all P2 (need SOC 2, signed installers, per-device identity first); Pune Rung-2 first-3-pilots list is "hypothesis, not research... no company has been contacted" — research-analysis/07-go-to-market/TARGET_CUSTOMERS.md; PUNE-TARGET-50.md. Industry fit ranking of 100 industries exists (Tier A ≥88) — research-analysis/07-go-to-market/industry-browser-work-conxa-fit.md (159KB, not read in full).
- Sales blockers: Windows installer Authenticode signing is "the single hard blocker" — code done in installer_builder.py (activates when CONXA_SIGN_CERT_SHA1 set), no certificate procured (~$200/yr EV cert); unsigned .exe triggers SmartScreen/GPO blocks — docs/Sales-Blockers.md §2.5; docs/Security.md SG-09. macOS support open upsell (7 days) — docs/Sales-Blockers.md 2.4.
- Shipped trust features: BYOK Azure OpenAI (key AES-256-GCM at rest), seat enforcement, audit log, RBAC on write routes, device/runtime registry + revoke, tamper-resistant audit and policy windows/deny-lists (require-approval deferred), ToS acceptance gate — docs/Sales-Blockers.md.
- Minimum-sellable plan (30 engineering days; first 10 design partners, supervised, reversible workflows): post-condition verification, honest repositioning, destructive-step guardrail, code signing + tokenised download, error-code UX — research-analysis/07-go-to-market/minimum-sellable-conxa.md (2026-07-era; status of each item not re-verified).
- Pilot-derived insight: services/consulting firms are a channel ("one deal = many logos") — docs/PRD.md §6 Rung 3.

### Inferences
- Honest positioning for a report: sell as deterministic compiled workflows + bounded recovery for cross-system, API-less, repeating browser processes on Windows; complementary to agents. The repo itself warns not to overclaim "fully autonomous self-healing".
- Moat claims split: structural cost-model and local-execution advantages are shipped and real; the data/telemetry flywheel is explicitly "a diagram, not a moat" until ~customer ten.

### Gaps
- No pricing validation, no win/loss data, no customer references, no named competitor benchmark numbers in PRD (only qualitative). research-analysis folders 01-external-research, 02-conxa-assessment, 03-insights, 04-architecture, 05-reliability, 06-execution-plan, ops/ were not read by me beyond file listing.

## 4. Known gaps, limitations and unbuilt items (for "where not to overclaim")

### Takeaway
Product is broad and mostly shipped on the single-user Windows path, but it is pre-revenue and has open items in signing, per-user identity, durable human review, platform coverage and recovery edge cases. TODO.md progress dashboard: 116 open items (P0 14, P1 1, P2 47, P3 21, P4 33); Done.md: 98 resolved.

### Cited Findings
- Windows only: recording and runtime ship for Windows; Mac "roadmap, not capability" (macOS groundwork inert) — docs/PRD.md §6; docs/Sales-Blockers.md 2.4. No Mac tray for scheduler — docs/TRD.md §4.8.
- Installer unsigned (no cert procured) — docs/Sales-Blockers.md 2.5; runtime binary Authenticode half of SG-09 open — docs/Security.md SG-09.
- Browser-only: native desktop, mainframe, Citrix/VDI out; `beforeunload` dialogs unhandled; native alert/confirm cannot be operated by a hand-over person — docs/PRD.md §6; docs/TRD.md §17, §11.4.
- Login/MFA: per-login OTP is a no-workaround blocker; unattended session lifetime is the "single hardest unsolved problem" between Horizon 1 and 2 (PROD-4; PRD §14.5 open question 5).
- Identity: runtime identity is per workspace, no per-end-user identity (SG-13); fine-grained RBAC only `require_admin`; employee-scoped execution / group access control not built (CLOUD-16/17/18) — docs/TRD.md §17; TODO.md.
- Free tier 1-install cap unenforced (CLOUD-3 reopened 2026-08-09) — docs/TRD.md §17; TODO.md. Starter external distribution detectable, not blocked (SG-17).
- Skill packs not publisher-signed (only update manifests are; UPD-1) — TODO.md; SG-08 shared installer sync token (Low, accepted).
- Domain ownership verification at publish (PROD-6) and first-run per-tenant calibration/repair memory (PROD-1) are open; PROD-1 is called in the source doc "the number the thesis lives on" (first-run success on a new customer's account) — TODO.md.
- Recovery gaps: no Tier A heal for renamed elements (EXEC-28); hover-reveal replay failures (EXEC-29a); WF-7 Tier 2 fixture no longer exercises a11y cascade (EXEC-31); recovery artifacts not yet delivered to customer machines (RECOV-2 phases 4–6, P0); long-workflow 30–40 step stress test open (EXEC-10); manual testing suites (TEST-11, TEST-12) open at P0 — TODO.md.
- Durable/resumable execution state not built (EXEC-22); hand-over lost on restart; weak "approve later" promise; work-item abstraction (ARCH-5) and process-graph (ARCH-6) open — TODO.md; docs/TRD.md §17.
- Fleet durability flywheel automation (EXEC-2), drift acted on (BUILD-28), measured durability scores (BUILD-31), dataset-grade telemetry (EXEC-9) all open — TODO.md.
- Installer generation still local Windows NSIS in Build Studio (ARCH-4 to cloud open, P0); `CONXA_REQUIRED_RUNTIME` default >=1.0.3 so old runtimes silently skip newer step types — TODO.md; docs/TRD.md §17.
- Concurrency: flat cap of 5 (RT-3-CAP-SIZING); host locks per machine; concurrent `watch:true` runs fight for focus — docs/TRD.md §4.6, §4.8.
- Understanding-first: Human Edit still depends on stored frames, so recordings made now show empty screenshot pane until on-demand frame command built; known gaps in typed-text verdicts and phase source — TODO.md BUILD-58. Popup/state general model is proposal only — docs/Popup-and-State-Handling.md.
- App maps: no cross-customer sharing (phase 8); SPA screens collapse — docs/TRD.md §11.5.
- Telemetry data-handling items: SG-20 (popup-shape telemetry leaves machine, mitigated), SG-21 (recording video to TwelveLabs via cloud, mitigated), SG-22 (clipboard steps/AI review data) — docs/Security.md.
- Enterprise hygiene not done: SOC 2/ISO (PROD-9/10 referenced), SSO/SAML only as Enterprise-tier promise, analytics retention prune write-side (CLOUD-4), no CDN/blob storage — docs/TRD.md §17; TODO.md header.
- Repo hygiene/doc drift flagged by repo itself: god files to split (BUILD-63), architecture audit patches vs structure (docs/Architecture-Audit-2026-11-05-Patches-vs-Structure.md, not read), DOC-1 re-drift risk. Branch fix/audit-2026-11-05 has uncommitted edits to TRD/TODO/understanding code (git status at session start).

### Inferences
- Claims safe to make: local execution/credential locality; deterministic zero-token Tier A with bounded Tier B; no per-run charge; MCP multi-host registration; margin-gated resolution and decision-popup safety; signed update manifests.
- Claims to avoid or caveat: autonomous self-healing (Tier B needs an agent and is bounded to 2 rounds); fleet learning moat (loop not spinning); cross-platform; unattended at scale (session lifetime unsolved); enterprise-ready security posture (unsigned installer, per-workspace identity); any revenue/margin figure (modelled only); "vendors/customers sign up" (no evidence in repo).

### Gaps
- I did not verify code behaviour by running anything; claims are as documented. TRD (303KB) read only in sections §4.3, 4.6, 4.8, 10.1, 11.3–11.5, 17, 3.6; Backend-Schema, App-Flow, Implementation-Plan, UI-UX-Brief, Auth-and-Updater and several research-analysis subfolders were not read in depth.
