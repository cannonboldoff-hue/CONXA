import { type ChangeEvent, type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { PageHeader } from '@/components/layout/PageHeader'
import { HumanEditPoolBadge } from '@/components/EntitlementMeters'
import type { WorkflowResponse } from '@/types/workflow'
import {
  deleteStep,
  errorMessage,
  fetchMetrics,
  fetchRecordingScreenshots,
  fetchSkillList,
  fetchWorkflow,
  postApplyRecordingVisual,
  postApplyStepFrame,
  postClearStepVisual,
  postInsertStep,
  postReorder,
  postSignOff,
  redoWorkflow,
  undoWorkflow,
} from '@/api/workflowApi'
import { fetchWorkflows, normalizeWorkflowList } from '@/api/workflowsApi'
import { EditConversationPanel } from '@/components/conversation/EditConversationPanel'
import { flowRerecordFrom, flowTestPrepare } from '@/api/conversationApi'
import { useShellData } from '@/hooks/useShellData'
import { compactStepLabel } from '@/lib/workflowViewerHelpers'
import { RecordingScreenshotsPanel } from '@/components/RecordingScreenshotsPanel'
import { WorkflowPlanPanel } from '@/components/WorkflowPlanPanel'
import { ReviewQuestionsDialog } from '@/components/ReviewQuestionsDialog'
import { DiagnosticsPanel } from '@/components/DiagnosticsPanel'
import { WorkflowViewer } from '@/components/WorkflowViewer'
import { UnsavedChangeGuard } from '@/components/UnsavedChangeGuard'
import { InlineRetargetFlow, type InlineRetargetFlowHandle } from '@/components/retarget/InlineRetargetFlow'
import { SuggestionsInlinePanel } from '@/components/SuggestionsPanel'
import { ParameterizationInlinePanel } from '@/components/ParameterizationDrawer'
import { useEditorStore } from '@/store/editorStore'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { InfoHint } from '@/components/ui/info-hint'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { editorHelp } from '@/lib/editorHelp'
import { fieldSelectClass } from '@/lib/fieldStyles'
import { cn } from '@/lib/utils'
import {
  AlertCircle,
  BadgeCheck,
  ChevronDown,
  Home,
  Image as ImageIcon,
  Lightbulb,
  type LucideIcon,
  Map as MapIcon,
  Redo2,
  RefreshCw,
  GitBranch,
  Video,
  SlidersHorizontal,
  Undo2,
  Wrench,
} from 'lucide-react'
import type { HelpEntry } from '@/lib/editorHelp'

type ToolPaneKey = 'suggestions' | 'variables' | 'screenshots' | 'workflowPlan' | 'diagnostics'

const FLOW_STEPS = ['Record', 'Compile', 'Edit', 'Approve'] as const

/** Compact "how editing works" strip for newcomers on the landing screen. */
function FlowExplainer() {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500">
      {FLOW_STEPS.map((step, i) => (
        <span key={step} className="flex items-center gap-1.5">
          <span
            className={cn(
              'rounded-md border px-1.5 py-0.5 font-medium',
              step === 'Edit'
                ? 'border-brand/40 bg-brand/10 text-brand'
                : 'border-white/10 bg-white/[0.03] text-zinc-400',
            )}
          >
            {step}
          </span>
          {i < FLOW_STEPS.length - 1 ? <span className="text-zinc-600">→</span> : null}
        </span>
      ))}
    </div>
  )
}

