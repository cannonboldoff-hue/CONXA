import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Download, ExternalLink, FileText, FolderOpen, Globe, Loader2, Lock, Maximize2, Minimize2, PanelRightClose, Plus, RotateCw, X } from 'lucide-react'
import { flowDiscardRecording, flowFinishRecording, flowPauseRecording, flowResumeRecording, flowSignInCancel, flowSignInDone } from '@/api/conversationApi'
import { useFlowAction } from '@/components/conversation/cards/useFlowAction'
import { PanelDivider } from '@/components/shell/PanelDivider'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { useNativeViewRegion } from '@/components/shell/useNativeViewRegion'
import { Button } from '@/components/ui/button'
import { activeTab, useBrowserStore, type FlowControls, type PanelDownload, type PanelRun, type PanelTab } from '@/store/browserStore'
import { BROWSER } from '@/lib/panels'
import { cn } from '@/lib/utils'

/** mm:ss since `startedAt` (epoch seconds), ticking while `running`. */
export function useElapsed(startedAt: number | undefined, running: boolean) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [running])
  const s = startedAt ? Math.max(0, Math.floor((now - startedAt * 1000) / 1000)) : 0
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

type Phase = 'test' | 'ready' | 'signin' | 'recording' | 'paused'

function phaseOf(run: PanelRun, flow: FlowControls | null): Phase {
  if (run.kind === 'test') return 'test'
  // Until the site's first page is up every tab is still a blank marker page.
  if (run.tabs.every((t) => !t.url)) return 'ready'
  if (run.kind === 'signin') return 'signin'
  return flow?.state === 'paused' ? 'paused' : 'recording'
}

const EDGE: Record<Phase, string> = {
  test: 'transparent',
  ready: '#93b4e6',
  signin: '#fbbf24',
  recording: '#f87171',
  paused: '#8e8e8e',
}

function PhaseTitle({ phase, run, flow }: { phase: Phase; run: PanelRun; flow: FlowControls | null }) {
  const elapsed = useElapsed(flow?.startedAt, phase === 'recording')
  if (phase === 'test') {
    const live = !run.finished
    return (
      <>
        <span className="text-sm font-medium text-[var(--text-primary)]">Test browser</span>
        <span className="flex items-center gap-1 text-xs text-[var(--text-meta)]">
          <StatusGlyph kind={live ? 'run' : 'ok'} /> {live ? 'live' : 'finished'}
        </span>
      </>
    )
  }
  if (phase === 'ready') {
    return <span className="text-sm font-medium text-[#93b4e6]">Getting ready…</span>
  }
  if (phase === 'signin') {
    return (
      <>
        <span className="flex min-w-0 items-center gap-1.5 truncate text-sm font-medium text-[var(--text-primary)]">
          <StatusGlyph kind="warn" /> Sign in to {flow?.appName ?? 'the app'}
        </span>
        <span className="shrink-0 rounded-[4px] border border-[var(--border-default)] px-1.5 py-px text-[0.6875rem] text-[var(--text-meta)]">Not recorded</span>
      </>
    )
  }
  return (
    <span className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]" role="status">
      <span
        aria-hidden
        className={cn('size-2 rounded-full', phase === 'recording' ? 'animate-pulse bg-[#f87171] motion-reduce:animate-none' : 'bg-[#8e8e8e]')}
      />
      {phase === 'recording' ? 'Recording' : 'Paused'}
      {flow?.startedAt != null && <span className="tabular-nums text-[var(--text-meta)]">· {elapsed}</span>}
    </span>
  )
}

function FlowButtons({ flow }: { flow: FlowControls }) {
  const { busy, run } = useFlowAction(flow.workflowId)
  if (flow.kind === 'signin') {
    return (
      <>
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(() => flowSignInCancel(flow.workflowId, flow.cardId))}>Cancel</Button>
        {flow.canForce && (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowSignInDone(flow.workflowId, flow.cardId, true))}>Save anyway</Button>
        )}
        <Button variant="brand" size="sm" disabled={busy} onClick={() => run(() => flowSignInDone(flow.workflowId, flow.cardId))}>I’m signed in</Button>
      </>
    )
  }
  return (
    <>
      <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(() => flowDiscardRecording(flow.workflowId, flow.cardId))}>Discard</Button>
      {flow.state === 'paused' ? (
        <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowResumeRecording(flow.workflowId, flow.cardId))}>Resume</Button>
      ) : (
        <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowPauseRecording(flow.workflowId, flow.cardId))}>Pause</Button>
      )}
      <Button variant="brand" size="sm" disabled={busy || !flow.count} onClick={() => run(() => flowFinishRecording(flow.workflowId, flow.cardId))}>Finish and save</Button>
    </>
  )
}

