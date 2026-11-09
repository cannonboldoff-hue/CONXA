import { apiFetch, json } from '@/lib/apiBase'

// ─────────────────────────────────────────────────
// SkillPack — the workspace-shared, published package (was "Plugin")
// ─────────────────────────────────────────────────

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

export type SkillPack = {
  workspace_id: string
  display_name: string
  status: 'idle' | 'building' | 'error'
  build: SkillPackBuild | null
  installer: SkillPackInstaller | null
  created_at: number
  updated_at: number
}

export type SkillPackResponse = { skill_pack: SkillPack }

export function fetchSkillPack(): Promise<SkillPackResponse> {
  return apiFetch('/workflows/skill-packs/current').then((r) => json<SkillPackResponse>(r))
}

// ─────────────────────────────────────────────────
// Runs / tracker
// ─────────────────────────────────────────────────

export type RunEvent = {
  event: 'step_failure' | 'recovery_attempt' | 'run_outcome'
  run_id: string
  workflow_id: string
  skill_slug: string
  step_id: string | null
  data: Record<string, unknown>
  ts: string
}

export type RunOutcome = {
  status: 'success' | 'failure' | 'aborted'
  duration_ms: number
  total_steps: number
  recovered_steps: number
  failed_step_id: string | null
}

export type Run = {
  run_id: string
  workflow_id: string
  skill_slug: string
  events: RunEvent[]
  outcome: RunOutcome | null
}

export type RunsResponse = { runs: Run[] }

// ─────────────────────────────────────────────────────────────────────────────
// Company-scoped tracking (lightweight telemetry)
// ─────────────────────────────────────────────────────────────────────────────

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

export type TrackingRunDetail = {
  run_id: string
  company: string
  workflow_id: string
  workflow_ver: string
  runtime_ver: string
  uid: string
  wid: string
  timeline: TrackingEvent[]
  summary: TrackingRunSummary
  /** Per-step outcome, classified server-side so it matches the dashboard aggregates. */
  steps: TrackingRunStep[]
}

export type TrackingRunStep = {
  index: number
  label: string
  status: 'ok' | 'recovered' | 'failed' | 'not_reached'
  tiers: string[]
  assertionsPassed: number
  assertionsFailed: number
}

export type TrackingDashboardRange = '24h' | '7d' | '30d' | '90d'
export type TrackingGranularity = 'hour' | 'day'
export type TrackingRecoveryType = 'Selector' | 'Text Anchor' | 'Text Variant' | 'Vision'
export type TrackingRecoveryTier = 'Tier A' | 'Tier B' | 'Unknown'

export type TrackingDashboardResponse = {
  range: TrackingDashboardRange
  metrics: {
    total_installs: number
    active_users: number
    active_companies: number
    total_executions: number
    executions_last_24h: number
    success_rate: number
    failed_executions: number
    recovery_rate: number
    average_execution_time: number
  }
  recovery_type_usage: Array<{ type: TrackingRecoveryType; count: number }>
  recovery_usage_by_step: Array<{
    company: string
    workflow: string
    step_index: number | null
    step_label: string
    recovery_type: TrackingRecoveryType
    tier: TrackingRecoveryTier
    count: number
    last_seen: number
  }>
  recovery_usage_by_workflow: Array<{
    company: string
    workflow: string
    count: number
    last_seen: number
    steps: Array<{
      step_index: number | null
      step_label: string
      total_count: number
      last_seen: number
      tier_counts: Array<{
        tier: TrackingRecoveryTier
        recovery_type: TrackingRecoveryType
        count: number
      }>
    }>
  }>
  most_failed_workflows: Array<{
    workflow: string
    failed_executions: number
    last_failure_code: string
    last_seen: number
  }>
  most_failed_steps: Array<{
    workflow: string
    step_index: number | null
    step_label: string
    failed_executions: number
    last_failure_code: string
    last_seen: number
  }>
  execution_trend: Array<{
    date: string
    executions: number
    successful: number
    failed: number
    recovered: number
  }>
  assertion_health_by_step: TrackingAssertionRow[]
  /** Steps whose proof keeps coming back unconfirmed (Proof of Done). Absent from an older backend. */
  proof_health_by_step?: TrackingProofRow[]

  // ── Operations analytics (composed by app.services.tracking_analytics) ──
  granularity: TrackingGranularity
  generated_at: number
  series: TrackingSeriesBucket[]
  kpis: TrackingKpi[]
  health: TrackingHealth
  workflows: TrackingWorkflowRow[]
  recovery_cascade: TrackingCascade
  reliability_heatmap: TrackingHeatmap
  failure_codes: TrackingFailureCode[]
  roi: TrackingRoi
  insights: TrackingInsight[]
  stale_runtimes: number
}

