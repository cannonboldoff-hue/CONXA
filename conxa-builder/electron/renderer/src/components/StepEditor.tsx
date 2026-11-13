import { type ComponentProps, forwardRef, useImperativeHandle, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { StepEditorDTO, WorkflowResponse } from '@/types/workflow'
import { StepConfigForm, type StepConfigFormHandle } from './StepConfigForm'
import { BranchBodyEditor } from '@/components/branch/BranchBodyEditor'
import { ForEachBodyViewer } from '@/components/loop/ForEachBodyViewer'
import { RecoveryAnchorsCard } from '@/components/RecoveryAnchorsCard'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { useEditorStore } from '@/store/editorStore'
import { cn } from '@/lib/utils'

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
  marker: 'Marker',
  if_present: 'If present',
  try_dismiss: 'Dismiss if shown',
  wait_for_one_of: 'Wait for one of',
}

function actionTypeLabel(actionType: string): string {
  const t = actionType.trim().toLowerCase().replace(/-/g, '_')
  return ACTION_TYPE_LABELS[t] ?? t.replace(/_/g, ' ')
}

/** Eyebrow + plain-language question heading atop the step editor. */
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

/** Collapsed-by-default wrapper for RecoveryAnchorsCard — kept out of the way until someone
 *  actually wants to see recovery search hints. */
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
}

export type StepEditorHandle = {
  /** Saves the open step's config fields if dirty. Returns whether save succeeded or was not needed. */
  submitIfDirty: () => Promise<boolean>
}

// Same gradient-fill depth treatment as WorkflowViewer's aside / PanelChrome (components/ui/
// panel-chrome.tsx) — this is the flush middle grid column (draggable resizers on both sides),
// so no rounded corners/outer shadow here; see WorkflowViewer.tsx for the same reasoning.
const PANEL_CLASS =
  'border-border/60 relative z-0 flex min-h-0 min-w-0 flex-col overflow-hidden border-t bg-[linear-gradient(180deg,rgba(17,24,39,0.9),rgba(7,10,16,0.95))] ring-1 ring-inset ring-white/[0.03] md:border-t-0 md:border-l'

/**
 * The center "step editor" panel: one scrollable page per step. A for_each loop shows its own
 * config plus its body; every other step shows `StepConfigForm` (its fields, selectors and
 * validation) plus, for an if-present step, its body editor, and a collapsed recovery-hints card.
 */
export const StepEditor = forwardRef<StepEditorHandle, Props>(function StepEditor(
  { step, skillId, onWorkflowUpdated, onHistoryUpdate },
  ref,
) {
  const formRef = useRef<StepConfigFormHandle>(null)

  // A focused nested for_each step (clicked in ForEachSubList.tsx — the same focusedBranchIndex
  // BranchSubList uses) gets the same editor a top-level step gets, scoped to it via
  // path-addressing ("for_each.steps[N]") — see handlers/workflow_editor.py's cmd_patch_step
  // `path` param and conxa_compile/editor/step_path.py. `step` stays the outer (possibly
  // for_each-wrapper) DTO — its `.step_index` is always the real top-level position the backend
  // needs; `effectiveStep` is whichever step's OWN data should actually render.
  const focusedBranchIndex = useEditorStore((s) => s.focusedBranchIndex)
  const nestedForEachStep =
    step?.for_each_summary && focusedBranchIndex !== null ? (step.for_each_steps[focusedBranchIndex] ?? null) : null
  const nestedPath = nestedForEachStep ? `for_each.steps[${focusedBranchIndex}]` : undefined
  const effectiveStep = nestedForEachStep ?? step

  useImperativeHandle(
    ref,
    () => ({
      submitIfDirty: () => formRef.current?.submitIfDirty() ?? Promise.resolve(true),
    }),
    [],
  )

  if (!step || !effectiveStep) {
    return (
      <div className={cn(PANEL_CLASS, 'text-muted-foreground items-center justify-center border-x p-4 text-sm')}>
        Select a step to edit
      </div>
    )
  }

  // for_each steps have no element of their own — this is the loop's own config + nested body
  // list. A focused nested step (nestedForEachStep set) falls through to the normal layout below:
  // clicking a row in ForEachSubList.tsx means "show me its details", not the loop's own summary.
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
            {/* if_present's own nested body. try_dismiss's `candidates` and wait_for_one_of's `options`
                are readable (BranchSummaryBadge in WorkflowStepItem.tsx) but have no authoring UI yet. */}
            {effectiveStep.branch_summary?.kind === 'if_present' ? (
              <BranchBodyEditor
                step={effectiveStep}
                skillId={skillId}
                onWorkflowUpdated={onWorkflowUpdated}
                onHistoryUpdate={onHistoryUpdate}
              />
            ) : null}
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
})
