# RPA Economics and Failure Modes (UiPath, Automation Anywhere, Power Automate, Blue Prism/SS&C, agentic pivots)

Research as of 2026-11-05. Evidence quality note: no primary Gartner/Forrester/Everest/HFS TCO reports were retrievable; most TCO percentages are secondary-source attributions and are flagged. Found fewer hard 2024-2026 failure statistics than hoped; many are 2019-2020 (flagged).

## Total cost of ownership (license, infra, CoE, developers, maintenance share)

### Takeaway
License is repeatedly claimed to be only ~25-30% of RPA TCO; build, maintenance and support are the rest, with maintenance claimed up to ~60% (Forrester, secondary attribution, unverified). Per-bot list prices vary hugely by vendor (Power Automate $150/bot/mo vs UiPath ~$8-10k/yr per unattended robot by one source), so license is the cheap part of the bill.

### Cited Findings
- HfS Research is cited as saying licensing is only 25-30% of RPA TCO; implementation, maintenance and support take 70-75%. SECONDARY attribution, original not found — [Hackernoon](https://hackernoon.com/robotic-process-automation-cost-deciphered); [ITRex](https://itrexgroup.com/blog/robotic-process-automation-cost/)
- Forrester is cited (secondary blogs) as estimating maintenance can be up to 60% of total RPA program cost; original report not located — [Auxilio Bits](https://www.auxiliobits.com/?p=21167); [Lleverage](https://www.lleverage.ai/blog/ai-automation-platforms-vs-traditional-rpa-total-cost-comparison-2026)
- Gartner is cited (secondary) as saying annual maintenance runs 15-20% of initial software cost; conflicts in base and magnitude with the Forrester 60% claim; unverified — [Prioxis](https://prioxis.com/blog/rpa-implementation-cost)
- McKinsey is cited (secondary) that a bot costs about 1/3 of an offshore employee and 1/5 of an onshore one — [ITRex](https://itrexgroup.com/blog/robotic-process-automation-cost/)
- Forrester TEI (vendor-commissioned) for Automation Anywhere: composite org $13.2M risk-adjusted benefits over 3 years, 262% ROI, payback under 12 months. Benefits-side, vendor-commissioned — [Prioxis/other secondary listing](https://prioxis.com/blog/rpa-implementation-cost)
- UiPath list pricing varies by source: Vendr ~$8,000-10,000/yr per unattended robot and $3,500-5,000/user/yr attended — [Vendr](https://www.vendr.com/marketplace/uipath); Activepieces $9,200 unattended / $1,450 attended — [Activepieces](https://www.activepieces.com/blog/how-much-does-uipath-pricing-2026-cost); Automation Atlas (July 2026) ~$1,680 unattended / ~$420 attended — [Automation Atlas](https://automationatlas.io/answers/uipath-pricing-explained-2026/). Sources conflict; UiPath does not publish firm prices.
- Power Automate: Premium $15/user/month (incl. attended RPA); Process (unattended) $150/bot/month; Hosted Process $215/bot/month — [Automation Atlas RPA comparison](https://automationatlas.io/answers/rpa-pricing-comparison-2026/)
- Automation Anywhere does not publish pricing; AWS Marketplace shows a $126,000 12-month sample configuration for its Agentic Process Automation System (reference only) — [G2](https://www.g2.com/products/automation-anywhere-rpa-robotic-process-automation/pricing); [AWS Marketplace](https://aws.amazon.com/marketplace/pp/prodview-3vz6dksgbr3gy)
- Staffing: one consultant says one automated process needs roughly one full-time RPA developer plus part-time transformation consultant, architect and QA; a developer can eventually carry up to ~30 processes — [Conectys](https://conectys.com/blog/posts/rpa-projects-how-long-until-implementation-and-who-is-involved/page/51)
- Pilot bot for an SME $4,000-$15,000 over 3-6 weeks excluding licensing, hosting, training (vendor guide) — [DigiSoft](https://www.digisoftsolution.com/blog/rpa-development-service-guide)
- Forrester Consulting (Tricentis-sponsored, Feb 2020, n=271 US): firms struggling with bot resiliency are 4x more likely to say they are very ineffective at controlling costs — [Business Wire](https://www.businesswire.com/news/home/20200212005226/en/RPA-Reality-Check-New-Forrester-Research-Identifies-Barriers-to-RPA-Scalability)

### Inferences
- Business framing: the license line item is the minority of spend; the true cost is people (developers, CoE, QA) and repair work. A model that removes per-process build and repair labor attacks the 70%+ portion.
- Any "per bot cost" comparison is dominated by the vendor chosen; use ranges and label them quotes-dependent.

### Gaps
- No primary Gartner, Everest, EY or Deloitte TCO split retrieved; Everest/HFS/Deloitte/EY maintenance percentages not found.
- No reliable CoE headcount/cost figures; no reliable infrastructure (Orchestrator/VDI) cost numbers.

## Failure, abandonment and breakage

### Takeaway
The best-supported failure figure is EY's "30-50% of initial RPA projects run into trouble" (2019). Scaling evidence (Deloitte 3% at 50+ bots, ~2018-19; Forrester 2020: most firms under 10 bots) shows RPA programs stall. Breakage is routine: 87% reported bot failures (Pega 2019); 45% see breakage weekly or more (Forrester/Tricentis 2020). All are old; I found no credible 2024-2026 equivalents.

### Cited Findings
- EY note: 30-50% of initial RPA projects run into trouble (2019) — [EY Global Tax News](https://globaltaxnews.ey.com/news/2019-6407-five-design-principles-to-help-build-confidence-in-robotic-process-automation-implementations). One blog misattributes the range to Deloitte; no primary Deloitte source — [VSoft](https://blog.vsoftconsulting.com/blog/why-40-of-rpa-implementations-fail-6-ways-to-succeed)
- Deloitte earlier Global Robotics Survey: only 3% of respondents had scaled RPA to 50+ robots (older survey, ~2018-19) — [Silicon Republic](https://www.siliconrepublic.com/?p=737047)
- Deloitte 2020 Intelligent Automation survey: 13% say they implement automation at scale; sample size varies by source (320-523) — [Deloitte](https://www.deloitte.com/us/en/insights/topics/talent/intelligent-automation-2020-survey-results.html)
- Forrester (2020, via CIO.com): majority of enterprises have fewer than 10 bots in production; only 13% have 100+ — [CIO.com](https://www.cio.com/article/3490239/4-surprising-rpa-research-findings-from-forrester.html)
- Forrester/Tricentis 2020: 45% of firms face bot breakage weekly or more often (secondary coverage; primary not retrieved); scripting-based RPA suffers more broken bots — [Business Wire](https://www.businesswire.com/news/home/20200212005226/en/RPA-Reality-Check-New-Forrester-Research-Identifies-Barriers-to-RPA-Scalability)
- Pega survey of 509 decision makers (Sept 2019): 41% said bot management took more time/resources than expected; 87% experience some bot failures (Pega is a competing vendor) — [AIwire](https://aiwire.net/2019/09/13/survey-most-businesses-find-rpa-effective-but-hard-to-deploy-and-maintain)
- Tech Mahindra whitepaper (vendor selling self-healing RPA): estimated 50% of developer resources go to fixing bots — [Tech Mahindra](https://www.techmahindra.com/insights/whitepapers/afterlife-of-rpa/)
- Unverified claims: "30-70% of effort spent maintaining bots" (2026 blog), and a "Forrester 2025" survey I could not confirm exists — [Activepieces postmortem](https://www.activepieces.com/blog/can-agentic-ai-replace-your-rpa-bots-a-real-postmortem); [Ecorpit](https://ecorpit.com/replace-rpa-computer-use-agents-cost-architecture-2026/). Treat as unreliable.
- Practitioner anecdote: bank-portal bots breaking every quarter — [Beancount forum](https://beancount.io/forum/t/rpas-dirty-secret-when-bank-portal-bots-break-every-quarter/2577)

### Inferences
- Failure is mostly about scale and upkeep, not initial build: programs stall under maintenance load and governance. This is the gap a self-healing identity model targets.
- The failure stats are 5-7 years old; the report should flag this, not present them as current.

### Gaps
- No primary EY report text; no 2024-2026 survey quantifying maintenance % of time from an independent analyst.

## Time to build, skills, who builds

### Takeaway
Typical bot: 2-12 weeks depending on complexity, built by specialist developers plus QA/architect; citizen-developer evidence not found.

### Cited Findings
- 3-4 weeks low complexity, 6-7 medium, 9-11 high (Conectys blog); one service firm says 2-6 weeks — [Conectys](https://conectys.com/blog/posts/rpa-projects-how-long-until-implementation-and-who-is-involved/page/51); [Acquaint Softtech](https://acquaintsoft.com/rpa-development-services)
- Danske Bank head of robotics: 6 weeks is the prognostication, but their team sees 10-12 weeks — [IQPC podcast](https://intelligentautomation.iqpc.com/the-ai-network-podcast-susanne-skaarup-head-of-0)
- Cognizant case study (vendor-reported): CoE cut bot development cycle time 50% and cost per bot 55% — [Cognizant](https://www.cognizant.com/us/en/case-studies/robotic-process-automation-utilities)
- Staffing profile: developer + part-time consultant, architect, QA (see TCO section) — [Conectys](https://conectys.com/blog/posts/rpa-projects-how-long-until-implementation-and-who-is-involved/page/51)

### Inferences
- Even optimistic timelines are weeks per process plus analyst/QA overhead; a record-once demonstration model competes on hours-to-days, but I have no measured Conxa-side numbers here.

### Gaps
- Nothing reliable on citizen-developer share or success; no Microsoft/Power Automate Desktop build-time data.

## Poor-fit processes (the long tail)

### Takeaway
Vendors and practitioners say RPA suits stable, high-volume, rule-based work with stable applications and low exception rates; variable, low-volume, judgment-heavy, UI-volatile work is a poor fit, so it stays manual.

### Cited Findings
- UiPath event deck lists favourable traits: stable process and underlying applications, low exception rates, high volume, low complexity, rule-based — [UiPath deck](https://www.uipath.com/hubfs/resources/documents/event_PDFs/Breakout-2.pdf)
- RPA not good at variability or change; needs defined inputs/outputs — [Medium](https://medium.com/womenintechnology/part-2-in-rpa-not-everything-can-be-automated-69c016e36361)
- UI-based RPA is a weak fit where interfaces change often; reserve for legacy without APIs — [Industrial Monitor Direct](https://industrialmonitordirect.com/blogs/knowledgebase/uipath-rpa-fragility-error-handling-and-drift-detection-best-practices)
- High exception rate (~20%+) plus low volume yields poor ROI — [Think Insights](https://thinkinsights.net/print/pdf/node/27947)
- Organisations often pick processes that are too complex, judgment-heavy or with inconsistent inputs — [Ksolves](https://www.ksolves.com/blog/uipath/common-challenges-and-how-our-experts-solve-them)

### Inferences
- The long tail follows from RPA's economics: fixed build and upkeep cost per process needs volume to amortise. Cheaper per-process build/maintenance shifts the break-even toward low-volume workflows.

### Gaps
- No single authoritative UiPath/AA document stating "do not automate low-volume, high-variability processes"; this is practitioner consensus plus a UiPath event deck.

## Agentic repositioning (2025-2026) and what it admits

### Takeaway
All majors now sell "agentic automation" layered on deterministic RPA, and say deterministic control remains essential. Gartner warns >40% of agentic projects will be cancelled by 2027 and that vendors "agent-wash" RPA.

### Cited Findings
- UiPath FY2026 (ended Jan 31, 2026): revenue $1.611B (+13%), ARR $1.853B (+11%), net new ARR $186M; describes itself as leader in agentic automation combining deterministic automation, agentic AI and orchestration — [UiPath IR](https://ir.uipath.com/news/detail/431/mailto:PR@uipath.com)
- Automation Anywhere announced agentic solutions (June 25, 2025) and sells AI Agent Studio / Agentic Process Automation System — [Automation Anywhere press](https://automationanywhere.com/company/press-room/automation-anywhere-unveils-new-ai-automation-enterprise-system-empowering); [Gartner Peer Insights listing](https://www.gartner.com/reviews/product/ai-agent-studio)
- SS&C Blue Prism: AI platform with 20 AI agents (Q1 2025) — [MarketBeat](https://www.marketbeat.com/earnings/reports/2025-4-24-ssc-technologies-holdings-inc-stock); partner programme revamped for agentic AI (Dec 2025) — [FinTech Global](https://fintech.global/2025/12/18/ssc-blue-prism-revamps-partner-programme-for-agentic-ai/); Blue Prism WorkHQ (spring 2026) complements existing RPA/BPM, "keeping deterministic automation where control is essential"; 23,000+ customers — [Business Wire](https://www.businesswire.com/news/home/20260429512507/en/)
- Gartner (June 2025): >40% of agentic AI projects will be cancelled by end of 2027 (costs, unclear value, weak risk controls); flags "agent washing" of RPA tools, only ~130 genuine agentic vendors — [The Star/Reuters](https://www.thestar.com.my/tech/tech-news/2025/06/25/over-40-of-agentic-ai-projects-will-be-scrapped-by-2027-gartner-says)
- Fetched pricing: Automation Anywhere AI Agent Studio does not host own models (third-party FAQ quote) — [Gartner listing](https://www.gartner.com/reviews/product/ai-agent-studio)

### Inferences
- Positioning of agents "where they add value" while keeping RPA for control is an implicit admission that pure UI scripts cannot handle variability, and that pure agents lack determinism/cost control. Conxa's "deterministic first, LLM only on failure" sits in this same hybrid space, but starts from the agent/MCP side and avoids a per-bot license stack.
- UiPath growth of 11-13% (large, mature vendor) suggests incumbent expansion is modest; do not over-read this as RPA decline without more data.

### Gaps
- No UiPath 10-K risk-factor language retrieved; no Q3/Q4 2026 (calendar) data; no Microsoft Power Automate agentic detail; no vendor statement quantifying bot maintenance.

## Deployment model and distributing to external customers

### Takeaway
RPA licensing is built around the buyer's own machines, orchestrator and VDI, and vendor terms restrict use for third parties. No evidence that a SaaS vendor can ship a bot to its customers as a product; the model is internal enterprise automation.

### Cited Findings
- UiPath licensing model (2018.3/2018.4, older versions) bars customers from letting third parties use the platform outside the license grant, and from providing services to third parties without agreement — [UiPath 2018.4](https://www.uipath.com/licensing-models/2018-4); [UiPath 2018.3](https://www.uipath.com/licensing-models/2018-3)
- MSSA grants customer and affiliates a limited, non-sublicensable, non-transferable right — [UiPath MSSA (Fujifilm copy)](https://www.fujifilm.com/fbau/-/media/FBAU/2,-d-,-Products/EULA/UiPath_MSSA.pdf)
- Unattended robots run without supervision via Orchestrator; licensing counts maximum simultaneous robots — [UiPath docs](https://docs.uipath.com/es/robot/standalone/2020.10/user-guide/about-licensing)
- Power Automate splits attended (per user) from unattended per-bot pricing — [Automation Atlas](https://automationatlas.io/answers/rpa-pricing-comparison-2026/)

### Inferences
- Reading from older license pages and the MSSA, distributing a UiPath/AA bot to external customers appears contractually unsupported without a special agreement; each end customer would need their own license, runtime and orchestrator. This is my inference, not a vendor statement. Conxa's per-customer installer with local execution is structurally different.

### Gaps
- Current (2026) UiPath/AA/Microsoft OEM or embedded-distribution terms not found; no VDI-specific terms; no explicit statement about SaaS vendors shipping bots. Recommend verifying with vendor licensing docs before asserting in a report.

## Process discovery and task mining

### Takeaway
Task/process mining is sold as add-ons with quote-only pricing, commonly bundled into broader licensing; evidence on capturing institutional knowledge is not found.

### Cited Findings
- UiPath Task Mining is now sold via Unified Pricing; Assisted Task Mining does not consume AI/Platform Units; Unassisted Task Mining deprecated (removal scheduled Dec 1, 2025) — [UiPath docs](https://docs.uipath.com/task-mining/automation-cloud/latest/user-guide/unified-pricing); [release notes](https://docs.uipath.com/task-mining/automation-cloud-public-sector/latest/user-guide/november-2025)
- No published price list; contact sales — [OMR](https://omr.com/en/reviews/product/uipath-task-mining/pricing)
- Reviewer anecdotes: UiPath Process Mining cheaper than rivals; Celonis "very expensive" (one from 2023) — [PeerSpot](https://peerspot.com/questions/what-is-your-experience-regarding-pricing-and-costs-for-uipath-process-mining)

### Inferences
- Discovery is a separate cost layer before build; Conxa's record-by-demonstration collapses discovery and build into one act (inference).

### Gaps
- No Celonis, Skan.ai or Soroco pricing found; no evidence on whether these tools capture tacit institutional knowledge.
