# What Conxa can do for a business, explained simply

## Before you start: the short version

Conxa lets a person **show a computer how to do a task in a web browser once**. Conxa then turns that recording into a fixed, step-by-step program called a **skill**. The skill runs on the customer's own computer, using the customer's own logins. An AI assistant (like Claude Desktop) can start the skill whenever someone asks for it.

The big idea is not "faster automation". The big idea is a **different way of paying for and owning automation**:

1. **You pay once to build it, not every time it runs.** Running a skill a million times costs Conxa the same as running it twice.
2. **The company that built a skill can hand it to all of its customers.** A software company can record "how to set up your account" once and give it to thousands of customers.
3. **Passwords never leave the customer's computer.** No outside company holds the logins.
4. **Most small breakages fix themselves for free.** Conxa tries cheap, no-AI repairs first. It only calls an AI model (which costs money) when those fail.
5. **The steps are fixed and can be checked.** The skill does not "make things up" each time. A reviewer can see exactly what it will do.

Other products have **some** of these pieces. In our research, **none had all five together**. And several of these come from Conxa's *business model*, not from a feature. A competitor cannot just "add a button" to copy them.

**The honest warning, up front.** Everything in this report is about how Conxa is *designed*. It is not yet proven in the market:

- Conxa has **no paying customers and no revenue yet**.
- There is **no measured success rate** on real customer work.
- There has been **only one pilot demo** (Centelon, 7 August 2026).
- The building tool works **only on Windows**.
- The installer is **not yet "signed"** (explained in section 5), which can make Windows block it.
- Some kinds of breakage **still cannot be fixed automatically**.
- AI agents from big companies are **getting better quickly**.

So: the advantage is real on paper. It still has to be proven with real customers.

> **How to read the evidence in this report**
>
> - When we say something is **"built"**, it means Conxa's own project documents say it exists. We did not test it by running the code.
> - When we say something is **"proposed"** or **"not built"**, it does not exist yet.
> - Many outside numbers come from **company blogs or second-hand reports**, and some are **old**. We say how old and how trustworthy each one is. Please check the original source before you quote any number to a customer.

### A few words you will see a lot

| Word | What it means in plain English |
|---|---|
| **Skill** | A recorded browser task that Conxa has turned into a fixed, repeatable program. |
| **AI agent** | An AI assistant that can use a computer or browser on its own, deciding each click as it goes. |
| **Computer-use agent** | An AI agent that looks at screenshots of the screen and decides where to click. |
| **RPA** (Robotic Process Automation) | Older "software robots" (UiPath, Automation Anywhere, Power Automate) that repeat clicks and typing. Usually built by specialists. |
| **API** | A "back door" one program offers so other programs can talk to it directly, without clicking through the screen. |
| **iPaaS** | Tools like Zapier, Make and Workato that connect apps through their APIs. |
| **MCP** | A common "plug" standard that lets AI assistants use outside tools. Conxa uses it so skills work inside many AI assistants. |
| **Tokens** | The unit AI models charge by. More tokens means a bigger AI bill. |
| **Tier A / Tier B repair** | Conxa's two levels of fixing a broken step. Tier A uses no AI and costs nothing. Tier B calls an AI model and costs tokens. |

---

## 1. The obvious value: no bill per run, and no repair crew

### What Conxa charges for

Conxa's pricing idea is **"pay for reach, not for runs"**. The company that *builds* skills pays a monthly fee. The people who *use* the skills pay nothing extra, however many times they run them.

| Plan | Price per month | What it is for |
|---|---|---|
| Free | ₹0 (30-day trial) | Trying it on one machine |
| Starter | ₹19,999 | Using skills inside your own company |
| Pro | ₹49,999 | Giving skills to outside customers (with Conxa branding) |
| Enterprise | from ₹99,999 | Giving skills to customers under your own brand, and more control |

