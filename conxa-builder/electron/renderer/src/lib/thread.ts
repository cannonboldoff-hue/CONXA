/**
 * Pure reducer logic for a conversation thread (BUILD-37).
 *
 * Dependency-free so node:test can exercise it (electron/test/thread.test.mjs). The backend
 * is the source of truth and sends items as they are appended (`thread_item` events) and again
 * in command results, so everything here must be idempotent: the same item arriving twice, or
 * an older revision of a card arriving after a newer one, changes nothing.
 */

export type ThreadItemKind =
  | 'user_message'
  | 'assistant_message'
  | 'event'
  | 'card'
  | 'summary'
  | 'import_marker'

export type ThreadItem = {
  id: string
  ts: string
  kind: ThreadItemKind
  text?: string
  glyph?: 'ok' | 'warn' | 'fail' | 'run' | 'none'
  card_id?: string
  card_type?: string
  state?: string
  revision?: number
  created_ts?: string
  updated_ts?: string
  [key: string]: unknown
}

/** Insert or replace one item. Cards are matched by `card_id` and keep their first position. */
export function upsertItem(items: ThreadItem[], incoming: ThreadItem): ThreadItem[] {
  if (incoming.kind === 'card' && incoming.card_id) {
    const idx = items.findIndex((i) => i.card_id === incoming.card_id)
    if (idx < 0) return [...items, incoming]
    const current = items[idx]
    if ((current.revision ?? 0) > (incoming.revision ?? 0)) return items // a stale revision lost the race
    const merged: ThreadItem = { ...incoming, ts: current.created_ts ?? current.ts, created_ts: current.created_ts ?? current.ts }
    const next = items.slice()
    next[idx] = merged
    return next
  }
  if (items.some((i) => i.id === incoming.id)) return items
  return [...items, incoming]
}

export function upsertMany(items: ThreadItem[], incoming: ThreadItem[]): ThreadItem[] {
  return incoming.reduce(upsertItem, items)
}

/** Prepend an older page (already chronological) without duplicating what is on screen. */
export function prependPage(items: ThreadItem[], older: ThreadItem[]): ThreadItem[] {
  const seenIds = new Set(items.map((i) => i.id))
  const seenCards = new Set(items.filter((i) => i.card_id).map((i) => i.card_id))
  const fresh = older.filter((i) => !(seenIds.has(i.id) || (i.card_id && seenCards.has(i.card_id))))
  return [...fresh, ...items]
}

/** Cards still waiting on a person. */
export function openCards(items: ThreadItem[]): ThreadItem[] {
  return items.filter((i) => i.kind === 'card' && i.state === 'open')
}

/**
 * What the conversation shows. Everything except the last `keep` items can be folded behind an
 * "Earlier · …" row (see Compiling / Clarify mockups); the row names what is inside, in plain
 * words. `summary` and `import_marker` items are bookkeeping and never render.
 */
export function visibleItems(items: ThreadItem[]): ThreadItem[] {
  return items.filter((i) => i.kind !== 'summary' && i.kind !== 'import_marker')
}

export function earlierLabel(items: ThreadItem[]): string {
  const names = items
    .filter((i) => i.kind === 'event' && typeof i.text === 'string')
    .map((i) => String(i.text).replace(/[.…]+$/, '').toLowerCase())
  const uniq = [...new Set(names)]
  return uniq.length ? `Earlier · ${uniq.slice(0, 3).join(', ')}${uniq.length > 3 ? '…' : ''}` : `Earlier · ${items.length} items`
}

export type Streaming = { turnId: string; text: string } | null

export function applyDelta(current: Streaming, turnId: string, chunk: string): Streaming {
  if (!chunk) return current
  if (current && current.turnId === turnId) return { turnId, text: current.text + chunk }
  return { turnId, text: chunk }
}

/** Where a message's `ts` places it, as a short clock time. Falls back to '' for a bad date. */
export function clockTime(ts: string | undefined, locale?: string): string {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' })
}
