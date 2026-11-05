# API-based workflow automation and code/cloud browser automation: business limits vs CONXA

Note: search-engine-mediated research (snippets, not full-page reads). Many sources are vendor/aggregator blogs; flagged where so. Verify key figures against primaries before publishing.

## API coverage gap and SaaS sprawl

### Takeaway
Companies run hundreds of apps but only a minority are connected; iPaaS catalogs (Zapier ~9,000 apps) cover a small slice of the long tail. I found no credible hard statistic for the share of business software lacking usable APIs; only vendor claims in specific verticals (insurance).

### Cited Findings
- Zylo 2025 SaaS Management Index: average company had 275 SaaS apps in 2024 (peak 323 in 2021 per SaaStr commentary) — [Zylo](https://zylo.com/news/2025-saas-management-index); [SaaStr](https://www.saastr.com/the-average-tech-company-pays-for-275-saas-apps-flat-from-last-year-but-its-paying-20-more-for-them)
- Zylo 2026 index: 305 SaaS apps on average (via Zylo blog; not independently verified) — [Zylo](https://zylo.com/?p=12869)
- Size split (secondary, unverified against report): 1-500 employees ~152 apps; 10,000+ employees ~660 apps — [SalesTechStar](https://salestechstar.com/?p=243442). A Zylo services page cites 651 apps for a "typical company", undated and conflicting — [Zylo](https://zylo.com/services/enterprise-saas-software-asset-management/)
- MuleSoft 2025 Connectivity Benchmark (1,050 IT leaders): average respondent uses 897 apps; 45% use 1,000+; typically only 29% of apps are connected; only 2% of orgs have more than half their apps connected (different bases) — [Salesforce AU blog](https://www.salesforce.com/au/blog/mulesoft-connectivity-benchmark-2025/); [Letter Two](https://thelettertwo.com/2025/02/18/it-leaders-ai-agents-workloads-integration-challenges-salesforce-mulesoft-research/); [Network World summary](https://us.resources.networkworld.com/resources/2025-connectivity-benchmark-report-8); [Techzine](https://www.techzine.eu/news/devops/101410/mulesoft-app-integration-still-a-big-problem-in-digital-transition/)
- MuleSoft: 88% of IT execs say integration challenges slow digital transformation (older report, year not confirmed) — [VentureBeat](https://venturebeat.com/business/report-88-of-it-execs-say-integration-challenges-are-slowing-digital-transformation)
- Postman 2025 State of the API: 83.2% adopt API-first to some degree (74% in 2024, 66% in 2023); only ~25% fully API-first; only 24% design APIs for AI agents — [BusinessWire](https://www.businesswire.com/news/home/20251008162423/en); [Nordic APIs](https://nordicapis.com/a-deep-dive-into-the-state-of-the-api-2025/)
- Zapier lists 9,000+ apps in 2026 (third-party sites; older reviews say 7,000+) — [Clay](https://clay.com/dossier/zapier-email-format); [PricingSaaS tracker](https://pricingsaas.com/companies/zapier/diffs/2026W21)
- Insurance vertical (vendor claim, QuoteSweep): ~98% of P&C carriers have no quoting API; another source counts 86 carriers with some commercial API but only ~8-10 with full core commercial APIs — [QuoteSweep](https://www.quotesweep.com/blog/98-percent-carriers-no-api); [Sacra](https://sacra.com/chat/h/716618df-6557-4139-82bb-45ffc9befc51/)
- Unsourced "some estimates" say legacy tech is 50-70% of systems at large companies (no primary source; low confidence) — [ExpertBeacon](https://expertbeacon.com/rpa-legacy-systems/)

### Inferences
- 275-900 apps vs ~9,000 in Zapier's directory implies each company's long tail (vertical, internal, legacy, partner portals) is where connector catalogs thin out; the 29% connected figure shows the gap persists even where APIs exist.
- API-first adoption is rising but "fully API-first" is only a quarter; UI usually exposes more than API (not measured here).

### Gaps
- No credible numeric share of business apps lacking public APIs, or UI-vs-API function parity. No Gartner/Forrester figure found (likely paywalled).
- Zapier/Make per-app depth (actions supported vs UI features) not found.

## Cost of API integrations / iPaaS pricing

### Takeaway
iPaaS is metered per task/credit/operation; enterprise tools (Workato) run ~$25K-$200K+/yr; AI steps burn tasks faster. Developer-time and deprecation-churn data not found.

### Cited Findings
- Zapier: Free 100 tasks/mo; Professional ~$19.99/mo annual ($29.99 monthly) at 750 tasks; Team ~$69 annual; Enterprise custom; task metering reportedly extends to AI/code steps (third-party guides, prices vary) — [Jetadmin](https://www.jetadmin.io/blog/zapier-pricing/); [Lindy](https://wf.lindy.ai/blog/zapier-pricing)
- Make renamed "operations" to "credits" on 27 Aug 2025 (charge per module call; AI apps token-based); single "Make Plan" slider June 2026; free tier 1,000 ops/mo — [AutomationAtlas](https://automationatlas.io/answers/make-pricing-explained-2026/); [UsagePricing](https://usagepricing.com/blueprint/activity/make-2026-08-11-single-make-plan-confirmed)
- Workato (no public list price): Vendr median ~$64,500/yr; ACV range $25K-$500K+; 5M-task workload list ~$180K, discounted ~$84K-$128K; Workato One agentic ~$144K-$216K (aggregator, low-sample data) — [Vendr](https://vendr.com/marketplace/workato); [CostBench](https://costbench.com/software/ai-automation/workato); [Integrate.io](https://www.integrate.io/blog/workato-pricing); [Tray.ai blog (competitor)](https://tray.ai/blog/workato-pricing/)
- Annual contracts with monthly task pools and overage; 24/7 support typically needs Enterprise tier — [Activepieces](https://www.activepieces.com/blog/workato-pricing.md)

### Inferences
- Per-task metering makes long multi-step flows scale cost linearly with volume; CONXA local execution has no per-run vendor meter (LLM tokens only at Tier B).

### Gaps
- No data on developer hours per custom integration, maintenance cost, or API deprecation/version churn. Tray.io, n8n, Power Automate pricing not collected.

## Script-based browser automation: brittleness, flakiness, maintenance

### Takeaway
Flakiness is documented at Google scale (2016 data); the widely repeated "40-60% of QA time on maintenance" traces only to vendor blogs citing Capgemini, unverified.

### Cited Findings
- Google Testing Blog (May 2016, older data): ~1.5% of all test runs report a flaky result; ~16% of tests show some flakiness — [Google Testing Blog](https://testing.googleblog.com/2016/05/); [Talent500 summary](https://talent500.com/blog/google-flaky-test-mitigation-strategies/)
- Separate Google study: 4.56% of test failures in TAP over 15 months attributed to flaky tests — [Google Research](https://research.google/pubs/de-flake-your-tests-automatically-locating-root-causes-of-flaky-tests-in-code-at-google/)
- "40-60% of QA time on maintenance" attributed to Capgemini World Quality Report by vendor blogs, no verifiable page/year — [Shiplight](https://www.shiplight.ai/blog/near-zero-maintenance-e2e-testing); [TestDino](https://testdino.com/blog/playwright-slow-tests)
- Anecdote: one Playwright engineer reports 70% of time on maintenance (Reddit, via vendor) — [QA.tech](https://qa.tech/blog/hidden-tax-of-test-maintenance)
- Playwright auto-wait fixes timing flakiness but not selector breakage; selector brittleness called the top complaint (opinion from competing-tool vendors) — [Momentic](https://momentic.ai/blog/what-are-the-limitations-of-playwright)
- 2026 flakiness benchmarks exist but were not read: [TestDino](https://testdino.com/blog/flaky-test-benchmark-report-2026), [Quash](https://quashbugs.com/blog/flaky-test-statistics), [Trunk 20.2M CI jobs](https://www.trunk.io/blog/what-we-learned-from-analyzing-20-2-million-ci-jobs-in-trunk-flaky-tests-part-2)
- UI bots break when screens change (vendor consensus) — [Skyvern](https://www.skyvern.com/blog/api-less-system-automation-tools-legacy-enterprise/); [Automation Anywhere](https://automationanywhere.com/company/blog/rpa-thought-leadership/why-use-rpa-to-integrate-legacy-systems)

### Inferences
- Ops teams without engineers cannot own scripts; the cost is the engineer plus on-call for portal changes. CONXA's multi-signal identity and self-healing target this, but should be benchmarked rather than asserted.

### Gaps
- No QA engineer salary data collected. No peer-reviewed selector-fragility study found. Why ops teams avoid Playwright is inferred, not surveyed.

## Cloud browser infrastructure: pricing and logged-in-session problems

### Takeaway
Cloud browsers are cheap per hour (~$0.06-0.12/hr) but proxy bandwidth, auth handoff, MFA/CAPTCHA, IP detection and ToS exposure are the real problems for acting inside a customer's logged-in accounts.

### Cited Findings
- Browserbase (third-party aggregators, not official page): Free; Developer $20/mo (~100 hrs, 25 concurrency); Startup $99/mo (~500 hrs, 100 concurrency); overage ~$0.12/hr Dev, $0.10/hr Startup; proxy bandwidth ~$12/GB over allowance; Managed Browserbase Agents launched 30 Jun 2026, capped at 50 runs/mo on Startup; Steel ~$0.10/hr, Kernel ~$0.06/hr (third-party) — [ScrapeGraphAI](https://scrapegraphai.com/blog/browserbase-pricing); [TinyFish](https://www.tinyfish.ai/blog/browserbase-pricing); [Costbench](https://www.costbench.com/software/browser-automation/browserbase/); [Ecorpit](https://ecorpit.com/cloud-browsers-ai-agents-browserbase-steel-kernel-2026/); [UsagePricing](https://www.usagepricing.com/blueprint/activity/browserbase-2026-09-11-agent-runs-entitlement)
- Browserbase docs: log in manually once via Session Live View, persist state in a "context"; recommend stealth mode and residential proxies matched to IP location — [Browserbase docs](https://docs.browserbase.com/best-practices/authentication)
- Saved profiles go stale (cookies expire, sites invalidate sessions), don't solve CAPTCHAs; each fresh login risks triggering MFA (TinyFish, a competitor) — [TinyFish](https://www.tinyfish.ai/blog/tinyfish-browser-context-profiles)
- Sites that fingerprint sessions sometimes detect cloud-browser IP ranges; proxies help but no guarantee (third-party MCP review) — [AGuideToCloud](https://agents.aguidetocloud.com/mcp/browserbase-mcp/)
- Hyperbrowser stealth features may breach target ToS where automation is banned; session data and credentials entered are processed by the third party (secondary) — [HeyClaude](https://heyclau.de/tools/hyperbrowser)
- ToS enforcement mostly contractual (account suspension); CFAA application to ToS violations contested after Van Buren v. United States (2021) (secondary, not legal advice) — [ConductAtlas](https://conductatlas.com/platform/box/box-terms-of-service/provision/CA-P-057759/prohibition-on-automated-access-bots-and-scrapers/)
- Deck and Skyvern advertise built-in MFA/CAPTCHA handling (vendor claims, unverified) — [Deck](https://deck.co/blog/4-browserbase-alternatives-to-consider-in-2026)

### Inferences
- Cloud execution means vendor-held credentials/session cookies, datacenter/proxy IPs unlike the customer's usual, and per-login MFA friction. Local execution in the user's own logged-in browser avoids credential handoff, uses the user's normal IP/device, and keeps data on-machine. This is inferred from vendor-doc patterns, not measured incident data.

### Gaps
- No data on data-residency, SOC2 posture, or breach incidents for cloud-browser vendors. Official pricing pages not fetched. No primary LinkedIn/Google ToS text retrieved.

## Tasks stuck manual between systems with no API

### Takeaway
Best-documented example is insurance carrier/broker portals; legacy mainframes/green screens are the classic RPA niche. Evidence is almost entirely vendor marketing.

### Cited Findings
- Brokers log into multiple carrier systems per insured, rekey data, upload documents; one vendor estimates ~7 min per portal for E&S carriers — [QuoteSweep wholesale](https://www.quotesweep.com/solutions/wholesale); [Automat](https://runautomat.com/blog/automat-for-insurance)
- Vendor claim: insurance RPA maintenance can exceed manual work cost; a 20-portal platform may need 3-5 portal updates monthly — [Automat](https://runautomat.com/blog/automat-for-insurance); [QuoteSweep](https://www.quotesweep.com/blog/ai-web-agents-for-insurance)
- Flow Specialty (BPO) claims AI portal agents cut manual work >95% (company claim) — [InsuranceNewsNet](https://insurancenewsnet.com/oarticle/flow-launches-breakthrough-ai-portal-automation-agent-to-eliminate-the-industrys-biggest-bottleneck)
- Legacy mainframe/green-screen systems cited as core RPA case — [Zoho RPA](https://www.zoho.com/rpa/blogs/rpa-for-legacy-systems.html); [Skyvern](https://www.skyvern.com/blog/api-less-system-automation-tools-legacy-enterprise/)

### Inferences
- Government, bank and supplier portals likely fit the same pattern (login-gated, MFA, no API) but I found no sourced data; do not assert as fact.

### Gaps
- No sourced figures for government/bank/supplier portals, legacy ERP admin consoles, or manual hours per category.

## Bardeen / Axiom-style no-code browser automation

### Takeaway
No shutdowns found; both survive. Bardeen pivoted toward AI agents/GTM; independent adoption and complaint data is thin and mostly competitor-authored.

### Cited Findings
- Bardeen launched a "business-ready" AI agent Aug 2024 and raised ~$3M strategic from Dropbox Ventures and HubSpot Ventures — [BusinessWire](https://businesswire.com/news/home/20240808892830/en/Bardeen-Launches-First-Business-Ready-AI-Agent-as-More-Workers-Embrace-Task-Automation); [TechCrunch mirror](https://hn.jakechampion.name/reader/https://techcrunch.com/2024/08/08/ai-business-agent-startup-bardeen-pulls-in-strategic-investment-from-dropbox-and-hubspot/)
- Directories say Bardeen repositioned toward GTM lead intelligence / "Work Intelligence" in 2025, restructured pricing in 2025 (jump surprised users), Chrome-only as of early 2026 (low-quality aggregators) — [Deepline](https://deepline.com/gtm-stack/providers/bardeen); [NeuronFeed](https://neuronfeed.com/startups/bardeen); [Voiceflow](https://voiceflow.com/blog/bardeen-ai)
- Axiom.ai limits: Chrome/browser-only, no desktop automation, aggressive bot detection may block it, layout changes force manual selector fixes; Capterra shows 1 review at 2.0/5 (thin data; competitor sources) — [Skyvern](https://skyvern.com/blog/axiom-reviews-pricing-alternatives/); [Airframe](https://www.airframe.ai/product/axiom-ai/analysis); [Capterra](https://www.capterra.co.uk/software/217564/axiom-ai)
- An unverified teardown claims Bardeen ~$10M ARR / ~$80M valuation, conflicting with other profiles; do not use — [OpenAIToolsHub](https://www.openaitoolshub.org/ai-product-research/bardeen)

### Inferences
- Pattern: end-user-owned, single-user, selector-based recorders drift to AI agents or narrower use cases; none found is positioned as a vendor-authored, distributed, self-healing package model like CONXA.

### Gaps
- No user counts, revenue or churn from primary sources. Stagehand, Airtop, Steel, Hyperbrowser, Tray.io, n8n, Power Automate not researched in depth.
