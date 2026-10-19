import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchGroups, type GroupSummary } from '@/api/groupsApi'
import { UsageCards } from '@/components/EntitlementMeters'
import { NewGroupDialog } from '@/components/NewGroupDialog'
import { PageHeader } from '@/components/layout/PageHeader'
import type { WorkflowStage } from '@/components/StagePath'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { FolderKanban, Plus } from 'lucide-react'

function NewGroupButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size="sm" variant="brand" onClick={() => setOpen(true)}>
        <Plus className="size-4" />
        New Group
      </Button>
      <NewGroupDialog open={open} onOpenChange={setOpen} />
    </>
  )
}

/** Plain-language phrasing and bar colour for each lifecycle stage, so a group's
 * contents read as a sentence ("2 ready · 1 needs review") rather than as jargon.
 * Order here is the order segments are drawn in — finished work first. */
const STAGE_SUMMARY: { stage: WorkflowStage; color: string; short: string; label: (n: number) => string }[] = [
  { stage: 'ready', color: 'bg-status-ok', short: 'Ready', label: (n) => `${n} ready` },
  { stage: 'needs_test', color: 'bg-status-warn', short: 'To test', label: (n) => `${n} to test` },
  { stage: 'needs_review', color: 'bg-status-warn', short: 'To review', label: (n) => `${n} ${n === 1 ? 'needs' : 'need'} review` },
  { stage: 'compiling', color: 'bg-sky-500', short: 'Compiling', label: (n) => `${n} compiling` },
  { stage: 'queued', color: 'bg-sky-500', short: 'Queued', label: (n) => `${n} queued` },
  { stage: 'ready_to_compile', color: 'bg-zinc-500', short: 'To compile', label: (n) => `${n} to compile` },
  { stage: 'recording', color: 'bg-zinc-500', short: 'Recording', label: (n) => `${n} recording` },
  { stage: 'error', color: 'bg-status-error', short: 'Failed', label: (n) => `${n} failed` },
]

const STAGE_BY_KEY = Object.fromEntries(STAGE_SUMMARY.map((s) => [s.stage, s])) as Record<
  WorkflowStage,
  (typeof STAGE_SUMMARY)[number]
>

/** Status is carried by a glyph as well as a colour, per DESIGN.md §6. */
function stageGlyph(stage: WorkflowStage) {
  if (stage === 'ready') return '●'
  if (stage === 'error') return '○'
  return '▲'
}

/** One line covering every workflow in the group, including the ones the preview
 * list couldn't fit. Deliberately text-only — a segmented bar next to the named
 * list below was saying the same thing twice, and cost the height that made the
 * list clip on short windows. */
function StageBreakdown({ group }: { group: GroupSummary }) {
  if (group.workflow_count === 0) return null
  const present = STAGE_SUMMARY.map((s) => ({ ...s, count: group.stages?.[s.stage] ?? 0 })).filter((s) => s.count > 0)

  return <p className="truncate text-[0.6875rem] text-zinc-500">{present.map((s) => s.label(s.count)).join(' · ')}</p>
}

/** What's actually in the folder — the point of drawing it as one. */
function FolderContents({ group }: { group: GroupSummary }) {
  const preview = group.workflow_preview ?? []
  const hidden = group.workflow_count - preview.length

  if (group.workflow_count === 0) {
    return (
      <p className="text-xs text-zinc-600">
        Empty — open this group to record its first workflow.
      </p>
    )
  }

  return (
    <ul className="space-y-1.5">
      {preview.map((wf) => {
        const meta = STAGE_BY_KEY[wf.stage]
        return (
          <li key={wf.id} className="flex items-center gap-2 text-xs">
            <span
              aria-hidden
              className={cn(
                'shrink-0 text-[11px] leading-none',
                wf.stage === 'ready'
                  ? 'text-status-ok'
                  : wf.stage === 'error'
                  ? 'text-status-error'
                  : 'text-status-warn',
              )}
            >
              {stageGlyph(wf.stage)}
            </span>
            <span className="min-w-0 flex-1 truncate text-zinc-300">{wf.name}</span>
            <span className="shrink-0 text-[0.6875rem] text-zinc-600">{meta ? meta.short : ''}</span>
          </li>
        )
      })}
      {hidden > 0 && <li className="pl-4 text-[0.6875rem] text-zinc-600">+{hidden} more</li>}
    </ul>
  )
}

/** A group rendered as an actual folder — the tab notch is cut out of the card
 * itself (see `.folder-card` in globals.css), not drawn on top of a rectangle. */
