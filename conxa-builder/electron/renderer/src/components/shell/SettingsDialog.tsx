import { useEffect, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LogOut, RefreshCw } from 'lucide-react'
import { getStudioSettings, setStudioSettings } from '@/api/conversationApi'
import { EntitlementMeters } from '@/components/EntitlementMeters'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { performLogout, useAuth } from '@/contexts/AuthContext'
import { useUpdater } from '@/hooks/useUpdater'
import { PRIVACY_URL, TERMS_URL } from '@/pages/LegalGateScreen'
import { useShellStore, type SettingsTab } from '@/store/shellStore'
import { cn } from '@/lib/utils'

const TABS: { id: SettingsTab; label: string; blurb: string }[] = [
  { id: 'account', label: 'Account', blurb: 'The account you are signed in with on this machine.' },
  { id: 'usage', label: 'Usage', blurb: 'What this workspace has used so far.' },
  { id: 'testing', label: 'Testing', blurb: 'How tests behave after something fails.' },
  { id: 'about', label: 'About', blurb: 'Details to include when you contact support.' },
]

const BOX = 'rounded-[10px] border border-white/8'

function initials(source: string) {
  const parts = source.split(/[\s@._-]+/).filter(Boolean)
  return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase()
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-6 border-b border-white/8 px-4 py-3 text-sm last:border-b-0">
      <span className="w-32 shrink-0 text-[var(--text-secondary)]">{label}</span>
      <div className="min-w-0 flex-1 text-[var(--text-primary)]">{children}</div>
    </div>
  )
}

function AccountTab() {
  const { identity, setIdentity } = useAuth()
  const name = identity?.name || identity?.email || ''
  if (!identity) return <p className="text-sm text-[var(--text-meta)]">Not signed in.</p>
  return (
    <div className={cn(BOX, 'flex items-center gap-4 p-4')}>
      <div
        aria-hidden="true"
        className="flex size-11 shrink-0 items-center justify-center rounded-full border border-white/12 bg-[#2c2c2c] text-[15px] font-medium"
      >
        {initials(name)}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-medium text-[var(--text-primary)]">{name}</p>
        {identity.name && identity.email && <p className="truncate text-sm text-[var(--text-secondary)]">{identity.email}</p>}
        {identity.org_name && <p className="truncate text-sm text-[var(--text-meta)]">{identity.org_name}</p>}
      </div>
      <Button variant="outline" size="sm" className="gap-2" onClick={() => performLogout(setIdentity)}>
        <LogOut className="size-4" />
        Sign out
      </Button>
    </div>
  )
}

/** The auto-fix switch: after a failed test, apply one safe change at a time and re-test, up to three times. */
function TestingTab() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['studio-settings'], queryFn: getStudioSettings, retry: false })
  const m = useMutation({
    mutationFn: (autofix_enabled: boolean) => setStudioSettings({ autofix_enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['studio-settings'] }),
  })
  const on = q.data?.settings.autofix_enabled ?? true
  return (
    <label className={cn(BOX, 'flex cursor-pointer items-start gap-4 p-4 text-sm')}>
      <input type="checkbox" className="mt-1 accent-white" checked={on} disabled={q.isLoading || m.isPending} onChange={(e) => m.mutate(e.target.checked)} />
      <span>
        <span className="block text-[var(--text-primary)]">Fix a failed test automatically</span>
        <span className="block text-[var(--text-meta)]">
          After a test fails, Build Studio makes one safe change, shows it with Undo, and tests again — up to three times, then it asks you.
          Re-tests always pause before steps that can’t be undone. Approving stays your decision.
        </span>
      </span>
    </label>
  )
}

