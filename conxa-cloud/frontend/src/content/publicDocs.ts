import { SUPPORT_EMAIL } from '@/lib/contact'

export type PublicDocCategoryId = 'product' | 'trust' | 'legal' | 'billing' | 'support'

export type PublicDocBlock =
  | {
      type: 'paragraphs'
      items: readonly string[]
    }
  | {
      type: 'bullets' | 'numbered'
      items: readonly string[]
    }
  | {
      type: 'callout'
      title: string
      body: string
    }
  | {
      type: 'table'
      columns: readonly string[]
      rows: readonly (readonly string[])[]
    }
  | {
      type: 'links'
      items: readonly {
        label: string
        href: string
        description: string
      }[]
    }

export type PublicDocSection = {
  id: string
  title: string
  intro?: string
  blocks: readonly PublicDocBlock[]
}

export type PublicDocPage = {
  slug: string
  category: PublicDocCategoryId
  title: string
  eyebrow: string
  description: string
  lastUpdated: string
  readingTime: string
  summary: readonly string[]
  sections: readonly PublicDocSection[]
  relatedSlugs?: readonly string[]
  references?: readonly {
    label: string
    href: string
  }[]
}

export type PublicDocCategory = {
  id: PublicDocCategoryId
  title: string
  description: string
  slugs: readonly string[]
}

export { SUPPORT_EMAIL }
export const SUPPORT_PHONE_DISPLAY =
  process.env.NEXT_PUBLIC_SALES_PHONE_DISPLAY ?? '+91 9970257247'
export const SUPPORT_PHONE_TEL =
  process.env.NEXT_PUBLIC_SALES_PHONE_TEL ?? SUPPORT_PHONE_DISPLAY.replace(/[^\d+]/g, '')

export const PUBLIC_DOCS_LAST_MODIFIED = '2026-08-29'
const LAST_UPDATED = 'June 11, 2026'
const TERMS_LAST_UPDATED = 'August 29, 2026'

