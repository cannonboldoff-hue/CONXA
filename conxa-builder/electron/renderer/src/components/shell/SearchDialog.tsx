import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { useShellData } from '@/hooks/useShellData'
import { useShellStore } from '@/store/shellStore'
import { cn } from '@/lib/utils'
import type { StatusKind } from '@/lib/status'

type Hit = { key: string; label: string; hint: string; to: string; kind: StatusKind }

/** Ctrl K — search workflows and groups. Enter opens; Esc closes. */
export function SearchDialog() {
  const open = useShellStore((s) => s.searchOpen)
  const setOpen = useShellStore((s) => s.setSearchOpen)
  const { groups } = useShellData()
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [cursor, setCursor] = useState(0)

  const hits: Hit[] = useMemo(() => {
    const all: Hit[] = []
    for (const g of groups) {
      all.push({ key: `g:${g.group.id}`, label: g.group.name, hint: 'Group', to: `/groups/${g.group.id}`, kind: g.kind })
      for (const w of g.workflows) {
        all.push({ key: `w:${w.workflow.id}`, label: w.workflow.name, hint: g.group.name, to: `/w/${w.workflow.id}`, kind: w.kind })
      }
    }
    const needle = q.trim().toLowerCase()
    return needle ? all.filter((h) => `${h.label} ${h.hint}`.toLowerCase().includes(needle)) : all
  }, [groups, q])

  useEffect(() => setCursor(0), [q, open])
  useEffect(() => {
    if (!open) setQ('')
  }, [open])

  const go = (h: Hit | undefined) => {
    if (!h) return
    setOpen(false)
    navigate(h.to)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="top-[20%] max-w-lg translate-y-0 gap-0 border-white/10 bg-[#252525] p-0 text-[#e8e6e3]">
        <DialogTitle className="sr-only">Search workflows and groups</DialogTitle>
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search workflows and groups"
          aria-label="Search workflows and groups"
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, hits.length - 1)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)) }
            else if (e.key === 'Enter') { e.preventDefault(); go(hits[cursor]) }
          }}
          className="w-full rounded-t-[14px] border-b border-[var(--border-default)] bg-transparent px-4 py-3 text-sm outline-none placeholder:text-[#8e8e8e]"
        />
        <ul role="listbox" className="max-h-72 overflow-y-auto p-1.5">
          {hits.length === 0 && <li className="px-3 py-6 text-center text-sm text-[#8e8e8e]">Nothing matches “{q}”.</li>}
          {hits.map((h, i) => (
            <li key={h.key} role="option" aria-selected={i === cursor}>
              <button
                type="button"
                onMouseEnter={() => setCursor(i)}
                onClick={() => go(h)}
                className={cn('flex w-full items-center gap-2.5 rounded-[8px] px-3 py-2 text-left text-sm', i === cursor ? 'bg-white/[0.07]' : 'hover:bg-white/[0.04]')}
              >
                <StatusGlyph kind={h.kind} />
                <span className="min-w-0 flex-1 truncate">{h.label}</span>
                <span className="shrink-0 text-xs text-[#8e8e8e]">{h.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  )
}
