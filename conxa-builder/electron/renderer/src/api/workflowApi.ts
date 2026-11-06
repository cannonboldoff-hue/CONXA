import { cmd, CmdError } from '@/lib/ipc'
import type { BackendEvent } from '@/lib/ipc'
import { errorMessages } from '@/lib/errorMessages'
import type {
  ForEachSuggestion,
  WorkflowResponse,
  WorkflowRevalidationResponse,
  WorkflowStepMutationResponse,
  WorkflowUndoRedoResponse,
} from '../types/workflow'

export { RECORDING_SCREENSHOT_DRAG_MIME, RECORDING_DRAG_MODE_CLEAR_VISUAL } from '@/lib/dragConstants'

export type SkillPackBuildLogEntry = Record<string, unknown>

export class SkillPackBuildRequestError extends Error {
  readonly buildLog: SkillPackBuildLogEntry[]

  constructor(message: string, buildLog: SkillPackBuildLogEntry[]) {
    super(message)
    this.name = 'SkillPackBuildRequestError'
    this.buildLog = buildLog
    Object.setPrototypeOf(this, new.target.prototype)
  }
}

export const errorMessage = (err: unknown, fallback: string) => {
  // Prefer friendly copy keyed on the backend error code, then the raw backend
  // message, then the caller's fallback.
  if (err instanceof CmdError && errorMessages[err.code]) {
    return errorMessages[err.code]
  }
  if (err instanceof Error) {
    const msg = err.message.trim()
    if (msg) return msg
  }
  if (typeof err === 'string' && err.trim()) return err.trim()
  return fallback
}

export type SkillSummary = {
  skill_id: string
  title: string
  version: number
  step_count: number
  modified_at: number
}

export type SkillPackageWorkflowSummary = {
  workflow_slug: string
  display_label?: string
  modified_at: number
  files: string[]
}

export type SkillPackageSummary = {
  package_name: string
  package_folder?: string
  package_path?: string
  modified_at: number
  workflows: SkillPackageWorkflowSummary[]
  files: string[]
}

export type SkillPackageFiles = {
  package_name: string
  bundle_name?: string
  files: Record<string, string>
}

export type RecordingScreenshotFrameDTO = {
  label: string
  url: string
}

export type RecordingScreenshotItemDTO = {
  event_index: number
  sequence: number
  persisted_full_screenshot: string
  preview_url: string
  viewport: string
  has_element_snapshot: boolean
  frame: Record<string, unknown>
  /** Every timed frame (before_far/before_near/at/after_near/after_far) captured around this event. */
  frames: RecordingScreenshotFrameDTO[]
}

