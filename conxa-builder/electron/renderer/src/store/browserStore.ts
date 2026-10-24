import { create } from 'zustand'

export type PanelTab = {
  id: string
  active: boolean
  label: string
  url: string
  canGoBack: boolean
  canGoForward: boolean
  loading: boolean
  opener?: string | null
  /** initial = the run's first tab; site = the page opened it; person = +, Ctrl+T or a menu. */
  openedBy?: 'initial' | 'site' | 'person'
  favicon?: string | null
}
/** test = the runtime's test run; record / signin = the recorder's (fixed 1280x720 page for record). */
export type PanelRunKind = 'test' | 'record' | 'signin'
/** One row of the browser's Downloads list. */
export type PanelDownload = {
  id: string
  filename: string
  host: string
  state: 'progressing' | 'completed' | 'cancelled' | 'interrupted'
  receivedBytes: number
  /** 0 when the server did not say. */
  totalBytes: number
}
export type PanelRun = { runId: string; tabs: PanelTab[]; finished: boolean; kind: PanelRunKind; downloads: PanelDownload[] }

/** The conversation's live sign-in or recording card, mirrored so the browser pane can carry its
 *  controls. Published by the card while it is live; `runId` is the recorder session the panel
 *  run belongs to. */
export type FlowControls = {
  runId: string
  workflowId: string
  cardId: string
  kind: 'record' | 'signin'
  /** recording | paused (record) · waiting (signin) */
  state: string
  startedAt?: number
  appName?: string
  note?: string
  canForce?: boolean
  count?: number
  last?: string
}

type Store = {
  /** Whether Electron could open the debugging port. False → tests keep their own window. */
  available: boolean
  /** Newest run with a view; there is at most one on screen at a time. */
  run: PanelRun | null
  /** The person folded the panel (hiding never stops the run; the header pill brings it back). */
  folded: boolean
  /** The width the person dragged to, if they did. */
  width: number | null
  /** The browser takes the room and the conversation folds to a rail. */
  wide: boolean
  /** Navigation hides while the browser is on screen; true = the person brought it back (as the
   *  rail) for this run. Never touches their own navigation setting, so it returns when the browser goes. */
  navShown: boolean
  flow: FlowControls | null
  /** Runs the person finished (Finish and save, Discard). The recorder keeps their views alive a few
   *  seconds to flush the last events, but the pane is gone at once; late updates for them are
   *  ignored until the panel reports the run closed. */
  released: string[]
  setAvailable: (a: boolean) => void
  onTabs: (msg: { runId: string; tabs: PanelTab[]; finished?: boolean; closed?: boolean; kind?: PanelRunKind; downloads?: PanelDownload[] }) => void
  setFolded: (f: boolean) => void
  setWidth: (w: number | null) => void
  setWide: (w: boolean) => void
  setNavShown: (v: boolean) => void
  setFlow: (f: FlowControls) => void
  /** Drops the card's controls, unless another card has published since. */
  clearFlow: (cardId: string) => void
  /** Takes a finished recording's browser off screen now; the backend closes the run when it has flushed. */
  releaseRun: (runId: string) => void
}

export const useBrowserStore = create<Store>((set) => ({
  available: false,
  run: null,
  folded: false,
  width: null,
  wide: false,
  navShown: false,
  flow: null,
  released: [],
  setAvailable: (available) => set({ available }),
  onTabs: ({ runId, tabs, finished, closed, kind, downloads }) =>
    set((s) => {
      if (closed || tabs.length === 0) {
        const released = closed ? s.released.filter((id) => id !== runId) : s.released
        return s.run?.runId === runId ? { run: null, released, folded: false, wide: false, navShown: false } : { released }
      }
      if (s.released.includes(runId)) return s
      // A new run replaces a finished one, and shows itself even if the last one was folded.
      const isNewRun = s.run?.runId !== runId
      return { run: { runId, tabs, finished: Boolean(finished), kind: kind ?? 'test', downloads: downloads ?? [] }, folded: isNewRun ? false : s.folded, wide: isNewRun ? false : s.wide, navShown: isNewRun ? false : s.navShown }
    }),
  setFolded: (folded) => set(folded ? { folded, wide: false, navShown: false } : { folded }),
  setWidth: (width) => set({ width }),
  setWide: (wide) => set({ wide }),
  setNavShown: (navShown) => set({ navShown }),
  setFlow: (flow) => set({ flow }),
  clearFlow: (cardId) => set((s) => (s.flow?.cardId === cardId ? { flow: null } : s)),
  releaseRun: (runId) =>
    set((s) => ({
      released: s.released.includes(runId) ? s.released : [...s.released, runId],
      ...(s.run?.runId === runId ? { run: null, folded: false, wide: false, navShown: false } : {}),
    })),
}))

export const activeTab = (run: PanelRun | null): PanelTab | null => run?.tabs.find((t) => t.active) ?? run?.tabs[0] ?? null

/** The run the right-hand pane shows, if any. A sign-in shows there only when the conversation owns
 *  it; one started from the Group page is drawn in that page's own sign-in pane instead. */
export const paneRun = (s: Pick<Store, 'run' | 'flow'>): PanelRun | null =>
  s.run && (s.run.kind !== 'signin' || s.run.runId === s.flow?.runId) ? s.run : null

/** Whether the browser pane is open on screen (not folded away). */
export const browserOpen = (s: Pick<Store, 'run' | 'flow' | 'folded'>): boolean => paneRun(s) !== null && !s.folded
