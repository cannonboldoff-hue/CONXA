import { cmd } from '@/lib/ipc'
import type { BackendEvent } from '@/lib/ipc'

export type Workflow = {
  id: string
  slug: string
  name: string
  owner_user_id: string
  group_id: string
  target_url: string
  protected_url: string
  protected_url_marker_text: string
  status: 'needs_auth' | 'ready' | 'error'
  created_at: number
  updated_at: number
  // The single recording this workflow holds.
  session_id: string | null
  recorded_at: number | null
  recording_status: 'recorded' | 'compiled' | 'error' | null
  /** Every hostname the saved recording navigated to — captured at "Save Workflow
   * Now", feeds used_apps platform-tag matching (see handlers/groups.py). */
  visited_hosts?: string[]
  skill_id: string | null
  edited_at: number | null
  last_test_at: number | null
  last_test_status: 'passed' | 'failed' | 'never'
  last_test_error: string | null
  last_test_inputs: Record<string, unknown>
  signed_off: boolean
  compile_status: 'ok' | 'review_needed' | 'failed' | null
  compile_min_confidence: number | null
  compile_steps_with_warnings: number | null
  /** Group apps this workflow actually touches (start URLs + visited_hosts via the
   * same matcher as the recording gate) — rendered as platform chips. */
  used_apps?: { id: string; name: string }[]
  /** Hosts the recording visited that no group app covers (AUTH-1) — the runtime warns about the same list. */
  unclaimed_hosts?: string[]
  stage: 'recording' | 'ready_to_compile' | 'queued' | 'compiling' | 'needs_review' | 'needs_test' | 'ready' | 'error'
  step_count?: number
}

export type WorkflowsResponse = { workflows: Workflow[] }

export type SkillPackBuild = {
  last_built_at: number
  output_path: string
  version: string
}

export type SkillPackInstaller = {
  built_at: number
  installer_path: string
  filename: string
  version: string
  runtime_version: string
  release_notes?: string
}

/** The one shared package every workflow in the workspace compiles into —
 * see conxa_core.models.workflow.SkillPack. */
export type SkillPack = {
  workspace_id: string
  display_name: string
  status: 'idle' | 'building' | 'error'
  build: SkillPackBuild | null
  installer: SkillPackInstaller | null
  created_at: number
  updated_at: number
}

export type CompiledSkillFiles = {
  'execution.json': Record<string, unknown> | unknown[] | null
  'recovery.json': Record<string, unknown> | unknown[] | null
  'input.json': Record<string, unknown> | unknown[] | null
  'inputs.json'?: Record<string, unknown> | unknown[] | null
}

export type InstallerBuildResult = {
  installer_path: string
  filename: string
  company: string
  workspace_id: string
  version: string
  runtime_version: string
  release_notes?: string
  cloud_download_url?: string
  cloud_version_download_url?: string
  cloud_sha256?: string
  cloud_upload_error?: string
  cloud_upload_error_message?: string
  cloud_workspace_id?: string
  cloud_tracking_url?: string
  cloud_tracking_token_present?: boolean
  cloud_sync_endpoint?: string
  installed_runtime_path?: string
}

export type SkillPackReleaseResult = {
  slug: string
  version: string
  cloud_api?: string
  workspace_id: string
  tracking_url: string
  tracking_token_present: boolean
  sync_token_present: boolean
  sync_endpoint: string
  published_at?: number
}

// A "ready" version has been published (uploaded, immutable, versioned) but
// not yet Released/Deployed by a Cloud admin — see the publish/release split
// in docs/TRD.md. Build Studio never branches on this beyond display; Cloud
// owns Release/Deploy, rollback, deployment, and audit.
export type ReleaseStatus = 'ready' | 'pending' | 'published'

export type SkillPackVersion = {
  slug: string
  skill_slug: string
  version: string
  release_notes: string
  group_id?: string
  tests_passed?: boolean
  workspace_id: string
  owner_user_id?: string
  published_by?: { user_id: string; email: string | null; name: string | null }
  published_at: number
  files_written?: number
  file_count?: number
  size_bytes?: number
  artifact_sha256?: string
  status?: ReleaseStatus
  is_latest: boolean
}

