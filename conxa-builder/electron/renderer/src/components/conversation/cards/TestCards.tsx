import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  flowApprove,
  flowAutofixResume,
  flowAutofixStop,
  flowTestDecision,
  flowSignInThenTest,
  flowTestPrepare,
  flowTestRun,
  flowTestStop,
} from '@/api/conversationApi'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { CardShell, Disclosure } from '@/components/conversation/parts'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { useBrowserStore } from '@/store/browserStore'
import { useOpenEdit } from '@/components/conversation/cards/CompileClarifyCards'
import type { CardProps } from '@/components/conversation/cards/ChangeAndDraftCards'
import type { StatusKind } from '@/lib/status'
import { cn } from '@/lib/utils'

const workflowIdOf = (threadId: string) => threadId.replace(/^wf:/, '')
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

/** Run a command; say so if it fails, and refresh what the shell shows either way. */
function useAction(threadId: string) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      toast.error(errMsg(e))
    } finally {
      setBusy(false)
      const wf = workflowIdOf(threadId)
      qc.invalidateQueries({ queryKey: ['lifecycle', wf] })
      qc.invalidateQueries({ queryKey: ['lifecycles'] })
      qc.invalidateQueries({ queryKey: ['workflows'] })
    }
  }
  return { busy, run }
}

type InputSpec = { id: string; label: string; type: string; options: string[]; required: boolean; value: string | number | boolean; suspect?: boolean }

/** Test inputs: fill them in the way a real request would, then run. Nothing is published by a test. */
export function TestInputsCard({ threadId, item }: CardProps) {
  const wf = workflowIdOf(threadId)
  const { busy, run } = useAction(threadId)
  const specs = (Array.isArray(item.inputs) ? item.inputs : []) as InputSpec[]
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(specs.map((s) => [s.id, String(s.value ?? '')])))
  const [guard, setGuard] = useState(item.guard !== false)
  const [showBrowser, setShowBrowser] = useState(item.show_browser !== false)
  const [tried, setTried] = useState(false)
  const panelAvailable = useBrowserStore((b) => b.available)
  const state = String(item.state)
  const irreversible = (Array.isArray(item.irreversible) ? item.irreversible : []) as string[]
  const count = Number(item.irreversible_count ?? irreversible.length)
  const missing = specs.filter((s) => s.required && !(values[s.id] ?? '').trim())

  if (state !== 'open') {
    return (
      <div className="flex items-center gap-2.5 text-sm">
        <StatusGlyph kind="none" />
        <span className="text-[var(--text-primary)]">Test inputs</span>
        <span className="text-[var(--text-meta)]">· {specs.map((s) => `${s.label}: ${values[s.id] || '—'}`).join(' · ')}</span>
      </div>
    )
  }

  return (
    <CardShell
      title="Test inputs"
      meta={`from this workflow’s ${plural(specs.length, 'input')} · version ${String(item.version ?? '')}`}
      footer={
        <>
          <Button
            variant="brand"
            size="sm"
            disabled={busy}
            onClick={() => (missing.length ? setTried(true) : run(() => flowTestRun(wf, String(item.card_id), values, guard, showBrowser)))}
          >
            Run test
          </Button>
          <span className="text-xs text-[var(--text-meta)]">Builds a fresh test copy first (a few seconds). Tests don’t publish anything.</span>
        </>
      }
    >
      <div className="space-y-3">
        {specs.length === 0 && <p className="text-[var(--text-meta)]">This workflow has no inputs to fill in.</p>}
        {specs.map((s) => {
          const bad = tried && s.required && !(values[s.id] ?? '').trim()
          return (
            <div key={s.id}>
              <label htmlFor={`ti-${s.id}`} className="text-sm text-[var(--text-primary)]">{s.label}</label>
              <span className="ml-2 text-xs text-[var(--text-meta)]">{s.type === 'date' ? 'Date' : s.options.length ? 'Choice' : 'Text'} · {s.required ? 'required' : 'optional'}</span>
              {s.options.length > 0 ? (
                <select
                  id={`ti-${s.id}`}
                  value={values[s.id] ?? ''}
                  onChange={(e) => setValues((v) => ({ ...v, [s.id]: e.target.value }))}
                  className="mt-1 h-9 w-full rounded-[10px] border border-[var(--border-default)] bg-[var(--bg-raised)] px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-brand/55"
                >
                  <option value="">—</option>
                  {s.options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <Input
                  id={`ti-${s.id}`}
                  value={values[s.id] ?? ''}
                  onChange={(e) => setValues((v) => ({ ...v, [s.id]: e.target.value }))}
                  aria-invalid={bad}
                  className="mt-1 font-mono text-sm"
                />
              )}
              {bad && <p className="mt-1 text-xs text-status-error">✕ Required — the workflow types this in.</p>}
              {s.suspect && <p className="mt-1 text-xs text-[var(--text-meta)]">The last test couldn’t find what this points to — check the value.</p>}
            </div>
          )
        })}

        <label className="flex cursor-pointer items-start gap-2.5 text-sm">
          <input type="checkbox" className="mt-1 accent-white" checked={showBrowser} onChange={(e) => setShowBrowser(e.target.checked)} />
          <span><span className="text-[var(--text-primary)]">Show the browser while it runs</span><span className="block text-xs text-[var(--text-meta)]">{panelAvailable ? 'Opens beside the conversation' : 'Opens in its own window'}</span></span>
        </label>

        {count > 0 && (
          <div className="rounded-[8px] border border-[var(--border-default)] p-3">
            <p className="flex items-start gap-2 text-sm text-[var(--text-primary)]">
              <StatusGlyph kind="warn" />
              <span>
                This is a real run. {count === 1 ? 'The last step' : `The last ${count} steps`}
                {irreversible.length ? <> — {irreversible.map((n) => `“${n}”`).join(', ')} —</> : null} can’t be undone.
              </span>
            </p>
            <label className="mt-2 flex cursor-pointer items-start gap-2.5 text-sm">
              <input type="checkbox" className="mt-1 accent-white" checked={guard} onChange={(e) => setGuard(e.target.checked)} />
              <span>
                <span className="text-[var(--text-primary)]">Ask before steps that can’t be undone</span>
                <span className="block text-xs text-[var(--text-meta)]">
                  {guard ? 'The test pauses before them so nothing real is sent without your OK.' : 'Runs straight through and does the real thing. Use data you control.'}
                </span>
              </span>
            </label>
          </div>
        )}
      </div>
    </CardShell>
  )
}

