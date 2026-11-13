import { useState } from 'react'
import { ChevronDown, ChevronRight, GitBranch } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { StepEditorDTO } from '@/types/workflow'
import { compactStepLabel } from '@/lib/workflowViewerHelpers'
import { useEditorStore } from '@/store/editorStore'

type Props = {
  /** The parent branch step's own step_index — nested rows select this step and focus the
   * clicked nested index so the center column's BranchBodyEditor can scroll to it. */
  parentStepIndex: number
  branchSteps: StepEditorDTO[]
  /** Selects a nested step, guarded the same way a top-level click is (WorkflowViewer's
   *  guardedSelect) — confirms first if a copilot proposal is pending on whatever's
   *  currently open, instead of silently discarding it. */
  onSelectNested: (parentStepIndex: number, nestedIndex: number) => void
}

/**
 * Indented, collapsible preview of an if_present step's nested body, rendered directly under its
 * WorkflowStepItem row in the workflow list. Read-only preview — clicking a row selects the
 * parent branch step and focuses that nested index so BranchBodyEditor.tsx (the actual editing
 * surface, shown in the center column once the parent is selected) can scroll to it. This closes
 * the audit's P0 finding: branch bodies were previously invisible to an approver entirely.
 */
export function BranchSubList({ parentStepIndex, branchSteps, onSelectNested }: Props) {
  const [expanded, setExpanded] = useState(false)
  const selected = useEditorStore((s) => s.selectedStepIndex)
  const focusedBranchIndex = useEditorStore((s) => s.focusedBranchIndex)

  if (branchSteps.length === 0) return null

  return (
    <li className="ml-6 border-l border-white/8 pl-2">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[0.7rem] text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-200"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        {expanded ? <ChevronDown className="size-3 shrink-0" /> : <ChevronRight className="size-3 shrink-0" />}
        <GitBranch className="size-3 shrink-0 text-violet-300/80" aria-hidden />
        <span>
          {branchSteps.length} step{branchSteps.length === 1 ? '' : 's'} inside this condition
        </span>
      </button>
      {expanded ? (
        <ol className="space-y-1 py-1">
          {branchSteps.map((nested, nestedIndex) => {
            const isFocused = selected === parentStepIndex && focusedBranchIndex === nestedIndex
            return (
              <li key={nested.id}>
                <button
                  type="button"
                  className={cn(
                    'flex w-full items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-left text-xs text-zinc-300 hover:bg-white/[0.04]',
                    isFocused && 'border-brand/40 bg-brand-subtle text-brand',
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
