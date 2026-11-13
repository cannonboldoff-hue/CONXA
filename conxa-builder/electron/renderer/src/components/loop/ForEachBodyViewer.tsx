import { ChevronRight, Repeat } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { InfoHint } from '@/components/ui/info-hint'
import { editorHelp } from '@/lib/editorHelp'
import { compactStepLabel } from '@/lib/workflowViewerHelpers'
import type { StepEditorDTO } from '@/types/workflow'
import { useEditorStore } from '@/store/editorStore'

type Props = {
  /** The parent for_each step (for_each_summary is set). */
  step: StepEditorDTO
}

/**
 * The for_each loop's own summary + a list of its nested body steps, each of which opens the
 * SAME editor a top-level step gets (StepEditor's nestedForEachStep path) — clicking a row here
 * sets focusedBranchIndex, which swaps this card out for that step's own editor, addressed via
 * `for_each.steps[N]`.
 * Shown only while no nested step is focused — the twin of BranchBodyEditor.tsx for a loop body,
 * but without add/delete/reorder: patch_gate.py has no structural (insert/delete/move) route for
 * `for_each.steps[j]` yet, only the field-level patch route the step editor uses.
 */
export function ForEachBodyViewer({ step }: Props) {
  const setFocusedBranchIndex = useEditorStore((s) => s.setFocusedBranchIndex)
  const nested = step.for_each_steps
  const summary = step.for_each_summary

  return (
    <Card className="gap-2 bg-[linear-gradient(180deg,rgba(17,24,39,0.85),rgba(7,10,16,0.92))] py-3 ring-white/10">
      <CardHeader className="p-2.5 pb-1">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold">
          <Repeat className="size-4 text-sky-300" aria-hidden />
          Runs these steps once per item
          <InfoHint {...editorHelp.forEachBody} side="bottom" align="start" />
        </CardTitle>
        <CardDescription className="text-xs">
          {summary?.items
            ? <>Driven by input <span className="font-mono text-zinc-400">{summary.items}</span></>
            : summary?.container_selector
              ? <>Driven by rows matching <span className="font-mono text-zinc-400">{summary.container_selector}</span></>
              : 'No row source set yet.'}
          {', up to '}
          {summary?.max_iterations ?? '?'}
          {' times. Click a step below to view or edit it.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-1.5 p-2.5 pt-0">
        {nested.length === 0 ? (
          <p className="rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-xs text-zinc-500">
            This loop has no steps yet.
          </p>
        ) : (
          <ol className="space-y-1.5">
            {nested.map((n, i) => (
              <li key={n.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-lg border border-white/8 bg-black/20 p-2 text-left text-sm hover:bg-white/[0.04]"
                  onClick={() => setFocusedBranchIndex(i)}
                >
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-white/[0.06] text-[0.65rem] text-zinc-500">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-zinc-200">{compactStepLabel(n.human_readable_description)}</span>
                  <Badge variant="outline" className="shrink-0 text-[0.6rem]">
                    {n.action_type}
                  </Badge>
                  <ChevronRight className="size-3.5 shrink-0 text-zinc-500" aria-hidden />
                </button>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}
