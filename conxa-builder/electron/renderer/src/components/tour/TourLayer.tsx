import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { DEMOS, getDemo, stepsFor } from '@/lib/tourScripts'
import { tourActive, useTourStore } from '@/store/tourStore'

type Rect = { x: number; y: number; w: number; h: number }
const PAD = 6
/** If a locked step's control never shows up (practice site or network down) the lock lifts, so nobody is stuck. */
const GIVE_UP_MS = 15000

const sel = (id: string) => `[data-tour~="${id}"]`
const find = (id: string): HTMLElement | null => document.querySelector<HTMLElement>(sel(id))
const inside = (node: EventTarget | null, ids: string[]) =>
  node instanceof Element && ids.some((id) => node.closest(sel(id)))

/** Chooses which practice demo to run. Shown to a new Beginner and from Settings → About → Replay. */
export function DemoPicker() {
  const picking = useTourStore((s) => s.picking)
  const canCancel = useTourStore((s) => s.done.length > 0 || s.mode === 'experienced')
  const start = useTourStore((s) => s.start)
  const setPicking = useTourStore((s) => s.setPicking)
  return (
    <Dialog open={picking} onOpenChange={(o) => { if (!o && canCancel) setPicking(false) }}>
      <DialogContent showCloseButton={false} className="gap-4 sm:max-w-[560px]">
        <DialogTitle className="text-lg font-semibold">Pick the one closest to your job</DialogTitle>
        <DialogDescription className="text-sm text-[var(--text-secondary)]">
          You will teach Conxa a real job on a practice company. Nothing here is real, so you cannot break anything.
        </DialogDescription>
        <div className="grid gap-2.5">
          {DEMOS.map((d) => (
            <button
              key={d.id}
              type="button"
              onClick={() => start(d.id)}
              className="rounded-[10px] border border-[var(--border-default)] px-4 py-3 text-left outline-none hover:bg-white/[0.04] focus-visible:ring-2 focus-visible:ring-brand/55"
            >
              <span className="block text-sm font-medium text-[var(--text-primary)]">{d.title}</span>
              <span className="block text-xs text-[var(--text-secondary)]">{d.blurb}</span>
            </button>
          ))}
        </div>
        {canCancel && <Button variant="ghost" className="justify-self-end" onClick={() => setPicking(false)}>Not now</Button>}
      </DialogContent>
    </Dialog>
  )
}

/**
 * The spotlight. Dims the whole window except the controls the current step names, blocks every other
 * click, and moves on when the person does the thing. Free-roam steps (recording, answering questions)
 * only show a note. Nothing here changes what the app does: it only points and waits.
 */
