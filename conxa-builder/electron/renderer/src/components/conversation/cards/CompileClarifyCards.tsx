import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { flowTestPrepare } from '@/api/conversationApi'
import { Button } from '@/components/ui/button'
import { CardShell } from '@/components/conversation/parts'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import type { CardProps } from '@/components/conversation/cards/ChangeAndDraftCards'
import { useThreadStore } from '@/store/threadStore'
import { useConnectionStore } from '@/store/connectionStore'
import { useShellData } from '@/hooks/useShellData'
import { cn } from '@/lib/utils'
import type { StatusKind } from '@/lib/status'

const workflowIdOf = (threadId: string) => threadId.replace(/^wf:/, '')
const BILLING_URL = 'https://www.conxa.in/billing'

/** Open Human Edit for this workflow's compiled skill (and come back to the conversation). */
export function useOpenEdit(threadId: string) {
  const navigate = useNavigate()
  const { workflowsById } = useShellData()
  const wf = workflowIdOf(threadId)
  const skillId = workflowsById.get(wf)?.workflow.skill_id
  return skillId ? () => navigate(`/edit/${skillId}?from=${encodeURIComponent(`/w/${wf}`)}`) : null
}

type StageRow = { key: string; label: string; status: 'pending' | 'running' | 'done' | 'failed'; detail: string }

const STAGE_GLYPH: Record<StageRow['status'], StatusKind> = { pending: 'none', running: 'run', done: 'ok', failed: 'fail' }

/** Compile progress: real stages from the compiler's own events — never an estimated bar. */
export function CompileCard({ threadId, item }: CardProps) {
  const act = useThreadStore((s) => s.act)
  const navigate = useNavigate()
  const offline = useConnectionStore((c) => c.cloud === 'offline')
  const waitingTitle = offline ? 'Waiting for Conxa Cloud — you’re offline' : undefined
  const [showDetails, setShowDetails] = useState(false)
  const state = String(item.state)
  const rows = (Array.isArray(item.rows) ? item.rows : []) as StageRow[]
  const credits = typeof item.credits_left === 'number' ? item.credits_left : null
  const error = typeof item.error === 'string' ? item.error : ''
  const run = (action: string) => act(threadId, String(item.card_id), action)

  if (state === 'queued') {
    return (
      <CardShell
        glyph="run"
        title="Next in line"
        meta={item.position ? `Position ${String(item.position)}` : undefined}
        footer={item.ahead_workflow_id ? <Button variant="outline" size="sm" onClick={() => navigate(`/w/${String(item.ahead_workflow_id)}`)}>Open the other one</Button> : undefined}
      >
        “{String(item.ahead_name ?? 'Another workflow')}” is compiling. Only one compiles at a time — yours starts automatically after it.
      </CardShell>
    )
  }
  if (state === 'out_of_credits') {
    return (
      <CardShell
        glyph="warn"
        title="No compile credits left"
        footer={
          <>
            <Button variant="brand" size="sm" onClick={() => void window.conxa.openExternal(BILLING_URL)}>Add credits</Button>
            <Button variant="ghost" size="sm" onClick={() => run('compile_later')}>Compile later</Button>
          </>
        }
      >
        Your recording is saved. Compiling resumes when credits are added — nothing is lost.
      </CardShell>
    )
  }
  if (state === 'failed') {
    return (
      <CardShell
        glyph="fail"
        title="Compile didn’t finish"
        footer={
          <>
            <Button variant="outline" size="sm" disabled={offline} title={waitingTitle} onClick={() => run('retry')}>Try again</Button>
            <Button variant="ghost" size="sm" onClick={() => setShowDetails((v) => !v)}>{showDetails ? 'Hide details' : 'Show details'}</Button>
          </>
        }
      >
        <p>Your recording is safe{item.refunded ? ' and no credit was used.' : '.'}</p>
        {showDetails && <p className="mt-2 font-mono text-xs text-[var(--text-meta)]">{error || 'No details were reported.'}</p>}
      </CardShell>
    )
  }
  if (state === 'deferred') {
    return (
      <CardShell glyph="none" title="Compile later" footer={<Button variant="brand" size="sm" disabled={offline} title={waitingTitle} onClick={() => run('retry')}>Compile now</Button>}>
        The recording is saved. Compile it whenever you’re ready.
      </CardShell>
    )
  }
  if (state === 'dismissed') return null

  const running = state === 'running'
  return (
    <CardShell
      glyph={running ? 'run' : 'ok'}
      title={running ? 'Compiling' : 'Compiled'}
      meta={running ? `usually under 2 minutes${credits != null ? ` · Uses 1 compile credit · ${credits} left` : ''}` : credits != null ? `${credits} compile credits left` : undefined}
    >
      <ol className="space-y-1.5">
        {rows.map((r) => (
          <li key={r.key} className="flex items-start gap-2.5">
            <span className="mt-[3px]"><StatusGlyph kind={STAGE_GLYPH[r.status]} label={r.status} /></span>
            <span className={cn('min-w-0', r.status === 'pending' && 'text-[var(--text-meta)]')}>
              <span className={r.status === 'pending' ? '' : 'text-[var(--text-primary)]'}>{r.label}</span>
              {r.detail && <span className="block text-xs text-[var(--text-meta)]">{r.detail}</span>}
            </span>
          </li>
        ))}
      </ol>
      {running && <p className="mt-3 text-xs text-[var(--text-meta)]">You can leave this screen — the sidebar shows when it’s done.</p>}
    </CardShell>
  )
}

