import { useCallback, useEffect, useState } from 'react'
import { Routes, Route, Navigate, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { cmd, type UpdateCheckResult } from '@/lib/ipc'
import { errorMessage } from '@/api/workflowApi'
import { Button } from '@/components/ui/button'
import { AuthContext, performLogout, type Identity } from '@/contexts/AuthContext'
import { AppChrome } from '@/components/layout/AppChrome'
import { LoginOverlay } from '@/components/LoginOverlay'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { BootstrapScreen } from '@/pages/BootstrapScreen'
import { UpdateRequiredScreen } from '@/pages/UpdateRequiredScreen'
import { LegalGateScreen, type LegalStatus } from '@/pages/LegalGateScreen'
import { fetchWorkflow } from '@/api/workflowsApi'
import { useShellStore } from '@/store/shellStore'

// Pages
import { WorkflowListPage } from '@/pages/WorkflowListPage'
import { GroupPage } from '@/pages/GroupPage'
import { HumanEditPage } from '@/pages/HumanEditPage'
import { InspectorPage } from '@/pages/InspectorPage'
import { HumanEditListPage } from '@/pages/HumanEditListPage'
import { PublishPage } from '@/pages/PublishPage'
import { BuildInstallerPage } from '@/pages/BuildInstallerPage'
import { ChatPage } from '@/pages/ChatPage'
import { NewWorkflowPage } from '@/pages/NewWorkflowPage'
import { FirstLaunchPage } from '@/pages/FirstLaunchPage'
import { useShellData } from '@/hooks/useShellData'
import { WorkflowConversationPage } from '@/pages/WorkflowConversationPage'

// Studio-exclusive pages (keep existing)

function SplashScreen() {
  return (
    <div className="flex h-dvh items-center justify-center bg-[#090b0d]">
      <div className="size-8 animate-pulse rounded-full bg-white/10" />
    </div>
  )
}

/**
 * Terms & Privacy acceptance, checked with the cloud rather than this machine —
 * an acceptance is only evidence if it names the signed-in user, so the gate sits
 * after sign-in and the record lives on the server (PROD-17).
 *
 * Deliberately fail-closed: if the cloud can't be reached we block rather than
 * assume acceptance. Dev builds skip it, same escape the deps gate uses.
 */
function LegalGate({ children }: { children: React.ReactNode }) {
  const [accepted, setAccepted] = useState(false)

  const q = useQuery({
    queryKey: ['legal-status'],
    queryFn: () => cmd<LegalStatus>('legal_status'),
    enabled: window.conxa.isPackaged,
    retry: false,
    staleTime: Infinity,
  })

  if (!window.conxa.isPackaged) return <>{children}</>
  if (q.isPending) return <SplashScreen />

  if (q.isError) {
    return (
      <BlockingNotice
        title="Build Studio can't start"
        body={errorMessage(q.error, "Conxa Cloud didn't respond.")}
        detail="Build Studio confirms your Terms and Privacy Policy acceptance with Conxa Cloud each time it starts, so it can't continue until the connection is back."
        actionLabel={q.isFetching ? 'Retrying…' : 'Try again'}
        onAction={() => void q.refetch()}
        disabled={q.isFetching}
      />
    )
  }

  if (accepted || q.data.accepted) return <>{children}</>

  return (
    <LegalGateScreen
      status={q.data}
      onAccept={async (payload) => {
        const app_version = await window.conxa.update.getVersion().catch(() => '')
        await cmd('legal_accept', { ...payload, app_version })
        setAccepted(true)
      }}
    />
  )
}

function BlockingNotice({
  title,
  body,
  detail,
  actionLabel,
  onAction,
  disabled,
}: {
  title: string
  body: string
  detail?: string
  actionLabel: string
  onAction: () => void
  disabled?: boolean
}) {
  return (
    <div className="fixed inset-x-0 top-10 bottom-0 z-[9999] flex items-center justify-center bg-[#090b0d] p-6">
      <div className="w-full max-w-sm rounded-2xl border border-white/10 bg-[#0d0f12] p-8 shadow-2xl">
        <h1 className="mb-1 text-center text-xl font-semibold text-white">{title}</h1>
        <p className="mb-6 text-center text-sm text-zinc-400">{body}</p>
        {detail && (
          <p className="mb-6 rounded-lg border border-white/8 bg-black/20 p-3 text-xs text-zinc-500">
            {detail}
          </p>
        )}
        <Button className="w-full" size="lg" onClick={onAction} disabled={disabled}>
          {actionLabel}
        </Button>
      </div>
    </div>
  )
}

function DeepLinkHandler() {
  const navigate = useNavigate()
  useEffect(() => {
    return window.conxa.onDeepLink((url) => {
      const workflowMatch = url.match(/[?&]workflow=([^&]+)/)
      const workflowId = workflowMatch ? decodeURIComponent(workflowMatch[1]) : null
      navigate(workflowId ? `/workflows/${workflowId}` : '/workflows')
    })
  }, [navigate])
  return null
}

/** Settings is a popup now; `/settings` (old links, the updater) opens it over the home screen. */
function OpenSettings() {
  const setSettingsOpen = useShellStore((s) => s.setSettingsOpen)
  useEffect(() => setSettingsOpen(true), [setSettingsOpen])
  return <Navigate to="/" replace />
}

/** `/` has no id to route on — the group list (`/workflows`) is the home, unless there is nothing in
 * the workspace yet: then the first-launch screen explains how to start. */
function DefaultRedirect() {
  const { groups, loading, failed } = useShellData()
  if (loading) return null
  return <Navigate to={groups.length === 0 && !failed ? '/welcome' : '/workflows'} replace />
}

/** The per-workflow detail page was folded into its owning group's page (every
 * action it offered — record/compile/review/test — now lives on the group
 * page's workflow row). This keeps old `/workflows/:workflowId` links (deep
 * links, the compile page's "back", Human Edit's `?from=`) working by
 * resolving the workflow and forwarding to its group. */
function WorkflowRedirect() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const q = useQuery({
    queryKey: ['workflow', workflowId],
    queryFn: () => fetchWorkflow(workflowId!),
    enabled: !!workflowId,
  })

  if (q.isLoading) return null
  if (q.isError || !q.data) return <Navigate to="/workflows" replace />
  return <Navigate to={`/groups/${encodeURIComponent(q.data.workflow.group_id)}`} replace />
}