export type SkillPackVersionsResponse = {
  slug: string
  skill_slug: string
  versions: SkillPackVersion[]
  current_stable: SkillPackVersion | null
}

// --- Release Center -------------------------------------------------------

export type ReleaseDiffSkillEntry = {
  status: 'added' | 'removed' | 'changed' | 'unchanged'
  steps_added: number
  steps_removed: number
  steps_modified: number
  recovery_changed: boolean
  metadata_changed: boolean
}

export type ReleaseDiff = {
  skills_added: string[]
  skills_removed: string[]
  steps_added: number
  steps_removed: number
  steps_modified: number
  recovery_changed_skills: string[]
  metadata_changed_skills: string[]
  summary: string
  per_skill: Record<string, ReleaseDiffSkillEntry>
}

export type ReleasePreview = {
  slug: string
  previous_version: string | null
  proposed_version: string | null
  version_valid: boolean
  version_available: boolean
  artifact_sha256: string
  artifact_unchanged: boolean
  diff: ReleaseDiff
}

export type ReleaseFile = { path: string; sha256: string; size: number }

export type ReleaseDetail = {
  slug: string
  release: SkillPackVersion
  is_stable: boolean
  files: ReleaseFile[]
  snapshot_available: boolean
}

export type ReleaseDiffResponse =
  | ({ slug: string; available: true; from_version: string | null; to_version: string } & ReleaseDiff)
  | { slug: string; available: false; reason: string; version: string; from_version?: string }

export type TrackingEvent = {
  e: string
  ts: number
  si?: number
  l?: number
  sc?: string
  fc?: string
  tier?: string
  dur?: number
  tot?: number
  rec?: number
  fsi?: number | null
}

export type TrackingRunSummary = {
  run_id: string
  workflow_id: string
  workflow_ver: string
  runtime_ver: string
  uid: string
  wid: string
  status: 'ok' | 'fail' | 'running'
  duration_ms: number
  total_steps: number
  recovered_steps: number
  failed_step_id: number | null
  failure_code: string | null
  started_at: number
  server_ts: number
}

export type TrackingRunsResponse = { runs: TrackingRunSummary[]; total: number }

export type TrackingRunDetail = {
  run_id: string
  company: string
  workflow_id: string
  workflow_ver: string
  runtime_ver: string
  uid: string
  wid: string
  timeline: TrackingEvent[]
}

export function normalizeWorkflowList(data: unknown): Workflow[] {
  if (Array.isArray(data)) return data as Workflow[]
  if (data && typeof data === 'object') {
    const workflows = (data as { workflows?: unknown }).workflows
    if (Array.isArray(workflows)) return workflows as Workflow[]
  }
  return []
}

export function fetchWorkflows(): Promise<WorkflowsResponse> {
  return cmd<WorkflowsResponse>('list_workflows')
}

export type SkillPackStatusWorkflow = {
  id: string
  name: string
  slug: string
  skill_id: string | null
  signed_off: boolean
  last_test_status: 'passed' | 'failed' | 'never'
}

/** Read-only status for the Publish / Build Installer pages — is the
 * workspace's shared skill package built yet, and what workflows are in it. */
export function fetchSkillPack(): Promise<{ skill_pack: SkillPack | null; workflows: SkillPackStatusWorkflow[] }> {
  return cmd('get_skill_pack')
}

export function fetchWorkflow(id: string): Promise<{ workflow: Workflow }> {
  return cmd<{ workflow: Workflow }>('get_workflow', { workflow_id: id })
}

export function createWorkflow(body: {
  name: string
  target_url: string
  group_id?: string
  protected_url?: string
  protected_url_marker_text?: string
  task_description?: string
}): Promise<{ workflow: Workflow }> {
  return cmd<{ workflow: Workflow }>('create_workflow', body)
}

export function deleteWorkflowEntity(id: string): Promise<{ deleted: boolean }> {
  return cmd<{ deleted: boolean }>('delete_workflow', { workflow_id: id })
}

export function getWorkflowRecordingStatus(sessionId: string): Promise<{
  session_id: string
  browser_open: boolean
  event_count: number
  ended_by_user: boolean
  binding_errors: string[]
  reached_wait_url?: boolean
  auth_captured?: boolean
  current_url?: string
}> {
  return cmd('get_recording_status', { session_id: sessionId })
}

