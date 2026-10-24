import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import type { PanelRun, PanelTab } from '@/store/browserStore'

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
 * Keeps a browser-panel view drawn over `region`: the page is a native WebContentsView owned by the
 * main process, so this tells it where the region is on every resize, divider move and layout
 * change, hides it while a dialog is open, and hides it when the host unmounts. Shared by the
 * right-hand browser pane and the Group page's sign-in pane. Returns whether a dialog hid it.
 */
export function useNativeViewRegion(region: RefObject<HTMLElement>, run: PanelRun | null, tab: PanelTab | null, suspend = false): boolean {
  const lastSent = useRef('')
  const dialogOpen = useDialogOpen()
  // `suspend`: the host floats its own popover over the page and stands a still in for it.
  const hidden = dialogOpen || suspend

  const report = () => {
    const el = region.current
    if (!el || !run || !tab) return
    if (hidden) {
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
  }, [run?.runId, tab?.id, hidden])
  // The native view must never outlive its host on screen. A layout cleanup, not a passive one: in
  // one commit React runs a removed host's layout cleanups before the next host's layout effects,
  // but its passive cleanups after them — a passive hide here landed after the new host's report and
  // left the page hidden. And it forgets what it sent, so a remount (React's development double
  // mount does exactly this) reports again instead of skipping its rect as already sent.
  useLayoutEffect(() => () => {
    lastSent.current = ''
    void window.conxa.panel.hide()
  }, [])
  return dialogOpen
}
