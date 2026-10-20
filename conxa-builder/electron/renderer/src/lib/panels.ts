/**
 * Panel sizing rules for the three-column shell (Panels mockup).
 *
 * Pure and dependency-free so node:test can exercise it — see electron/test/panels.test.mjs.
 * The workspace column never drops below WORKSPACE_MIN; when space runs out the inspector
 * folds to its strip first, then navigation folds to its rail, then the inspector becomes a
 * sheet over the workspace.
 */

export const NAV = { def: 264, min: 220, max: 320, rail: 56 } as const
export const INSPECTOR = { def: 320, min: 280, max: 400, strip: 44 } as const
export const WORKSPACE_MIN = 520
export const MIN_WINDOW = { width: 960, height: 640 } as const
export const ARROW_STEP = 16

export function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n))
}

export type ShellLayout = {
  navMode: 'full' | 'rail'
  inspectorMode: 'hidden' | 'panel' | 'strip' | 'sheet'
  navWidth: number
  inspectorWidth: number
}

export type LayoutInput = {
  windowWidth: number
  navWidth: number
  inspectorWidth: number
  navCollapsed: boolean
  /** True on workflow screens — the only place the inspector exists. */
  inspectorAvailable: boolean
  inspectorOpen: boolean
}

/**
 * Resolve what each side column does at this window width. Order of giving way:
 * inspector → strip, navigation → rail, then inspector → sheet over the workspace.
 */
export function resolveLayout(i: LayoutInput): ShellLayout {
  const navWidth = clamp(i.navWidth, NAV.min, NAV.max)
  const inspectorWidth = clamp(i.inspectorWidth, INSPECTOR.min, INSPECTOR.max)
  let navMode: ShellLayout['navMode'] = i.navCollapsed ? 'rail' : 'full'
  const navPx = () => (navMode === 'rail' ? NAV.rail : navWidth)

  if (!i.inspectorAvailable) {
    if (navMode === 'full' && i.windowWidth - navPx() < WORKSPACE_MIN) navMode = 'rail'
    return { navMode, inspectorMode: 'hidden', navWidth, inspectorWidth }
  }
  if (!i.inspectorOpen) {
    if (navMode === 'full' && i.windowWidth - navPx() - INSPECTOR.strip < WORKSPACE_MIN) navMode = 'rail'
    return { navMode, inspectorMode: 'strip', navWidth, inspectorWidth }
  }
  if (i.windowWidth - navPx() - inspectorWidth >= WORKSPACE_MIN) {
    return { navMode, inspectorMode: 'panel', navWidth, inspectorWidth }
  }
  if (i.windowWidth - navPx() - INSPECTOR.strip >= WORKSPACE_MIN) {
    return { navMode, inspectorMode: 'strip', navWidth, inspectorWidth }
  }
  navMode = 'rail'
  if (i.windowWidth - navPx() - INSPECTOR.strip >= WORKSPACE_MIN) {
    return { navMode, inspectorMode: 'strip', navWidth, inspectorWidth }
  }
  return { navMode, inspectorMode: 'sheet', navWidth, inspectorWidth }
}

export type PersistedPanels = { navWidth: number; inspectorWidth: number; navCollapsed: boolean; inspectorOpen: boolean }

export const DEFAULT_PANELS: PersistedPanels = {
  navWidth: NAV.def,
  inspectorWidth: INSPECTOR.def,
  navCollapsed: false,
  inspectorOpen: true,
}

/** Parse whatever was stored; anything malformed or out of range falls back to defaults. */
export function parsePanels(raw: string | null | undefined): PersistedPanels {
  if (!raw) return { ...DEFAULT_PANELS }
  try {
    const v = JSON.parse(raw) as Partial<PersistedPanels>
    return {
      navWidth: typeof v.navWidth === 'number' ? clamp(v.navWidth, NAV.min, NAV.max) : NAV.def,
      inspectorWidth:
        typeof v.inspectorWidth === 'number' ? clamp(v.inspectorWidth, INSPECTOR.min, INSPECTOR.max) : INSPECTOR.def,
      navCollapsed: typeof v.navCollapsed === 'boolean' ? v.navCollapsed : false,
      inspectorOpen: typeof v.inspectorOpen === 'boolean' ? v.inspectorOpen : true,
    }
  } catch {
    return { ...DEFAULT_PANELS }
  }
}

export const PANELS_STORAGE_KEY = 'conxa-shell-panels'

// ── the embedded test browser (BUILD-42) ────────────────────────────────────────────────────

/** Browser panel: 60 % of the window by default, never under 480 px, never over 75 %. */
export const BROWSER = { min: 480, defFraction: 0.6, maxFraction: 0.75 } as const

export type BrowserLayout = {
  /** 'panel' = shown beside the conversation; 'hidden' = no room (the header pill offers it back). */
  mode: 'panel' | 'hidden'
  width: number
  /** Navigation steps aside while the browser is shown ('hidden'); 'rail' when the person brought
   *  it back. The browser also takes the inspector's place (the inspector is its 44 px strip). */
  navMode: 'rail' | 'hidden'
}

/**
 * Where the browser goes. Opening it hides navigation entirely and the browser takes that room too
 * (the person's own navigation setting is untouched, so hiding the browser brings navigation back as
 * it was). `navShown` = the person asked for navigation while the browser is open: it comes back as
 * the 56 px rail. The workspace never drops below WORKSPACE_MIN, and if the browser can't have its
 * 480 px even then it is hidden rather than squeezing the conversation.
 */
export function resolveBrowserLayout(i: {
  windowWidth: number
  navShown?: boolean
  preferredWidth?: number | null
}): BrowserLayout {
  const want = clamp(i.preferredWidth ?? i.windowWidth * BROWSER.defFraction, BROWSER.min, Math.max(BROWSER.min, i.windowWidth * BROWSER.maxFraction))
  const navMode: BrowserLayout['navMode'] = i.navShown ? 'rail' : 'hidden'
  const available = i.windowWidth - (navMode === 'rail' ? NAV.rail : 0) - INSPECTOR.strip - WORKSPACE_MIN
  if (available < BROWSER.min) return { mode: 'hidden', width: 0, navMode }
  return { mode: 'panel', width: Math.round(Math.min(want, available)), navMode }
}
