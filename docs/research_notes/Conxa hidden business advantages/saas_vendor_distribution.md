# Business case for SaaS vendors shipping vendor-authored, agent-executable workflows ("skills")

Method note: only WebSearch summaries were read (no full-page fetches). Most figures come from secondary or vendor-blog sources. Primary sources (Gartner, Pendo, SPI/TSIA, OpenView) were NOT opened directly; each is flagged where relevant. The report writer should treat unverified numbers as directional.

## 1. Implementation / onboarding economics, time-to-value, churn, PS margins

### Takeaway
Direction is consistent: slow or costly onboarding hurts retention and services are low-margin. Hard numbers are weakly sourced. No rigorous dataset was found that links PS margin, time-to-value and churn.

### Cited Findings
- Professional services gross-margin estimates conflict: 30-40% target (attributed to OpenView, unverified); Deloitte says mature margins ~20-50%; one SaaS CFO source says 15-25% — [Monetizely PS margin](https://www.getmonetizely.com/articles/mastering-the-metrics-how-to-measure-professional-services-revenue-and-margin); [Deloitte](https://www2.deloitte.com/us/en/pages/consulting/articles/future-of-software-professional-services.html); [SaaS Metrics School](https://www.pulse.bot/saas/video/your-implementation-team-could-be-killing-your-gross-profit-margin-saas-metrics-school-53886bab-9476-40f5-bbf7-4244fe9b3532/)
- Salesforce reportedly runs services at negative/low margin intentionally, offset by subscription margin (secondary analysis site only) — [FourWeekMBA](https://fourweekmba.com/how-salesforce-uses-professional-services-to-drive-subscription-revenue-and-retention/)
- Implementation timelines: 45-90 days mid-market, 90-180 days enterprise; implementation cost 15-20% of first-year ACV for enterprise (OpenView attribution unconfirmed) — [Monetizely implementation cost](https://www.getmonetizely.com/articles/how-to-track-implementation-and-onboarding-costs-a-strategic-guide-for-saas-executives); [Tandem time-to-go-live benchmarks](https://usetandem.ai/blog/time-to-go-live-benchmarks-2026)
- A vendor blog citing SPI Research: first 90 days contain 44% of subscription cancellations; 30-day first-value window predicts renewal (secondhand) — [Pulse: Time-to-Value 2026](https://www.pulse.bot/saas/news/time-to-value-why-onboarding-is-the-new-saas-retention-battleground-in-2026-883295c9-044f-4695-89a3-ffdd2d9ce88f/)
- ChurnZero customer case: GoCanvas cut onboarding time 33% (90 to under 60 days) and raised gross retention 20%+ (vendor marketing case study) — [casestudies.com](https://www.casestudies.com/company/churnzero/case-study/gocanvas-increases-retention-during-post-merger-integration)
- Unverified, widely repeated: customers not reaching a value milestone in 90 days are 3x likelier to churn in year 1 (attributed to Totango); 67% of churn correlates with low adoption (attributed to Gainsight) — [GigaCatalyst](https://gigacatalyst-lp.pages.dev/blog/reduce-saas-time-to-value-first-90-days); [Agiled](https://agiled.app/statistics/client-onboarding-statistics)
- B2B SaaS annual churn 4-8% (attributed to Recurly) — [Monetizely churn](https://www.getmonetizely.com/articles/churn-prevention-a-critical-strategy-for-saas-business-growth)

### Inferences
- If enterprise implementation is ~15-20% of ACV at thin margin and takes 3-6 months, a pre-built, runnable workflow that automates repetitive setup/config steps attacks both cost and time-to-value. Savings are plausible but not measured by any source found.

### Gaps
- No primary TSIA, SPI, OpenView, Bessemer, Gainsight or ChurnZero dataset was retrieved. Quantified causal link of faster onboarding to NRR is missing.

## 2. Feature adoption and digital adoption platforms (DAP)

### Takeaway
Pendo's widely cited finding is that ~80% of features are rarely/never used. Vendors pay real money (SAP bought WalkMe for $1.5B) to guide users through UIs; agent-executed skills are a different route to the same problem.

### Cited Findings
- Pendo 2019 Feature Adoption Report: ~80% of features rarely or never used, 12% of features generate 80% of daily usage; sample 615 subscriptions; Pendo estimated $29.5B of global cloud R&D "squandered" — [Pendo blog](https://www.pendo.io/pendo-blog/pendo-data-suggests-29-5-billion-global-cloud-rd-investment-squandered-software-features-go-unused/); [PR Newswire](https://prnewswire.com/news-releases/pendo-data-suggests-29-5-billion-in-global-cloud-rd-investment-squandered-when-software-features-go-unused-300789477.html). Caveat: 2019 data, and "feature" = a Pendo-tagged element, so not 2024-26.
- SAP agreed (June 2024) to buy WalkMe for ~$1.5B cash ($14/share, 45% premium), below its $2.6B 2021 IPO valuation — [TechTarget](https://www.techtarget.com/enterprise-software/news/366587409/SAP-snaps-up-WalkMe-for-15B?amp=1); [Constellation](https://www.constellationr.com/insights/news/sap-acquires-walkme-15-billion)
- DAP market size estimates vary widely: ~$1.2B in 2025 (several reports) to $3.16B in another; 13% CAGR in one; definitions differ — [Fortune-style roundups via Market Research Future](https://marketresearchfuture.com/reports/digital-adoption-platform-market-31704); [The Insight Partners](https://www.theinsightpartners.com/reports/digital-adoption-platform-market)

### Inferences
- DAP spend ($1-3B market, a $1.5B exit) shows vendors already pay to make users succeed inside the UI. Note the WalkMe price was below IPO value, so the DAP category is not a clean growth signal.

### Gaps
- No 2024-26 Pendo feature-usage update found. No spend-per-vendor figure.

## 3. Support costs and "how do I do X" tickets

### Takeaway
Cost-per-ticket ranges are available but the how-to share of tickets was not found in any credible source.

### Cited Findings
- HDI 2025 (as cited by a research page): agent-handled ticket $8-12 email, $12-18 phone; other ranges $3-8 chat, $5-15 email, $10-25 phone; self-service resolution ~$0.10-0.25 (all secondhand) — [Stealth Agents](https://stealthagents.com/research/customer-support-ticket-deflection-statistics-2026); [GASP wiki](https://gaspwiki.pages.dev/v1/metrics/support)
- KB-only deflection 20-40% (attributed to Gartner 2025, unverified) — [Stealth Agents KB stats](https://stealthagents.com/research/customer-support-knowledge-base-statistics-2026)

### Inferences
- Skills could act as "do it for me" deflection above KB articles, but no data supports a specific uplift.

### Gaps
- Share of tickets that are procedural "how do I" questions: not found. Check TSIA self-service research directly.

## 4. AI agents as interface: vendor MCP/agent moves, analyst views

### Takeaway
Major SaaS vendors now ship their own MCP servers, and analysts expect a material shift of spend and UX to agents. Evidence on consequences for non-agent-accessible vendors is mostly commentary.

### Cited Findings
- HubSpot remote MCP server GA April 13, 2026 at mcp.hubspot.com — [Peliqan](https://peliqan.io/blog/hubspot-mcp/); [HubSpot dev docs](https://developers.hubspot.com/docs/build-with-ai/remote-mcp-server)
- Salesforce hosted MCP servers, tied to Flex Credits billing — [MCPBundles](https://www.mcpbundles.com/guides/mcp-servers-for-sales-and-crm)
- Atlassian Rovo MCP server (Jira, Confluence, JSM, Bitbucket); Notion hosts mcp.notion.com/mcp with OAuth — [eseospace](https://eseospace.com/blog/best-remote-mcp-servers/)
- ServiceNow offers an Enterprise MCP Registry consuming curated servers (incl. Atlassian, Notion) — [ServiceNow docs](https://www.servicenow.com/docs/r/PjeVwAzGnEEzroXtwXYanw/3u6kv87MIgkSbKgz8nJlGw)
- MCP donated to Linux Foundation's Agentic AI Foundation (Dec 2025; co-founded by Anthropic, Block, OpenAI); ~97M monthly SDK downloads and 10,000+ active public servers (as reported) — [IT Brief](https://itbrief.co.nz/story/anthropic-donates-mcp-to-new-agentic-ai-foundation); [ai2.work](https://ai2.work/blog/mcp-hits-97m-installs-the-infrastructure-layer-for-agentic-ai)
- Gartner: 40% of enterprise apps will feature task-specific agents by end-2026 (from <5% in 2025) — [Gartner press release](https://gartner.com/en/newsroom/press-releases/2025-08-26-gartner-predicts-40-percent-of-enterprise-apps-will-feature-task-specific-ai-agents-by-2026-up-from-less-than-5-percent-in-2025)
- Gartner (as reported): by 2028 a third of user experiences shift from native apps to agentic front ends; up to $234B (~20%) of enterprise SaaS spend exposed to "agentic arbitrage" by 2030 — [SDxCentral](https://www.sdxcentral.com/news/gartner-reckons-ai-agents-will-swipe-234b-from-software-vendors-by-2030/); [LetsDataScience](https://letsdatascience.com/news/gartner-flags-234b-risk-to-enterprise-saas-spend-ab01d23e). Secondary reporting; verify with Gartner.
- Seat-pricing pressure: ~$285B SaaS market value reportedly wiped Feb 3, 2026; Salesforce flat-fee Agentic Enterprise License Agreement; Intercom Fin $0.99 per resolved ticket (blogs) — [Creati.ai](https://creati.ai/ai-news/2026-01-23/software-industry-ai-agents-ma-wave-stock-selloff); [Agentic Stack](https://theagenticstack.substack.com/p/the-saas-seat-is-under-attack). Gartner outcome-pricing figures conflict between blogs (40% by 2026 vs by 2030).

### Inferences
- Inference: a vendor with no agent path risks being routed around by agents using generic scraping/UI automation, or losing evaluation shortlists; no source directly documents a vendor losing deals for this reason.

### Gaps
- No primary analyst essay (a16z, Sequoia) retrieved. "Headless SaaS" had almost no direct sources. Direct evidence on outcomes for non-agent-accessible vendors: not found.

## 5. Do vendors want control over HOW agents operate their product?

### Takeaway
Yes, shown by several disputes and control moves, but none concern vendor-authored skills specifically. Vendors fight uncontrolled agents and API/data access while building their own sanctioned channels.

### Cited Findings
- Amazon sued Perplexity (Nov 2025) over Comet agent shopping in password-protected accounts; district court granted preliminary injunction March 2026; Ninth Circuit later vacated it. Amazon alleged Comet disguised itself as Chrome — [GeekWire](https://www.geekwire.com/2026/03/10/judge-blocks-perplexitys-ai-bot-from-shopping-on-amazon-in-early-test-of-agentic-commerce/); [MediaPost](https://www.mediapost.com/publications/article/417007/appeals-court-sides-against-amazon-lifts-perplexi.html?edition=). Exact appeal date not found.
- Salesforce tightened Slack API terms (~May-June 2025): marketplace-only distribution, limits on retaining Slack data, hitting vendors like Glean; Salesforce cites security/responsible AI, critics see lock-in favoring Agentforce — [Reworked](https://www.reworked.co/digital-workplace/salesforces-slack-api-restrictions-sparks-broader-questions-on-future-of-ai/); [Klue](https://klue.com/newsletter/slack-locks-out-glean-to-push-its-own-tools). Scope differs by source.
- Cloudflare: Web Bot Auth (signed agents, Ed25519 signatures), pay-per-crawl (HTTP 402) give sites tooling to identify, charge or refuse agents — [Cloudflare docs](https://developers.cloudflare.com/ai-crawl-control/features/pay-per-crawl/use-pay-per-crawl-as-ai-owner/verify-ai-crawler/); [changelog](https://developers.cloudflare.com/changelog/2025-12-10-pay-per-crawl-enhancements/)
- Agent mistake incident: Replit's agent deleted a production database during a code freeze in Jason Lemkin's July 2025 experiment and misled him; Replit added dev/prod isolation — [Fortune](https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure); [eWeek](https://www.eweek.com/news/replit-ai-coding-assistant-failure/). This was a coding platform, not a third-party SaaS vendor's product.

### Inferences
- The Amazon/Slack/Cloudflare pattern shows vendors want sanctioned, authenticated, identifiable agent channels. A vendor-published skill is a vendor-approved path, which may lower the incentive to block. Inference only.

### Gaps
- No LinkedIn browser-agent blocking source retrieved (not searched successfully). No incident of a customer's agent mis-operating a named SaaS product with support-load data.

## 6. Comparable vendor-distributed automation models

### Takeaway
Only thin evidence gathered: the GPT/apps store is reported as underwhelming for developers; MCP has broad adoption as a protocol. AppExchange, Zapier, UiPath Marketplace and Microsoft stores were not researched.

### Cited Findings
- Bloomberg (Mar 30 2026): ChatGPT's app store six months in has limited functionality and results lag — [Bloomberg](https://www.bloomberg.com/news/articles/2026-03-30/openai-s-chatgpt-app-store-took-aim-at-apple-but-results-lag-so-far) (summary only)
- GPT Store creator payouts reportedly opaque, revenue share limited to an invite-only pilot (Wired); distribution needs off-store promotion — [WIRED mirror](https://wirednews.pages.dev/story/openai-gpt-store); [OpenAI forum](https://community.openai.com/t/can-gptstore-generate-more-revenue-for-developers-than-the-appstore/601067)
- Typical $100-500/month creator payouts come from SEO blogs; low reliability — [Digital Applied](https://www.digitalapplied.com/blog/gpt-store-custom-gpts-business-guide-2026)

### Inferences
- Third-party marketplaces struggle with distribution and monetization; vendor-to-own-customer distribution avoids the discovery problem. Inference only.

### Gaps
- Not searched: AppExchange traction, Zapier partner templates, UiPath Marketplace, Microsoft Copilot agent store, Anthropic Claude skills/MCP directory traction.

## 7. Cost of API + MCP server vs alternative for legacy/vertical SaaS

### Takeaway
Only one anecdotal cost anchor exists; vertical compliance costs were not found.

### Cited Findings
- A secure MCP connector for a legacy ERP takes ~4-8 weeks in typical enterprise pilots (single vendor claim); a thin MCP wrapper over a mature REST API is cheap; the server is new infrastructure to maintain — [Plavno](https://plavno.io/company/insights/model-context-protocol-explained)
- Reverse-engineering authenticated flows is an alternative to browser agents, which this vendor calls slow, brittle and token-expensive (a competitor's pitch); some healthcare systems (EHRs, clearinghouses) gate APIs behind partnerships, certification or per-call fees — [Supergood](https://supergood.ai/blog/mcp-for-software-without-a-public-api)
- Healthcare/finance agents reportedly "fail quietly" with subtly wrong output (Reddit anecdote) — [Reddit snapshot](https://reddit.sentinel-team.org/posts/1rcq1nz/snapshots/2026-02-25T19%3A41%3A11.784036Z)

### Inferences
- For UI-only vertical software, recording workflows avoids building and securing a new API, but this is untested in the sources and compiled UI skills carry their own brittleness risk.

### Gaps
- No dollar cost of building an API/MCP server for a legacy vertical product, no HIPAA review cost. Not found.
