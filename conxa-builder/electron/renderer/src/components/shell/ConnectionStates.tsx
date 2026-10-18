import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { recheckConnection } from '@/hooks/useConnection'
import { useConnectionStore } from '@/store/connectionStore'

/** One slim line above the workspace while Conxa Cloud can't be reached. Local work carries on. */
export function OfflineBanner() {
  const cloud = useConnectionStore((s) => s.cloud)
  const [open, setOpen] = useState(false)
  if (cloud !== 'offline') return null
  return (
    <div role="status" className="shrink-0 border-b border-[var(--border-subtle)] bg-[#1e1e1e] px-4 py-2 text-sm sm:px-6">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusGlyph kind="fail" label="Offline" />
        <span className="font-medium text-[var(--text-primary)]">You’re offline</span>
        <span className="text-[var(--text-secondary)]">Compiling, chat and publishing wait until Conxa Cloud is back.</span>
        <span className="ml-auto flex items-center gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            What still works
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={recheckConnection}>Try again</Button>
        </span>
      </div>
      {open && (
        <p className="mt-1.5 pl-5 text-xs text-[var(--text-meta)]">
          Recording, Human Edit and tests that run on this computer keep working. Anything you start that needs the cloud is
          held, not lost, and goes ahead once the connection returns.
        </p>
      )}
    </div>
  )
}

/** Replaces the workspace when the local engine has stopped. Workflows are files on this computer, so
 *  nothing is lost; Restart brings the engine back without closing the app. */
export function EngineStoppedNotice() {
  const engine = useConnectionStore((s) => s.engine)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const restarting = engine === 'restarting' || busy

  const restart = async () => {
    setBusy(true)
    try {
      await window.conxa.engine.restart()
    } finally {
      setBusy(false)
    }
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(await window.conxa.engine.diagnostics())
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard can be unavailable; the notice still works */
    }
  }

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div role="alert" className="w-full max-w-md space-y-3 rounded-[10px] border border-[var(--border-default)] bg-[#1e1e1e] p-5">
        <div className="flex items-baseline gap-2.5">
          <StatusGlyph kind={restarting ? 'run' : 'fail'} />
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">
            {restarting ? 'Restarting Build Studio’s engine…' : 'Build Studio’s engine stopped'}
          </h2>
        </div>
        <p className="text-sm leading-relaxed text-[var(--text-secondary)]">
          The local process that records and compiles quit unexpectedly. Your workflows are saved on this computer.
        </p>
        <div className="flex gap-2 pt-1">
          <Button type="button" size="sm" disabled={restarting} onClick={() => void restart()}>Restart engine</Button>
          <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>
            {copied ? 'Copied' : 'Copy diagnostics'}
          </Button>
        </div>
      </div>
    </div>
  )
}

/** The sidebar footer's connection dot: shape plus words, never colour alone. */
export function ConnectionLine() {
  const engine = useConnectionStore((s) => s.engine)
  const cloud = useConnectionStore((s) => s.cloud)
  const kind = engine !== 'running' || cloud === 'offline' ? 'fail' : cloud === 'unknown' ? 'none' : 'ok'
  const label =
    engine === 'stopped' ? 'Engine stopped'
    : engine === 'restarting' ? 'Engine restarting'
    : cloud === 'offline' ? 'Offline'
    : cloud === 'unknown' ? 'Connecting…'
    : 'Connected'
  return (
    <p className="flex items-center gap-1.5">
      <StatusGlyph kind={kind} label={label} />
      <span>{label}</span>
    </p>
  )
}