export type TrackingProofRow = {
  company: string
  workflow: string
  step_index: number | null
  step_label: string
  total: number
  proven: number
  /** Runs where the step ran but nothing confirmed it finished. */
  unproven: number
  failed: number
  /** Runs where the work was still going when the call ran out of time. */
  waiting: number
  /** Share of runs that were unconfirmed or failed, in percent. */
  unproven_rate: number
  /** Same step's rate in the previous period; 0 when it had no problem then. */
  previous_unproven_rate?: number
  last_seen: number
}

export type TrackingAssertionRow = {
  company: string
  workflow: string
  step_index: number | null
  step_label: string
  total: number
  passed: number
  pass_rate: number
  /** Same step's pass rate in the previous period; null when it didn't report then. */
  previous_pass_rate?: number | null
  advisory_failures: number
  last_seen: number
}

export type TrackingSeriesBucket = {
  bucket: string
  at: number
  executions: number
  successful: number
  failed: number
  recovered: number
  /** null when nothing completed in the bucket — distinct from a genuine 0%. */
  success_rate: number | null
  avg_duration: number
}

export type TrackingKpi = {
  key: string
  label: string
  unit: 'count' | 'percent' | 'duration'
  /** Which way is good, so a rising failure count is never painted as an improvement. */
  direction: 'up_good' | 'down_good'
  value: number
  previous: number
  delta: number
  /** null when the previous period had no data to compare against. */
  delta_pct: number | null
  series: number[]
}

export type TrackingHealthFactor = {
  key: string
  label: string
  weight: number
  value: number
  contribution: number
  detail: string
}

export type TrackingHealth = {
  /** null means "no telemetry", never "scored zero". */
  score: number | null
  grade: 'Excellent' | 'Healthy' | 'Degraded' | 'Critical' | 'No telemetry'
  factors: TrackingHealthFactor[]
  summary: string
}

export type TrackingWorkflowVersion = {
  version: string
  runs: number
  success_rate: number
  recovery_rate: number
  last_seen: number
  /** First run of this version seen inside the window — not its publish time. */
  first_seen: number
}

export type TrackingWorkflowRow = {
  company: string
  workflow: string
  runs: number
  successful: number
  failed: number
  success_rate: number
  previous_success_rate: number | null
  success_rate_delta: number | null
  recovery_rate: number
  unattended_rate: number
  p50_duration: number
  p95_duration: number
  last_seen: number
  versions: TrackingWorkflowVersion[]
}

export type TrackingCascade = {
  nodes: Array<{ name: string }>
  links: Array<{ source: number; target: number; value: number }>
  entered_recovery: number
  healed: number
  failed: number
  heal_rate: number
  resolved_directly: number
  tier_touch: Array<{ tier: string; steps: number }>
  /** Steps that healed without ever reaching a paid tier — a count of steps, not tier hits. */
  zero_token_heals: number
  /** Steps whose recovery reached Tier B, healed or not. */
  agent_assisted: number
}

export type TrackingHeatmap = {
  cells: Array<{
    weekday: number
    hour: number
    runs: number
    successful: number
    failed: number
    success_rate: number | null
  }>
  max_runs: number
}

