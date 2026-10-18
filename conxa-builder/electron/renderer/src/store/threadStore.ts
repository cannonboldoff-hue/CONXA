import { create } from 'zustand'
import {
  cardAction,
  threadCancel,
  threadLoad,
  threadTurn,
  type CardActionResult,
  type Lifecycle,
} from '@/api/conversationApi'
import { CmdError } from '@/lib/ipc'
import { applyDelta, prependPage, upsertItem, upsertMany, type Streaming, type ThreadItem } from '@/lib/thread'

export type ThreadState = {
  items: ThreadItem[]
  loading: boolean
  loadingOlder: boolean
  loaded: boolean
  hasMore: boolean
  cursor: number | null
  streaming: Streaming
  /** The turn in flight (or queued) for this thread, if any. */
  turnId: string | null
  queued: boolean
  error: string | null
  lifecycle: Lifecycle | null
  workflow: { id: string; name: string; group_name: string; skill_id: string | null } | null
}

const EMPTY: ThreadState = {
  items: [],
  loading: false,
  loadingOlder: false,
  loaded: false,
  hasMore: false,
  cursor: null,
  streaming: null,
  turnId: null,
  queued: false,
  error: null,
  lifecycle: null,
  workflow: null,
}

type Store = {
  threads: Record<string, ThreadState>
  load: (threadId: string) => Promise<void>
  loadOlder: (threadId: string) => Promise<void>
  send: (threadId: string, message: string, anchor?: string) => Promise<void>
  answer: (threadId: string, cardId: string, body: { choice?: string; action?: string; message?: string }) => Promise<void>
  act: (threadId: string, cardId: string, action: string, payload?: Record<string, unknown>) => Promise<CardActionResult | null>
  cancel: (threadId: string) => Promise<void>
  /** Backend events (see useCompileEventBridge). */
  onItem: (threadId: string, item: ThreadItem) => void
  onDelta: (threadId: string, turnId: string, text: string) => void
  onTurn: (threadId: string, turnId: string, status: string) => void
  setLifecycle: (threadId: string, lifecycle: Lifecycle) => void
}

function patch(set: (fn: (s: Store) => Partial<Store>) => void, threadId: string, p: Partial<ThreadState> | ((t: ThreadState) => Partial<ThreadState>)) {
  set((s) => {
    const cur = s.threads[threadId] ?? EMPTY
    return { threads: { ...s.threads, [threadId]: { ...cur, ...(typeof p === 'function' ? p(cur) : p) } } }
  })
}

const messageOf = (e: unknown) => (e instanceof CmdError || e instanceof Error ? e.message : String(e))

export const useThreadStore = create<Store>((set, get) => ({
  threads: {},

  load: async (threadId) => {
    patch(set, threadId, { loading: true, error: null })
    try {
      const page = await threadLoad(threadId)
      patch(set, threadId, (t) => ({
        // Anything that arrived by event while the page was loading is already in `t.items`.
        items: upsertMany(page.items, t.items.filter((i) => !page.items.some((p) => p.id === i.id))),
        loading: false,
        loaded: true,
        hasMore: page.has_more,
        cursor: page.cursor,
        lifecycle: page.lifecycle ?? t.lifecycle,
        workflow: page.workflow ?? t.workflow,
      }))
    } catch (e) {
      patch(set, threadId, { loading: false, loaded: true, error: messageOf(e) })
    }
  },

  loadOlder: async (threadId) => {
    const t = get().threads[threadId]
    if (!t || t.loadingOlder || !t.hasMore || t.cursor == null) return
    patch(set, threadId, { loadingOlder: true })
    try {
      const page = await threadLoad(threadId, t.cursor)
      patch(set, threadId, (cur) => ({
        items: prependPage(cur.items, page.items),
        hasMore: page.has_more,
        cursor: page.cursor,
        loadingOlder: false,
      }))
    } catch (e) {
      patch(set, threadId, { loadingOlder: false, error: messageOf(e) })
    }
  },

  send: async (threadId, message, anchor) => {
    const text = message.trim()
    if (!text) return
    const turnId = `turn_${Math.random().toString(36).slice(2, 12)}`
    patch(set, threadId, { turnId, error: null, streaming: null })
    try {
      const res = await threadTurn({ thread_id: threadId, message: text, turn_id: turnId, anchor })
      patch(set, threadId, (t) => ({ items: upsertMany(t.items, res.items), streaming: null, turnId: null, queued: false }))
    } catch (e) {
      // The backend already put the person's words and an error card in the thread.
      patch(set, threadId, { streaming: null, turnId: null, queued: false, error: messageOf(e) })
    }
  },

  answer: async (threadId, cardId, body) => {
    const turnId = `turn_${Math.random().toString(36).slice(2, 12)}`
    patch(set, threadId, { turnId, error: null })
    try {
      const res = await threadTurn({ thread_id: threadId, card_id: cardId, turn_id: turnId, ...body })
      patch(set, threadId, (t) => ({ items: upsertMany(t.items, res.items), turnId: null }))
    } catch (e) {
      patch(set, threadId, { turnId: null, error: messageOf(e) })
    }
  },

  act: async (threadId, cardId, action, payload) => {
    try {
      return await cardAction(threadId, cardId, action, payload)
    } catch (e) {
      patch(set, threadId, { error: messageOf(e) })
      return null
    }
  },

  cancel: async (threadId) => {
    const turnId = get().threads[threadId]?.turnId
    if (!turnId) return
    try {
      await threadCancel(turnId)
    } catch {
      /* the turn finished on its own */
    }
  },

  onItem: (threadId, item) => patch(set, threadId, (t) => ({ items: upsertItem(t.items, item) })),
  onDelta: (threadId, turnId, text) => patch(set, threadId, (t) => ({ streaming: applyDelta(t.streaming, turnId, text) })),
  onTurn: (threadId, _turnId, status) => patch(set, threadId, { queued: status === 'queued' }),
  setLifecycle: (threadId, lifecycle) => patch(set, threadId, { lifecycle }),
}))

export const selectThread = (threadId: string | null) => (s: Store): ThreadState =>
  (threadId && s.threads[threadId]) || EMPTY
