import { create } from 'zustand'
import type { DemoId } from '@/lib/tourScripts'

// Tour progress is a per-viewer convenience: it lives only in localStorage, never in a build or publish output.
const KEY = 'conxa.tour.v1'

type Persisted = { mode: 'beginner' | 'experienced' | null; demo: DemoId | null; step: number; done: DemoId[] }
const EMPTY: Persisted = { mode: null, demo: null, step: 0, done: [] }

function load(): Persisted {
  try {
    const p = JSON.parse(window.localStorage.getItem(KEY) || 'null') as Partial<Persisted> | null
    if (!p) return EMPTY
    return {
      mode: p.mode === 'beginner' || p.mode === 'experienced' ? p.mode : null,
      demo: (p.demo as DemoId) ?? null,
      step: Number.isInteger(p.step) && (p.step as number) >= 0 ? (p.step as number) : 0,
      done: Array.isArray(p.done) ? (p.done as DemoId[]) : [],
    }
  } catch {
    return EMPTY
  }
}

type TourState = Persisted & {
  /** True while the demo picker is open (first launch as Beginner, or Replay). */
  picking: boolean
  setMode: (m: 'beginner' | 'experienced') => void
  setPicking: (p: boolean) => void
  start: (d: DemoId) => void
  setStep: (i: number) => void
  finish: () => void
}

export const useTourStore = create<TourState>((set, get) => {
  const upd = (patch: Partial<Persisted> & { picking?: boolean }) => {
    set(patch)
    const { mode, demo, step, done } = get()
    try {
      window.localStorage.setItem(KEY, JSON.stringify({ mode, demo, step, done }))
    } catch {
      /* per-viewer convenience only */
    }
  }
  return {
    ...load(),
    picking: false,
    setMode: (mode) => upd({ mode, picking: mode === 'beginner' && !get().demo }),
    setPicking: (picking) => set({ picking }),
    start: (demo) => upd({ mode: 'beginner', demo, step: 0, picking: false }),
    setStep: (step) => upd({ step }),
    finish: () => {
      const d = get().demo
      upd({ demo: null, step: 0, done: d ? [...new Set([...get().done, d])] : get().done })
    },
  }
})

/** True while a demo is in progress. */
export const tourActive = (s: Pick<Persisted, 'mode' | 'demo'>) => s.mode === 'beginner' && s.demo !== null
