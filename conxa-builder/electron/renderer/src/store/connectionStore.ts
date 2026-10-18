import { create } from 'zustand'

export type EngineState = 'running' | 'restarting' | 'stopped'
export type CloudState = 'unknown' | 'online' | 'offline'

/** What the person needs to know about the two things Build Studio depends on: the local engine
 *  (records, compiles and tests on this computer) and Conxa Cloud (compile, chat, publish). */
type ConnectionState = {
  engine: EngineState
  cloud: CloudState
  setEngine: (e: EngineState) => void
  setCloud: (c: CloudState) => void
}

export const useConnectionStore = create<ConnectionState>((set) => ({
  engine: 'running',
  cloud: 'unknown',
  setEngine: (engine) => set({ engine }),
  setCloud: (cloud) => set((s) => (s.cloud === cloud ? s : { cloud })),
}))

/** True when something that needs Conxa Cloud should wait instead of being attempted. */
export const cloudWaiting = (s: Pick<ConnectionState, 'cloud'>) => s.cloud === 'offline'
