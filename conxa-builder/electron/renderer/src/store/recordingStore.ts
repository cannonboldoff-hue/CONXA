import { create } from 'zustand'
import { flowSetVaries, recordingRows, type RecordingRow } from '@/api/conversationApi'

type Session = { rows: RecordingRow[]; varies: Record<string, boolean>; count: number; hydrated: boolean }

type Store = {
  sessions: Record<string, Session>
  /** Recording sessions whose cards are on screen — the only ones whose dialogs we answer. */
  watching: Record<string, true>
  onRow: (sessionId: string, row: RecordingRow, count: number) => void
  hydrate: (sessionId: string) => Promise<void>
  setVaries: (sessionId: string, sequence: number, varies: boolean) => Promise<void>
  watch: (sessionId: string) => () => void
}

const EMPTY: Session = { rows: [], varies: {}, count: 0, hydrated: false }

/** Insert a row by its sequence number — a typed value that is still being typed replaces itself. */
export function upsertRow(rows: RecordingRow[], row: RecordingRow): RecordingRow[] {
  const i = rows.findIndex((r) => r.sequence === row.sequence)
  if (i < 0) return [...rows, row].sort((a, b) => a.sequence - b.sequence)
  const next = rows.slice()
  next[i] = row
  return next
}

export const useRecordingStore = create<Store>((set, get) => ({
  sessions: {},
  watching: {},
  onRow: (sessionId, row, count) =>
    set((s) => {
      const cur = s.sessions[sessionId] ?? EMPTY
      return { sessions: { ...s.sessions, [sessionId]: { ...cur, rows: upsertRow(cur.rows, row), count: Math.max(count, cur.count) } } }
    }),
  hydrate: async (sessionId) => {
    if (get().sessions[sessionId]?.hydrated) return
    try {
      const res = await recordingRows(sessionId)
      set((s) => {
        const cur = s.sessions[sessionId] ?? EMPTY
        const rows = res.rows.reduce(upsertRow, cur.rows)
        return { sessions: { ...s.sessions, [sessionId]: { rows, varies: res.varies, count: Math.max(rows.length, cur.count), hydrated: true } } }
      })
    } catch {
      /* the live list simply stays empty */
    }
  },
  setVaries: async (sessionId, sequence, varies) => {
    set((s) => {
      const cur = s.sessions[sessionId] ?? EMPTY
      return { sessions: { ...s.sessions, [sessionId]: { ...cur, varies: { ...cur.varies, [String(sequence)]: varies } } } }
    })
    try {
      await flowSetVaries(sessionId, sequence, varies)
    } catch {
      set((s) => {
        const cur = s.sessions[sessionId] ?? EMPTY
        const { [String(sequence)]: _drop, ...rest } = cur.varies
        return { sessions: { ...s.sessions, [sessionId]: { ...cur, varies: rest } } }
      })
    }
  },
  watch: (sessionId) => {
    set((s) => ({ watching: { ...s.watching, [sessionId]: true } }))
    return () =>
      set((s) => {
        const { [sessionId]: _drop, ...rest } = s.watching
        return { watching: rest }
      })
  },
}))

export const selectSession = (sessionId: string | undefined) => (s: Store): Session =>
  (sessionId && s.sessions[sessionId]) || EMPTY
