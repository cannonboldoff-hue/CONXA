import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  cancelRecording,
  finalizeWorkflow,
  getWorkflowRecordingStatus,
  resolveFilePicker,
  resolveJsDialog,
  startWorkflowRecord,
  type Workflow,
} from '@/api/workflowsApi'
import { getGroupAuthStatus } from '@/api/groupsApi'
import { GroupAuthWizard } from '@/components/GroupAuthWizard'
import { CmdError } from '@/lib/ipc'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Loader2, Play } from 'lucide-react'

/** Walks the user through recording a workflow: instructions → optional URL
 * variables → live browser session → save. Moved verbatim out of
 * the removed per-workflow detail page so the group page's Record rail node
 * can open the same flow inline. */
export function RecordWorkflowDialog({
  open,
  onOpenChange,
  workflow,
  onRecorded,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  workflow: Workflow
  onRecorded: () => void
}) {
  const [step, setStep] = useState<1 | 2>(1)
  const [urlVariables, setUrlVariables] = useState<Record<string, string>>({})
  // Hover capture is in beta and disabled in the UI — always off until it's ready.
  const captureHover = false
  const [activeSession, setActiveSession] = useState<string | null>(null)
  const [error, setError] = useState('')

  // A recorded page's native alert/confirm/prompt is held open (not auto-dismissed — see
  // recorder/session.py's _on_dialog) until answered here, so the human sees the real box
  // instead of it vanishing instantly.
  const [pendingDialog, setPendingDialog] = useState<{
    requestId: string
    dialogType: string
    message: string
    defaultValue: string
  } | null>(null)
  const [promptText, setPromptText] = useState('')

  const workflowStartUrl = (workflow.protected_url || workflow.target_url).trim()
  const varPattern = /\{\{\s*([a-zA-Z][a-zA-Z0-9_]*)\s*\}\}/g
  const requiredVars = Array.from(workflowStartUrl.matchAll(varPattern), (m) => m[1])

  // Recording is pre-flight-gated on every app in the workflow's group being authenticated
  // (see handlers/session.py's cmd_start_recording) — an auth_required failure means the
  // browser never launched. Rather than just showing the error text, surface the wizard
  // right here so the user can fix it and retry without leaving this dialog.
  const [authBlocked, setAuthBlocked] = useState(false)

  const startMut = useMutation({
    mutationFn: () =>
      startWorkflowRecord(workflow.id, requiredVars.length > 0 ? urlVariables : undefined, captureHover),
    onSuccess: (data) => {
      setActiveSession(data.session_id)
      setError('')
      setAuthBlocked(false)
    },
    onError: (e: Error) => {
      setError(e.message)
      setAuthBlocked(e instanceof CmdError && e.code === 'auth_required')
    },
  })

  // Only the apps that came back not-ready are relevant here — a healthy sibling app
  // shouldn't clutter a "you're blocked" screen. Shares its query cache with
  // GroupAuthWizard's own internal fetch (same queryKey), so this never double-fetches.
  const authStatusQ = useQuery({
    queryKey: ['group-auth-status', workflow.group_id],
    queryFn: () => getGroupAuthStatus(workflow.group_id),
    enabled: authBlocked && !!workflow.group_id,
  })
  const notReadyAppIds = authStatusQ.data?.apps.filter((a) => a.state !== 'ready').map((a) => a.id)

  const finalizeMut = useMutation({
    mutationFn: () => finalizeWorkflow(workflow.id, activeSession!),
    onSuccess: () => {
      onOpenChange(false)
      setStep(1)
      setActiveSession(null)
      onRecorded()
    },
    onError: (e: Error) => {
      const message = e.message
      setError(message)
      if (message.toLowerCase().startsWith('no workflow actions were recorded')) {
        setActiveSession(null)
        onRecorded()
      }
    },
  })

  const cancelMut = useMutation({
    mutationFn: () => cancelRecording(activeSession!, workflow.id),
    onSettled: () => {
      setActiveSession(null)
      setError('')
      onRecorded()
    },
  })

  const isRecording = !!activeSession
  const statusQ = useQuery({
    queryKey: ['workflow-recording-status', workflow.id, activeSession],
    queryFn: () => getWorkflowRecordingStatus(activeSession!),
    enabled: isRecording && !finalizeMut.isPending,
    refetchInterval: 1000,
    retry: false,
  })
  const workflowBrowserClosed = statusQ.data?.browser_open === false

  // While recording, a "Choose File" click asks us (instead of Chrome's native picker,
  // which is deliberately suppressed — see recorder/session.py's filechooser listener) to
  // show Electron's own file-pick dialog pre-pointed at the session's download folder.
  useEffect(() => {
    if (!activeSession) return
    return window.conxa.onEvent(async (event) => {
      if (event.session_id !== activeSession) return
      if (event.action === 'file_picker_request') {
        const requestId = event.request_id as string
        const picked = await window.conxa.pickFile({
          defaultPath: event.default_dir as string,
          multiple: Boolean(event.multiple),
          filters: [{ name: 'All Files', extensions: ['*'] }],
        })
        await resolveFilePicker(activeSession, requestId, picked)
        return
      }
      if (event.action === 'js_dialog_request') {
        // The recorded page is blocked until this is answered — surface the Studio window
        // above the (now frozen-looking) recording browser so the human sees the modal.
        window.conxa.raiseWindow(true)
        setPromptText((event.default_value as string) || '')
        setPendingDialog({
          requestId: event.request_id as string,
          dialogType: event.dialog_type as string,
          message: event.message as string,
          defaultValue: (event.default_value as string) || '',
        })
        return
      }
      if (event.action === 'js_dialog_cancelled') {
        // Left unanswered too long — the recorder auto-accepted it itself; just close our
        // modal so it isn't left showing for a dialog that no longer exists.
        setPendingDialog((current) =>
          current && current.requestId === event.request_id ? null : current,
        )
        window.conxa.raiseWindow(false)
        return
      }
      if (event.action === 'js_dialog_resolved') {
        // The backend has actually accepted/dismissed the dialog and brought the recording
        // browser back to front by this point — see answerJsDialog below for why we wait for
        // this instead of unpinning as soon as resolveJsDialog's RPC call returns.
        window.conxa.raiseWindow(false)
      }
    })
  }, [activeSession])

  async function answerJsDialog(accepted: boolean) {
    if (!activeSession || !pendingDialog) return
    // resolveJsDialog only confirms the answer was queued, not that the recorder has processed
    // it — that happens on the recorder's own next pump tick, which is when the dialog is
    // actually accepted/dismissed and the recording browser is brought back to front. Unpinning
    // Studio right here (on the RPC ack) used to race that: Studio would drop its always-on-top
    // pin before the recording browser regained focus, leaving neither window truly focused —
    // the first click back in the browser just refocused it instead of registering, so it
    // looked like the click had to be done twice. Closing our own modal immediately is still
    // fine; only the un-pin waits for the "js_dialog_resolved" event above.
    await resolveJsDialog(activeSession, pendingDialog.requestId, accepted, promptText)
    setPendingDialog(null)
  }

  return (
    <>
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && activeSession && !finalizeMut.isPending) return
        if (!nextOpen) setStep(1)
        onOpenChange(nextOpen)
      }}
    >
      <DialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100 sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-white">Record Workflow</DialogTitle>
        </DialogHeader>

        {!isRecording && step === 1 ? (
          <div className="space-y-5 pt-1">
            <div className="space-y-3">
              <p className="text-xs font-semibold text-zinc-300">How it works</p>
              {[
                'Click "Start Recording" on the next screen.',
                'Do the steps in the browser like normal. Clicks, typing, and page changes are recorded.',
                'When done, close the browser, then click "Save Workflow Now".',
              ].map((text, i) => (
                <div key={i} className="flex items-start gap-3">
                  <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-white/8 text-[11px] font-bold text-zinc-400">{i + 1}</span>
                  <p className="text-xs leading-5 text-zinc-400">{text}</p>
                </div>
              ))}
            </div>
            <div className="border-t border-white/8" />
            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">Best practices</p>
              {[
                'Keep the recording to one task.',
                'Passwords are removed automatically — you don\'t need to do anything.',
                'Go slow. Let each page finish loading before you click or type the next thing.',
                'Do it in one clean pass — don\'t go back and redo steps.',
              ].map((tip, i) => (
                <p key={i} className="text-xs leading-5 text-zinc-500"><span className="mr-1.5 text-zinc-600">·</span>{tip}</p>
              ))}
            </div>
            <div className="border-t border-white/8" />
            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">Limitations</p>
              {[
                'Can\'t get past CAPTCHAs or other bot-check screens.',
                'Sites with bot detection, IP allowlisting, or similar anti-automation protection will block it, same as they\'d block any script.',
                'Can\'t automate logins that require a brand-new one-time code every single time.',
                'If a session expires partway through a run, it pauses and waits for someone to log back in — it won\'t force its way past a login screen.',
                'Drag-and-drop actions (like reordering items on a board) can be recorded, but may need extra review before they replay reliably.',
                'Canvas-drawn interfaces (like design tools or custom charts, where nothing is a real clickable element) aren\'t supported yet.',
                'Hover-revealed menus are in beta and not fully reliable yet.',
              ].map((tip, i) => (
                <p key={i} className="text-xs leading-5 text-zinc-500"><span className="mr-1.5 text-zinc-600">·</span>{tip}</p>
              ))}
            </div>
            <Button className="w-full" onClick={() => setStep(2)}>Next →</Button>
          </div>

        ) : !isRecording ? (
          <div className="space-y-4 pt-1">
            {requiredVars.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium text-zinc-400">URL variables <span className="text-zinc-600">(optional)</span></p>
                {requiredVars.map((varName) => (
                  <div key={varName} className="space-y-1">
                    <Label className="text-xs text-zinc-400">{varName}</Label>
                    <Input value={urlVariables[varName] || ''} onChange={(e) => setUrlVariables((prev) => ({ ...prev, [varName]: e.target.value }))} placeholder={`Enter ${varName} (optional)`} className="border-white/10 bg-white/5 text-zinc-100 h-8" />
                  </div>
                ))}
              </div>
            )}
            <p className="text-xs text-zinc-500">
              The browser will open pre-authenticated at{' '}
              <span className="font-mono text-zinc-300">
                {requiredVars.some((v) => urlVariables[v])
                  ? requiredVars.reduce((url, varName) => url.replace(new RegExp(`{{\\s*${varName}\\s*}}`), urlVariables[varName] || `{{${varName}}}`), workflowStartUrl)
                  : workflowStartUrl}
              </span>.
            </p>
            <label className="flex items-start gap-2 text-xs text-zinc-400 opacity-50">
              <Checkbox checked={false} disabled className="mt-0.5" />
              <span>
                <span className="inline-flex items-center gap-1.5">
                  This workflow uses hover menus
                  <span className="rounded-sm border border-zinc-600 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                    Beta
                  </span>
                </span>
                <span className="block text-zinc-500">
                  Hover-menu capture is in beta and turned off for now.
                </span>
              </span>
            </label>
            {error ? <p className="text-sm text-red-400">{error}</p> : null}
            {authBlocked && workflow.group_id ? (
              authStatusQ.data ? (
                <div className="space-y-3 rounded-lg border border-red-500/15 bg-red-500/[0.03] p-3">
                  <GroupAuthWizard
                    groupId={workflow.group_id}
                    appIds={notReadyAppIds}
                    onAllAuthenticated={() => {
                      setAuthBlocked(false)
                      setError('')
                      // Everything this workflow needs just got fixed — retry immediately
                      // instead of making the user click Start Recording a second time.
                      startMut.mutate()
                    }}
                  />
                </div>
              ) : (
                <p className="text-xs text-zinc-500">Checking authentication…</p>
              )
            ) : (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" className="flex-1 border-white/10 bg-white/5 text-zinc-300" onClick={() => { setUrlVariables({}); setError('') }}>Clear</Button>
                <Button className="flex-1" onClick={() => startMut.mutate()} disabled={startMut.isPending}>
                  {startMut.isPending ? <><Loader2 className="size-4 animate-spin" />Launching browser…</> : <><Play className="size-4" />Start Recording</>}
                </Button>
              </div>
            )}
          </div>

        ) : (
          <div className="space-y-4 pt-1">
            <div className="flex items-center gap-2 rounded-lg border border-blue-500/20 bg-blue-500/5 px-3 py-2">
              <Loader2 className="size-4 animate-spin text-blue-400" />
              <p className="text-xs text-blue-300">
                {finalizeMut.isPending
                  ? 'Saving workflow…'
                  : workflowBrowserClosed
                  ? 'Browser closed. Click Save Workflow Now to keep this recording, or Cancel to discard it.'
                  : 'Browser is open — perform your workflow, then close it when done.'}
              </p>
            </div>
            {error ? <p className="text-sm text-red-400">{error}</p> : null}
            {!finalizeMut.isPending && (
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="flex-1 border-white/10 bg-white/5 text-zinc-300"
                  onClick={() => cancelMut.mutate()}
                  disabled={cancelMut.isPending}
                >
                  Cancel
                </Button>
                <Button size="sm" className="flex-1" onClick={() => finalizeMut.mutate()}>
                  Save Workflow Now
                </Button>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>

    <Dialog open={!!pendingDialog} onOpenChange={() => {}}>
      <DialogContent
        className="border-white/10 bg-[#0d0f12] text-zinc-100"
        showCloseButton={false}
        onEscapeKeyDown={(e) => e.preventDefault()}
        onPointerDownOutside={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="text-white">
            {pendingDialog?.dialogType === 'prompt'
              ? 'The website wants input'
              : pendingDialog?.dialogType === 'confirm'
              ? 'The website asks'
              : 'The website says'}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 pt-1">
          <p className="rounded-md border border-white/10 bg-white/5 px-3 py-2 font-mono text-sm text-zinc-200">
            {pendingDialog?.message}
          </p>
          {pendingDialog?.dialogType === 'prompt' && (
            <div className="space-y-1.5">
              <Label htmlFor="js-dialog-prompt-text">Answer</Label>
              <Input
                id="js-dialog-prompt-text"
                autoFocus
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
              />
            </div>
          )}
          <div className="flex gap-2">
            {pendingDialog?.dialogType !== 'alert' && (
              <Button
                size="sm"
                variant="outline"
                className="flex-1 border-white/10 bg-white/5 text-zinc-300"
                onClick={() => answerJsDialog(false)}
              >
                Cancel
              </Button>
            )}
            <Button size="sm" className="flex-1" onClick={() => answerJsDialog(true)}>
              OK
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
    </>
  )
}
