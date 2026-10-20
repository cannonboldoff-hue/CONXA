import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Run a flow command: on failure, say so instead of leaving the card looking stuck. */
export function useFlowAction(workflowId: string) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      toast.error(errMsg(e))
    } finally {
      setBusy(false)
      qc.invalidateQueries({ queryKey: ['lifecycle', workflowId] })
      qc.invalidateQueries({ queryKey: ['lifecycles'] })
      qc.invalidateQueries({ queryKey: ['workflows'] })
    }
  }
  return { busy, run }
}
