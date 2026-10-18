import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchGroups, type GroupSummary } from '@/api/groupsApi'
import { fetchWorkflows, type Workflow } from '@/api/workflowsApi'
import { fetchEntitlements } from '@/api/usageApi'
import { listLifecycles, type Lifecycle } from '@/api/conversationApi'
import { workflowGlyph, worstStatus, type StatusKind } from '@/lib/status'

export type ShellWorkflow = { workflow: Workflow; kind: StatusKind; note: string; lifecycle?: Omit<Lifecycle, 'rows'> }
export type ShellGroup = { group: GroupSummary; workflows: ShellWorkflow[]; kind: StatusKind }

/**
 * Everything the sidebar and inspector need, from commands that already exist: every
 * workflow in its group from the moment it is created (list_workflows carries group_id),
 * with a status glyph from the interim stage mapping in lib/status.ts.
 */
export function useShellData() {
  const groupsQ = useQuery({ queryKey: ['groups'], queryFn: fetchGroups, refetchInterval: 15_000 })
  const workflowsQ = useQuery({ queryKey: ['workflows'], queryFn: fetchWorkflows, refetchInterval: 15_000 })
  const entitlementsQ = useQuery({ queryKey: ['entitlements'], queryFn: fetchEntitlements })
  // The backend's lifecycle is the source of truth for glyphs; the stage mapping in lib/status.ts
  // is only the fallback for the moment before it answers (or against an older engine).
  const lifecyclesQ = useQuery({ queryKey: ['lifecycles'], queryFn: listLifecycles, refetchInterval: 15_000, retry: false })

  const groups: ShellGroup[] = useMemo(() => {
    const all = workflowsQ.data?.workflows ?? []
    return (groupsQ.data?.groups ?? []).map((group) => {
      const workflows = all
        .filter((w) => w.group_id === group.id)
        .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0))
        .map((workflow) => {
          const lifecycle = lifecyclesQ.data?.lifecycles[workflow.id]
          return lifecycle
            ? { workflow, kind: lifecycle.glyph as StatusKind, note: lifecycle.note, lifecycle }
            : { workflow, ...workflowGlyph(workflow) }
        })
      return { group, workflows, kind: worstStatus(workflows.map((w) => w.kind)) }
    })
  }, [groupsQ.data, workflowsQ.data, lifecyclesQ.data])

  const workflowsById = useMemo(() => {
    const m = new Map<string, ShellWorkflow & { group: GroupSummary }>()
    for (const g of groups) for (const w of g.workflows) m.set(w.workflow.id, { ...w, group: g.group })
    return m
  }, [groups])

  /** Interim "publishable" count: signed-off workflows. BUILD-37 replaces this with a
   * count of approved-and-unpublished workflows from the backend. */
  const publishCount = useMemo(
    () => (workflowsQ.data?.workflows ?? []).filter((w) => w.signed_off).length,
    [workflowsQ.data],
  )

  return {
    groups,
    workflowsById,
    publishCount,
    entitlements: entitlementsQ.data,
    loading: groupsQ.isLoading || workflowsQ.isLoading,
    /** Either list could not be read — an empty result then means "unknown", not "nothing here". */
    failed: groupsQ.isError || workflowsQ.isError,
  }
}
