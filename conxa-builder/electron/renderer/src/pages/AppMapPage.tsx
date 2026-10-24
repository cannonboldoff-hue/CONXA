import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Loader2, Map as MapIcon, TriangleAlert } from 'lucide-react'
import { getAppGraph, listAppGraphs, setAppPopupPolicy, syncAppGraphs, type AppGraph, type MapAction, type MapPopup, type MapScreen } from '@/api/appMapApi'
import { PageHeader } from '@/components/layout/PageHeader'
import { NODE_H, NODE_W, actionLabel, layoutAppGraph, type PlacedEdge } from '@/lib/appMapLayout'
import { cn } from '@/lib/utils'

type Selection = { kind: 'screen'; id: string } | { kind: 'action'; id: string } | { kind: 'popup'; id: string } | null

const PAD = 24

export function AppMapPage() {
  const qc = useQueryClient()
  const apps = useQuery({ queryKey: ['app-graphs'], queryFn: listAppGraphs, staleTime: 5_000 })
  const [chosen, setChosen] = useState<string | null>(null)

  // Share maps with teammates, quietly, the first time the page opens.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const list = await listAppGraphs().catch(() => null)
      const workspaces = [...new Set((list?.apps ?? []).map((a) => a.workspace_id).filter((w) => w && w !== 'wrk_local'))]
      const results = await Promise.all(workspaces.map((w) => syncAppGraphs(w).catch(() => null)))
      if (!cancelled && results.some((r) => r && (r.pulled || r.removed))) void qc.invalidateQueries({ queryKey: ['app-graphs'] })
    })()
    return () => { cancelled = true }
  }, [qc])

  const rows = apps.data?.apps ?? []
  const multiWorkspace = new Set(rows.map((r) => r.workspace_id)).size > 1
  const rowKey = (r: { workspace_id: string; app_key: string }) => `${r.workspace_id}::${r.app_key}`
  const active = rows.find((r) => rowKey(r) === chosen) ?? rows[0] ?? null

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="App maps" description="What Conxa has learned about each app from your recordings." />
      <div className="min-h-0 flex-1 overflow-hidden">
        {apps.isLoading ? (
          <div className="flex items-center gap-2 px-6 py-6 text-zinc-500">
            <Loader2 className="size-4 animate-spin" />
            <span className="text-sm">Loading…</span>
          </div>
        ) : apps.isError ? (
          <div className="mx-6 mt-6 flex items-start gap-2.5 rounded-lg border border-red-500/20 bg-red-500/[0.06] px-4 py-3">
            <AlertCircle className="mt-0.5 size-4 shrink-0 text-red-400" />
            <p className="text-sm text-red-300">{(apps.error as Error)?.message ?? 'Could not load the maps'}</p>
          </div>
        ) : !active ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <div className="rounded-full border border-white/8 bg-white/[0.03] p-5">
              <MapIcon className="size-9 text-zinc-700" />
            </div>
            <div>
              <p className="text-sm font-semibold text-zinc-400">No maps yet</p>
              <p className="mt-1 max-w-xs text-xs text-zinc-600">Compile a workflow and the app it ran on shows up here.</p>
            </div>
          </div>
        ) : (
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex flex-wrap gap-2 border-b border-white/8 px-4 py-2.5 sm:px-6" role="tablist" aria-label="Apps">
              {rows.map((r) => (
                <button
                  key={rowKey(r)}
                  role="tab"
                  aria-selected={rowKey(r) === rowKey(active)}
                  type="button"
                  onClick={() => setChosen(rowKey(r))}
                  className={cn(
                    'rounded-[8px] px-2.5 py-1.5 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-brand/55',
                    rowKey(r) === rowKey(active) ? 'bg-white/[0.07] text-[#e8e6e3]' : 'text-[#a3a3a3] hover:bg-white/[0.04] hover:text-[#e8e6e3]',
                  )}
                >
                  {r.app_key}
                  <span className="ml-2 text-[0.6875rem] text-[#8e8e8e]">
                    {r.workflows} workflow{r.workflows === 1 ? '' : 's'}
                    {multiWorkspace ? ` · ${r.workspace_id}` : ''}
                  </span>
                </button>
              ))}
            </div>
            <AppGraphView key={rowKey(active)} workspaceId={active.workspace_id} appKey={active.app_key} />
          </div>
        )}
      </div>
    </div>
  )
}