export const publicDocs = [
  {
    slug: 'platform',
    category: 'product',
    title: 'Platform Overview',
    eyebrow: 'Product docs',
    description:
      'How Conxa turns recorded software workflows into local MCP skills that AI tools can execute reliably.',
    lastUpdated: LAST_UPDATED,
    readingTime: '8 min read',
    summary: [
      'Conxa has three surfaces: Build Studio for recording and compiling, Conxa Cloud for coordination, and the customer runtime for local execution.',
      'The cloud hosts packages, installers, billing, team management, LLM proxying, and telemetry. It does not operate customer applications.',
      'End users run skills through a local MCP runtime that syncs packaged workflow definitions and executes them on their own machine.',
    ],
    sections: [
      {
        id: 'what-conxa-does',
        title: 'What Conxa does',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Conxa turns a human-performed browser workflow into a precompiled skill package. A product team or operations team records the workflow once, reviews the captured steps, compiles the workflow into structured execution data, and distributes it to end users through a branded installer.',
              'The finished skill is exposed to AI tools through the Model Context Protocol (MCP). Instead of asking an AI model to rediscover a product interface on every run, Conxa gives the local runtime a stable execution artifact with selectors, recovery metadata, validation expectations, and update information.',
            ],
          },
          {
            type: 'callout',
            title: 'Execution stays local',
            body: 'Conxa Cloud coordinates packages, updates, billing, and telemetry. Workflow execution itself happens on the end user machine through the local runtime.',
          },
        ],
      },
      {
        id: 'three-systems',
        title: 'The three-system model',
        intro:
          'Conxa is deliberately split so each system has a narrow responsibility.',
        blocks: [
          {
            type: 'table',
            columns: ['System', 'Who uses it', 'What it does'],
            rows: [
              [
                'Build Studio',
                'SaaS vendor or enterprise builder',
                'Records browser workflows, captures UI context, reviews steps, compiles skill packages, and builds installers locally.',
              ],
              [
                'Conxa Cloud',
                'Workspace admins and builders',
                'Hosts published packages and installers, manages billing and teams, proxies compile-time LLM calls, and stores operational telemetry.',
              ],
              [
                'Conxa Runtime',
                'End customer or internal operator',
                'Runs as a local MCP server, syncs skill packages, launches Playwright, executes steps, and reports execution telemetry.',
              ],
            ],
          },
          {
            type: 'paragraphs',
            items: [
              'This separation matters for privacy and reliability. Build-time artifacts can be improved with AI assistance, while runtime sessions and target-site credentials remain local to the machine that executes the workflow.',
            ],
          },
        ],
      },
      {
        id: 'data-flow',
        title: 'Typical data flow',
        blocks: [
          {
            type: 'numbered',
            items: [
              'A builder signs in and creates a workflow in the Build Studio.',
              'The builder records the workflow in a controlled browser session and reviews the captured steps.',
              'The Build Studio compiles the workflow locally into a skill package, using Conxa Cloud only for authorized compile-time LLM proxy calls when needed.',
              'The builder publishes the data-only package and a branded installer to Conxa Cloud.',
              'The end user installs the runtime, which registers with Claude Desktop or another MCP-capable client.',
              'The runtime syncs skill packages from Conxa Cloud, executes skills locally, and sends compact telemetry back for observability.',
            ],
          },
        ],
      },
      {
        id: 'what-cloud-does-not-do',
        title: 'What the cloud does not do',
        blocks: [
          {
            type: 'bullets',
            items: [
              'It does not run customer workflows inside Conxa infrastructure.',
              'It does not need the end user target-application passwords to execute a skill.',
              'It does not receive local browser session files used by the runtime.',
              'It does not replace the customer application API or require the SaaS vendor to build a native integration.',
            ],
          },
          {
            type: 'paragraphs',
            items: [
              'The cloud is a coordination and distribution layer. That boundary is a core product property, not an implementation detail.',
            ],
          },
        ],
      },
      {
        id: 'who-it-is-for',
        title: 'Who uses Conxa',
        blocks: [
          {
            type: 'bullets',
            items: [
              'SaaS product teams that want to make their product usable by Claude or other AI tools without building a new integration for every workflow.',
              'Enterprise operations teams that want repeatable local execution for browser-based internal work.',
              'Automation consultants and internal IT teams that package operational workflows for non-technical users.',
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['claude-automation', 'build-studio', 'cloud', 'runtime'],
  },
  {
    slug: 'claude-automation',
    category: 'product',
    title: 'Claude Automation With CONXA',
    eyebrow: 'Product docs',
    description:
      'How Conxa packages browser workflow automation as local MCP skills that Claude Desktop can invoke safely.',
    lastUpdated: LAST_UPDATED,
    readingTime: '6 min read',
    summary: [
      'Claude automation with Conxa starts from a reviewed human workflow, not from an AI model improvising browser clicks at runtime.',
      'Claude Desktop automation runs through the installed Conxa runtime, which registers as a local MCP server and executes prepared skills on the end user machine.',
      'MCP automation still depends on authorized access, reviewed workflows, local browser session state, and package boundaries that exclude target-site credentials.',
    ],
    sections: [
      {
        id: 'how-it-works',
        title: 'How Claude automation works',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Claude automation with Conxa starts when a builder records a browser workflow once in Build Studio. Conxa compiles that recording into a data-only skill package with step intent, selectors, validation expectations, and recovery metadata.',
              'The installed runtime registers as an MCP server for Claude Desktop or another MCP-capable client. When Claude invokes a skill, the runtime executes the prepared browser workflow automation locally on the user machine.',
            ],
          },
        ],
      },
      {
        id: 'why-mcp',
        title: 'Why MCP is the boundary',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'MCP automation gives Claude Desktop a defined tool surface instead of asking the model to rediscover every screen. The runtime can expose available skills, input schemas, execution status, cancellation, package refresh, and permitted skill metadata.',
              'This keeps the AI client focused on selecting the right skill and providing inputs. The local Conxa runtime handles browser execution, deterministic recovery, assertions, and telemetry.',
            ],
          },
        ],
      },
      {
        id: 'local-runtime',
        title: 'Local AI automation runtime',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'The runtime is the local AI automation runtime for Conxa skills. It syncs packaged workflow definitions, opens the local browser automation context, uses local session state where available, and reports compact operational telemetry.',
              'Conxa Cloud coordinates packages, installers, billing, updates, compile-time LLM proxying, and telemetry. It does not operate the customer application for the end user.',
            ],
          },
          {
            type: 'callout',
            title: 'Execution stays on the user machine',
            body: 'Claude Desktop can ask for a skill to run, but the browser session and target-application credentials remain local runtime state rather than cloud execution state.',
          },
        ],
      },
      {
        id: 'safe-use',
        title: 'Authorized workflow automation',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Build skills only for products, accounts, data, and workflows the user is authorized to operate.',
              'Review compiled workflows before distribution so end users understand what the skill will do.',
              'Do not package browser session files, passwords, cookies, credential stealers, or intentionally harmful automation.',
              'Use Conxa for repeatable operational workflows, not as a way to bypass target-application permissions or policy.',
            ],
          },
        ],
      },
      {
        id: 'who-benefits',
        title: 'Who this is for',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'SaaS vendors can make common product workflows usable from Claude Desktop without building a custom native API integration for every workflow. Internal operations teams can package browser workflow automation for repeatable local execution by authorized users.',
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['platform', 'runtime', 'security', 'acceptable-use'],
  },
  {
    slug: 'build-studio',
    category: 'product',
    title: 'Build Studio',
    eyebrow: 'Product docs',
    description:
      'How builders record, edit, compile, test, and package workflows before publishing them to Conxa Cloud.',
    lastUpdated: LAST_UPDATED,
    readingTime: '9 min read',
    summary: [
      'Build Studio is the local authoring environment for workflow recording and compilation.',
      'Recording, auth capture, compilation, package building, and installer generation happen on the builder machine.',
      'Cloud services may be contacted for login, entitlement checks, package upload, billing, telemetry, and compile-time LLM proxy calls.',
    ],
    sections: [
      {
        id: 'authoring-loop',
        title: 'The authoring loop',
        blocks: [
          {
            type: 'numbered',
            items: [
              'Create a workflow with a name, target product, and starting URL.',
              'Record an authentication session when the workflow requires a signed-in target application.',
              'Record the workflow by performing the task once in the browser.',
              'Review captured steps, screenshots, inputs, and validation expectations.',
              'Compile the workflow into a skill package with selectors, intent, assertions, and recovery metadata.',
              'Test the compiled skill locally, adjust if needed, then build a skill package or installer.',
            ],
          },
        ],
      },
      {
        id: 'recording',
        title: 'Recording workflow actions',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Build Studio launches a controlled browser session and injects a capture bridge into pages and frames. The bridge records user actions such as clicks, typing, selection changes, navigation, uploads, scrolling, tabs, popups, and frame transitions.',
              'The recorder captures more than coordinates. It keeps element signals, surrounding context, URL information, iframe chain information, timestamps, and visual references where available. This gives the compiler enough context to produce a workflow that can survive normal interface changes.',
            ],
          },
          {
            type: 'callout',
            title: 'Auth capture is local',
            body: 'Target-site browser session files are local runtime state. They are not supposed to be included in build output and are not uploaded as part of a skill package.',
          },
        ],
      },
      {
        id: 'human-edit',
        title: 'Review and Human Edit',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'After recording, builders can inspect the workflow step by step. Human Edit is intended for practical correction: rename steps, adjust inputs, confirm screenshots, tune validation expectations, remove noise, and repair selector or semantic interpretation errors before packaging.',
              'Deterministic edits are separate from LLM-assisted repair. Product teams can keep cleaning a workflow even when LLM-assisted pools or monthly compile credits are exhausted, depending on the plan limits enforced by the workspace.',
            ],
          },
        ],
      },
      {
        id: 'compile',
        title: 'Compilation',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Compilation turns recorded events into a structured skill package. The compiler normalizes actions, removes duplicate noise, enriches events with context, selects stable element identities, creates assertions, and builds a recovery policy for the runtime.',
              'When AI assistance is enabled, compile-time prompts may include workflow context, selected element information, screenshots, or DOM-derived signals needed to understand the workflow. The goal is to create execution metadata, not to run the customer task in the cloud.',
            ],
          },
        ],
      },
      {
        id: 'build-output',
        title: 'Build output',
        blocks: [
          {
            type: 'table',
            columns: ['Output', 'Purpose', 'Sensitive data policy'],
            rows: [
              [
                'Skill package',
                'Data-only workflow artifact used by the runtime.',
                'Should not contain browser credentials or Playwright storage state.',
              ],
              [
                'Skill package archive',
                'Package used for inspection, distribution, and installer assembly.',
                'Auth files are excluded from build output.',
              ],
              [
                'Installer',
                'Customer-facing executable that installs the runtime and sync configuration.',
                'Contains distribution metadata and tokens required for package sync, not target-site passwords.',
              ],
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['platform', 'cloud', 'runtime', 'billing', 'build-studio-guide'],
  },
  {
    slug: 'build-studio-guide',
    category: 'product',
    title: 'Build Studio: Step-By-Step Guide',
    eyebrow: 'Product docs',
    description:
      'A plain-language walkthrough of installing Build Studio, recording an automation, reviewing it, and publishing it to your team, plus troubleshooting and FAQ.',
    lastUpdated: 'August 22, 2026',
    readingTime: '11 min read',
    summary: [
      'Recording, compiling, reviewing, and publishing an automation is one continuous flow inside a single group page.',
      'Apps are connected once per group, not once per automation, and every recording starts already signed in.',
      'Publishing prepares a release; a separate, deliberate step in Conxa Cloud is what actually sends it to your team.',
    ],
    sections: [
      {
        id: 'what-it-does',
        title: 'What this guide covers',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'This is a practical, step-by-step companion to the Build Studio overview above. It walks through installing the app, recording a task, fixing anything that looks off, and getting it in front of your team, plus what to do when something goes wrong.',
              'It is written for anyone using Build Studio day to day, not just engineers. Technical setup details are called out separately where they matter.',
            ],
          },
        ],
      },
      {
        id: 'install',
        title: 'Installing Build Studio',
        blocks: [
          {
            type: 'numbered',
            items: [
              'Sign up at the Conxa dashboard and download the Build Studio installer for Windows.',
              'Run the installer. The first launch downloads a few one-time dependencies, including a bundled browser engine, which can take a few minutes on a slow connection.',
              'Sign in when prompted. This opens your normal web browser to a secure sign-in page; Build Studio picks up the result automatically once you finish there.',
            ],
          },
          {
            type: 'callout',
            title: 'On a locked-down network',
            body: 'If downloads stall, the setup screen shows the exact web addresses it is trying to reach. Give those to your IT team to allow through the firewall or proxy.',
          },
        ],
      },
      {
        id: 'connecting-apps',
        title: "Connecting the apps you'll automate",
        blocks: [
          {
            type: 'paragraphs',
            items: [
              "Automations are organized into groups, like folders for a business area such as Sales or Finance. Before recording the first automation in a group, connect the apps (websites) it uses, once per group rather than once per automation.",
            ],
          },
          {
            type: 'numbered',
            items: [
              "Open a group and find the Applications panel.",
              "Add an app: give it a name, its login page, and the page you land on once signed in successfully.",
              "Click Connect next to the app. A browser window opens to its login page, sign in normally.",
              "Once you land on the page you specified as signed in, the window closes on its own and the app shows as connected.",
            ],
          },
          {
            type: 'callout',
            title: 'Saved logins stay local',
            body: 'Every automation recorded in that group starts already signed in to every connected app. Saved sessions are stored locally and encrypted on your machine, and are never included in anything you publish or in any installer you build.',
          },
        ],
      },
      {
        id: 'recording',
        title: 'Recording an automation',
        blocks: [
          {
            type: 'numbered',
            items: [
              'In a group, create a new automation with a name and a starting web address.',
              'Start the recording. A browser window opens, already signed in to the group\'s connected apps.',
              'Do the task exactly as you normally would: click, type, select menus, fill forms, switch tabs, upload files.',
              'Close the recording window and save it once you are done.',
            ],
          },
          {
            type: 'bullets',
            items: [
              'Multiple tabs: opening a new tab mid-task, or switching between tabs, is captured in order and replayed the same way later.',
              "File uploads: Build Studio remembers the file's name, not its location on your computer, so anyone running the automation later supplies their own copy.",
              'Optional pop-ups: a cookie banner or confirm dialog you interacted with is flagged during review as a candidate to treat as optional, rather than guessed at automatically.',
            ],
          },
        ],
      },
      {
        id: 'compile',
        title: 'Compiling',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Compiling turns a saved recording into a structured automation, typically in under a minute to a few minutes depending on the length of the recording, with a progress view while it works.',
              'A first compile uses one of your compile credits. Re-compiling an automation you have already compiled draws from a separate, smaller AI Usage Credits pool instead. Both meters are visible at the top of the screen.',
            ],
          },
        ],
      },
      {
        id: 'reviewing',
        title: 'Reviewing in Human Edit',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'After compiling, open the automation for review. This is where every step gets checked and fixed before it goes live.',
            ],
          },
          {
            type: 'bullets',
            items: [
              'Edit what a step does, or which input field it fills.',
              'Reorder, insert, or delete steps.',
              'Turn a value typed during recording into a variable the AI assistant asks for each time, instead of a fixed value.',
              'Fix a step pointing at the wrong element: change how it is found in the details of the step, or re-record from that step, and adjust what should be checked afterward to know it worked.',
            ],
          },
          {
            type: 'paragraphs',
            items: [
              'A banner at the top honestly reports whether the whole automation looks solid or has steps that need a second look, with links straight to any flagged step. Approving the automation packages it automatically if every automation in the group is compiled and approved, with no separate build step to remember.',
            ],
          },
        ],
      },
      {
        id: 'testing',
        title: 'Testing',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Before publishing, run the automation once against the local Conxa runtime to watch it complete, or see exactly where it fails, before your team relies on it.',
            ],
          },
        ],
      },
      {
        id: 'publishing',
        title: 'Publishing and releasing',
        blocks: [
          {
            type: 'numbered',
            items: [
              'Open Publish Skill Package and pick the automation being released.',
              'Review the plain-language summary of what is changing compared to the last published version.',
              'Enter a version number and short release notes, then publish.',
            ],
          },
          {
            type: 'callout',
            title: 'Publishing is not the same as releasing',
            body: 'Publishing prepares an immutable, versioned release. Making it actually go live to your team is a separate, deliberate action taken by an admin in the Conxa Cloud dashboard, so a publish never quietly reaches everyone without a second confirmation. Once released, everyone\'s installed runtime picks up the update automatically, typically within a minute, with nothing to reinstall.',
          },
        ],
      },
      {
        id: 'installer',
        title: 'Building an installer',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Most routine updates never need this: publishing is enough for anyone who already has Conxa installed. Building an installer is only needed to distribute Conxa to a machine for the first time, and requires that at least one automation has already been published.',
            ],
          },
        ],
      },
      {
        id: 'troubleshooting',
        title: 'Troubleshooting',
        blocks: [
          {
            type: 'table',
            columns: ['Problem', 'What to do'],
            rows: [
              [
                'Setup or a download got stuck.',
                'Re-launch Build Studio; setup resumes and skips anything already installed. On a corporate network, allow the addresses shown on the setup screen through your firewall.',
              ],
              [
                'Signed in, but the app did not notice.',
                'The sign-in window can take a few minutes to time out. Close and reopen Build Studio, then try Sign In again.',
              ],
              [
                'An app shows as expired or not connected, but you are logged in on the website itself.',
                "Build Studio keeps its own saved login separate from your regular browser. Reconnect (or Connect) that app's row and sign in inside the window it opens.",
              ],
              [
                'Recording will not start.',
                'Recording is blocked until every app the automation needs is connected in that group. Check the Applications panel for anything still needing sign-in.',
              ],
              [
                'A step keeps failing when testing.',
                "Open the step's reliability details in review; it usually shows the recorded element could not be found the same way anymore. Fix how it is found in the step's details, or re-record from that step, then re-test.",
              ],
              [
                'Out of compile credits or AI Usage Credits.',
                'Check the usage meters at the top of the screen for what is left and when they reset. Editing steps directly does not use quota; only AI-assisted actions like re-generating a selector do.',
              ],
              [
                'Publish failed.',
                'Build Studio states plainly what did not upload and why, and nothing partial is marked as published. Fix the issue shown and publish again; it is safe to retry.',
              ],
              [
                'Installer build says a skill pack has not been published yet.',
                'Publish an automation first; the installer packages an already-published release rather than creating one.',
              ],
            ],
          },
        ],
      },
      {
        id: 'faq-access',
        title: 'Does Build Studio need special access to the websites I automate?',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'No. It works the same way a person does, by opening a real browser and interacting with the page. There is nothing to configure on the target website itself.',
            ],
          },
        ],
      },
      {
        id: 'faq-logins',
        title: 'Where do the recorded logins get stored?',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              "Locally on your computer, encrypted. They are never uploaded, published, or included in anything distributed to customers or teammates.",
            ],
          },
        ],
      },
      {
        id: 'faq-redesign',
        title: 'What happens if the website changes after I publish an automation?',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Small changes, like a moved button or a renamed label, are adapted to automatically. A bigger redesign may need a human to look at it again; that is flagged the same way a failed run is, rather than failing silently and repeatedly.',
            ],
          },
        ],
      },
      {
        id: 'faq-rollback',
        title: 'I released a bad version, can I undo it?',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              "Yes. From that automation's release history in the Conxa Cloud dashboard, an admin can roll back to any previously released version with no rebuild needed, and it only affects that one automation.",
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['build-studio', 'platform', 'runtime', 'support'],
  },
  {
    slug: 'cloud',
    category: 'product',
    title: 'Conxa Cloud',
    eyebrow: 'Product docs',
    description:
      'What the cloud dashboard and backend coordinate for published skill packages, billing, teams, telemetry, and updates.',
    lastUpdated: LAST_UPDATED,
    readingTime: '8 min read',
    summary: [
      'Conxa Cloud is the hosted coordination layer for package hosting, installer distribution, billing, teams, LLM proxying, and telemetry.',
      'It is not a remote workflow execution environment.',
      'Cloud routes are authenticated where required and public only where distribution or telemetry ingest requires it.',
    ],
    sections: [
      {
        id: 'cloud-role',
        title: 'Cloud role',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Conxa Cloud gives workspaces a central place to manage published skill packages, billing, team access, hosted installers, runtime updates, and operational telemetry. It exists so builders and customers do not need to manually exchange skill files or runtime updates.',
              'The cloud backend exposes API routes under the versioned API path used by the frontend and runtime. The frontend dashboard uses those APIs through the existing proxy layer and Clerk authentication where required.',
            ],
          },
        ],
      },
      {
        id: 'hosted-assets',
        title: 'Hosted packages and installers',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'A workspace publishes one skill package per company, bundling every signed-off workflow for that company into a single hosted installer. There is no limit on how many distinct company skill packages a workspace can publish — what the plan controls is who the installer can reach: an internal-only build on Free and Starter, or a distributable build on Pro and Enterprise, white-labeled on Enterprise.',
              'Uploading a new version for the same company slug is treated as an update to that same hosted release.',
            ],
          },
        ],
      },
      {
        id: 'llm-proxy',
        title: 'Compile-time LLM proxy',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Build Studio can use the cloud as an authenticated LLM proxy during compilation and Human Edit repair. The proxy centralizes provider keys, entitlement checks, and metering so individual builders do not need to configure every provider locally.',
              'LLM usage is for build-time interpretation and repair. Runtime step execution is not moved into the cloud just because compile-time AI assistance is available.',
            ],
          },
        ],
      },
      {
        id: 'telemetry',
        title: 'Telemetry and observability',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Runtime telemetry helps builders understand whether installed skills are working. Telemetry can include run status, timestamps, skill identifiers, recovery attempts, duration, and compact failure information.',
              'Telemetry is not a substitute for full session replay. Runtime step content and local browser session state remain local unless a customer explicitly shares logs or files for support.',
            ],
          },
        ],
      },
      {
        id: 'cloud-boundaries',
        title: 'Cloud boundaries',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Cloud APIs coordinate package distribution, update checks, tracking, billing, and team access.',
              'The dashboard can show workspace usage and execution health but does not operate the target product for the customer.',
              'The cloud may store skill package content in plain text because packages are data-only workflow artifacts; sensitive auth files are excluded from package output.',
              'Private enterprise distribution, contractual SLAs, or custom retention terms should be handled through a written order form or enterprise agreement.',
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['platform', 'billing', 'security', 'data-processing'],
  },
  {
    slug: 'runtime',
    category: 'product',
    title: 'Runtime And MCP Execution',
    eyebrow: 'Product docs',
    description:
      'How the installed runtime exposes local browser workflow automation to Claude Desktop or another MCP client and executes skills locally.',
    lastUpdated: LAST_UPDATED,
    readingTime: '8 min read',
    summary: [
      'The runtime is a local MCP server installed on the customer machine.',
      'It syncs data-only skill packages, launches browser automation locally, and reports compact telemetry.',
      'Runtime browser sessions and target-application credentials are stored locally and are not part of published skill packages.',
    ],
    sections: [
      {
        id: 'mcp-server',
        title: 'Local MCP server',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'The runtime installs on the end user machine and registers as an MCP server. MCP clients can list available skills, request input schemas, execute a skill, inspect execution status, cancel execution, refresh skill packages, and read skill metadata where permitted.',
              'Because it runs locally, the runtime can operate the user browser session without sending the live UI state to a remote execution service.',
            ],
          },
        ],
      },
      {
        id: 'skill-sync',
        title: 'Skill sync',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'The runtime checks Conxa Cloud for skill package updates and downloads package deltas or current package content as configured. Files are verified and written atomically so a failed update does not leave a half-written package in place.',
              'The sync token packaged for a company should be treated as distribution-sensitive. It allows the runtime to retrieve data-only skill packages for that company, but it is not the same thing as the end user target-site session.',
            ],
          },
        ],
      },
      {
        id: 'execution',
        title: 'Execution loop',
        blocks: [
          {
            type: 'numbered',
            items: [
              'The MCP client asks the runtime to execute a named skill with validated inputs.',
              'The runtime loads the skill package and starts or reuses a browser context.',
              'Each workflow step resolves its target element using compiled selectors, accessibility signals, visual context, and recovery policy as needed.',
              'Assertions verify that the step produced the expected state.',
              'Telemetry is batched and sent to Conxa Cloud so builders can see health and failure trends.',
            ],
          },
        ],
      },
      {
        id: 'recovery',
        title: 'Recovery behavior',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Conxa skills are designed to recover from routine UI drift. The runtime starts with deterministic recovery paths and only escalates to more expensive repair strategies where the compiled policy allows it.',
              'Some steps are intentionally not recoverable. Frame-enter and frame-exit markers, for example, describe navigation boundaries and should not be treated as interactable elements.',
            ],
          },
        ],
      },
      {
        id: 'local-storage',
        title: 'Runtime storage',
        blocks: [
          {
            type: 'table',
            columns: ['Data', 'Typical location', 'Purpose'],
            rows: [
              [
                'Skill packages',
                'Local Conxa skill-pack directory',
                'Defines available MCP tools and execution steps.',
              ],
              [
                'Runtime token',
                'OS keychain where supported',
                'Authenticates package sync for the installed company runtime.',
              ],
              [
                'Browser session',
                'Local encrypted runtime cache',
                'Keeps target-site session state on the customer machine.',
              ],
              [
                'Execution logs',
                'Local runtime logs and cloud telemetry summary',
                'Supports debugging and operational health checks.',
              ],
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['claude-automation', 'platform', 'security', 'privacy'],
  },
  {
    slug: 'security',
    category: 'trust',
    title: 'Security',
    eyebrow: 'Trust and security',
    description:
      'Security boundaries, local execution guarantees, auth handling, token storage, and practical operational limits.',
    lastUpdated: LAST_UPDATED,
    readingTime: '10 min read',
    summary: [
      'Conxa separates build-time AI assistance, cloud coordination, and local runtime execution.',
      'Target-site browser session state is local and excluded from build output.',
      'Workspace access uses Clerk authentication, runtime package sync uses runtime tokens, and customer browser sessions are encrypted locally where supported.',
    ],
    sections: [
      {
        id: 'security-principles',
        title: 'Security principles',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Keep workflow execution local to the end user machine.',
              'Exclude auth files and browser storage state from packaged skill output.',
              'Use the cloud for coordination and telemetry, not remote operation of customer applications.',
              'Prefer deterministic recovery before LLM-assisted repair.',
              'Make operational limits explicit instead of presenting incomplete controls as certifications.',
            ],
          },
        ],
      },
      {
        id: 'identity-and-access',
        title: 'Identity and access',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'The cloud dashboard uses Clerk-based authentication for workspace access. API requests that require a signed-in user are protected through the authenticated frontend and backend route model.',
              'The Build Studio uses a local sign-in flow for builders. Runtime package sync uses tokens provisioned for the installed company runtime. These are separate credentials with separate scopes.',
            ],
          },
        ],
      },
      {
        id: 'local-session-security',
        title: 'Local browser sessions',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Browser session state for target applications is treated as sensitive. Build Studio auth sessions and runtime browser sessions are local state, not published package content.',
              'Runtime browser sessions are encrypted at rest on the customer machine using local key material where the runtime environment supports it. Customers should still control endpoint security, OS account access, disk encryption, and browser profile hygiene.',
            ],
          },
          {
            type: 'callout',
            title: 'Auth file exclusion',
            body: 'The build pipeline is expected to reject build inputs that contain auth files. Published skill packages should contain workflow data, not target-site credentials.',
          },
        ],
      },
      {
        id: 'data-protection',
        title: 'Data protection controls',
        blocks: [
          {
            type: 'table',
            columns: ['Control', 'Current behavior'],
            rows: [
              [
                'Transport security',
                'Cloud communication should use HTTPS in production deployments.',
              ],
              [
                'Workspace auth',
                'Dashboard access is tied to authenticated Clerk users and workspace context.',
              ],
              [
                'Runtime session storage',
                'Target-site browser sessions stay local and are encrypted at rest where supported by the runtime.',
              ],
              [
                'Package integrity',
                'Runtime sync verifies package content and writes updates atomically.',
              ],
              [
                'Telemetry scope',
                'Telemetry is compact operational metadata rather than full runtime browser replay.',
              ],
            ],
          },
        ],
      },
      {
        id: 'operational-limits',
        title: 'Known operational limits',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Hosted installer URLs and company sync tokens should be treated as distribution-sensitive.',
              'Enterprise private distribution, device registration, custom retention, and contractual security terms should be captured in a written enterprise agreement.',
              'Conxa does not claim SOC 2, ISO 27001, HIPAA, PCI DSS, or similar certification on these public docs pages unless a signed compliance artifact says otherwise.',
              'Customers remain responsible for target-application authorization, user access rights, endpoint protection, and compliance requirements that apply to their own data.',
            ],
          },
        ],
      },
      {
        id: 'reporting',
        title: 'Reporting security issues',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              `Report suspected security issues to ${SUPPORT_EMAIL}. Include the affected workspace, company slug if relevant, timestamps, reproduction steps, and whether any package or token may have been exposed.`,
              'Do not send target-site passwords, browser storage files, or production customer data unless Conxa support explicitly requests a secure transfer path.',
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['privacy', 'data-processing', 'runtime', 'support'],
    references: [
      {
        label: 'FTC Privacy and Security business guidance',
        href: 'https://www.ftc.gov/business-guidance/privacy-security',
      },
      {
        label: 'India Digital Personal Data Protection Act, 2023',
        href: 'https://www.indiacode.nic.in/handle/123456789/22037?view_type=browse',
      },
    ],
  },
  {
    slug: 'privacy',
    category: 'legal',
    title: 'Privacy Policy',
    eyebrow: 'Legal',
    description:
      'How CONXA collects, uses, stores, shares, and protects information across the website, dashboard, Build Studio, cloud services, and runtime telemetry.',
    lastUpdated: LAST_UPDATED,
    readingTime: '14 min read',
    summary: [
      'CONXA collects account, workspace, billing, product usage, package, support, and telemetry information needed to provide the service.',
      'Build Studio is a desktop app: recordings, screenshots, and built packages stay on your machine unless you publish, upload, or send them.',
      'Target-site browser session files are local-only runtime or Build Studio state and are not intended to be uploaded in skill packages.',
      'CONXA does not sell personal information or use advertising cookies by default.',
    ],
    sections: [
      {
        id: 'scope',
        title: 'Scope of this policy',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'This Privacy Policy explains how CONXA handles information when you visit the website, sign in to the cloud dashboard, use the Build Studio, publish packages, install the runtime, contact support, or use billing features.',
              'If your organization signs a separate agreement, data processing addendum, order form, or enterprise security document with CONXA, that written agreement controls where it conflicts with this public policy.',
            ],
          },
        ],
      },
      {
        id: 'information-collected',
        title: 'Information we collect',
        blocks: [
          {
            type: 'table',
            columns: ['Category', 'Examples', 'Purpose'],
            rows: [
              [
                'Account and identity information',
                'Name, email, organization, sign-in identifiers, workspace role, and authentication metadata from Clerk or similar providers.',
                'Create accounts, secure dashboard access, manage workspaces, and support account recovery.',
              ],
              [
                'Workspace and skill package information',
                'Company names, slugs, workflow names, package versions, installer metadata, usage meters, and publish status.',
                'Host packages, show dashboard state, enforce plan limits, and deliver updates.',
              ],
              [
                'Build and compile information',
                'Recorded workflow metadata, step descriptions, selected screenshots, DOM-derived signals, validation settings, and compile logs where submitted for compile-time assistance.',
                'Generate and improve skill packages, troubleshoot build failures, and meter compile usage.',
              ],
              [
                'Billing information',
                'Plan tier, subscription status, payment provider identifiers, billing period, checkout events, and invoice-related metadata.',
                'Operate subscriptions, process payments, prevent fraud, and maintain tax or accounting records.',
              ],
              [
                'Telemetry information',
                'Skill identifiers, run status, timestamps, recovery counts, duration, compact errors, runtime version, and package version.',
                'Provide observability, diagnose failures, improve reliability, and measure product health.',
              ],
              [
                'Legal acceptance records',
                'Your user ID, email, name, workspace, and role; the version and content hashes of the Terms and Privacy Policy you accepted; the server timestamp; your IP address at that moment; the Build Studio version; and the machine identifier described above.',
                'Record and evidence your agreement to the CONXA terms, and establish which version of each document you accepted. Retained for as long as needed to evidence the agreement.',
              ],
              [
                'Support information',
                'Messages, screenshots, logs, attachments, contact details, and diagnostic context that you choose to send.',
                'Respond to support requests and investigate incidents.',
              ],
            ],
          },
        ],
      },
      {
        id: 'local-only-data',
        title: 'Local-only browser session data',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Target-application browser sessions can contain cookies, local storage, and authentication state. CONXA treats this as sensitive local state. It is used by the Build Studio or runtime on the local machine and is not intended to be included in published skill packages.',
              'The runtime stores browser sessions locally and encrypts them at rest where supported. You should not upload auth files, browser profiles, passwords, or raw customer credentials to CONXA support unless a secure support process is agreed first.',
            ],
          },
        ],
      },
      {
        id: 'build-studio-local-data',
        title: 'What Build Studio keeps on your machine',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Build Studio is a desktop application. Workflow recordings, page snapshots, screenshots, compiled skill packages, and built installers are written to your own machine and stay there unless you publish, upload, or send them to CONXA.',
              'Your Build Studio sign-in tokens are stored in the operating system credential store, not in ordinary files. Your acceptance of the Terms and this policy is recorded on CONXA servers against your signed-in account, so the app does not ask again and so CONXA holds a record of the agreement — see the legal acceptance records row above.',
              'Content leaves your machine only for actions you take: signing in, compile-time assistance that calls a model provider through CONXA, publishing a skill package, uploading an installer, sending support material, and the product and telemetry events described above.',
            ],
          },
        ],
      },
      {
        id: 'how-used',
        title: 'How we use information',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Provide, secure, and operate the website, dashboard, Build Studio, cloud APIs, and runtime update services.',
              'Authenticate users, manage workspace membership, and enforce plan entitlements.',
              'Compile and repair workflows when you request compile-time AI assistance.',
              'Host skill packages and installers and deliver updates to installed runtimes.',
              'Process payments and maintain subscription records.',
              'Send service messages, security notices, support responses, and operational updates.',
              'Detect abuse, investigate failures, maintain logs, and improve product reliability.',
              'Comply with applicable law, accounting duties, dispute resolution, and legal process.',
            ],
          },
        ],
      },
      {
        id: 'llm-processing',
        title: 'Compile-time LLM processing',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA may route compile-time prompts to third-party LLM providers or model infrastructure when you request compilation, semantic repair, selector repair, visual anchor generation, or related Human Edit assistance.',
              'The information sent to an LLM provider depends on the feature. It may include workflow descriptions, selected DOM context, element metadata, validation goals, or selected screenshots. It should not include target-site browser session files or passwords.',
              'Runtime execution of an already compiled skill is separate from compile-time LLM assistance. The cloud does not execute your target workflow merely because a build-time model was used to create the skill package.',
            ],
          },
        ],
      },
      {
        id: 'sharing',
        title: 'How information is shared',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA shares information with service providers that help deliver the product. These may include identity providers, hosting providers, payment processors, LLM providers, email/support tools, analytics or logging tools, and security infrastructure.',
              'CONXA may disclose information when required by law, to protect users or the service, to investigate abuse, in connection with a business transaction, or with your direction or consent.',
            ],
          },
          {
            type: 'callout',
            title: 'No sale of personal information',
            body: 'CONXA does not sell personal information and does not use advertising cookies by default. If advertising or cross-context behavioral tracking is added later, this policy and cookie disclosures should be updated before use.',
          },
        ],
      },
      {
        id: 'retention',
        title: 'Retention',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA keeps information for as long as needed to provide the service, maintain business records, comply with legal obligations, resolve disputes, enforce agreements, and protect the service.',
              'Retention may vary by plan and data type. Telemetry is not retained on the free trial tier, is kept 90 days on Starter and 1 year on Pro, and follows contract terms on Enterprise. Support records and billing records may be kept longer when needed for accounting, tax, fraud prevention, or legal reasons.',
            ],
          },
        ],
      },
      {
        id: 'rights',
        title: 'Your privacy rights',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Depending on where you live, you may have rights to access, correct, delete, restrict, object to, export, or receive information about personal data processing. You may also have rights related to consent withdrawal, grievance redressal, nomination, or non-discrimination.',
              `To make a request, contact ${SUPPORT_EMAIL}. We may need to verify your identity and workspace authority before acting on a request. If your employer or customer organization controls the workspace, CONXA may direct your request to that organization where appropriate.`,
            ],
          },
        ],
      },
      {
        id: 'children',
        title: 'Children',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA is a business software platform. It is not directed to children, and users should not create accounts or submit personal information if they are not legally permitted to use business software services in their jurisdiction.',
            ],
          },
        ],
      },
      {
        id: 'international',
        title: 'International transfers',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA may process information in countries other than your own through hosting, identity, support, payment, and LLM service providers. Where required, transfer safeguards should be addressed through customer agreements, subprocessors, or applicable statutory mechanisms.',
            ],
          },
        ],
      },
      {
        id: 'contact',
        title: 'Contact',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              `For privacy questions, data requests, or complaints, contact ${SUPPORT_EMAIL}. Enterprise customers should also use any support channel or contract contact specified in their order form.`,
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['data-processing', 'cookies', 'security', 'terms'],
    references: [
      {
        label: 'India Digital Personal Data Protection Act, 2023',
        href: 'https://www.indiacode.nic.in/handle/123456789/22037?view_type=browse',
      },
      {
        label: 'California CCPA overview',
        href: 'https://oag.ca.gov/privacy/ccpa',
      },
      {
        label: 'ICO privacy information guidance',
        href: 'https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/the-right-to-be-informed/what-privacy-information-should-we-provide/',
      },
    ],
  },
  {
    slug: 'terms',
    category: 'legal',
    title: 'Terms And Conditions',
    eyebrow: 'Legal',
    description:
      'Terms for using CONXA websites, dashboard, Build Studio, cloud APIs, package hosting, installers, and runtime services, including the Build Studio software licence.',
    lastUpdated: TERMS_LAST_UPDATED,
    readingTime: '16 min read',
    summary: [
      'These terms apply to use of CONXA unless a signed agreement or order form says otherwise.',
      'Build Studio is licensed per machine, not sold: it may not be modified, reverse-engineered, resold, white-labeled, or made available to third parties.',
      'Customers keep ownership of their workflows and customer content, and may sell the skill packages and installers they build, while CONXA receives the rights needed to provide the service.',
      'Customers are responsible for target-application authorization, lawful workflow use, endpoint security, and plan compliance.',
    ],
    sections: [
      {
        id: 'agreement',
        title: 'Agreement to terms',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'These Terms and Conditions govern access to and use of CONXA websites, cloud dashboard, Build Studio, runtime, package hosting, installer distribution, support, and related services.',
              'If your organization has a signed agreement, order form, data processing addendum, or enterprise terms with CONXA, that written agreement controls where it conflicts with these public terms.',
            ],
          },
        ],
      },
      {
        id: 'accounts',
        title: 'Accounts and workspace responsibility',
        blocks: [
          {
            type: 'bullets',
            items: [
              'You must provide accurate account and workspace information.',
              'You are responsible for activity under your account and for keeping sign-in credentials secure.',
              'Workspace admins are responsible for inviting appropriate users and removing users who no longer need access.',
              'You must promptly notify CONXA if you suspect unauthorized account, token, installer, or workspace access.',
            ],
          },
        ],
      },
      {
        id: 'build-studio-license',
        title: 'Build Studio software licence',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Build Studio is licensed to you, not sold. Subject to your plan and to these terms, CONXA grants you a limited, non-exclusive, non-transferable, revocable licence to install and run one copy of Build Studio per licensed machine, on machines you or your organization own or control, and only for the number of installs your plan or order form permits.',
              'The licence is granted for your internal business purpose of recording, compiling, testing, publishing, and distributing your own skill packages and installers. It ends automatically when your subscription ends, when your plan no longer covers the install, or when CONXA terminates access under these terms. On termination you must stop using Build Studio and remove it from your machines.',
            ],
          },
        ],
      },
      {
        id: 'build-studio-restrictions',
        title: 'Build Studio restrictions',
        intro:
          'Except where a right below cannot lawfully be restricted, and except to the extent CONXA has agreed otherwise in a signed agreement, you must not:',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Copy, modify, adapt, translate, patch, or create derivative works of Build Studio or any part of it, including its installer, bundled backend, and bundled runtime components.',
              'Reverse-engineer, decompile, disassemble, or otherwise attempt to derive the source code, algorithms, model prompts, selector-generation logic, or internal data formats of Build Studio, except to the extent that this restriction is prohibited by applicable law or that applicable law grants you a non-waivable interoperability right, and then only after you have first asked CONXA in writing for the information you need.',
              'Circumvent, disable, or tamper with licensing checks, entitlement limits, plan limits, update checks, integrity checks, signature verification, or telemetry built into Build Studio.',
              'Rent, lease, lend, sell, resell, sublicense, assign, white-label, rebrand, or otherwise transfer Build Studio, or make it available to any third party, whether for a fee or free of charge.',
              'Operate Build Studio as, or as part of, a service bureau, managed service, hosted service, agency offering, or any arrangement where a third party uses Build Studio through you.',
              'Share your Build Studio installs, sign-in credentials, or workspace access with people outside the users your plan covers, or install Build Studio on more machines than your plan permits.',
              'Remove, obscure, or alter any copyright, trademark, licence, version, or attribution notice in Build Studio, its installer, or its output.',
              'Publish benchmarks, security-test results, or reverse-engineering findings about Build Studio without CONXA written consent, other than a good-faith vulnerability report sent privately to CONXA.',
            ],
          },
          {
            type: 'callout',
            title: 'Reporting instead of probing',
            body: 'If you believe Build Studio has a security defect, report it privately to ' +
              SUPPORT_EMAIL +
              '. Good-faith private reporting is welcome and is not a breach of these restrictions.',
          },
        ],
      },
      {
        id: 'ownership-and-outputs',
        title: 'Ownership, and what you may do with what you build',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA and its licensors own all right, title, and interest in Build Studio, the CONXA cloud services, the local runtime, the recorder, the compiler, the element-identity and self-healing recovery logic, the templates shipped inside generated packages, and all related intellectual property. These terms grant you the licence described above and no other rights, express or implied.',
              'The skill packages, installers, and workflow content you create with Build Studio are yours. Subject to your plan capabilities and to the acceptable-use rules in these terms, you may use, bundle, distribute, and sell those packages and installers to your own end customers under your own name and commercial terms.',
              'That freedom covers what you build, never the tool that built it. Distributing a skill package or an installer you produced is permitted; distributing, reselling, white-labeling, or otherwise providing Build Studio itself, in whole or in part, is not.',
              'Generated packages and installers include CONXA runtime components under the licence CONXA grants to your end customers through the installer. You must not modify, repackage, or strip those components, and you must not represent them as your own software.',
            ],
          },
        ],
      },
      {
        id: 'customer-content',
        title: 'Customer content and workflows',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'You retain ownership of workflow recordings, workflow names, step descriptions, screenshots, skill package metadata, package content, support materials, and other content you submit or create through CONXA.',
              'You grant CONXA the rights needed to host, process, transmit, analyze, compile, package, update, support, secure, and operate that content for the service. This includes using authorized service providers and LLM providers for requested build-time features.',
              'You represent that you have the rights and permissions needed to record workflows, automate the target application, publish packages, distribute installers, and process any data involved in those workflows.',
            ],
          },
        ],
      },
      {
        id: 'local-execution',
        title: 'Local execution responsibilities',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA skills execute on the end user machine through a local runtime. You are responsible for ensuring that each end user is authorized to access the target application and perform the workflow being automated.',
              'You are responsible for endpoint security, operating system permissions, target-site account controls, browser session hygiene, and compliance with your own contracts and policies for the software being operated.',
            ],
          },
        ],
      },
      {
        id: 'subscriptions',
        title: 'Subscriptions and payment',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Paid plans are subscription services unless an order form says otherwise. Self-serve paid checkout may use Razorpay or another payment provider. Enterprise plans may use custom procurement, invoicing, and written commercial terms.',
              'Plan limits may include seats, machines/devices, Conxa Execute seats, monthly compile credits, and AI Usage Credits pools, along with which capabilities a plan unlocks — internal or external distribution, dashboard and audit-log depth, and bring-your-own-key. CONXA may enforce these limits automatically through the dashboard, Build Studio, backend APIs, or support processes.',
              'Taxes, payment fees, foreign exchange costs, and bank charges may apply depending on the payment method, jurisdiction, and customer setup.',
            ],
          },
        ],
      },
      {
        id: 'acceptable-use',
        title: 'Acceptable use',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Do not use CONXA to access systems without authorization.',
              'Do not publish installers or packages that contain passwords, session cookies, malware, credential stealers, or intentionally harmful automation.',
              'Do not bypass rate limits, plan limits, security controls, workspace boundaries, or technical restrictions.',
              'Do not use CONXA to violate laws, third-party rights, target-application terms, privacy obligations, or employment policies.',
              'Do not interfere with the service, probe for vulnerabilities without authorization, or attempt to extract another workspace data.',
            ],
          },
        ],
      },
      {
        id: 'third-party-services',
        title: 'Third-party services',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA depends on third-party services such as identity providers, payment processors, hosting platforms, model providers, MCP clients, browsers, operating systems, and target applications. Those services may have their own terms and privacy policies.',
              'CONXA is not responsible for target-application behavior, target-application availability, customer endpoint configuration, MCP client changes, or third-party provider outages outside CONXA control.',
            ],
          },
        ],
      },
      {
        id: 'service-changes',
        title: 'Service changes and availability',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA may change, improve, suspend, remove, or limit features as the product evolves. Public docs describe current intended behavior and may be updated over time.',
              'No public docs page creates a service-level agreement, uptime commitment, support response time, security certification, or custom compliance obligation unless that commitment is included in a signed agreement.',
            ],
          },
        ],
      },
      {
        id: 'disclaimers',
        title: 'Disclaimers and limitation of liability',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'To the maximum extent permitted by law, CONXA is provided on an as-is and as-available basis. CONXA does not guarantee that every workflow will compile, recover, execute, or remain compatible with future target-application changes.',
              'To the maximum extent permitted by law, CONXA is not liable for indirect, incidental, special, consequential, exemplary, or punitive damages, or for lost profits, lost revenue, lost data, business interruption, target-application account action, or unauthorized customer-side use.',
            ],
          },
        ],
      },
      {
        id: 'termination',
        title: 'Suspension and termination',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA may suspend or terminate access if you violate these terms, fail to pay, create security risk, misuse the service, or use the platform in a way that may harm CONXA, users, third parties, or target applications.',
              'You may stop using CONXA at any time. Some records may remain as needed for billing, security, legal, backup, dispute, or compliance purposes.',
            ],
          },
        ],
      },
      {
        id: 'governing-law',
        title: 'Governing law and disputes',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'These public terms are drafted for an India-oriented SaaS service. Unless a signed agreement says otherwise, disputes should first be escalated to CONXA support for good-faith resolution.',
              'The exact governing law, venue, arbitration, tax, and procurement language for enterprise customers should be set in the signed order form or master agreement rather than inferred from public docs.',
            ],
          },
        ],
      },
      {
        id: 'contact',
        title: 'Contact',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              `Questions about these terms can be sent to ${SUPPORT_EMAIL}.`,
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['privacy', 'acceptable-use', 'billing', 'support'],
  },
  {
    slug: 'cookies',
    category: 'legal',
    title: 'Cookie Policy',
    eyebrow: 'Legal',
    description:
      'How CONXA uses essential cookies and similar technologies for authentication, checkout, security, and service operation.',
    lastUpdated: LAST_UPDATED,
    readingTime: '6 min read',
    summary: [
      'CONXA uses cookies and similar browser storage primarily for essential website, authentication, security, and checkout functions.',
      'CONXA does not use advertising cookies by default.',
      'Users can manage cookies through browser settings, but blocking essential cookies may break sign-in or checkout.',
    ],
    sections: [
      {
        id: 'what-cookies-are',
        title: 'What cookies are',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Cookies are small files or browser storage entries used by websites and service providers to remember sessions, preferences, security state, and device information. Similar technologies include local storage, session storage, pixels, and SDK-managed browser state.',
            ],
          },
        ],
      },
      {
        id: 'cookies-used',
        title: 'Cookies and storage we use',
        blocks: [
          {
            type: 'table',
            columns: ['Category', 'Purpose', 'Examples'],
            rows: [
              [
                'Essential service cookies',
                'Keep the website, dashboard, and route protection working.',
                'Session state, CSRF protection, routing state, and security checks.',
              ],
              [
                'Authentication cookies',
                'Support sign-in, sign-up, organization switching, and account security.',
                'Cookies or browser state set by Clerk or similar identity services.',
              ],
              [
                'Checkout cookies',
                'Support payment checkout and fraud prevention.',
                'Cookies or scripts used by Razorpay during checkout flows.',
              ],
              [
                'Local preferences',
                'Remember interface state or reduce repeated prompts.',
                'Theme, dismissal, or dashboard state where implemented.',
              ],
            ],
          },
        ],
      },
      {
        id: 'no-ad-cookies',
        title: 'No advertising cookies by default',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA does not use advertising cookies, ad retargeting pixels, or cross-context behavioral advertising cookies by default. If marketing tracking is added later, CONXA should update this policy before that tracking is enabled.',
            ],
          },
        ],
      },
      {
        id: 'managing-cookies',
        title: 'Managing cookies',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'You can block or delete cookies through your browser settings. Blocking essential cookies may prevent sign-in, dashboard access, organization switching, payment checkout, or security controls from working correctly.',
              'Third-party providers may provide their own controls for cookies or browser storage used in authentication, checkout, or support tools.',
            ],
          },
        ],
      },
      {
        id: 'runtime-storage',
        title: 'Runtime browser storage is different',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'This Cookie Policy is about the CONXA website and cloud dashboard. It is not the same as target-application browser session storage used by Build Studio or the runtime. Target-application browser sessions are local workflow execution state and are handled under the privacy and security docs.',
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['privacy', 'security', 'runtime'],
  },
  {
    slug: 'billing',
    category: 'billing',
    title: 'Billing And Refund Policy',
    eyebrow: 'Billing',
    description:
      'Plans, visible meters, checkout behavior, cancellations, refunds, failed payments, and enterprise billing defaults.',
    lastUpdated: LAST_UPDATED,
    readingTime: '9 min read',
    summary: [
      'CONXA is a capability ladder: Free proves it works, Starter and Pro run it across your organization, and Pro and Enterprise can ship it to your own customers. Every tier has visible meters for seats, machines, Conxa Execute seats, compile credits, and AI Usage Credits.',
      'Self-serve checkout may use Razorpay. Enterprise customers can use custom procurement and written order forms.',
      'Refunds are reviewed case by case unless mandatory law or a signed agreement requires a different result.',
    ],
    sections: [
      {
        id: 'plans',
        title: 'Plans',
        blocks: [
          {
            type: 'table',
            columns: ['Plan', 'Typical fit', 'Commercial model'],
            rows: [
              [
                'Free',
                'A 30-day trial to prove the product works, on one machine, before bringing it to a team.',
                'No paid subscription required.',
              ],
              [
                'Starter',
                'One product team automating its own processes internally.',
                'Monthly self-serve subscription where checkout is available.',
              ],
              [
                'Pro',
                'An enterprise running its own cross-system processes, or a team scaling as a distribution channel — more seats, Conxa Execute seats, compile volume, and AI Usage Credits, plus external distribution and the full dashboard.',
                'Monthly self-serve subscription where checkout is available.',
              ],
              [
                'Enterprise',
                'A SaaS vendor or consultancy shipping to its own customers — white-label installers, bring-your-own-key, SSO, custom seats, support, and usage overrides.',
                'Custom annual or contract terms.',
              ],
            ],
          },
        ],
      },
      {
        id: 'visible-meters',
        title: 'Visible usage meters',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Seats: people who can use the dashboard or Build Studio for the workspace.',
              'Machines: distinct devices a workspace can build from — the control that keeps a trial from becoming a free seat.',
              'Conxa Execute seats: people the workspace has granted access to Conxa Execute, independent of Build Studio membership.',
              'Compile credits: monthly fresh workflow compiles.',
              'AI Usage Credits: monthly LLM-assisted repair, recompile, selector repair, semantic repair, visual re-anchor, screenshot/bbox anchor regeneration, or Conxa Execute chat usage under a granted seat.',
            ],
          },
          {
            type: 'paragraphs',
            items: [
              'Local workflow creation, local workflow recording, deterministic edits, reorder/delete/input edits, validation edits, sign-off, and package builds before quota-gated cloud actions are not intended to be billed as separate public meters.',
            ],
          },
        ],
      },
      {
        id: 'checkout',
        title: 'Checkout and payment processing',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Self-serve paid plans may use Razorpay Checkout or another payment provider. The checkout provider may collect payment details, fraud signals, billing identifiers, tax information, and transaction metadata under its own terms and privacy policy.',
              'A paid subscription is active only after payment authorization and CONXA-side verification succeed. If checkout is unavailable, the dashboard may show plan and meter information while paid plan activation remains disabled.',
            ],
          },
        ],
      },
      {
        id: 'billing-periods',
        title: 'Billing periods and plan changes',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Monthly meters reset on the workspace billing cycle shown in the dashboard.',
              'Upgrades may unlock higher limits after payment verification or enterprise provisioning.',
              'Downgrades may take effect at the next renewal, immediately, or by support action depending on the checkout provider and current implementation.',
              'Enterprise changes are governed by the signed order form or procurement process.',
            ],
          },
        ],
      },
      {
        id: 'refunds',
        title: 'Refunds',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Refunds are reviewed case by case. CONXA may consider whether the subscription was recently purchased, whether material paid functionality was unavailable, whether usage occurred, whether a duplicate payment was made, and whether applicable law requires a refund.',
              'Unless a signed agreement or mandatory law says otherwise, subscription charges are not automatically refundable simply because a workflow did not compile, a target application changed, a customer did not use the service, or an integration decision changed after purchase.',
            ],
          },
        ],
      },
      {
        id: 'failed-payments',
        title: 'Failed payments and non-payment',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'If a payment fails, CONXA or the payment provider may retry the charge, ask for a new payment method, restrict paid features, downgrade the plan, suspend package publishing, or terminate access after notice where practical.',
              'Previously installed runtimes may continue to hold local skill packages, but cloud-hosted updates, dashboard access, compile assistance, and hosted installer operations may be restricted when a workspace is unpaid or suspended.',
            ],
          },
        ],
      },
      {
        id: 'taxes',
        title: 'Taxes and invoices',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Prices shown in the product may exclude taxes, levies, bank charges, currency conversion fees, and payment provider fees unless the checkout screen or order form states otherwise. Enterprise invoices should be handled through the agreed procurement process.',
            ],
          },
        ],
      },
      {
        id: 'billing-support',
        title: 'Billing support',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              `For billing, refund, failed payment, or enterprise procurement questions, contact ${SUPPORT_EMAIL} or call ${SUPPORT_PHONE_DISPLAY}.`,
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['terms', 'support', 'privacy'],
  },
  {
    slug: 'acceptable-use',
    category: 'legal',
    title: 'Acceptable Use Policy',
    eyebrow: 'Legal',
    description:
      'Rules for safe, lawful, and authorized use of CONXA workflow recording, packaging, installers, runtime, and cloud services.',
    lastUpdated: LAST_UPDATED,
    readingTime: '7 min read',
    summary: [
      'Use CONXA only for workflows and systems you are authorized to operate.',
      'Do not publish packages or installers containing credentials, malware, or intentionally harmful automation.',
      'Do not bypass plan, security, workspace, or target-application controls.',
    ],
    sections: [
      {
        id: 'authorized-use',
        title: 'Authorized use only',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'You may use CONXA only for workflows, websites, products, accounts, and data that you are authorized to access and automate. Authorization must come from your organization, the target system owner, your customer agreement, or applicable law.',
            ],
          },
        ],
      },
      {
        id: 'prohibited',
        title: 'Prohibited activity',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Credential theft, phishing, session hijacking, account takeover, spam, scraping where prohibited, or unauthorized account creation.',
              'Packaging passwords, API keys, browser storageState files, session cookies, malware, droppers, or intentionally harmful scripts.',
              'Automating regulated, high-risk, or safety-critical workflows without required human review, approvals, records, and legal authority.',
              'Bypassing rate limits, plan limits, security checks, workspace isolation, target-application restrictions, or payment controls.',
              'Using CONXA to harass, surveil, discriminate, defraud, impersonate, or violate privacy rights.',
              'Testing vulnerabilities or running load tests against CONXA without written authorization.',
            ],
          },
        ],
      },
      {
        id: 'customer-responsibility',
        title: 'Customer responsibility',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Customers are responsible for reviewing compiled workflows before distribution, confirming that installers go only to authorized users, and ensuring that end users understand what a skill will do before execution.',
              'Customers must maintain appropriate access controls in the target application. CONXA cannot determine whether a particular end user should be allowed to perform a business action inside the customer product.',
            ],
          },
        ],
      },
      {
        id: 'enforcement',
        title: 'Enforcement',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA may investigate suspected misuse and may suspend accounts, revoke hosted installers, restrict package sync, remove content, contact workspace admins, or terminate service where misuse creates legal, security, operational, or reputational risk.',
            ],
          },
        ],
      },
      {
        id: 'report-abuse',
        title: 'Report abuse',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              `Report suspected abuse to ${SUPPORT_EMAIL}. Include the workspace, company slug, installer URL, timestamps, and a concise description of the concern.`,
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['terms', 'security', 'support'],
  },
  {
    slug: 'data-processing',
    category: 'legal',
    title: 'Data Processing',
    eyebrow: 'Legal',
    description:
      'Plain-language explanation of customer and CONXA data roles, subprocessors, local-only data, deletion requests, and enterprise DPA expectations.',
    lastUpdated: LAST_UPDATED,
    readingTime: '10 min read',
    summary: [
      'Customers usually control the workflows they record, publish, and distribute.',
      'CONXA processes account, workspace, package, billing, telemetry, support, and compile-time data to provide the service.',
      'A signed data processing addendum should control enterprise-specific roles, subprocessors, transfers, audits, and retention.',
    ],
    sections: [
      {
        id: 'roles',
        title: 'Data roles',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'In many business use cases, the customer decides what workflows to record, what target application is used, who receives the installer, and what data is processed during execution. For that customer-controlled workflow data, the customer is typically the primary decision-maker.',
              'CONXA makes independent decisions for account administration, billing records, fraud prevention, service security, product analytics, support operations, and legal compliance. For those areas, CONXA may act as an independent controller or equivalent role depending on applicable law.',
            ],
          },
        ],
      },
      {
        id: 'processing-activities',
        title: 'Processing activities',
        blocks: [
          {
            type: 'table',
            columns: ['Activity', 'Data involved', 'Purpose'],
            rows: [
              [
                'Account access',
                'User identity, workspace role, session metadata.',
                'Authenticate users and secure dashboard access.',
              ],
              [
                'Compilation',
                'Workflow metadata, screenshots, DOM-derived context, validation intent, logs.',
                'Create and repair skill packages requested by the customer.',
              ],
              [
                'Package hosting',
                'Skill package content, company slug, package versions, installer metadata.',
                'Distribute package updates and hosted installers.',
              ],
              [
                'Telemetry',
                'Run status, timing, skill version, compact error and recovery information.',
                'Show product health and support reliability improvements.',
              ],
              [
                'Billing',
                'Plan, payment provider IDs, usage meters, billing periods.',
                'Operate subscriptions and enforce plan limits.',
              ],
            ],
          },
        ],
      },
      {
        id: 'subprocessors',
        title: 'Subprocessors and service providers',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'CONXA may use subprocessors or service providers for hosting, identity, payments, email, support, logging, security, and LLM infrastructure. Examples in the current product include Clerk-style identity services and Razorpay-style checkout services.',
              'Enterprise customers that require a named subprocessor list, notice period, audit terms, cross-border transfer terms, or region-specific controls should capture those requirements in a signed data processing addendum.',
            ],
          },
        ],
      },
      {
        id: 'local-only',
        title: 'Local-only data',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Target-application browser sessions are not normal cloud processing inputs. They are local Build Studio or runtime state. Published package output should exclude auth files and browser storage state.',
              'If a customer voluntarily sends logs, screenshots, or files to support, those materials become support data and may be processed for troubleshooting.',
            ],
          },
        ],
      },
      {
        id: 'deletion',
        title: 'Access, deletion, and return',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'Customers can request access, export, correction, deletion, or return of customer-controlled data through support or through product controls where available. CONXA may retain data where needed for billing, security, legal obligations, backups, dispute resolution, or abuse prevention.',
              'End users whose data belongs to a customer workspace may need to contact that customer first. CONXA may route requests to the workspace owner when the customer controls the relevant data.',
            ],
          },
        ],
      },
      {
        id: 'dpa',
        title: 'Enterprise DPA',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'This page is a public explanation, not a full enterprise data processing addendum. Enterprise customers should use a signed DPA to define controller/processor roles, subprocessors, breach notice, audit rights, retention, cross-border transfers, security exhibits, and deletion assistance.',
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['privacy', 'security', 'terms', 'support'],
    references: [
      {
        label: 'India Digital Personal Data Protection Act, 2023',
        href: 'https://www.indiacode.nic.in/handle/123456789/22037?view_type=browse',
      },
      {
        label: 'ICO right to be informed guidance',
        href: 'https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/the-right-to-be-informed/',
      },
    ],
  },
  {
    slug: 'support',
    category: 'support',
    title: 'Support',
    eyebrow: 'Support',
    description:
      'How to contact CONXA for product help, billing questions, security reports, privacy requests, and enterprise procurement.',
    lastUpdated: LAST_UPDATED,
    readingTime: '5 min read',
    summary: [
      `Email support at ${SUPPORT_EMAIL}.`,
      `For sales or enterprise procurement, call ${SUPPORT_PHONE_DISPLAY} where phone support is available.`,
      'Never send passwords, target-site session files, or production customer data unless a secure support path is agreed first.',
    ],
    sections: [
      {
        id: 'contact',
        title: 'Contact channels',
        blocks: [
          {
            type: 'links',
            items: [
              {
                label: SUPPORT_EMAIL,
                href: `mailto:${SUPPORT_EMAIL}`,
                description:
                  'Use email for product support, privacy requests, billing issues, security reports, and general questions.',
              },
              {
                label: SUPPORT_PHONE_DISPLAY,
                href: `tel:${SUPPORT_PHONE_TEL}`,
                description:
                  'Use phone for enterprise pricing, procurement, onboarding, and time-sensitive commercial questions.',
              },
            ],
          },
        ],
      },
      {
        id: 'what-to-include',
        title: 'What to include',
        blocks: [
          {
            type: 'bullets',
            items: [
              'Workspace name or account email.',
              'Company slug, workflow name, installer version, or runtime version if relevant.',
              'Exact error message, timestamp, and what you expected to happen.',
              'Whether the issue affects Build Studio, Conxa Cloud, installer download, runtime sync, or local execution.',
              'Logs or screenshots that do not contain passwords, cookies, tokens, or confidential customer data.',
            ],
          },
        ],
      },
      {
        id: 'security-reports',
        title: 'Security reports',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'For suspected security issues, include concise reproduction steps, affected route or package slug, impact, and whether tokens, installers, or packages may have been exposed. Do not run intrusive testing against CONXA systems without written authorization.',
            ],
          },
        ],
      },
      {
        id: 'privacy-requests',
        title: 'Privacy requests',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'For data access, correction, deletion, or complaint requests, include the account email and workspace context. CONXA may verify identity and authority before acting on the request.',
            ],
          },
        ],
      },
      {
        id: 'billing-help',
        title: 'Billing help',
        blocks: [
          {
            type: 'paragraphs',
            items: [
              'For failed payment, duplicate charge, refund review, invoice, or enterprise procurement questions, include payment provider identifiers if available, plan tier, workspace name, and the relevant billing period.',
            ],
          },
        ],
      },
    ],
    relatedSlugs: ['billing', 'privacy', 'security', 'terms'],
  },
] as const satisfies readonly PublicDocPage[]

