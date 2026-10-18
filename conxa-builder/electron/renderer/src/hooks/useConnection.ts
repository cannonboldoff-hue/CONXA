import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { cmd } from '@/lib/ipc'
import { useConnectionStore } from '@/store/connectionStore'

const ONLINE_EVERY_MS = 30_000
const OFFLINE_EVERY_MS = 8_000

/**
 * The single owner of the connection state. Engine state is pushed by the main process; cloud
 * reachability is polled through the engine (`cloud_ping`) — slowly while it works, quickly while it
 * doesn't — and re-checked the moment the OS reports the network coming or going. When the engine comes
 * back after being down, every cached query is refreshed: workflows are files on disk, so the data is
 * all still there, just stale.
 */
export function useConnection() {
  const qc = useQueryClient()

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const store = useConnectionStore.getState
    let wasDown = false

    const applyEngine = (state: 'running' | 'restarting' | 'stopped') => {
      if (!alive) return
      store().setEngine(state)
      if (state === 'running' && wasDown) void qc.invalidateQueries()
      wasDown = state !== 'running'
    }

    const ping = async () => {
      clearTimeout(timer)
      if (!alive) return
      let next: 'online' | 'offline' = store().cloud === 'offline' ? 'offline' : 'online'
      if (store().engine === 'running') {
        if (typeof navigator !== 'undefined' && navigator.onLine === false) {
          next = 'offline'
        } else {
          try {
            const r = await cmd<{ online: boolean }>('cloud_ping')
            next = r.online ? 'online' : 'offline'
          } catch {
            // The engine itself didn't answer — that is the engine's problem, shown by its own notice.
          }
        }
        if (alive) {
          const was = store().cloud
          store().setCloud(next)
          if (was === 'offline' && next === 'online') void qc.invalidateQueries()
        }
      }
      if (alive) timer = setTimeout(() => void ping(), store().cloud === 'offline' ? OFFLINE_EVERY_MS : ONLINE_EVERY_MS)
    }

    let offEngine: (() => void) | undefined
    try {
      void window.conxa.engine.status().then((s) => applyEngine(s.state)).catch(() => {})
      offEngine = window.conxa.engine.onStatus((m) => applyEngine(m.state))
    } catch {
      // an older preload without the engine bridge: the engine is simply assumed to be running
    }

    const onNet = () => void ping()
    window.addEventListener('online', onNet)
    window.addEventListener('offline', onNet)
    void ping()
    // lets the banner's "Try again" re-check on demand
    const recheck = () => void ping()
    window.addEventListener('conxa:recheck-connection', recheck)

    return () => {
      alive = false
      clearTimeout(timer)
      offEngine?.()
      window.removeEventListener('online', onNet)
      window.removeEventListener('offline', onNet)
      window.removeEventListener('conxa:recheck-connection', recheck)
    }
  }, [qc])
}

export const recheckConnection = () => window.dispatchEvent(new CustomEvent('conxa:recheck-connection'))