/** A tab's icon: the site's favicon, a spinner while it loads, a globe otherwise. */
function TabIcon({ tab }: { tab: PanelTab }) {
  const [broken, setBroken] = useState(false)
  useEffect(() => setBroken(false), [tab.favicon])
  if (tab.loading) return <Loader2 className="size-3.5 shrink-0 animate-spin text-[var(--text-meta)] motion-reduce:animate-none" aria-hidden />
  if (tab.favicon && !broken) return <img src={tab.favicon} alt="" className="size-4 shrink-0" onError={() => setBroken(true)} draggable={false} />
  return <Globe className="size-3.5 shrink-0 text-[var(--text-meta)]" aria-hidden />
}

/** Chrome's tab strip: every tab with its icon, title and close button, then + for a new tab. */
function TabStrip({ run, seen }: { run: PanelRun; seen: Set<string> }) {
  return (
    <div className="flex h-9 shrink-0 items-end gap-0.5 overflow-x-auto bg-[#1f1f1f] px-1.5 pt-1.5" role="tablist" aria-label="Browser tabs">
      {run.tabs.map((t) => (
        <div
          key={t.id}
          role="tab"
          aria-selected={t.active}
          tabIndex={0}
          title={t.url || t.label}
          onClick={() => window.conxa.panel.selectTab({ runId: run.runId, tabId: t.id })}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') window.conxa.panel.selectTab({ runId: run.runId, tabId: t.id })
          }}
          // Middle-click closes a tab, as in a browser.
          onAuxClick={(e) => {
            if (e.button === 1) void window.conxa.panel.closeTab({ runId: run.runId, tabId: t.id })
          }}
          className={cn(
            'group flex h-full min-w-0 max-w-56 flex-1 basis-40 cursor-default select-none items-center gap-2 rounded-t-[8px] pl-2.5 pr-1 text-xs outline-none focus-visible:ring-2 focus-visible:ring-brand/55',
            t.active ? 'bg-[#262626] text-[var(--text-primary)]' : 'text-[var(--text-meta)] hover:bg-white/[0.05]',
          )}
        >
          <TabIcon tab={t} />
          <span className="min-w-0 flex-1 truncate">{t.label || 'New tab'}</span>
          {t.openedBy === 'site' && !seen.has(t.id) && (
            <span className="shrink-0 rounded-[4px] bg-[#d97757]/15 px-1 text-[0.625rem] font-medium text-[#d97757]">New</span>
          )}
          <button
            type="button"
            aria-label={`Close ${t.label || 'tab'}`}
            title="Close tab (Ctrl+W)"
            onClick={(e) => {
              e.stopPropagation()
              void window.conxa.panel.closeTab({ runId: run.runId, tabId: t.id })
            }}
            className={cn(
              'flex size-5 shrink-0 items-center justify-center rounded-full text-[var(--text-meta)] outline-none hover:bg-white/10 hover:text-[var(--text-primary)] focus-visible:ring-2 focus-visible:ring-brand/55',
              !t.active && 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100',
            )}
          >
            <X className="size-3" />
          </button>
        </div>
      ))}
      <button
        type="button"
        aria-label="New tab"
        title="New tab (Ctrl+T)"
        onClick={() => void window.conxa.panel.newTab({ runId: run.runId }).then(() => window.dispatchEvent(new Event('conxa:focus-address')))}
        className="mb-1 ml-0.5 flex size-7 shrink-0 items-center justify-center rounded-full text-[var(--text-meta)] outline-none hover:bg-white/10 hover:text-[var(--text-primary)] focus-visible:ring-2 focus-visible:ring-brand/55"
      >
        <Plus className="size-4" />
      </button>
    </div>
  )
}