export const publicDocCategories = [
  {
    id: 'product',
    title: 'Product docs',
    description:
      'How Conxa supports Claude Desktop automation, where work happens, and how packages reach installed runtimes.',
    slugs: ['platform', 'claude-automation', 'build-studio', 'build-studio-guide', 'cloud', 'runtime'],
  },
  {
    id: 'trust',
    title: 'Trust and security',
    description:
      'Security boundaries, local execution guarantees, token handling, and support expectations.',
    slugs: ['security'],
  },
  {
    id: 'legal',
    title: 'Legal',
    description:
      'Customer-facing policies for privacy, terms, cookies, acceptable use, and data processing.',
    slugs: ['privacy', 'terms', 'cookies', 'acceptable-use', 'data-processing'],
  },
  {
    id: 'billing',
    title: 'Billing',
    description:
      'Plan structure, visible meters, payment processing, refund handling, and enterprise billing.',
    slugs: ['billing'],
  },
  {
    id: 'support',
    title: 'Support',
    description:
      'How to contact CONXA for help, billing, privacy, security, and procurement.',
    slugs: ['support'],
  },
] as const satisfies readonly PublicDocCategory[]

export const publicDocSlugs = publicDocs.map((doc) => doc.slug)

export function getPublicDoc(slug: string): PublicDocPage | undefined {
  return publicDocs.find((doc) => doc.slug === slug)
}

export function getPublicDocsByCategory(category: PublicDocCategory): PublicDocPage[] {
  const docs: PublicDocPage[] = []

  for (const slug of category.slugs) {
    const doc = getPublicDoc(slug)
    if (doc) docs.push(doc)
  }

  return docs
}