type Choice = { id: string; label: string; detail?: string; selected?: boolean }

/** One Clarify question. It only shows an answer as applied once the backend has finished applying it. */
export function ClarifyQuestionCard({ threadId, item }: CardProps) {
  const act = useThreadStore((s) => s.act)
  const openEdit = useOpenEdit(threadId)
  const choices = (Array.isArray(item.choices) ? item.choices : []) as Choice[]
  const multi = item.multi === true
  const state = String(item.state)
  const [single, setSingle] = useState(choices[0]?.id ?? '')
  const [picked, setPicked] = useState<Set<string>>(() => new Set(choices.filter((c) => c.selected).map((c) => c.id)))
  const [busy, setBusy] = useState(false)
  const qKind = String(item.question_kind ?? '')
  const title = String(item.title ?? '')
  const receipt = typeof item.receipt === 'string' ? item.receipt : ''

  const send = async (action: string, payload?: Record<string, unknown>) => {
    setBusy(true)
    const r = await act(threadId, String(item.card_id), action, payload)
    setBusy(false)
    if (r && r.ok === false && r.message) toast.error(r.message)
  }

  if (state === 'applying') {
    return <div className="flex items-center gap-2.5 text-sm"><StatusGlyph kind="run" /><span className="text-[var(--text-primary)]">{title}</span><span className="text-[var(--text-meta)]">— applying…</span></div>
  }
  if (state === 'answered' || state === 'skipped') {
    const keys = Array.isArray(item.step_keys) ? item.step_keys : []
    return (
      <div className="flex items-start gap-2.5 text-sm">
        <span className="mt-[3px]"><StatusGlyph kind={state === 'answered' ? 'ok' : 'none'} /></span>
        <div className="min-w-0 flex-1">
          <p className="text-[var(--text-primary)]">{title}</p>
          <p className="text-xs text-[var(--text-meta)]">{receipt || (state === 'skipped' ? 'Kept as compiled' : '')}</p>
        </div>
        {state === 'answered' && keys.length > 0 && openEdit && (
          <button
            type="button"
            className="shrink-0 text-xs text-[var(--text-secondary)] underline-offset-2 hover:underline"
            onClick={openEdit}
            title="Open Human Edit to change this"
          >
            Change
          </button>
        )}
      </div>
    )
  }

  const count = picked.size
  const primary =
    qKind === 'values' ? (count ? `Make ${count} input${count === 1 ? '' : 's'}` : 'Keep all as recorded')
    : qKind === 'done_when' ? (count ? `Use ${count} check${count === 1 ? '' : 's'}` : 'No required checks')
    : 'Use this'

  return (
    <CardShell
      glyph="warn"
      title={title}
      meta={`Question ${String(item.index)} of ${String(item.total)}`}
      footer={
        <>
          <Button
            variant="brand"
            size="sm"
            disabled={busy || (!multi && !single)}
            onClick={() => send('choose', multi ? { selected: [...picked] } : { choice: single })}
          >
            {primary}
          </Button>
          {multi && qKind === 'values' && count > 0 && (
            <Button variant="outline" size="sm" disabled={busy} onClick={() => send('choose', { selected: [] })}>Keep all as recorded</Button>
          )}
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => send('skip')}>Not now</Button>
          {state === 'failed' && typeof item.error === 'string' && <span className="basis-full text-xs text-status-error">✕ {item.error}</span>}
        </>
      }
    >
      {item.body ? <p className="mb-3 leading-[1.6]">{String(item.body)}</p> : null}
      <ul className="space-y-1.5" role={multi ? 'group' : 'radiogroup'} aria-label={title}>
        {choices.map((c) => {
          const on = multi ? picked.has(c.id) : single === c.id
          return (
            <li key={c.id}>
              <label className={cn('flex cursor-pointer items-start gap-2.5 rounded-[8px] border px-3 py-2', on ? 'border-white/25 bg-white/[0.07]' : 'border-[var(--border-default)] hover:bg-white/[0.04]')}>
                <input
                  type={multi ? 'checkbox' : 'radio'}
                  name={String(item.card_id)}
                  className="mt-1 accent-white"
                  checked={on}
                  onChange={() => {
                    if (!multi) return setSingle(c.id)
                    setPicked((prev) => {
                      const next = new Set(prev)
                      if (next.has(c.id)) next.delete(c.id)
                      else next.add(c.id)
                      return next
                    })
                  }}
                />
                <span className="min-w-0">
                  <span className="block text-[var(--text-primary)]">{c.label}</span>
                  {c.detail && <span className="block text-xs text-[var(--text-meta)]">{c.detail}</span>}
                </span>
              </label>
            </li>
          )
        })}
      </ul>
      <p className="mt-2 text-xs text-[var(--text-meta)]">Or answer in your own words in the box below.</p>
    </CardShell>
  )
}