function AppGraphView({ workspaceId, appKey }: { workspaceId: string; appKey: string }) {
  const q = useQuery({ queryKey: ['app-graph', workspaceId, appKey], queryFn: () => getAppGraph(workspaceId, appKey), staleTime: 5_000 })
  const [selection, setSelection] = useState<Selection>(null)
  const [pathId, setPathId] = useState<string>('')

  const graph = q.data?.graph
  const layout = useMemo(() => (graph ? layoutAppGraph(graph) : null), [graph])

  if (q.isLoading) return <div className="px-6 py-6 text-sm text-zinc-500">Loading…</div>
  if (q.isError || !graph || !layout) {
    return <p className="px-6 py-6 text-sm text-red-300">{(q.error as Error)?.message ?? 'Could not load this map'}</p>
  }

  const path = graph.paths.find((p) => p.skill_id === pathId)
  const onPath = new Set(path?.action_ids ?? [])
  const dimmed = (actionId: string) => !!path && !onPath.has(actionId)
  const screenOnPath = (id: string) => !path || graph.actions.some((a) => onPath.has(a.id) && (a.from_screen === id || a.to_screen === id))

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-3 px-4 py-2 text-xs text-[#a3a3a3] sm:px-6">
          <label className="flex items-center gap-2">
            Highlight workflow
            <select
              value={pathId}
              onChange={(e) => setPathId(e.target.value)}
              className="rounded-[6px] border border-white/10 bg-[#252525] px-2 py-1 text-xs text-[#e8e6e3] outline-none focus-visible:ring-2 focus-visible:ring-brand/55"
            >
              <option value="">All workflows</option>
              {graph.paths.map((p) => (
                <option key={p.skill_id} value={p.skill_id}>{p.name || p.skill_id}</option>
              ))}
            </select>
          </label>
          <span>{graph.screens.length} screens · {graph.actions.length} actions · {graph.popups.length} popups</span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-4 pb-6 sm:px-6">
          <svg
            width={layout.width + PAD * 2}
            height={layout.height + PAD * 2}
            role="img"
            aria-label={`Screens and actions of ${appKey}`}
            className="max-w-none"
          >
            <g transform={`translate(${PAD},${PAD})`}>
              {layout.edges.map((e) => (
                <EdgeLine
                  key={e.action.id}
                  edge={e}
                  dim={dimmed(e.action.id)}
                  popups={layout.popupsByAction.get(e.action.id) ?? []}
                  selected={selection?.kind === 'action' && selection.id === e.action.id}
                  onSelect={() => setSelection({ kind: 'action', id: e.action.id })}
                />
              ))}
              {layout.nodes.map((n) => {
                const staying = layout.stayingActions.get(n.screen.id) ?? []
                const popupCount = new Set(staying.flatMap((a) => (layout.popupsByAction.get(a.id) ?? []).map((p) => p.id))).size
                const selected = selection?.kind === 'screen' && selection.id === n.screen.id
                return (
                  <g
                    key={n.screen.id}
                    transform={`translate(${n.x},${n.y})`}
                    opacity={screenOnPath(n.screen.id) ? 1 : 0.3}
                    className="cursor-pointer outline-none"
                    tabIndex={0}
                    role="button"
                    aria-label={`Screen ${n.screen.route}`}
                    onClick={() => setSelection({ kind: 'screen', id: n.screen.id })}
                    onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') setSelection({ kind: 'screen', id: n.screen.id }) }}
                  >
                    <rect width={NODE_W} height={NODE_H} rx={10} fill="#252525" stroke={selected ? '#d97757' : 'rgba(255,255,255,0.12)'} strokeWidth={selected ? 2 : 1} />
                    <text x={12} y={24} fill="#e8e6e3" fontSize={13} fontWeight={600}>{clip(n.screen.route, 24)}</text>
                    <text x={12} y={44} fill="#8e8e8e" fontSize={11}>{clip(n.screen.title_tokens.join(' ') || appKey, 30)}</text>
                    {popupCount > 0 && (
                      <g transform={`translate(${NODE_W - 12},0)`}>
                        <circle r={10} fill="#fbbf24" />
                        <text textAnchor="middle" y={4} fontSize={11} fontWeight={700} fill="#1a1a1a">{popupCount}</text>
                      </g>
                    )}
                  </g>
                )
              })}
            </g>
          </svg>
        </div>
      </div>
      <aside className="max-h-[45%] min-h-0 shrink-0 overflow-y-auto border-t border-white/8 p-4 lg:max-h-none lg:w-80 lg:border-l lg:border-t-0" aria-label="Details">
        <Details graph={graph} selection={selection} stayingActions={layout.stayingActions} popupsByAction={layout.popupsByAction} onSelect={setSelection} />
      </aside>
    </div>
  )
}

