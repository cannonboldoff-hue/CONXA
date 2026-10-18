import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { CardShell } from '@/components/conversation/parts'
import { useShellStore } from '@/store/shellStore'
import { useThreadStore } from '@/store/threadStore'
import type { StatusKind } from '@/lib/status'
import type { ThreadItem } from '@/lib/thread'

export type CardProps = { threadId: string; item: ThreadItem }

const asText = (v: unknown) => (v == null ? '' : typeof v === 'string' ? v : JSON.stringify(v))

/** A proposed change to the workflow: the exact change, Apply / Undo. Nothing is applied until Apply. */
export function PendingChangeCard({ threadId, item }: CardProps) {
  const act = useThreadStore((s) => s.act)
  const [note, setNote] = useState('')
  const state = String(item.state)
  const preview = (item.preview ?? null) as { before?: unknown; after?: unknown } | null
  const glyph: StatusKind =
    state === 'applied' ? 'ok' : state === 'applying' ? 'run' : state === 'failed' ? 'fail' : state === 'open' ? 'warn' : 'none'
  const run = async (action: string) => {
    const r = await act(threadId, String(item.card_id), action)
    if (r && r.ok === false) setNote(r.message ?? 'That didn’t work.')
  }

  return (
    <CardShell
      glyph={glyph}
      title={String(item.title ?? 'Proposed change')}
      meta={state === 'open' ? 'Not applied yet' : state === 'applied' ? (item.auto === true ? 'Applied automatically — you can undo it' : 'Applied') : state === 'undone' ? 'Undone' : state === 'dismissed' ? 'Dismissed' : undefined}
      footer={
        <>
          {state === 'open' && (
            <>
              <Button variant="brand" size="sm" onClick={() => run('apply')}>Apply</Button>
              <Button variant="ghost" size="sm" onClick={() => run('dismiss')}>Dismiss</Button>
            </>
          )}
          {state === 'applying' && <span className="text-xs text-[var(--text-meta)]">Applying…</span>}
          {state === 'applied' && <Button variant="outline" size="sm" onClick={() => run('undo')}>Undo</Button>}
          {state === 'failed' && <span className="text-xs text-status-error">✕ {asText(item.error) || 'Couldn’t apply'}</span>}
          {note && <span className="text-xs text-status-warn">▲ {note}</span>}
        </>
      }
    >
      {item.why ? <p className="leading-[1.6]">{asText(item.why)}</p> : null}
      {preview && (preview.before !== undefined || preview.after !== undefined) && (
        <div className="mt-2 space-y-1 rounded-[8px] bg-[var(--bg-raised)] p-2.5 font-mono text-xs">
          {preview.before !== undefined && <p className="text-[var(--text-meta)] line-through">{asText(preview.before)}</p>}
          {preview.after !== undefined && <p className="text-[var(--text-primary)]">{asText(preview.after)}</p>}
        </div>
      )}
    </CardShell>
  )
}

/** A workflow the chat suggests. Nothing is created until the person confirms. */
export function DraftWorkflowCard({ threadId, item }: CardProps) {
  const act = useThreadStore((s) => s.act)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const setDraft = useShellStore((s) => s.setNewWorkflowDraft)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const draft = (item.draft ?? {}) as { name?: string; group?: string; starting_page?: string; task?: string }
  const state = String(item.state)

  const finish = async (action: string) => {
    setBusy(true)
    setNote('')
    const r = await act(threadId, String(item.card_id), action)
    setBusy(false)
    if (!r) return
    if (r.open_form) {
      setDraft(r.open_form)
      navigate('/new')
      if (r.needs?.length) setNote(`Still needed: ${r.needs.map((n) => n.replace('_', ' ')).join(', ')}.`)
      return
    }
    if (r.ok && r.workflow_id) {
      qc.invalidateQueries({ queryKey: ['workflows'] })
      qc.invalidateQueries({ queryKey: ['groups'] })
      navigate(`/w/${r.workflow_id}`, { state: { startRecording: r.start_recording === true } })
    } else if (r.ok === false && r.message) {
      setNote(r.message)
    }
  }

  const rows: [string, string][] = [
    ['Name', draft.name ?? ''],
    ['Group', draft.group ?? ''],
    ['Starting page', draft.starting_page ?? ''],
    ['Task', draft.task ?? ''],
  ]
  return (
    <CardShell
      glyph={state === 'created' ? 'ok' : 'none'}
      title="Draft workflow"
      meta={state === 'open' ? 'Nothing is created until you confirm' : state === 'created' ? 'Created' : 'Dismissed'}
      footer={
        state === 'open' ? (
          <>
            <Button variant="brand" size="sm" disabled={busy} onClick={() => finish('create_and_record')}>Create and start recording</Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => finish('edit_details')}>Edit details first</Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => act(threadId, String(item.card_id), 'dismiss')}>Not now</Button>
            {note && <span className="text-xs text-status-warn">▲ {note}</span>}
          </>
        ) : undefined
      }
    >
      <dl className="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1.5">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-xs text-[var(--text-meta)]">{k}</dt>
            <dd className={v ? 'text-[var(--text-primary)]' : 'text-[var(--text-meta)]'}>{v || '—'}</dd>
          </div>
        ))}
      </dl>
    </CardShell>
  )
}

/** The cloud couldn't answer. The person's words are kept, so Try again is always safe. */
export function ErrorCard({ threadId, item }: CardProps) {
  const act = useThreadStore((s) => s.act)
  const send = useThreadStore((s) => s.send)
  const items = useThreadStore((s) => s.threads[threadId]?.items ?? [])
  const state = String(item.state)
  const retry = async () => {
    await act(threadId, String(item.card_id), 'retry')
    const last = [...items].reverse().find((i) => i.kind === 'user_message')
    if (last?.text) void send(threadId, String(last.text))
  }
  return (
    <CardShell
      glyph="fail"
      title={String(item.title ?? 'Something went wrong')}
      footer={state === 'failed' ? <Button variant="outline" size="sm" onClick={retry}>Try again</Button> : undefined}
    >
      {asText(item.body)}
    </CardShell>
  )
}

/** Fallback for a card type this build doesn't know — keeps the thread readable. */
export function UnknownCard({ item }: CardProps) {
  return <CardShell glyph="none" title={String(item.title ?? item.card_type ?? 'Card')}>{asText(item.body)}</CardShell>
}