type DepUpdateBanner =
  | { phase: 'idle' }
  | { phase: 'updating'; pct: number | null }
  | { phase: 'done' }
  | { phase: 'failed'; dep: string; message: string }

export function App() {
  // 'checking' = deps status not yet known, 'needed' = bootstrap required, 'ready' = deps ok
  const [depsState, setDepsState] = useState<'checking' | 'needed' | 'ready'>('checking')
  // 'checking' = update check in-flight, 'required' = newer version exists, 'ok' = proceed
  const [updateState, setUpdateState] = useState<'checking' | 'required' | 'ok'>('checking')
  const [updateCheckResult, setUpdateCheckResult] = useState<UpdateCheckResult | null>(null)
  const [identity, setIdentity] = useState<Identity | null | 'checking'>('checking')
  const [depUpdateBanner, setDepUpdateBanner] = useState<DepUpdateBanner>({ phase: 'idle' })

  useEffect(() => {
    // Skip the bootstrap gate entirely in dev — deps (nsis/chromium/etc.) are managed by
    // scripts/setup.ps1, and Test Skill always runs against the local runtime/ source tree
    // (see conxa_runtime.py), so dev never needs the packaged-download flow.
    if (!window.conxa.isPackaged) {
      setDepsState('ready')
      return
    }
    cmd<{ all_ready: boolean }>('deps_status')
      .then((r) => setDepsState(r.all_ready ? 'ready' : 'needed'))
      .catch(() => setDepsState('needed'))
  }, [])

  // Check for updates after deps are ready. Fail-open: any error lets the user through.
  useEffect(() => {
    if (depsState !== 'ready') return
    window.conxa.update.check()
      .then((result) => {
        if (result.available && !result.error) {
          setUpdateCheckResult(result)
          setUpdateState('required')
        } else {
          setUpdateState('ok')
        }
      })
      .catch(() => setUpdateState('ok'))
  }, [depsState])

  // Identity check runs only after update check passes.
  useEffect(() => {
    if (updateState !== 'ok') return
    cmd<{ identity: Identity | null }>('whoami')
      .then((r) => {
        const id = r?.identity
        // Require a user_id — rejects empty objects that can slip through on
        // broken sessions where userinfo fetch failed and the token is opaque.
        setIdentity(id?.user_id ? id as Identity : null)
      })
      .catch(() => setIdentity(null))
  }, [updateState])

  // When all deps are present, silently check for newer versions in the background.
  useEffect(() => {
    if (depsState !== 'ready' || !window.conxa.isPackaged) return
    const unsub = window.conxa.onEvent((ev) => {
      if (ev.phase !== 'bootstrap') return
      if (!ev.dep) {
        // 'complete' is emitted even when a dep failed, so it must not clear a failure.
        if (ev.status === 'complete') {
          setDepUpdateBanner((b) => (b.phase === 'failed' ? b : { phase: 'done' }))
        }
        return
      }
      if (ev.status === 'downloading') {
        const pct = typeof ev.pct === 'number' ? Math.round(ev.pct as number) : null
        // Deps download concurrently — a sibling still in flight must not paint over
        // a failure the user hasn't read yet.
        setDepUpdateBanner((b) => (b.phase === 'failed' ? b : { phase: 'updating', pct }))
      } else if (ev.status === 'error') {
        // Show it. A dep that fails here (checksum mismatch, blocked download) silently
        // pins the user to the installed version forever — hiding it is how an app layer
        // that never updated went unnoticed for weeks. Banner, not a blocking screen:
        // the rest of the app still works on the old dep.
        setDepUpdateBanner({
          phase: 'failed',
          dep: String(ev.dep),
          message: String(ev.message ?? 'download failed'),
        })
      }
    })
    cmd('bootstrap', {}).catch(() => {})
    return unsub
  }, [depsState])

  // Auto-dismiss the 'done' banner after 2 s.
  useEffect(() => {
    if (depUpdateBanner.phase !== 'done') return
    const t = setTimeout(() => setDepUpdateBanner({ phase: 'idle' }), 2000)
    return () => clearTimeout(t)
  }, [depUpdateBanner.phase])

  const handleBootstrapComplete = useCallback(() => setDepsState('ready'), [])

  if (depsState === 'checking') return <SplashScreen />
  if (depsState === 'needed') return <BootstrapScreen onComplete={handleBootstrapComplete} />
  if (updateState === 'checking') return <SplashScreen />
  if (updateState === 'required') return (
    <UpdateRequiredScreen
      currentVersion={updateCheckResult?.currentVersion ?? ''}
      latestVersion={updateCheckResult?.latestVersion ?? ''}
    />
  )
  if (identity === 'checking') return <SplashScreen />

  const resolvedIdentity = identity as Identity | null

  const logout = () => performLogout(setIdentity)

  // Hard gate: routes are never mounted until identity is confirmed.
  // AppChrome is always present so the frameless window's title bar controls work.
  if (!resolvedIdentity) {
    return (
      <AuthContext.Provider value={{ identity: null, setIdentity, logout }}>
        <AppChrome>
          <LoginOverlay onLogin={setIdentity} />
        </AppChrome>
      </AuthContext.Provider>
    )
  }

  return (
    <AuthContext.Provider value={{ identity: resolvedIdentity, setIdentity, logout }}>
      <ErrorBoundary>
        <AppChrome>
          <LegalGate>
          <DeepLinkHandler />
          <Routes>
            <Route path="/" element={<DefaultRedirect />} />
            <Route path="/workflows" element={<WorkflowListPage />} />
            <Route path="/welcome" element={<FirstLaunchPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/new" element={<NewWorkflowPage />} />
            <Route path="/chat/:chatId" element={<ChatPage />} />
            <Route path="/w/:workflowId" element={<WorkflowConversationPage />} />
            <Route path="/workflows/:workflowId" element={<WorkflowRedirect />} />
            <Route path="/workflows/:workflowId/inspector" element={<InspectorPage />} />
            <Route path="/groups/:groupId" element={<GroupPage />} />
            <Route path="/edit" element={<HumanEditPage />} />
            <Route path="/edit/:skillId" element={<HumanEditPage />} />
            <Route path="/human-edit" element={<HumanEditListPage />} />
            <Route path="/publish" element={<PublishPage />} />
            <Route path="/build-installer" element={<BuildInstallerPage />} />
            <Route path="/settings" element={<OpenSettings />} />
            <Route path="*" element={<Navigate to="/workflows" replace />} />
          </Routes>
          </LegalGate>
        </AppChrome>
      </ErrorBoundary>
      {depUpdateBanner.phase !== 'idle' && (
        <div className="fixed bottom-0 left-0 right-0 z-50 flex items-center gap-3 border-t border-white/8 bg-[#0d0f12]/95 px-4 py-2 backdrop-blur">
          {depUpdateBanner.phase === 'updating' ? (
            <span className="size-2 shrink-0 animate-pulse rounded-full bg-blue-500" />
          ) : depUpdateBanner.phase === 'failed' ? (
            <span className="size-2 shrink-0 rounded-full bg-red-500" />
          ) : (
            <span className="size-2 shrink-0 rounded-full bg-emerald-500" />
          )}
          <span
            className={`flex-1 text-xs ${
              depUpdateBanner.phase === 'failed' ? 'text-red-300' : 'text-zinc-400'
            }`}
          >
            {depUpdateBanner.phase === 'updating'
              ? 'Updating dependencies…'
              : depUpdateBanner.phase === 'failed'
                ? `Couldn't update ${depUpdateBanner.dep} — ${depUpdateBanner.message}. Still running the installed version.`
                : 'Dependencies updated'}
          </span>
          {depUpdateBanner.phase === 'failed' && (
            <button
              type="button"
              onClick={() => setDepUpdateBanner({ phase: 'idle' })}
              className="shrink-0 rounded px-2 py-0.5 text-xs text-zinc-400 hover:bg-white/8 hover:text-zinc-200"
            >
              Dismiss
            </button>
          )}
          {depUpdateBanner.phase === 'updating' && depUpdateBanner.pct != null && (
            <>
              <span className="text-xs tabular-nums text-zinc-500">{depUpdateBanner.pct}%</span>
              <div className="h-1 w-28 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-blue-500 transition-all duration-300"
                  style={{ width: `${depUpdateBanner.pct}%` }}
                />
              </div>
            </>
          )}
        </div>
      )}
    </AuthContext.Provider>
  )
}
