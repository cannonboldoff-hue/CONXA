import { type ReactNode, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  cancelGroupAppAuth,
  checkGroupAppAuth,
  finishGroupAppAuth,
  getGroupAuthStatus,
  removeGroupApp,
  updateGroupApp,
  startGroupAppAuth,
  type GroupAppStatus,
  type WindowRect,
} from '@/api/groupsApi'
import { getWorkflowRecordingStatus } from '@/api/workflowsApi'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Check, Loader2, MoreHorizontal, Pencil, RotateCw, ShieldAlert, ShieldCheck, Trash2 } from 'lucide-react'

type AppState = 'idle' | 'launching' | 'waiting' | 'unconfirmed' | 'captured' | 'failed'

/** What the group page's browser pane shows while one app's sign-in window is open. */
export type SignInSession = { appName: string; loginUrl: string; phase: 'launching' | 'waiting' | 'unconfirmed' | 'failed' }

/** Edit-in-place dialog for a group app's name/login URL/success URL. */
function EditAppDialog({
  groupId,
  app,
  open,
  onOpenChange,
}: {
  groupId: string
  app: GroupAppStatus
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const qc = useQueryClient()
  const [name, setName] = useState(app.name)
  const [loginUrl, setLoginUrl] = useState(app.login_url)
  const [successUrl, setSuccessUrl] = useState(app.success_url)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setName(app.name)
    setLoginUrl(app.login_url)
    setSuccessUrl(app.success_url)
    setError('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, app.id])

  const mutation = useMutation({
    mutationFn: () => updateGroupApp(groupId, app.id, { name, login_url: loginUrl, success_url: successUrl }),
    onSuccess: () => {
      onOpenChange(false)
      qc.invalidateQueries({ queryKey: ['group-auth-status', groupId] })
      qc.invalidateQueries({ queryKey: ['group', groupId] })
      qc.invalidateQueries({ queryKey: ['groups'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
        <DialogHeader>
          <DialogTitle className="text-white">Edit application</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 pt-2">
          <div className="space-y-1.5">
            <Label className="text-zinc-300">Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} className="border-white/10 bg-white/5 text-zinc-100" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-zinc-300">Login URL</Label>
            <Input value={loginUrl} onChange={(e) => setLoginUrl(e.target.value)} className="border-white/10 bg-white/5 text-zinc-100" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-zinc-300">Success URL <span className="text-zinc-600">(optional)</span></Label>
            <Input value={successUrl} onChange={(e) => setSuccessUrl(e.target.value)} className="border-white/10 bg-white/5 text-zinc-100" />
          </div>
          {error ? <p className="text-sm text-red-400">{error}</p> : null}
          <Button className="w-full" onClick={() => mutation.mutate()} disabled={!name || !loginUrl || mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** Confirm-then-remove for a single group app, opened from the row's "More" menu. */
function RemoveAppDialog({
  groupId,
  app,
  open,
  onOpenChange,
}: {
  groupId: string
  app: GroupAppStatus
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const qc = useQueryClient()
  const setOpen = onOpenChange
  const mutation = useMutation({
    mutationFn: () => removeGroupApp(groupId, app.id),
    onSuccess: () => {
      setOpen(false)
      qc.invalidateQueries({ queryKey: ['group-auth-status', groupId] })
      qc.invalidateQueries({ queryKey: ['group', groupId] })
      qc.invalidateQueries({ queryKey: ['groups'] })
    },
  })
  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-white">Remove &ldquo;{app.name}&rdquo;?</AlertDialogTitle>
          <AlertDialogDescription className="text-zinc-400">
            This removes the app and its captured session from this group. Workflows in this group will need it reconnected before recording.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel className="border-white/10 bg-white/5 text-zinc-200" disabled={mutation.isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction className="bg-red-600 text-white hover:bg-red-700" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? 'Removing...' : 'Remove'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/** One row of a popover "More" menu. */
export function MenuItem({ icon, danger, disabled, onSelect, children }: { icon: ReactNode; danger?: boolean; disabled?: boolean; onSelect: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onSelect}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[0.8125rem] outline-none hover:bg-white/[0.06] focus-visible:bg-white/[0.06] disabled:opacity-50 [&_svg]:size-3.5',
        danger ? 'text-red-400' : 'text-[#e8e6e3]',
      )}
    >
      {icon}
      {children}
    </button>
  )
}

/** Walks every unauthenticated app in a group one at a time: opens a login
 * browser at the app's login URL, polls until the recorder self-detects the
 * app's success URL (or the user closes the browser), saves that app's
 * session, then moves to the next app. Shared by the group setup page and
 * the pre-run gate (RunGateDialog). `editable` (group page only, not
 * RunGateDialog) adds a pencil/trash pair per row for managing the app
 * itself, replacing the old separate "Remove {app}" chip strip.
 *
 * `appIds` scopes the wizard to a subset of the group's apps — used when it's
 * opened from a specific blocker (e.g. a recording's `auth_required` error
 * naming only the app(s) that workflow actually needs) rather than the group
 * setup page, where the whole group is relevant. Omit it to show every app,
 * the original behavior. `onAllAuthenticated` fires once every SCOPED app is
 * ready, not the whole group — a sibling app outside `appIds` staying broken
 * must not block a caller that only cares about its own subset. */
export function GroupAuthWizard({
  groupId,
  onAllAuthenticated,
  editable = false,
  appIds,
  autoStart = false,
  onAutoStarted,
  getWindowRect,
  onSession,
  onConnected,
}: {
  groupId: string
  onAllAuthenticated: () => void
  editable?: boolean
  appIds?: string[]
  /** Open Connect for the first app that still needs signing in, once the app list has loaded —
   * used right after "Create group & sign in" so the person lands mid-sign-in. Fires at most once. */
  autoStart?: boolean
  onAutoStarted?: () => void
  /** Where to open the login window (group page: docked over its browser pane). Read once the
   * sign-in has been requested and the pane has had a frame to lay out. */
  getWindowRect?: () => WindowRect | undefined
  /** Fires as the active sign-in changes (null when none is open). */
  onSession?: (s: SignInSession | null) => void
  /** Fires when an app's session was saved. */
  onConnected?: (appName: string) => void
}) {
  const qc = useQueryClient()
  const statusQ = useQuery({
    queryKey: ['group-auth-status', groupId],
    queryFn: () => getGroupAuthStatus(groupId),
  })
  // Scoped readiness: whether every app THIS wizard was asked to cover is ready — distinct
  // from GroupAuthStatus.ready, which reflects the whole group and would wrongly block
  // onAllAuthenticated on an unrelated sibling app when appIds narrows the scope.
  function isScopedReady(status: { apps: GroupAppStatus[] }): boolean {
    if (!appIds) return status.apps.every((a) => a.state === 'ready')
    return appIds.every((id) => status.apps.find((a) => a.id === id)?.state === 'ready')
  }

  const [activeAppId, setActiveAppId] = useState<string | null>(null)
  const [editingAppId, setEditingAppId] = useState<string | null>(null)
  const [removingAppId, setRemovingAppId] = useState<string | null>(null)
  const [menuAppId, setMenuAppId] = useState<string | null>(null)
  const [appState, setAppState] = useState<AppState>('idle')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const allDoneRef = useRef(onAllAuthenticated)
  allDoneRef.current = onAllAuthenticated
  const onConnectedRef = useRef(onConnected)
  onConnectedRef.current = onConnected
  const onSessionRef = useRef(onSession)
  onSessionRef.current = onSession

  const startMut = useMutation({
    mutationFn: async (appId: string) => {
      let rect: WindowRect | undefined
      if (getWindowRect) {
        // onMutate has just asked the page to draw its pane; give it two frames to lay out.
        await new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())))
        rect = getWindowRect()
      }
      return startGroupAppAuth(groupId, appId, rect)
    },
    onMutate: (appId) => {
      setActiveAppId(appId)
      setAppState('launching')
      setError('')
    },
    onSuccess: (data) => {
      setSessionId(data.session_id)
      setAppState('waiting')
    },
    onError: (e: Error) => {
      setAppState('failed')
      setError(e.message)
    },
  })

  const finishMut = useMutation({
    mutationFn: (force?: boolean) => finishGroupAppAuth(sessionId!, groupId, activeAppId!, force),
    onSuccess: (data) => {
      // P0 (Application Authentication Recording): a failed self-test does NOT end the
      // session — the login window is still open server-side, so stay in `waiting` with the
      // reason shown, letting the person keep signing in (or click "Save anyway"). Only a
      // `confirmed: true` response (learned + self-tested, or force-saved, or the browser was
      // already closed) means the session actually ended.
      if (!data.confirmed) {
        setAppState('unconfirmed')
        setError(data.reason)
        return
      }
      setAppState('captured')
      setSessionId(null)
      onConnectedRef.current?.(statusQ.data?.apps.find((a) => a.id === activeAppId)?.name ?? '')
      qc.setQueryData(['group-auth-status', groupId], data.auth)
      qc.invalidateQueries({ queryKey: ['groups'] })
      qc.invalidateQueries({ queryKey: ['group', groupId] })
      if (isScopedReady(data.auth)) {
        setActiveAppId(null)
        allDoneRef.current()
      } else {
        setActiveAppId(null)
        setAppState('idle')
      }
    },
    onError: (e: Error) => {
      setAppState('failed')
      setError(e.message)
    },
  })

  // Poll recording status while a login window is open, same 1s cadence as the old
  // RecordLoginDialog. Only the browser closing (by hand) or the poll itself erroring
  // auto-finishes — reached_wait_url is a passive hint on the row below, never an auto-close
  // trigger (spec P0 §3: only the user's own Done ends an auth session; the server-side
  // auto-stop-on-success_url this used to rely on was removed for the same reason).
  const recStatusQ = useQuery({
    queryKey: ['group-app-recording-status', sessionId],
    queryFn: () => getWorkflowRecordingStatus(sessionId!),
    enabled: appState === 'waiting' && !!sessionId,
    refetchInterval: 1000,
    retry: false,
  })

  useEffect(() => {
    if (appState !== 'waiting' || finishMut.isPending) return
    // A poll that starts erroring while `retry: false` is set freezes `recStatusQ.data` at its
    // last good value forever — without this, the card would stay stuck on "waiting" with
    // nothing to break out of it.
    if (recStatusQ.isError) {
      finishMut.mutate(undefined)
      return
    }
    // The browser closing on its own (the user closed Chromium by hand) is the one remaining
    // non-Done way a session ends — cmd_finish_group_app_auth falls back to a 2-way check in
    // that case rather than the full learn flow (nothing left to observe live).
    if (recStatusQ.data?.browser_open === false) {
      finishMut.mutate(undefined)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    appState,
    finishMut.isPending,
    recStatusQ.isError,
    recStatusQ.data?.browser_open,
  ])

  // Mirror the open sign-in to the page so it can draw the browser pane around the login window.
  const activeApp = statusQ.data?.apps.find((a) => a.id === activeAppId)
  const phase = appState === 'launching' || appState === 'waiting' || appState === 'unconfirmed' || appState === 'failed' ? appState : null
  useEffect(() => {
    onSessionRef.current?.(activeApp && phase ? { appName: activeApp.name, loginUrl: activeApp.login_url, phase } : null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeApp?.name, activeApp?.login_url, phase])

  const autoStartedRef = useRef(false)
  useEffect(() => {
    if (!autoStart || autoStartedRef.current || !statusQ.data) return
    autoStartedRef.current = true
    const first = statusQ.data.apps.find((a) => a.state !== 'ready' && (!appIds || appIds.includes(a.id)))
    if (first) startMut.mutate(first.id)
    onAutoStarted?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, statusQ.data])

  function retry(appId: string) {
    startMut.mutate(appId)
  }

  function skip() {
    if (sessionId) cancelGroupAppAuth(sessionId).catch(() => {})
    setActiveAppId(null)
    setAppState('idle')
    setSessionId(null)
  }

  // "Check now" — a bounded, user-initiated probe of whether a saved session actually
  // still works, distinct from `state === 'ready'` (which just means a session file
  // exists — see group_auth_status's docstring). Per-app, keyed so one app's probe
  // spinner doesn't block another's button.
  const [checkingAppId, setCheckingAppId] = useState<string | null>(null)
  const checkMut = useMutation({
    mutationFn: (appId: string) => checkGroupAppAuth(groupId, appId),
    onMutate: (appId) => setCheckingAppId(appId),
    onSuccess: (data) => {
      setCheckingAppId(null)
      qc.setQueryData(['group-auth-status', groupId], data.auth)
      qc.invalidateQueries({ queryKey: ['groups'] })
      qc.invalidateQueries({ queryKey: ['group', groupId] })
    },
    onError: () => setCheckingAppId(null),
  })

  if (statusQ.isLoading || !statusQ.data) {
    return <p className="px-1 py-4 text-xs text-zinc-500">Loading apps…</p>
  }

  const apps = statusQ.data.apps
  const visibleApps = appIds ? apps.filter((a) => appIds.includes(a.id)) : apps
  const editingApp = editingAppId ? apps.find((a) => a.id === editingAppId) ?? null : null
  const removingApp = removingAppId ? apps.find((a) => a.id === removingAppId) ?? null : null

  return (
    <div className="space-y-1">
      {visibleApps.map((app: GroupAppStatus) => {
        const isActive = activeAppId === app.id
        const isReady = app.state === 'ready' && !isActive
        const isExpired = app.state === 'expired' && !isActive
        const isFailed = isActive && appState === 'failed'
        const isUnconfirmed = isActive && appState === 'unconfirmed'
        const isChecking = checkingAppId === app.id
        const isUnverified = isReady && !app.verified
        return (
          // Flat rows, not boxed cards: only the app being signed in to is lifted, so the
          // eye lands on the one row that needs you. Status lives in the glyph + caption.
          <div
            key={app.id}
            className={cn('rounded-xl px-3 py-3 transition-colors', isActive ? 'bg-white/[0.05]' : 'hover:bg-white/[0.025]')}
          >
            {/* Row 1: status glyph + name/URL + actions */}
            <div className="flex items-center gap-3">
              <span
                className={cn(
                  'flex size-8 shrink-0 items-center justify-center rounded-lg',
                  isReady && app.verified
                    ? 'bg-emerald-500/15 text-emerald-400'
                    : isUnverified || isUnconfirmed
                      ? 'bg-amber-500/15 text-amber-400'
                      : isExpired || isFailed
                        ? 'bg-red-500/15 text-red-400'
                        : 'bg-white/8 text-zinc-500',
                )}
              >
                {isReady ? (
                  <Check className="size-3.5" />
                ) : isActive && (appState === 'launching' || appState === 'waiting') ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : isExpired || isFailed || isUnconfirmed ? (
                  <ShieldAlert className="size-3.5" />
                ) : (
                  <ShieldCheck className="size-3.5" />
                )}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-[#e8e6e3]">{app.name}</p>
                <p className="truncate font-mono text-[11px] text-[#8e8e8e]" title={app.login_url}>{app.login_url.replace(/^https?:\/\//, '')}</p>
              </div>
              <div className="ml-auto flex shrink-0 items-center gap-1.5">
                {isUnverified ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={isChecking}
                    onClick={() => checkMut.mutate(app.id)}
                  >
                    {isChecking ? <Loader2 className="size-3.5 animate-spin" /> : 'Check now'}
                  </Button>
                ) : isReady ? null : isFailed ? (
                  <>
                    <Button size="sm" variant="outline" onClick={skip}>Skip</Button>
                    <Button size="sm" onClick={() => retry(app.id)}>Retry</Button>
                  </>
                ) : isActive && (appState === 'waiting' || isUnconfirmed) ? (
                  // Not disabled — a stuck poll (see the auto-finish effect above) must never
                  // trap the user with no way out of "waiting" other than force-quitting Chromium.
                  <>
                    <Button size="sm" variant="outline" onClick={skip}>Cancel</Button>
                    {isUnconfirmed && (
                      // P0: the self-test couldn't confirm sign-in — force-save with no learned
                      // definition rather than force the person to keep retrying indefinitely.
                      <Button size="sm" variant="outline" disabled={finishMut.isPending} onClick={() => finishMut.mutate(true)}>
                        Save anyway
                      </Button>
                    )}
                    <Button size="sm" disabled={finishMut.isPending} onClick={() => finishMut.mutate(undefined)}>
                      {finishMut.isPending ? <Loader2 className="size-3.5 animate-spin" /> : 'Done'}
                    </Button>
                  </>
                ) : isActive ? (
                  <Button size="sm" variant="outline" disabled>
                    <Loader2 className="size-3.5 animate-spin" /> Launching…
                  </Button>
                ) : (
                  <Button size="sm" variant="outline" onClick={() => startMut.mutate(app.id)} disabled={!!activeAppId}>
                    {isExpired ? 'Sign in again' : 'Sign in'}
                  </Button>
                )}
                {editable && !isActive && (
                  <Popover open={menuAppId === app.id} onOpenChange={(v) => setMenuAppId(v ? app.id : null)}>
                    <PopoverTrigger asChild>
                      <Button size="icon-sm" variant="ghost" aria-label={`More options for ${app.name}`} className="text-[#8e8e8e] hover:text-[#e8e6e3]">
                        <MoreHorizontal className="size-4" />
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent align="end" className="w-44 p-1">
                      {/* Recheck: same headless probe as "Check now", for an already-verified
                          ready session, which has no other way to be re-probed on demand.
                          isUnverified already has "Check now" in the primary slot, and
                          isExpired has "Reconnect", so neither gets it here. */}
                      {isReady && !isUnverified && (
                        <MenuItem icon={<RotateCw />} disabled={isChecking} onSelect={() => { setMenuAppId(null); checkMut.mutate(app.id) }}>
                          Check sign-in
                        </MenuItem>
                      )}
                      <MenuItem icon={<Pencil />} onSelect={() => { setMenuAppId(null); setEditingAppId(app.id) }}>
                        Edit
                      </MenuItem>
                      <MenuItem icon={<Trash2 />} danger onSelect={() => { setMenuAppId(null); setRemovingAppId(app.id) }}>
                        Remove
                      </MenuItem>
                    </PopoverContent>
                  </Popover>
                )}
              </div>
            </div>
            {/* Row 2: contextual status, indented under the name */}
            <div className="pl-11 empty:hidden">
              {isActive && appState === 'waiting' && (
                <p className="mt-2 flex items-center gap-1.5 text-xs text-[#a3a3a3]">
                  <Loader2 className="size-3 animate-spin text-[#93b4e6] motion-reduce:animate-none" />
                  Sign in in the browser window, then click Done.
                </p>
              )}
              {isChecking && (
                <p className="mt-2 flex items-center gap-1.5 text-xs text-[#a3a3a3]">
                  <Loader2 className="size-3 animate-spin motion-reduce:animate-none" /> Checking sign-in…
                </p>
              )}
              {isUnconfirmed && (
                <p className="mt-1.5 text-xs text-amber-300">
                  {error || "Couldn't confirm you're signed in yet. Finish signing in and click Done again, or Save anyway."}
                </p>
              )}
              {isFailed && (
                <p className="mt-1.5 text-xs text-red-300">{error || app.last_error || 'Login failed.'}</p>
              )}
              {isExpired && (
                <p className="mt-1.5 text-xs text-red-300">Session expired — sign in again.</p>
              )}
              {isUnverified && (
                <p className="mt-1.5 text-xs text-amber-300">Not verified recently — may have expired.</p>
              )}
              {/* Amber, not red: the sign-in itself worked and is saved — it is the success URL that is suspect. */}
              {isReady && app.detect_warning && (
                <p className="mt-1.5 text-xs text-amber-300">{app.detect_warning}</p>
              )}
            </div>
          </div>
        )
      })}
      {editable && editingApp && (
        <EditAppDialog groupId={groupId} app={editingApp} open={!!editingApp} onOpenChange={(v) => !v && setEditingAppId(null)} />
      )}
      {editable && removingApp && (
        <RemoveAppDialog groupId={groupId} app={removingApp} open={!!removingApp} onOpenChange={(v) => !v && setRemovingAppId(null)} />
      )}
    </div>
  )
}