function FolderCard({ group }: { group: GroupSummary }) {
  const navigate = useNavigate()
  const authLabel =
    group.apps_total === 0
      ? 'No apps yet'
      : `${group.apps_authenticated} of ${group.apps_total} app${group.apps_total === 1 ? '' : 's'} connected`

  return (
    <button
      type="button"
      onClick={() => navigate(`/groups/${encodeURIComponent(group.id)}`)}
      aria-label={`${group.name} — ${group.workflow_count} workflow${group.workflow_count === 1 ? '' : 's'}, ${authLabel}`}
      className={cn(
        'folder-card group relative rounded-[10px] bg-white/10 text-left',
        'transition-[transform,background-color] duration-150 ease-out',
        'hover:-translate-y-0.5 hover:bg-white/20 motion-reduce:transform-none motion-reduce:transition-none',
        'focus-visible:bg-brand focus-visible:outline-none',
      )}
    >
      {/* Inner layer — opaque so it masks the outer layer, leaving only a
          hairline of it visible as an outline that follows the folder edge. */}
      <span
        aria-hidden
        className={cn(
          'folder-card-inner absolute inset-px rounded-[10px] bg-[#101317] transition-colors duration-150',
          'group-hover:bg-[#171a1f]',
          // Focus thickens the visible hairline into a 2px clay outline that
          // still traces the folder edge, since a clipped card can't take a ring.
          'group-focus-visible:inset-[2px]',
        )}
      />

      <span className="absolute left-4 top-0 flex h-[26px] max-w-[150px] items-center truncate text-[0.6875rem] font-medium text-zinc-400">
        {group.workflow_count} workflow{group.workflow_count === 1 ? '' : 's'}
      </span>

      <div className="folder-body relative flex h-full flex-col gap-4 px-4 pb-4">
        <div className="flex items-center gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] text-zinc-400 transition-colors group-hover:border-brand/40 group-hover:text-brand">
            <FolderKanban className="size-4.5" />
          </span>
          <p className="min-w-0 flex-1 truncate text-base font-semibold text-white">{group.name}</p>
        </div>

        {/* `flex-1` fills the slack, but deliberately no `min-h-0`: the default
            `min-height: auto` is what stops this from shrinking below its own
            content and clipping a workflow name in half. */}
        <div className="flex-1 border-t border-white/6 pt-3">
          <FolderContents group={group} />
        </div>

        <div className="space-y-1.5">
          <StageBreakdown group={group} />

          <div className="flex items-center gap-1.5 text-xs">
            <span
              aria-hidden
              className={cn('text-[13px] leading-none', group.ready ? 'text-status-ok' : 'text-status-warn')}
            >
              {group.ready ? '●' : '▲'}
            </span>
            <span className={cn(group.ready ? 'text-status-ok' : 'text-status-warn')}>{authLabel}</span>
          </div>
        </div>
      </div>
    </button>
  )
}

export function WorkflowListPage() {
  const groupsQ = useQuery({ queryKey: ['groups'], queryFn: fetchGroups })
  const groups = groupsQ.data?.groups ?? []

  const header = (
    <PageHeader
      title="Workflows"
      description="Organize your automations by business group — each one owns its own logins."
      inlineActions
      actions={
        <>
          <UsageCards />
          <NewGroupButton />
        </>
      }
    />
  )

  if (groupsQ.isLoading) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        {header}
        <div className="min-h-0 flex-1 p-4 sm:p-6">
          <div className="folder-grid h-full">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="folder-card h-full w-full rounded-[10px]" />
            ))}
          </div>
        </div>
      </div>
    )
  }

  if (groupsQ.isError || !groupsQ.data) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        {header}
        <div className="flex flex-1 flex-col items-center justify-center gap-3">
          <p className="text-sm font-medium text-red-300">Failed to load groups</p>
          <p className="text-xs text-zinc-500">{(groupsQ.error as Error)?.message ?? 'Unknown error'}</p>
          <Button size="sm" variant="outline" onClick={() => void groupsQ.refetch()}>
            Retry
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {header}

      {groups.length === 0 ? (
        <div className="flex min-h-0 flex-1 items-center justify-center p-6">
          <div className="flex max-w-sm flex-col items-center gap-4 text-center">
            <div className="rounded-full border border-white/8 bg-white/[0.03] p-4">
              <FolderKanban className="size-7 text-zinc-700" />
            </div>
            <div>
              <p className="text-sm font-medium text-zinc-300">No groups yet</p>
              <p className="mt-1 text-xs text-zinc-500">
                A group is a business domain like Sales or Support. It holds your workflows and the logins they need.
              </p>
            </div>
            <NewGroupButton />
          </div>
        </div>
      ) : (
        // `.folder-scroll` is a size container: the grid sizes its rows against
        // this element's height, so exactly four folders fill the viewport and
        // everything past the fourth is honest scroll.
        <div className="folder-scroll min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
          <div className="folder-grid">
            {groups.map((g) => (
              <FolderCard key={g.id} group={g} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