export function startWorkflowRecord(
  workflowId: string,
  urlVariables?: Record<string, string>,
  captureHover = false,
): Promise<{
  session_id: string
  workflow_id: string
  downloads_dir?: string
}> {
  return cmd('start_recording', {
    workflow_id: workflowId,
    auth_mode: false,
    url_variables: urlVariables ?? {},
    capture_hover: captureHover,
    // This dialog stays open while recording, and a dialog hides the browser panel — so this
    // path keeps its own browser window.
    in_panel: false,
  })
}

export function finalizeWorkflow(
  workflowId: string,
  sessionId: string,
): Promise<{ status: string; session_id: string; workflow_id: string; workflow_kind: 'workflow' }> {
  return cmd<{ status: string; session_id: string; workflow_id: string; workflow_kind: 'workflow' }>(
    'stop_recording',
    { workflow_id: workflowId, session_id: sessionId },
  )
}

export function cancelRecording(
  sessionId: string,
  workflowId?: string,
): Promise<{ ok: boolean }> {
  return cmd<{ ok: boolean }>('cancel_recording', {
    session_id: sessionId,
    workflow_id: workflowId ?? '',
  })
}

/** Cancels an in-progress Run Test (EXEC-35) via the runtime's cancel_execution tool.
 * A benign no-op if the test already finished. */
export function cancelTestWorkflow(workflowId: string): Promise<{ ok: boolean }> {
  return cmd<{ ok: boolean }>('cancel_test_workflow', { workflow_id: workflowId })
}

/** Answers a "file_picker_request" event (see RecordWorkflowDialog) with the path(s) the
 * user chose in the Studio's own file-pick dialog, or `null` paths on cancel. */
export function resolveFilePicker(
  sessionId: string,
  requestId: string,
  paths: string[] | null,
): Promise<{ ok: boolean }> {
  return cmd<{ ok: boolean }>('resolve_file_picker', {
    session_id: sessionId,
    request_id: requestId,
    paths,
  })
}

/** Answers a "js_dialog_request" event (see RecordWorkflowDialog) with the human's choice on a
 * recorded native alert/confirm/prompt — `accepted=false` is only meaningful for confirm/prompt
 * (an alert only has OK). `text` is the prompt's typed answer; ignored otherwise. */
export function resolveJsDialog(
  sessionId: string,
  requestId: string,
  accepted: boolean,
  text: string,
): Promise<{ ok: boolean }> {
  return cmd<{ ok: boolean }>('resolve_js_dialog', {
    session_id: sessionId,
    request_id: requestId,
    accepted,
    text,
  })
}

export function deleteWorkflow(workflowId: string): Promise<{ deleted: boolean }> {
  return cmd<{ deleted: boolean }>('delete_workflow', { workflow_id: workflowId })
}

export function updateWorkflow(
  workflowId: string,
  body: { skill_id?: string | null; status?: string; name?: string; group_id?: string },
): Promise<{ workflow_id: string; skill_id: string | null; status: string; group_id: string; name: string }> {
  return cmd('update_workflow', { workflow_id: workflowId, ...body })
}

/** Subscribes to backend events of a given `kind` and forwards their `message`
 * to `onLog` for the duration of `fn`. Shared by buildSkillPackage/buildInstaller/
 * testWorkflow, which all stream progress lines this way. */
async function withKindLog<T>(kind: string, onLog: (message: string) => void, fn: () => Promise<T>): Promise<T> {
  const unsub = window.conxa.onEvent((ev: BackendEvent) => {
    if (ev.kind === kind && ev.message) onLog(String(ev.message))
  })
  try {
    return await fn()
  } finally {
    unsub()
  }
}

/** Like withKindLog, but also forwards the event's `stage` field (when
 * present) — used by publishSkillPack so the Release Center's publishing
 * checklist (see lib/releaseState.ts's stageChecklist) reflects real,
 * observed backend progress rather than an animated guess. */
async function withKindLogAndStage<T>(
  kind: string,
  onLog: (message: string) => void,
  onStage: (stage: string) => void,
  fn: () => Promise<T>,
): Promise<T> {
  const unsub = window.conxa.onEvent((ev: BackendEvent) => {
    if (ev.kind !== kind) return
    if (ev.message) onLog(String(ev.message))
    if (ev.stage) onStage(String(ev.stage))
  })
  try {
    return await fn()
  } finally {
    unsub()
  }
}

