import { useState } from 'react'
import { ChevronDown, ChevronRight, Repeat } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { StepEditorDTO } from '@/types/workflow'
import { compactStepLabel } from '@/lib/workflowViewerHelpers'
import { useEditorStore } from '@/store/editorStore'

type Props = {
  /** The parent for_each step's own step_index — nested rows select this step and focus the
   * clicked nested index so the right column opens that step's own editor. */
  parentStepIndex: number
  forEachSteps: StepEditorDTO[]
  /** Selects a nested step, guarded the same way a top-level click is (WorkflowViewer's
   *  guardedSelect) — confirms first if a copilot proposal is pending on whatever's
   *  currently open, instead of silently discarding it. */
  onSelectNested: (parentStepIndex: number, nestedIndex: number) => void
}

/**
 * Indented, collapsible preview of a for_each step's nested body, rendered directly under its
 * WorkflowStepItem row — the for_each twin of BranchSubList.tsx. Clicking a row selects the
 * parent loop step and focuses that nested index, which StepEditor.tsx reads to swap the
 * right column to that nested step's own editor, addressed via `for_each.steps[N]` (see step_path.py) — the same experience a top-level step
 * gets. Structural edits (add/delete/reorder a loop body step) aren't supported yet (EXEC-38-UI).
 */
export function ForEachSubList({ parentStepIndex, forEachSteps, onSelectNested }: Props) {
  const [expanded, setExpanded] = useState(false)
  const selected = useEditorStore((s) => s.selectedStepIndex)
  const focusedBranchIndex = useEditorStore((s) => s.focusedBranchIndex)

  if (forEachSteps.length === 0) return null

  return (
    <li className="ml-6 border-l border-white/8 pl-2">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[0.7rem] text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-200"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        {expanded ? <ChevronDown className="size-3 shrink-0" /> : <ChevronRight className="size-3 shrink-0" />}
        <Repeat className="size-3 shrink-0 text-sky-300/80" aria-hidden />
        <span>
          {forEachSteps.length} step{forEachSteps.length === 1 ? '' : 's'} run each time round the loop
        </span>
      </button>
      {expanded ? (
        <ol className="space-y-1 py-1">
          {forEachSteps.map((nested, nestedIndex) => {
            const isFocused = selected === parentStepIndex && focusedBranchIndex === nestedIndex
            return (
              <li key={nested.id}>
                <button
                  type="button"
                  className={cn(
                    'flex w-full items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-left text-xs text-zinc-300 hover:bg-white/[0.04]',
                    isFocused && 'border-sky-400/40 bg-sky-400/[0.06] text-sky-100',
                  )}
                  onClick={() => onSelectNested(parentStepIndex, nestedIndex)}
                >
                  <span className="flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded bg-white/[0.06] text-[0.6rem] text-zinc-500">
                    {nestedIndex + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{compactStepLabel(nested.human_readable_description)}</span>
                </button>
              </li>
            )
          })}
        </ol>
      ) : null}
    </li>
  )
}