export type TrackingFailureCode = {
  code: string
  count: number
  last_seen: number
  workflow_count: number
}

export type RoiAssumptions = {
  default_minutes: number
  hourly_rate: number
  currency: string
  per_workflow: Record<string, number>
  updated_at?: number
  updated_by?: string
}

export type TrackingRoi = {
  assumptions: RoiAssumptions
  /** Derived from an admin-supplied baseline — always labelled as an estimate in the UI. */
  estimated: {
    hours_saved: number
    value_amount: number
    currency: string
    by_workflow: Array<{
      company: string
      workflow: string
      runs: number
      minutes_per_run: number
      is_estimate_default: boolean
      minutes_saved: number
      hours_saved: number
      /** Measured — average automated execution time for this workflow, not an assumption. */
      automated_minutes: number
    }>
  }
  /** Pure telemetry — no assumption anywhere in these numbers. */
  measured: {
    unattended_completions: number
    self_healed_runs: number
    zero_token_recoveries: number
    agent_assisted_recoveries: number
  }
}

export type TrackingInsight = {
  id: string
  severity: 'critical' | 'warning' | 'info'
  title: string
  body: string
  metric: string
  evidence: string
}

export type TrackingActivityRow = {
  run_id: string
  company: string
  workflow: string
  version: string
  runtime_version: string
  status: 'ok' | 'fail' | 'running'
  duration_ms: number
  total_steps: number
  recovered_steps: number
  failed_step_id: number | null
  failure_code: string | null
  recovery_tiers: string[]
  at: number
}

export type TrackingStepRow = {
  step_index: number | null
  step_label: string
  attempts: number
  failures: number
  recoveries: number
  success_rate: number
  recovery_rate: number
  tier_counts: Array<{ tier: string; count: number }>
  assertion_pass_rate: number | null
  assertions_total: number
  advisory_failures: number
  dominant_failure_code: string
  last_seen: number
}

export type TrackingWorkflowDetail = {
  company: string
  workflow: string
  range: TrackingDashboardRange
  granularity: TrackingGranularity
  generated_at: number
  summary: TrackingWorkflowRow | null
  series: TrackingSeriesBucket[]
  steps: TrackingStepRow[]
  recovery_cascade: TrackingCascade
  failure_codes: TrackingFailureCode[]
  assertion_health: TrackingAssertionRow[]
  recent_runs: TrackingActivityRow[]
}

export type TrackingDriftEntry = {
  workflow_id: string
  workflow_ver: string
  step_id: number | null
  occurrences: number
  run_count: number
  total_runs: number
  occurrence_rate_pct: number
  dominant_tier: string
  dominant_method: string
  tiers: Record<string, number>
  methods: Record<string, number>
  stable_hash: string
  app_version_fingerprint: string
  last_seen: number
  status: string
}

export type TrackingPreExecDriftEntry = {
  workflow_id: string
  workflow_ver: string
  occurrences: number
  max_drift_ratio: number
  missing_intents: Record<string, number>
  last_seen: number
  status: string
}

export type TrackingDriftResponse = {
  queue: TrackingDriftEntry[]
  total: number
  pre_exec: TrackingPreExecDriftEntry[]
  pre_exec_total: number
}

export function fetchTrackingDrift(): Promise<TrackingDriftResponse> {
  return apiFetch('/tracking/drift').then((r) => json<TrackingDriftResponse>(r))
}

export function fetchTrackingWorkflow(
  company: string,
  slug: string,
  range: TrackingDashboardRange,
): Promise<TrackingWorkflowDetail> {
  const path = `/tracking/workflows/${encodeURIComponent(company)}/${encodeURIComponent(slug)}`
  return apiFetch(`${path}?range=${encodeURIComponent(range)}`).then((r) => json<TrackingWorkflowDetail>(r))
}

export function fetchRoiAssumptions(): Promise<RoiAssumptions> {
  return apiFetch('/tracking/roi-assumptions').then((r) => json<RoiAssumptions>(r))
}

