import { type ReactNode, useEffect, useState } from 'react'
import { useMatch } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useBackendEvents } from '@/hooks/usePythonCmd'
import { useShellShortcuts } from '@/hooks/useShellShortcuts'
import { useConnection } from '@/hooks/useConnection'
import { useConnectionStore } from '@/store/connectionStore'
import { EngineStoppedNotice, OfflineBanner } from '@/components/shell/ConnectionStates'
import { SettingsDialog } from '@/components/shell/SettingsDialog'
import { useCompileStore } from '@/store/compileStore'
import { useShellStore } from '@/store/shellStore'
import { useThreadStore } from '@/store/threadStore'
import { useRecordingStore } from '@/store/recordingStore'
import type { RecordingRow } from '@/api/conversationApi'
import { RecordingDialogs } from '@/components/shell/RecordingDialogs'
import type { ThreadItem } from '@/lib/thread'
import { useAuth } from '@/contexts/AuthContext'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { BROWSER, INSPECTOR, NAV, resolveBrowserLayout, resolveLayout } from '@/lib/panels'
import { useBrowserStore } from '@/store/browserStore'
import { BrowserPane } from '@/components/shell/BrowserPane'
import { WindowTitleBar } from '@/components/layout/WindowTitleBar'
import { PageHeaderProvider, usePageHeaderContext } from '@/contexts/PageHeaderContext'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Sidebar } from '@/components/shell/Sidebar'
import { PanelDivider } from '@/components/shell/PanelDivider'
import { InspectorPane, InspectorStrip } from '@/components/shell/InspectorPane'
import { SearchDialog } from '@/components/shell/SearchDialog'
import { ArrowLeft, Globe, RefreshCw } from 'lucide-react'

// The workspace header row lines up with the sidebar's brand row. Keep them equal.
const HEADER_ROW_HEIGHT = 'h-14'