type Summary = {
  name: string
  version: number
  recorded_actions: number
  steps: number
  added_steps: number
  inputs: number
  uncertain: number
  step_names: string[]
  done_when: string[]
  goal?: string
  input_names?: string[]
  queued?: number
}

/** The workflow as it stands. The counts add up across screens: compiled + added = steps now. */
export function WorkflowSummaryCard({ threadId, item }: CardProps) {
  const openEdit = useOpenEdit(threadId)
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const s = item.summary as Summary
  const compiled = s.steps - s.added_steps
  const shown = s.step_names.slice(0, 6)
  const wf = workflowIdOf(threadId)

  const test = async () => {
    setBusy(true)
    try {
      await flowTestPrepare(wf)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
      qc.invalidateQueries({ queryKey: ['lifecycle', wf] })
    }
  }

  return (
    <CardShell
      title={s.name}
      meta={`version ${s.version}`}
      footer={
        <>
          <Button variant="brand" size="sm" disabled={busy} onClick={test}>Test it</Button>
          {s.uncertain > 0 && openEdit && (
            <Button variant="outline" size="sm" onClick={openEdit}>
              Review the {s.uncertain} uncertain step{s.uncertain === 1 ? '' : 's'}
            </Button>
          )}
        </>
      }
    >
      {s.goal && <p className="mb-2 text-[var(--text-primary)]">{s.goal}</p>}
      <ol className="space-y-1">
        {shown.map((n, i) => (
          <li key={i} className="flex gap-2"><span className="w-5 shrink-0 text-right tabular-nums text-[var(--text-meta)]">{i + 1}</span><span className="text-[var(--text-primary)]">{n}</span></li>
        ))}
        {s.step_names.length > shown.length && <li className="pl-7 text-xs text-[var(--text-meta)]">and {s.step_names.length - shown.length} more</li>}
      </ol>
      <p className="mt-3 text-xs text-[var(--text-meta)]">
        {s.steps} steps{s.added_steps ? ` (${compiled} compiled + ${s.added_steps} added)` : ''} · {s.inputs} input{s.inputs === 1 ? '' : 's'}
        {s.input_names && s.input_names.length ? ` (${s.input_names.join(', ')})` : ''}
        {s.done_when.length ? ` · Done when ${s.done_when.join(' and ')}` : ''}
        {s.queued ? ` · ${s.queued} more question${s.queued === 1 ? '' : 's'} when you want them` : ''}
      </p>
    </CardShell>
  )
}

/** Offer to record the other way a popup decision can go. Optional: skipping leaves the workflow as it is. */
export function ForkVariantCard({ threadId, item }: CardProps) {
  const act = useThreadStore((s) => s.act)
  const [busy, setBusy] = useState(false)
  const state = String(item.state)
  const title = String(item.title ?? 'the popup')

  const send = async (action: string) => {
    setBusy(true)
    const r = await act(threadId, String(item.card_id), action)
    setBusy(false)
    if (r && r.ok === false && r.message) toast.error(r.message)
  }

  if (state === 'skipped') {
    return <div className="flex items-center gap-2.5 text-sm"><StatusGlyph kind="none" /><span className="text-[var(--text-secondary)]">Didn’t record the case without “{title}”.</span></div>
  }
  if (state === 'recording') {
    return <div className="flex items-center gap-2.5 text-sm"><StatusGlyph kind="run" /><span className="text-[var(--text-primary)]">Recording the case without “{title}”…</span></div>
  }
  return (
    <CardShell
      glyph="warn"
      title={`Record what happens when “${title}” does NOT appear?`}
      meta="Optional"
      footer={
        <>
          <Button variant="brand" size="sm" disabled={busy} onClick={() => send('record')}>Record it now</Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => send('skip')}>Skip</Button>
        </>
      }
    >
      <p className="leading-[1.6]">
        First make it so the popup won’t show, for example by uploading a file that isn’t in the folder yet. I’ll compare it with your first
        recording, which stays exactly as it is. If you skip, the workflow just carries on when the popup doesn’t show.
      </p>
    </CardShell>
  )
}
