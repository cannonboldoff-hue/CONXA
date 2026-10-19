import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addGroupApp,
  checkGroupAppAuth,
  fetchGroup,
  type GroupApp,
} from '@/api/groupsApi'
import { fetchSkillPack, fetchSkillPackVersions, updateWorkflow, type SkillPackBuild, type Workflow } from '@/api/workflowsApi'
import { GroupAuthWizard, type SignInSession } from '@/components/GroupAuthWizard'
import { SignInPane } from '@/components/SignInPane'
import { RecordWorkflowDialog } from '@/components/RecordWorkflowDialog'
import { DeleteWorkflowButton } from '@/components/DeleteWorkflowButton'
import { UsageCards } from '@/components/EntitlementMeters'
import { WorkflowTestRow } from '@/components/WorkflowTests'
import { PageHeader } from '@/components/layout/PageHeader'
import { CompilePanel } from '@/components/CompilePanel'
import { WorkflowStageBadge, WorkflowStageRail } from '@/components/StagePath'
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
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { formatCompileEta, useCompileEtaSeconds, useCompileStore } from '@/store/compileStore'
import { useShellStore } from '@/store/shellStore'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { FolderKanban, Layers, Loader2, Pencil, Plus } from 'lucide-react'

function AddAppDialog({ groupId }: { groupId: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [loginUrl, setLoginUrl] = useState('')
  const [error, setError] = useState('')

  // Success URL is no longer collected here (P0: Application Authentication Recording) — sign-in
  // is learned from the Connect session itself, not configured up front. The field still exists
  // on the model (Edit keeps it, as an optional manual hint for the runtime's generic fallback
  // when a definition can't be learned) — see docs/TRD.md §5.3.
  const mutation = useMutation({
    mutationFn: () => addGroupApp(groupId, name, loginUrl, ''),
    onSuccess: () => {
      setOpen(false)
      setName('')
      setLoginUrl('')
      setError('')
      qc.invalidateQueries({ queryKey: ['group', groupId] })
      qc.invalidateQueries({ queryKey: ['group-auth-status', groupId] })
      qc.invalidateQueries({ queryKey: ['groups'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="ghost" className="text-[#e8e6e3]">
          <Plus className="size-3.5" /> Add app
        </Button>
      </DialogTrigger>
      <DialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
        <DialogHeader>
          <DialogTitle className="text-white">Add application</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 pt-2">
          <div className="space-y-1.5">
            <Label className="text-zinc-300">Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Salesforce" className="border-white/10 bg-white/5 text-zinc-100" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-zinc-300">Login URL</Label>
            <Input value={loginUrl} onChange={(e) => setLoginUrl(e.target.value)} placeholder="https://app.example.com/login" className="border-white/10 bg-white/5 text-zinc-100" />
          </div>
          {error ? <p className="text-sm text-red-400">{error}</p> : null}
          <Button className="w-full" onClick={() => mutation.mutate()} disabled={!name || !loginUrl || mutation.isPending}>
            {mutation.isPending ? 'Adding…' : 'Add application'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** Opens the full New workflow page with this group preselected. */
function NewWorkflowButton({ groupName }: { groupName: string }) {
  const navigate = useNavigate()
  const setDraft = useShellStore((s) => s.setNewWorkflowDraft)
  return (
    <Button
      variant="brand"
      onClick={() => {
        setDraft({ group: groupName })
        navigate('/new')
      }}
    >
      <Plus className="size-4" /> New workflow
    </Button>
  )
}

function fmtDate(epoch: number) {
  return new Date(epoch * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return ''
  }
}

/** Which of the group's configured apps this workflow targets. The backend
 * computes `used_apps` (start URLs + every host the recording visited, via the
 * same matcher as the recording gate — see handlers/groups.py); this
 * fallback only covers stale payloads from before that field existed. */
function appsUsedBy(wf: Workflow, apps: GroupApp[]): GroupApp[] {
  if (wf.used_apps) return apps.filter((a) => wf.used_apps!.some((u) => u.id === a.id))
  const hosts = new Set([hostnameOf(wf.target_url), hostnameOf(wf.protected_url)].filter(Boolean))
  if (hosts.size === 0) return []
  return apps.filter((a) => hosts.has(hostnameOf(a.login_url)))
}

function RenameWorkflowButton({ workflow, onRenamed }: { workflow: Workflow; onRenamed: () => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const mutation = useMutation({
    mutationFn: () => updateWorkflow(workflow.id, { name: name.trim() }),
    onSuccess: () => {
      setOpen(false)
      onRenamed()
    },
    onError: (e) => toast.error((e as Error).message),
  })
  const canSave = !!name.trim() && name.trim() !== workflow.name && !mutation.isPending

  return (
    <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (v) setName(workflow.name) }}>
      <DialogTrigger asChild>
        <Button size="icon-sm" variant="outline" title="Edit name" className="border-white/10 bg-white/[0.04] text-zinc-400 hover:text-white">
          <Pencil className="size-3.5" />
        </Button>
      </DialogTrigger>
      <DialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
        <DialogHeader><DialogTitle className="text-white">Edit workflow</DialogTitle></DialogHeader>
        <form
          className="space-y-4 pt-2"
          onSubmit={(e) => { e.preventDefault(); if (canSave) mutation.mutate() }}
        >
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} className="border-white/10 bg-white/5 text-zinc-100" />
          <Button type="submit" className="w-full" disabled={!canSave}>Save</Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** One workflow's whole lifecycle in a single row: name/status on the left,
 * the five-node Record → Compile → Review → Test → Ready to Package rail in
 * the middle, Inspector + Delete on the right. Replaces the old row that only
 * navigated to a separate per-workflow detail page — every action that page
 * used to own now lives here. */
function WorkflowRow({
  wf,
  groupId,
  groupReady,
  apps,
  skillPackBuild,
  onChanged,
}: {
  wf: Workflow
  groupId: string
  groupReady: boolean
  apps: GroupApp[]
  skillPackBuild: SkillPackBuild | null
  onChanged: () => void
}) {
  const navigate = useNavigate()
  const compileRun = useCompileStore((s) => s.run)
  const ownCompiling = compileRun?.status === 'running' && compileRun.workflowId === wf.id
  const compileEtaSeconds = useCompileEtaSeconds(ownCompiling ? compileRun : null)
  const [recordOpen, setRecordOpen] = useState(false)
  const [rerecordConfirmOpen, setRerecordConfirmOpen] = useState(false)
  const [recompileConfirmOpen, setRecompileConfirmOpen] = useState(false)
  // One inline panel per row, never two: opening compile closes test and vice versa.
  // `collapsed` hides the active panel without unmounting it, so a running test or
  // compile keeps its logs while folded away.
  const [panel, setPanel] = useState<'compile' | 'test' | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [compileMode, setCompileMode] = useState<'compile' | 'recompile'>('compile')

  const hasRecording = !!wf.session_id
  const hasSkill = !!wf.skill_id
  const packBuilt = !!skillPackBuild
  const stale = wf.edited_at != null && skillPackBuild != null && wf.edited_at > skillPackBuild.last_built_at
  const usedApps = appsUsedBy(wf, apps)
  const unclaimed = wf.unclaimed_hosts ?? []
  const stage = wf.stage ?? 'ready_to_compile'

  // The last rail node only turns green once this workflow's skill actually has
  // a published release on Conxa Cloud — a passed test merely makes it amber.
  // Scoped to wf.slug (1 Workflow = 1 Skill); only fetched once a test passed,
  // which is the earliest point the node could leave amber anyway.
  const versionsQ = useQuery({
    queryKey: ['skill-pack-versions', wf.slug],
    queryFn: () => fetchSkillPackVersions(wf.slug),
    enabled: stage === 'ready',
    staleTime: 10_000,
  })
  // Upload writes status "ready" (publish_routes.py); a Cloud admin's Release
  // later flips it to "published". Green means "uploaded to Conxa Cloud", so
  // either status counts — checking only "published" left the node amber
  // forever after a normal publish.
  const published = (versionsQ.data?.versions ?? []).some(
    (v) => v.status === 'ready' || v.status === 'published',
  )

  function handleCompileClick() {
    if (!hasRecording) return
    // Compiles run one at a time (see compileStore) — say so here rather than
    // letting the user land on the compile page just to be told no. But if the
    // running compile IS this workflow's own (started, then navigated away
    // from), let it through — CompilePanel re-attaches to it by key instead
    // of refusing to open.
    const key = `${wf.id}:${wf.session_id}:${hasSkill ? 'recompile' : 'compile'}`
    if (compileRun?.status === 'running' && !ownCompiling && compileRun.key !== key) {
      toast.info('Another workflow is compiling. Try again once it finishes.')
      return
    }
    if (hasSkill) {
      setRecompileConfirmOpen(true)
      return
    }
    openCompile('compile')
  }

  function openCompile(mode: 'compile' | 'recompile') {
    setCompileMode(mode)
    setPanel('compile')
    setCollapsed(false)
  }

  function toggleTest() {
    if (panel === 'test') setCollapsed((c) => !c)
    else {
      setPanel('test')
      setCollapsed(false)
    }
  }

  // Clicking the card itself shows/hides whichever panel it has: compile if one
  // ran this session, else test when a test can run.
  function handleCardClick(e: React.MouseEvent) {
    if ((e.target as HTMLElement).closest('button')) return
    if (panel) return setCollapsed((c) => !c)
    if (compileRun?.workflowId === wf.id) openCompile(compileRun.mode) // same mode = same key, so it re-attaches instead of starting a second compile
    else if (hasSkill && packBuilt) toggleTest()
  }

  return (
    <div className="border-b border-white/6 last:border-b-0">
      {/* Two lines, deliberately: identity on the first, the lifecycle rail on
          its own line below. Sharing one line is what made the rail read as five
          loose buttons rather than a pipeline. */}
      <div className="px-5 py-4" onClick={handleCardClick}>
        <div className="flex cursor-pointer items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="truncate text-sm font-medium text-white">{wf.name}</p>
              <WorkflowStageBadge stage={wf.stage} />
            </div>
            {(usedApps.length > 0 || unclaimed.length > 0) && (
              <div className="mt-1 flex flex-wrap items-center gap-1">
                {usedApps.map((a) => (
                  <span
                    key={a.id}
                    className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] font-medium text-zinc-400"
                  >
                    {a.name}
                  </span>
                ))}
                {unclaimed.map((h) => (
                  <span
                    key={h}
                    title={`This workflow visits ${h}, which has no sign-in set up in this group. If it needs you signed in there, add it as an app.`}
                    className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[10px] font-medium text-amber-300"
                  >
                    {h} · no sign-in
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <Button
              size="icon-sm"
              variant="outline"
              title="Inspector"
              className="border-white/10 bg-white/[0.04] text-zinc-400 hover:text-white"
              onClick={() => navigate(`/workflows/${encodeURIComponent(wf.id)}/inspector?from=${encodeURIComponent(`/groups/${groupId}`)}`)}
            >
              <FolderKanban className="size-3.5" />
            </Button>
            <RenameWorkflowButton workflow={wf} onRenamed={onChanged} />
            <DeleteWorkflowButton workflow={wf} onDeleted={onChanged} iconOnly />
          </div>
        </div>

        <div className="mt-3.5">
          <WorkflowStageRail
            stage={stage}
            hasRecording={hasRecording}
            hasSkill={hasSkill}
            groupReady={groupReady}
            packBuilt={packBuilt}
            stale={stale}
            published={published}
            compileBusy={ownCompiling}
            compileEtaLabel={ownCompiling ? formatCompileEta(compileEtaSeconds) : undefined}
            onRecord={() => (hasRecording ? setRerecordConfirmOpen(true) : setRecordOpen(true))}
            onCompile={handleCompileClick}
            onReview={() => navigate(`/edit/${encodeURIComponent(wf.skill_id!)}?from=${encodeURIComponent(`/groups/${groupId}`)}`)}
            onToggleTest={toggleTest}
            onPackage={() => navigate('/publish')}
          />
        </div>
      </div>

      {panel === 'compile' && (
        <div className={cn('border-t border-white/8 bg-black/10 px-5 py-3', collapsed && 'hidden')}>
          <CompilePanel workflowId={wf.id} sessionId={wf.session_id!} mode={compileMode} groupId={groupId} />
        </div>
      )}
      {panel === 'test' && (
        <div className={cn('border-t border-white/8 bg-black/10 px-5 py-3', collapsed && 'hidden')}>
          <WorkflowTestRow wf={wf} skillPackBuild={skillPackBuild} onComplete={onChanged} />
        </div>
      )}

      <AlertDialog open={rerecordConfirmOpen} onOpenChange={setRerecordConfirmOpen}>
        <AlertDialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white">Re-record &ldquo;{wf.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription className="text-zinc-400">
              Your previous recording is backed up to disk, then the new recording replaces it everywhere (compile,
              recompile and tests all use the latest take).
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-white/10 bg-white/5 text-zinc-200">Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="brand"
              onClick={() => {
                setRerecordConfirmOpen(false)
                setRecordOpen(true)
              }}
            >
              Re-record
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <RecordWorkflowDialog
        open={recordOpen}
        onOpenChange={setRecordOpen}
        workflow={wf}
        onRecorded={() => {
          setRecordOpen(false)
          onChanged()
        }}
      />

      <AlertDialog open={recompileConfirmOpen} onOpenChange={setRecompileConfirmOpen}>
        <AlertDialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white">Recompile &ldquo;{wf.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription className="text-zinc-400">
              This rebuilds the skill from the original raw recording and uses your AI Usage Credits. Saved editor changes will be replaced.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-white/10 bg-white/5 text-zinc-200">Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-amber-600 text-white hover:bg-amber-700"
              onClick={() => openCompile('recompile')}
            >
              Recompile
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

export function GroupPage() {
  const { groupId } = useParams<{ groupId: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  // Set by NewGroupDialog's "Create group & sign in": start Connect as soon as the page opens.
  const autoConnect = Boolean((location.state as { autoConnect?: boolean } | null)?.autoConnect)
  const qc = useQueryClient()
  // Browser pane: open while a Connect window is up, then briefly shows "connected".
  const [signIn, setSignIn] = useState<SignInSession | null>(null)
  const [connectedApp, setConnectedApp] = useState<string | null>(null)
  const dockRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (connectedApp === null) return
    const t = window.setTimeout(() => setConnectedApp(null), 2500)
    return () => window.clearTimeout(t)
  }, [connectedApp])
  const paneOpen = signIn !== null || connectedApp !== null
  // Where Chromium should open: the pane's region, in screen coordinates.
  const getWindowRect = () => {
    const el = dockRef.current
    if (!el) return undefined
    const r = el.getBoundingClientRect()
    return { x: Math.round(window.screenX + r.x), y: Math.round(window.screenY + r.y), width: Math.round(r.width), height: Math.round(r.height) }
  }

  const q = useQuery({
    queryKey: ['group', groupId],
    queryFn: () => fetchGroup(groupId!),
    enabled: !!groupId,
  })
  const packQ = useQuery({
    queryKey: ['skill-pack'],
    queryFn: fetchSkillPack,
    staleTime: 10_000,
  })

  // Bounded probe of every captured app's session at once — "ready" (a session file
  // exists) and "verified" (a probe recently confirmed it works) can disagree; this is
  // how the whole group catches up to verified in one action instead of app-by-app.
  const checkAllMut = useMutation({
    mutationFn: () => checkGroupAppAuth(groupId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['group', groupId] })
      qc.invalidateQueries({ queryKey: ['group-auth-status', groupId] })
      qc.invalidateQueries({ queryKey: ['groups'] })
    },
  })

  if (q.isLoading) {
    return (
      <div className="h-full overflow-y-auto">
        <PageHeader title="Group" />
        <p className="px-6 py-6 text-sm text-zinc-500">Loading…</p>
      </div>
    )
  }

  if (q.isError || !q.data) {
    return (
      <div className="h-full overflow-y-auto">
        <PageHeader title="Group" />
        <p className="px-6 py-6 text-sm text-red-400">{(q.error as Error)?.message ?? 'Not found'}</p>
      </div>
    )
  }

  const { group, auth, workflows } = q.data
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['group', groupId] })
    qc.invalidateQueries({ queryKey: ['groups'] })
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title={group.name}
        description={[
          `${auth.apps_authenticated} of ${auth.apps_total} app${auth.apps_total === 1 ? '' : 's'} connected`,
          `${workflows.length} workflow${workflows.length === 1 ? '' : 's'}`,
          auth.ready ? null : 'sign in to start recording',
        ]
          .filter(Boolean)
          .join(' · ')}
        // Usage meters sit in the window title bar; Rename/Delete live in the sidebar's "…" menu
        // on this group's row (GroupMenu), so the page body starts straight at the content.
        titleBarActions={<UsageCards />}
      />

      <div className="mx-auto flex w-full max-w-7xl flex-1 min-h-0 flex-col gap-6 overflow-y-auto px-4 py-6 sm:px-6 lg:flex-row lg:gap-8 lg:overflow-hidden">
        {/* ── Applications column — open, not boxed: the list itself is the content ── */}
        <section aria-labelledby="group-apps-heading" className="flex shrink-0 flex-col lg:w-[26rem]">
          <div className="shrink-0 px-1">
            <h2 id="group-apps-heading" className="text-base font-semibold text-[#e8e6e3]">Applications</h2>
            <p className="mt-1 text-sm text-[#a3a3a3]">Sign in once so workflows in this group can run on your behalf.</p>
          </div>
          <div className="mt-5 flex shrink-0 items-center justify-between gap-2 border-b border-[var(--border-subtle)] px-1 pb-2.5">
            {group.apps.length > 0 ? (
              // Answers "is this group blocked?" without counting rows.
              <div className="flex min-w-0 items-center gap-1.5 text-xs">
                <span aria-hidden className={cn('shrink-0 text-[11px] leading-none', auth.ready ? 'text-status-ok' : 'text-status-warn')}>
                  {auth.ready ? '●' : '▲'}
                </span>
                <span className="truncate text-[#a3a3a3]">
                  {auth.ready
                    ? 'All apps signed in'
                    : `${auth.apps_total - auth.apps_authenticated} of ${auth.apps_total} still need signing in`}
                </span>
              </div>
            ) : (
              <span className="text-xs text-[#8e8e8e]">No apps yet</span>
            )}
            <div className="flex shrink-0 items-center gap-1">
              {group.apps.length > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  title="Re-probe every connected app's session right now"
                  className="text-[#8e8e8e] hover:text-[#e8e6e3]"
                  disabled={checkAllMut.isPending}
                  onClick={() => checkAllMut.mutate()}
                >
                  {checkAllMut.isPending ? <Loader2 className="size-3 animate-spin" /> : 'Check all'}
                </Button>
              )}
              <AddAppDialog groupId={group.id} />
            </div>
          </div>
          <div className="-mx-2 min-h-0 flex-1 overflow-y-auto pt-2">
            {group.apps.length === 0 ? (
              <p className="px-3 py-6 text-sm text-[#8e8e8e]">Add the apps this group's workflows use — you'll sign in to each one once.</p>
            ) : (
              <GroupAuthWizard
                groupId={group.id}
                onAllAuthenticated={refresh}
                editable
                autoStart={autoConnect}
                onAutoStarted={() => navigate(location.pathname, { replace: true, state: null })}
                getWindowRect={getWindowRect}
                onSession={(sess) => {
                  setSignIn(sess)
                  if (sess) setConnectedApp(null)
                }}
                onConnected={(name) => setConnectedApp(name)}
              />
            )}
          </div>
          {/* The page's one next action, placed where sign-in leads to it. */}
          <div className="mt-4 flex shrink-0 items-center gap-4 rounded-[14px] border border-[var(--border-subtle)] bg-[#1e1e1e] p-4">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-[#e8e6e3]">Record a workflow</p>
              <p className="mt-0.5 text-xs text-[#a3a3a3]">
                {group.apps.length > 0 && !auth.ready ? 'Finish signing in above first.' : 'Uses the sessions signed in above.'}
              </p>
            </div>
            <NewWorkflowButton groupName={group.name} />
          </div>
        </section>

        {paneOpen && <SignInPane session={signIn} connectedApp={connectedApp} regionRef={dockRef} />}

        {/* ── Workflows column ── */}
        <div className={cn('min-h-0 min-w-0 flex-1 flex-col overflow-hidden', paneOpen ? 'hidden' : 'flex')}>
          {workflows.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-4 rounded-xl border border-dashed border-white/8 py-16 text-center">
              <div className="rounded-full border border-white/8 bg-white/[0.03] p-4">
                <Layers className="size-7 text-zinc-700" />
              </div>
              <div>
                <p className="text-sm font-medium text-zinc-400">
                  {group.apps.length > 0 && !auth.ready ? `Sign in to ${group.apps.length === 1 ? group.apps[0]!.name : 'your apps'} first` : 'No workflows yet'}
                </p>
                <p className="mt-1 max-w-sm text-xs text-zinc-600">
                  {group.apps.length > 0 && !auth.ready
                    ? `Workflows in ${group.name} need a signed-in session before you can record them.`
                    : 'Create a workflow in this group to start automating.'}
                </p>
              </div>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-white/8 bg-white/[0.02]">
              <div className="flex shrink-0 items-center justify-between border-b border-white/8 px-5 py-3.5">
                <span className="text-[0.6875rem] font-medium text-zinc-400">Workflows</span>
                <span className="text-[0.6875rem] text-zinc-600">Updated {fmtDate(Math.max(...workflows.map((w) => w.updated_at)))}</span>
              </div>
              <ScrollArea className="min-h-0 flex-1">
                {workflows.map((wf) => (
                  <WorkflowRow
                    key={wf.id}
                    wf={wf}
                    groupId={group.id}
                    groupReady={auth.ready}
                    apps={group.apps}
                    skillPackBuild={packQ.data?.skill_pack?.build ?? null}
                    onChanged={refresh}
                  />
                ))}
              </ScrollArea>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
