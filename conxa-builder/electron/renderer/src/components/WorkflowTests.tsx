
import { type RefObject, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getLifecycle } from '@/api/conversationApi'
import { fetchWorkflow } from '@/api/workflowApi'
import {
  cancelTestWorkflow,
  getCompiledSkill,
  testWorkflow,
  type SkillPackBuild,
  type Workflow,
} from '@/api/workflowsApi'
import { getGroupAuthStatus } from '@/api/groupsApi'
import { RunGateDialog } from '@/components/RunGateDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { CheckCircle2, Loader2, PlayCircle, SlidersHorizontal, StopCircle, XCircle } from 'lucide-react'

/** mm:ss once a Run Test passes a minute, plain seconds before that — keeps the
 * "Testing…" label short while still showing real elapsed time (EXEC-35). */
function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  if (totalSeconds < 60) return `${totalSeconds}s`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m ${seconds}s`
}

type InputSpec = {
  id: string
  label?: string
  type?: string
  default?: string | null
  pattern?: string | null
  options?: string[]
  sensitive?: boolean
  required?: boolean
}

// eslint-disable-next-line no-control-regex
const ANSI_RE = /\x1b\[[0-9;]*[A-Za-z]/g

// Raw causes the runtime throws (run.js/resolution.js/handlers.js/browser.js) mapped to plain
// English. Checked as substring matches against the cause text, in order, first match wins.
const CAUSE_PATTERNS: Array<[RegExp, string]> = [
  [/Executable doesn't exist/i, 'The test browser isn’t installed. Restarting Build Studio should install it automatically.'],
  [/Timeout \d+ms exceeded/i, 'The page didn’t respond in time.'],
  [/net::ERR_NAME_NOT_RESOLVED/i, 'That website’s address couldn’t be found. Check the URL.'],
  [/net::ERR_CONNECTION|net::ERR_INTERNET_DISCONNECTED/i, 'Couldn’t connect to the website. Check your internet connection.'],
  [/Element not found \(resolve miss\)/i, 'The element this step needs wasn’t found on the page.'],
  [/Ambiguous element resolution/i, 'Found more than one matching element and couldn’t tell which one to use.'],
  [/Containing frame could not be located/i, 'The embedded section (iframe) holding this element couldn’t be found — the page may have changed.'],
  [/Element is disabled/i, 'The element was disabled and couldn’t be clicked or typed into.'],
  [/Tab not found/i, 'The browser tab this step needs wasn’t open.'],
  [/Verification failed:/i, 'The step ran, but the page didn’t end up in the expected state afterward.'],
  [/none of the candidate selectors appeared/i, 'None of the expected outcomes appeared on the page in time.'],
  [/^Assert text:/i, 'The page didn’t contain the expected text.'],
  [/^(URL check failed|Assert failed: URL)/i, 'The page didn’t navigate to the expected address.'],
  [/Authentication session was not captured/i, 'Sign-in wasn’t completed or saved. Please authenticate again.'],
  [/upload step has no file path/i, 'This step needs a file to upload, but none was provided.'],
  [/^Execution cancelled/i, 'The test was stopped before it finished.'],
  [/Runtime tool call timed out/i, 'The test stopped responding and had to be cancelled.'],
  [/Skill not found/i, 'This workflow isn’t in the test copy yet. Test it from its conversation; that builds a fresh copy first.'],
]

function friendlyCause(text: string): string {
  const hit = CAUSE_PATTERNS.find(([re]) => re.test(text))
  return hit ? hit[1] : text
}

// Runtime messages look like "Execution failed at step N: Step N (type) failed: <cause>" —
// the "Step N (type) failed:" marker isn't at the very start of the string, so this must not
// anchor to ^ or it silently misses and falls back to a step-less generic message.
const STEP_FAILURE_RE = /Step (\d+) \(([a-z_]+)\) failed: ([\s\S]*)$/i

function formatRuntimeError(msg: string): string {
  const stripped = msg.replace(ANSI_RE, '')
  const tailIdx = stripped.indexOf('Runtime log tail:')
  const cleaned = tailIdx !== -1 ? stripped.slice(0, tailIdx).trim() : stripped.trim()
  if (!cleaned) return 'Test failed'

  const stepMatch = cleaned.match(STEP_FAILURE_RE)
  if (stepMatch) {
    const [, stepNum, stepType, cause] = stepMatch
    return `Step ${stepNum} (${stepType}) failed: ${friendlyCause(cause.trim())}`
  }
  return friendlyCause(cleaned)
}

// The runtime appends a "Runtime log tail:" block (last few stderr lines) to its error.
// formatRuntimeError() strips it for the one-line summary; this returns it so the test panel
// can show the underlying cause instead of throwing the only diagnostic away.
function extractRuntimeDetail(msg: string): string {
  const stripped = msg.replace(ANSI_RE, '')
  const idx = stripped.indexOf('Runtime log tail:')
  return idx !== -1 ? stripped.slice(idx).trim() : ''
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function normalizeInputSpec(raw: unknown): InputSpec | null {
  if (typeof raw === 'string') {
    const id = raw.trim()
    return id ? { id, label: id.replace(/_/g, ' ') } : null
  }

  const rec = asRecord(raw)
  if (!rec) return null

  const id = String(rec.id ?? rec.name ?? '').trim()
  if (!id) return null

  const labelRaw = rec.label ?? rec.description
  const label = typeof labelRaw === 'string' && labelRaw.trim() ? labelRaw.trim() : id.replace(/_/g, ' ')
  const rawOptions = Array.isArray(rec.options) ? rec.options : Array.isArray(rec.enum) ? rec.enum : []
  const options = rawOptions.map((item) => String(item)).filter(Boolean)
  const defaultValue = rec.default == null ? null : String(rec.default)
  const type = typeof rec.type === 'string' && rec.type.trim() ? rec.type.trim() : options.length > 0 ? 'select' : 'text'

  return {
    id,
    label,
    type,
    default: defaultValue,
    pattern: typeof rec.pattern === 'string' ? rec.pattern : null,
    options,
    sensitive: rec.sensitive === true,
    required: rec.optional !== true && !(defaultValue != null && defaultValue.trim() !== ''),
  }
}

function inputSpecsFromPayload(payload: unknown): InputSpec[] {
  const rec = asRecord(payload)
  const rawItems = Array.isArray(payload)
    ? payload
    : Array.isArray(rec?.inputs)
      ? rec.inputs
      : Array.isArray(rec?.required)
        ? rec.required
        : []
  return rawItems.map(normalizeInputSpec).filter((item): item is InputSpec => item !== null)
}

function missingRequiredInputLabels(specs: InputSpec[], values: Record<string, string>) {
  return specs
    .filter((spec) => spec.required !== false && !String(values[spec.id] ?? '').trim())
    .map((spec) => spec.label || spec.id)
}

function WorkflowLogSection({
  logs,
  runDone,
  runError,
  logRef,
  onRetry,
}: {
  logs: string[]
  runDone: boolean
  runError: string
  logRef: RefObject<HTMLDivElement>
  onRetry?: () => void
}) {
  return (
    <div className="border-t border-white/8 px-3 pb-3 pt-2 space-y-3">
      {logs.length > 0 && (
        <div>
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Execution log
          </p>
          <div
            ref={logRef}
            className="max-h-48 overflow-y-auto rounded border border-white/8 bg-black/40 p-2 font-mono text-[10px] text-zinc-300 space-y-px"
          >
            {logs.map((line, i) => (
              <div key={i} className="flex gap-1.5">
                <span className="shrink-0 text-zinc-600">›</span>
                <span className="min-w-0 break-all">{line}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {runDone && (
        <div className="flex items-center gap-2 text-xs text-emerald-300">
          <CheckCircle2 className="size-3.5 shrink-0" />
          Test passed
        </div>
      )}
      {runError && (
        <div className="space-y-2">
          <div className="flex min-w-0 items-start gap-2 text-xs text-red-300">
            <XCircle className="mt-0.5 size-3.5 shrink-0" />
            <span className="min-w-0 max-h-24 overflow-y-auto break-all">{formatRuntimeError(runError)}</span>
          </div>
          {extractRuntimeDetail(runError) && (
            <details className="ml-6">
              <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-wider text-zinc-500 hover:text-zinc-300">
                Runtime log
              </summary>
              <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-all rounded border border-white/8 bg-black/40 p-2 font-mono text-[10px] text-zinc-400">{extractRuntimeDetail(runError)}</pre>
            </details>
          )}
          {onRetry && (
            <Button size="sm" variant="outline" onClick={onRetry} className="border-white/10 bg-white/5 text-zinc-200 hover:bg-white/10">
              <PlayCircle className="size-3.5" />
              Try again
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

function testStatusBadge(wf: Workflow) {
  if (wf.last_test_status === 'passed') {
    return (
      <Badge variant="outline" className="shrink-0 border-emerald-500/30 bg-emerald-500/10 text-[10px] text-emerald-300">
        Passed
      </Badge>
    )
  }
  if (wf.last_test_status === 'failed') {
    return (
      <Badge variant="outline" className="shrink-0 border-red-500/30 bg-red-500/10 text-[10px] text-red-300">
        Failed
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="shrink-0 border-zinc-500/30 bg-zinc-500/10 text-[10px] text-zinc-400">
      Never tested
    </Badge>
  )
}

// Edited since the last build. Run test still works (the backend builds a fresh test copy first, BUILD-104 (3)); only
// the inputs are read from the saved workflow then, since the old copy may not list them yet.
function isStaleTest(wf: Workflow, skillPackBuild: SkillPackBuild | null) {
  return (
    wf.edited_at != null &&
    skillPackBuild != null &&
    wf.edited_at > skillPackBuild.last_built_at
  )
}

export function WorkflowTestRow({
  wf,
  skillPackBuild,
  onComplete,
}: {
  wf: Workflow
  skillPackBuild: SkillPackBuild | null
  onComplete: () => void
}) {
  const [inputs, setInputs] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(wf.last_test_inputs ?? {}).map(([k, v]) => [k, String(v ?? '')]),
    ),
  )
  const [inputSpecs, setInputSpecs] = useState<InputSpec[] | null>(null)
  const [inputDialogOpen, setInputDialogOpen] = useState(false)
  const [inputDialogMode, setInputDialogMode] = useState<'edit' | 'run'>('run')
  const [inputError, setInputError] = useState('')
  const [running, setRunning] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [logs, setLogs] = useState<string[]>([])
  const [runError, setRunError] = useState('')
  const [runDone, setRunDone] = useState(false)
  const [gateOpen, setGateOpen] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  const stale = isStaleTest(wf, skillPackBuild)
  // An unanswered question blocks every test (the backend refuses too); "open" is the lifecycle's questions-waiting state.
  const lifecycleQ = useQuery({ queryKey: ['lifecycle', wf.id], queryFn: () => getLifecycle(wf.id), retry: false })
  const questionsOpen = lifecycleQ.data?.lifecycle.substate === 'open'
  const blockedReason = questionsOpen ? 'Answer Studio’s questions first' : undefined

  // EXEC-35: visible elapsed time while a test runs, so a stuck "Testing…" reads as
  // "how long has this been going" instead of a static, unreadable label.
  useEffect(() => {
    if (!running) return
    const startedAt = Date.now()
    setElapsedMs(0)
    const timer = setInterval(() => setElapsedMs(Date.now() - startedAt), 1000)
    return () => clearInterval(timer)
  }, [running])

  // Load input specs eagerly so we know which button layout to use.
  useEffect(() => {
    let alive = true
    const savedInputs = Object.fromEntries(
      Object.entries(wf.last_test_inputs ?? {}).map(([k, v]) => [k, String(v ?? '')]),
    )

    setInputSpecs(null)
    setInputs(savedInputs)

    async function loadInputSpecs() {
      // A compiled skill's input.json is authoritative — including when it declares none.
      // The compiler deliberately omits runtime-bound placeholders such as
      // {{downloaded_file_dir}} (upload_binding.py::filter_runtime_only_inputs), because
      // run.js's download_observed handler binds them from this run's own download.
      // Guessing inputs back out of execution.json would prompt for exactly the values the
      // download->upload binding exists to supply automatically.
      let declared: unknown = null
      try {
        if (!stale) {
          const compiled = await getCompiledSkill(wf.slug)
          declared = compiled.files['input.json'] ?? null
        }
      } catch {
        declared = null
      }

      let specs: InputSpec[] = []
      if (declared != null) {
        specs = inputSpecsFromPayload(declared)
      } else if (wf.skill_id) {
        // Pack built before input.json existed — fall back to the saved skill doc, which
        // handlers/compile.py has already run filter_runtime_only_inputs over.
        try {
          const wfData = await fetchWorkflow(wf.skill_id)
          specs = inputSpecsFromPayload(wfData.inputs ?? [])
        } catch {
          specs = []
        }
      }

      if (!alive) return
      setInputSpecs(specs)
      setInputs((prev) => {
        const next = { ...prev }
        for (const spec of specs) {
          if (!(spec.id in next)) next[spec.id] = spec.default ?? ''
        }
        return next
      })
    }

    void loadInputSpecs()

    return () => {
      alive = false
    }
  }, [wf.id, wf.skill_id, wf.slug, wf.last_test_inputs, stale])

  async function runTest() {
    setLogs([])
    setRunError('')
    setRunDone(false)
    setInputError('')
    if (inputSpecs === null) {
      setRunError('Inputs are still loading. Try again in a moment.')
      return
    }
    const missingInputs = missingRequiredInputLabels(inputSpecs, inputs)
    if (missingInputs.length > 0) {
      const message = `Enter required inputs before running: ${missingInputs.join(', ')}`
      setInputError(message)
      setRunError(message)
      setInputDialogMode('run')
      setInputDialogOpen(true)
      return
    }
    setRunning(true)
    setInputDialogOpen(false)
    try {
      const parsedInputs: Record<string, unknown> = {}
      for (const spec of inputSpecs) parsedInputs[spec.id] = inputs[spec.id] ?? ''
      const result = await testWorkflow(wf.id, parsedInputs, false, (msg) => {
        setLogs((prev) => [...prev, msg])
        setTimeout(() => logRef.current?.scrollTo(0, logRef.current.scrollHeight), 0)
      })
      // A user-initiated cancel (see cancelTest) resolves rather than throws — it's neither
      // a pass nor a failure worth an error banner, just back to idle.
      if (result.status !== 'cancelled') setRunDone(true)
      onComplete()
    } catch (err) {
      // Store the RAW message (incl. the "Runtime log tail:" block); the panel formats the
      // summary and exposes the tail via extractRuntimeDetail().
      setRunError(err instanceof Error ? err.message : 'Test failed')
      onComplete()
    } finally {
      setRunning(false)
    }
  }

  // EXEC-35: stop a hung or unwanted Run Test. Fires and forgets — runTest()'s own
  // await/finally is what actually flips `running` back to idle once the runtime
  // acknowledges the cancel.
  async function cancelTest() {
    setCancelling(true)
    try {
      await cancelTestWorkflow(wf.id)
    } finally {
      setCancelling(false)
    }
  }

  // Gate the run behind the workflow's group auth — same "belongs to the X
  // group and requires authentication to N applications" check the runtime
  // itself enforces, surfaced here so Studio users aren't left staring at a
  // runtime error when a session has expired.
  async function requestRun() {
    if (!wf.group_id) { void runTest(); return }
    try {
      const status = await getGroupAuthStatus(wf.group_id)
      if (status.ready) void runTest()
      else setGateOpen(true)
    } catch {
      void runTest()
    }
  }

  function openInputDialog(mode: 'edit' | 'run') {
    setInputError('')
    setInputDialogMode(mode)
    setInputDialogOpen(true)
  }

  function saveInputs() {
    setInputError('')
    setInputDialogOpen(false)
  }

  const canRun = !questionsOpen && !running && Boolean(wf.skill_id)

  return (
    <div className="overflow-hidden rounded-lg border border-white/8 bg-white/[0.02]">
      {/* Header row */}
      <div className="flex items-center justify-between gap-3 px-3 py-2.5">
        <div className="min-w-0 overflow-hidden">
          <p className="truncate text-sm font-medium text-white">{wf.name}</p>
          {questionsOpen && (
            <p className="mt-0.5 text-xs text-amber-400">Answer Studio’s questions first. Open the workflow’s conversation to answer them.</p>
          )}
          {wf.last_test_status === 'failed' && wf.last_test_error && !runError && logs.length === 0 && (
            <p className="mt-0.5 truncate text-xs text-red-300">{formatRuntimeError(wf.last_test_error)}</p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {testStatusBadge(wf)}

          {running && (
            <Button size="sm" variant="outline" onClick={() => void cancelTest()} disabled={cancelling}>
              <StopCircle className="size-3.5" />
              Cancel
            </Button>
          )}

          {/* No-inputs workflows: single "Run test" button runs directly */}
          {inputSpecs !== null && inputSpecs.length === 0 && (
            <Button
              size="sm"
              onClick={() => void requestRun()}
              disabled={!canRun}
              title={blockedReason}
            >
              {running ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <PlayCircle className="size-3.5" />
              )}
              {running ? `Testing… (${formatElapsed(elapsedMs)})` : runDone ? 'Re-run' : 'Run test'}
            </Button>
          )}

          {/* Input workflows: edit values separately, then run through the same input dialog. */}
          {inputSpecs !== null && inputSpecs.length > 0 && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => openInputDialog('edit')}
                disabled={!wf.skill_id}
              >
                <SlidersHorizontal className="size-3.5" />
                Inputs
              </Button>
              <Button
                size="sm"
                onClick={() => openInputDialog('run')}
                disabled={!canRun}
                title={blockedReason}
              >
                {running ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <PlayCircle className="size-3.5" />
                )}
                {running ? `Testing… (${formatElapsed(elapsedMs)})` : runDone ? 'Re-run' : 'Run test'}
              </Button>
            </>
          )}

          {/* Loading state */}
          {inputSpecs === null && (
            <Button size="sm" variant="outline" disabled>
              <Loader2 className="size-3.5 animate-spin" />
              Loading...
            </Button>
          )}
        </div>
      </div>

      <Dialog open={inputDialogOpen} onOpenChange={setInputDialogOpen}>
        <DialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100 sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-white">Workflow inputs</DialogTitle>
            <DialogDescription className="text-zinc-400">
              {inputDialogMode === 'run'
                ? 'Enter the required values, then run this workflow test.'
                : 'Edit the values that will be used the next time this workflow test runs.'}
            </DialogDescription>
          </DialogHeader>
          {inputSpecs !== null && inputSpecs.length > 0 && (
            <form
              id={`workflow-inputs-${wf.id}`}
              onSubmit={(e) => {
                e.preventDefault()
                if (inputDialogMode === 'run') {
                  void requestRun()
                } else {
                  saveInputs()
                }
              }}
              className="space-y-3"
            >
              <div className="grid max-h-[50vh] gap-3 overflow-y-auto pr-1">
                {inputSpecs.map((spec) => {
                  const displayLabel = spec.label || spec.id
                  return (
                    <div key={spec.id} className="grid gap-1">
                      <Label className="text-xs font-medium text-zinc-300">{displayLabel}</Label>
                      {spec.options && spec.options.length > 0 ? (
                        <select
                          value={inputs[spec.id] ?? ''}
                          onChange={(e) => setInputs((prev) => ({ ...prev, [spec.id]: e.target.value }))}
                          required={spec.required !== false}
                          className="h-8 rounded-md border border-white/10 bg-black/30 px-2 text-xs text-white outline-none focus:border-white/25"
                        >
                          <option value="">Select {displayLabel.toLowerCase()}</option>
                          {spec.options.map((option) => (
                            <option key={option} value={option}>
                              {option}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <Input
                          type={spec.sensitive ? 'password' : 'text'}
                          value={inputs[spec.id] ?? ''}
                          onChange={(e) => setInputs((prev) => ({ ...prev, [spec.id]: e.target.value }))}
                          placeholder={displayLabel ? `Enter ${displayLabel.toLowerCase()}...` : ''}
                          required={spec.required !== false}
                          pattern={spec.pattern ?? undefined}
                          className="h-8 text-xs"
                        />
                      )}
                    </div>
                  )
                })}
              </div>
              {inputError && (
                <div className="flex items-start gap-2 text-xs text-red-300">
                  <XCircle className="mt-0.5 size-3.5 shrink-0" />
                  <span>{inputError}</span>
                </div>
              )}
            </form>
          )}
          <DialogFooter className="border-white/8 bg-white/[0.03]">
            <Button
              type="button"
              variant="outline"
              onClick={() => setInputDialogOpen(false)}
              disabled={running}
              className="border-white/10 bg-white/5 text-zinc-200 hover:bg-white/10"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              form={`workflow-inputs-${wf.id}`}
              disabled={inputDialogMode === 'run' ? !canRun : !wf.skill_id}
            >
              {inputDialogMode === 'run' ? (
                running ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin" />
                    {`Testing… (${formatElapsed(elapsedMs)})`}
                  </>
                ) : (
                  <>
                    <PlayCircle className="size-3.5" />
                    Run test
                  </>
                )
              ) : (
                <>
                  <SlidersHorizontal className="size-3.5" />
                  Save inputs
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Log + result panel — shown whenever there's output */}
      {(logs.length > 0 || runDone || runError) && (
        <WorkflowLogSection logs={logs} runDone={runDone} runError={runError} logRef={logRef} onRetry={runError ? () => void runTest() : undefined} />
      )}

      {wf.group_id && (
        <RunGateDialog open={gateOpen} onOpenChange={setGateOpen} groupId={wf.group_id} onReady={() => void runTest()} />
      )}
    </div>
  )
}

