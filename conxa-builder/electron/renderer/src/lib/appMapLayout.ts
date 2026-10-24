import type { AppGraph, MapAction, MapPopup, MapScreen } from '@/api/appMapApi'

export const NODE_W = 200
export const NODE_H = 64
const COL_GAP = 110
const ROW_GAP = 34

export type PlacedScreen = { screen: MapScreen; x: number; y: number }

/** An edge between two different screens; the actions that stay on one screen live in the node's panel. */
export type PlacedEdge = { action: MapAction; from: PlacedScreen; to: PlacedScreen }

export type MapLayout = {
  nodes: PlacedScreen[]
  edges: PlacedEdge[]
  width: number
  height: number
  /** Actions that start and end on the same screen (or leave the app), by screen id. */
  stayingActions: Map<string, MapAction[]>
  /** Popups that were seen after each action. */
  popupsByAction: Map<string, MapPopup[]>
}

/** Columns by breadth-first distance from the entry screens (those no other screen leads to). */
export function layoutAppGraph(graph: AppGraph): MapLayout {
  const ids = new Set(graph.screens.map((s) => s.id))
  const crossing = graph.actions.filter((a) => a.to_screen && a.to_screen !== a.from_screen && ids.has(a.to_screen))
  const hasIncoming = new Set(crossing.map((a) => a.to_screen as string))
  const entries = graph.screens.filter((s) => !hasIncoming.has(s.id))
  const queue = (entries.length ? entries : graph.screens.slice(0, 1)).map((s) => s.id)

  const depth = new Map<string, number>(queue.map((id) => [id, 0]))
  for (let i = 0; i < queue.length; i += 1) {
    const id = queue[i]
    for (const a of crossing) {
      if (a.from_screen === id && !depth.has(a.to_screen as string)) {
        depth.set(a.to_screen as string, (depth.get(id) ?? 0) + 1)
        queue.push(a.to_screen as string)
      }
    }
  }
  // Screens only reachable through a cycle (or unreachable) go in a last column rather than vanishing.
  const last = Math.max(0, ...depth.values()) + (depth.size < graph.screens.length ? 1 : 0)
  for (const s of graph.screens) if (!depth.has(s.id)) depth.set(s.id, last)

  const rows = new Map<number, number>()
  const nodes: PlacedScreen[] = graph.screens.map((screen) => {
    const col = depth.get(screen.id) ?? 0
    const row = rows.get(col) ?? 0
    rows.set(col, row + 1)
    return { screen, x: col * (NODE_W + COL_GAP), y: row * (NODE_H + ROW_GAP) }
  })
  const byId = new Map(nodes.map((n) => [n.screen.id, n]))

  const edges: PlacedEdge[] = []
  for (const action of crossing) {
    const from = byId.get(action.from_screen)
    const to = byId.get(action.to_screen as string)
    if (from && to) edges.push({ action, from, to })
  }

  const stayingActions = new Map<string, MapAction[]>()
  for (const a of graph.actions) {
    if (a.to_screen && a.to_screen !== a.from_screen && ids.has(a.to_screen)) continue
    stayingActions.set(a.from_screen, [...(stayingActions.get(a.from_screen) ?? []), a])
  }

  const popupsByAction = new Map<string, MapPopup[]>()
  for (const p of graph.popups) {
    for (const aid of p.after_actions) popupsByAction.set(aid, [...(popupsByAction.get(aid) ?? []), p])
  }

  const cols = Math.max(0, ...nodes.map((n) => Math.round(n.x / (NODE_W + COL_GAP)))) + 1
  const tallest = Math.max(1, ...rows.values())
  return {
    nodes,
    edges,
    width: cols * (NODE_W + COL_GAP) - COL_GAP,
    height: tallest * (NODE_H + ROW_GAP) - ROW_GAP,
    stayingActions,
    popupsByAction,
  }
}

export function actionLabel(a: MapAction): string {
  return a.name ? `${a.kind} “${a.name}”` : a.kind
}