/** Admin-only on the server; the UI hides the control for everyone else. */
export function saveRoiAssumptions(payload: Partial<RoiAssumptions>): Promise<RoiAssumptions> {
  return apiFetch('/tracking/roi-assumptions', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((r) => json<RoiAssumptions>(r))
}

export function fetchTrackingDashboard(range: TrackingDashboardRange): Promise<TrackingDashboardResponse> {
  return apiFetch(`/tracking/dashboard?range=${encodeURIComponent(range)}`).then((r) => json<TrackingDashboardResponse>(r))
}

export function fetchTrackingRun(company: string, runId: string): Promise<TrackingRunDetail> {
  return apiFetch(
    `/tracking/${encodeURIComponent(company)}/runs/${encodeURIComponent(runId)}`,
  ).then((r) => json<TrackingRunDetail>(r))
}

// ─────────────────────────────────────────────────────────────────────────────
// Installer build + download
// ─────────────────────────────────────────────────────────────────────────────

export type InstallerVersion = {
  slug: string
  version: string
  release_notes: string
  filename: string
  sha256: string
  size: number
  uploaded_at: number
  workspace_id: string
  is_latest: boolean
  workflow_count?: number
  download_url: string
}

export function fetchInstallerVersions(): Promise<{ versions: InstallerVersion[] }> {
  return apiFetch(`/workflows/installer/versions`).then((r) =>
    json<{ versions: InstallerVersion[] }>(r),
  )
}

export type StudioManifest = {
  version: string
  win_url: string
  win_sha256: string
}

export function getStudioManifest(): Promise<StudioManifest> {
  return apiFetch('/updates/studio-manifest').then((r) => json<StudioManifest>(r))
}

// ── Release Center — Cloud is the release/deployment control plane ─────────
// Build Studio only ever publishes an immutable "ready" version here (see
// publishSkillPack in Build Studio's own workflowsApi.ts). Reviewing a
// version, releasing/deploying it, rolling it back, and reading deployment/
// audit state are all Cloud-only actions — see docs/App-Flow.md.
//
// `{installer_version}` in these paths is a Conxa-owned platform-generation
// tag ("v1"/"v2") the backend validates but does not yet branch behavior on
// (see app/api/skillpack_update_routes.py) — "v2" (current) is safe to use
// unconditionally here rather than adding a second round trip to resolve it.
const RELEASE_GENERATION = 'v2'

// A "ready" version has been published (uploaded, immutable, versioned) but
// not yet Released/Deployed by a Cloud admin — see the publish/release split
// in docs/TRD.md. "pending" is legacy: a row left behind by a publish attempt
// that crashed before even reaching "ready".
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

export function fetchSkillPackVersions(skillSlug: string): Promise<SkillPackVersionsResponse> {
  return apiFetch(
    `/workflows/${RELEASE_GENERATION}/skill-packs/versions?skill_slug=${encodeURIComponent(skillSlug)}`,
  ).then((r) => json<SkillPackVersionsResponse>(r))
}

// --- Release Center (Cloud is the authoritative owner — see docs/App-Flow.md) ---

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

export type ReleaseDiffResponse =
  | ({ slug: string; available: true; from_version: string | null; to_version: string } & ReleaseDiff)
  | { slug: string; available: false; reason: string; version: string; from_version?: string }

export function fetchReleaseDiff(skillSlug: string, version: string): Promise<ReleaseDiffResponse> {
  return apiFetch(
    `/workflows/${RELEASE_GENERATION}/releases/${encodeURIComponent(version)}/diff?skill_slug=${encodeURIComponent(skillSlug)}`,
  ).then((r) => json<ReleaseDiffResponse>(r))
}

export type ReleaseResult = { slug: string; skill_slug: string; released: string; previous_stable: string | null }

/** The Release/Deploy action — Cloud-only, the only place a "ready" version
 * ever becomes the live stable release. Requires the target version's status
 * to already be "ready" server-side (require_admin-gated, same as rollback). */
export function releaseVersion(skillSlug: string, version: string): Promise<ReleaseResult> {
  return apiFetch(
    `/workflows/${RELEASE_GENERATION}/releases/${encodeURIComponent(version)}/release?skill_slug=${encodeURIComponent(skillSlug)}`,
    { method: 'POST' },
  ).then((r) => json<ReleaseResult>(r))
}

export type RollbackResult = {
  slug: string
  skill_slug: string
  rolled_back_to: string
  previous_stable: string | null
}

export function rollbackRelease(skillSlug: string, version: string): Promise<RollbackResult> {
  return apiFetch(
    `/workflows/${RELEASE_GENERATION}/releases/${encodeURIComponent(version)}/rollback?skill_slug=${encodeURIComponent(skillSlug)}`,
    { method: 'POST' },
  ).then((r) => json<RollbackResult>(r))
}

export type DeploymentStatus = 'up_to_date' | 'pending' | 'failed' | 'offline' | 'unknown'

export type DeploymentMachine = {
  machine_id: string
  platform: string
  runtime_version: string
  installed_skill_versions: string | null
  desired_skill_version: string | null
  status: DeploymentStatus
  sync_error: { code: string; at: string } | null
  last_seen: number
  last_sync: number
}

export type DeploymentsResponse = {
  slug: string
  skill_slug: string
  desired_version: string | null
  machines: DeploymentMachine[]
  summary: { total: number; up_to_date: number; pending: number; failed: number; offline: number; unknown: number }
}

export function fetchDeployments(skillSlug: string): Promise<DeploymentsResponse> {
  return apiFetch(
    `/workflows/${RELEASE_GENERATION}/deployments?skill_slug=${encodeURIComponent(skillSlug)}`,
  ).then((r) => json<DeploymentsResponse>(r))
}

export type ReleaseEvent = {
  id: string
  workspace_id: string
  user_id: string
  action: string
  resource_type: string
  resource_id: string
  metadata: Record<string, unknown>
  created_at: number
}

export type ReleaseEventsResponse = { slug: string; skill_slug: string; events: ReleaseEvent[] }

export function fetchReleaseEvents(skillSlug: string): Promise<ReleaseEventsResponse> {
  return apiFetch(
    `/workflows/${RELEASE_GENERATION}/releases/events?skill_slug=${encodeURIComponent(skillSlug)}`,
  ).then((r) => json<ReleaseEventsResponse>(r))
}

export type GroupWorkflowSummary = {
  skill_slug: string
  workflow_name: string
  current_stable_version: string | null
  has_ready_version: boolean
  ready_version: string | null
  archived: boolean
}

export type Group = { group_id: string; group_name: string; workflows: GroupWorkflowSummary[] }

export type GroupsResponse = { slug: string; groups: Group[] }

export function fetchGroups(): Promise<GroupsResponse> {
  return apiFetch(`/workflows/${RELEASE_GENERATION}/groups`).then((r) =>
    json<GroupsResponse>(r),
  )
}

export type ArchiveResult = { slug: string; skill_slug: string; archived: boolean; restored?: boolean }

/** Pull a workflow out of every installed runtime's next sync — its files and
 * version history stay put, so restoring it is a single unarchiveSkill call. */
export function archiveSkill(skillSlug: string): Promise<ArchiveResult> {
  return apiFetch(
    `/workflows/${RELEASE_GENERATION}/skills/archive?skill_slug=${encodeURIComponent(skillSlug)}`,
    { method: 'POST' },
  ).then((r) => json<ArchiveResult>(r))
}

export function unarchiveSkill(skillSlug: string): Promise<ArchiveResult> {
  return apiFetch(
    `/workflows/${RELEASE_GENERATION}/skills/unarchive?skill_slug=${encodeURIComponent(skillSlug)}`,
    { method: 'POST' },
  ).then((r) => json<ArchiveResult>(r))
}
