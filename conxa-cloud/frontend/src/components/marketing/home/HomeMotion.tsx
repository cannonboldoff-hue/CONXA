'use client'

import { createContext, useContext, useState } from 'react'
import './home.css'

const MotionContext = createContext({ paused: false })

/** True when the visitor pressed "Pause motion". The film listens to this too. */
export const useHomePaused = () => useContext(MotionContext).paused

/** Wraps the homepage: one pause button stops every CSS animation (see home.css) and the film. */
export function HomeMotion({ children }: { children: React.ReactNode }) {
  const [paused, setPaused] = useState(false)
  return (
    <MotionContext.Provider value={{ paused }}>
      <div className={`hx bg-[#06080b] text-[#f4f5f7] overflow-x-clip${paused ? ' paused' : ''}`}>
        {children}
        <button
          type="button"
          aria-pressed={paused}
          onClick={() => setPaused((p) => !p)}
          className="fixed bottom-4 left-4 z-40 min-h-11 cursor-pointer rounded-full border border-white/30 bg-[#0b0f14]/90 px-4 text-sm text-[#f4f5f7] backdrop-blur hover:border-white/60"
        >
          {paused ? 'Play motion' : 'Pause motion'}
        </button>
      </div>
    </MotionContext.Provider>
  )
}
