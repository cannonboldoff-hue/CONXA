import { useEffect, useState } from 'react'
import {
  flowDiscardRecording,
  flowFinishRecording,
  flowPauseRecording,
  flowResumeRecording,
  flowSignInCancel,
  flowSignInDone,
} from '@/api/conversationApi'
import { Button } from '@/components/ui/button'
import { CardShell } from '@/components/conversation/parts'
import type { CardProps } from '@/components/conversation/cards/ChangeAndDraftCards'
import { useFlowAction } from '@/components/conversation/cards/useFlowAction'
import { selectSession, useRecordingStore } from '@/store/recordingStore'
import { useBrowserStore, type FlowControls } from '@/store/browserStore'
import { stepTypeColor, type StatusKind } from '@/lib/status'
import { cn } from '@/lib/utils'

const workflowIdOf = (threadId: string) => threadId.replace(/^wf:/, '')

type Refs = { session_id?: string; embedded?: boolean } | undefined

/** While a card's sign-in or recording lives in Studio's browser panel, mirror its controls there. */
function usePanelControls(controls: FlowControls | null) {
  const setFlow = useBrowserStore((s) => s.setFlow)
  const clearFlow = useBrowserStore((s) => s.clearFlow)
  const key = controls ? JSON.stringify(controls) : ''
  useEffect(() => {
    if (!controls) return
    setFlow(controls)
    return () => clearFlow(controls.cardId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setFlow, clearFlow])
}

/** A step-type chip: 1px border in the action's category colour, 4px radius, 11px text. */
export function KindChip({ kind }: { kind: string }) {
  const color = stepTypeColor(kind)
  return (
    <span className="shrink-0 rounded-[4px] border px-1.5 py-px text-[0.6875rem] font-medium capitalize leading-4" style={{ borderColor: color, color }}>
      {kind}
    </span>
  )
}

/** Sign in to an app the workflow uses. Nothing is recorded until the sign-in is confirmed — by
 *  itself once the person is in (the backend watches the sign-in tab), or by the button. */
export function SignInCard({ threadId, item }: CardProps) {
  const wf = workflowIdOf(threadId)
  const { busy, run } = useFlowAction(wf)
  const state = String(item.state)
  const note = typeof item.note === 'string' ? item.note : ''
  const app = String(item.app_name ?? 'the app')
  const glyph: StatusKind = state === 'saved' ? 'ok' : state === 'waiting' ? 'warn' : 'none'
  const refs = item.refs as Refs
  usePanelControls(
    state === 'waiting' && refs?.embedded && refs.session_id
      ? { runId: refs.session_id, workflowId: wf, cardId: String(item.card_id), kind: 'signin', state, appName: app, note, canForce: item.can_force === true }
      : null,
  )

  return (
    <CardShell
      glyph={glyph}
      title={state === 'saved' ? `Signed in to ${app}` : `Sign in to ${app}`}
      meta={state === 'saved' ? 'Saved for the whole group' : state === 'cancelled' ? 'Cancelled' : 'Sign-in only — recording starts by itself once you’re signed in'}
      footer={
        state === 'waiting' ? (
          <>
            <Button variant="brand" size="sm" disabled={busy} onClick={() => run(() => flowSignInDone(wf, String(item.card_id)))}>
              I’m signed in
            </Button>
            {item.can_force === true && (
              <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowSignInDone(wf, String(item.card_id), true))}>
                Save anyway
              </Button>
            )}
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(() => flowSignInCancel(wf, String(item.card_id)))}>
              Cancel
            </Button>
          </>
        ) : undefined
      }
    >
      {state === 'waiting' && (
        <>
          <p className="font-mono text-xs text-[var(--text-meta)]">{String(item.login_url ?? '')}</p>
          {note && <p className="mt-2 text-status-warn">▲ {note}</p>}
        </>
      )}
    </CardShell>
  )
}

function useElapsed(startedAt: unknown, running: boolean) {
  const start = typeof startedAt === 'number' ? startedAt * 1000 : Date.now()
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [running])
  const s = Math.max(0, Math.floor((now - start) / 1000))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

