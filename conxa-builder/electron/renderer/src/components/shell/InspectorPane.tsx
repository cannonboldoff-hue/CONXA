import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getLifecycle } from '@/api/conversationApi'
import { PanelRightClose, PanelRightOpen } from 'lucide-react'
import { useShellData } from '@/hooks/useShellData'
import { useShellStore } from '@/store/shellStore'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { Button } from '@/components/ui/button'
import { interimProgress, STATUS } from '@/lib/status'
import { cn } from '@/lib/utils'

function hostOf(url: string) {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** The 44 px strip the inspector folds to — carries the workflow's status glyph. */
export function InspectorStrip({ workflowId }: { workflowId: string | null }) {
  const toggle = useShellStore((s) => s.toggleInspector)
  const { workflowsById } = useShellData()
  const w = workflowId ? workflowsById.get(workflowId) : undefined
  return (
    <div className="flex h-full w-full flex-col items-center gap-3 bg-[var(--bg-sidebar)] pt-3">
      <Button type="button" variant="ghost" size="icon-sm" className="text-[#8e8e8e]" aria-label="Unfold inspector (Ctrl .)" title="Unfold inspector (Ctrl .)" onClick={toggle}>
        <PanelRightOpen className="size-4" />
      </Button>
      {w && <StatusGlyph kind={w.kind} />}
    </div>
  )
}

/** Workflow inspector: lifecycle, details and the actions that belong to this workflow. */
export function InspectorPane({ workflowId }: { workflowId: string }) {
  const toggle = useShellStore((s) => s.toggleInspector)
  const { workflowsById } = useShellData()
  const navigate = useNavigate()
  const w = workflowsById.get(workflowId)
  // The backend derives the rows; the client-side mapping is the fallback until it answers.
  const lifecycleQ = useQuery({ queryKey: ['lifecycle', workflowId], queryFn: () => getLifecycle(workflowId), retry: false })
  const rows = lifecycleQ.data?.lifecycle.rows ?? (w ? interimProgress(w.workflow) : [])

  return (
    <aside aria-label="Workflow inspector" className="flex h-full min-h-0 w-full flex-col bg-[var(--bg-sidebar)]">
      <div className="flex h-14 shrink-0 items-center gap-2 px-4">
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold text-[#e8e6e3]">{w ? w.workflow.name : 'Workflow'}</h2>
        <Button type="button" variant="ghost" size="icon-sm" className="text-[#8e8e8e]" aria-label="Fold inspector (Ctrl .)" title="Fold inspector (Ctrl .)" onClick={toggle}>
          <PanelRightClose className="size-4" />
        </Button>
      </div>

      {!w ? (
        <div className="space-y-2 px-4" aria-hidden>
          {[0, 1, 2, 3].map((i) => <div key={i} className="h-5 animate-pulse rounded-[6px] bg-white/[0.05]" />)}
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 pb-4">
          <p className="text-xs text-[#8e8e8e]">{w.group.name}</p>

          <section aria-label="Lifecycle">
            <h3 className="pb-1.5 text-[0.6875rem] font-medium text-[#8e8e8e]">Lifecycle</h3>
            <ol className="space-y-1.5">
              {rows.map((r) => (
                <li key={r.label} className="flex items-start gap-2 text-sm">
                  <span className="mt-[3px]"><StatusGlyph kind={r.kind} /></span>
                  <span className="min-w-0">
                    <span className={cn(r.kind === 'none' ? 'text-[#8e8e8e]' : 'text-[#e8e6e3]')}>{r.label}</span>
                    {r.note && <span className="block text-xs text-[#8e8e8e]">{r.note}</span>}
                  </span>
                </li>
              ))}
            </ol>
          </section>

          <section aria-label="Details" className="space-y-2 text-sm">
            <h3 className="text-[0.6875rem] font-medium text-[#8e8e8e]">Details</h3>
            <div>
              <p className="text-xs text-[#8e8e8e]">Starting page</p>
              <p className="truncate font-mono text-xs text-[#e8e6e3]">{w.workflow.target_url ? hostOf(w.workflow.target_url) : '—'}</p>
            </div>
            <div>
              <p className="text-xs text-[#8e8e8e]">Apps it uses</p>
              <p className="text-[#e8e6e3]">{w.workflow.used_apps?.length ? w.workflow.used_apps.map((a) => a.name).join(', ') : '—'}</p>
            </div>
            <div>
              <p className="text-xs text-[#8e8e8e]">Steps</p>
              <p className="text-[#e8e6e3]">{w.workflow.step_count ?? '—'}</p>
            </div>
            <div>
              <p className="text-xs text-[#8e8e8e]">Status</p>
              <p className="flex items-center gap-1.5 text-[#e8e6e3]">
                <StatusGlyph kind={w.kind} />
                {w.note || STATUS[w.kind].label}
              </p>
            </div>
          </section>

          <section aria-label="Actions" className="space-y-2">
            <Button
              type="button"
              variant="outline"
              className="w-full"
              disabled={!w.workflow.skill_id}
              title={w.workflow.skill_id ? 'Open Human Edit (Ctrl E)' : 'Human Edit unlocks when compiling finishes.'}
              onClick={() => navigate(`/edit/${w.workflow.skill_id}?from=${encodeURIComponent(`/w/${w.workflow.id}`)}`)}
            >
              Human Edit
            </Button>
          </section>
        </div>
      )}
    </aside>
  )
}
