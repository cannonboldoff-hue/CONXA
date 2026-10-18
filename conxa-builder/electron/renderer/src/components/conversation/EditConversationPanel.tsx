import { useEffect, useRef, useState } from 'react'
import { MessageSquare, X } from 'lucide-react'
import { Conversation } from '@/components/conversation/Conversation'
import { PanelDivider } from '@/components/shell/PanelDivider'
import { Button } from '@/components/ui/button'
import { useThreadStore } from '@/store/threadStore'

const MIN = 300
const MAX = 440
const DEF = 340
const KEY = 'conxa-edit-conversation'

function load(): { width: number; open: boolean } {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) ?? '{}') as { width?: number; open?: boolean }
    return { width: Math.min(MAX, Math.max(MIN, Number(v.width) || DEF)), open: v.open !== false }
  } catch {
    return { width: DEF, open: true }
  }
}

/**
 * Human Edit's conversation: the same thread the workflow lives in, scoped to the step that is
 * selected. It replaces the old floating copilot — a proposal still arrives as a card with the
 * exact change and Apply / Undo, through the same gates. Whenever one is applied or undone the
 * editor is told to reload, so what the card says happened is what the step list shows.
 */
export function EditConversationPanel({
  workflowId,
  stepName,
  title,
  onWorkflowChanged,
}: {
  workflowId: string
  stepName?: string
  title: string
  onWorkflowChanged: () => void
}) {
  const threadId = `wf:${workflowId}`
  const [{ width, open }, setLayout] = useState(load)
  const save = (next: { width: number; open: boolean }) => {
    setLayout(next)
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next))
    } catch {
      /* per-viewer convenience only */
    }
  }

  // Reload the editor when a change card is applied or undone (never on first paint).
  const items = useThreadStore((s) => s.threads[threadId]?.items)
  const signature = (items ?? [])
    .filter((i) => i.card_type === 'pending_change' && (i.state === 'applied' || i.state === 'undone'))
    .map((i) => `${i.card_id}:${i.state}`)
    .join('|')
  const seen = useRef<string | null>(null)
  useEffect(() => {
    if (items === undefined) return
    if (seen.current !== null && seen.current !== signature) onWorkflowChanged()
    seen.current = signature
  }, [signature, items, onWorkflowChanged])

  if (!open) {
    return (
      <div className="flex w-11 shrink-0 flex-col items-center border-l border-[var(--border-subtle)] bg-[var(--bg-sidebar)] pt-3">
        <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" aria-label="Show conversation" title="Show conversation" onClick={() => save({ width, open: true })}>
          <MessageSquare className="size-4" />
        </Button>
      </div>
    )
  }

  return (
    <>
      <PanelDivider
        label="Resize conversation"
        value={width}
        min={MIN}
        max={MAX}
        grow={-1}
        onChange={(w) => save({ width: Math.min(MAX, Math.max(MIN, w)), open })}
        onNudge={(d) => save({ width: Math.min(MAX, Math.max(MIN, width + d * -16)), open })}
        onReset={() => save({ width: DEF, open })}
      />
      <aside aria-label="Conversation" className="flex min-h-0 shrink-0 flex-col bg-[var(--bg-base)]" style={{ width }}>
        <div className="flex h-11 shrink-0 items-center gap-2 border-b border-[var(--border-subtle)] px-3">
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--text-primary)]">Conversation</span>
          <Button type="button" variant="ghost" size="icon-sm" className="text-[var(--text-meta)]" aria-label="Hide conversation" title="Hide conversation" onClick={() => save({ width, open: false })}>
            <X className="size-4" />
          </Button>
        </div>
        <div className="min-h-0 flex-1">
          <Conversation
            key={threadId}
            threadId={threadId}
            anchor={stepName ? `the step “${stepName}”` : undefined}
            placeholder={stepName ? 'Ask about this step, or describe a change' : 'Ask about this workflow'}
            composerNote={stepName ? `${title} · “${stepName}” selected` : title}
          />
        </div>
      </aside>
    </>
  )
}