export type SkillPackBuildPayload = {
  json_text: string
  package_name?: string
  bundle_name?: string
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'

export type JobRecord = {
  job_id: string
  kind: string
  status: JobStatus
  resource_id?: string | null
  retry_count: number
  user_error?: string | null
  internal_error_code?: string | null
  result?: Record<string, unknown> | null
  created_at: number
  updated_at: number
}

export type EnqueuedJob = Pick<JobRecord, 'job_id' | 'status' | 'resource_id'>

export type JobEvent = {
  ts: number
  event: string
  message: string
  data: Record<string, unknown>
}

export type SkillPackBuildApiResult = {
  name: string
  bundle_slug?: string
  index_json: string
  skill_md: string
  execution_json: string
  recovery_json: string
  inputs_json: string
  manifest_json: string
  input_count: number
  step_count: number
  used_llm: boolean
  warnings: string[]
  workflow_names?: string[]
  build_log?: SkillPackBuildLogEntry[]
}

// ─────────────────────────────────────────────────
// Skills
// ─────────────────────────────────────────────────

export function fetchSkillList(): Promise<{ skills: SkillSummary[] }> {
  return cmd<{ skills: SkillSummary[] }>('list_skills')
}

export function deleteSkillPackage(skillId: string): Promise<{ skill_id: string; title: string; deleted: boolean }> {
  return cmd<{ skill_id: string; title: string; deleted: boolean }>('delete_skill', { skill_id: skillId })
}

export function fetchWorkflow(skillId: string): Promise<WorkflowResponse> {
  // 'get_skill_workflow' — distinct from 'get_workflow', which now fetches the
  // top-level Workflow entity (see api/workflowsApi.ts). This one still fetches
  // a compiled skill's step-editor payload by skill_id, unchanged otherwise.
  return cmd<WorkflowResponse>('get_skill_workflow', { skill_id: skillId })
}

export function fetchRecordingScreenshots(skillId: string): Promise<{
  skill_id: string
  session_id: string | null
  items: RecordingScreenshotItemDTO[]
}> {
  return cmd('list_recording_screenshots', { skill_id: skillId })
}

// `path` addresses a nested for_each loop-body step ("for_each.steps[N]") instead of the
// top-level step at stepIndex — see retargetPreview's `path` doc comment. Omit for an
// ordinary top-level step.
export function postApplyRecordingVisual(
  skillId: string,
  stepIndex: number,
  body: { event_index: number; frame_label?: string },
  path?: string,
): Promise<WorkflowRevalidationResponse> {
  return cmd('apply_recording_visual', { skill_id: skillId, step_index: stepIndex, ...body, path })
}

export function postApplyStepFrame(
  skillId: string,
  stepIndex: number,
  frameLabel: string,
  path?: string,
): Promise<WorkflowRevalidationResponse> {
  return cmd('apply_step_frame', { skill_id: skillId, step_index: stepIndex, frame_label: frameLabel, path })
}

export function postClearStepVisual(
  skillId: string,
  stepIndex: number,
  path?: string,
): Promise<WorkflowRevalidationResponse> {
  return cmd('clear_step_visual', { skill_id: skillId, step_index: stepIndex, path })
}

export function postUpdateVisualBbox(
  skillId: string,
  stepIndex: number,
  body: { x: number; y: number; w: number; h: number },
): Promise<WorkflowRevalidationResponse> {
  return cmd('update_visual_bbox', { skill_id: skillId, step_index: stepIndex, ...body })
}

// ─────────────────────────────────────────────────
// Re-target wizard (Phase 2/3 preview + atomic apply)
// ─────────────────────────────────────────────────

export type Bbox = { x: number; y: number; w: number; h: number }

export type RetargetCandidate = {
  selector: string
  engine: string
  durability: number
  /** test-contract | semantic-aria | visible-text | spatial-anchor | structural — signals in
   *  different classes are independent axes of identity (see selector_score.py). */
  orthogonality_class?: string
  /** compiler | llm | input_bound | user — where this candidate's selector came from. */
  source?: string
  match_count: number
  unique: boolean
  /** unique = uniquely matched the recorded page; not_unique = matched 0 or many; unverified =
   *  couldn't be checked offline (validated by the browser at run time). */
  verified?: 'unique' | 'not_unique' | 'unverified'
  descriptor: string
}

/** `RetargetCandidate` plus a client-only stable id, so the Review Selectors phase can let a
 *  human reorder/edit/add/remove candidates by identity instead of by array index. Never sent
 *  to the backend — `retargetApply` only ever sees the plain `selector` strings. */
export type EditableCandidate = RetargetCandidate & { id: string }

export type PickQuality = 'good' | 'ambiguous' | 'none'

/** Recovery anchors generated for a redrawn region, computed once here at preview time so
 *  `retargetApply` can pass them straight through instead of re-calling the vision LLM. */
export type VisualAnchors = { anchors: Record<string, unknown>[]; intent: string }

export type RetargetPreviewResponse = {
  bbox: Bbox
  pick_quality: PickQuality
  candidates: RetargetCandidate[]
  current_wait_for: Record<string, unknown>
  proposed_wait_for: Record<string, unknown>
  current_assertions: Record<string, unknown>[]
  proposed_assertions: Record<string, unknown>[]
  validation_changed: boolean
  fast_finish: boolean
  /** Step-level identity-signal-quality rollup (0-1); null/undefined when there's no
   *  identity_bundle to derive it from. */
  compile_confidence?: number | null
  /** Only set when this preview regenerated (redrawn region) — null on the review-only path. */
  visual_anchors?: VisualAnchors | null
}

// regenerate=false reviews the already-compiled selectors (no LLM); pass true only when the
// user re-picked the element and the selectors must be regenerated for the new target.
// `path` addresses a nested for_each loop-body step ("for_each.steps[N]") instead of the
// top-level step at stepIndex — see cmd_retarget_preview's `path` parameter
// (handlers/workflow_editor.py). Omit for an ordinary top-level step.
export function retargetPreview(
  skillId: string,
  stepIndex: number,
  bbox: Bbox,
  regenerate = true,
  path?: string,
): Promise<RetargetPreviewResponse> {
  return cmd('retarget_preview', { skill_id: skillId, step_index: stepIndex, ...bbox, regenerate, path })
}

export function retargetApply(
  skillId: string,
  stepIndex: number,
  body: {
    bbox: Bbox
    primary_selector: string
    fallback_selectors: string[]
    keep_validation: boolean
    proposed_wait_for?: Record<string, unknown>
    proposed_assertions?: Record<string, unknown>[]
    /** Human-edited assertion list from the Validation phase — takes precedence over
     *  proposed_assertions/current assertions when present. */
    edited_assertions?: Record<string, unknown>[]
    /** Anchors already generated at Continue's preview — reused here so Apply doesn't call the
     *  vision LLM again. Omitted/undefined on the position-only/no-preview fallback paths. */
    visual_anchors?: VisualAnchors | null
  },
  /** Addresses a nested for_each loop-body step ("for_each.steps[N]") — see retargetPreview's
   *  `path` doc comment. Omit for an ordinary top-level step. */
  path?: string,
): Promise<WorkflowRevalidationResponse> {
  return cmd('retarget_apply', { skill_id: skillId, step_index: stepIndex, ...body, path })
}

export function patchStep(
  skillId: string,
  stepIndex: number,
  patch: Record<string, unknown>,
  assistLlm = false,
  /** Addresses a nested branch-body step (e.g. "branch.steps[1]") instead of the top-level step
   * at stepIndex — see cmd_patch_step's `path` parameter (handlers/workflow_editor.py). Omit for
   * ordinary top-level step patches. */
  path?: string,
): Promise<WorkflowRevalidationResponse> {
  return cmd('patch_step', { skill_id: skillId, step_index: stepIndex, patch, assist_llm: assistLlm, path })
}

export function patchSkillInputs(
  skillId: string,
  body: { inputs: Record<string, unknown>[]; title?: string | null },
): Promise<Record<string, unknown>> {
  return cmd<Record<string, unknown>>('update_workflow_inputs', { skill_id: skillId, ...body })
}

export function postWorkflowReplaceLiterals(
  skillId: string,
  body: { find: string; replace_with: string },
): Promise<{
  skill_id: string
  meta: Record<string, unknown>
  workflow: WorkflowResponse
  match_count: number
}> {
  return cmd('replace_literals', { skill_id: skillId, ...body })
}

export function renameSkill(skillId: string, title: string): Promise<Record<string, unknown>> {
  return cmd<Record<string, unknown>>('rename_skill', { skill_id: skillId, title })
}

export function postValidate(skillId: string): Promise<Record<string, unknown>> {
  return cmd<Record<string, unknown>>('validate_workflow', { skill_id: skillId })
}

export function postReorder(skillId: string, newOrder: number[]): Promise<WorkflowStepMutationResponse> {
  return cmd('reorder_steps', { skill_id: skillId, new_order: newOrder })
}

export function postInsertStep(
  skillId: string,
  body: { action_kind: string; insert_after?: number | null },
): Promise<WorkflowStepMutationResponse> {
  return cmd('insert_step', { skill_id: skillId, ...body })
}

export function deleteStep(skillId: string, stepIndex: number): Promise<WorkflowStepMutationResponse> {
  return cmd('delete_step', { skill_id: skillId, step_index: stepIndex })
}

/** Structural mutations scoped to an if_present step's nested body
 * (`steps[stepIndex]["branch"]["steps"]`) — mirror postInsertStep/deleteStep/postReorder but
 * addressed by the parent step's index. See BranchBodyEditor.tsx. */
export function postInsertBranchStep(
  skillId: string,
  stepIndex: number,
  body: { action_kind: string; insert_after?: number | null },
): Promise<WorkflowStepMutationResponse> {
  return cmd('insert_branch_step', { skill_id: skillId, step_index: stepIndex, ...body })
}

export function deleteBranchStep(
  skillId: string,
  stepIndex: number,
  nestedIndex: number,
): Promise<WorkflowStepMutationResponse> {
  return cmd('delete_branch_step', { skill_id: skillId, step_index: stepIndex, nested_index: nestedIndex })
}

export function postReorderBranchSteps(
  skillId: string,
  stepIndex: number,
  newOrder: number[],
): Promise<WorkflowStepMutationResponse> {
  return cmd('reorder_branch_steps', { skill_id: skillId, step_index: stepIndex, new_order: newOrder })
}

/** Human resolves a recorder-flagged optional interstitial (recording-next-steps.md Priority 2).
 * `keepAsStep=false` (default) converts the step into a real try_dismiss branch; `true` is the
 * reviewer's "No, it's not optional" answer — clears the hint, leaves the step untouched. See
 * StepEditorDTO.optional_hint and cmd_confirm_optional_interstitial. */
export function confirmOptionalInterstitial(
  skillId: string,
  stepIndex: number,
  keepAsStep = false,
): Promise<WorkflowStepMutationResponse> {
  return cmd('confirm_optional_interstitial', { skill_id: skillId, step_index: stepIndex, keep_as_step: keepAsStep })
}

export function undoWorkflow(skillId: string): Promise<WorkflowUndoRedoResponse> {
  return cmd('undo_workflow', { skill_id: skillId })
}

export function redoWorkflow(skillId: string): Promise<WorkflowUndoRedoResponse> {
  return cmd('redo_workflow', { skill_id: skillId })
}

export function postCompileUpdated(
  skillId: string,
  skillTitle?: string,
): Promise<Record<string, unknown>> {
  return cmd<Record<string, unknown>>('compile_updated', { skill_id: skillId, skill_title: skillTitle ?? null })
}

export type SignOffResult = {
  skill_id: string
  signed_off: boolean
  built: boolean
  waiting_on: string[]
  build_error?: string
}

export function postSignOff(skillId: string): Promise<SignOffResult> {
  return cmd<SignOffResult>('sign_off_workflow', { skill_id: skillId })
}

export function postStartRecording(): Promise<{ session_id: string }> {
  return cmd<{ session_id: string }>('start_recording', {})
}

export function getRecordingStatus(sessionId: string): Promise<{
  session_id: string
  browser_open: boolean
  event_count: number
  ended_by_user: boolean
  binding_errors: string[]
}> {
  return cmd('get_recording_status', { session_id: sessionId })
}

export function postStopRecording(sessionId: string): Promise<{ session_id: string; status: string }> {
  return cmd<{ session_id: string; status: string }>('stop_recording', { session_id: sessionId })
}

export function postCompileSession(sessionId: string, skillTitle?: string): Promise<{
  skill_id: string
  version: number
  step_count: number
  audit_status: string
}> {
  return cmd('compile', { session_id: sessionId, skill_title: skillTitle ?? '' })
}

export function fetchSkillDocument(skillId: string): Promise<Record<string, unknown>> {
  return cmd<Record<string, unknown>>('get_skill_document', { skill_id: skillId })
}

export function fetchMetrics(): Promise<Record<string, unknown>> {
  return cmd<Record<string, unknown>>('get_metrics')
}

// ─────────────────────────────────────────────────
// Skill packages
// ─────────────────────────────────────────────────

export function fetchSkillPackageList(): Promise<{ packages: SkillPackageSummary[]; bundle_root: string }> {
  return cmd('list_skill_packages')
}

export function fetchSkillPackageFiles(bundleName: string): Promise<SkillPackageFiles> {
  return cmd<SkillPackageFiles>('list_skill_package_files', { package_name: bundleName })
}

export function deleteStoredSkillPackage(bundleName: string): Promise<{ package_name: string; deleted: boolean }> {
  return cmd<{ package_name: string; deleted: boolean }>('delete_skill_package', { package_name: bundleName })
}

export function renameStoredSkillPackage(
  bundleName: string,
  newName: string,
): Promise<{ package_name: string; previous_name: string }> {
  return cmd<{ package_name: string; previous_name: string }>('rename_skill_package', {
    package_name: bundleName,
    new_name: newName,
  })
}

export function postBuildSkillPack(body: SkillPackBuildPayload): Promise<SkillPackBuildApiResult> {
  return cmd<SkillPackBuildApiResult>('build_skill_pack', body)
}

/** Shared listener for the `pack_log` / `pack_done` / `pack_error` event stream
 * emitted by both `build_skill_pack_stream` and `append_skill_pack_stream`. */
function runSkillPackStream(
  rpcCommand: string,
  payload: Record<string, unknown>,
  onLog: ((entry: SkillPackBuildLogEntry) => void) | undefined,
  failureMessage: string,
): Promise<SkillPackBuildApiResult> {
  return new Promise<SkillPackBuildApiResult>((resolve, reject) => {
    const unsub = window.conxa.onEvent((ev: BackendEvent) => {
      if (ev.phase === 'pack_log' && onLog) onLog(ev.entry as SkillPackBuildLogEntry)
      if (ev.phase === 'pack_done') {
        unsub()
        resolve(ev.result as SkillPackBuildApiResult)
      }
      if (ev.phase === 'pack_error') {
        unsub()
        const bl = Array.isArray(ev.build_log) ? (ev.build_log as SkillPackBuildLogEntry[]) : []
        reject(new SkillPackBuildRequestError(String(ev.message ?? failureMessage), bl))
      }
    })
    cmd(rpcCommand, payload).catch((err) => {
      unsub()
      reject(err)
    })
  })
}

export function postBuildSkillPackStream(
  body: SkillPackBuildPayload,
  onLog?: (entry: SkillPackBuildLogEntry) => void,
): Promise<SkillPackBuildApiResult> {
  return runSkillPackStream('build_skill_pack_stream', body, onLog, 'Skill pack build failed')
}

export function postAppendSkillPackStream(
  bundleName: string,
  body: { json_text: string; package_name?: string },
  onLog?: (entry: SkillPackBuildLogEntry) => void,
): Promise<SkillPackBuildApiResult> {
  return runSkillPackStream(
    'append_skill_pack_stream',
    { bundle_name: bundleName, ...body },
    onLog,
    'Skill pack append failed',
  )
}

export function postAppendSkillPack(
  bundleName: string,
  body: { json_text: string; package_name?: string },
): Promise<SkillPackBuildApiResult> {
  return cmd<SkillPackBuildApiResult>('append_skill_pack', { bundle_name: bundleName, ...body })
}

export function patchSkillPackBundleRoot(bundleRoot: string): Promise<{ bundle_root: string }> {
  return cmd<{ bundle_root: string }>('set_skill_pack_bundle_root', { bundle_root: bundleRoot })
}

// ── Human Review Copilot (BUILD-26) ──────────────────────────────────────────────────────────
// A chat turn diagnoses from the server-assembled evidence bundle (conxa_compile/editor/
// evidence.py — the copilot backend resolves the workflow's most recent test run on its own, no
// run_id needed from here) and may return pre-gated proposals; the renderer never applies one
// itself — accept delegates server-side to the exact patch_step path a manual edit takes, reject
// only logs. See conxa_compile/editor/copilot_proposals.py for what "pre-gated" means.

export type CopilotTurnMessage = { role: 'user' | 'assistant'; text: string }

export type CopilotProposal = {
  id: string
  command: string
  why: string
  evidence_refs?: string[]
  preview: { before: unknown; after: unknown }
  // `patch_step` proposals only:
  patch?: Record<string, unknown>
  step_key?: string | null
  field?: string
  // `insert_overlay_branch` proposals only (BUILD-26 stage f) — an inserted step, not an edited
  // field, so it carries no step_key/field of its own.
  primitive?: 'try_dismiss' | 'if_present'
  overlay_id?: string
  after_step_key?: string | null
  nested_step?: Record<string, unknown> | null
  // `structural_op` proposals only (BUILD-26 stage g) — insert/delete/move a step, or a
  // workflow-level edit. `op` names which; the renderer never inspects the rest itself, it just
  // round-trips whatever the gate produced back to `accept_copilot_proposal` unchanged (see
  // acceptCopilotProposal below) — the server is the only side that interprets these fields.
  op?: 'insert_step' | 'delete_step' | 'move_step' | 'update_inputs' | 'replace_literals'
  action_kind?: string
  fields?: Record<string, unknown>
  identity_from_step_key?: string | null
  inputs?: unknown[]
  find?: string
  replace?: string
}

export type CopilotTurnResult = {
  reply: string
  proposals: CopilotProposal[]
}

/** `onDelta`, when given, is called with each piece of the reply's text as the model generates
 *  it (relayed from the backend's `copilot_delta` events — see cmd_copilot_turn) — the same
 *  `onEvent` mechanism runSkillPackStream uses, just without a terminal event to watch for
 *  since the final `{reply, proposals}` already arrives via this call's own resolution. */
export function copilotTurn(
  skillId: string,
  message: string,
  transcript: CopilotTurnMessage[],
  onDelta?: (text: string) => void,
): Promise<CopilotTurnResult> {
  if (!onDelta) return cmd('copilot_turn', { skill_id: skillId, message, transcript })
  const unsub = window.conxa.onEvent((ev: BackendEvent) => {
    if (ev.phase === 'copilot_delta' && typeof ev.text === 'string') onDelta(ev.text)
  })
  return cmd<CopilotTurnResult>('copilot_turn', { skill_id: skillId, message, transcript }).finally(unsub)
}

/** Resolves the proposal's step_key to the CURRENT step_index server-side, then patches through
 *  cmd_patch_step — same undo entry, same edits.jsonl trail (attributed source="copilot") a
 *  manual edit gets. Refuses with `proposal_stale` if the workflow changed since this was shown.
 *
 *  An `insert_overlay_branch` proposal (BUILD-26 stage f) carries no step_key — the same command
 *  name routes server-side to a different accept path (cmd_insert_step + cmd_patch_step, +
 *  cmd_insert_branch_step for if_present) based on `command`/`primitive` in the payload.
 *
 *  A `structural_op` proposal (BUILD-26 stage g) round-trips as one op in a single-item `ops`
 *  batch — the server applies it (and any sibling ops accepted alongside it in the same call)
 *  as one all-or-nothing change with exactly one undo entry. This function always sends a
 *  one-element batch; a caller that wants to accept several structural proposals together should
 *  call `acceptCopilotProposalBatch` instead. */
export function acceptCopilotProposal(
  skillId: string,
  proposal: CopilotProposal,
): Promise<WorkflowRevalidationResponse> {
  if (proposal.command === 'structural_op') {
    return acceptCopilotProposalBatch(skillId, [proposal])
  }
  return cmd('accept_copilot_proposal', {
    skill_id: skillId,
    step_key: proposal.step_key ?? null,
    patch: proposal.patch,
    proposal_id: proposal.id,
    command: proposal.command,
    primitive: proposal.primitive,
    overlay_id: proposal.overlay_id,
    after_step_key: proposal.after_step_key ?? null,
    nested_step: proposal.nested_step ?? null,
  })
}

/** Accepts several `structural_op` proposals as ONE batch — one document write, one undo entry,
 *  aborting the whole batch untouched if any op in it turns out stale. Use this when the reviewer
 *  accepts a multi-step proposal (e.g. "insert an agent step, then hover the result")
 *  as a single action rather than one accept per step. */
export function acceptCopilotProposalBatch(
  skillId: string,
  proposals: CopilotProposal[],
): Promise<WorkflowRevalidationResponse> {
  return cmd('accept_copilot_proposal', {
    skill_id: skillId,
    command: 'structural_op',
    proposal_id: proposals.map((p) => p.id).join(','),
    ops: proposals.map((p) => ({
      op: p.op,
      step_key: p.step_key ?? null,
      after_step_key: p.after_step_key ?? null,
      action_kind: p.action_kind,
      fields: p.fields ?? {},
      identity_from_step_key: p.identity_from_step_key ?? null,
      inputs: p.inputs,
      find: p.find,
      replace: p.replace,
    })),
  })
}

/** Changes nothing in the compiled skill — logs the rejection so the correction dataset never
 *  keeps only the flattering half. An insert_overlay_branch rejection sends overlay_id instead of
 *  step_key — the backend logs it against "overlay:<overlay_id>" since there is no step to name. */
export function rejectCopilotProposal(skillId: string, proposal: CopilotProposal): Promise<{ ok: boolean }> {
  return cmd('reject_copilot_proposal', {
    skill_id: skillId,
    step_key: proposal.step_key ?? null,
    overlay_id: proposal.overlay_id,
    proposal_id: proposal.id,
    field: proposal.field,
    why: proposal.why,
    command: proposal.command,
  })
}

/** Archives the outgoing conversation to disk before "New session" clears it in-memory —
 *  never blocks starting a fresh conversation; a save failure is surfaced but not fatal. */
export function saveCopilotSession(skillId: string, transcript: CopilotTurnMessage[]): Promise<{ ok: boolean }> {
  return cmd('copilot_save_session', { skill_id: skillId, transcript })
}

/** BUILD-26 stage g: the archive above was write-only until now — this resumes the last saved
 *  conversation for a skill instead of always starting cold, called from copilotStore's
 *  `ensureFor` the first time a skill's panel opens in this session. Empty when there is no
 *  archive yet. */
export function loadLastCopilotSession(skillId: string): Promise<{ messages: CopilotTurnMessage[] }> {
  return cmd('copilot_load_session', { skill_id: skillId })
}

/** Applies a `compiler/loop_suggestion.py` finding — a one-click "generalize this to a loop"
 *  suggestion (deterministic, no LLM). One atomic document write server-side
 *  (`apply_for_each_loop_suggestion`), one undo entry, logged with source="for_each_suggestion"
 *  in the same edits.jsonl every other proposal decision uses. Refuses with `suggestion_stale`
 *  if the workflow changed since this was shown.
 *
 *  `removeRedundantClick` (default true) answers the SEPARATE "remove this leftover click
 *  too?" popup — only relevant, and only ever shown, when `suggestion.redundant_click_key` is
 *  set. Ignored server-side otherwise. */
export function acceptForEachSuggestion(
  skillId: string,
  suggestion: ForEachSuggestion,
  removeRedundantClick = true,
): Promise<WorkflowRevalidationResponse> {
  return cmd('accept_for_each_suggestion', {
    skill_id: skillId,
    suggestion,
    remove_redundant_click: removeRedundantClick,
  })
}

/** Changes nothing in the compiled skill — logs the dismissal so this exact suggestion is
 *  excluded from the next compile's findings (`loop_suggestion.py::filter_rejected`). */
export function rejectForEachSuggestion(skillId: string, suggestion: ForEachSuggestion): Promise<{ ok: boolean }> {
  return cmd('reject_for_each_suggestion', { skill_id: skillId, suggestion })
}

// ── Verified retest (BUILD-26 stage e) ───────────────────────────────────────────────────────
// Two-phase: call with confirmed=false first to get the irreversible-step count for a confirm
// prompt (no build, no browser); confirmed=true actually rebuilds + retests. cmd_copilot_verify
// delegates to cmd_build_skill_package / cmd_test_workflow server-side, so their own progress
// events (kind: 'skill_package_build' / 'workflow_test') arrive on the same channel alongside the
// verify command's own 'copilot_verify' phase — onLog is handed all three.

export type CopilotVerifyPreflight = {
  status: 'confirm_required'
  irreversible_count: number
  irreversible_steps: { step_key: string; description: string }[]
}

export type CopilotVerifyOutcome = {
  status: 'verified' | 'cancelled'
  verdict?: 'fixed' | 'still_failing' | 'progressed'
  run_id: string | null
  message?: string
}

export type CopilotVerifyResponse = CopilotVerifyPreflight | CopilotVerifyOutcome

export function copilotVerify(
  skillId: string,
  args: { stepKey: string; proposalId?: string; confirmed: boolean },
  onLog?: (message: string) => void,
): Promise<CopilotVerifyResponse> {
  const payload = {
    skill_id: skillId,
    step_key: args.stepKey,
    proposal_id: args.proposalId ?? null,
    confirmed: args.confirmed,
  }
  if (!onLog) return cmd('copilot_verify', payload)
  const unsub = window.conxa.onEvent((ev: BackendEvent) => {
    if (ev.phase === 'copilot_verify' && typeof ev.text === 'string') onLog(ev.text)
    else if ((ev.kind === 'workflow_test' || ev.kind === 'skill_package_build') && ev.message) {
      onLog(String(ev.message))
    }
  })
  return cmd<CopilotVerifyResponse>('copilot_verify', payload).finally(unsub)
}
