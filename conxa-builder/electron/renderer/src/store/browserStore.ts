import { create } from 'zustand'

export type PanelTab = { id: string; active: boolean; label: string; url: string; canGoBack: boolean; canGoForward: boolean; loading: boolean }
export type PanelRun = { runId: string; tabs: PanelTab[]; finished: boolean }

type Store = {
  /** Whether Electron could open the debugging port. False → tests keep their own window. */
  available: boolean
  /** Newest run with a view; there is at most one on screen at a time. */
  run: PanelRun | null
  /** The person folded the panel (hiding never stops the run; the header pill brings it back). */
  folded: boolean
  /** The width the person dragged to, if they did. */
  width: number | null
  setAvailable: (a: boolean) => void
  onTabs: (msg: { runId: string; tabs: PanelTab[]; finished?: boolean; closed?: boolean }) => void
  setFolded: (f: boolean) => void
  setWidth: (w: number | null) => void
}

export const useBrowserStore = create<Store>((set) => ({
  available: false,
  run: null,
  folded: false,
  width: null,
  setAvailable: (available) => set({ available }),
  onTabs: ({ runId, tabs, finished, closed }) =>
    set((s) => {
      if (closed || tabs.length === 0) return s.run?.runId === runId ? { run: null, folded: false } : s
      // A new run replaces a finished one, and shows itself even if the last one was folded.
      const isNewRun = s.run?.runId !== runId
      return { run: { runId, tabs, finished: Boolean(finished) }, folded: isNewRun ? false : s.folded }
    }),
  setFolded: (folded) => set({ folded }),
  setWidth: (width) => set({ width }),
}))

export const activeTab = (run: PanelRun | null): PanelTab | null => run?.tabs.find((t) => t.active) ?? run?.tabs[0] ?? null
