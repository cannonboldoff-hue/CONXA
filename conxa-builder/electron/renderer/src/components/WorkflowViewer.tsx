import { Fragment, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { StepEditorDTO } from '../types/workflow'
import { useEditorStore } from '../store/editorStore'
import { useRetargetStore } from '@/store/retargetStore'
import { useCopilotStore } from '@/store/copilotStore'
import { rejectCopilotProposal } from '@/api/workflowApi'
import { ListPlus, Plus } from 'lucide-react'
import { effectStepFlags, type AddActionKind } from '@/lib/workflowViewerHelpers'
import { WorkflowHeader } from '@/components/workflowViewer/WorkflowHeader'
import { WorkflowStepItem } from '@/components/workflowViewer/WorkflowStepItem'
import { DeleteStepDialog } from '@/components/workflowViewer/DeleteStepDialog'
import { BranchSubList } from '@/components/workflowViewer/BranchSubList'
import { ForEachSubList } from '@/components/workflowViewer/ForEachSubList'
import { TabDivider } from '@/components/workflowViewer/TabDivider'
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

type Props = {
  /** BUILD-26: only needed to log a rejection if a step-switch discards a pending copilot
   *  proposal — see the dirty guard below. */
  skillId: string
  steps: StepEditorDTO[]
  onReorder: (newOrder: number[]) => void
  onDelete: (index: number) => void
  onAddAction: (actionKind: AddActionKind) => void
  /** Drop a recording screenshot (custom drag payload) onto a step to swap visuals and refresh anchors. */
  onDroppedRecordingScreenshot?: (stepIndex: number, eventIndex: number) => void
  /** Drop “No image” payload to detach screenshot and clear anchors. */
  onClearStepVisual?: (stepIndex: number) => void
  recordingShotDragActive?: boolean
  /** compile_health.steps_below_threshold — shown as a warn dot on the matching row. */
  stepsBelowThreshold?: number[]
}

export function WorkflowViewer({
  skillId,
  steps,
  onReorder,
  onDelete,
  onAddAction,
  onDroppedRecordingScreenshot,
  onClearStepVisual,
  recordingShotDragActive,
  stepsBelowThreshold,
}: Props) {
  const belowThresholdSet = new Set(stepsBelowThreshold ?? [])
  const effectFlags = effectStepFlags(steps)
  const selected = useEditorStore((s) => s.selectedStepIndex)
  const focusedBranchIndex = useEditorStore((s) => s.focusedBranchIndex)
  const dirty = useEditorStore((s) => s.dirtySteps)
  const setSel = useEditorStore((s) => s.setSelectedStepIndex)
  const selectBranchStep = useEditorStore((s) => s.selectBranchStep)
  const [deleteIndex, setDeleteIndex] = useState<number | null>(null)
  const [draggingIndex, setDraggingIndex] = useState<number | null>(null)
  // `nested: null` for a plain top-level target; a number for a nested for_each/branch step under
  // that top-level index (see guardedSelect below).
  const [pendingSelect, setPendingSelect] = useState<{ index: number; nested: number | null } | null>(null)
  // Which unsaved-state kind is blocking the switch — decides the dialog copy/action below.
  const [pendingKind, setPendingKind] = useState<'retarget' | 'copilot' | null>(null)

  // The re-target wizard stages selector/validation edits in useRetargetStore until Apply
  // (see InlineRetargetFlow) — switching steps before Apply would silently drop them, so confirm
  // first instead. `dirty` only ever gets set once a phase-2/3 edit actually happens. Shared by
  // every way a selection can change — a plain top-level click (handleSelect) and a nested
  // for_each/branch row (handleSelectNested) alike — since a nested for_each step now stages the
  // exact same wizard state a top-level one does (InlineRetargetFlow's nestedForEachStep path),
  // and rows in ForEachSubList/BranchSubList used to call selectBranchStep directly, bypassing
  // this guard entirely.
  //
  // BUILD-26: a pending copilot proposal is the same kind of "you're about to lose this" state —
  // reuses the same dialog rather than a second one, but discarding it must still COUNT as a
  // rejection (stage d: a proposal a reviewer walks away from is logged as loudly as one they
  // accept), not a silent drop.
  const guardedSelect = (target: { index: number; nested: number | null }) => {
    if (target.index === selected && target.nested === focusedBranchIndex) return
    const rt = useRetargetStore.getState()
    if (rt.dirty) {
      setPendingKind('retarget')
      setPendingSelect(target)
      return
    }
    if (useCopilotStore.getState().pendingProposal) {
      setPendingKind('copilot')
      setPendingSelect(target)
      return
    }
    if (target.nested === null) setSel(target.index)
    else selectBranchStep(target.index, target.nested)
  }

  const handleSelect = (index: number) => guardedSelect({ index, nested: null })
  const handleSelectNested = (index: number, nestedIndex: number) => guardedSelect({ index, nested: nestedIndex })

  const move = (from: number, to: number) => {
    if (to < 0 || to >= steps.length || from === to) return
    const order = steps.map((_, i) => i)
    const [moved] = order.splice(from, 1)
    order.splice(to, 0, moved)
    onReorder(order)
  }

  return (
    <>
      {/* Same gradient-fill depth treatment as PanelChrome (components/ui/panel-chrome.tsx), applied
          inline rather than nested — this pane is flush against the grid's draggable resizer, so
          PanelChrome's rounded corners + outer shadow would clash with that boundary. */}
      <aside className="border-border relative z-10 flex min-h-0 min-w-0 flex-col border-b bg-[linear-gradient(180deg,rgba(17,24,39,0.9),rgba(7,10,16,0.95))] ring-1 ring-inset ring-white/[0.03] md:border-r md:border-b-0">
        <WorkflowHeader onAddAction={onAddAction} />
        <ScrollArea className="min-h-[12rem] w-full flex-1 md:min-h-0">
          {steps.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
              <span className="flex size-11 items-center justify-center rounded-xl border border-white/10 bg-white/[0.03] text-zinc-400" aria-hidden>
                <ListPlus className="size-5" />
              </span>
              <div className="space-y-1">
                <p className="text-sm font-medium text-zinc-200">No steps yet</p>
                <p className="text-xs text-zinc-500">Add your first action to start building this workflow.</p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="gap-1.5"
                onClick={() => onAddAction('click')}
              >
                <Plus className="size-3.5" />
                Add first action
              </Button>
            </div>
          ) : (
          <ol className="w-full space-y-1.5 p-2">
            {steps.map((step, index) => {
              const prevTabId = index > 0 ? steps[index - 1]?.tab?.id : undefined
              const tabId = step.tab?.id
              const showTabDivider = !!tabId && tabId !== 'tab_0' && tabId !== prevTabId
              return (
              <Fragment key={step.id}>
                {showTabDivider ? (
                  <TabDivider tabNumber={(step.tab?.index ?? 0) + 1} url={step.tab?.url} />
                ) : null}
                <WorkflowStepItem
                  step={step}
                  isSelected={selected === step.step_index}
                  isDirty={dirty.has(step.step_index)}
                  belowThreshold={belowThresholdSet.has(step.step_index)}
                  isEffect={effectFlags[index]}
                  isDragging={draggingIndex === step.step_index}
                  recordingShotDragActive={recordingShotDragActive}
                  draggingIndex={draggingIndex}
                  onSelect={handleSelect}
                  onDeleteRequest={setDeleteIndex}
                  onDragStart={setDraggingIndex}
                  onDragEnd={() => setDraggingIndex(null)}
                  onMove={move}
                  onDroppedRecordingScreenshot={onDroppedRecordingScreenshot}
                  onClearStepVisual={onClearStepVisual}
                />
                <BranchSubList
                  parentStepIndex={step.step_index}
                  branchSteps={step.branch_steps}
                  onSelectNested={handleSelectNested}
                />
                <ForEachSubList
                  parentStepIndex={step.step_index}
                  forEachSteps={step.for_each_steps}
                  onSelectNested={handleSelectNested}
                />
              </Fragment>
              )
            })}
          </ol>
          )}
        </ScrollArea>
      </aside>

      <DeleteStepDialog
        deleteIndex={deleteIndex}
        onOpenChange={(open) => !open && setDeleteIndex(null)}
        onConfirm={() => {
          if (deleteIndex === null) return
          onDelete(deleteIndex)
          setDeleteIndex(null)
        }}
      />

      <AlertDialog
        open={pendingSelect !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPendingSelect(null)
            setPendingKind(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingKind === 'copilot'
                ? 'Reject the copilot’s pending proposal?'
                : 'Discard your unsaved re-target edits?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingKind === 'copilot'
                ? "The copilot proposed a change on the current step that you haven't accepted or rejected. Switching steps now will reject it."
                : "You've changed selectors or outcome checks on the current step that haven't been applied yet. Switching steps now will lose those changes."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => { setPendingSelect(null); setPendingKind(null) }}>
              Keep editing
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (pendingKind === 'copilot') {
                  const proposal = useCopilotStore.getState().pendingProposal
                  useCopilotStore.getState().setPendingProposal(null)
                  // Fire-and-forget, same as every other reject path — a logging failure must
                  // never block the step switch the reviewer already confirmed.
                  if (proposal) void rejectCopilotProposal(skillId, proposal)
                } else {
                  useRetargetStore.getState().reset()
                }
                if (pendingSelect !== null) {
                  if (pendingSelect.nested === null) setSel(pendingSelect.index)
                  else selectBranchStep(pendingSelect.index, pendingSelect.nested)
                }
                setPendingSelect(null)
                setPendingKind(null)
              }}
            >
              {pendingKind === 'copilot' ? 'Reject and switch' : 'Discard edits'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
