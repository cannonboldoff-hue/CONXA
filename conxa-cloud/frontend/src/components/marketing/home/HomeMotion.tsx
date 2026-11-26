'use client'

import { createContext, useContext } from 'react'
import './home.css'

const MotionContext = createContext({ paused: false })

/** True when the visitor pressed "Pause motion". The film listens to this too. */
export const useHomePaused = () => useContext(MotionContext).paused

/** Wraps the homepage: one pause button stops every CSS animation (see home.css) and the film. */
export function HomeMotion({ children }: { children: React.ReactNode }) {
  return (
    <MotionContext.Provider value={{ paused: false }}>
      <div className="hx bg-[#06080b] text-[#f4f5f7] overflow-x-clip">
        {children}
      </div>
    </MotionContext.Provider>
  )
}