export function HumanEditPage() {
  const EDITOR_SIDEBAR_WIDTH_KEY = 'ai-native-editor-sidebar-width'
  const EDITOR_SIDEBAR_MIN = 280
  const EDITOR_SIDEBAR_MAX = 560
  const { skillId } = useParams<{ skillId: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const fromPath = searchParams.get('from') ? decodeURIComponent(searchParams.get('from')!) : null
  const qc = useQueryClient()
  // The workflow this skill belongs to — Human Edit's conversation is that workflow's own thread, so
  // what is said and decided here is on the same page as everything else that happened to it.
  const { workflowsById } = useShellData()
  const convWorkflow = useMemo(
    () => [...workflowsById.values()].find((w) => w.workflow.skill_id === skillId)?.workflow ?? null,
    [workflowsById, skillId],
  )
  const fromConversation = Boolean(convWorkflow && fromPath?.startsWith('/w/'))
  const [flowStatus, setFlowStatus] = useState('Load a saved skill to edit, or go home to record a new one.')
  const [resumePick, setResumePick] = useState('')
  const [manualSkillId, setManualSkillId] = useState('')
  const [workflowPaneWidth, setWorkflowPaneWidth] = useState(340)
  const [isResizingPane, setIsResizingPane] = useState(false)
  const [openTool, setOpenTool] = useState<ToolPaneKey | null>(null)
  const [showAllScreenshots, setShowAllScreenshots] = useState(false)
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null)
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const splitPaneRef = useRef<HTMLDivElement | null>(null)
  const stepEditorRef = useRef<InlineRetargetFlowHandle>(null)
  const selected = useEditorStore((s) => s.selectedStepIndex)
  const focusedBranchIndex = useEditorStore((s) => s.focusedBranchIndex)
  const setValidationReport = useEditorStore((s) => s.setValidationReport)
  const setSelectedStepIndex = useEditorStore((s) => s.setSelectedStepIndex)
  const canUndo = useEditorStore((s) => s.canUndo)
  const canRedo = useEditorStore((s) => s.canRedo)
  const dirtyCount = useEditorStore((s) => s.dirtySteps.size)
  const setHistoryState = useEditorStore((s) => s.setHistoryState)
  const resetHistory = useEditorStore((s) => s.resetHistory)

  // Only used by the "Resume a skill" picker on the no-skill-selected landing state
  // below — skip the full skills-directory scan entirely when a skill is already
  // open (e.g. arriving here via a "Review" link), since the result is unused then.
  const skillsListQ = useQuery({
    queryKey: ['skillList'],
    queryFn: fetchSkillList,
    staleTime: 60_000,
    enabled: !skillId,
  })

  const q = useQuery({
    queryKey: ['workflow', skillId],
    queryFn: () => fetchWorkflow(skillId as string),
    enabled: Boolean(skillId),
    staleTime: 30_000,
  })

  const version = Number((q.data?.package_meta.version as number) ?? 0)
  const savedSkills = skillsListQ.data?.skills ?? []

  const onWorkflowUpdated = useCallback(
    (wf: WorkflowResponse) => {
      if (!skillId) return
      qc.setQueryData(['workflow', skillId], wf)
    },
    [qc, skillId],
  )

  // Shared by Back, Recompile and Approve — lands on this skill's workflow group page
  // (recompile + testing live there), falling back to the workflows list if it can't be found.
  const goToOwnerGroup = useCallback(async () => {
    try {
      const list = normalizeWorkflowList(await fetchWorkflows())
      const owner = list.find((wf) => wf.skill_id === skillId)
      navigate(owner ? `/groups/${encodeURIComponent(owner.group_id)}` : '/workflows')
    } catch {
      navigate(fromPath ?? '/workflows')
    }
  }, [skillId, fromPath, navigate])

  const currentStep = useMemo(() => {
    if (!q.data || selected === null) return null
    return q.data.steps.find((s) => s.step_index === selected) ?? null
  }, [q.data, selected])

  // A focused nested for_each step (clicked in ForEachSubList.tsx) — the recording-screenshots
  // tool (opened from InlineRetargetFlow.tsx's Pick Element phase) must read/write the nested
  // step's own screenshot data, not the parent for_each wrapper's, and address it the same way
  // InlineRetargetFlow.tsx does: `for_each.steps[N]` (see step_path.py).
  const nestedForEachStep =
    currentStep?.for_each_summary && focusedBranchIndex !== null
      ? (currentStep.for_each_steps[focusedBranchIndex] ?? null)
      : null
  const nestedPath = nestedForEachStep ? `for_each.steps[${focusedBranchIndex}]` : undefined
  const effectiveStep = nestedForEachStep ?? currentStep

  useEffect(() => {
    if (skillId) {
      setFlowStatus('Editing workflow.')
      resetHistory()
    }
  }, [skillId, resetHistory])

  const onHistoryUpdate = useCallback(
    (canUndo: boolean, canRedo: boolean) => setHistoryState(canUndo, canRedo),
    [setHistoryState],
  )

  useEffect(() => {
    if (!q.data) return
    if (q.data.steps.length === 0) {
      setSelectedStepIndex(null)
      return
    }
    if (selected === null || !q.data.steps.some((step) => step.step_index === selected)) {
      setSelectedStepIndex(q.data.steps[0].step_index)
    }
  }, [q.data, selected, setSelectedStepIndex])

  useEffect(() => {
    const stored = window.localStorage.getItem(EDITOR_SIDEBAR_WIDTH_KEY)
    if (!stored) return
    const parsed = Number.parseInt(stored, 10)
    if (Number.isNaN(parsed)) return
    setWorkflowPaneWidth(Math.max(EDITOR_SIDEBAR_MIN, Math.min(EDITOR_SIDEBAR_MAX, parsed)))
  }, [])

  useEffect(() => {
    window.localStorage.setItem(EDITOR_SIDEBAR_WIDTH_KEY, String(workflowPaneWidth))
  }, [workflowPaneWidth])

  useEffect(() => {
    if (!isResizingPane) return
    const onMouseMove = (event: MouseEvent) => {
      const rect = splitPaneRef.current?.getBoundingClientRect()
      if (!rect) return
      const proposed = event.clientX - rect.left
      const nextWidth = Math.max(EDITOR_SIDEBAR_MIN, Math.min(EDITOR_SIDEBAR_MAX, proposed))
      setWorkflowPaneWidth(nextWidth)
    }
    const onMouseUp = () => {
      setIsResizingPane(false)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [isResizingPane])

  const handleUndo = useCallback(async () => {
    if (!skillId || !canUndo) return
    try {
      const r = await undoWorkflow(skillId)
      onWorkflowUpdated(r.workflow)
      setHistoryState(r.can_undo, r.can_redo)
      toast.success('Undone')
    } catch (err) {
      toast.error(errorMessage(err, 'Could not undo'))
    }
  }, [skillId, canUndo, onWorkflowUpdated, setHistoryState])

  // A change card in the conversation was applied or undone — those commands write the skill
  // directly, so reload the editor's copy (and its undo/redo flags) rather than guess.
  const onConversationChanged = useCallback(() => {
    if (!skillId) return
    void qc.invalidateQueries({ queryKey: ['workflow', skillId] })
  }, [qc, skillId])

  const handleRedo = useCallback(async () => {
    if (!skillId || !canRedo) return
    try {
      const r = await redoWorkflow(skillId)
      onWorkflowUpdated(r.workflow)
      setHistoryState(r.can_undo, r.can_redo)
      toast.success('Redone')
    } catch (err) {
      toast.error(errorMessage(err, 'Could not redo'))
    }
  }, [skillId, canRedo, onWorkflowUpdated, setHistoryState])

  useEffect(() => {
    if (!skillId) return
    const onKey = (e: KeyboardEvent) => {
      const ctrl = e.ctrlKey || e.metaKey
      if (!ctrl) return
      const tag = (document.activeElement as HTMLElement | null)?.tagName?.toLowerCase()
      const isEditable =
        tag === 'input' ||
        tag === 'textarea' ||
        (document.activeElement as HTMLElement | null)?.isContentEditable
      if (isEditable) return
      if (e.key === 'z' && !e.shiftKey) {
        e.preventDefault()
        void handleUndo()
      } else if (e.key === 'y' || (e.key === 'z' && e.shiftKey)) {
        e.preventDefault()
        void handleRedo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [skillId, handleUndo, handleRedo])

  const onReorder = (newOrder: number[]) => {
    if (!skillId) return
    const prevSelected = useEditorStore.getState().selectedStepIndex
    postReorder(skillId, newOrder)
      .then((r) => {
        onWorkflowUpdated(r.workflow)
        if (r.can_undo !== undefined) setHistoryState(r.can_undo, r.can_redo ?? false)
        if (prevSelected !== null) {
          const newPos = newOrder.findIndex((origIdx) => origIdx === prevSelected)
          if (newPos !== -1) useEditorStore.getState().setSelectedStepIndex(newPos)
        }
      })
      .catch((err) => {
        toast.error(errorMessage(err, 'Could not reorder steps'))
        void q.refetch()
      })
  }

  const onDelete = async (index: number) => {
    if (!skillId) return
    if (index === selected) {
      const savedOk = await (stepEditorRef.current?.submitIfDirty() ?? Promise.resolve(true))
      if (!savedOk) {
        toast.error('Could not save the open step — fix errors before deleting.')
        return
      }
    }
    deleteStep(skillId, index)
      .then((r) => {
        onWorkflowUpdated(r.workflow)
        if (r.can_undo !== undefined) setHistoryState(r.can_undo, r.can_redo ?? false)
        useEditorStore.getState().reindexDirtyAfterDelete(index)
        const n = r.workflow.steps.length
        const sel = useEditorStore.getState().selectedStepIndex
        if (n === 0) {
          useEditorStore.getState().setSelectedStepIndex(null)
          return
        }
        if (sel === null) {
          useEditorStore.getState().setSelectedStepIndex(0)
          return
        }
        if (index < sel) useEditorStore.getState().setSelectedStepIndex(sel - 1)
        else if (index === sel) useEditorStore.getState().setSelectedStepIndex(Math.min(sel, n - 1))
      })
      .catch((err: Error) => {
        toast.error(errorMessage(err, 'Could not delete step'))
        void q.refetch()
      })
  }

  const onAddAction = async (actionKind: string) => {
    if (!skillId) return
    const savedOk = await (stepEditorRef.current?.submitIfDirty() ?? Promise.resolve(true))
    if (!savedOk) {
      toast.error('Could not save the open step before adding an action.')
      return
    }
    const currentSteps = q.data?.steps ?? []
    const insertAfter = selected === null ? (currentSteps.length > 0 ? currentSteps.length - 1 : null) : selected
    try {
      const res = await postInsertStep(skillId, {
        action_kind: actionKind,
        insert_after: insertAfter,
      })
      onWorkflowUpdated(res.workflow)
      if (res.can_undo !== undefined) setHistoryState(res.can_undo, res.can_redo ?? false)
      const nextIndex = insertAfter === null ? res.workflow.steps.length - 1 : insertAfter + 1
      useEditorStore.getState().setSelectedStepIndex(nextIndex)
      toast.success('Action added')
    } catch (err) {
      toast.error(errorMessage(err, 'Could not add action'))
    }
  }

  // From a workflow's conversation the primary action is Test changes: save the open step, put the test
  // card in the thread, and go back. Approval is not offered here — it follows a passing test, in the
  // conversation. (Reached from the group page instead, the legacy Approve below still applies.)
  const testChanges = async () => {
    if (!skillId || !convWorkflow) return
    const savedOk = await (stepEditorRef.current?.submitIfDirty() ?? Promise.resolve(true))
    if (!savedOk || useEditorStore.getState().dirtySteps.size > 0) {
      toast.error('Save the open step first — fix any errors or tap Save step, then test.')
      return
    }
    try {
      await flowTestPrepare(convWorkflow.id)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not start a test — please try again.'))
      return
    }
    navigate(`/w/${convWorkflow.id}`)
  }

  /** Replay the workflow up to the open step and record the rest by hand; the result replaces this step
   *  and everything after it. Needs the open step saved first, so the replay runs what is on screen. */
  const rerecordFromHere = async (mode: 'replace' | 'branch' = 'replace') => {
    if (!skillId || !convWorkflow || selected === null) return
    const savedOk = await (stepEditorRef.current?.submitIfDirty() ?? Promise.resolve(true))
    if (!savedOk || useEditorStore.getState().dirtySteps.size > 0) {
      toast.error('Save the open step first, then record from it.')
      return
    }
    try {
      await flowRerecordFrom(skillId, selected, mode)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not start recording from that step.'))
      return
    }
    navigate(`/w/${convWorkflow.id}`)
  }

  const approveWorkflow = async () => {
    if (!skillId) return
    const savedOk = await (stepEditorRef.current?.submitIfDirty() ?? Promise.resolve(true))
    if (!savedOk) {
      toast.error('Could not save the open step — fix errors or tap Save step, then try Approve.')
      return
    }
    const dirtySteps = useEditorStore.getState().dirtySteps
    if (dirtySteps.size > 0) {
      const label =
        dirtySteps.size === 1
          ? `step ${[...dirtySteps][0]}`
          : `${dirtySteps.size} steps (${[...dirtySteps].sort((a, b) => a - b).join(', ')})`
      toast.warning(`Still have unsaved changes on ${label} — switch to each and save before approving`)
      return
    }
    let signOff
    try {
      signOff = await postSignOff(skillId)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not approve this workflow — please try again.'))
      return
    }
    setFlowStatus('Approved; your skill stays the same id and title on disk.')
    void qc.invalidateQueries({ queryKey: ['skillList'] })
    if (signOff.built) {
      toast.success(`${skillId} approved and built — test it from the group page.`)
    } else if (signOff.build_error) {
      toast.error(`Approved, but the package failed to build: ${signOff.build_error}`)
    } else {
      // Sign-off now always attempts a build scoped to this one workflow (see
      // cmd_sign_off_workflow) — a sibling workflow's readiness never blocks
      // it, so `waiting_on` is always empty and `built` is only false when
      // this workflow itself has no matching skill yet.
      toast.success(`${skillId} saved in place — same skill id as when you compiled from the recording.`)
    }
    // Land on the workflow's group page — testing lives there (WorkflowTestRow), not on a
    // dedicated Test Skill page anymore.
    await goToOwnerGroup()
  }

  const onDroppedRecordingScreenshot = useCallback(
    async (stepIndex: number, eventIndex: number, frameLabel?: string, path?: string) => {
      if (!skillId) return
      try {
        const res = await postApplyRecordingVisual(
          skillId,
          stepIndex,
          { event_index: eventIndex, frame_label: frameLabel },
          path,
        )
        onWorkflowUpdated(res.workflow)
        if (res.can_undo !== undefined) setHistoryState(res.can_undo, res.can_redo ?? false)
        useEditorStore.getState().clearStepDirty(stepIndex)
        void qc.invalidateQueries({ queryKey: ['recordingScreenshots', skillId] })
        toast.success('Screenshot attached — anchors recomputed')
      } catch (err) {
        toast.error(errorMessage(err, 'Could not apply recording screenshot'))
      }
    },
    [onWorkflowUpdated, qc, setHistoryState, skillId],
  )

  const allScreenshotsQ = useQuery({
    queryKey: ['recordingScreenshots', skillId],
    queryFn: () => fetchRecordingScreenshots(skillId as string),
    enabled: !!skillId && openTool === 'screenshots' && showAllScreenshots,
  })

  const onSelectAllScreenshot = useCallback(
    (eventIndex: number, frameLabel: string | null) => {
      if (selected === null) return
      // null means this event has no precomputed timed frame — apply its representative
      // full_screenshot by omitting frame_label, not a frame image that doesn't exist.
      void onDroppedRecordingScreenshot(selected, eventIndex, frameLabel ?? undefined, nestedPath)
    },
    [onDroppedRecordingScreenshot, selected, nestedPath],
  )

  const onClearStepVisual = useCallback(
    async (stepIndex: number, path?: string) => {
      if (!skillId) return
      try {
        const res = await postClearStepVisual(skillId, stepIndex, path)
        onWorkflowUpdated(res.workflow)
        if (res.can_undo !== undefined) setHistoryState(res.can_undo, res.can_redo ?? false)
        useEditorStore.getState().clearStepDirty(stepIndex)
        toast.success('Screenshot removed — anchors cleared')
      } catch (err) {
        toast.error(errorMessage(err, 'Could not remove screenshot'))
      }
    },
    [onWorkflowUpdated, setHistoryState, skillId],
  )

  const onApplyStepFrame = useCallback(
    async (frameLabel: string) => {
      if (!skillId || selected === null) return
      try {
        const res = await postApplyStepFrame(skillId, selected, frameLabel, nestedPath)
        onWorkflowUpdated(res.workflow)
        if (res.can_undo !== undefined) setHistoryState(res.can_undo, res.can_redo ?? false)
        useEditorStore.getState().clearStepDirty(selected)
        toast.success('Frame applied — anchors recomputed')
      } catch (err) {
        toast.error(errorMessage(err, 'Could not apply frame'))
      }
    },
    [onWorkflowUpdated, selected, setHistoryState, skillId, nestedPath],
  )

  const openSkillForEdit = useCallback(
    (id: string) => {
      const sid = id.trim()
      if (!sid) {
        setFlowStatus('Enter or choose a skill id.')
        toast.error('Enter or pick a skill id first.')
        return
      }
      setSelectedStepIndex(0)
      setValidationReport(null)
      setFlowStatus(`Opened skill ${sid} for editing.`)
      navigate(`/edit/${sid}`)
      void qc.invalidateQueries({ queryKey: ['workflow', sid] })
      void qc.invalidateQueries({ queryKey: ['skillList'] })
    },
    [qc, navigate, setSelectedStepIndex, setValidationReport],
  )

  const refreshMetrics = useCallback(() => {
    fetchMetrics()
      .then((data) => {
        setMetrics(data)
        toast.message('Metrics refreshed')
      })
      .catch((err: Error) => {
        setMetrics({ error: err.message })
        toast.error('Could not load metrics')
      })
  }, [])

  if (!skillId) {
    return (
      <div className="h-full overflow-y-auto">
        <PageHeader
          title="Edit Skill"
          description="After recording compiles into a skill, open it here — your edits overwrite that same skill package (same id/title)."
          actions={
            <>
              <Button variant="outline" size="sm" asChild className="border-white/10 bg-white/[0.04] text-zinc-200 hover:bg-white/[0.08]">
                <Link to="/workflows">
                  <Home className="size-3.5" />
                  Home
                </Link>
              </Button>
            </>
          }
        />
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6">
          <section className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
            <Card className="border-white/8 bg-white/[0.035] shadow-none">
              <CardHeader className="border-b border-white/8">
                <CardTitle className="flex items-center gap-2 text-white">
                  Open a skill
                  <InfoHint {...editorHelp.openSkill} side="bottom" align="start" />
                </CardTitle>
                <CardDescription className="text-zinc-500">
                  Fine-tune a compiled workflow, then Approve to save it back to the same skill.
                </CardDescription>
                <FlowExplainer />
              </CardHeader>
              <CardContent className="space-y-6 pt-6">
                <fieldset className="space-y-4 rounded-xl border border-white/8 bg-black/20 p-4">
                  <legend className="flex items-center gap-1.5 px-1.5 text-sm font-semibold text-zinc-200">
                    <span>Resume a skill</span>
                    <InfoHint {...editorHelp.resume} side="top" align="start" />
                  </legend>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="grid gap-2">
                      <Label className="text-zinc-200" htmlFor="resume">
                        Saved skills
                      </Label>
                      <div className="relative">
                        <select
                          id="resume"
                          className={cn(
                            fieldSelectClass,
                            'h-10 appearance-none border-white/10 bg-black/30 pr-10 text-zinc-100 transition-colors hover:border-white/20 focus-visible:border-white/25',
                          )}
                          value={resumePick}
                          onChange={(e: ChangeEvent<HTMLSelectElement>) => {
                            setResumePick(e.target.value)
                            setManualSkillId('')
                          }}
                        >
                          <option value="" className="bg-zinc-950 text-zinc-200">
                            Choose a skill to resume...
                          </option>
                          {savedSkills.map((s) => (
                            <option key={s.skill_id} value={s.skill_id} className="bg-zinc-950 text-zinc-100">
                              {s.title} - v{s.version} - {s.step_count} steps
                            </option>
                          ))}
                        </select>
                        <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-zinc-400" />
                      </div>
                      <p className="text-xs text-zinc-500">
                        {savedSkills.length > 0
                          ? `${savedSkills.length} saved skill${savedSkills.length === 1 ? '' : 's'} available`
                          : 'No saved skills yet. Enter a skill id manually to continue.'}
                      </p>
                    </div>
                    <div className="grid gap-2">
                      <Label className="text-zinc-200" htmlFor="manualId">
                        Or enter skill id
                      </Label>
                      <Input
                        id="manualId"
                        type="text"
                        placeholder="skill_abc123"
                        value={manualSkillId}
                        onChange={(e: ChangeEvent<HTMLInputElement>) => {
                          setManualSkillId(e.target.value)
                          setResumePick('')
                        }}
                        className="border-white/10 bg-black/20 text-zinc-100 placeholder:text-zinc-500"
                      />
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="brand" onClick={() => openSkillForEdit(resumePick || manualSkillId)}>
                      Load and edit
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void skillsListQ.refetch()}
                      disabled={skillsListQ.isFetching}
                      className="gap-1.5 border-white/10 bg-white/[0.04] text-zinc-200 hover:bg-white/[0.08]"
                    >
                      <RefreshCw className={cn('size-3.5', skillsListQ.isFetching && 'animate-spin')} />
                      Refresh list
                    </Button>
                  </div>
                  {skillsListQ.isError ? (
                    <div
                      className="flex items-start gap-2.5 rounded-lg border border-red-500/20 bg-red-500/[0.06] px-3 py-2.5"
                      role="alert"
                    >
                      <AlertCircle className="mt-0.5 size-4 shrink-0 text-red-400" />
                      <p className="text-sm text-red-300">{(skillsListQ.error as Error).message}</p>
                    </div>
                  ) : null}
                </fieldset>

                <p className="text-sm text-zinc-500" role="status" aria-live="polite">
                  {flowStatus}
                </p>
              </CardContent>
            </Card>

            <Card className="border-white/8 bg-white/[0.035] shadow-none">
              <CardHeader className="border-b border-white/8">
                <CardTitle className="flex items-center gap-2 text-white">
                  Diagnostics
                  <Badge variant="outline" className="border-white/10 text-[0.6rem] font-normal uppercase tracking-wide text-zinc-500">
                    Technical
                  </Badge>
                  <InfoHint {...editorHelp.diagnostics} side="bottom" align="end" className="ml-auto" />
                </CardTitle>
                <CardDescription className="text-zinc-500">
                  Optional behind-the-scenes signals. You don't need these to edit — they help when reporting an issue.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 pt-6">
                <Collapsible open={diagnosticsOpen} onOpenChange={setDiagnosticsOpen}>
                  <CollapsibleTrigger asChild>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between gap-2 rounded-lg border border-white/8 bg-black/20 px-3 py-2 text-sm text-zinc-300 outline-none transition-colors hover:bg-white/[0.05] focus-visible:ring-2 focus-visible:ring-brand-ring"
                    >
                      <span>{diagnosticsOpen ? 'Hide raw metrics' : 'Show raw metrics'}</span>
                      <ChevronDown className={cn('size-4 text-zinc-500 transition-transform duration-200', diagnosticsOpen && 'rotate-180')} />
                    </button>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <ScrollArea className="mt-3 h-64 rounded-lg border border-white/8 bg-black/20 p-3">
                      <pre className="font-mono text-xs leading-6 break-words whitespace-pre-wrap text-zinc-400">
                        {JSON.stringify(metrics ?? { info: 'Click "Refresh metrics"' }, null, 2)}
                      </pre>
                    </ScrollArea>
                  </CollapsibleContent>
                </Collapsible>
                <Button
                  type="button"
                  variant="outline"
                  onClick={refreshMetrics}
                  className="w-full gap-1.5 border-white/10 bg-white/[0.04] text-zinc-200 hover:bg-white/[0.08]"
                >
                  <RefreshCw className="size-3.5" />
                  Refresh metrics
                </Button>
              </CardContent>
            </Card>
          </section>
        </div>
      </div>
    )
  }

  if (q.isLoading) {
    return (
      <div className="h-full overflow-y-auto">
        <PageHeader title="Edit Skill" description="Preparing the workflow editor and loading steps." />
        <div className="flex flex-1 flex-col gap-4 px-4 py-4 md:px-6">
          <div className="bg-muted/15 border-border/60 max-w-2xl rounded-lg border p-4 shadow-sm">
            <Skeleton className="mb-2 h-4 w-32" />
            <Skeleton className="h-3 w-full max-w-md" />
          </div>
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 md:min-h-0 md:grid-cols-3">
            <Skeleton className="h-full min-h-[220px] rounded-lg md:min-h-0" />
            <Skeleton className="h-full min-h-[220px] rounded-lg md:min-h-0" />
            <Skeleton className="h-full min-h-[220px] rounded-lg md:min-h-0" />
          </div>
        </div>
      </div>
    )
  }
  if (q.isError) {
    return (
      <div className="h-full overflow-y-auto">
        <PageHeader title="Edit Skill" description="The skill could not be opened." />
        <main className="flex flex-1 items-center justify-center p-6">
          <Card className="max-w-md border-red-500/20 bg-red-500/5 shadow-none">
            <CardHeader>
              <div className="flex items-start gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-red-500/20 bg-red-500/10 text-red-300">
                  <AlertCircle className="size-5" />
                </div>
                <div className="min-w-0 space-y-1">
                  <CardTitle className="text-base text-white">Failed to load skill</CardTitle>
                  <CardDescription className="break-words text-red-200">{(q.error as Error).message}</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <Button variant="default" asChild className="bg-white text-black hover:bg-zinc-200">
                <Link to={fromPath ?? '/edit'}>
                  {fromPath ? 'Back to Workflow' : 'Back to choose skill'}
                </Link>
              </Button>
            </CardContent>
          </Card>
        </main>
      </div>
    )
  }
  if (!q.data) {
    return (
      <div className="h-full overflow-y-auto">
        <PageHeader title="Edit Skill" description="The editor did not receive workflow data." />
        <main className="flex flex-1 items-center justify-center p-6">
          <Card className="max-w-md border-white/10 bg-white/[0.04] shadow-none">
            <CardHeader>
              <CardTitle className="text-base text-white">No workflow data</CardTitle>
              <CardDescription className="text-zinc-400">
                The compiled skill opened, but Build Studio did not receive a workflow payload. Go back to the
                workflow and open the compiled skill again.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button variant="default" asChild className="bg-white text-black hover:bg-zinc-200">
                <Link to={fromPath ?? '/workflows'}>{fromPath ? 'Back to Workflow' : 'Back to Workflows'}</Link>
              </Button>
            </CardContent>
          </Card>
        </main>
      </div>
    )
  }

  const wf = q.data
  const skillTitle =
    typeof wf.package_meta.title === 'string' && wf.package_meta.title.trim()
      ? wf.package_meta.title.trim()
      : skillId
  const suggestionCount = wf.suggestions.length
  const splitPaneStyle = {
    ['--workflow-pane-width' as string]: `${workflowPaneWidth}px`,
  } as CSSProperties

  const toolPanes: {
    key: ToolPaneKey
    label: string
    icon: LucideIcon
    iconClass: string
    help: HelpEntry
    controls: string
    count?: number | string
  }[] = [
    { key: 'suggestions', label: 'Suggestions', icon: Lightbulb, iconClass: 'text-amber-300', help: editorHelp.toolSuggestions, controls: 'suggestions-modal', count: suggestionCount },
    { key: 'variables', label: 'Input variables', icon: SlidersHorizontal, iconClass: 'text-sky-300', help: editorHelp.toolVariables, controls: 'variables-modal' },
    { key: 'workflowPlan', label: 'Workflow plan', icon: MapIcon, iconClass: 'text-violet-300', help: editorHelp.toolWorkflowPlan, controls: 'workflow-plan-modal' },
    { key: 'diagnostics', label: 'Diagnostics', icon: Wrench, iconClass: 'text-zinc-400', help: editorHelp.toolDiagnostics, controls: 'diagnostics-modal' },
  ]
  // Not a top-bar tab — opened from the "Recording screenshots" button inside the Pick
  // Element phase of the re-target wizard, but reuses the same dialog/state machinery.
  const screenshotsTool = {
    key: 'screenshots' as const,
    label: 'Recording screenshots',
    icon: ImageIcon,
    iconClass: 'text-fuchsia-300',
    help: editorHelp.toolScreenshots,
    controls: 'recording-screenshots-modal',
    count: effectiveStep?.screenshot?.frames?.length ?? '—',
  }
  const activeTool = openTool === 'screenshots' ? screenshotsTool : (toolPanes.find((t) => t.key === openTool) ?? null)
  // Workflow plan / Diagnostics use the redesigned 760px auto-height modal with a Close footer.
  const isCompactTool = activeTool?.key === 'workflowPlan' || activeTool?.key === 'diagnostics'
  const compileHealth = wf.compile_health
  const stepsBelowThreshold = compileHealth.steps_below_threshold ?? []
  const statusWord =
    compileHealth.status === 'failed' ? 'compile issues' : compileHealth.status === 'review_needed' ? 'review recommended' : null

  return (
    <TooltipProvider>
    <div className="flex h-full min-h-0 flex-col">
    <PageHeader
      title={skillTitle}
      description={
        <div className="flex max-w-full flex-nowrap items-center gap-x-1.5 overflow-hidden whitespace-nowrap text-sm text-zinc-500">
          <span>{wf.steps.length} step{wf.steps.length === 1 ? '' : 's'}</span>
          {statusWord && <span className="text-status-warn">· {statusWord}</span>}
        </div>
      }
      onBack={() => (fromPath ? navigate(fromPath) : void goToOwnerGroup())}
      backLabel={fromConversation ? 'Back to conversation' : undefined}
      inlineActions
      actions={
        <div className="flex items-center gap-2.5">
          {/* Sits in AppChrome's header row after the title (PageHeader's inlineActions):
              Workflow plan + Input variables launchers (Suggestions' old spot), the More
              menu (Suggestions, Diagnostics, credits), then the commit cluster
              (unsaved / undo-redo / Approve). */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-controls="workflow-plan-modal"
                className={cn(
                  'h-9 gap-1.5 px-3 text-zinc-300 hover:bg-white/[0.07] hover:text-white',
                  openTool === 'workflowPlan' && 'bg-brand/14 text-brand hover:bg-brand/18 hover:text-brand',
                )}
                onClick={() => setOpenTool('workflowPlan')}
              >
                <MapIcon className={cn('size-3.5 shrink-0', openTool !== 'workflowPlan' && 'text-violet-300')} />
                <span className="hidden sm:inline">Workflow plan</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Workflow plan</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-controls="variables-modal"
                className={cn(
                  'h-9 gap-1.5 px-3 text-zinc-300 hover:bg-white/[0.07] hover:text-white',
                  openTool === 'variables' && 'bg-brand/14 text-brand hover:bg-brand/18 hover:text-brand',
                )}
                onClick={() => setOpenTool('variables')}
              >
                <SlidersHorizontal className={cn('size-3.5 shrink-0', openTool !== 'variables' && 'text-sky-300')} />
                <span className="hidden sm:inline">Input variables</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Input variables</TooltipContent>
          </Tooltip>
          <div className="flex shrink-0 items-center gap-2.5">
          {dirtyCount > 0 && (
            <span
              className="hidden h-9 items-center gap-1.5 rounded-md border border-amber-400/25 bg-amber-400/10 px-2.5 text-[0.7rem] font-medium text-amber-200 sm:inline-flex"
              role="status"
              aria-live="polite"
            >
              <span className="size-1.5 rounded-full bg-amber-300" aria-hidden />
              {dirtyCount} unsaved
            </span>
          )}
          <div className="flex h-9 items-center overflow-hidden rounded-md border border-white/12 bg-white/[0.045]">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-full rounded-none px-3 text-zinc-300 hover:bg-white/[0.07] hover:text-white disabled:opacity-30"
                  onClick={() => void handleUndo()}
                  disabled={!canUndo}
                  aria-label="Undo"
                >
                  <Undo2 className="size-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Undo · Ctrl+Z</TooltipContent>
            </Tooltip>
            <div className="h-5 w-px bg-white/12" />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-full rounded-none px-3 text-zinc-300 hover:bg-white/[0.07] hover:text-white disabled:opacity-30"
                  onClick={() => void handleRedo()}
                  disabled={!canRedo}
                  aria-label="Redo"
                >
                  <Redo2 className="size-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Redo · Ctrl+Y</TooltipContent>
            </Tooltip>
          </div>
          <Popover>
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-9 gap-1.5 border-white/12 bg-white/[0.045] px-3 text-sm text-zinc-300 hover:bg-white/[0.08] hover:text-white"
              >
                More
                <ChevronDown className="size-3" aria-hidden />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-56 p-1.5">
              {toolPanes
                .filter((t) => t.key !== 'variables' && t.key !== 'workflowPlan')
                .map((tool) => {
                  const Icon = tool.icon
                  return (
                    <button
                      key={tool.key}
                      type="button"
                      aria-controls={tool.controls}
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-zinc-200 hover:bg-white/[0.06]"
                      onClick={() => setOpenTool(tool.key)}
                    >
                      <Icon className={cn('size-3.5 shrink-0', tool.iconClass)} />
                      {tool.label}
                      {tool.key === 'suggestions' && (
                        <span className="ml-auto text-zinc-500">{suggestionCount}</span>
                      )}
                      {stepsBelowThreshold.length > 0 && tool.key === 'diagnostics' && (
                        <span className="ml-auto size-1.5 rounded-full bg-status-warn" aria-hidden />
                      )}
                    </button>
                  )
                })}
              <div className="my-1 h-px bg-white/10" />
              <div className="flex items-center justify-between px-2.5 py-1.5 text-xs text-zinc-500">
                AI usage credits
                <HumanEditPoolBadge className="h-auto border-0 bg-transparent p-0 text-zinc-300" />
              </div>
            </PopoverContent>
          </Popover>
          <div className="hidden h-7 w-px bg-white/15 sm:block" aria-hidden />
          {fromConversation && selected !== null && selected > 0 ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button type="button" variant="outline" size="sm" className="h-9 gap-1.5 px-3 text-sm" onClick={() => void rerecordFromHere('replace')}>
                  <Video className="size-4" aria-hidden />
                  Record from here
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                Replay the workflow up to just before this step, then do the rest yourself — what you record replaces this step and the ones after it
              </TooltipContent>
            </Tooltip>
          ) : null}
          {fromConversation && selected !== null && selected > 0 ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button type="button" variant="outline" size="sm" className="h-9 gap-1.5 px-3 text-sm" onClick={() => void rerecordFromHere('branch')}>
                  <GitBranch className="size-4" aria-hidden />
                  Record another way
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                For when the app sometimes goes a different way from this step (a different page, a different popup). Replay to just before it, record the other way, and the workflow keeps both and takes whichever the page offers
              </TooltipContent>
            </Tooltip>
          ) : null}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="brand"
                size="sm"
                className="ring-brand-ring/50 h-9 gap-1.5 px-4 text-sm font-semibold ring-1"
                onClick={() => void (fromConversation ? testChanges() : approveWorkflow())}
              >
                <BadgeCheck className="size-4" aria-hidden />
                {fromConversation ? 'Test changes' : 'Approve'}
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {fromConversation
                ? 'Save this step and test the workflow — you approve it from the conversation once a test passes'
                : 'Sign off this workflow — builds the package and returns to its group'}
            </TooltipContent>
          </Tooltip>
          </div>
        </div>
      }
    />
    {skillId && (
      <ReviewQuestionsDialog skillId={skillId} wf={wf} onWorkflowUpdated={onWorkflowUpdated} />
    )}
    <div className="flex min-h-0 flex-1 border-t border-white/8">
    <div
      ref={splitPaneRef}
        className="relative grid flex-1 min-h-0 w-full min-w-0 grid-cols-1 overflow-hidden md:min-h-0 md:[grid-template-columns:var(--workflow-pane-width)_minmax(0,1fr)] md:items-stretch"
        style={splitPaneStyle}
      >
        <WorkflowViewer
          skillId={skillId}
          steps={wf.steps}
          stepsBelowThreshold={stepsBelowThreshold}
          onReorder={onReorder}
          onDelete={onDelete}
          onAddAction={(actionKind) => void onAddAction(actionKind)}
          onDroppedRecordingScreenshot={(stepIndex, eventIndex) =>
            void onDroppedRecordingScreenshot(stepIndex, eventIndex)
          }
          onClearStepVisual={(stepIndex) => void onClearStepVisual(stepIndex)}
        />
        <div
          className="group absolute inset-y-0 z-20 hidden w-3 -translate-x-1/2 cursor-col-resize md:block"
          style={{ left: workflowPaneWidth }}
          onMouseDown={(event) => {
            event.preventDefault()
            setIsResizingPane(true)
          }}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize workflow sidebar"
        >
          <div className="mx-auto h-full w-px bg-white/10 transition-colors group-hover:bg-white/35 group-active:bg-white/45" />
        </div>
        <InlineRetargetFlow
          key={currentStep?.id ?? 'empty-step-editor'}
          ref={stepEditorRef}
          step={currentStep}
          skillId={skillId}
          onWorkflowUpdated={onWorkflowUpdated}
          onHistoryUpdate={onHistoryUpdate}
          onOpenScreenshots={() => setOpenTool('screenshots')}
          screenshotCount={screenshotsTool.count}
        />
      </div>
    {convWorkflow && (
      <EditConversationPanel
        workflowId={convWorkflow.id}
        title={convWorkflow.name}
        stepName={currentStep ? compactStepLabel(currentStep.human_readable_description) : undefined}
        onWorkflowChanged={onConversationChanged}
      />
    )}
    </div>
    </div>
    <Dialog
      open={activeTool !== null}
      onOpenChange={(open) => {
        if (!open) {
          setOpenTool(null)
          setShowAllScreenshots(false)
        }
      }}
    >
      <DialogContent
        className={cn(
          // sm:max-w-none is required: the base DialogContent sets sm:max-w-sm (384px),
          // and an unprefixed max-w-none does NOT override a sm:-prefixed class (different
          // tailwind-merge scope), so without this every tool dialog was silently clamped
          // to 384px and its content overflowed/clipped instead of using the width below.
          'anim-pop flex max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none',
          activeTool?.key === 'screenshots'
            ? 'h-[min(85vh,880px)] w-[min(1500px,96vw)]'
            : activeTool?.key === 'suggestions'
              ? 'h-[min(85vh,880px)] w-[min(1100px,94vw)]'
              : // Variables / Workflow plan / Diagnostics: the step-editor-redesign 760px
                // modal, sized to its content up to a cap instead of a fixed height.
                'max-h-[min(780px,88vh)] w-[min(760px,94vw)]',
        )}
        id={activeTool?.controls}
      >
        {activeTool && (
          <>
            <DialogHeader className="flex-col gap-3 border-b border-white/8 pt-6 pb-5 pl-7 pr-12">
              <div className="flex items-start gap-3.5">
                <span className="bg-brand/14 text-brand flex size-[34px] shrink-0 items-center justify-center rounded-[9px]">
                  <activeTool.icon className="size-[17px]" />
                </span>
                <div className="min-w-0 flex-1 pt-0.5">
                  <DialogTitle className="flex items-center gap-2 text-base leading-none font-semibold">
                    <span className="truncate">{activeTool.label}</span>
                    {activeTool.count !== undefined && (
                      <Badge variant="outline" className="shrink-0 border-brand/40 text-[0.65rem] text-brand">
                        {activeTool.count}
                      </Badge>
                    )}
                  </DialogTitle>
                  <DialogDescription className="mt-1.5 max-w-2xl text-sm leading-normal">{activeTool.help.summary}</DialogDescription>
                </div>
                <InfoHint {...activeTool.help} side="bottom" align="end" triggerLabel={`About ${activeTool.label}`} />
              </div>
              {activeTool.key === 'screenshots' && (
                <div className="flex justify-end pl-12">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="gap-1.5 whitespace-nowrap"
                    onClick={() => setShowAllScreenshots((v) => !v)}
                  >
                    <ImageIcon className="size-3.5 shrink-0" aria-hidden />
                    {showAllScreenshots ? 'Show 5 frames for this step' : 'Show all recording screenshots'}
                  </Button>
                </div>
              )}
            </DialogHeader>
            {activeTool.key === 'variables' ? (
              // Own bounded flex layout (not the shared ScrollArea below): its footer sits
              // below an internal, properly height-constrained scroll region instead of
              // scrolling out of view at the end of an unbounded content column.
              <ParameterizationInlinePanel workflow={wf} onSaved={onWorkflowUpdated} onClose={() => setOpenTool(null)} />
            ) : (
              <>
              {!isCompactTool ? (
              <ScrollArea className="min-h-0 flex-1">
                <div className="p-5">
                  {activeTool.key === 'suggestions' && <SuggestionsInlinePanel suggestions={wf.suggestions} />}
                  {activeTool.key === 'screenshots' && (
                    <RecordingScreenshotsPanel
                      frames={effectiveStep?.screenshot?.frames ?? []}
                      activeFrameLabel={effectiveStep?.screenshot?.default_frame_label ?? null}
                      canClearVisual={!!effectiveStep && !effectiveStep.flags.is_scroll}
                      onApplyFrame={effectiveStep && !effectiveStep.flags.is_scroll ? onApplyStepFrame : undefined}
                      onClearVisual={
                        selected !== null ? () => void onClearStepVisual(selected, nestedPath) : undefined
                      }
                      showAllFrames={showAllScreenshots}
                      allItems={allScreenshotsQ.data?.items}
                      isLoadingAllFrames={allScreenshotsQ.isLoading}
                      onSelectAllFrame={
                        effectiveStep && !effectiveStep.flags.is_scroll ? onSelectAllScreenshot : undefined
                      }
                    />
                  )}
                </div>
              </ScrollArea>
              ) : (
                <>
                  {/* Auto-height dialog (max-h only): a plain overflow div scrolls here, whereas
                      ScrollArea's size-full viewport never gets a definite height and would clip. */}
                  <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
                    {activeTool.key === 'workflowPlan' ? (
                      <WorkflowPlanPanel workflow={wf} />
                    ) : (
                      <DiagnosticsPanel workflow={wf} currentStep={currentStep} skillId={skillId} version={version} />
                    )}
                  </div>
                  <div className="flex shrink-0 justify-end border-t border-white/8 px-7 py-4">
                    <Button type="button" variant="outline" onClick={() => setOpenTool(null)}>
                      Close
                    </Button>
                  </div>
                </>
              )}
              </>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
    <UnsavedChangeGuard save={() => stepEditorRef.current?.submitIfDirty() ?? Promise.resolve(true)} />
    </TooltipProvider>
  )
}