export function TourOverlay() {
  const demoId = useTourStore((s) => s.demo)
  const mode = useTourStore((s) => s.mode)
  const stepIdx = useTourStore((s) => s.step)
  const setStep = useTourStore((s) => s.setStep)
  const finish = useTourStore((s) => s.finish)
  const active = tourActive({ mode, demo: demoId })
  const steps = useMemo(() => { const d = getDemo(demoId); return d ? stepsFor(d) : [] }, [demoId])
  const step = active ? steps[stepIdx] : undefined
  const [rects, setRects] = useState<Rect[]>([])
  const [gaveUp, setGaveUp] = useState(false)
  const blockerRef = useRef<HTMLDivElement>(null)

  const advance = () => {
    if (stepIdx + 1 < steps.length) setStep(stepIdx + 1)
    else finish()
  }
  const advanceRef = useRef(advance)
  advanceRef.current = advance

  // A note drawn inside the sign-in browser (main process allows it only on the practice website).
  useEffect(() => {
    void window.conxa?.tourHint?.(step?.browserHint ? { target: step.browserHint.target ?? '', text: step.browserHint.text } : null)
    return () => { void window.conxa?.tourHint?.(null) }
  }, [step])

  // Measure the highlighted controls, and give up on a lock whose control never appears.
  useEffect(() => {
    setGaveUp(false)
    setRects([])
    if (!step?.target?.length) return
    const ids = step.target
    const started = Date.now()
    const measure = () => {
      const next: Rect[] = []
      for (const id of ids) {
        const el = find(id)
        if (!el) continue
        const r = el.getBoundingClientRect()
        if (r.width > 0 && r.height > 0) next.push({ x: r.left - PAD, y: r.top - PAD, w: r.width + PAD * 2, h: r.height + PAD * 2 })
      }
      setRects((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next))
      if (next.length === 0 && step.lock && Date.now() - started > GIVE_UP_MS) setGaveUp(true)
      if (next.length > 0) setGaveUp(false)
    }
    measure()
    const t = window.setInterval(measure, 200)
    return () => window.clearInterval(t)
  }, [step])

  // Finish the step when the person does what it asks.
  useEffect(() => {
    if (!step) return
    const adv = step.advance
    if ('click' in adv) {
      const onClick = (e: MouseEvent) => { if (inside(e.target, [adv.click])) window.setTimeout(() => advanceRef.current(), 0) }
      document.addEventListener('click', onClick, true)
      return () => document.removeEventListener('click', onClick, true)
    }
    if ('appear' in adv) {
      const t = window.setInterval(() => {
        const el = find(adv.appear)
        if (el && !(el as HTMLButtonElement).disabled) advanceRef.current()
      }, 300)
      return () => window.clearInterval(t)
    }
  }, [step])

  const locked = !!step?.lock && !gaveUp && rects.length > 0
  const targetIds = step?.target ?? []

  // While locked, only the highlighted controls take clicks or keys. Escape cannot close the dialog underneath.
  useEffect(() => {
    if (!locked) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || (!inside(e.target, targetIds) && e.key !== 'Tab')) { e.stopPropagation(); e.preventDefault() }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [locked, targetIds.join('|')]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the dialog underneath from treating a click on the dimmed area as "click outside".
  useEffect(() => {
    const el = blockerRef.current
    if (!el) return
    const stop = (e: Event) => e.stopPropagation()
    for (const n of ['pointerdown', 'mousedown', 'click']) el.addEventListener(n, stop)
    return () => { for (const n of ['pointerdown', 'mousedown', 'click']) el.removeEventListener(n, stop) }
  }, [locked])

  if (!step) return null

  const W = window.innerWidth
  const H = window.innerHeight
  const holes = rects.map((r) => `M${r.x},${r.y} h${r.w} v${r.h} h${-r.w} Z`).join(' ')
  const clip = `path(evenodd, "M0,0 H${W} V${H} H0 Z ${holes}")`
  const first = rects[0]
  const noteStyle: React.CSSProperties = locked && first
    ? {
        left: Math.max(12, Math.min(first.x, W - 360)),
        top: first.y + first.h + 130 > H ? Math.max(12, first.y - 120) : first.y + first.h + 12,
      }
    : { right: 16, bottom: 16 }

  return createPortal(
    <>
      {locked && (
        <div
          ref={blockerRef}
          aria-hidden
          className="fixed inset-0 z-[9998] bg-black/60"
          style={{ clipPath: clip, pointerEvents: 'auto' }}
        />
      )}
      {locked && rects.map((r, i) => (
        <div key={i} aria-hidden className="pointer-events-none fixed z-[9998] rounded-[10px] ring-2 ring-[#22d3ee]" style={{ left: r.x, top: r.y, width: r.w, height: r.h }} />
      ))}
      <div
        role="status"
        className="fixed z-[9999] w-[340px] rounded-[12px] bg-[#22d3ee] px-4 py-3 text-sm font-medium leading-snug text-[#06080b] shadow-xl"
        style={{ ...noteStyle, pointerEvents: 'auto' }}
      >
        <p>{gaveUp ? 'This step can’t load right now. Try again a little later.' : step.text}</p>
        <div className="mt-2 flex items-center justify-between text-xs font-normal text-[#06080b]/70">
          <span>Step {stepIdx + 1} of {steps.length}</span>
          {'button' in step.advance && (
            <Button size="sm" className="bg-[#06080b] text-white hover:bg-[#06080b]/85" onClick={advance}>{step.advance.button}</Button>
          )}
        </div>
      </div>
    </>,
    document.body,
  )
}

export function TourLayer() {
  return (
    <>
      <DemoPicker />
      <TourOverlay />
    </>
  )
}