type Row = { index: number; name: string; status: 'running' | 'done' | 'failed'; destructive?: boolean }
const ROW_GLYPH: Record<Row['status'], StatusKind> = { running: 'run', done: 'ok', failed: 'fail' }

/** One test attempt, from start to result. Pauses, failures and passes are all states of this one card. */
export function TestRunCard({ threadId, item }: CardProps) {
  const wf = workflowIdOf(threadId)
  const { busy, run } = useAction(threadId)
  const state = String(item.state)
  const rows = (Array.isArray(item.rows) ? item.rows : []) as Row[]
  const attempt = Number(item.attempt ?? 1)
  const auto = item.auto === true
  const total = typeof item.step_total === 'number' ? item.step_total : null
  const cardId = String(item.card_id)
  const secs = Number(item.seconds ?? 0)
  const clock = secs ? `${Math.floor(secs / 60)} min ${String(secs % 60).padStart(2, '0')} s` : ''

  const rowList = (
    <ol className="space-y-1">
      {rows.map((r) => (
        <li key={r.index} className="flex items-start gap-2.5">
          <span className="mt-[3px]"><StatusGlyph kind={ROW_GLYPH[r.status]} /></span>
          <span className={cn('min-w-0', r.status === 'failed' ? 'text-status-error' : 'text-[var(--text-primary)]')}>{r.name}</span>
        </li>
      ))}
    </ol>
  )

  if (state === 'running') {
    return (
      <CardShell
        glyph="run"
        title={auto ? `Re-testing · attempt ${attempt}` : 'Test running'}
        meta={`${total ? `Step ${rows.length} of ${total}` : `${plural(rows.length, 'step')} so far`}${item.guard ? ' · pausing before steps that can’t be undone' : ''}`}
        footer={<Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowTestStop(wf))}>Stop test</Button>}
      >
        {rowList}
        <p className="mt-3 text-xs text-[var(--text-meta)]">Clicking in the page while it runs can throw the test off.</p>
      </CardShell>
    )
  }

  if (state === 'waiting_guard' || state === 'waiting_confirm') {
    const confirm = state === 'waiting_confirm'
    return (
      <CardShell
        glyph="warn"
        title={confirm ? 'A person needs to confirm' : `Paused before “${String(item.paused_before ?? 'a step')}”`}
        meta={confirm ? undefined : `${plural(Number(item.ran ?? 0), 'step')} passed`}
        footer={
          <>
            <Button variant="brand" size="sm" disabled={busy} onClick={() => run(() => flowTestDecision(wf, confirm ? 'yes' : 'run_rest'))}>
              {confirm ? 'Yes, go ahead' : item.remaining ? `Run the last ${String(item.remaining)} for real` : 'Run it for real'}
            </Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowTestDecision(wf, 'end_here'))}>End the test here</Button>
          </>
        }
      >
        {confirm
          ? <p>{String(item.prompt ?? 'The workflow asks for a person’s OK before a step that can’t be undone.')}</p>
          : <p>The next steps can’t be undone, so the re-test waits for you. Nothing has been sent or changed yet.</p>}
        <div className="mt-3">{rowList}</div>
      </CardShell>
    )
  }

  if (state === 'waiting_fork') {
    // A popup the workflow can't decide for itself (an unreviewed Replace/Delete, "ask me", or one it
    // was never taught). Only a person answers this; the choices are exactly what the page offered.
    const fork = (item.fork ?? {}) as { title?: string; message?: string; suggested?: string | null; choices?: { id: string; label: string; role?: string }[] }
    const choices = (fork.choices ?? []).filter((c) => c.id !== '__stop__')
    return (
      <CardShell
        glyph="warn"
        title={`A popup needs your decision${fork.title ? `: “${fork.title}”` : ''}`}
        meta={`${plural(rows.filter((r) => r.status === 'done').length, 'step')} passed`}
        footer={
          <>
            {choices.map((c) => (
              <Button
                key={c.id}
                variant={c.role === 'consequential' ? 'outline' : 'brand'}
                size="sm"
                disabled={busy}
                onClick={() => run(() => flowTestDecision(wf, `fork:${c.id}`))}
              >
                {c.label}{c.id === fork.suggested ? ' (what you chose when recording)' : ''}
              </Button>
            ))}
            <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowTestDecision(wf, 'end_here'))}>End the test here</Button>
          </>
        }
      >
        {fork.message ? <p>{fork.message}</p> : null}
        <p className="mt-2 text-xs text-[var(--text-secondary)]">The test is paused on the page. Nothing has been changed yet.</p>
        <div className="mt-3">{rowList}</div>
      </CardShell>
    )
  }

  if (state === 'failed') {
    return (
      <CardShell
        glyph="fail"
        title={`${auto ? `Attempt ${attempt}` : 'Test'} failed${item.failed_step ? ` at “${String(item.failed_step)}”` : ''}`}
        meta={clock || undefined}
      >
        <p className="leading-[1.6] text-[var(--text-primary)]">{String(item.summary || 'Something went wrong at this step.')}</p>
        {typeof item.failure_image === 'string' && item.failure_image && (
          <img
            src={item.failure_image}
            alt="The page at the moment the step failed"
            className="mt-3 max-h-56 w-full rounded-[8px] border border-[var(--border-subtle)] object-contain object-top"
          />
        )}
        <div className="mt-3">{rowList}</div>
        {item.nothing_sent === true && <p className="mt-2 text-xs text-[var(--text-secondary)]">Nothing was saved or sent.</p>}
        {item.error ? <Disclosure label="Technical details">{String(item.error)}</Disclosure> : null}
      </CardShell>
    )
  }

  if (state === 'passed') {
    const approved = item.approved === true
    return (
      <CardShell
        glyph="ok"
        title={approved ? 'Approved' : 'Test passed'}
        meta={approved ? `by you${item.approved_version ? ` · version ${String(item.approved_version)}` : ''}` : `${plural(Number(item.steps_ran ?? rows.length), 'step')}${clock ? ` · ${clock}` : ''}`}
        footer={
          <>
            {!approved && (
              <Button variant="brand" size="sm" disabled={busy} onClick={() => run(() => flowApprove(wf, cardId))}>Approve for packaging</Button>
            )}
            <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowTestPrepare(wf))}>Test with other inputs</Button>
          </>
        }
      >
        {item.irreversible_ran === true && (
          <p className="flex items-start gap-2 text-status-warn"><StatusGlyph kind="warn" /><span className="text-[var(--text-secondary)]">Because you let the last steps run, this test did the real thing. Remove it in the app if it was test data.</span></p>
        )}
        {typeof item.outputs === 'object' && item.outputs !== null && Object.keys(item.outputs as Record<string, string>).length > 0 && (
          <div className="mt-2">
            <p className="text-xs text-[var(--text-meta)]">What the run read back</p>
            <ul className="mt-1 space-y-0.5">
              {Object.entries(item.outputs as Record<string, string>).map(([k, v]) => (
                <li key={k} className="text-[var(--text-secondary)]"><span className="text-[var(--text-primary)]">{k}</span>: {v}</li>
              ))}
            </ul>
          </div>
        )}
        {approved && typeof item.build_error === 'string' && item.build_error && <p className="mt-2 text-xs text-status-warn">▲ Approved, but packaging it failed: {item.build_error}</p>}
        {approved && <p className="mt-1 text-xs text-[var(--text-meta)]">Approving doesn’t publish anything on its own.</p>}
      </CardShell>
    )
  }

  if (state === 'ended_before_irreversible') {
    return (
      <CardShell
        glyph="none"
        title={`Passed up to “${String(item.stopped_before ?? 'the last steps')}”`}
        meta="Not a full pass"
        footer={<Button variant="outline" size="sm" onClick={() => run(() => flowTestPrepare(wf))}>Run again</Button>}
      >
        You ended the test before the steps that can’t be undone{item.declined ? ' (you didn’t confirm)' : ''}. Nothing was sent. A workflow isn’t tested until it runs to the end.
      </CardShell>
    )
  }

  if (state === 'interrupted') {
    return (
      <CardShell
        glyph="warn"
        title="This test was interrupted"
        meta="Studio was closed or restarted while it ran"
        footer={<Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowTestPrepare(wf))}>Test again</Button>}
      >
        <p>It didn’t finish, and a paused test can’t be picked up again. If a step saved or sent something before this stopped, check the app.</p>
      </CardShell>
    )
  }

  if (state === 'stopped') {
    return (
      <CardShell
        glyph="none"
        title="Test stopped by you"
        footer={<Button variant="outline" size="sm" onClick={() => run(() => flowTestPrepare(wf))}>Run again</Button>}
      >
        <p>{plural(Number(item.steps_ran ?? rows.length), 'step')} ran{item.last_step ? `, up to “${String(item.last_step)}”` : ''}. The rest didn’t. If a step saved something, check the app.</p>
      </CardShell>
    )
  }

  if (state === 'needs_sign_in') {
    return (
      <CardShell
        glyph="warn"
        title="A sign-in expired during the test"
        footer={
          <>
            <Button variant="brand" size="sm" disabled={busy} onClick={() => run(() => flowSignInThenTest(wf))}>Sign in and test again</Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowTestPrepare(wf))}>Test again</Button>
          </>
        }
      >
        <p>
          The test paused because a saved sign-in stopped working — nothing in the workflow failed, and no automatic fix was tried.
          “Sign in and test again” signs in, then runs the same test with the same inputs.
        </p>
        {item.detail ? <p className="mt-1 text-xs text-[var(--text-meta)]">{String(item.detail)}</p> : null}
      </CardShell>
    )
  }

  if (state === 'previewed') {
    return (
      <CardShell
        glyph="warn"
        title="Preview finished"
        footer={<Button variant="outline" size="sm" onClick={() => run(() => flowTestPrepare(wf))}>Test again</Button>}
      >
        This test engine is older than the safety guard, so I previewed instead: steps that can’t be undone were checked but not run. A preview doesn’t count as a pass — update Build Studio to run a full test.
      </CardShell>
    )
  }
  return null
}

