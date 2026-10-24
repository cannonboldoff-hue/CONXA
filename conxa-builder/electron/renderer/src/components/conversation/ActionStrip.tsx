import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { flowDoAction, type Lifecycle } from '@/api/conversationApi'
import { Button } from '@/components/ui/button'
import { useFlowAction } from '@/components/conversation/cards/useFlowAction'
import { useOpenEdit } from '@/components/conversation/cards/CompileClarifyCards'
import { ACTION_INFO } from '@/lib/actions'

/** The next step at every stage, always visible: one button per action the lifecycle allows right now.
 *  Typing the same thing in chat runs the same action — this is just the discoverable version. */
export function ActionStrip({ workflowId, lifecycle }: { workflowId: string; lifecycle: Lifecycle | undefined }) {
  const { busy, run } = useFlowAction(workflowId)
  const navigate = useNavigate()
  const openEdit = useOpenEdit(`wf:${workflowId}`)
  const actions = (lifecycle?.allowed_actions ?? []).filter((a) => ACTION_INFO[a]?.strip)
  // No button to show, but "checking" is a wait the person should see: nothing else on screen says why Test isn't there yet.
  if (!lifecycle || (actions.length === 0 && lifecycle.substate !== 'checking')) return null

  const press = (id: string) => {
    const info = ACTION_INFO[id]
    if (info.kind === 'nav') {
      if (info.target === 'edit') openEdit?.()
      else navigate('/publish')
      return
    }
    void run(async () => {
      const r = await flowDoAction(workflowId, id)
      if (r.ok === false && r.message) toast.error(r.message)
    })
  }

  return (
    <div className="flex flex-wrap items-center gap-2 py-1" role="group" aria-label="Next steps">
      <span className="text-xs text-[var(--text-meta)]">{lifecycle.note}</span>
      {actions.map((id) => (
        <Button key={id} size="sm" variant={ACTION_INFO[id].primary ? 'brand' : 'outline'} disabled={busy} onClick={() => press(id)}>
          {ACTION_INFO[id].label}
        </Button>
      ))}
    </div>
  )
}
