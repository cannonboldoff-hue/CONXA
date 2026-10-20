import { cmd } from '@/lib/ipc'
import type { Workflow } from '@/api/workflowsApi'
import type { WorkflowStage } from '@/components/StagePath'

export type GroupApp = {
  id: string
  name: string
  login_url: string
  success_url: string
  captured_at: number | null
  checked_at: number | null
  storage_state_path: string
  last_error: string
}

export type Group = {
  id: string
  slug: string
  name: string
  workspace_id: string
  apps: GroupApp[]
  created_at: number
  updated_at: number
}

export type GroupSummary = {
  id: string
  slug: string
  name: string
  workflow_count: number
  /** How this group's workflows are distributed across the lifecycle — keys are
   * only present for stages that have at least one workflow in them. */
  stages: Partial<Record<WorkflowStage, number>>
  /** The first few workflows in the group, so a group card can show its contents.
   * Capped server-side (WORKFLOW_PREVIEW_LIMIT); `workflow_count` holds the total. */
  workflow_preview: { id: string; name: string; stage: WorkflowStage }[]
  apps_total: number
  apps_authenticated: number
  ready: boolean
  created_at: number
  updated_at: number
}

export type GroupAppStatus = {
  id: string
  name: string
  login_url: string
  success_url: string
  state: 'ready' | 'missing' | 'expired'
  captured_at: number | null
  /** When this app's session was last actually probed (headless navigation), not just
   * captured. null means "never verified since capture" — see `verified` below. */
  checked_at: number | null
  /** True only when checked_at is set and recent — a real probe recently confirmed the
   * session, not just "a session file exists". A `ready` app can still be unverified. */
  verified: boolean
  last_error: string
  /** "" when has_auth_definition is true — a learned definition detects sign-in on its own.
   * Otherwise set when the sign-in was saved without ever reaching success_url (closed by
   * hand, or none set): the runtime's generic detection ladder then has less to go on. */
  detect_warning: string
  /** Learned at Connect's Done (P0: Application Authentication Recording) — true once this
   * app's sign-in is detected via a learned definition rather than the generic ladder. */
  has_auth_definition: boolean
}

export type GroupAuthStatus = {
  group_id: string
  apps: GroupAppStatus[]
  apps_total: number
  apps_authenticated: number
  ready: boolean
  first_missing_app_id: string | null
}

export function fetchGroups(): Promise<{ groups: GroupSummary[] }> {
  return cmd('list_groups')
}

export function fetchGroup(groupId: string): Promise<{ group: Group; auth: GroupAuthStatus; workflows: Workflow[] }> {
  return cmd('get_group', { group_id: groupId })
}

export function createGroup(name: string): Promise<{ group: Group }> {
  return cmd('create_group', { name })
}

export function renameGroup(groupId: string, name: string): Promise<{ group: Group }> {
  return cmd('rename_group', { group_id: groupId, name })
}

export function deleteGroup(groupId: string): Promise<{ deleted: boolean; reassigned_to: string }> {
  return cmd('delete_group', { group_id: groupId })
}

export function addGroupApp(groupId: string, name: string, loginUrl: string, successUrl: string): Promise<{ group: Group }> {
  return cmd('add_group_app', { group_id: groupId, name, login_url: loginUrl, success_url: successUrl })
}

export function updateGroupApp(
  groupId: string,
  appId: string,
  body: { name?: string; login_url?: string; success_url?: string },
): Promise<{ group: Group }> {
  return cmd('update_group_app', { group_id: groupId, app_id: appId, ...body })
}

export function removeGroupApp(groupId: string, appId: string): Promise<{ group: Group }> {
  return cmd('remove_group_app', { group_id: groupId, app_id: appId })
}

export function getGroupAuthStatus(groupId: string): Promise<GroupAuthStatus> {
  return cmd('get_group_auth_status', { group_id: groupId })
}

/** Screen rect (CSS px, as window.screenX/Y report them) to open the login window at. */
export type WindowRect = { x: number; y: number; width: number; height: number }

export function startGroupAppAuth(
  groupId: string,
  appId: string,
  windowRect?: WindowRect,
  /** False when the caller has no region to draw the panel's view in (a dialog): the sign-in
   *  then opens its own window. */
  inPanel = true,
  /** embedded = the sign-in is a view in Studio's browser panel, run id = session_id. */
): Promise<{ session_id: string; group_id: string; app_id: string; login_url: string; embedded?: boolean }> {
  return cmd('start_group_app_auth', { group_id: groupId, app_id: appId, in_panel: inPanel, ...(windowRect ? { window_rect: windowRect } : {}) })
}

/** P0 (Application Authentication Recording): while the login window is still open, this first
 * tries to LEARN a sign-in definition from what just happened and self-tests it — a definition
 * that fails the self-test does NOT end the session (`confirmed: false`); the window stays open
 * so the person can keep signing in and click Done again. Pass `force: true` (the "Save anyway"
 * action) to skip learning and save the session as-is, falling back to generic detection for
 * this app — same as before this feature existed. */
export function finishGroupAppAuth(
  sessionId: string,
  groupId: string,
  appId: string,
  force?: boolean,
): Promise<{ confirmed: true; group: Group; auth: GroupAuthStatus } | { confirmed: false; reason: string }> {
  return cmd('finish_group_app_auth', { session_id: sessionId, group_id: groupId, app_id: appId, force: !!force })
}

export function cancelGroupAppAuth(sessionId: string): Promise<{ ok: boolean }> {
  return cmd('cancel_group_app_auth', { session_id: sessionId })
}

/** Bounded, user-initiated probe of whether a saved session is still actually valid —
 * "Check now" on the Group page. Omit appId to check every captured app in the group. */
export function checkGroupAppAuth(groupId: string, appId?: string): Promise<{ group: Group; auth: GroupAuthStatus }> {
  return cmd('check_group_app_auth', appId ? { group_id: groupId, app_id: appId } : { group_id: groupId })
}
