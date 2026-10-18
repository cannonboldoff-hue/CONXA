import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/contexts/AuthContext'
import { cmd } from '@/lib/ipc'
import type { StatusKind } from '@/lib/status'

type DepsStatus = {
  chromium: boolean
  runtime: boolean
  app: boolean
  all_ready: boolean
  versions?: Record<string, string | null | undefined>
}

const STEPS = [
  { n: 1, title: 'Record it once', body: 'Sign in and do the task in Chromium, like you normally would.' },
  { n: 2, title: 'Answer a few questions', body: 'Which values change, what counts as done, where a person must approve.' },
  { n: 3, title: 'Test, then approve', body: 'Watch it run with test inputs. Nothing is published until you say so.' },
]

function CheckRow({ kind, title, hint, trailing }: { kind: StatusKind; title: string; hint?: string; trailing?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 text-[0.8125rem]">
      <StatusGlyph kind={kind} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span>{title}</span>
        {hint && <span className="text-xs text-[var(--text-secondary)]">{hint}</span>}
      </span>
      {trailing}
    </div>
  )
}

/**
 * What a brand-new workspace sees instead of an empty list: what the three-step flow is, how to start,
 * and — honestly — whether this computer is ready (signed in, recording browser, test runner). A failing
 * check says what it needs and offers the fix; it never blocks the page.
 */
export function FirstLaunchPage() {
  const { identity } = useAuth()
  const qc = useQueryClient()
  const [installing, setInstalling] = useState(false)
  const [installError, setInstallError] = useState('')
  const deps = useQuery({ queryKey: ['deps-status'], queryFn: () => cmd<DepsStatus>('deps_status'), retry: false })
  const d = deps.data

  const install = async () => {
    setInstalling(true)
    setInstallError('')
    try {
      await cmd('deps_apply')
    } catch (err) {
      setInstallError(err instanceof Error ? err.message : 'The install did not finish.')
    } finally {
      setInstalling(false)
      void qc.invalidateQueries({ queryKey: ['deps-status'] })
    }
  }

  const runtimeVersion = d?.versions?.['conxa-runtime'] ?? d?.versions?.['conxa-app']
  return (
    <div className="flex h-full items-center justify-center overflow-y-auto p-10">
      <div className="flex w-full max-w-[640px] flex-col gap-7">
        <div className="space-y-2.5">
          <h1 className="text-2xl font-semibold leading-tight text-[var(--text-primary)]">Teach Conxa your first workflow</h1>
          <p className="text-[0.9375rem] leading-relaxed text-[var(--text-secondary)]">
            You do the task once in a real browser. Conxa turns it into a workflow your customers’ AI agents can run, asks you about
            anything it isn’t sure of, and lets you test it before anything ships.
          </p>
        </div>

        <ol className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {STEPS.map((s) => (
            <li key={s.n} className="flex flex-col gap-1.5 rounded-[10px] border border-white/8 p-3.5">
              <span className="text-xs text-[var(--text-meta)]">{s.n}</span>
              <span className="text-sm font-medium text-[var(--text-primary)]">{s.title}</span>
              <span className="text-[0.8125rem] leading-snug text-[var(--text-secondary)]">{s.body}</span>
            </li>
          ))}
        </ol>

        <div className="flex flex-wrap items-center gap-2.5">
          <Button asChild variant="brand">
            <Link to="/new"><Plus className="size-4" />New workflow</Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/chat">Plan one in chat first</Link>
          </Button>
        </div>

        <section aria-label="This computer" className="space-y-2.5 border-t border-white/8 pt-4">
          <h2 className="text-xs font-medium text-[var(--text-meta)]">This computer</h2>
          <div className="space-y-2">
            <CheckRow
              kind={identity ? 'ok' : 'warn'}
              title={identity ? 'Signed in to Conxa Cloud' : 'Not signed in to Conxa Cloud'}
              trailing={identity?.org_name ? <span className="text-[var(--text-meta)]">{identity.org_name}</span> : undefined}
            />
            {d && d.chromium ? (
              <CheckRow kind="ok" title="Recording browser installed" trailing={<span className="text-[var(--text-meta)]">Chromium</span>} />
            ) : d ? (
              <CheckRow
                kind="warn"
                title="Recording browser not installed"
                hint={installError || 'Needed before you can record. About 150 MB, one time.'}
                trailing={<Button type="button" variant="outline" size="sm" disabled={installing} onClick={() => void install()}>{installing ? 'Installing…' : 'Install now'}</Button>}
              />
            ) : (
              <CheckRow kind="none" title={deps.isError ? 'Couldn’t check the recording browser' : 'Checking the recording browser…'} />
            )}
            {d && d.runtime && d.app ? (
              <CheckRow kind="ok" title="Local test runner ready" trailing={runtimeVersion ? <span className="text-[var(--text-meta)]">{runtimeVersion}</span> : undefined} />
            ) : d ? (
              <CheckRow
                kind="warn"
                title="Local test runner not installed"
                hint={installError || 'Needed before you can test a workflow.'}
                trailing={<Button type="button" variant="outline" size="sm" disabled={installing} onClick={() => void install()}>{installing ? 'Installing…' : 'Install now'}</Button>}
              />
            ) : null}
          </div>
        </section>
      </div>
    </div>
  )
}
