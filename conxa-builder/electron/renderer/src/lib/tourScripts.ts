// Guided first-run tour (BUILD-79): the demos as plain data. No imports, so it is unit-testable.
// Every word shown is everyday language: no selectors, ids or internal names.

export type DemoId = 'onboarding' | 'support' | 'billing' | 'leave'

/** How a step is finished. */
export type Advance =
  | { click: string } // the person clicks the element with this tour id
  | { appear: string } // an enabled element with this tour id shows up
  | { button: string } // the note shows a button with this label

export type TourStep = {
  id: string
  text: string
  /** Tour ids that stay clickable while locked. */
  target?: string[]
  /** Dim and block everything except `target`. Free-roam steps (recording, answering) are not locked. */
  lock: boolean
  advance: Advance
  /** A note drawn inside the sign-in browser (practice website only). */
  browserHint?: { text: string; target?: string }
}

export type Demo = {
  id: DemoId
  title: string
  blurb: string
  workflowName: string
  /** Where recording starts, on the practice site. */
  startPath: string
  /** What to do while recording, in plain words. */
  recordSteps: string[]
}

export const DEMOS: Demo[] = [
  {
    id: 'onboarding',
    title: 'Welcome a new customer',
    blurb: 'Add the customer, open a deal, build a quote, send it and mark the deal won.',
    workflowName: 'Onboard a new customer',
    startPath: '/practice/crm/contacts/new',
    recordSteps: [
      'Fill in the contact and save it',
      'Open a deal for them',
      'Add two items to the quote',
      'Send the quote, then mark the deal Won',
    ],
  },
  {
    id: 'support',
    title: 'Handle a support request',
    blurb: 'Pick the oldest open ticket, look up the customer, set it up, reply and close it.',
    workflowName: 'Handle the oldest support ticket',
    startPath: '/practice/helpdesk/inbox',
    recordSteps: [
      'Sort so the oldest open ticket is first, then open it',
      'Look up the customer',
      'Set the priority and topic, and pick who handles it',
      'Add a ready-made reply and send it, then close the ticket',
    ],
  },
  {
    id: 'billing',
    title: 'Monthly report and invoice',
    blurb: 'Filter last month’s orders, download the report and invoice the unpaid ones.',
    workflowName: 'Monthly report and invoice',
    startPath: '/practice/billing/orders',
    recordSteps: [
      'Show one month of orders and download the report',
      'Tick the unpaid orders and start an invoice',
      'Add the discount code and send it for approval',
    ],
  },
  {
    id: 'leave',
    title: 'Ask for time off',
    blurb: 'Request leave, check the balance and approve it as the manager.',
    workflowName: 'Request and approve leave',
    startPath: '/practice/people/leave/new',
    recordSteps: [
      'Fill in the dates and type of leave',
      'Check the leave balance page',
      'Send the request',
      'Switch to the manager view and approve it',
    ],
  },
]

export const getDemo = (id: DemoId | null): Demo | undefined => DEMOS.find((d) => d.id === id)

/** The shared opening (group, sign-in) followed by the chosen demo's record, questions and test path. */
export function stepsFor(demo: Demo): TourStep[] {
  return [
    { id: 'new-group', lock: true, target: ['new-group'], advance: { click: 'new-group' },
      text: 'A group keeps the apps you sign in to in one place. Click New group.' },
    { id: 'group-name', lock: true, target: ['group-name', 'group-next'], advance: { click: 'group-next' },
      text: 'Give your group a name, for example “Practice”, then click Next.' },
    { id: 'group-app', lock: true, target: ['group-practice'], advance: { click: 'group-practice' },
      text: 'Add the practice company. It is a pretend business made just for learning.' },
    { id: 'group-create', lock: true, target: ['group-create'], advance: { click: 'group-create' },
      text: 'Now create the group. A browser opens so you can sign in.' },
    { id: 'sign-in', lock: true, target: ['signin-region', 'auth-done'], advance: { appear: 'new-workflow' },
      browserHint: { text: 'Sign in with your Conxa account here.' },
      text: 'Sign in in the browser below. When you are in, click Done.' },
    { id: 'new-workflow', lock: true, target: ['new-workflow'], advance: { click: 'new-workflow' },
      text: 'You are signed in. Now teach Conxa something: click New workflow.' },
    { id: 'start-recording', lock: true, target: ['nw-create'], advance: { click: 'nw-create' },
      text: 'Everything is filled in for you. Click Create and start recording.' },
    { id: 'record', lock: false, advance: { click: 'finish-recording' },
      text: `Do these in the browser, like you normally would: ${demo.recordSteps.map((s, i) => `${i + 1}) ${s}`).join(' · ')}. Then click Finish and save.` },
    { id: 'questions', lock: false, advance: { appear: 'test-run' },
      text: 'Conxa may ask a few questions about what you did. Answer each one in your own words.' },
    { id: 'test', lock: true, target: ['test-run'], advance: { click: 'test-run' },
      text: 'Everything is answered. Click Run test to watch Conxa do it by itself.' },
    { id: 'done', lock: false, advance: { button: 'Finish tour' },
      text: 'You taught Conxa a whole job and watched it run. You can replay a demo any time in Settings → About.' },
  ]
}
