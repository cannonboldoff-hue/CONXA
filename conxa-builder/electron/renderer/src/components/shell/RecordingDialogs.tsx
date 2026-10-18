import { useEffect, useState } from 'react'
import { resolveFilePicker, resolveJsDialog } from '@/api/workflowsApi'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useRecordingStore } from '@/store/recordingStore'

type Pending = { sessionId: string; requestId: string; dialogType: string; message: string; defaultValue: string }

/**
 * While a recording is on screen in a conversation, the page can ask for things only the
 * Studio can answer: a native file picker (suppressed in the recorder's browser) and
 * alert/confirm/prompt dialogs. Same behaviour as the old record dialog, answered only for
 * sessions a recording card is watching so the two never answer the same request twice.
 */
export function RecordingDialogs() {
  const watching = useRecordingStore((s) => s.watching)
  const [pending, setPending] = useState<Pending | null>(null)
  const [text, setText] = useState('')
  const active = Object.keys(watching).length > 0

  useEffect(() => {
    if (!active) return
    return window.conxa.onEvent(async (event) => {
      const sessionId = String(event.session_id ?? '')
      if (!sessionId || !useRecordingStore.getState().watching[sessionId]) return
      if (event.action === 'file_picker_request') {
        const picked = await window.conxa.pickFile({
          defaultPath: event.default_dir as string,
          multiple: Boolean(event.multiple),
          filters: [{ name: 'All Files', extensions: ['*'] }],
        })
        await resolveFilePicker(sessionId, String(event.request_id), picked)
      } else if (event.action === 'js_dialog_request') {
        // The recorded page is blocked until this is answered — bring Studio above the browser.
        window.conxa.raiseWindow(true)
        setText(String(event.default_value ?? ''))
        setPending({
          sessionId, requestId: String(event.request_id), dialogType: String(event.dialog_type),
          message: String(event.message ?? ''), defaultValue: String(event.default_value ?? ''),
        })
      } else if (event.action === 'js_dialog_cancelled') {
        setPending((cur) => (cur && cur.requestId === event.request_id ? null : cur))
        window.conxa.raiseWindow(false)
      } else if (event.action === 'js_dialog_resolved') {
        window.conxa.raiseWindow(false)
      }
    })
  }, [active])

  const answer = async (accepted: boolean) => {
    if (!pending) return
    const p = pending
    setPending(null)
    await resolveJsDialog(p.sessionId, p.requestId, accepted, text)
  }

  return (
    <Dialog open={pending != null} onOpenChange={(o) => !o && void answer(false)}>
      <DialogContent className="border-white/10 bg-[var(--bg-surface)] text-[var(--text-primary)]">
        <DialogHeader><DialogTitle>The page is asking</DialogTitle></DialogHeader>
        <p className="whitespace-pre-wrap text-sm text-[var(--text-secondary)]">{pending?.message}</p>
        {pending?.dialogType === 'prompt' && <Input value={text} onChange={(e) => setText(e.target.value)} autoFocus />}
        <DialogFooter>
          {pending?.dialogType !== 'alert' && <Button variant="outline" onClick={() => answer(false)}>Cancel</Button>}
          <Button variant="brand" onClick={() => answer(true)}>OK</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
