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
  /** "New conversation": the model reads nothing above it; everything above stays on screen and works. */
  | 'new_conversation'
  /** "Edit and resend": `ids` of the messages it replaced. Never stored, only applied. */
  | 'retract'

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
  if (incoming.kind === 'retract') {
    const gone = new Set(Array.isArray(incoming.ids) ? incoming.ids.map(String) : [])
    return items.some((i) => gone.has(i.id)) ? items.filter((i) => !gone.has(i.id)) : items
  }
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

/**
 * A freshly loaded newest page, plus only what arrived by event while it loaded (WF-15 F2). A local item older
 * than the page belongs to earlier history (`loadOlder` brings it back in place); appending it put an old test
 * card below the newest messages after leaving a workflow and coming back.
 */
export function mergeLoadedPage(local: ThreadItem[], page: ThreadItem[]): ThreadItem[] {
  const ids = new Set(page.map((p) => p.id))
  const cards = new Set(page.filter((p) => p.card_id).map((p) => p.card_id))
  const from = page.length ? page[0].ts : ''
  return upsertMany(page, local.filter((i) => !ids.has(i.id) && ((i.card_id && cards.has(i.card_id)) || i.ts > from)))
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
  // `hidden`: an instruction Conxa gave itself in the person's voice; they never typed it, so it is not shown.
  return items.filter((i) => i.kind !== 'summary' && i.kind !== 'import_marker' && !i.hidden)
}

/**
 * The person's last message, if "Edit" may be offered on it: only plain replies came after it, and no card
 * changed since it was sent. Mirrors the backend's `thread_store.editable_message`, which has the last word.
 */
export function editableMessageId(items: ThreadItem[]): string | null {
  const idx = items.map((i) => i.kind).lastIndexOf('user_message')
  if (idx < 0) return null
  const msg = items[idx]
  if (msg.hidden || msg.imported) return null
  const plainReplies = items.slice(idx + 1).every((i) => (i.kind === 'assistant_message' && !i.routed) || i.kind === 'summary')
  const cardMovedSince = items.some((i) => i.kind === 'card' && String(i.updated_ts ?? i.ts) > msg.ts)
  return plainReplies && !cardMovedSince ? msg.id : null
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
