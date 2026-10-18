import type { DragEvent, KeyboardEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { stepTypeColor } from '@/lib/status'
import type { StepEditorDTO } from '@/types/workflow'
import { RECORDING_SCREENSHOT_DRAG_MIME } from '@/api/workflowApi'
import { BoxSelect, GitBranch, GripVertical, Repeat, Trash2 } from 'lucide-react'
import { compactStepLabel, handleRecordingScreenshotDrop, visualBboxState, type BboxState } from '@/lib/workflowViewerHelpers'

type WorkflowStepItemProps = {
  step: StepEditorDTO
  isSelected: boolean
  isDirty: boolean
  isDragging: boolean
  /** Set when this step's index is in compile_health.steps_below_threshold — shown as a quiet
   *  warn dot instead of the old banner's separate "jump to step" chip. */
  belowThreshold?: boolean
  recordingShotDragActive?: boolean
  draggingIndex: number | null
  onSelect: (index: number) => void
  onDeleteRequest: (index: number) => void
  onDragStart: (index: number) => void
  onDragEnd: () => void
  onMove: (from: number, to: number) => void
  onDroppedRecordingScreenshot?: (stepIndex: number, eventIndex: number) => void
  onClearStepVisual?: (stepIndex: number) => void
}

export function WorkflowStepItem({
  step,
  isSelected,
  isDirty,
  isDragging,
  belowThreshold,
  recordingShotDragActive,
  draggingIndex,
  onSelect,
  onDeleteRequest,
  onDragStart,
  onDragEnd,
  onMove,
  onDroppedRecordingScreenshot,
  onClearStepVisual,
}: WorkflowStepItemProps) {
  const bboxState = visualBboxState(step)

  const selectStep = () => onSelect(step.step_index)
  const onRowKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    selectStep()
  }

  const onDragOver = (event: DragEvent<HTMLLIElement>) => {
    event.preventDefault()
    event.dataTransfer.dropEffect =
      recordingShotDragActive || event.dataTransfer.types.includes(RECORDING_SCREENSHOT_DRAG_MIME) ? 'copy' : 'move'
  }

  const onDrop = (event: DragEvent<HTMLLIElement>) => {
    event.preventDefault()
    const raw = event.dataTransfer.getData(RECORDING_SCREENSHOT_DRAG_MIME).trim()
    if (raw && (onClearStepVisual || onDroppedRecordingScreenshot)) {
      handleRecordingScreenshotDrop(raw, step.step_index, onDroppedRecordingScreenshot, onClearStepVisual)
      return
    }
    if (draggingIndex === null) return
    onMove(draggingIndex, step.step_index)
    onDragEnd()
  }

  return (
    <li
      className="w-full space-y-1.5"
      draggable
      onDragStart={() => onDragStart(step.step_index)}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
    >
      <div
        role="button"
        tabIndex={0}
        onClick={selectStep}
        onKeyDown={onRowKeyDown}
        className={cn(
          'group border-border bg-background hover:bg-muted/50 relative flex w-full min-w-0 items-start gap-2 overflow-hidden rounded-lg border p-2.5 text-left text-sm transition-colors',
          'focus-visible:ring-brand-ring focus-visible:ring-2 focus-visible:outline-none',
          isSelected &&
            'border-brand/40 bg-brand-subtle before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-brand',
          isDragging && 'opacity-70',
        )}
      >
        <span className="text-muted-foreground mt-1 shrink-0 cursor-grab" aria-hidden>
          <GripVertical className="size-4" />
        </span>
        <span
          className={cn(
            'flex h-6 w-6 shrink-0 items-center justify-center rounded text-xs font-medium',
            isSelected ? 'bg-brand/15 text-brand' : 'bg-muted text-muted-foreground',
          )}
          aria-hidden
        >
          {step.step_index + 1}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 leading-6" title={step.human_readable_description}>
            <span className="min-w-0 truncate">{compactStepLabel(step.human_readable_description)}</span>
            {step.action_type ? (
              <span
                className="shrink-0 rounded-[4px] border px-1.5 text-[0.6875rem] font-medium capitalize leading-4"
                style={{ borderColor: stepTypeColor(step.action_type), color: stepTypeColor(step.action_type) }}
              >
                {step.action_type.replace(/_/g, ' ')}
              </span>
            ) : null}
          </span>
          {bboxState || step.branch_summary || step.for_each_summary ? (
            <span className="mt-1.5 flex flex-wrap gap-1.5">
              {bboxState ? <VisualBboxBadge state={bboxState} /> : null}
              {step.branch_summary ? <BranchSummaryBadge summary={step.branch_summary} /> : null}
              {step.for_each_summary ? <ForEachSummaryBadge summary={step.for_each_summary} /> : null}
            </span>
          ) : null}
        </span>
        <StepBadges step={step} isDirty={isDirty} belowThreshold={belowThreshold} onDeleteRequest={onDeleteRequest} />
      </div>
    </li>
  )
}

/** "N conditional steps" badge for if_present/try_dismiss/wait_for_one_of steps — makes the
 * (previously invisible) branch body's existence visible on the step row itself, even before
 * expanding the nested sub-list (see BranchSubList.tsx). */
