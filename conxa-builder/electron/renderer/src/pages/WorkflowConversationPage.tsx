import { useEffect, useMemo, useRef } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { flowBegin, getLifecycle } from '@/api/conversationApi'
import { PageHeader } from '@/components/layout/PageHeader'
import { Conversation } from '@/components/conversation/Conversation'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { ActionStrip } from '@/components/conversation/ActionStrip'
import { StageBar } from '@/components/conversation/StageBar'
import { useShellData } from '@/hooks/useShellData'

/**
 * A workflow's home: one continuous conversation carrying everything that happens to it.
 * Recording, compile, Clarify, tests and approval arrive as cards in later phases (BUILD-38
 * onward).
 */
export function WorkflowConversationPage() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const { workflowsById, loading } = useShellData()
  const w = workflowId ? workflowsById.get(workflowId) : undefined
  const location = useLocation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const lifecycleQ = useQuery({ queryKey: ['lifecycle', workflowId], queryFn: () => getLifecycle(workflowId!), enabled: Boolean(workflowId), retry: false })
  const lifecycle = lifecycleQ.data?.lifecycle
  const stageBar = useMemo(() => <StageBar lifecycle={lifecycle} />, [lifecycle])

  const begin = async () => {
    if (!workflowId) return
    try {
      await flowBegin(workflowId)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      qc.invalidateQueries({ queryKey: ['lifecycle', workflowId] })
      qc.invalidateQueries({ queryKey: ['lifecycles'] })
    }
  }

  // "Create and start recording" lands here with a flag; begin once, then drop the flag so a
  // reload doesn't start another recording.
  const kicked = useRef(false)
  useEffect(() => {
    if (kicked.current || !workflowId || !(location.state as { startRecording?: boolean } | null)?.startRecording) return
    kicked.current = true
    navigate(location.pathname, { replace: true, state: null })
    void begin()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId, location.state])

  if (!w) {
    return (
      <>
        <PageHeader title={loading ? 'Opening workflow…' : 'Workflow not found'} />
        <div className="mx-auto max-w-[44rem] space-y-3 p-6" aria-hidden={loading}>
          {loading ? (
            [0, 1, 2].map((i) => <div key={i} className="h-10 animate-pulse rounded-[8px] bg-white/[0.05]" />)
          ) : (
            <p className="text-sm text-[var(--text-secondary)]">This workflow isn’t in the list any more. Pick another from the sidebar.</p>
          )}
        </div>
      </>
    )
  }

  const { workflow, group, kind, note } = w

  return (
    <>
      <PageHeader
        title={workflow.name}
        description={
          <span className="inline-flex items-center gap-1.5">
            {group.name} · <StatusGlyph kind={kind} /> {note || 'Ready'}
          </span>
        }
        extra={stageBar}
      />
      <div className="flex h-full min-h-0 flex-col">
        <div className="min-h-0 flex-1">
          <Conversation
            key={workflow.id}
            threadId={`wf:${workflow.id}`}
            placeholder="Ask about this workflow, or describe a change"
            composerNote={workflow.name}
            empty={<p className="text-sm text-[var(--text-meta)]">This is the start of {workflow.name}’s conversation.</p>}
            stageDividers
            tail={<ActionStrip workflowId={workflow.id} lifecycle={lifecycle} />}
          />
        </div>
      </div>
    </>
  )
}