*(Source: Conxa's internal cost model and product plan. These are the planned prices.)*

**Why it can be priced this way:** the skill runs on the customer's computer. Conxa has no server doing the work, so each extra run costs Conxa nothing.

**What it costs Conxa to build a skill:** Conxa's own model says about **$0.02 to $0.38 to turn one recording into a skill**, depending on the plan. That is cheap because the "where to click" instructions are written by ordinary code, not by an AI. *(This is a model, not a real bill. Some of its inputs are out of date. See section 5.)*

**What this means for a business:** with most other tools, **the more you use automation, the bigger the bill**. That creates an awkward situation: success costs you money. With Conxa, heavy use does not raise the bill.

### How the other options charge

Think of it like transport:

- **An AI agent is like a taxi.** You pay every single trip.
- **RPA is like owning a fleet of cars.** The car is not that expensive. The mechanics, insurance and garage are.
- **Conxa is like building a train track once.** After that, each trip is nearly free.

The numbers behind this:

- **AI agents** pay for AI usage on every run. Research tests found about **$0.05 to $0.46 per task**, and failed tasks often cost *more* than successful ones ([research paper](https://arxiv.org/pdf/2608.27808); [another study](https://arxiv.org/pdf/2606.01533)). One write-up compared a screenshot-reading agent with a scripted path. The agent took **53 steps and 551,000 tokens**. The script took **8 steps and 12,000 tokens** ([Towards AI](https://pub.towardsai.net/computer-use-the-hardest-harness-problem-8752e5f94fde)). That is one person's example, not a formal study.
- **App-connecting tools (iPaaS)** charge per task or per "credit". The middle price for a Workato contract is about **$64,500 a year**, based on a small sample ([Vendr](https://vendr.com/marketplace/workato)).
- **RPA** can look cheap. Microsoft's Power Automate robot costs **$150 per robot per month** ([Automation Atlas](https://automationatlas.io/answers/rpa-pricing-comparison-2026/)). But the licence is reported to be only **25–30% of the total cost**. The rest goes on building, fixing and supporting the robots (a figure credited to the analyst firm HfS, reported second-hand by [Hackernoon](https://hackernoon.com/robotic-process-automation-cost-deciphered)).

So the bigger hidden cost is not the price tag. It is **the people you need to keep fixing things**.

### The repair problem

Software robots break when a website changes, even a little. A button moves, a label changes, a new pop-up appears. Then someone has to fix the robot by hand.

What the (old) data says:

- In a **2020** study by Forrester, paid for by the testing company Tricentis, **45% of companies said their robots broke every week or more often**. Companies that struggled with this were **four times more likely** to say they could not control their costs ([Business Wire](https://www.businesswire.com/news/home/20200212005226/en/RPA-Reality-Check-New-Forrester-Research-Identifies-Barriers-to-RPA-Scalability)).
- A **2019** survey by Pega (a rival vendor) found **87% of companies had robot failures** ([AIwire](https://aiwire.net/2019/09/13/survey-most-businesses-find-rpa-effective-but-hard-to-deploy-and-maintain)).
- In **2019**, EY said **30–50% of early RPA projects ran into trouble** ([EY](https://globaltaxnews.ey.com/news/2019-6407-five-design-principles-to-help-build-confidence-in-robotic-process-automation-implementations)).

**Important:** these numbers are **5 to 7 years old**. We found no good recent numbers. They show the long-standing pattern, not today's exact rate.

### How Conxa tries to avoid the repair crew

Conxa is designed to cut down on hand-fixing in three ways. All three are **built**:

1. **It remembers each button in many ways.** It records the button's name, its role, its text, where it sits, and other clues. Think of how you recognise a friend: by face, voice, clothes and the way they walk. If one clue changes, the others still work.
2. **It refuses to guess.** If two buttons look almost equally right, Conxa stops instead of picking one. It only clicks when the winner is clearly ahead of the runner-up. A clean stop is safer than a wrong click.
3. **It tries free repairs first (Tier A).** These include waiting longer, scrolling, re-opening a menu, or closing a known pop-up. No AI model is used, so they cost nothing. Only if these fail does it call an AI (Tier B), which costs tokens.

**Not yet proven:** how often Tier A succeeds on real customer work has not been measured. And Conxa **cannot yet repair a button that has been renamed** (a known gap called EXEC-28).

### Why "fixed steps" matter so much: the reliability maths

Imagine a task with 20 steps. Say each step works 95% of the time. That sounds good. But all 20 steps must work for the task to succeed:

| Success rate per step | Chance the whole 20-step task works |
|---|---|
| 95% | about **36%** |
| 98% | about **67%** |
| 99% | about **82%** |
| 99.9% | about **98%** |

*(This is our own maths. It assumes each step succeeds or fails on its own. It is not a measurement of any real product.)*

In simple words: **small mistakes add up fast over many steps.** A long task needs each step to be almost perfect.

How AI agents score today:

- On short, standard tests, AI agents now score about **80–86%** in 2026, according to third-party trackers. People score about 72% on the same test ([BenchLM](https://www.benchlm.ai/benchmarks/osworld-verified)). These are not the official results.
- On a stricter **2025** test on real, live websites, OpenAI's Operator scored **61%** and most others about **30%**. Scores dropped by about 31 points from easy to medium tasks ([Online-Mind2Web](https://arxiv.org/html/2504.01382v4)). Those AI versions are now out of date.
- **No study we found measures 20+ step tasks on real business software.**

**The business point:** for work that runs on its own, has many steps, and touches money or customer records, you need each step to be extremely reliable. An AI that re-decides every click has not yet been shown to reach that level. And **stopping cleanly is far cheaper than clicking the wrong thing.**

---

## 2. Hidden advantages that show up after a company starts using Conxa

### Hidden advantage 1: a company can hand automation to all of its customers

This is the **least obvious and most valuable** thing about Conxa.

**The problem with RPA here:** we found no sign that a software company can sell or give an RPA robot to its own customers as part of its product. UiPath's licence terms (in the older versions we could find) give limited rights that cannot be passed on. They also forbid serving outside parties without a special agreement. So every end customer would need their own licence, their own robot software and their own control server ([UiPath 2018.4 terms](https://www.uipath.com/licensing-models/2018-4); [UiPath master agreement](https://www.fujifilm.com/fbau/-/media/FBAU/2,-d-,-Products/EULA/UiPath_MSSA.pdf)). *We could not find the current 2026 terms, so please check before you say this to a customer.*

**How Conxa works instead (built):**

1. The company **publishes** a versioned set of skills.
2. The customer installs a **small installer**. It contains only the company's identity and web addresses, not the skills themselves.
3. The customer's computer then **downloads the skills** and keeps them up to date by itself.
4. Updates are **checked with a digital signature**, so the customer knows they are genuine.

**The second-order benefit:** when a website changes and a skill needs fixing, the company fixes it **once** and republishes. **Every customer gets the fix automatically.** It is like a phone app update: the maker fixes a bug once and every phone gets it. So the company's support work does not grow every time it gains a customer.

**What this could mean for a software company (these are educated guesses, not proven):**

- **Setting up big customers is slow and costly.** It is often said to cost **15–20% of the first year's contract** and take **90–180 days** (credited to OpenView, not confirmed, via [Monetizely](https://www.getmonetizely.com/articles/how-to-track-implementation-and-onboarding-costs-a-strategic-guide-for-saas-executives)).
- **Profit on setup services is low** and the figures vary a lot (15–50% depending on the source).
- **Many customers quit early.** A widely repeated but weakly sourced claim says **44% of cancellations happen in the first 90 days** ([Pulse](https://www.pulse.bot/saas/news/time-to-value-why-onboarding-is-the-new-saas-retention-battleground-in-2026-883295c9-044f-4695-89a3-ffdd2d9ce88f/)).

A ready-made **"set up my account for me"** skill attacks all three: lower setup cost, faster first success, and less chance the customer gives up.

The same skill can also turn a support ticket that says "how do I do X?" into **"do X for me"**. Support tickets reportedly cost **$3–25 each** depending on the channel (second-hand, [Stealth Agents](https://stealthagents.com/research/customer-support-ticket-deflection-statistics-2026)).

Companies already pay a lot to guide people through screens. SAP agreed to buy WalkMe, a tool that shows on-screen guides, for about **$1.5 billion in 2024**. That was less than WalkMe was valued at in 2021, so it is not a clean sign of growth ([TechTarget](https://www.techtarget.com/enterprise-software/news/366587409/SAP-snaps-up-WalkMe-for-15B?amp=1)). Conxa goes one step further than a guide: **it does the task**, rather than showing how.

*No study we found proves that skill-based setup improves customer retention. This is a reasonable guess, not a fact.*

### Hidden advantage 2: a recorded task becomes a company asset you can run

**Today**, many companies write their processes down as how-to guides. Tools like Scribe make this easy. Scribe raised money at a **$1.3 billion valuation in November 2025** ([SiliconANGLE](https://siliconangle.com/?p=720328)), which shows companies care about this. But a guide:

- goes **out of date**,
- has to be **read and followed by a person**, and
- **cannot do the work itself**.

**A Conxa skill is the process itself.** It has version numbers, it can be reviewed, and **it still works after the employee who recorded it leaves**. Think of the difference between a recipe card and a kitchen machine that cooks the dish.

**Why this matters when people leave:**

- The well-known cost numbers for lost knowledge are **old and weak**. Panopto's **$47 million per large company per year** comes from a 2018 survey. The **$31.5 billion** for the Fortune 500 traces back to around 2004 ([SHRM](https://shrm.org/hr-today/news/hr-magazine/Pages/0504covstory.aspx)). Treat them as a rough direction only.
- A **stronger argument** is the shortage of skilled people. In accounting, the number of first-time CPA exam takers fell from about **48,000 in 2016 to 32,000 in 2021**. More than **42% of firms said they were turning work away** ([NYSSCPA](https://nysscpa.org/news/1051685-how-ai-is-helping-firms-address-the-pipeline-shortage-2025-05-13), second-hand).

Recording a retiring expert's routine clicks in a website is cheap. **But be honest about the limit:** a skill captures the *steps* a person takes, not their *judgment*. It knows *how* they did something, not *why* they chose to.

**A smaller bonus (built):** within one company, Conxa builds an **"app map"**, a cleaned-up outline of each website's screens and pop-ups. It holds no customer data. Each new recording starts with what earlier recordings learned. *Sharing these maps between different customers is not built.*

### Hidden advantage 3: software you cannot connect becomes something you can automate

- The average company uses about **275 software apps** (Zylo, 2024 data, [Zylo](https://zylo.com/news/2025-saas-management-index)).
- On average, only **29% of a company's apps are connected** to each other ([MuleSoft 2025](https://www.salesforce.com/au/blog/mulesoft-connectivity-benchmark-2025/)).
- Only about a quarter of organisations build all their software "API-first", and only **24% design their APIs for AI agents** ([Postman 2025](https://www.businesswire.com/news/home/20251008162423/en)).
- We found **no reliable number** for how much business software has no usable API at all.

Building a proper connection to an old product is estimated at **4–8 weeks** (one vendor's claim, [Plavno](https://plavno.io/company/insights/model-context-protocol-explained)). For some healthcare systems, access is only given through partnerships or fees ([Supergood](https://supergood.ai/blog/mcp-for-software-without-a-public-api)).

**Conxa works through the screen**, the same way a person does. So you **do not have to wait** for another company to build an API for you. It is like walking in through the front door when the back door is locked.

### Hidden advantage 4: automating work that is "too small to automate" today

RPA only pays off for **big, steady, repetitive work**, because each robot is expensive to build and maintain. A UiPath presentation and practitioners agree it suits work that is stable, high-volume and has few exceptions ([UiPath deck](https://www.uipath.com/hubfs/resources/documents/event_PDFs/Breakout-2.pdf)). A typical robot takes **2–12 weeks** to build, with a developer, a tester and an architect involved ([Conectys](https://conectys.com/blog/posts/rpa-projects-how-long-until-implementation-and-who-is-involved/page/51)).

So lots of work stays manual because it is **not worth building a robot for**. Examples:

- the **monthly** reconciliation,
- the **quarterly** filing to a regulator,
- the **weekly** upload to an insurance carrier's website.

**If** recording a skill takes minutes or hours instead of weeks, and **if** most repairs happen automatically, then these smaller jobs become worth automating.

**Two honest caveats:**

1. **We have no measured time for how long it takes to build a Conxa skill.** Conxa's own documents admit building is real work. The author may answer up to five questions about the recording, review the result, and set up choices for pop-ups.
2. **"Growing without hiring as many people" is a guess.** No source we found measures how operations staff grow with revenue in any industry.

**An example with real-world numbers:** a medical practice with 10 doctors. Each doctor handles about **39 prior authorisations a week** (insurance approvals before treatment). This takes about **13 hours a week** of doctor and staff time per doctor (AMA 2024, via [HMP Global](https://www.hmpglobalevents.com/tciv/news-insights/prior-authorization-burden-hidden-valuation-driver-ama-finds-39-prior-auth)). Across 10 doctors, that is about **130 hours a week, or roughly three full-time staff**. *(Our maths on second-hand numbers.)*

### Hidden advantage 5: safety properties a strict buyer can check

**Logins stay on the customer's computer (built).**

- Login details are never put into the skills that get shipped.
- Tokens (digital keys) are kept in the computer's own secure password store.
- Saved login sessions are scrambled with strong encryption (AES-256-GCM).

**Cloud browser services work the other way around.** You give the outside company your login or session. Its computers then visit the website from data-centre internet addresses, which websites sometimes spot and block. Saved sessions expire, and each fresh login may ask for a phone code again ([Browserbase docs](https://docs.browserbase.com/best-practices/authentication); [TinyFish](https://www.tinyfish.ai/blog/tinyfish-browser-context-profiles), a competitor's blog).

**Less exposure to "trick" attacks on AI.** Some websites hide instructions that try to trick an AI into doing something harmful. This is called **prompt injection**.

- Anthropic reported that, even after safety fixes, these attacks still worked **11.2% of the time** on Claude for Chrome (August 2025, [Claude blog](https://claude.com/blog/claude-for-chrome)).
- OpenAI said prompt injection is **"unlikely to ever be fully solved"** ([The Decoder](https://the-decoder.com/openai-admits-prompt-injection-may-never-be-fully-solved-casting-doubt-on-the-agentic-ai-vision/)).
- Both companies advise users to **avoid using their agents on banking and sensitive sites**.

When a Conxa skill runs normally, it follows fixed steps. **It does not read the web page and ask an AI what to do next.** So a hidden trick message on the page has much less chance to steer it.

*But Conxa does not remove this risk completely.* The AI assistant that starts the skill, and the Tier B repair step, can still see page content.

**Easier to audit.** An emerging view, from vendors and one 2026 claim about the audit body COSO that rests on a single blog, is: **"use AI when building, then run fixed, version-tracked code"** ([FloQast](https://www.floqast.com/blog/what-ai-audit-controls-actually-look-like)). That is exactly how Conxa is built. *No auditor has confirmed that Conxa meets this standard.*

**Two built-in safety rules (built):**

- **Pop-ups that ask for a decision are never answered for you.** For example: "This file already exists: Replace / Keep both / Cancel". Conxa will not pick a risky option like "Replace" unless the skill's author approved it in advance.
- **When unsure, it stops.** It never guesses between two similar-looking buttons.

---

## 3. Valuable uses we may not have considered

### Healthcare billing: the best-measured opportunity

- **Prior authorisation** (getting insurer approval before treatment) costs about **$11–13 per request when done by hand**, versus about **$3 when fully electronic** ([CAQH 2025](https://www.caqh.org/blog/2025-caqh-index-shows-u.s.-healthcare-avoided-258-billion-and-accelerated-automation-interoperability-and-ai-adoption)).
- **Fixing a denied insurance claim** costs about **$25–118 per claim**, and **35–65% of denied claims are never fixed at all**. That is money simply lost ([Becker's](https://www.beckershospitalreview.com/finance/denial-rework-costs-providers-roughly-118-per-claim-4-takeaways.html)).
- Insurer websites usually **have no API**, so staff click through them by hand.

**The catch:** healthcare is also where Conxa's weak spots hurt most. These sites often need a phone code at every login. They are subject to strict health-privacy rules (HIPAA), which we did not research. And Conxa only works on Windows.

### Insurance brokers: the second opportunity

- **74% of insurance agents** say typing the same customer details into many insurers' websites is unacceptable ([Insurance Journal 2024](https://www.insurancejournal.com/news/national/2024/01/16/755751.htm)).
- One vendor claims about **98% of property and casualty insurers have no API for quotes** ([QuoteSweep](https://www.quotesweep.com/blog/98-percent-carriers-no-api), vendor claim).
- Another vendor claims insurer websites change **3–5 times a month across 20 sites**. That makes fixing RPA robots cost more than doing the work by hand ([Automat](https://runautomat.com/blog/automat-for-insurance), vendor claim).

### Less obvious uses that follow from how Conxa is built

These come from thinking about the design, not from market data. Treat them as ideas to test.

- **Capture knowledge when someone leaves.** In an employee's last week, record their regular website routines. Their departure then leaves behind **working skills, not a binder** nobody reads.
- **Outsourcing firms and consultancies.** These firms repeat the same process for many clients, so "build once, install for many clients" fits them well. Conxa's own planning calls this the highest-leverage type of customer.
  - The outsourcing giant Genpact expected over **$1 billion of AI-agent contracts in 2026**. It said clients moving to AI-agent delivery gave it more than **3 percentage points of extra profit margin** ([Nasdaq](https://www.nasdaq.com/articles/genpact-q2-earnings-call-highlights)).
  - Its rival Concentrix lowered its forecast as clients pushed for lower prices (second-hand).
  - Offshore workers cost roughly **$12,000–29,000 a year** (vendor blogs). So for low-wage work, the value of Conxa is **fewer errors, a record of what was done, and work at any hour**, not cheaper labour.
- **Evidence packs for auditors.** Run the same version-tracked skill every month. Keep the run record as proof that the control was carried out. This could help with month-end finance checks and with drug-industry record rules (FDA 21 CFR Part 11, [MasterControl](https://www.mastercontrol.com/glossary-page/21-cfr-part-11/)). *There is no volume data, and no auditor has approved this.*
- **Other ideas worth testing (no data yet):** supplier websites, online marketplace seller work, state government filing websites, customer setup at software companies, and customer identity checks ("KYC").

### Making old software "ready for AI assistants"

Big software companies like **Salesforce, HubSpot and Atlassian** now ship official MCP connections so AI assistants can use their products. Gartner expects **40% of business apps to include task-specific AI agents by the end of 2026** ([Gartner](https://gartner.com/en/newsroom/press-releases/2025-08-26-gartner-predicts-40-percent-of-enterprise-apps-will-feature-task-specific-ai-agents-by-2026-up-from-less-than-5-percent-in-2025)).

A smaller, specialist software company with no API can use Conxa to give AI assistants an **official, approved way** to use its product.

**Do software companies want that kind of control?** There are hints, but nothing direct:

- Amazon sued Perplexity to stop its AI shopping agent. A court blocked the agent, but that order was later cancelled ([GeekWire](https://www.geekwire.com/2026/03/10/judge-blocks-perplexitys-ai-bot-from-shopping-on-amazon-in-early-test-of-agentic-commerce/)).
- Slack (owned by Salesforce) tightened who can access its data.
- Cloudflare now sells a way for websites to **check which AI agents are visiting** ([Cloudflare](https://developers.cloudflare.com/ai-crawl-control/features/pay-per-crawl/use-pay-per-crawl-as-ai-owner/verify-ai-crawler/)).

None of these is about company-made skills directly.

---

## 4. Where Conxa is better because of how it is built, and what each rival still does well

"**Better by design**" here means the gap comes from Conxa's product design and business model, not just from today's features, which a rival could copy quickly.

| Rival | What the rival does well | How Conxa is different | Why a buyer should care | How strong is the evidence? |
|---|---|---|---|---|
| **AI agents that use a browser or computer** (Claude, OpenAI Operator, Google Gemini, Perplexity Comet) | One-off jobs, unfamiliar websites, no setup needed. On short tests they now match or beat people. | Conxa pays for AI **once, when building**, not on every run. Steps are fixed and can be reviewed. On a normal run, page text is not fed to an AI. | Cost and failure are **predictable** for repeated work that touches money. Less exposure to trick attacks. A clear answer for auditors. | The cost logic is sound. There are **no head-to-head tests**. The AI companies' own safety advice supports the point. |
| **Cloud browser services** (Browserbase, Steel, Hyperbrowser) | Cheap (about **$0.06–0.12 per browser hour**) and easy to scale up. | Runs in the user's **own** logged-in browser, on their own computer and internet address. Logins never go to an outside company. | No risk from an outside company holding your passwords. Fewer phone-code prompts and blocks. Shorter security reviews. | Based on how these services describe themselves, not on records of actual incidents. |
| **Hand-written scripts** (Playwright, Selenium) | Flexible, free, controlled by your own developers. | No coding needed: an operations person records the task. Repair, updates and shipping to customers are built in. | You no longer need an engineer on call every time a website changes. | Claims about upkeep cost come from vendors ("40–60% of testing time" is unconfirmed). The data on unreliable tests is from Google in 2016 ([Google](https://testing.googleblog.com/2016/05/)). |
| **Traditional RPA** (UiPath, Automation Anywhere, Power Automate, Blue Prism) | Mature controls, works on desktop and mainframe apps too, many connectors. UiPath's yearly recurring revenue is **$1.85 billion, up 11%** ([UiPath](https://ir.uipath.com/news/detail/431/mailto:PR@uipath.com)). | Can be shipped from one company to **many customers**. No stack of per-robot licences. Showing the task replaces weeks of analysis and building. Repair starts with free fixes. | Goes after the **70–75% of RPA cost that is not the licence**. Opens up a way of distributing automation that RPA licences seem not to allow. | The cost split is second-hand. The licence point is **our reading of older terms**. |
| **App-connecting tools** (Zapier, Make, Workato) | The fastest and most reliable option **wherever a good API exists**. Over 9,000 apps listed. | Works through the screen where there is no API. No charge per task. | Covers the long list of apps these tools do not reach, without a bill that grows with use. | The coverage gap is real (only 29% of apps are connected). How well screen-based skills compare with APIs is not measured. |
| **AI agents that save and replay their work** (Skyvern code caching, Amazon Nova Act) | **The closest rivals.** After one AI run, they can replay without calling the AI. Nova Act claims over **90% reliability** on early customer work ([AWS](https://aws.amazon.com/about-aws/whats-new/2025/12/build-automate-production-ui-workflows-nova-act)). | Conxa starts from a **person showing the task**, not from an AI's attempt, which might be wrong. First-line repair uses no AI. It can be shipped to other companies' customers. It runs locally. | If an AI makes a mistake and the mistake is saved, **it replays forever**. Shipping to customers and keeping logins local change who can sell and who can buy. | Differences come from reading their documents. Skyvern's reported savings ($0.11 to $0.04 per run, 279 to 120 seconds) come from a third party ([TinyFish](https://current.tinyfish.ai/tinyfish/issue/32/practitioners-corner/article/18634)). |
| **Personal recorders** (Bardeen, Axiom) | Cheap, personal, instant. | Made by a company, shipped to customers, repairs itself, and stops safely when unsure. | A **different kind of customer and trust**, not a cheaper copy of the same thing. | Little evidence. These tools have mostly moved toward AI agents or narrower uses. |
| **How-to guide makers** (Scribe, Tango) | Guides that people can read. | The recorded process **actually runs**. | Closes the gap between "what the guide says" and "what actually gets done". | Scribe's valuation shows market interest. It is not a direct comparison. |

### The two combinations that make the case

**Combination 1: the pieces only work well together.** Each rival has one piece. The cost and safety story needs all of them:

- **Fixed replay without shipping to customers** is just an internal tool.
- **Shipping to customers without running locally** means an outside company holds everyone's passwords.
- **Running locally without free repair** just moves the AI bill onto the customer's own AI subscription. (When Conxa does need Tier B repair, each round uses about 2,500–3,500 tokens of the customer's AI allowance. This is an estimate and has not been measured.)

**Combination 2: it works with many AI assistants, not just one.** Conxa installs its connection into **more than 20 AI assistants**, including Claude Desktop, Cursor, VS Code, Gemini CLI and Codex CLI (built). MCP, the "plug" standard it uses, is now looked after by a neutral body under the Linux Foundation. Second-hand reports say it has about **97 million downloads a month** ([IT Brief](https://itbrief.co.nz/story/anthropic-donates-mcp-to-new-agentic-ai-foundation); [ai2.work](https://ai2.work/blog/mcp-hits-97m-installs-the-infrastructure-layer-for-agentic-ai)).

So a buyer is **not betting on one AI company**. A product tied to a single AI assistant cannot offer that. *How well each of the 20+ assistants actually works with Conxa has not been tested.*

---

## 5. Where Conxa is NOT better, and what it must not claim

### Limit 1: there is no proof yet

- Conxa's own documents contain **no revenue, no customer count, no paid sign-ups, no won-or-lost deal data, and no measured success rate**.
- The profit figures (about **72–79%** net margin) are **projections**.
- The ideal-customer description is called **"a working hypothesis from the pilot"**.
- The sales-blockers document says plainly: **"Nobody has said yes."**
- Every number on reliability, repair success and repair cost is **an estimate** until real customer work is measured.
- Conxa's own to-do list says the first-run success rate on a new customer's account is **"the number the thesis lives on"**. That number has not been measured yet.

### Limit 2: what the product can reach today

- **Windows only.** Mac support is "roadmap, not capability".
- **The installer is not signed.** A "signature" is a digital stamp that tells Windows who made the software. Without one, Windows (SmartScreen) and company IT rules can block the install. The code to sign it exists, but the certificate (about **$200 a year**) has not been bought. Conxa's documents call this **the single hard sales blocker**.
- **Not covered at all:** regular desktop apps, mainframes, and remote-desktop setups like Citrix.
- **Sites that ask for a new phone code at every login** cannot run unattended. Keeping a login alive for unattended runs is called **the hardest unsolved problem**.
- **Identity is per company, not per person.** At run time, Conxa knows which company is running a skill, not which employee.
- **The skill packs themselves are not signed.** Only the update notices are.
- **No security certifications yet** (such as SOC 2 or ISO 27001), which big buyers often require.
- **Running on the customer's own computer is a bigger ask** than a simple website. Conxa's own plan admits it **"will lose deals to a competitor willing to run everything"** for the customer.

### Limit 3: self-repair has gaps

- **Free repair (Tier A) never switches to a different button.** So if a button is **renamed**, the step needs paid AI repair or it fails (EXEC-28).
- **Steps that need you to hover the mouse to reveal a menu** still fail on real replays (EXEC-29a).
- **Repair data is not yet sent to customer computers.** This is marked top priority (RECOV-2).
- **Long tasks of 30–40 steps have not been tested** (EXEC-10).
- **Paid repair (Tier B) is limited to two tries per step** and needs an AI assistant to be present.
- **"Learning across all customers" is not built.** Conxa notices when websites change, but nothing acts on that yet. Its own plan calls this idea **"a diagram, not a moat"** until it has about ten customers.
- **Handing a task to a person mid-run is fragile.** If the program restarts, the handover is lost, and it cannot resume inside a repeating loop (EXEC-22).
- **The full plan for unexpected pop-ups and screens is only a proposal**, not built.

Conxa's own advice is **not to claim** any of these:

- "fully self-healing",
- "works on every platform",
- "runs unattended at large scale",
- "enterprise-ready security".

### Limit 4: competition and timing

- Conxa's own plan admits that **"a strong team of ten could build a recorder-to-script compiler in six months"**, and that big AI companies are active in this space.
- **Skyvern and Amazon Nova Act are already moving toward "save, then replay".**
- Gartner predicts that **over 40% of AI-agent projects will be cancelled by 2027**, and says many "AI agent" products are just old automation with a new label ([Gartner](https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027)). This cuts both ways. It shows buyers want **controlled, predictable** automation. It also shows buyers are **suspicious of new names**.
- **AI agents keep improving.** Third-party trackers say they passed the human score on a standard test in 2026.
- **RPA is not dying.** UiPath grew **13%** in its 2026 financial year. All the big RPA companies now add AI-agent features on top of their fixed robots, which **narrows Conxa's gap**.
- **Where a good API exists, app-connecting tools win.** For one-off jobs and unfamiliar websites, AI agents win.

### Limit 5: risks in the business model itself

- Conxa charges for **building**, but the customer feels the value when skills **run**. A company that builds a lot but uses or ships little may **not renew**.
- Two features **do cost Conxa money per use**: the chat window in Conxa Execute and the "Human Edit" repair. This weakens the "no cost per run" story for those parts.
- The video-understanding step costs about **$0.03–0.05 per minute of recording**. This is **not included** in the cost model's totals.

---

## 6. The strongest way to describe Conxa

### The core message

Not "AI automation". Instead:

> **"Your vendor-approved, checkable procedure. Installed once. Run as often as you like. You pay for how many people it reaches, not how many times it runs."**

### The message for each type of buyer

- **To a software company:** "Ship setup, data moves and repeat tasks as skills that run inside your customers' own AI assistants. Cut setup costs and support tickets. And give AI agents an official path into your product, instead of letting them scrape it." *(Conxa's plan calls this the highest-leverage channel. It is promising but not yet proven.)*
- **To a company's operations or finance team:** "Automate the repeating browser task that crosses several systems and has no API. You need no developer and pay no per-run fee. Keep passwords on your own computers. Keep a record of exactly which version ran."
- **To a consultancy or outsourcing firm:** "Build a process once and install it for every client. There is no per-robot licence to pass on."
- **To risk and compliance officers:** "The AI writes the program once. A person reviews it. The run never improvises." This matches what auditors say they want from AI.

### Words to use, and words to avoid

**Avoid** words like "faster" or "self-healing" on their own. They mean nothing to a buyer until you say what they save. **Use** phrases that link to the buyer's costs and risks:

- "Cuts the repair crew."
- "No bill that grows with success."
- "The leaver's routine survives them."
- "No password leaves your machine."
- "It can't press the wrong 'confirm' button. The steps are fixed, and it stops when two buttons look alike."

### Position Conxa as a partner to AI assistants, not a rival

Conxa's own plan puts it simply: **the AI assistant decides *what* to do, and the skill is *how* to do it.** So Conxa sells to people **using** Claude, Cursor and similar assistants, not against them.

**Being honest about limits helps sell.** Saying "repair has limits, and it stops safely when it is unsure" builds trust. Many buyers have learned to distrust over-hyped "AI agent" claims.

---

## 7. "Killer" uses a customer understands in one sentence

1. **Insurance quotes across many insurer websites.** "Record one quote once, then repeat it across many insurer websites from a chat, and the broker's logins never leave their laptop."
   - The benefit is hours saved on retyping, and fewer typing mistakes.
   - Any hours figure must be **measured in a pilot**. The only survey we found on hours per week is from 2007.

2. **Prior authorisations and claim checks for a clinic or billing company.**
   - It is a repeatable website process with a known cost of about **$11–13 per request by hand** (second-hand).
   - A safety guard means a submission is **never made on a guess**.

3. **Customer setup shipped by a software company.**
   - The software company records "set up a new customer account" once.
   - Every customer gets it as a button in their AI assistant.
   - The software company can see which skills run and which fail on its dashboard. This is built, though the money-saved figures on the dashboard are editable estimates.

4. **Month-end evidence for the finance team.**
   - A version-tracked skill downloads the same bank, accounting-system and website reports every month.
   - The run record serves as proof for the auditor.

5. **The retiring expert.**
   - Record a long-serving employee's website routines in their final weeks, for example in a utility, an accounting firm or a government back office.
   - Their know-how becomes a company asset instead of being lost when they retire.

**How to start:** prove each of these first with **supervised tasks that can be undone**. Conxa's own launch plan recommends exactly this for its first ten partner customers. *(That plan dates from around July 2026 and was not re-checked for this report.)*

---

## The bottom line

### The direct answer

**What can Conxa uniquely do?** It lets a software company, a consultancy or an operations team **show a browser task once**. That task can then **run on many people's computers at no extra cost per run**. Logins stay on each computer. The first line of repair costs nothing. And the steps are **fixed, checkable, and stop safely** when something is unclear.

**Why does that matter for money?** It turns automation from an **ongoing cost** into a **one-time building cost**. Today you pay for AI on every run, for every task, for every robot licence, and for staff to fix things every time a website changes. Conxa replaces most of that with the cost of building the skill once.

**Why does it matter strategically?** It makes automation something you can **ship to others and audit**. Each rival hits a wall somewhere:

- **AI agents** are hard to reproduce exactly and are open to trick attacks.
- **Cloud browsers** have to hold your passwords.
- **RPA licences** seem not to allow shipping robots to your customers.
- **App-connecting tools** stop where there is no API.

**Why can't others just copy it?** Rivals have pieces:

- AI agents are **flexible**.
- Skyvern and Nova Act can **save and replay**.
- RPA is **predictable** and has good controls.
- App-connecting tools are **reliable where APIs exist**.

But no rival we found combines all four of these: **a person showing the task, shipping it from one company to its customers, running it locally, and pricing that does not depend on how often it runs.** Several of these are part of the **business model**, and a rival cannot simply add them as a feature.

### But this is still a theory to prove

Conxa has **no customer, revenue or measured-reliability evidence yet**. It is **Windows-only**, its installer is **not signed**, and its self-repair **has gaps**.

**What to do next:** prove the idea where it is sharpest and easiest to measure. Pick **one software company or one insurance broker** as a partner. Run **a few supervised, undoable tasks on websites with no API**. Measure these four things, and compare them against an AI agent and an RPA robot doing the same work:

1. How often the task works **the first time**.
2. How often **automatic repair** succeeds.
3. How many **hours it takes to build** the skill.
4. How many **hours are spent fixing** it later.

**Until those numbers exist, the safe claim is the narrower one:**

> For repeated browser tasks that cross several systems, have no API, and must be shipped to others, audited, or kept off outside companies' computers, Conxa's "build once, run locally" approach has a cost and risk structure that pay-per-run AI agents and pay-per-robot RPA do not have.

Today, Conxa's real advantage is **that structure**, not any single clever piece of its technology.