function clip(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}

function EdgeLine({ edge, dim, popups, selected, onSelect }: { edge: PlacedEdge; dim: boolean; popups: MapPopup[]; selected: boolean; onSelect: () => void }) {
  const x1 = edge.from.x + NODE_W
  const y1 = edge.from.y + NODE_H / 2
  const x2 = edge.to.x
  const y2 = edge.to.y + NODE_H / 2
  const mx = (x1 + x2) / 2
  const my = (y1 + y2) / 2
  const d = x2 > x1 ? `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}` : `M${x1},${y1} C${x1 + 60},${y1 - 50} ${x2 - 60},${y2 - 50} ${x2},${y2}`
  return (
    <g
      opacity={dim ? 0.2 : 1}
      className="cursor-pointer outline-none"
      tabIndex={0}
      role="button"
      aria-label={`Action ${actionLabel(edge.action)}`}
      onClick={onSelect}
      onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') onSelect() }}
    >
      <path d={d} fill="none" stroke="transparent" strokeWidth={14} />
      <path d={d} fill="none" stroke={selected ? '#d97757' : '#6b6b6b'} strokeWidth={selected ? 2.5 : 1.5} />
      <text x={mx} y={my - 8} textAnchor="middle" fill="#a3a3a3" fontSize={11}>{clip(actionLabel(edge.action), 22)}</text>
      {popups.length > 0 && (
        <g transform={`translate(${mx},${my + 10})`}>
          <rect x={-34} y={-9} width={68} height={18} rx={9} fill="#fbbf24" />
          <text textAnchor="middle" y={4} fontSize={10.5} fontWeight={700} fill="#1a1a1a">{popups.length} popup{popups.length === 1 ? '' : 's'}</text>
        </g>
      )}
    </g>
  )
}