function BranchSummaryBadge({ summary }: { summary: NonNullable<import('@/types/workflow').StepEditorDTO['branch_summary']> }) {
  const label =
    summary.kind === 'if_present'
      ? `${summary.step_count} conditional step${summary.step_count === 1 ? '' : 's'}`
      : summary.kind === 'try_dismiss'
        ? `try dismiss · ${summary.candidates?.length ?? 0} candidate${(summary.candidates?.length ?? 0) === 1 ? '' : 's'}`
        : `wait for one of · ${summary.options?.length ?? 0} option${(summary.options?.length ?? 0) === 1 ? '' : 's'}`
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex max-w-full items-center gap-1 rounded border border-violet-400/20 bg-violet-400/[0.06] px-1.5 py-0.5 text-[0.65rem] leading-none text-violet-300/90">
          <GitBranch className="size-3 shrink-0" aria-hidden />
          <span className="min-w-0 truncate">{label}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top">
        {summary.kind === 'if_present'
          ? `Runs its body only if "${summary.probe}" is present on the page. Best-effort — never enters recovery.`
          : summary.kind === 'try_dismiss'
            ? 'Tries each candidate selector in order and dismisses the first one found.'
            : 'Waits for one of several alternative states before continuing.'}
      </TooltipContent>
    </Tooltip>
  )
}

/** "Loops N steps" badge for a for_each step — the loop-suggestion Accept flow
 * (ForEachSuggestionBanner.tsx) replaces several visible steps with one for_each step whose body
 * is nested and otherwise invisible in this list; without this badge that looks exactly like the
 * wrapped steps (a filename click, a download) were deleted rather than moved into the loop. */
function ForEachSummaryBadge({ summary }: { summary: NonNullable<import('@/types/workflow').StepEditorDTO['for_each_summary']> }) {
  const source = summary.items ? `input "${summary.items}"` : summary.container_selector ? 'a matched row list' : 'no source set'
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex max-w-full items-center gap-1 rounded border border-sky-400/20 bg-sky-400/[0.06] px-1.5 py-0.5 text-[0.65rem] leading-none text-sky-100">
          <Repeat className="size-3 shrink-0" aria-hidden />
          <span className="min-w-0 truncate">
            loops {summary.step_count} step{summary.step_count === 1 ? '' : 's'} · {source}
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top">
        Runs its {summary.step_count} nested step{summary.step_count === 1 ? '' : 's'} once per item from{' '}
        {source}, up to {summary.max_iterations ?? '?'} times. Nothing was deleted — expand below to see them.
      </TooltipContent>
    </Tooltip>
  )
}

function VisualBboxBadge({ state }: { state: BboxState }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            'inline-flex max-w-full items-center gap-1 rounded border px-1.5 py-0.5 text-[0.65rem] leading-none',
            state.usable ? 'border-sky-400/20 bg-sky-400/[0.06] text-sky-300/90' : 'border-white/8 bg-white/[0.02] text-zinc-500',
          )}
        >
          <BoxSelect className="size-3 shrink-0" aria-hidden />
          <span className="min-w-0 truncate">bbox {state.label}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top">{state.title}</TooltipContent>
    </Tooltip>
  )
}

/** A single quiet status dot with an explanatory tooltip — replaces the old text badge row.
 *  Destructive/forced/hover-chain/generic-intent detail now surfaces in the step editor's own
 *  eyebrow line (InlineRetargetFlow) instead of cluttering every row in the list. */
function StatusDot({ colorClass, label, detail }: { colorClass: string; label: string; detail: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn('mt-1 size-1.5 shrink-0 rounded-full', colorClass)} aria-label={label} />
      </TooltipTrigger>
      <TooltipContent side="top">{detail}</TooltipContent>
    </Tooltip>
  )
}

function StepBadges({
  step,
  isDirty,
  belowThreshold,
  onDeleteRequest,
}: {
  step: StepEditorDTO
  isDirty: boolean
  belowThreshold?: boolean
  onDeleteRequest: (index: number) => void
}) {
  return (
    <span className="flex shrink-0 items-start gap-1.5">
      {belowThreshold ? (
        <StatusDot colorClass="bg-status-warn" label="Below confidence threshold" detail="This step's identity confidence is below the workflow's minimum — review it in Diagnostics." />
      ) : null}
      {isDirty ? <StatusDot colorClass="bg-amber-300" label="Edited" detail="Unsaved changes on this step." /> : null}
      {step.flags.is_destructive ? (
        <StatusDot colorClass="bg-status-error" label="Destructive" detail="This step performs a destructive action (e.g. delete, submit)." />
      ) : null}
      {step.safety.allow_forced_action ? (
        <StatusDot colorClass="bg-status-error" label="Forced" detail="This step is allowed to force the action even when the target looks non-interactable." />
      ) : null}
      {step.safety.has_hover_chain ? (
        <StatusDot colorClass="bg-sky-400" label="Hover chain" detail="This step depends on hovering other elements first before it can act." />
      ) : null}
      {step.flags.generic_intent ? (
        <StatusDot colorClass="bg-zinc-500" label="Generic intent" detail="This step's intent was not confidently inferred." />
      ) : null}
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            className="text-destructive hover:text-destructive -mr-1 h-7 w-7 opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100"
            onClick={(event) => {
              event.stopPropagation()
              onDeleteRequest(step.step_index)
            }}
            aria-label="Remove step"
          >
            <Trash2 className="size-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top">Remove step</TooltipContent>
      </Tooltip>
    </span>
  )
}
