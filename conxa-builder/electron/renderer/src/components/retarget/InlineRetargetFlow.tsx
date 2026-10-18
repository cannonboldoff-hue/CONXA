import { type ComponentProps, forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ArrowLeft, ArrowRight, ChevronRight } from 'lucide-react'
import { CmdError } from '@/lib/ipc'
import { errorMessage, retargetApply, retargetPreview, type Bbox } from '@/api/workflowApi'
import type { StepEditorDTO, WorkflowResponse } from '@/types/workflow'
import { StepConfigForm, type StepConfigFormHandle } from '../StepConfigForm'
import { RetargetPhasePick } from './RetargetPhasePick'
import { RetargetPhaseSelectors } from './RetargetPhaseSelectors'
import { RetargetPhaseValidation } from './RetargetPhaseValidation'
import { BranchBodyEditor } from '@/components/branch/BranchBodyEditor'
import { ForEachBodyViewer } from '@/components/loop/ForEachBodyViewer'
import { RecoveryAnchorsCard } from '@/components/RecoveryAnchorsCard'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { makeCandidateId, useRetargetStore } from '@/store/retargetStore'
import { useEditorStore } from '@/store/editorStore'
import { cn } from '@/lib/utils'

const PHASE_LABELS = ['Element', 'How it’s found', 'Validation'] as const
type Phase = 1 | 2 | 3

/** "Step N · Go to a page" style eyebrow label — same words used in Add step's menu categories
 *  where they overlap, otherwise a plain human name for the action_type. */
const ACTION_TYPE_LABELS: Record<string, string> = {
  navigate: 'Go to a page',
  click: 'Click',
  fill: 'Type text',
  type: 'Type text',
  select: 'Select an option',
  scroll: 'Scroll',
  upload: 'Upload a file',
  upload_intent: 'Upload a file',
  check: 'Check',
  ai_review: 'AI review',
  marker: 'Marker',
  if_present: 'If present',
  try_dismiss: 'Dismiss if shown',
  wait_for_one_of: 'Wait for one of',
}

function actionTypeLabel(actionType: string): string {
  const t = actionType.trim().toLowerCase().replace(/-/g, '_')
  return ACTION_TYPE_LABELS[t] ?? t.replace(/_/g, ' ')
}

/** Eyebrow + plain-language question heading atop every step editor layout — the "one plain
 *  question per step" pattern the redesign is built around, in place of the old dense form
 *  header. `question` defaults to the generic prompt; the wizard passes a phase-specific one. */
function StepEditorHeading({ step, question }: { step: StepEditorDTO; question: string }) {
  return (
    <div>
      <div className="text-sm text-zinc-500">
        Step {step.step_index + 1} · {actionTypeLabel(step.action_type)}
      </div>
      <h2 className="mt-1.5 text-2xl font-semibold tracking-tight text-zinc-100">{question}</h2>
    </div>
  )
}

/** "1 Element — 2 Selectors — 3 Check" numbered-circle stepper for the 3-phase re-target
 *  wizard — brand outline + fill on the current phase, done phases stay outlined without fill. */