// The current page's title/description, registered by its <PageHeader> via
// PageHeaderContext, rendered here instead of the page repeating its own header block.
function HeaderPageTitle() {
  const { header } = usePageHeaderContext()
  // Always occupies the flex-1 slot (even empty) so trailing header controls stay pinned right.
  return (
    <div className="flex min-w-0 flex-1 items-baseline gap-2">
      {header ? (
        <>
          <h1 className="shrink-0 truncate text-base font-semibold text-[var(--text-primary)]">{header.title}</h1>
          {header.description != null && header.description !== false ? (
            <div className={cn('min-w-0 text-xs text-[var(--text-meta)]', typeof header.description === 'string' && 'truncate')}>
              {header.description}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

/** Back-chevron at the true left edge of the bar — only when a page opts in via `onBack`. */
function HeaderBackButton() {
  const { header } = usePageHeaderContext()
  if (!header?.onBack) return null
  if (header.backLabel) {
    return (
      <Button type="button" variant="ghost" size="sm" className="shrink-0 gap-1.5 text-[var(--text-secondary)]" onClick={header.onBack}>
        <ArrowLeft className="size-4" />
        {header.backLabel}
      </Button>
    )
  }
  return (
    <Button type="button" variant="ghost" size="icon-sm" className="shrink-0 text-[var(--text-meta)]" aria-label="Back" onClick={header.onBack}>
      <ArrowLeft className="size-4" />
    </Button>
  )
}

// A page-supplied strip (e.g. a progress bar) laid over the header row's bottom edge.
function HeaderExtra() {
  const { header } = usePageHeaderContext()
  if (!header?.extra) return null
  return <div className="absolute inset-x-0 bottom-0">{header.extra}</div>
}

function HeaderActions() {
  const { header } = usePageHeaderContext()
  if (!header?.actions) return null
  return <div className="flex shrink-0 items-center gap-2">{header.actions}</div>
}

/** Refetches every active query on the page, so each page needn't wire its own button. */
function HeaderRefreshButton() {
  const qc = useQueryClient()
  const [spinning, setSpinning] = useState(false)
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      className="shrink-0 text-[var(--text-meta)]"
      title="Refresh"
      aria-label="Refresh"
      onClick={async () => {
        setSpinning(true)
        try {
          await qc.refetchQueries({ type: 'active' })
        } finally {
          setSpinning(false)
        }
      }}
    >
      <RefreshCw className={cn('size-4', spinning && 'animate-spin')} />
    </Button>
  )
}

/**
 * The single app-wide subscription to backend compile events. It lives here, not on the
 * compile page, so a compile keeps reporting progress after the user navigates away — and so
 * the usage cards and the sidebar's status glyphs refresh the moment something changes.
 */
function useCompileEventBridge() {
  const qc = useQueryClient()
  const applyEvent = useCompileStore((s) => s.applyEvent)
  const threads = useThreadStore.getState()

  useBackendEvents((ev) => {
    applyEvent(ev)
    if (ev.phase === 'quota') qc.invalidateQueries({ queryKey: ['entitlements'] })
    if (ev.phase === 'compile_done' || ev.phase === 'compile_error') {
      qc.invalidateQueries({ queryKey: ['workflows'] })
      qc.invalidateQueries({ queryKey: ['groups'] })
      qc.invalidateQueries({ queryKey: ['lifecycles'] })
    }
    // Conversation threads (BUILD-37): the backend appends items and revises cards; the store
    // only ever mirrors what it was told happened.
    if (ev.phase === 'thread_item' && typeof ev.thread_id === 'string') {
      threads.onItem(ev.thread_id, ev.item as ThreadItem)
      if (ev.thread_id.startsWith('wf:')) {
        qc.invalidateQueries({ queryKey: ['lifecycle', ev.thread_id.slice(3)] })
        qc.invalidateQueries({ queryKey: ['lifecycles'] })
        qc.invalidateQueries({ queryKey: ['workflows'] })
      } else {
        qc.invalidateQueries({ queryKey: ['chats'] })
      }
    } else if (ev.phase === 'recording_event' && typeof ev.session_id === 'string') {
      useRecordingStore.getState().onRow(ev.session_id, ev.row as RecordingRow, Number(ev.count ?? 0))
    } else if (ev.phase === 'thread_delta' && typeof ev.thread_id === 'string') {
      threads.onDelta(ev.thread_id, String(ev.turn_id), String(ev.text ?? ''))
    } else if (ev.phase === 'thread_turn' && typeof ev.thread_id === 'string') {
      threads.onTurn(ev.thread_id, String(ev.turn_id), String(ev.status))
    }
  })
}

/** Hiding the browser never stops a test — the pill keeps it one click away. */
function BrowserPill({ roomForPanel }: { roomForPanel: boolean }) {
  const run = useBrowserStore((s) => s.run)
  const folded = useBrowserStore((s) => s.folded)
  const setFolded = useBrowserStore((s) => s.setFolded)
  if (!run || (!folded && roomForPanel)) return null
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="shrink-0 gap-1.5"
      title={roomForPanel ? 'Show the test browser' : 'Widen the window to show the test browser'}
      disabled={!roomForPanel}
      onClick={() => setFolded(false)}
    >
      <Globe className="size-3.5" />
      {run.finished ? 'Show browser' : 'Test running · Show browser'}
    </Button>
  )
}

/** Keeps the store's window width current so panel layout can fold as the window narrows. */
function useWindowWidth() {
  const setWindowWidth = useShellStore((s) => s.setWindowWidth)
  useEffect(() => {
    const onResize = () => setWindowWidth(window.innerWidth)
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [setWindowWidth])
}

function Shell({ children }: { children: ReactNode }) {
  useCompileEventBridge()
  useShellShortcuts()
  useWindowWidth()
  useConnection()
  const engine = useConnectionStore((c) => c.engine)
  const engineDown = engine !== 'running'

  // The embedded test browser: Electron tells us which views exist; we tell it where to draw them.
  const setAvailable = useBrowserStore((b) => b.setAvailable)
  const onTabs = useBrowserStore((b) => b.onTabs)
  useEffect(() => {
    let off: (() => void) | undefined
    try {
      void window.conxa.panel.available().then(setAvailable).catch(() => setAvailable(false))
      off = window.conxa.panel.onTabs(onTabs)
    } catch {
      setAvailable(false) // an older preload: tests simply keep their own window
    }
    return () => off?.()
  }, [setAvailable, onTabs])

  const match = useMatch('/w/:workflowId')
  // With the engine down the inspector has nothing to read, so it is hidden along with the workspace.
  const workflowId = engineDown ? null : (match?.params.workflowId ?? null)
  const s = useShellStore()
  const browserRun = useBrowserStore((b) => b.run)
  const browserFolded = useBrowserStore((b) => b.folded)
  const browserWidth = useBrowserStore((b) => b.width)
  const layout = resolveLayout({
    windowWidth: s.windowWidth,
    navWidth: s.navWidth,
    inspectorWidth: s.inspectorWidth,
    navCollapsed: s.navCollapsed,
    inspectorAvailable: workflowId != null,
    inspectorOpen: s.inspectorOpen && !(browserRun && !browserFolded),
  })
  const browser = browserRun ? resolveBrowserLayout({ windowWidth: s.windowWidth, navWidth: s.navWidth, navCollapsed: s.navCollapsed, preferredWidth: browserWidth }) : null
  const browserShown = Boolean(browser && browser.mode === 'panel' && !browserFolded)
  // While the browser is shown it takes the inspector's place (the inspector is its strip), and on a
  // medium window it also folds navigation to the rail.
  const rail = layout.navMode === 'rail' || (browserShown && browser?.navMode === 'rail')

  return (
    <div className="flex min-h-0 flex-1">
      <div
        className={cn('shrink-0 transition-[width] duration-200', engineDown && 'pointer-events-none opacity-60')}
        aria-disabled={engineDown || undefined}
        style={{ width: rail ? NAV.rail : layout.navWidth }}
      >
        <Sidebar rail={rail} />
      </div>
      {!rail && (
        <PanelDivider
          label="Resize navigation"
          value={s.navWidth}
          min={NAV.min}
          max={NAV.max}
          grow={1}
          onChange={s.setNavWidth}
          onNudge={s.nudgeNav}
          onReset={s.resetNav}
        />
      )}

      <PageHeaderProvider>
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-30 border-b border-[var(--border-subtle)] bg-[var(--bg-base)]">
            <div className={cn('relative flex items-center gap-3 px-4 sm:px-6', HEADER_ROW_HEIGHT)}>
              <HeaderBackButton />
              <HeaderPageTitle />
              <HeaderActions />
              <BrowserPill roomForPanel={browser?.mode === 'panel'} />
              <HeaderRefreshButton />
              <HeaderExtra />
            </div>
          </header>
          <OfflineBanner />
          <main className="min-h-0 flex-1 overflow-hidden">{engineDown ? <EngineStoppedNotice /> : children}</main>

          {workflowId && layout.inspectorMode === 'sheet' && (
            <div
              className="absolute inset-y-0 right-0 z-40 border-l border-[var(--border-default)] anim-pop"
              style={{ width: layout.inspectorWidth }}
            >
              <InspectorPane workflowId={workflowId} />
            </div>
          )}
        </div>
      </PageHeaderProvider>

      {browserShown && browser && (
        <BrowserPane width={browser.width} maxWidth={Math.max(BROWSER.min, Math.round(s.windowWidth * BROWSER.maxFraction))} />
      )}

      {workflowId && layout.inspectorMode === 'panel' && (
        <>
          <PanelDivider
            label="Resize inspector"
            value={s.inspectorWidth}
            min={INSPECTOR.min}
            max={INSPECTOR.max}
            grow={-1}
            onChange={s.setInspectorWidth}
            onNudge={s.nudgeInspector}
            onReset={s.resetInspector}
          />
          <div className="shrink-0" style={{ width: layout.inspectorWidth }}>
            <InspectorPane workflowId={workflowId} />
          </div>
        </>
      )}
      {workflowId && (layout.inspectorMode === 'strip' || layout.inspectorMode === 'sheet') && (
        <div className="shrink-0 border-l border-[var(--border-subtle)]" style={{ width: INSPECTOR.strip }}>
          <InspectorStrip workflowId={workflowId} />
        </div>
      )}

      <SearchDialog />
      <SettingsDialog />
      <RecordingDialogs />
    </div>
  )
}

export function AppChrome({ children }: { children: ReactNode }) {
  const { identity } = useAuth()

  return (
    <TooltipProvider>
      <div className="flex h-dvh flex-col overflow-hidden bg-[var(--bg-base)] text-[var(--text-primary)]">
        <WindowTitleBar />
        {/* Before sign-in there is nothing to navigate to — and the shell's data commands
            need an identity — so only the title bar and the login surface render. */}
        {identity ? (
          <Shell>{children}</Shell>
        ) : (
          <PageHeaderProvider>
            <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
          </PageHeaderProvider>
        )}
      </div>
    </TooltipProvider>
  )
}
