import { cmd } from '@/lib/ipc'

// Mirrors conxa_core/models/app_graph.py. The map holds the shape of an app only (docs/TRD.md §11.5).

export type AppSummary = {
  app_key: string
  workspace_id: string
  workflows: number
  screens: number
  actions: number
  popups: number
  built_at: number
}

export type MapScreen = { id: string; route: string; title_tokens: string[]; seen: number; sources: string[] }

export type MapAction = {
  id: string
  from_screen: string
  to_screen: string | null
  kind: string
  role: string
  name: string | null
  intent: string
  consequence: string
  input_name: string | null
  seen: number
  sources: string[]
}

export type MapPopupOption = { label: string; role: 'dismiss' | 'consequential' | 'neutral' | string; chosen: number }

/** The author's standing answer for a popup (set in the App maps page, shipped with the next publish). */
export type AppPopupPolicy =
  | { mode: 'auto'; option: string; author_approved: boolean }
  | { mode: 'ask_human' }
  | { mode: 'stop'; message?: string }

export type PolicyRequest =
  | { mode: 'auto'; option: string }
  | { mode: 'ask_human' }
  | { mode: 'stop' }
  | { mode: 'clear' }

export type MapPopup = {
  id: string
  title: string
  button_labels: string[]
  popup_class: string
  after_actions: string[]
  appeared: number
  absent: number
  options: MapPopupOption[]
  field_seen: number
  app_policy: AppPopupPolicy | null
  sources: string[]
}

export type MapPath = { skill_id: string; name: string; action_ids: string[] }

export type AppGraph = {
  app_key: string
  workspace_id: string
  built_at: number
  screens: MapScreen[]
  actions: MapAction[]
  popups: MapPopup[]
  paths: MapPath[]
}

export function listAppGraphs(): Promise<{ apps: AppSummary[] }> {
  return cmd<{ apps: AppSummary[] }>('list_app_graphs')
}

export function getAppGraph(workspace_id: string, app_key: string): Promise<{ graph: AppGraph }> {
  return cmd<{ graph: AppGraph }>('get_app_graph', { workspace_id, app_key })
}

/** Set the author's standing answer for one popup. A risky button also needs `confirmRisky`. */
export function setAppPopupPolicy(workspace_id: string, app_key: string, popup_id: string, policy: PolicyRequest, confirmRisky = false) {
  return cmd<{ ok: boolean; app_policy: AppPopupPolicy | null }>('set_app_popup_policy', {
    workspace_id, app_key, popup_id, policy, confirm_risky: confirmRisky,
  })
}

/** Share maps with teammates through Conxa Cloud. Best effort: callers ignore failures. */
export function syncAppGraphs(workspace_id: string) {
  return cmd<{ pushed: number; deleted: number; pulled: number; removed: number; errors: string[] }>('sync_app_graphs', { workspace_id })
}