/** Compiles ONE signed-off workflow into the shared local skill package —
 * a sibling workflow that isn't compiled/edited/tested yet is never touched
 * or required. company_name is only required the first time this workspace
 * ever builds (nothing to derive it from otherwise). */
export function buildSkillPackage(
  skillSlug: string,
  companyName?: string,
  version = '0.1.0',
  onLog: (message: string) => void = () => {},
): Promise<SkillPackBuild> {
  return withKindLog('skill_package_build', onLog, () =>
    cmd<SkillPackBuild>('build_skill_package', { skill_slug: skillSlug, company_name: companyName, version }),
  )
}

export function buildInstaller(
  onLog: (message: string) => void = () => {},
  logoPath?: string | null,
  version?: string,
  releaseNotes?: string,
  installerName?: string,
): Promise<InstallerBuildResult> {
  return withKindLog('installer_build', onLog, () =>
    cmd<InstallerBuildResult>('build_installer', {
      logo_path: logoPath ?? null,
      version,
      release_notes: releaseNotes,
      installer_name: installerName,
    }),
  )
}

/** The primary, mandatory release-management action — publishes ONE skill's
 * release (version + release notes + that skill's own built files) to Conxa
 * Cloud. A sibling skill's test status, files, and version history are never
 * touched. Build Installer (above) is a secondary, advanced action that
 * requires a release to already exist. */
export function publishSkillPack(
  skillSlug: string,
  version: string,
  releaseNotes: string,
  onLog: (message: string) => void = () => {},
  onStage: (stage: string) => void = () => {},
): Promise<SkillPackReleaseResult> {
  return withKindLogAndStage('skill_pack_publish', onLog, onStage, () =>
    cmd<SkillPackReleaseResult>('publish_skill_pack', {
      skill_slug: skillSlug,
      version,
      release_notes: releaseNotes,
    }),
  )
}

export function fetchSkillPackVersions(skillSlug: string): Promise<SkillPackVersionsResponse> {
  return cmd<SkillPackVersionsResponse>('list_skill_pack_versions', { skill_slug: skillSlug })
}

/** Dry-run against the exact bytes a Publish would upload — computed cloud-side
 * by the same code path publish itself uses, so nothing shown here can drift
 * from what actually gets recorded on Publish. Scoped to one skill. */
export function previewRelease(skillSlug: string, version: string): Promise<ReleasePreview> {
  return cmd<ReleasePreview>('release_preview', { skill_slug: skillSlug, version })
}

export function fetchReleaseDetail(skillSlug: string, version: string): Promise<ReleaseDetail> {
  return cmd<ReleaseDetail>('release_detail', { skill_slug: skillSlug, version })
}

export function fetchReleaseDiff(skillSlug: string, version: string): Promise<ReleaseDiffResponse> {
  return cmd<ReleaseDiffResponse>('release_diff', { skill_slug: skillSlug, version })
}

export function getCompiledSkill(
  skillSlug: string,
): Promise<{ workspace_id: string; skill_slug: string; files: CompiledSkillFiles }> {
  return cmd('get_compiled_skill', { skill_slug: skillSlug })
}

// BUILD-26 stage (a4): replaces the former fetchRuns/fetchRun — those read data/runs/*.jsonl,
// a file nothing in the codebase ever wrote. cmd_get_failure_evidence reads the real thing: the
// evidence bundle assembled from the runtime's failure capture + the compiled skill document.
export function fetchFailureEvidence(
  skillId: string,
  runId?: string,
): Promise<{ evidence: Record<string, unknown> }> {
  return cmd('get_failure_evidence', { skill_id: skillId, run_id: runId })
}

export function testWorkflow(
  workflowId: string,
  inputs: Record<string, unknown> = {},
  headless = false,
  onLog: (message: string) => void = () => {},
): Promise<{ status: 'passed' | 'cancelled'; message: string }> {
  return withKindLog('workflow_test', onLog, () =>
    cmd('test_workflow', {
      workflow_id: workflowId,
      inputs,
      headless,
    }),
  )
}
