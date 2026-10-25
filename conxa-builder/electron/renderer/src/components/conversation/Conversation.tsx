import { type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, ShieldCheck, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AssistantMessage, EventRow, ThreadSkeleton, UserMessage } from '@/components/conversation/parts'
import { cardComponent } from '@/components/conversation/cards'
import { selectThread, useThreadStore } from '@/store/threadStore'
import { visibleItems, type ThreadItem } from '@/lib/thread'
import { groupByStage, STAGES } from '@/lib/stages'
import { StageDivider } from '@/components/conversation/StageBar'
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
  hero,
  prefill,
  onSend,
}: {
  threadId: string
  placeholder: string
  note?: ReactNode
  disabledReason?: string
  /** The larger, centred composer of an empty thread; its note sits beneath the box. */
  hero?: boolean
  /** Text to drop into the box (e.g. a starter idea); a new object each time it should apply. */
  prefill?: { text: string } | null
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

  useEffect(() => {
    if (!prefill) return
    setText(prefill.text)
    const el = ref.current
    if (el) {
      el.focus()
      el.setSelectionRange(prefill.text.length, prefill.text.length)
    }
  }, [prefill])

  const submit = () => {
    const v = text.trim()
    if (!v || disabledReason) return
    setText('')
    void onSend(v)
  }

  return (
    <div className={cn('mx-auto w-full max-w-[44rem]', hero ? 'px-0' : 'px-4 pb-4 pt-2')}>
      {t.error && !busy && <p className="mb-2 text-xs text-status-error">✕ {t.error}</p>}
      {t.queued && <p className="mb-2 text-xs text-[var(--text-meta)]">Waiting for the previous reply to finish…</p>}
      <div
        className={cn(
          'rounded-[14px] border border-[var(--border-default)] p-2.5 transition-[border-color,box-shadow] focus-within:border-brand focus-within:ring-[3px] focus-within:ring-brand/20',
          hero ? 'bg-[var(--bg-surface)]' : 'bg-[var(--bg-raised)]',
        )}
      >
        <textarea
          ref={ref}
          value={text}
          rows={hero ? 3 : 1}
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
          className={cn(
            'block max-h-[180px] w-full resize-none bg-transparent text-[0.9375rem] leading-[1.6] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-meta)]',
            hero ? 'min-h-[84px] px-2 pb-1 pt-1.5' : 'px-1.5 py-1',
          )}
        />
        <div className="mt-1 flex items-center gap-2">
          {hero ? (
            <span className="min-w-0 flex-1 truncate text-right text-xs text-[var(--text-meta)]">Enter to send · Shift+Enter for new line</span>
          ) : (
            <span className="min-w-0 flex-1 truncate text-xs text-[var(--text-meta)]">{note}</span>
          )}
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
      {hero && note && (
        <p className="mt-2.5 flex items-center justify-center gap-1.5 text-center text-xs text-[var(--text-meta)]">
          <ShieldCheck className="size-3.5 shrink-0" aria-hidden />
          <span>{note}</span>
        </p>
      )}
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
  stageDividers,
  centerWhenEmpty,
  emptyBelow,
  onSend,
}: {
  threadId: string
  empty?: ReactNode
  /** While the thread is empty, centre `empty` and a larger composer instead of pinning it to the bottom. */
  centerWhenEmpty?: boolean
  /** Shown under the centred composer; `fill` drops text into it. */
  emptyBelow?: (fill: (text: string) => void) => ReactNode
  placeholder?: string
  composerNote?: ReactNode
  disabledReason?: string
  anchor?: string
  /** A thread that doesn't exist yet (a brand-new chat): nothing to load, `onSend` creates it. */
  virtual?: boolean
  /** Rendered after the last item — e.g. the next step the lifecycle is waiting on. */
  tail?: ReactNode
  /** Break the thread into Record / Compile / Test sections with a heading each (a workflow's thread). */
  stageDividers?: boolean
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
  const pinnedRef = useRef(true)
  pinnedRef.current = pinned

  useEffect(() => {
    if (!virtual) void load(threadId)
  }, [threadId, load, virtual])

  const items = useMemo(() => visibleItems(t.items), [t.items])
  const groups = useMemo(() => groupByStage(items), [items])
  const hasItems = items.length > 0

  const toBottom = useCallback(() => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  // Follow new content only while the person is at the bottom; otherwise offer "Jump to latest".
  useLayoutEffect(() => {
    if (pinned) toBottom()
  }, [items.length, t.streaming?.text, pinned, toBottom])

  // A card that grows in place (a failure's details, a revised card) adds height without adding an item.
  useEffect(() => {
    const el = scroller.current?.firstElementChild
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      if (pinnedRef.current) toBottom()
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [toBottom, hasItems])

  const onScroll = () => {
    const el = scroller.current
    if (!el) return
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
    if (el.scrollTop < 40 && t.hasMore) void loadOlder(threadId)
  }

  const [prefill, setPrefill] = useState<{ text: string } | null>(null)
  const ready = t.loaded || Boolean(virtual)
  const showEmpty = ready && items.length === 0 && !t.streaming
  const composerProps = {
    threadId,
    placeholder,
    note: composerNote,
    disabledReason: disabledReason ?? (offline ? 'You’re offline — replies need Conxa Cloud. Try again when the connection is back.' : undefined),
    onSend: onSend ?? ((text: string) => send(threadId, text, anchor)),
  }

  if (centerWhenEmpty && showEmpty && !t.turnId) {
    return (
      <div className="h-full min-h-0 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[47.5rem] flex-col gap-9 px-6 pb-12 pt-[12vh]">
          {empty}
          <Composer {...composerProps} hero prefill={prefill} />
          {emptyBelow?.((text) => setPrefill({ text }))}
        </div>
      </div>
    )
  }

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
          {stageDividers
            ? groups.map((g, gi) => (
                <section key={`${g.stage}-${gi}`} className="space-y-5">
                  <StageDivider stage={g.stage} number={STAGES.indexOf(g.stage) + 1} />
                  {g.items.map((item) => (
                    <ItemView key={item.card_id ?? item.id} threadId={threadId} item={item} />
                  ))}
                </section>
              ))
            : items.map((item) => <ItemView key={item.card_id ?? item.id} threadId={threadId} item={item} />)}
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
      <Composer {...composerProps} />
    </div>
  )
}