const fmtBytes = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`)

function downloadStatus(d: PanelDownload): string {
  const size = d.totalBytes > 0 ? `${fmtBytes(d.receivedBytes)} of ${fmtBytes(d.totalBytes)}` : fmtBytes(d.receivedBytes)
  switch (d.state) {
    case 'progressing': return `Downloading… ${size}`
    case 'completed': return `${fmtBytes(d.receivedBytes)} • Done`
    case 'cancelled': return 'Cancelled'
    default: return 'Failed - interrupted'
  }
}

/** Chrome's Downloads popover: drops from the toolbar icon over the page. */
function DownloadsPopover({ run, onClose }: { run: PanelRun; onClose: () => void }) {
  const act = (id: string, action: 'cancel' | 'retry' | 'show' | 'open' | 'clear') => void window.conxa.panel.downloadAction({ runId: run.runId, id, action })
  const rows = [...run.downloads].reverse()
  const icon = 'flex size-7 shrink-0 items-center justify-center rounded-full text-[var(--text-secondary)] outline-none hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-brand/55'
  return (
    <div className="absolute right-2 top-1 z-10 w-80 max-w-[calc(100%-1rem)] rounded-[12px] bg-[#2b2b2b] p-2 shadow-xl ring-1 ring-white/10" role="dialog" aria-label="Downloads" onClick={(e) => e.stopPropagation()}>
      <ul className="max-h-72 overflow-y-auto">
        {rows.length === 0 && <li className="px-2 py-2 text-sm text-[var(--text-meta)]">No downloads yet.</li>}
        {rows.map((d) => {
          const pct = d.totalBytes > 0 ? Math.min(100, Math.round((d.receivedBytes / d.totalBytes) * 100)) : null
          const bad = d.state === 'cancelled' || d.state === 'interrupted'
          return (
            <li key={d.id} className="flex items-center gap-3 rounded-[8px] px-2 py-2 hover:bg-white/[0.05]">
              <FileText className="size-5 shrink-0 text-[var(--text-secondary)]" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-[var(--text-primary)]" title={d.filename}>{d.filename}</p>
                <p className={cn('truncate text-xs', bad ? 'text-status-warn' : 'text-[var(--text-meta)]')}>{downloadStatus(d)}</p>
                {d.state === 'progressing' && (
                  <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/10" role="progressbar" aria-valuenow={pct ?? undefined} aria-valuemin={0} aria-valuemax={100}>
                    <div className={cn('h-full bg-[#8ab4f8]', pct === null && 'w-1/3 animate-pulse motion-reduce:animate-none')} style={pct === null ? undefined : { width: `${pct}%` }} />
                  </div>
                )}
              </div>
              {d.state === 'progressing' && <button type="button" aria-label={`Cancel ${d.filename}`} title="Cancel" className={icon} onClick={() => act(d.id, 'cancel')}><X className="size-4" /></button>}
              {bad && <button type="button" aria-label={`Retry ${d.filename}`} title="Retry" className={icon} onClick={() => act(d.id, 'retry')}><RotateCw className="size-4" /></button>}
              {d.state === 'completed' && (
                <>
                  <button type="button" aria-label={`Open ${d.filename}`} title="Open" className={icon} onClick={() => act(d.id, 'open')}><ExternalLink className="size-4" /></button>
                  <button type="button" aria-label={`Show ${d.filename} in folder`} title="Show in folder" className={icon} onClick={() => act(d.id, 'show')}><FolderOpen className="size-4" /></button>
                </>
              )}
            </li>
          )
        })}
      </ul>
      {rows.some((d) => d.state !== 'progressing') && (
        <div className="mt-1 flex justify-end border-t border-white/10 px-2 pt-1.5">
          <button type="button" className="text-xs text-[#8ab4f8] hover:underline" onClick={() => { act('', 'clear'); onClose() }}>Clear list</button>
        </div>
      )}
    </div>
  )
}

/** Back / forward / reload-or-stop and an address bar you can type a site or a search into. */
function Toolbar({ run, tab, dlOpen, onToggleDownloads }: { run: PanelRun; tab: PanelTab; dlOpen: boolean; onToggleDownloads: () => void }) {
  const active = run.downloads.filter((d) => d.state === 'progressing')
  const failed = run.downloads.some((d) => d.state === 'cancelled' || d.state === 'interrupted')
  const input = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState<string | null>(null) // null = showing the page's own address
  const nav = (action: 'back' | 'forward' | 'reload' | 'stop') => window.conxa.panel.navigate({ runId: run.runId, tabId: tab.id, action })

  // Switching tabs drops what was half-typed, as in a browser.
  useEffect(() => setDraft(null), [tab.id])

  // Ctrl+L / F6 inside the page, or the + button: put the cursor in the address bar.
  useEffect(() => {
    const focus = () => {
      input.current?.focus()
      input.current?.select()
    }
    const onWindow = () => focus()
    window.addEventListener('conxa:focus-address', onWindow)
    let off: (() => void) | undefined
    try {
      off = window.conxa.panel.onShortcut((msg) => {
        if (msg.runId === run.runId && msg.action === 'focus-address') focus()
      })
    } catch {
      /* an older preload: only the button focuses it */
    }
    return () => {
      window.removeEventListener('conxa:focus-address', onWindow)
      off?.()
    }
  }, [run.runId])

  const secure = tab.url.startsWith('https://')
  const shown = draft ?? tab.url

  return (
    <div className="flex h-10 shrink-0 items-center gap-0.5 bg-[#262626] px-1.5">
      <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" disabled={!tab.canGoBack} aria-label="Back" title="Back (Alt+Left)" onClick={() => nav('back')}><ArrowLeft className="size-4" /></Button>
      <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" disabled={!tab.canGoForward} aria-label="Forward" title="Forward (Alt+Right)" onClick={() => nav('forward')}><ArrowRight className="size-4" /></Button>
      {tab.loading ? (
        <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" aria-label="Stop loading" title="Stop" onClick={() => nav('stop')}><X className="size-4" /></Button>
      ) : (
        <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" aria-label="Reload" title="Reload (Ctrl+R)" onClick={() => nav('reload')}><RotateCw className="size-4" /></Button>
      )}
      <form
        className="ml-1 flex h-7 min-w-0 flex-1 items-center gap-2 rounded-full bg-[#1a1a1a] px-3 focus-within:ring-2 focus-within:ring-brand/55"
        onSubmit={(e) => {
          e.preventDefault()
          const v = (draft ?? '').trim()
          if (!v) return
          void window.conxa.panel.navigate({ runId: run.runId, tabId: tab.id, action: 'load', url: v })
          setDraft(null)
          input.current?.blur()
        }}
      >
        {secure && draft === null ? (
          <Lock className="size-3 shrink-0 text-[var(--text-meta)]" aria-label="Secure connection" />
        ) : (
          <Globe className="size-3 shrink-0 text-[var(--text-meta)]" aria-hidden />
        )}
        <input
          ref={input}
          value={shown}
          placeholder="Search Google or type a URL"
          aria-label="Address and search bar"
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => setDraft(e.target.value)}
          onFocus={(e) => {
            if (draft === null) setDraft(tab.url)
            e.target.select()
          }}
          onBlur={() => setDraft((d) => (d === tab.url ? null : d))}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              setDraft(null)
              e.currentTarget.blur()
            }
          }}
          className="min-w-0 flex-1 bg-transparent text-[0.8125rem] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-meta)]"
        />
      </form>
      {run.downloads.length > 0 && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className={cn('relative ml-1 rounded-full text-[var(--text-secondary)]', dlOpen && 'bg-[#3c4b66] text-[#8ab4f8]')}
          aria-label="Downloads"
          aria-expanded={dlOpen}
          title="Downloads"
          onClick={onToggleDownloads}
        >
          {active.length > 0 ? <Loader2 className="size-4 animate-spin motion-reduce:animate-none" /> : <Download className="size-4" />}
          {active.length === 0 && (
            <span aria-hidden className={cn('absolute right-1 top-1 size-1.5 rounded-full', failed ? 'bg-status-warn' : 'bg-[#93b4e6]')} />
          )}
        </Button>
      )}
    </div>
  )
}

/**
 * Studio's embedded browser — a test run, a sign-in or a recording — built like Chrome: a tab strip
 * with + and ×, back / forward / reload, an address bar that takes a site or a search, the usual
 * keys (browser_panel.js catches them inside the page) and a right-click menu. The page itself is a
 * native WebContentsView owned by the main process, drawn into the region below
 * (useNativeViewRegion). A native view floats above every element, so nothing is drawn over the
 * page: the run's state lives in the coloured top edge, the status row and the footer. Closing a
 * finished test frees the browser; hiding never stops a run.
 */
export function BrowserPane({ width, maxWidth, wide }: { width: number; maxWidth: number; wide: boolean }) {
  const run = useBrowserStore((s) => s.run)
  const storeFlow = useBrowserStore((s) => s.flow)
  const setFolded = useBrowserStore((s) => s.setFolded)
  const setWidth = useBrowserStore((s) => s.setWidth)
  const setWide = useBrowserStore((s) => s.setWide)
  const region = useRef<HTMLDivElement>(null)
  const tab = activeTab(run)
  // The Downloads popover floats over the page, so the native page steps aside for a still of itself.
  const [dlOpen, setDlOpen] = useState(false)
  const [still, setStill] = useState<string | null>(null)
  const dialogOpen = useNativeViewRegion(region, run, tab, dlOpen)
  const openDownloads = async () => {
    if (dlOpen || !run || !tab) return
    setStill(await window.conxa.panel.snapshot({ runId: run.runId, tabId: tab.id }))
    setDlOpen(true)
  }
  const closeDownloads = () => {
    setDlOpen(false)
    setStill(null)
  }
  const dlCount = run?.downloads.length ?? 0
  // No auto-open: the still would freeze the page the person is working in. The icon says a download
  // exists; they open it. It also closes whenever the page underneath changes (the main process
  // re-shows the native view on a tab switch, which would land on top of the still).
  useEffect(() => {
    if (dlCount === 0) closeDownloads()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dlCount])
  useEffect(() => {
    closeDownloads()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab?.id, tab?.url])
  useEffect(() => {
    if (!dlOpen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && closeDownloads()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dlOpen])
  // Site-opened tabs the person hasn't looked at yet.
  const [seen, setSeen] = useState<Set<string>>(new Set())
  useEffect(() => {
    if (tab && !seen.has(tab.id)) setSeen((s) => new Set(s).add(tab.id))
  }, [tab, seen])

  if (!run || !tab) return null
  const flow = storeFlow?.runId === run.runId ? storeFlow : null
  const phase = phaseOf(run, flow)
  const blankTab = !tab.url && phase !== 'ready'

  return (
    <>
      {!wide && (
        <PanelDivider
          label="Resize browser"
          value={width}
          min={BROWSER.min}
          max={maxWidth}
          grow={-1}
          onChange={(w) => setWidth(Math.min(maxWidth, Math.max(BROWSER.min, w)))}
          onNudge={(d) => setWidth(Math.min(maxWidth, Math.max(BROWSER.min, width + d * -16)))}
          onReset={() => setWidth(null)}
        />
      )}
      <section
        aria-label={run.kind === 'test' ? 'Test browser' : run.kind === 'signin' ? 'Sign-in browser' : 'Recording browser'}
        className={cn('flex min-h-0 flex-col border-t-2 bg-[#262626]', wide ? 'min-w-0 flex-1' : 'shrink-0')}
        style={{ width: wide ? undefined : width, borderTopColor: EDGE[phase] }}
        // The same browser keys browser_panel.js catches inside the page, for when the keyboard is in
        // the browser's own bar (the address field, a tab) rather than the page.
        onKeyDown={(e) => {
          if (!(e.ctrlKey || e.metaKey) || e.altKey) return
          const k = e.key.toLowerCase()
          if (k === 't' && !e.shiftKey) {
            e.preventDefault()
            void window.conxa.panel.newTab({ runId: run.runId }).then(() => window.dispatchEvent(new Event('conxa:focus-address')))
          } else if (k === 'w') {
            e.preventDefault()
            void window.conxa.panel.closeTab({ runId: run.runId, tabId: tab.id })
          } else if (k === 'l') {
            e.preventDefault()
            window.dispatchEvent(new Event('conxa:focus-address'))
          }
        }}
      >
        <header className="flex h-10 shrink-0 items-center gap-2 bg-[#1f1f1f] px-3">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <PhaseTitle phase={phase} run={run} flow={flow} />
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="text-[var(--text-meta)]"
            aria-label={wide ? 'Show the conversation' : 'Widen the browser'}
            title={wide ? 'Show the conversation' : 'Widen the browser'}
            onClick={() => setWide(!wide)}
          >
            {wide ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="text-[var(--text-meta)]"
            aria-label="Hide browser"
            title={run.kind === 'test' ? 'Hide browser — a running test keeps going' : 'Hide browser — it keeps going'}
            onClick={() => setFolded(true)}
          >
            <PanelRightClose className="size-4" />
          </Button>
        </header>

        <TabStrip run={run} seen={seen} />
        <Toolbar run={run} tab={tab} dlOpen={dlOpen} onToggleDownloads={() => (dlOpen ? closeDownloads() : void openDownloads())} />
        <div aria-hidden className={cn('h-0.5 shrink-0', (phase === 'ready' || tab.loading) && 'animate-pulse bg-[#93b4e6]/60 motion-reduce:animate-none')} />

        {/* The native view is drawn into this region. */}
        <div ref={region} className="relative min-h-0 flex-1 bg-[#1a1a1a]" onClick={dlOpen ? closeDownloads : undefined}>
          {dlOpen && still && <img src={still} alt="" draggable={false} className="absolute inset-0 size-full object-cover object-top" />}
          {dlOpen && <DownloadsPopover run={run} onClose={closeDownloads} />}
          {dialogOpen && !dlOpen ? (
            <p className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-[var(--text-meta)]">Browser hidden while this dialog is open</p>
          ) : phase === 'ready' ? (
            <p className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-[var(--text-meta)]">Opening {run.kind === 'signin' ? 'the sign-in page' : 'the site'}…</p>
          ) : null}
        </div>

        <footer className="flex shrink-0 items-center gap-1.5 border-t border-[var(--border-subtle)] px-3 py-2">
          <div className="min-w-0 flex-1">
            <FooterNote phase={phase} run={run} flow={flow} blankTab={blankTab} />
          </div>
          {run.kind === 'test' && run.finished && (
            <Button type="button" variant="outline" size="sm" onClick={() => window.conxa.panel.closeRun({ runId: run.runId })}>
              <X className="size-3.5" /> Close browser
            </Button>
          )}
          {flow && phase !== 'ready' && <FlowButtons flow={flow} />}
        </footer>
      </section>
    </>
  )
}

function FooterNote({ phase, run, flow, blankTab }: { phase: Phase; run: PanelRun; flow: FlowControls | null; blankTab: boolean }) {
  const cls = 'flex min-w-0 items-start gap-1.5 text-xs text-[var(--text-meta)]'
  if (blankTab && phase !== 'test') return <p className={cls}>New tab — type a site or a search in the address bar.</p>
  switch (phase) {
    case 'test':
      return run.finished ? (
        <p className={cls}>Stays on the final page so you can check the result yourself. You can click around now — the run is over.</p>
      ) : (
        <p className={cls}><StatusGlyph kind="warn" /> Clicking in the page while it runs can throw the test off.</p>
      )
    case 'ready':
      return <p className={cls}>{run.kind === 'signin' ? 'Nothing here is recorded.' : 'Recording starts once the page has loaded.'}</p>
    case 'signin':
      return (
        <div className="min-w-0">
          <p className={cls}>Sign in as you normally do, including any two-step code — Studio notices when you’re in and carries on. Nothing here is recorded.</p>
          {flow?.note && <p className="text-xs text-status-warn">▲ {flow.note}</p>}
        </div>
      )
    case 'paused':
      return <p className={cls}>Paused — nothing you do in the browser is recorded.</p>
    default:
      return (
        <p className={cls}>
          <span className="shrink-0">{flow?.count ?? 0} captured</span>
          {flow?.last && <span className="min-w-0 truncate text-[var(--text-secondary)]">· Last: {flow.last}</span>}
        </p>
      )
  }
}
