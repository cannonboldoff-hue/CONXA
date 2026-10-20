import { type ReactNode, useEffect } from 'react'
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
import { paneRun, useBrowserStore } from '@/store/browserStore'
import { BrowserPane } from '@/components/shell/BrowserPane'
import { WindowTitleBar } from '@/components/layout/WindowTitleBar'
import { PageHeaderProvider, usePageHeaderContext } from '@/contexts/PageHeaderContext'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Sidebar } from '@/components/shell/Sidebar'
import { PanelDivider } from '@/components/shell/PanelDivider'
import { InspectorPane, InspectorStrip } from '@/components/shell/InspectorPane'
import { SearchDialog } from '@/components/shell/SearchDialog'
import { ArrowLeft, Globe, MessageSquare } from 'lucide-react'

// The workspace header row lines up with the sidebar's brand row. Keep them equal.
const HEADER_ROW_HEIGHT = 'h-14'

// Keeps the flex-1 slot so trailing header controls stay pinned right. The page title itself
// (registered by <PageHeader> via PageHeaderContext) is drawn in the window title bar.
function HeaderSpacer() {
  return <div className="min-w-0 flex-1" />
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

/** The workspace header row — omitted unless the page registers a back button, actions or an extra strip, or a test browser is hidden. */
function HeaderBar({ roomForPanel }: { roomForPanel: boolean }) {
  const { header } = usePageHeaderContext()
  const run = useBrowserStore((b) => b.run)
  if (!header?.onBack && !header?.actions && !header?.extra && !run) return null
  return (
  <header className="sticky top-0 z-30 border-b border-[var(--border-subtle)] bg-[var(--bg-base)]">
    <div className={cn('relative flex items-center gap-3 px-4 sm:px-6', HEADER_ROW_HEIGHT)}>
      <HeaderBackButton />
      <HeaderSpacer />
      <HeaderActions />
      <BrowserPill roomForPanel={roomForPanel} />
      <HeaderExtra />
    </div>
  </header>
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

/** The conversation folded beside a widened browser: what has been captured, the tabs, and the way back. */
function ConversationRail() {
  const flow = useBrowserStore((b) => b.flow)
  const run = useBrowserStore((b) => b.run)
  const setWide = useBrowserStore((b) => b.setWide)
  return (
    <aside aria-label="Conversation (folded)" className="flex w-16 shrink-0 flex-col items-center gap-3 border-r border-[var(--border-subtle)] bg-[var(--bg-base)] py-3">
      <Button variant="ghost" size="icon-sm" aria-label="Show conversation" title="Show conversation" onClick={() => setWide(false)}>
        <MessageSquare className="size-4" />
      </Button>
      {flow?.kind === 'record' && (
        <div className="flex flex-col items-center text-center" title="Actions captured so far">
          <span className="text-base font-semibold tabular-nums text-[var(--text-primary)]">{flow.count ?? 0}</span>
          <span className="text-[0.625rem] text-[var(--text-meta)]">captured</span>
        </div>
      )}
      {run && run.tabs.length > 1 && (
        <div className="flex flex-col items-center gap-1">
          {run.tabs.map((t, i) => (
            <button
              key={t.id}
              type="button"
              title={t.label || t.url}
              onClick={() => window.conxa.panel.selectTab({ runId: run.runId, tabId: t.id })}
              className={cn('w-10 rounded-[6px] py-0.5 text-[0.6875rem] tabular-nums', t.active ? 'bg-white/[0.07] text-[var(--text-primary)]' : 'text-[var(--text-meta)] hover:bg-white/[0.04]')}
            >
              Tab {i + 1}
            </button>
          ))}
        </div>
      )}
    </aside>
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
  const browserRun = useBrowserStore((b) => paneRun(b))
  const browserFolded = useBrowserStore((b) => b.folded)
  const browserWidth = useBrowserStore((b) => b.width)
  const browserWide = useBrowserStore((b) => b.wide)
  const navShownByPerson = useBrowserStore((b) => b.navShown)
  const layout = resolveLayout({
    windowWidth: s.windowWidth,
    navWidth: s.navWidth,
    inspectorWidth: s.inspectorWidth,
    navCollapsed: s.navCollapsed,
    inspectorAvailable: workflowId != null,
    inspectorOpen: s.inspectorOpen && !(browserRun && !browserFolded),
  })
  const browser = browserRun ? resolveBrowserLayout({ windowWidth: s.windowWidth, navShown: navShownByPerson, preferredWidth: browserWidth }) : null
  const browserShown = Boolean(browser && browser.mode === 'panel' && !browserFolded)
  // Wide: the browser takes the room and the conversation folds to a 64 px rail (kept mounted).
  const wide = browserShown && browserWide
  // While the browser is shown it takes the inspector's place (the inspector is its strip) and
  // navigation steps aside entirely, unless the person brought it back (then it is the rail). Hiding
  // or closing the browser brings navigation back exactly as it was.
  const navHidden = browserShown && browser?.navMode === 'hidden'
  const rail = layout.navMode === 'rail' || (browserShown && browser?.navMode === 'rail')

  return (
    <div className="flex min-h-0 flex-1">
      <div
        // `invisible` (visibility: hidden) also keeps a stepped-aside sidebar out of the tab order.
        className={cn('shrink-0 overflow-hidden transition-[width] duration-200', engineDown && 'pointer-events-none opacity-60', navHidden && 'invisible')}
        aria-disabled={engineDown || undefined}
        style={{ width: navHidden ? 0 : rail ? NAV.rail : layout.navWidth }}
      >
        <Sidebar rail={rail || navHidden} />
      </div>
      {!rail && !navHidden && (
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

      {wide && <ConversationRail />}
      <div className={cn('relative min-h-0 min-w-0 flex-1 flex-col', wide ? 'hidden' : 'flex')}>
          <HeaderBar roomForPanel={browser?.mode === 'panel'} />
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

      {browserShown && browser && (
        <BrowserPane width={browser.width} maxWidth={Math.max(BROWSER.min, Math.round(s.windowWidth * BROWSER.maxFraction))} wide={wide} />
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
      <PageHeaderProvider>
      <div className="flex h-dvh flex-col overflow-hidden bg-[var(--bg-base)] text-[var(--text-primary)]">
        <WindowTitleBar />
        {/* Before sign-in there is nothing to navigate to — and the shell's data commands
            need an identity — so only the title bar and the login surface render. */}
        {identity ? (
          <Shell>{children}</Shell>
        ) : (
          <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
        )}
      </div>
      </PageHeaderProvider>
    </TooltipProvider>
  )
}