type Change = { card_id: string; title: string }

/** The auto-fix loop: finds the cause, makes one change, re-tests — up to three times, then asks. */
export function AutofixCard({ threadId, item }: CardProps) {
  const wf = workflowIdOf(threadId)
  const { busy, run } = useAction(threadId)
  const openEdit = useOpenEdit(threadId)
  const state = String(item.state)
  const changes = (Array.isArray(item.changes) ? item.changes : []) as Change[]
  const max = Number(item.max_attempts ?? 3)

  if (state === 'running') {
    return (
      <CardShell
        glyph="run"
        title="Fixing and re-testing"
        meta={`Attempt ${String(item.attempt ?? 1)} of ${max}${item.phase ? ` · ${String(item.phase)}` : ''}`}
        footer={<Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowAutofixStop(wf))}>Stop</Button>}
      >
        {changes.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-5">{changes.map((c) => <li key={c.card_id}>{c.title}</li>)}</ul>
        )}
      </CardShell>
    )
  }
  if (state === 'interrupted') {
    return <CardShell glyph="warn" title="Fixing was interrupted" meta="Studio was closed or restarted">Each change made so far is listed above with Undo.</CardShell>
  }
  if (state === 'passed') {
    return (
      <CardShell glyph="ok" title={`Test passed on attempt ${String(item.attempts ?? '')}`}>
        {changes.length ? `${plural(changes.length, 'change')} made — each is listed above with Undo.` : 'No changes were needed.'} Approving is still up to you.
      </CardShell>
    )
  }
  return (
    <CardShell
      glyph="none"
      title="Auto-fix stopped"
      footer={
        <>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(() => flowAutofixResume(wf))}>Resume auto-fix</Button>
          {openEdit && <Button variant="outline" size="sm" onClick={openEdit}>Fix it myself in Human Edit</Button>}
        </>
      }
    >
      {item.reason ? <p className="mb-1 text-[var(--text-primary)]">{String(item.reason)}</p> : null}
      <p>Changes made so far are kept and listed above. The workflow stays marked “needs a fix” until a test passes.</p>
    </CardShell>
  )
}