function WizardStepper({ phase }: { phase: Phase }) {
  return (
    <div className="flex items-center gap-4">
      {PHASE_LABELS.map((label, i) => {
        const idx = ((i + 1) as Phase)
        const active = idx === phase
        const done = idx < phase
        return (
          <div key={label} className="flex items-center gap-4">
            {i > 0 && <span className="h-px w-8 bg-white/15" aria-hidden />}
            <div className={cn('flex items-center gap-2 text-sm font-medium', active ? 'text-brand' : 'text-zinc-500')}>
              <span
                className={cn(
                  'flex size-[22px] shrink-0 items-center justify-center rounded-full border text-xs',
                  active ? 'border-brand bg-brand text-white' : done ? 'border-brand/60 text-brand' : 'border-white/25',
                )}
              >
                {idx}
              </span>
              {label}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Collapsed-by-default wrapper for RecoveryAnchorsCard on the non-wizard step layouts — kept
 *  out of the way until someone actually wants to see recovery search hints. */
function RecoveryHintsDisclosure(props: ComponentProps<typeof RecoveryAnchorsCard>) {
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <button type="button" className="flex items-center gap-1 py-1 text-sm text-zinc-500 hover:text-zinc-300">
          Recovery hints
          <ChevronRight className={cn('size-3.5 transition-transform', open && 'rotate-90')} aria-hidden />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">
        <RecoveryAnchorsCard {...props} />
      </CollapsibleContent>
    </Collapsible>
  )
}

type Props = {
  step: StepEditorDTO | null
  skillId: string
  onWorkflowUpdated: (wf: WorkflowResponse) => void
  onHistoryUpdate?: (canUndo: boolean, canRedo: boolean) => void
  /** Opens the Recording Screenshots dialog (lives in HumanEditPage — reused from there so its
   *  fetch/apply/clear state doesn't need duplicating here). Surfaced as a button in the Pick
   *  Element phase rather than the page's top bar, since it's a per-step "borrow a frame" tool. */
  onOpenScreenshots?: () => void
  screenshotCount?: number | string
}

export type InlineRetargetFlowHandle = {
  /** Saves the open step's config fields if dirty. Returns whether save succeeded or was not needed. */
  submitIfDirty: () => Promise<boolean>
}

// Same gradient-fill depth treatment as WorkflowViewer's aside / PanelChrome (components/ui/
// panel-chrome.tsx) — this is the flush middle grid column (draggable resizers on both sides),
// so no rounded corners/outer shadow here; see WorkflowViewer.tsx for the same reasoning.
const PANEL_CLASS =
  'border-border/60 relative z-0 flex min-h-0 min-w-0 flex-col overflow-hidden border-t bg-[linear-gradient(180deg,rgba(17,24,39,0.9),rgba(7,10,16,0.95))] ring-1 ring-inset ring-white/[0.03] md:border-t-0 md:border-l'

/**
 * Replaces the old center "step editor" panel. For steps with an element to target, this
 * embeds the 3-phase re-target wizard (previously a separate full-screen route) directly next
 * to the steps list: Pick element on the screenshot -> review generated selectors (and edit the
 * step's other config fields alongside them) -> confirm & apply the validation diff. Scroll
 * steps have no single element to re-target, so they just get the config form.
 */
export const InlineRetargetFlow = forwardRef<InlineRetargetFlowHandle, Props>(function InlineRetargetFlow(
  { step, skillId, onWorkflowUpdated, onHistoryUpdate, onOpenScreenshots, screenshotCount },
  ref,
) {
  const formRef = useRef<StepConfigFormHandle>(null)
  const [phase, setPhase] = useState<Phase>(1)
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [sessionMissing, setSessionMissing] = useState(false)

  const bbox = useRetargetStore((s) => s.bbox)
  const setBbox = useRetargetStore((s) => s.setBbox)
  const preview = useRetargetStore((s) => s.preview)
  const setPreview = useRetargetStore((s) => s.setPreview)
  const candidates = useRetargetStore((s) => s.candidates)
  const setCandidates = useRetargetStore((s) => s.setCandidates)
  const keepValidation = useRetargetStore((s) => s.keepValidation)
  const setKeepValidation = useRetargetStore((s) => s.setKeepValidation)
  const editedAssertions = useRetargetStore((s) => s.editedAssertions)
  const setEditedAssertions = useRetargetStore((s) => s.setEditedAssertions)
  const markDirty = useRetargetStore((s) => s.markDirty)
  const ensureFor = useRetargetStore((s) => s.ensureFor)
  const reset = useRetargetStore((s) => s.reset)

  // A focused nested for_each step (clicked in ForEachSubList.tsx — the same focusedBranchIndex
  // BranchSubList uses) gets the SAME 3-phase wizard a top-level step gets, scoped to it via
  // path-addressing ("for_each.steps[N]") — see handlers/workflow_editor.py's cmd_patch_step /
  // cmd_retarget_preview / cmd_retarget_apply `path` param and conxa_compile/editor/step_path.py.
  // `step` stays the outer (possibly for_each-wrapper) DTO throughout — its `.step_index` is
  // always the real top-level position the backend needs; `effectiveStep` is whichever step's
  // OWN data (selectors, screenshot, url, ...) should actually render.
  const focusedBranchIndex = useEditorStore((s) => s.focusedBranchIndex)
  const nestedForEachStep =
    step?.for_each_summary && focusedBranchIndex !== null ? (step.for_each_steps[focusedBranchIndex] ?? null) : null
  const nestedPath = nestedForEachStep ? `for_each.steps[${focusedBranchIndex}]` : undefined
  const effectiveStep = nestedForEachStep ?? step
  const scopeKey = step ? (nestedForEachStep ? `${step.step_index}:${nestedPath}` : String(step.step_index)) : null

  // Scope the wizard store to the open step (or nested step). No-ops if it's already scoped
  // there (e.g. a workflow refresh), so it never wipes an in-progress wizard out from under the
  // user — only switching to a genuinely different step/nested-step resets bbox/candidates/
  // validation edits.
  useEffect(() => {
    if (scopeKey) ensureFor(skillId, scopeKey)
  }, [skillId, scopeKey, ensureFor])

  useImperativeHandle(
    ref,
    () => ({
      submitIfDirty: () => formRef.current?.submitIfDirty() ?? Promise.resolve(true),
    }),
    [],
  )

  const handleDrawn = useCallback(
    async (drawn: Bbox, regenerate: boolean) => {
      if (!step) return
      setBbox(drawn)
      setSessionMissing(false)
      setLoading(true)
      try {
        const result = await retargetPreview(skillId, step.step_index, drawn, regenerate, nestedPath)
        setPreview(result)
        const seeded = result.candidates.map((c) => ({ ...c, id: makeCandidateId() }))
        setCandidates(seeded)
        setKeepValidation(!result.validation_changed)
        setEditedAssertions(null)
        setPhase(2)
      } catch (err) {
        if (err instanceof CmdError && err.code === 'session_artifacts_missing') {
          setSessionMissing(true)
        } else {
          toast.error(errorMessage(err, 'Could not find this element in the recorded page'))
        }
      } finally {
        setLoading(false)
      }
    },
    [skillId, step, nestedPath, setBbox, setPreview, setCandidates, setKeepValidation, setEditedAssertions],
  )

  const handleApplyPositionOnly = useCallback(async () => {
    if (!step || !effectiveStep || !bbox) return
    setApplying(true)
    try {
      const res = await retargetApply(
        skillId,
        step.step_index,
        {
          bbox,
          primary_selector: String(effectiveStep.target.primary_selector ?? ''),
          fallback_selectors: Array.isArray(effectiveStep.target.fallback_selectors)
            ? (effectiveStep.target.fallback_selectors as string[])
            : [],
          keep_validation: true,
        },
        nestedPath,
      )
      onWorkflowUpdated(res.workflow)
      if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
      useEditorStore.getState().clearStepDirty(step.step_index)
      toast.success('Position updated')
      reset()
      setPhase(1)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not update the position'))
    } finally {
      setApplying(false)
    }
  }, [skillId, step, effectiveStep, nestedPath, bbox, onWorkflowUpdated, onHistoryUpdate, reset])

  const handleCancelPick = useCallback(() => {
    reset()
  }, [reset])

  const handleContinueToConfirm = useCallback(async () => {
    if (!candidates[0]?.selector.trim()) {
      toast.error('Add or drag a selector to the top (primary) before continuing.')
      return
    }
    const savedOk = await (formRef.current?.submitIfDirty() ?? Promise.resolve(true))
    if (!savedOk) {
      toast.error('Could not save the step details — fix errors before continuing.')
      return
    }
    setPhase(3)
  }, [candidates])

  const handleApply = useCallback(async () => {
    if (!step || !bbox || !preview) return
    const primary = candidates[0]?.selector.trim() || ''
    if (!primary) {
      toast.error('Add or drag a selector to the top (primary) before applying.')
      return
    }
    const fallbacks = candidates
      .slice(1)
      .map((c) => c.selector.trim())
      .filter(Boolean)
    setApplying(true)
    try {
      const res = await retargetApply(
        skillId,
        step.step_index,
        {
          bbox,
          primary_selector: primary,
          fallback_selectors: fallbacks,
          keep_validation: keepValidation,
          proposed_wait_for: preview.proposed_wait_for,
          proposed_assertions: preview.proposed_assertions,
          // Only sent when the human actually touched the Validation phase's assertion editor —
          // otherwise the backend falls back to keep_validation/proposed_assertions as before.
          edited_assertions: editedAssertions ?? undefined,
          // Generated back at Continue's preview — passing it through here means Apply skips the
          // vision LLM call entirely instead of regenerating anchors it already has.
          visual_anchors: preview.visual_anchors ?? undefined,
        },
        nestedPath,
      )
      onWorkflowUpdated(res.workflow)
      if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
      useEditorStore.getState().clearStepDirty(step.step_index)
      toast.success('Re-target applied')
      reset()
      setPhase(1)
    } catch (err) {
      toast.error(errorMessage(err, 'Could not apply the re-target'))
    } finally {
      setApplying(false)
    }
  }, [skillId, step, nestedPath, bbox, preview, candidates, keepValidation, editedAssertions, onWorkflowUpdated, onHistoryUpdate, reset])

  if (!step || !effectiveStep) {
    return (
      <div className={cn(PANEL_CLASS, 'text-muted-foreground items-center justify-center border-x p-4 text-sm')}>
        Select a step to edit
      </div>
    )
  }

  // for_each steps (EXEC-38) have no element of their own to re-target — this is the loop's own
  // config + nested body list. Order matters: a focused nested step (nestedForEachStep set) takes
  // priority and falls through to the SAME treatment below any other step gets (navigate/scroll/
  // branch skip-blocks or the full wizard, based on THAT nested step's own shape) — clicking a
  // row in ForEachSubList.tsx means "show me its details", not the loop's own summary.
  if (step.for_each_summary && !nestedForEachStep) {
    return (
      <div className={PANEL_CLASS}>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-7 px-8 py-9">
            <StepEditorHeading step={step} question={step.human_readable_description || 'Repeat for each item'} />
            <div className="max-w-[680px] space-y-7">
              <StepConfigForm
                ref={formRef}
                step={step}
                skillId={skillId}
                onWorkflowUpdated={onWorkflowUpdated}
                onHistoryUpdate={onHistoryUpdate}
              />
              <ForEachBodyViewer step={step} />
            </div>
          </div>
        </ScrollArea>
      </div>
    )
  }

  // Navigate steps have no element to re-target — their editable surface is the URL/intent
  // form plus validations, which StepConfigForm already owns (http/https gate on save). Like
  // scroll and branch steps, they skip the 3-phase wizard entirely. Checked against
  // effectiveStep so a nested for_each step of this shape gets the same treatment.
  //
  // Upload/upload_intent steps skip it too, for a different reason: unlike navigate, they DO have
  // a real compiled selector (StepConfigForm's own selector fields below still edit it — see
  // SELECTOR_ACTIONS in action_registry.py) — but that selector targets a hidden <input
  // type="file">, which has no meaningful on-page appearance to draw a box around and is resolved
  // deterministically at compile time from IdentityBundle/DOM signals, never from a human-picked
  // screenshot region. Recorded upload_intent events also never get their own screenshot
  // extracted (frame_extractor.py — the preceding click that opens the file picker already has
  // one), so Phase 1's "draw a box" requirement was a dead end here: no image to draw on, and
  // nothing meaningful to pick even when one happened to exist from an older compile.
  const normalizedAction = effectiveStep.action_type.trim().toLowerCase().replace(/-/g, '_')
  if (
    normalizedAction === 'navigate' ||
    normalizedAction === 'upload' ||
    normalizedAction === 'upload_intent' ||
    effectiveStep.flags.is_scroll
  ) {
    return (
      <div className={PANEL_CLASS}>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-7 px-8 py-9">
            <StepEditorHeading step={effectiveStep} question="What should this step do?" />
            <div className="max-w-[680px] space-y-7">
              <StepConfigForm
                ref={formRef}
                step={effectiveStep}
                skillId={skillId}
                onWorkflowUpdated={onWorkflowUpdated}
                onHistoryUpdate={onHistoryUpdate}
                path={nestedPath}
                parentStepIndex={step.step_index}
              />
              <RecoveryHintsDisclosure
                stepIndex={step.step_index}
                path={nestedPath}
                skillId={skillId}
                anchors={effectiveStep.anchors_recovery}
                onWorkflowUpdated={onWorkflowUpdated}
                onHistoryUpdate={onHistoryUpdate}
              />
            </div>
          </div>
        </ScrollArea>
      </div>
    )
  }

  // Branch steps (if_present/try_dismiss/wait_for_one_of — EXEC-1) skip the 3-phase re-target
  // wizard: its bbox-driven "one primary + fallbacks" model doesn't map onto try_dismiss's
  // ordered candidate list or wait_for_one_of's set of alternative options, and if_present's own
  // probe selector is still editable through the normal selector fields in StepConfigForm below.
  // Only if_present gets a dedicated nested-body editor this pass — try_dismiss's `candidates`
  // and wait_for_one_of's `options` are readable (see BranchSummaryBadge in WorkflowStepItem.tsx)
  // but have no authoring UI yet; see TODO.md follow-up. A for_each loop body step nested one
  // level deeper than THIS can't itself be a branch step (patch_gate.py has no such nesting), so
  // effectiveStep.branch_summary here is only ever true for a genuine top-level branch step —
  // nestedPath stays undefined in that case.
  if (effectiveStep.branch_summary) {
    return (
      <div className={PANEL_CLASS}>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-7 px-8 py-9">
            <StepEditorHeading step={effectiveStep} question="What should this step do?" />
            <div className="max-w-[680px] space-y-7">
              <StepConfigForm
                ref={formRef}
                step={effectiveStep}
                skillId={skillId}
                onWorkflowUpdated={onWorkflowUpdated}
                onHistoryUpdate={onHistoryUpdate}
              />
              {effectiveStep.branch_summary.kind === 'if_present' ? (
                <BranchBodyEditor
                  step={effectiveStep}
                  skillId={skillId}
                  onWorkflowUpdated={onWorkflowUpdated}
                  onHistoryUpdate={onHistoryUpdate}
                />
              ) : null}
              <RecoveryHintsDisclosure
                stepIndex={step.step_index}
                skillId={skillId}
                anchors={effectiveStep.anchors_recovery}
                onWorkflowUpdated={onWorkflowUpdated}
                onHistoryUpdate={onHistoryUpdate}
              />
            </div>
          </div>
        </ScrollArea>
      </div>
    )
  }

  const wizardQuestion =
    phase === 1
      ? 'Is this the right element?'
      : phase === 2
        ? 'Will this keep finding the right element?'
        : `How do we know this ${actionTypeLabel(effectiveStep.action_type).toLowerCase()} worked?`

  return (
    <div className={PANEL_CLASS}>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-7 px-8 py-9">
          <StepEditorHeading step={effectiveStep} question={wizardQuestion} />
          <WizardStepper phase={phase} />
          {phase === 1 ? (
            <RetargetPhasePick
              step={effectiveStep}
              loading={loading || applying}
              sessionMissing={sessionMissing}
              onDrawn={handleDrawn}
              onApplyPositionOnly={handleApplyPositionOnly}
              onCancel={handleCancelPick}
              onOpenScreenshots={onOpenScreenshots}
              screenshotCount={screenshotCount}
            />
          ) : null}
          <div className={cn('max-w-[720px] space-y-7', phase === 2 ? '' : 'hidden')}>
            {/* Kept mounted (just hidden) across phases so in-progress edits and the ref survive
                phase changes instead of being lost on unmount. Visible during phase 2 only, per
                the "review selectors doubles as the step editor" design. hideSelectorTools also
                suppresses the form's own Validation panel — that's the job of phase 3
                (RetargetPhaseValidation) here, and showing both would let a user save assertions
                via two different, inconsistent paths (instant patchStep vs. staged retargetApply).
                hideSubmitButton: the wizard's own Continue button already calls submitIfDirty(),
                so a second manual "Save step" button here would be redundant. */}
            <StepConfigForm
              ref={formRef}
              step={effectiveStep}
              skillId={skillId}
              onWorkflowUpdated={onWorkflowUpdated}
              onHistoryUpdate={onHistoryUpdate}
              hideSelectorTools
              hideSubmitButton
              path={nestedPath}
              parentStepIndex={step.step_index}
            />
            {phase === 2 && preview ? (
              <RetargetPhaseSelectors
                pickQuality={preview.pick_quality}
                candidates={candidates}
                onCandidatesChange={(next) => {
                  setCandidates(next)
                  markDirty()
                }}
                onBack={() => setPhase(1)}
                compileConfidence={preview.compile_confidence}
              />
            ) : null}
            {phase === 2 ? (
              <>
                <RecoveryAnchorsCard
                  stepIndex={step.step_index}
                  path={nestedPath}
                  skillId={skillId}
                  anchors={effectiveStep.anchors_recovery}
                  onWorkflowUpdated={onWorkflowUpdated}
                  onHistoryUpdate={onHistoryUpdate}
                />
                <div className="flex items-center justify-between gap-3 border-t border-white/8 pt-7">
                  <Button variant="ghost" onClick={() => setPhase(1)} className="gap-1.5 text-zinc-400 hover:text-zinc-200">
                    <ArrowLeft className="size-3.5" aria-hidden />
                    Back to element
                  </Button>
                  <div className="flex items-center gap-2.5">
                    {!candidates[0]?.selector.trim() ? (
                      <span className="text-muted-foreground text-xs">Add a primary selector to continue</span>
                    ) : null}
                    <Button
                      variant="brand"
                      onClick={() => void handleContinueToConfirm()}
                      disabled={!candidates[0]?.selector.trim()}
                      className="gap-1.5"
                    >
                      Continue to check
                      <ArrowRight className="size-3.5" aria-hidden />
                    </Button>
                  </div>
                </div>
              </>
            ) : null}
          </div>
          {phase === 3 && preview ? (
            <div className="max-w-[720px]">
              <RetargetPhaseValidation
                step={effectiveStep}
                preview={preview}
                keepValidation={keepValidation}
                onKeepValidationChange={(v) => {
                  setKeepValidation(v)
                  markDirty()
                }}
                editedAssertions={editedAssertions}
                onEditedAssertionsChange={(a) => {
                  setEditedAssertions(a)
                  markDirty()
                }}
                onBack={() => setPhase(2)}
                onApply={() => void handleApply()}
                applying={applying}
              />
            </div>
          ) : null}
        </div>
      </ScrollArea>
    </div>
  )
})
