# AI computer-use and browser agents: business reality vs CONXA (notes as of 2026-11-05)

Source-quality warning: many 2026 figures below come from aggregator/leaderboard-tracker sites, not primary vendor or benchmark pages. Each is flagged. The official OSWorld leaderboard, vendor system cards for 2026 models, and Microsoft/Manus/Browser Use primary docs were NOT directly read.

## 1. Benchmark success rates vs human baselines, and compounding

### Takeaway
On OSWorld-Verified, the top agent scores reportedly passed the ~72% human baseline in 2026 (about 80-86%+). Cross-site, real-world benchmarks that are judged strictly (Online-Mind2Web) showed far lower rates in 2025 (Operator 61%, most others about 30%). Short-task benchmark wins do not translate into multi-step reliability, because per-step success compounds multiplicatively.

### Cited Findings
- OSWorld original paper: human baseline 72.36%, best model at the time 12.24% (2024). — [aiwiki OSWorld summary via search](https://www.aiwiki.ai/wiki/osworld) (secondary; verify against the OSWorld paper)
- Per aggregator trackers, OSWorld-Verified leaders over time: Holo3-35B-A3B 80.4% (Apr 2026); Claude Opus 4.8 83.4% (2 Jun 2026); Claude Fable 5 85% (6 Jul 2026); Qwen3.8 Max 86.1% (29 Oct 2026). Aggregator, not official, and scores may use different harnesses/conditions. — [BenchLM OSWorld-Verified](https://www.benchlm.ai/benchmarks/osworld-verified); [BuildMVPFast](https://www.buildmvpfast.com/blog/osworld-benchmark-ai-agents-passed-human-baseline-2026)
- One tracker claims an official-leaderboard top entry of 90.19% (Intelligence-Indeed Agent, 25 Jul 2026). Unverified; one source only. — [presenc.ai](https://presenc.ai/research/osworld-computer-use-leaderboard-2026)
- A tracker notes a submission was removed from the verified leaderboard, i.e. entries can change. — [BuildMVPFast](https://www.buildmvpfast.com/blog/osworld-benchmark-ai-agents-passed-human-baseline-2026)
- A 2026 paper "OSWorld 2.0: Benchmarking Computer Use Agents on Long-Horizon Real-World Tasks" exists, implying the original benchmark is saturating and long-horizon tasks are the new frontier (scores not read). — [arXiv 2606.29537](https://arxiv.org/pdf/2606.29537)
- Online-Mind2Web (300 tasks, 136 live sites), human-evaluated success, 2025 snapshot: OpenAI Operator 61.3%; Claude Computer Use 3.7 56.3%; SeeAct 30.7%; Browser Use 30.0%; Claude CU 3.5 29.0%; Agent-E 28.0%. SUPERSEDED by newer models, but shows the gap to self-reported numbers. — [arXiv 2504.01382v4](https://arxiv.org/html/2504.01382v4)
- Same paper: success falls 31.6 points from easy to medium and another 15.4 from medium to hard; Claude CU 3.7 90.4% and Operator 83.1% on easy tasks only. — [arXiv 2504.01382v4](https://arxiv.org/html/2504.01382v4)
- Same paper: WebVoyager results are inflated; a simple search agent solves up to 51% of WebVoyager but 22% of Online-Mind2Web; WebVoyager auto-judge agrees with humans only 73.9%; Browser Use's reported results used that auto-evaluation. — [arXiv 2504.01382v4](https://arxiv.org/html/2504.01382v4)
- Gemini 2.5 Computer Use (7 Oct 2025): Google claims lead on Online-Mind2Web, WebVoyager, AndroidWorld, "70%+ accuracy at ~225 s latency" on the Browserbase harness; model optimized for browsers, "not yet optimized for desktop OS-level control." Secondary sites give 65.7% Online-Mind2Web and 88.9% WebVoyager (unverified). — [Google blog](https://blog.google/technology/google-deepmind/gemini-computer-use-model/)
- Earlier OpenAI CUA preview: 38.1% OSWorld, 58.1% WebArena; its system card said 38.1% was not reliable enough for OS automation and recommended human oversight (2025; superseded). — [arXiv 2609.04894 (secondary description)](https://arxiv.org/pdf/2609.04894)
- ChatGPT agent (Jul 2025): 68.9% BrowseComp per OpenAI claim; BrowseComp measures finding facts, not completing transactions. Original WebArena had ~11.3% false-negative rate, corrected in WebArena-Verified. — [chatai.guide review](https://chatai.guide/?p=408); [Galileo on BrowseComp](https://galileo.ai/blog/what-is-browsecomp-openai-benchmark-web-browsing-agents)
- Amazon Nova Act GA (2 Dec 2025): AWS claims "over 90% task reliability" on early customer workflows; vendor-reported, no independent verification, criteria unstated. — [AWS What's New](https://aws.amazon.com/about-aws/whats-new/2025/12/build-automate-production-ui-workflows-nova-act)
- Gartner (25 Jun 2025): over 40% of agentic AI projects will be canceled by end of 2027 (cost, unclear value, weak risk controls); only ~130 of thousands of "agentic" vendors are real ("agent washing"). — [Gartner press release](https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027)

### Inferences
- Compounding (my arithmetic, p^n): 95% per step over 20 steps = 35.8% end-to-end; 98% = 66.8%; 99% = 81.8%; 99.9% = 98.0%; 90% = 12.2%. A 20-step workflow needs ~99.9% per step to be 98% reliable. This is the core structural argument for compile-once deterministic execution (a compiled step that resolves with a uniqueness gate can approach that; LLM re-deciding every step cannot).
- Benchmark tasks are mostly short (tens of steps), single-session, and not tied to company-specific SaaS UIs; headline OSWorld numbers likely overstate real business-workflow reliability. This is an inference, not directly measured.
- Vendors' own reliability claims (Nova Act 90%) sit well below the 99%+ needed for unattended 20-step work, and Nova Act itself achieves reliability by decomposing into small scripted steps, which is convergent with CONXA's thesis.

### Gaps
- Official OSWorld-Verified leaderboard page not read; exact current top and harness conditions unknown.
- No direct figures found for Microsoft Copilot computer use, Manus, Project Mariner, or Comet on public benchmarks.
- No located source for REAL benchmark or WebArena-Verified 2026 leaders.
- No found measurement of multi-step (20+) real SaaS workflow success for any agent.

## 2. Cost per task and latency

### Takeaway
Published figures range from ~$0.05 per task in optimized research harnesses to several dollars in naive production loops; step count and screenshot retention dominate cost. Per-action latency is a few seconds, so typical tasks take minutes (Gemini ~225 s on Online-Mind2Web; Skyvern uncached ~279 s).

### Cited Findings
- Multi-agent study with Claude Opus 4.6 manager: ~$0.21 per OSWorld task, $0.38 per Online-Mind2Web task, $0.46 WebTailBench, $0.90 Odysseys. — [arXiv 2606.01533](https://arxiv.org/pdf/2606.01533)
- CURA paper: median ~$0.049 per task; failed tasks cost far more than solved ones. — [arXiv 2608.27808](https://arxiv.org/pdf/2608.27808)
- AgentSynth: a 1920x1080 screenshot is roughly 1k tokens (GPT-4.1) to 2k tokens (computer-use-preview); estimated ~$0.60 per 50-step trajectory. — [arXiv 2506.14205](https://arxiv.org/pdf/2506.14205)
- OSWorld 2.0 reports per-task output tokens and turns; Claude Opus 4.8 was the most token-expensive agent but among the fewest turns. — [arXiv 2606.29537](https://arxiv.org/pdf/2606.29537)
- Practitioner anecdotes (low rigor): per-action latency 2-5 s; one comparison of a vision agent at 53 steps/551k input tokens versus 8 tool calls/12k tokens (~45x). — [Jacar: Computer Use in production](https://jacar.es/en/computer-use-in-production-agents-that-drive-the-interface/); [Towards AI](https://pub.towardsai.net/computer-use-the-hardest-harness-problem-8752e5f94fde)
- Skyvern code caching: after a successful run, generated code replays without LLM/screenshot calls; per-block caching; falls back to the agent and regenerates on failure. A TinyFish article reports run cost $0.11 to $0.04 and time 279 s to 120 s (third-party, not independently verified). — [Skyvern docs](https://skyvern.com/docs/developers/features/code-caching); [TinyFish](https://current.tinyfish.ai/tinyfish/issue/32/practitioners-corner/article/18634)
- A secondhand claim: compiled workflows break even at ~17 runs and cut tokens 57x at 1,000 runs; the paper is unnamed. — [TinyFish](https://current.tinyfish.ai/tinyfish/issue/33/practitioners-corner/article/22098) (treat as unverified)

### Inferences
- A screenshot agent pays LLM tokens on every run of the same workflow; a compiled skill pays at compile time and near-zero at replay. At 1,000+ runs/month per skill the gap is large; for a SaaS vendor distributing skills to many customers it multiplies.
- Failure cost asymmetry (failed runs cost more) compounds the reliability problem.

### Gaps
- No published per-task dollar figures from Anthropic/OpenAI/Microsoft/Perplexity for real business workflows; no Manus cost data found; Skyvern/Browser Use cost figures are vendor or third-party.

## 3. Consistency and run-to-run variance

### Takeaway
I found no rigorous published study of same-task repeatability for these agents. Indirect evidence: vendors themselves caveat reliability and require confirmation; Skyvern markets caching specifically to make runs "deterministic."

### Cited Findings
- TinyFish quotes Skyvern's team saying cached runs "are now deterministic," implying uncached LLM runs are not. — [TinyFish](https://current.tinyfish.ai/tinyfish/issue/32/practitioners-corner/article/18634)
- Nova Act is built to chain small verifiable atomic steps with deterministic Python rather than one prompt for a whole workflow. — [AWS news blog](https://aws.amazon.com/blogs/aws/build-reliable-ai-agents-for-ui-workflow-automation-with-amazon-nova-act-now-generally-available)
- Online-Mind2Web's automatic judge (WebJudge) agreed with humans only ~84-87%, showing even measuring agent success is noisy. — [arXiv 2504.01382v4](https://arxiv.org/html/2504.01382v4)

### Inferences
- LLM sampling means identical prompts produce different action paths; this is a general property, but I have no sourced pass^k-style numbers for these products.

### Gaps
- No pass^k / repeated-run variance data located for computer-use agents (search needed: tau-bench pass^k, REAL benchmark, repeat-run studies).

## 4. Enterprise objections: security, governance, data, irreversible actions

### Takeaway
Vendors acknowledge prompt injection is unsolved. Anthropic published 11.2% attack success even after mitigations (Aug 2025); OpenAI says it is unlikely ever fully solved. Vendors block or confirm high-risk actions and recommend limiting access to sensitive sites.

### Cited Findings
- Claude for Chrome (25 Aug 2025): 123 adversarial test cases across 29 scenarios: 23.6% attack success without mitigations, 11.2% with mitigations in autonomous mode; browser-specific set of 4 attacks 35.7% to 0%. Anthropic says its earlier Computer Use rate was higher (The Register cites 19.4%). — [Anthropic/Claude blog](https://claude.com/blog/claude-for-chrome); [The Register](https://www.theregister.com/2025/08/26/anthropic_claude_chrome_warnings/)
- Same post: Claude blocked from financial services, adult and pirated-content sites; Anthropic recommends starting with trusted sites and avoiding sites with financial, legal, medical or other sensitive information; confirmation required for publishing, purchasing, sharing personal data; admin allow/blocklists added 18 Dec 2025; pilot of 1,000 Max users Aug 2025, all Pro/Team/Enterprise by Dec 2025. — [Claude blog](https://claude.com/blog/claude-for-chrome)
- Simon Willison: 11.2% is still a catastrophic failure rate for security. — [Willison](https://simonwillison.net/2025/Aug/26/piloting-claude-for-chrome/)
- An unverified secondary report claims later layered defenses reached 0-0.3% attack success; not confirmed against Anthropic's publication. — [AI Understanding](https://aiunderstanding.org/news/anthropic-makes-claude-in-chrome-generally-available-with-autonomous-browser-actio)
- OpenAI Atlas (23 Dec 2025): prompt injection "unlikely to ever be fully solved"; deterministic guarantees "challenging"; years-long problem; new adversarially trained model after automated red-teaming. Advice to users: use logged-out mode, review confirmations, give specific instructions rather than "take whatever action is needed." CISO Dane Stuckey earlier called it a "frontier, unsolved security problem." — [The Decoder](https://the-decoder.com/openai-admits-prompt-injection-may-never-be-fully-solved-casting-doubt-on-the-agentic-ai-vision/); [CyberScoop](https://cyberscoop.com/openai-chatgpt-atlas-prompt-injection-browser-agent-security-update-head-of-preparedness/)
- UK NCSC warned prompt injection may never be totally mitigated. — [CyberScoop search summary](https://cyberscoop.com/openai-chatgpt-atlas-prompt-injection-browser-agent-security-update-head-of-preparedness/) (secondary mention)
- Perplexity Comet: Brave showed indirect prompt injection (page content passed to the LLM unseparated; Reddit-post demo of account takeover); Guardio showed the agent buying from a fake Walmart page using saved card details; Perplexity says it fixed the Brave issue, a report says the first patch was incomplete (unverified). — [Tom's Hardware](https://www.tomshardware.com/tech-industry/cyber-security/perplexitys-ai-powered-comet-browser-leaves-users-vulnerable-to-phishing-scams-and-malicious-code-injection-brave-and-guardios-security-audits-call-out-paid-ai-browser); [Windows Central](https://www.windowscentral.com/artificial-intelligence/perplexity-comet-browser-serious-security-flaws)
- Data and screenshots go to the vendor's model (inherent to screenshot-based cloud agents; CONXA runs locally but the orchestrating LLM in Claude Desktop is still cloud). No source fetched specifically on SOC2/HIPAA status of each agent.

### Inferences
- A compiled skill removes the page from the decision loop on the happy path: no untrusted page text reaches an LLM unless Tier B recovery fires. That shrinks (not eliminates) prompt-injection surface and gives a reviewable step list for audit. Needs framing carefully: Tier B and the orchestrating agent remain exposed.

### Gaps
- No primary source for Microsoft Copilot computer use, Manus, Mariner security/compliance, SOC2/HIPAA attestations, or audit-log features. No 2026 update from Anthropic with verified injection numbers found.

## 5. What vendors say NOT to use agents for

### Takeaway
Vendors steer away from financial, sensitive and unattended high-stakes use; confirmations are default.

### Cited Findings
- Anthropic: blocked categories (financial services, adult, pirated); avoid sites with financial, legal, medical info. — [Claude blog](https://claude.com/blog/claude-for-chrome)
- OpenAI CUA system card (predecessor): 38.1% OSWorld not reliable enough for OS automation; human oversight recommended. — [arXiv 2609.04894](https://arxiv.org/pdf/2609.04894)
- OpenAI Atlas: logged-out mode, careful review of confirmations. — [The Decoder](https://the-decoder.com/openai-admits-prompt-injection-may-never-be-fully-solved-casting-doubt-on-the-agentic-ai-vision/)
- Google: Gemini 2.5 Computer Use not optimized for desktop OS control. — [Google blog](https://blog.google/technology/google-deepmind/gemini-computer-use-model/)

### Gaps
- Exact "do not use for" wording from the ChatGPT agent system card not verified; Microsoft/Manus/Perplexity guidance not found.

## 6. Record/teach/replay and workflow memory: closeness to CONXA

### Takeaway
The market is converging on "let the agent explore once, then cache/script it" (Skyvern, Nova Act, Agent Workflow Memory research). Skyvern's code caching with agent fallback is the closest analogue; it is derived from an agent run, not from a human demonstration, and I found no evidence of multi-signal identity, vendor-authoring/distribution, or zero-token tiered recovery.

### Cited Findings
- Skyvern: records a successful AI run, generates code, replays without LLM; per-block caching; live blocks never cached (conditionals, waits, code); fallback to agent and regeneration on failure. — [Skyvern docs](https://skyvern.com/docs/developers/features/code-caching)
- Amazon Nova Act: natural language plus deterministic Python, atomic verifiable steps, IDE extension; GA Dec 2025. — [AWS blog](https://aws.amazon.com/blogs/aws/build-reliable-ai-agents-for-ui-workflow-automation-with-amazon-nova-act-now-generally-available)
- Agent Workflow Memory (Wang, Mao, Fried, Neubig, arXiv 2409.07429, Sep 2024): induces reusable workflows from successful trajectories; 51.1% relative gain on WebArena, 24.6% on Mind2Web (abstract figures via aggregator, not checked against PDF). — [aitopics mirror](https://aitopics.org/doc/arxivorg:EA4AFEFC)

### Inferences
- Differentiators for CONXA vs these: human demonstration as ground truth (agent-derived caches inherit agent errors), tiered zero-token recovery before any LLM, distribution to third-party customers via MCP, local execution. Skyvern/Nova Act are developer SDKs/hosted services where the builder is also the operator.

### Gaps
- Not researched: Claude Skills, OpenAI Agent Builder, Browser Use workflows, Manus, Microsoft Copilot teach/record features. Need follow-up.

## 7. MCP ecosystem

### Takeaway
MCP is now an industry standard under neutral governance, with large reported SDK download numbers, which makes "distribute skills via MCP into any host" a credible channel. Numbers are from secondary sources.

### Cited Findings
- Dec 2025: Anthropic donated MCP to the Agentic AI Foundation (Linux Foundation directed fund) co-founded by Anthropic, Block and OpenAI; platinum members reportedly include AWS, Google, Microsoft, Cloudflare, Bloomberg. — [SerpApi: state of MCP H1 2026](https://serpapi.com/blog/the-state-of-mcp-everything-that-changed-in-h1-2026/); [Toloka](https://toloka.ai/blog/the-future-of-mcp-enterprise-adoption/)
- Reported 97 million monthly SDK downloads (25 Mar 2026; another source says 110M); 10,000-12,000+ public servers; MCP Dev Summit NA April 2026 ~1,200 attendees. Secondary/possibly low-quality sources. — [ai2.work](https://ai2.work/blog/mcp-hits-97-million-installs-anthropic-s-standard-is-now-infrastructure); [Moklabs](https://moklabs-research.pages.dev/reports/technology/2026-03-20-mcp-ecosystem-standards-adoption)

### Inferences
- Vendors ship MCP servers for their own products because the API/tool route is the reliable path for agents (the cost/latency/security gaps in sections 1-4 do not apply to API calls). Where a SaaS has no API coverage for a workflow, UI skills fill the gap. This reasoning is mine; no source on vendors' stated motives was fetched.

### Gaps
- No primary Anthropic/AAIF announcement fetched; no list of host apps supporting MCP with counts; no data on which SaaS vendors ship official MCP servers and why.
