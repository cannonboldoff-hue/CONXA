import { create } from 'zustand'
import {
  ARROW_STEP,
  INSPECTOR,
  NAV,
  PANELS_STORAGE_KEY,
  clamp,
  parsePanels,
  type PersistedPanels,
} from '@/lib/panels'

// Storage can be empty, blocked or throw (private window, cleared site data) — the shell must
// render correctly without it, so every access is wrapped.
function load(): PersistedPanels {
  try {
    return parsePanels(window.localStorage.getItem(PANELS_STORAGE_KEY))
  } catch {
    return parsePanels(null)
  }
}

function save(p: PersistedPanels) {
  try {
    window.localStorage.setItem(PANELS_STORAGE_KEY, JSON.stringify(p))
  } catch {
    /* per-viewer convenience only */
  }
}

export type SettingsTab = 'account' | 'usage' | 'testing' | 'about'

export type WorkflowDraft = { name?: string; group?: string; starting_page?: string; task?: string }

type ShellState = PersistedPanels & {
  windowWidth: number
  searchOpen: boolean
  settingsOpen: boolean
  settingsTab: SettingsTab
  /** Prefill for the New workflow screen when a chat draft asked to "Edit details first". */
  newWorkflowDraft: WorkflowDraft | null
  setWindowWidth: (w: number) => void
  setNavWidth: (w: number) => void
  setInspectorWidth: (w: number) => void
  nudgeNav: (dir: 1 | -1) => void
  nudgeInspector: (dir: 1 | -1) => void
  resetNav: () => void
  resetInspector: () => void
  toggleNav: () => void
  toggleInspector: () => void
  setSearchOpen: (open: boolean) => void
  setSettingsOpen: (open: boolean, tab?: SettingsTab) => void
  setNewWorkflowDraft: (draft: WorkflowDraft | null) => void
}

const persisted = (s: ShellState): PersistedPanels => ({
  navWidth: s.navWidth,
  inspectorWidth: s.inspectorWidth,
  navCollapsed: s.navCollapsed,
  inspectorOpen: s.inspectorOpen,
})

export const useShellStore = create<ShellState>((set, get) => {
  const update = (patch: Partial<PersistedPanels>) => {
    set(patch)
    save(persisted(get()))
  }
  return {
    ...load(),
    windowWidth: typeof window === 'undefined' ? 1440 : window.innerWidth,
    searchOpen: false,
    settingsOpen: false,
    settingsTab: 'account',
    newWorkflowDraft: null,
    setWindowWidth: (windowWidth) => set({ windowWidth }),
    setNavWidth: (w) => update({ navWidth: clamp(w, NAV.min, NAV.max) }),
    setInspectorWidth: (w) => update({ inspectorWidth: clamp(w, INSPECTOR.min, INSPECTOR.max) }),
    nudgeNav: (dir) => update({ navWidth: clamp(get().navWidth + dir * ARROW_STEP, NAV.min, NAV.max) }),
    nudgeInspector: (dir) =>
      update({ inspectorWidth: clamp(get().inspectorWidth + dir * ARROW_STEP, INSPECTOR.min, INSPECTOR.max) }),
    resetNav: () => update({ navWidth: NAV.def }),
    resetInspector: () => update({ inspectorWidth: INSPECTOR.def }),
    toggleNav: () => update({ navCollapsed: !get().navCollapsed }),
    toggleInspector: () => update({ inspectorOpen: !get().inspectorOpen }),
    setSearchOpen: (searchOpen) => set({ searchOpen }),
    setSettingsOpen: (settingsOpen, tab) => set(tab ? { settingsOpen, settingsTab: tab } : { settingsOpen }),
    setNewWorkflowDraft: (newWorkflowDraft) => set({ newWorkflowDraft }),
  }
})
