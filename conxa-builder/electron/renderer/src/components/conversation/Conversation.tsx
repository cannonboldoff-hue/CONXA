import { type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AssistantMessage, EventRow, ThreadSkeleton, UserMessage } from '@/components/conversation/parts'
import { cardComponent } from '@/components/conversation/cards'
import { selectThread, useThreadStore } from '@/store/threadStore'
import { visibleItems, type ThreadItem } from '@/lib/thread'
import { useConnectionStore } from '@/store/connectionStore'
import { cn } from '@/lib/utils'

function ItemView({ threadId, item }: { threadId: string; item: ThreadItem }) {
  switch (item.kind) {
    case 'user_message':
      return <UserMessage text={String(item.text ?? '')} ts={item.ts} />
    case 'assistant_message':
      return <AssistantMessage text={String(item.text ?? '')} ts={item.ts} />
    case 'event':
      return <EventRow item={item} />
    case 'card': {
      const Card = cardComponent(item.card_type)
      return <Card threadId={threadId} item={item} />
    }
    default:
      return null
  }
}

/** The composer: Enter sends, Shift+Enter is a new line, Stop replaces Send while a turn runs. */
function Composer({
  threadId,
  placeholder,
  note,
  disabledReason,
  onSend,
}: {
  threadId: string
  placeholder: string
  note?: ReactNode
  disabledReason?: string
  onSend: (text: string) => void | Promise<void>
}) {
  const t = useThreadStore(selectThread(threadId))
  const cancel = useThreadStore((s) => s.cancel)
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  const busy = t.turnId != null

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }, [text])

  const submit = () => {
    const v = text.trim()
    if (!v || disabledReason) return
    setText('')
    void onSend(v)
  }

  return (
    <div className="mx-auto w-full max-w-[44rem] px-4 pb-4 pt-2">
      {t.error && !busy && <p className="mb-2 text-xs text-status-error">✕ {t.error}</p>}
      {t.queued && <p className="mb-2 text-xs text-[var(--text-meta)]">Waiting for the previous reply to finish…</p>}
      <div className="rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-raised)] p-2.5 focus-within:border-[var(--border-default)] focus-within:ring-2 focus-within:ring-brand/55">
        <textarea
          ref={ref}
          value={text}
          rows={1}
          disabled={Boolean(disabledReason)}
          aria-label="Message"
          placeholder={disabledReason ?? placeholder}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              submit()
            }
          }}
          className="block max-h-[180px] w-full resize-none bg-transparent px-1.5 py-1 text-[0.9375rem] leading-[1.6] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-meta)]"
        />
        <div className="mt-1 flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-xs text-[var(--text-meta)]">{note}</span>
          {busy ? (
            <Button type="button" variant="outline" size="sm" onClick={() => cancel(threadId)} aria-label="Stop the reply">
              <Square className="size-3" /> Stop
            </Button>
          ) : (
            <Button type="button" variant="brand" size="icon-sm" disabled={!text.trim() || Boolean(disabledReason)} onClick={submit} aria-label="Send">
              <ArrowUp className="size-4" />
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * One continuous thread: messages, event rows and stateful cards, a composer at the bottom.
 * History loads newest-first behind skeletons; older items load on demand. Cards only ever
 * show what the backend has already done — they re-render when it appends a new revision.
 */
export function Conversation({
  threadId,
  empty,
  placeholder = 'Message Conxa',
  composerNote,
  disabledReason,
  anchor,
  virtual,
  tail,
  onSend,
}: {
  threadId: string
  empty?: ReactNode
  placeholder?: string
  composerNote?: ReactNode
  disabledReason?: string
  anchor?: string
  /** A thread that doesn't exist yet (a brand-new chat): nothing to load, `onSend` creates it. */
  virtual?: boolean
  /** Rendered after the last item — e.g. the next step the lifecycle is waiting on. */
  tail?: ReactNode
  /** Replace the default send (e.g. a chat that must create its thread first). */
  onSend?: (text: string) => void | Promise<void>
}) {
  const t = useThreadStore(selectThread(threadId))
  const offline = useConnectionStore((c) => c.cloud === 'offline')
  const load = useThreadStore((s) => s.load)
  const loadOlder = useThreadStore((s) => s.loadOlder)
  const send = useThreadStore((s) => s.send)
  const scroller = useRef<HTMLDivElement>(null)
  const [pinned, setPinned] = useState(true)

  useEffect(() => {
    if (!virtual) void load(threadId)
  }, [threadId, load, virtual])

  const items = useMemo(() => visibleItems(t.items), [t.items])

  const toBottom = useCallback(() => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  // Follow new content only while the person is at the bottom; otherwise offer "Jump to latest".
  useLayoutEffect(() => {
    if (pinned) toBottom()
  }, [items.length, t.streaming?.text, pinned, toBottom])

  const onScroll = () => {
    const el = scroller.current
    if (!el) return
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
    if (el.scrollTop < 40 && t.hasMore) void loadOlder(threadId)
  }

  const ready = t.loaded || Boolean(virtual)
  const showEmpty = ready && items.length === 0 && !t.streaming

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={scroller} onScroll={onScroll} className="relative min-h-0 flex-1 overflow-y-auto" role="log" aria-live="polite" aria-label="Conversation">
        <div className="mx-auto w-full max-w-[44rem] space-y-5 px-4 py-6">
          {t.hasMore && (
            <button
              type="button"
              onClick={() => loadOlder(threadId)}
              className="mx-auto block rounded-[8px] px-3 py-1 text-xs text-[var(--text-meta)] hover:bg-white/[0.04] hover:text-[var(--text-primary)]"
            >
              {t.loadingOlder ? 'Loading earlier…' : 'Earlier · load more'}
            </button>
          )}
          {!ready && <ThreadSkeleton />}
          {showEmpty && (empty ?? <p className="text-sm text-[var(--text-meta)]">Nothing here yet.</p>)}
          {items.map((item) => (
            <ItemView key={item.card_id ?? item.id} threadId={threadId} item={item} />
          ))}
          {tail}
          {t.streaming && <AssistantMessage text={t.streaming.text} streaming />}
          {t.turnId && !t.streaming && <AssistantMessage text="" streaming />}
        </div>
        {!pinned && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={cn('sticky bottom-3 left-1/2 z-10 -translate-x-1/2 bg-[var(--bg-raised)]')}
            onClick={() => {
              toBottom()
              setPinned(true)
            }}
          >
            <ArrowDown className="size-3.5" /> Jump to latest
          </Button>
        )}
      </div>
      <Composer
        threadId={threadId}
        placeholder={placeholder}
        note={composerNote}
        disabledReason={disabledReason ?? (offline ? 'You’re offline — replies need Conxa Cloud. Try again when the connection is back.' : undefined)}
        onSend={onSend ?? ((text) => send(threadId, text, anchor))}
      />
    </div>
  )
}
