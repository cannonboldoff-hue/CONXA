import { useEffect, useState, type ReactNode } from 'react'
import { ChevronRight, Pencil } from 'lucide-react'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { Button } from '@/components/ui/button'
import { clockTime, type ThreadItem } from '@/lib/thread'
import type { StatusKind } from '@/lib/status'
import { cn } from '@/lib/utils'

/** The assistant's voice: prose for explaining. Anything with state is a card, not a message. */
export function AssistantMessage({ text, ts, streaming }: { text: string; ts?: string; streaming?: boolean }) {
  return (
    <div className="flex gap-3">
      <span
        aria-hidden
        className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-[8px] bg-white/[0.08] text-xs font-semibold text-[var(--text-primary)]"
      >
        C
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-xs text-[var(--text-meta)]">
          Conxa{ts ? ` · ${clockTime(ts)}` : ''}
          {streaming ? ' · replying…' : ''}
        </p>
        <p className="mt-0.5 whitespace-pre-wrap text-[0.9375rem] leading-[1.65] text-[var(--text-secondary)]">
          {text || (streaming ? <StillWorking /> : '…')}
        </p>
      </div>
    </div>
  )
}

/** Before the first words of a reply arrive (often several seconds): say what is happening and count up, so it never looks stuck. */
function StillWorking() {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => window.clearInterval(id)
  }, [])
  return (
    <span className="text-[var(--text-meta)]" role="status">
      Working on a reply{seconds >= 2 ? ` · ${seconds} s` : '…'}
    </span>
  )
}

/**
 * The person's own messages sit on the surface step, right-aligned. `onEdit` is given only to their last
 * message while nothing but a reply followed it: "Edit" changes the words and gets one new reply in place
 * of the old one.
 */
export function UserMessage({ text, ts, onEdit }: { text: string; ts?: string; onEdit?: (text: string) => void }) {
  const [draft, setDraft] = useState<string | null>(null)
  const editing = draft !== null && onEdit !== undefined
  const resend = () => {
    const v = (draft ?? '').trim()
    if (!v || !onEdit) return
    setDraft(null)
    if (v !== text.trim()) onEdit(v)
  }
  return (
    <div className="flex justify-end">
      <div className={cn('rounded-[10px] border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3.5 py-2.5', editing ? 'w-[85%]' : 'max-w-[85%]')}>
        {editing ? (
          <>
            <textarea
              value={draft}
              autoFocus
              rows={3}
              aria-label="Change your message"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  resend()
                }
                if (e.key === 'Escape') setDraft(null)
              }}
              className="block w-full resize-none bg-transparent text-[0.9375rem] leading-[1.65] text-[var(--text-primary)] outline-none"
            />
            <div className="mt-2 flex justify-end gap-2">
              <Button type="button" variant="ghost" size="sm" onClick={() => setDraft(null)}>Cancel</Button>
              <Button type="button" variant="brand" size="sm" disabled={!draft.trim()} onClick={resend}>Send</Button>
            </div>
          </>
        ) : (
          <p className="whitespace-pre-wrap text-[0.9375rem] leading-[1.65] text-[var(--text-primary)]">{text}</p>
        )}
        {!editing && (ts || onEdit) && (
          <p className="mt-1 flex items-center justify-end gap-2 text-[0.6875rem] text-[var(--text-meta)]">
            {onEdit && (
              <button
                type="button"
                onClick={() => setDraft(text)}
                className="inline-flex items-center gap-1 rounded px-1 hover:text-[var(--text-primary)]"
                title="Change this message and get a new reply in place of the old one"
              >
                <Pencil className="size-3" aria-hidden /> Edit
              </button>
            )}
            {ts && <span>{clockTime(ts)}</span>}
          </p>
        )}
      </div>
    </div>
  )
}

/** Where the person started a new conversation. Everything above stays; only Conxa's replies forget it. */
export function ConversationDivider({ ts }: { ts?: string }) {
  return (
    <div className="space-y-1 text-center text-xs text-[var(--text-meta)]">
      <div className="flex items-center gap-2.5">
        <span className="h-px flex-1 bg-[var(--border-subtle)]" aria-hidden />
        <span>New conversation{ts ? ` · ${clockTime(ts)}` : ''}</span>
        <span className="h-px flex-1 bg-[var(--border-subtle)]" aria-hidden />
      </div>
      <p className="text-[0.6875rem]">Conxa won’t use the messages above in its replies. Questions and changes above still work.</p>
    </div>
  )
}

/** The system's voice: one line, a glyph, a timestamp, threaded on a hairline. */
export function EventRow({ item }: { item: ThreadItem }) {
  const kind = (item.glyph ?? 'ok') as StatusKind
  const subject = typeof item.subject === 'string' ? item.subject : ''
  return (
    <div className="flex items-center gap-2.5 text-sm">
      <StatusGlyph kind={kind} />
      <span className="min-w-0 truncate text-[var(--text-primary)]">
        {item.text}
        {subject && <span className="text-[var(--text-meta)]"> · {subject}</span>}
      </span>
      <span className="h-px flex-1 bg-[var(--border-subtle)]" aria-hidden />
      <span className="shrink-0 text-[0.6875rem] text-[var(--text-meta)]">{clockTime(item.ts)}</span>
    </div>
  )
}

/** Frame shared by every card: surface step, hairline border, 10px radius, 16px padding. */
export function CardShell({
  glyph,
  lead,
  title,
  meta,
  children,
  footer,
  className,
}: {
  glyph?: StatusKind
  /** Quiet context read before the title, e.g. what Conxa noticed that led to the question. */
  lead?: ReactNode
  title: ReactNode
  meta?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  className?: string
}) {
  return (
    <section className={cn('min-w-0 rounded-[10px] border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4', className)}>
      <header className="flex items-start gap-2.5">
        {glyph && <span className="mt-[3px]"><StatusGlyph kind={glyph} /></span>}
        <div className="min-w-0 flex-1">
          {lead && <div className="mb-3 text-base leading-snug text-[var(--text-secondary)]">{lead}</div>}
          <h3 className="text-base font-semibold leading-snug text-[var(--text-primary)]">{title}</h3>
          {meta && <p className="mt-0.5 text-xs text-[var(--text-meta)]">{meta}</p>}
        </div>
      </header>
      {children && <div className="mt-3 min-w-0 text-sm text-[var(--text-secondary)]">{children}</div>}
      {footer && <footer className="mt-4 flex flex-wrap items-center gap-2">{footer}</footer>}
    </section>
  )
}

/** A closed-by-default "Technical details" / "Show exact change" row. One look for every disclosure in the thread. */
export function Disclosure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="group mt-3">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-3 transition-transform group-open:rotate-90" aria-hidden />
        {label}
      </summary>
      <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-[8px] bg-[var(--bg-raised)] p-3 font-mono text-xs leading-[1.6] text-[var(--text-secondary)]">{children}</pre>
    </details>
  )
}

/** Grey placeholder lines in the shape of messages — never a spinner over a blank page. */
export function ThreadSkeleton() {
  return (
    <div className="space-y-6" aria-hidden aria-label="Loading conversation">
      {[70, 45, 85].map((w, i) => (
        <div key={i} className="flex gap-3">
          <div className="size-6 shrink-0 animate-pulse rounded-[8px] bg-white/[0.06]" />
          <div className="flex-1 space-y-2">
            <div className="h-3 w-24 animate-pulse rounded bg-white/[0.05]" />
            <div className="h-4 animate-pulse rounded bg-white/[0.05]" style={{ width: `${w}%` }} />
          </div>
        </div>
      ))}
    </div>
  )
}
