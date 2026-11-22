import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { CardShell, Disclosure } from '@/components/conversation/parts'
import { useOpenEdit } from '@/components/conversation/cards/CompileClarifyCards'
import { readsPlainly, type CardProps } from '@/components/conversation/cards/ChangeAndDraftCards'
import { useThreadStore } from '@/store/threadStore'

/** A typed command with a side effect (record, compile, test, approve…). Nothing has happened until Confirm. */
export function ConfirmActionCard({ threadId, item }: CardProps) {
  const act = useThreadStore((s) => s.act)
  const [busy, setBusy] = useState(false)
  const state = String(item.state)
  const label = String(item.label ?? 'Do this')

  const send = async (action: 'confirm' | 'cancel') => {
    setBusy(true)
    const r = await act(threadId, String(item.card_id), action)
    setBusy(false)
    if (r && r.ok === false && r.message) toast.error(r.message)
  }

  if (state === 'dismissed') return <p className="text-sm text-[var(--text-meta)]">Cancelled — {label.toLowerCase()} didn’t run.</p>
  if (state === 'expired') return <p className="text-sm text-[var(--text-meta)]">{label} is no longer available — things have moved on.</p>
  if (state === 'running') return <p className="text-sm text-[var(--text-secondary)]">{label}…</p>
  if (state === 'done' || state === 'failed') {
    return <p className="text-sm text-[var(--text-secondary)]">{state === 'failed' ? 'Couldn’t: ' : ''}{String(item.receipt || label)}</p>
  }
  return (
    <CardShell
      glyph="warn"
      title={label}
      meta={String(item.describe ?? '')}
      footer={
        <>
          <Button variant="brand" size="sm" disabled={busy} onClick={() => send('confirm')}>Confirm</Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => send('cancel')}>Cancel</Button>
        </>
      }
    />
  )
}

/** "Open Human Edit", "Open <workflow>", "Publish": the renderer navigates; the card is just the button. */
export function ActionLinkCard({ threadId, item }: CardProps) {
  const act = useThreadStore((s) => s.act)
  const navigate = useNavigate()
  const openEdit = useOpenEdit(threadId)
  const target = String(item.target ?? '')
  const go = () => {
    void act(threadId, String(item.card_id), 'open')
    if (target === 'edit') openEdit?.(typeof item.step_index === 'number' ? item.step_index : undefined)
    else if (target === 'publish') navigate('/publish')
    else if (target === 'workflow' && item.workflow_id) navigate(`/w/${String(item.workflow_id)}`)
    else toast.error('That isn’t available yet.')
  }
  // The model's reason may name element addresses: kept one click away, never in the title. A card saved by an
  // older build has the reason in its title ("Open step 3 in Human Edit — role=button…"), so it is split here.
  const [head, ...tail] = String(item.title ?? 'Open').split(' — ')
  const reason = tail.join(' — ')
  const oldReason = reason && !readsPlainly(reason, false) ? reason : ''
  const detail = [typeof item.detail === 'string' ? item.detail : '', oldReason].filter(Boolean).join('\n')
  return (
    <CardShell title={oldReason ? head : String(item.title ?? 'Open')} footer={<Button variant="brand" size="sm" onClick={go}>Open</Button>}>
      {detail ? <Disclosure label="Technical details">{detail}</Disclosure> : null}
    </CardShell>
  )
}