function AboutTab() {
  const { status, currentVersion, check, startDownload, install } = useUpdater()
  useEffect(() => {
    if (status.phase === 'downloaded') install()
  }, [status.phase, install])
  const isChecking = status.phase === 'checking'
  const isAvailable = status.phase === 'available'
  const isDownloading = status.phase === 'downloading'
  const isDownloaded = status.phase === 'downloaded'
  const hasError = status.phase === 'error'
  return (
    <div className={cn(BOX, 'overflow-hidden')}>
      <Row label="Product">Conxa Build Studio</Row>
      <Row label="Description">Offline AI-native workflow recorder &amp; compiler</Row>
      <Row label="Version">
        <span className="font-mono text-[13px] tabular-nums">{currentVersion || '—'}</span>
        {status.phase === 'available' && <span className="ml-3 text-[var(--text-secondary)]">{status.latestVersion} available</span>}
      </Row>
      <Row label="Software update">
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1 text-sm">
            {status.phase === 'downloading' ? (
              <div>
                <div className="mb-1 flex justify-between text-xs text-[var(--text-secondary)]">
                  <span>Downloading update...</span>
                  <span>{Math.round(status.percent)}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
                  <div className="h-full rounded-full bg-zinc-400 transition-all duration-300" style={{ width: `${status.percent}%` }} />
                </div>
              </div>
            ) : isDownloaded ? (
              <span className="text-[var(--text-secondary)]">Installing and restarting...</span>
            ) : status.phase === 'error' ? (
              <span className="text-xs text-status-error">{status.message}</span>
            ) : status.phase === 'not-available' ? (
              <span className="text-[var(--text-secondary)]">You’re up to date.</span>
            ) : null}
          </div>
          {!isDownloading && !isDownloaded && (
            <Button variant="outline" size="sm" className="gap-2" onClick={check} disabled={isChecking}>
              <RefreshCw className={cn('size-4', isChecking && 'animate-spin')} />
              {isChecking ? 'Checking...' : 'Check for updates'}
            </Button>
          )}
          {(isAvailable || hasError) && <Button size="sm" onClick={startDownload}>Update now</Button>}
        </div>
      </Row>
      <p className="px-4 py-3 text-xs text-[var(--text-meta)]">
        Licensed, not sold.{' '}
        <button type="button" onClick={() => void window.conxa.openExternal(TERMS_URL)} className="text-[var(--text-secondary)] underline underline-offset-2 hover:text-white">
          Terms and Conditions
        </button>{' '}
        &middot;{' '}
        <button type="button" onClick={() => void window.conxa.openExternal(PRIVACY_URL)} className="text-[var(--text-secondary)] underline underline-offset-2 hover:text-white">
          Privacy Policy
        </button>
      </p>
    </div>
  )
}

/** Settings as a popup over whatever the person was doing (Settings mockup): Account, Usage, Testing,
 *  About. Closing it returns to exactly where they were. */
export function SettingsDialog() {
  const open = useShellStore((s) => s.settingsOpen)
  const tab = useShellStore((s) => s.settingsTab)
  const setOpen = useShellStore((s) => s.setSettingsOpen)
  const current = TABS.find((t) => t.id === tab) ?? TABS[0]
  return (
    <Dialog open={open} onOpenChange={(o) => setOpen(o)}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[560px] max-h-[calc(100vh-2rem)] w-[800px] max-w-[calc(100%-2rem)] gap-0 overflow-hidden p-0 sm:max-w-[800px]"
      >
        <nav aria-label="Settings sections" className="flex w-[210px] shrink-0 flex-col gap-0.5 border-r border-white/8 bg-[#1a1a1a] p-2.5 pt-4">
          <DialogTitle className="mb-3 ml-2.5 text-base font-semibold">Settings</DialogTitle>
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-current={t.id === current.id ? 'page' : undefined}
              onClick={() => setOpen(true, t.id)}
              className={cn(
                'h-[34px] rounded-[8px] px-2.5 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-brand/55',
                t.id === current.id ? 'bg-white/[0.07] text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:bg-white/[0.04]',
              )}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-14 shrink-0 items-center border-b border-white/6 pl-7 pr-3">
            <span className="flex-1 text-[15px] font-semibold">{current.label}</span>
            <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={() => setOpen(false)} className="text-[var(--text-secondary)]">
              ✕
            </Button>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-7 py-6">
            <DialogDescription className="text-[13px] text-[var(--text-secondary)]">{current.blurb}</DialogDescription>
            {current.id === 'account' && <AccountTab />}
            {current.id === 'usage' && <EntitlementMeters />}
            {current.id === 'testing' && <TestingTab />}
            {current.id === 'about' && <AboutTab />}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