/** The live recording: what has been captured so far, and the controls. */
export function RecordingCard({ threadId, item }: CardProps) {
  const wf = workflowIdOf(threadId)
  const cardId = String(item.card_id)
  const refs = item.refs as Refs
  const sessionId = String(refs?.session_id ?? '')
  const { busy, run } = useFlowAction(wf)
  const state = String(item.state)
  const live = state === 'recording' || state === 'paused'
  const session = useRecordingStore(selectSession(sessionId))
  const hydrate = useRecordingStore((s) => s.hydrate)
  const watch = useRecordingStore((s) => s.watch)
  const setVaries = useRecordingStore((s) => s.setVaries)
  const elapsed = useElapsed(item.started_at, state === 'recording')

  useEffect(() => {
    if (!sessionId || !(live || state === 'browser_closed')) return
    void hydrate(sessionId)
    return watch(sessionId)
  }, [sessionId, live, state, hydrate, watch])

  const count = live ? Math.max(session.count, session.rows.length) : Number(item.count ?? session.count ?? 0)
  const last = session.rows[session.rows.length - 1]
  const glyph: StatusKind = state === 'saved' ? 'ok' : live ? 'run' : state === 'browser_closed' ? 'warn' : 'none'
  const warnings = Array.isArray(item.warnings) ? (item.warnings as string[]) : []
  const inPanel = refs?.embedded === true
  usePanelControls(
    live && inPanel && sessionId
      ? { runId: sessionId, workflowId: wf, cardId, kind: 'record', state, startedAt: typeof item.started_at === 'number' ? item.started_at : undefined, count, last: last?.text }
      : null,
  )

  const title =
    state === 'saved' ? 'Recording saved'
    : state === 'discarded' ? 'Recording discarded'
    : state === 'browser_closed' ? 'The browser closed before you finished'
    : state === 'saving' ? 'Saving the recording…'
    : state === 'paused' ? 'Recording paused'
    : 'Recording'
  const meta =
    state === 'saved' ? `${plural(count, 'action')}${Number(item.duration_seconds) ? ` · ${Math.floor(Number(item.duration_seconds) / 60)} min ${String(Math.floor(Number(item.duration_seconds) % 60)).padStart(2, '0')} s` : ''}`
    : state === 'browser_closed' ? `${plural(count, 'action')} were captured. Save them if the task was complete, or start over.`
    : live ? `${elapsed} · ${plural(count, 'action')} captured`
    : state === 'discarded' ? (typeof item.note === 'string' && item.note ? item.note : 'Nothing was saved.')
    : undefined

  return (
    <CardShell
      glyph={glyph}
      title={title}
      meta={meta}
      footer={
        live ? (
          <>
            <Button variant="brand" size="sm" disabled={busy || count === 0} onClick={() => run(() => flowFinishRecording(wf, cardId))}>
              Finish and save
            </Button>
            {state === 'recording' ? (
              <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowPauseRecording(wf, cardId))}>Pause</Button>
            ) : (
              <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowResumeRecording(wf, cardId))}>Resume</Button>
            )}
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(() => flowDiscardRecording(wf, cardId))}>Discard</Button>
          </>
        ) : state === 'browser_closed' ? (
          <>
            {count > 0 && (
              <Button variant="brand" size="sm" disabled={busy} onClick={() => run(() => flowFinishRecording(wf, cardId))}>
                Save {plural(count, 'action')}
              </Button>
            )}
            <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowDiscardRecording(wf, cardId))}>Discard</Button>
          </>
        ) : undefined
      }
    >
      {live && (
        <>
          <p className="text-xs text-[var(--text-meta)]">
            {state === 'paused'
              ? 'Paused — nothing you do in the browser is recorded.'
              : `Recording everything you do in ${inPanel ? 'the browser on the right' : 'the browser window'}${item.host ? ` · ${String(item.host)}` : ''}`}
          </p>
          {last && <p className="mt-1 text-xs text-[var(--text-secondary)]">Last: {last.text}</p>}
          {warnings.map((w) => <p key={w} className="mt-1 text-xs text-status-warn">▲ {w}</p>)}
          <h4 className="mb-1.5 mt-4 text-[0.6875rem] font-medium text-[var(--text-meta)]">Captured so far · {count}</h4>
          {session.rows.length === 0 ? (
            <p className="text-xs text-[var(--text-meta)]">Nothing yet — do the first step in the browser.</p>
          ) : (
            <ol className="max-h-64 space-y-1 overflow-y-auto rounded-[8px] bg-[var(--bg-raised)] p-2">
              {session.rows.map((r) => {
                const on = session.varies[String(r.sequence)] === true
                return (
                  <li key={r.sequence} className="flex items-center gap-2 text-xs">
                    <span className="w-5 shrink-0 text-right tabular-nums text-[var(--text-meta)]">{r.sequence}</span>
                    <KindChip kind={r.kind} />
                    <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">{r.text}</span>
                    {r.can_vary && r !== last && (
                      <button
                        type="button"
                        aria-pressed={on}
                        onClick={() => setVaries(sessionId, r.sequence, !on)}
                        className={cn(
                          'shrink-0 rounded-[6px] border px-1.5 py-0.5 text-[0.6875rem] outline-none focus-visible:ring-2 focus-visible:ring-brand/55',
                          on ? 'border-white/25 bg-white/[0.07] text-[var(--text-primary)]' : 'border-[var(--border-default)] text-[var(--text-meta)] hover:text-[var(--text-primary)]',
                        )}
                      >
                        {on ? 'Changes each run' : 'Changes each run?'}
                      </button>
                    )}
                  </li>
                )
              })}
            </ol>
          )}
          <p className="mt-2 text-xs text-[var(--text-meta)]">Mark a typed value “Changes each run” and Conxa won’t need to ask about it later.</p>
        </>
      )}
    </CardShell>
  )
}