function Details({
  graph, selection, stayingActions, popupsByAction, onSelect,
}: {
  graph: AppGraph
  selection: Selection
  stayingActions: Map<string, MapAction[]>
  popupsByAction: Map<string, MapPopup[]>
  onSelect: (s: Selection) => void
}) {
  const screenById = new Map(graph.screens.map((s) => [s.id, s]))
  const workflowsOf = (sources: string[]) =>
    sources.map((src) => graph.paths.find((p) => `skill:${p.skill_id}` === src)?.name || src.replace('skill:', ''))

  if (!selection) {
    return (
      <div className="space-y-2 text-sm text-[#a3a3a3]">
        <p className="font-semibold text-[#e8e6e3]">{graph.app_key}</p>
        <p>Click a screen, an arrow or a popup badge to see what Conxa knows about it.</p>
        {graph.popups.length > 0 && (
          <div className="pt-2">
            <p className="mb-1 text-[0.6875rem] uppercase tracking-wide text-[#8e8e8e]">Popups seen in this app</p>
            <ul className="space-y-1">
              {graph.popups.map((p) => (
                <li key={p.id}>
                  <button type="button" className="text-left text-[#fbbf24] hover:underline" onClick={() => onSelect({ kind: 'popup', id: p.id })}>
                    {p.title || 'Untitled popup'}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    )
  }

  if (selection.kind === 'screen') {
    const s = screenById.get(selection.id) as MapScreen | undefined
    if (!s) return null
    const staying = stayingActions.get(s.id) ?? []
    return (
      <div className="space-y-3 text-sm">
        <div>
          <p className="font-semibold text-[#e8e6e3]">{s.route}</p>
          <p className="text-xs text-[#8e8e8e]">{s.title_tokens.join(' ') || 'No title words kept'}</p>
        </div>
        <p className="text-xs text-[#a3a3a3]">Used by {workflowsOf(s.sources).join(', ') || 'no workflow'}</p>
        <div>
          <p className="mb-1 text-[0.6875rem] uppercase tracking-wide text-[#8e8e8e]">Done on this screen</p>
          {staying.length === 0 ? <p className="text-xs text-[#8e8e8e]">Nothing recorded here.</p> : (
            <ul className="space-y-1">
              {staying.map((a) => (
                <li key={a.id}>
                  <button type="button" className="text-left text-[#e8e6e3] hover:underline" onClick={() => onSelect({ kind: 'action', id: a.id })}>
                    {actionLabel(a)}
                    {(popupsByAction.get(a.id) ?? []).length > 0 && <span className="ml-1 text-[#fbbf24]">· popup</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    )
  }

  if (selection.kind === 'action') {
    const a = graph.actions.find((x) => x.id === selection.id)
    if (!a) return null
    const to = a.to_screen ? screenById.get(a.to_screen) : undefined
    return (
      <div className="space-y-3 text-sm">
        <p className="font-semibold text-[#e8e6e3]">{actionLabel(a)}</p>
        <p className="text-xs text-[#a3a3a3]">
          {screenById.get(a.from_screen)?.route ?? '?'} → {to ? to.route : a.to_screen ? 'another site' : 'stays here'}
        </p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-[#8e8e8e]">Seen</dt><dd className="text-[#e8e6e3]">{a.seen}×</dd>
          {a.consequence && (<><dt className="text-[#8e8e8e]">Effect</dt><dd className="text-[#e8e6e3]">{a.consequence.replace('_', ' ')}</dd></>)}
          {a.input_name && (<><dt className="text-[#8e8e8e]">Input</dt><dd className="text-[#e8e6e3]">{a.input_name}</dd></>)}
        </dl>
        <p className="text-xs text-[#a3a3a3]">Used by {workflowsOf(a.sources).join(', ')}</p>
        {(popupsByAction.get(a.id) ?? []).map((p) => (
          <button key={p.id} type="button" onClick={() => onSelect({ kind: 'popup', id: p.id })} className="flex w-full items-center gap-2 rounded-[8px] border border-[#fbbf24]/30 bg-[#fbbf24]/[0.06] px-3 py-2 text-left text-xs text-[#fbbf24]">
            <TriangleAlert className="size-3.5 shrink-0" />
            <span>May show “{p.title || 'a popup'}”</span>
          </button>
        ))}
      </div>
    )
  }

  const p = graph.popups.find((x) => x.id === selection.id)
  if (!p) return null
  return (
    <div className="space-y-3 text-sm">
      <p className="font-semibold text-[#fbbf24]">{p.title || 'Untitled popup'}</p>
      <p className="text-xs text-[#a3a3a3]">
        {p.appeared === 0 && p.field_seen > 0
          ? 'No workflow has recorded this popup yet. '
          : `Appeared in ${p.appeared} recording${p.appeared === 1 ? '' : 's'}; the same step ran without it in ${p.absent}. `}
        {p.field_seen > 0 ? `Seen in ${p.field_seen} real run${p.field_seen === 1 ? '' : 's'}.` : ''}
      </p>
      <div>
        <p className="mb-1 text-[0.6875rem] uppercase tracking-wide text-[#8e8e8e]">Buttons</p>
        <ul className="space-y-1 text-xs">
          {p.options.map((o) => (
            <li key={o.label} className="flex items-center justify-between gap-2 text-[#e8e6e3]">
              <span>{o.label}{o.role === 'consequential' && <span className="ml-1 text-[#f87171]">· risky</span>}</span>
              <span className="text-[#8e8e8e]">{o.chosen > 0 ? `chosen ${o.chosen}×` : 'never chosen'}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-[#a3a3a3]">Used by {workflowsOf(p.sources).join(', ')}</p>
      <PolicyEditor graph={graph} popup={p} />
    </div>
  )
}

const NO_ANSWER = ''

function policyValue(p: MapPopup): string {
  const a = p.app_policy
  if (!a) return NO_ANSWER
  return a.mode === 'auto' ? `auto:${a.option}` : a.mode
}

/** The author's standing answer for this popup in this app. Nothing is ever set automatically: with
 * no answer a person is asked each time, and a button that changes or removes data also needs the
 * author to confirm. It reaches customers with the next publish of a workflow on this app. */
function PolicyEditor({ graph, popup }: { graph: AppGraph; popup: MapPopup }) {
  const qc = useQueryClient()
  const [value, setValue] = useState(policyValue(popup))
  const [confirmed, setConfirmed] = useState(false)
  useEffect(() => { setValue(policyValue(popup)); setConfirmed(false) }, [popup.id, popup.app_policy])

  const chosenLabel = value.startsWith('auto:') ? value.slice(5) : ''
  const risky = popup.options.some((o) => o.label === chosenLabel && o.role === 'consequential')
  const save = useMutation({
    mutationFn: () => setAppPopupPolicy(
      graph.workspace_id, graph.app_key, popup.id,
      value === NO_ANSWER ? { mode: 'clear' } : chosenLabel ? { mode: 'auto', option: chosenLabel } : { mode: value as 'ask_human' | 'stop' },
      confirmed,
    ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['app-graph', graph.workspace_id, graph.app_key] })
      void qc.invalidateQueries({ queryKey: ['app-graphs'] })
    },
  })
  const unchanged = value === policyValue(popup)

  return (
    <div className="space-y-2 border-t border-white/8 pt-3">
      <label className="block text-[0.6875rem] uppercase tracking-wide text-[#8e8e8e]" htmlFor="popup-policy">What should runs do?</label>
      <select
        id="popup-policy"
        value={value}
        onChange={(e) => { setValue(e.target.value); setConfirmed(false); save.reset() }}
        className="w-full rounded-[6px] border border-white/10 bg-[#252525] px-2 py-1.5 text-xs text-[#e8e6e3] outline-none focus-visible:ring-2 focus-visible:ring-brand/55"
      >
        <option value={NO_ANSWER}>Nothing set (a person is asked)</option>
        {popup.options.map((o) => (
          <option key={o.label} value={`auto:${o.label}`}>Always press “{o.label}”{o.role === 'consequential' ? ' (risky)' : ''}</option>
        ))}
        <option value="ask_human">Always ask a person</option>
        <option value="stop">Stop the run</option>
      </select>
      {risky && (
        <label className="flex items-start gap-2 text-xs text-[#f87171]">
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} className="mt-0.5" />
          <span>“{chosenLabel}” can change or remove data. I understand it will run without asking.</span>
        </label>
      )}
      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={unchanged || (risky && !confirmed) || save.isPending}
          onClick={() => save.mutate()}
          className="rounded-[8px] bg-[#d97757] px-3 py-1.5 text-xs font-medium text-[#1a1a1a] outline-none transition-colors hover:bg-[#e08565] focus-visible:ring-2 focus-visible:ring-brand/55 disabled:opacity-40"
        >
          Save
        </button>
        {save.isSuccess && <span className="text-xs text-[#4ade80]">Saved</span>}
        {save.isError && <span className="text-xs text-[#f87171]">{(save.error as Error).message}</span>}
      </div>
      <p className="text-xs text-[#8e8e8e]">Applies to workflows on this app the next time you publish them. Where nothing is set, a person is asked.</p>
    </div>
  )
}
