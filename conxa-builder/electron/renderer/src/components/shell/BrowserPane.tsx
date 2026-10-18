import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, PanelRightClose, RotateCw, X } from 'lucide-react'
import { PanelDivider } from '@/components/shell/PanelDivider'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { Button } from '@/components/ui/button'
import { activeTab, useBrowserStore } from '@/store/browserStore'
import { BROWSER } from '@/lib/panels'
import { cn } from '@/lib/utils'

/** True while any dialog or sheet is open: a native view floats above the page, so nothing can be
 *  drawn over it — the region is hidden for the length of the dialog instead. */
function useDialogOpen(): boolean {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const check = () => setOpen(document.querySelector('[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]') != null)
    check()
    const mo = new MutationObserver(check)
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-state'] })
    return () => mo.disconnect()
  }, [])
  return open
}

/**
 * The embedded test browser. The page itself is a native WebContentsView owned by the main process,
 * drawn into the empty region below; this component tells the main process where that region is on
 * every resize, every divider move and every layout change. Closing the panel frees the browser;
 * hiding it does not stop the run.
 */
export function BrowserPane({ width, maxWidth }: { width: number; maxWidth: number }) {
  const run = useBrowserStore((s) => s.run)
  const setFolded = useBrowserStore((s) => s.setFolded)
  const setWidth = useBrowserStore((s) => s.setWidth)
  const region = useRef<HTMLDivElement>(null)
  const lastSent = useRef('')
  const dialogOpen = useDialogOpen()
  const tab = activeTab(run)

  const report = () => {
    const el = region.current
    if (!el || !run || !tab) return
    if (dialogOpen) {
      if (lastSent.current !== 'hidden') {
        lastSent.current = 'hidden'
        void window.conxa.panel.hide()
      }
      return
    }
    const r = el.getBoundingClientRect()
    const key = `${run.runId}:${tab.id}:${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.width)},${Math.round(r.height)}`
    if (key === lastSent.current) return
    lastSent.current = key
    void window.conxa.panel.setBounds({ runId: run.runId, tabId: tab.id, rect: { x: r.x, y: r.y, width: r.width, height: r.height } })
  }

  // Layout settles after render (divider drags, folds, window resizes), so read it after every render.
  useLayoutEffect(report)
  useEffect(() => {
    const el = region.current
    if (!el) return
    const ro = new ResizeObserver(report)
    ro.observe(el)
    window.addEventListener('resize', report)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', report)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.runId, tab?.id, dialogOpen])
  // The native view must never outlive this pane on screen.
  useEffect(() => () => void window.conxa.panel.hide(), [])

  if (!run || !tab) return null
  const live = !run.finished

  return (
    <>
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
      <section aria-label="Test browser" className="flex min-h-0 shrink-0 flex-col bg-[#262626]" style={{ width }}>
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-[var(--border-subtle)] px-3">
          <span className="text-sm font-medium text-[var(--text-primary)]">Test browser</span>
          <span className="flex items-center gap-1 text-xs text-[var(--text-meta)]">
            <StatusGlyph kind={live ? 'run' : 'ok'} /> {live ? 'live' : 'finished'}
          </span>
          <span className="min-w-0 flex-1 truncate text-center font-mono text-xs text-[var(--text-meta)]" title={tab.url}>{tab.url.replace(/^https?:\/\//, '')}</span>
          <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" disabled={!tab.canGoBack} aria-label="Back" onClick={() => window.conxa.panel.navigate({ runId: run.runId, tabId: tab.id, action: 'back' })}><ArrowLeft className="size-4" /></Button>
          <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" disabled={!tab.canGoForward} aria-label="Forward" onClick={() => window.conxa.panel.navigate({ runId: run.runId, tabId: tab.id, action: 'forward' })}><ArrowRight className="size-4" /></Button>
          <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" aria-label="Reload" onClick={() => window.conxa.panel.navigate({ runId: run.runId, tabId: tab.id, action: 'reload' })}><RotateCw className="size-4" /></Button>
          <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" aria-label="Hide browser" title="Hide browser — a running test keeps going" onClick={() => setFolded(true)}><PanelRightClose className="size-4" /></Button>
        </header>

        {/* The native view is drawn into this region. */}
        <div ref={region} className="relative min-h-0 flex-1 bg-[#1a1a1a]">
          {dialogOpen && (
            <p className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-[var(--text-meta)]">Browser hidden while this dialog is open</p>
          )}
        </div>

        <footer className="flex shrink-0 flex-col gap-1.5 border-t border-[var(--border-subtle)] px-3 py-2">
          {live ? (
            <p className="flex items-start gap-1.5 text-xs text-[var(--text-meta)]"><StatusGlyph kind="warn" /> Clicking in the page while it runs can throw the test off.</p>
          ) : (
            <p className="text-xs text-[var(--text-meta)]">Stays on the final page so you can check the result yourself. You can click around now — the run is over.</p>
          )}
          <div className="flex items-center gap-1.5">
            <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto" role="tablist" aria-label="Browser tabs">
              {run.tabs.map((t, i) => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={t.active}
                  onClick={() => window.conxa.panel.selectTab({ runId: run.runId, tabId: t.id })}
                  className={cn('max-w-40 shrink-0 truncate rounded-[6px] px-2 py-1 text-xs outline-none focus-visible:ring-2 focus-visible:ring-brand/55', t.active ? 'bg-white/[0.07] text-[var(--text-primary)]' : 'text-[var(--text-meta)] hover:bg-white/[0.04]')}
                  title={t.url || t.label}
                >
                  {t.label || `Tab ${i + 1}`}
                </button>
              ))}
              <span className="shrink-0 pl-1 text-xs text-[var(--text-meta)]">Tab {run.tabs.findIndex((t) => t.active) + 1} of {run.tabs.length}</span>
            </div>
            {run.finished && (
              <Button type="button" variant="outline" size="sm" onClick={() => window.conxa.panel.closeRun({ runId: run.runId })}>
                <X className="size-3.5" /> Close browser
              </Button>
            )}
          </div>
        </footer>
      </section>
    </>
  )
}
