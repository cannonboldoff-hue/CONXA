import { useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Hammer,
  MessageSquare,
  PackageCheck,
  PanelLeftClose,
  PanelLeftOpen,
  PencilLine,
  Plus,
  Search,
  Settings,
  UploadCloud,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'
import { createGroup } from '@/api/groupsApi'
import { chatList } from '@/api/conversationApi'
import { useAuth } from '@/contexts/AuthContext'
import { useShellData, type ShellGroup } from '@/hooks/useShellData'
import { useShellStore } from '@/store/shellStore'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { ConnectionLine } from '@/components/shell/ConnectionStates'
import { useConnectionStore } from '@/store/connectionStore'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const logoUrl = new URL('../../assets/conxa-icon.png', import.meta.url).href

const ROW = 'flex items-center gap-2 rounded-[8px] px-2.5 py-1.5 text-sm outline-none transition-colors'
const REST = 'text-[#a3a3a3] hover:bg-white/[0.04] hover:text-[#e8e6e3]'
const SELECTED = 'bg-white/[0.07] text-[#e8e6e3]'
const FOCUS = 'focus-visible:ring-2 focus-visible:ring-brand/55 focus-visible:ring-offset-0'

function Kbd({ children }: { children: string }) {
  return <span className="ml-auto text-[0.6875rem] font-medium text-[#8e8e8e]">{children}</span>
}

function NavItem({ to, icon: Icon, label, rail, trailing }: {
  to: string
  icon: typeof MessageSquare
  label: string
  rail: boolean
  trailing?: React.ReactNode
}) {
  const { pathname } = useLocation()
  const active = pathname === to || pathname.startsWith(`${to}/`)
  return (
    <NavLink
      to={to}
      title={rail ? label : undefined}
      className={cn(ROW, FOCUS, active ? SELECTED : REST, rail && 'justify-center px-0')}
    >
      <Icon className="size-4 shrink-0" />
      {!rail && <span className="truncate">{label}</span>}
      {!rail && trailing}
    </NavLink>
  )
}

function GroupBlock({ g, rail }: { g: ShellGroup; rail: boolean }) {
  const { pathname } = useLocation()
  const [open, setOpen] = useState(true)
  const attention = g.workflows.filter((w) => w.kind === 'warn' || w.kind === 'fail').length
  const groupActive = pathname === `/groups/${g.group.id}`

  if (rail) {
    return (
      <NavLink
        to={`/groups/${g.group.id}`}
        title={g.group.name}
        className={cn(ROW, FOCUS, 'justify-center px-0', groupActive ? SELECTED : REST)}
      >
        <StatusGlyph kind={g.kind} />
      </NavLink>
    )
  }

  return (
    <div>
      <div className={cn(ROW, 'gap-1 px-1', groupActive ? SELECTED : REST)}>
        <button
          type="button"
          aria-label={open ? `Collapse ${g.group.name}` : `Expand ${g.group.name}`}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className={cn('rounded-[6px] p-1 outline-none', FOCUS)}
        >
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </button>
        <NavLink to={`/groups/${g.group.id}`} className={cn('flex min-w-0 flex-1 items-center gap-2 outline-none', FOCUS)}>
          <span className="truncate">{g.group.name}</span>
        </NavLink>
        {attention > 0 && (
          <span className="flex items-center gap-1 text-[0.6875rem] font-medium text-[#8e8e8e]">
            <StatusGlyph kind="warn" label={`${attention} need you`} />
            {attention}
          </span>
        )}
      </div>
      {open && (
        <ul className="ml-4 mt-0.5 space-y-0.5 border-l border-[var(--border-subtle)] pl-1.5">
          {g.workflows.length === 0 && <li className="px-2.5 py-1 text-xs text-[#8e8e8e]">No workflows yet</li>}
          {g.workflows.map(({ workflow, kind, note }) => {
            const active = pathname === `/w/${workflow.id}`
            return (
              <li key={workflow.id}>
                <NavLink
                  to={`/w/${workflow.id}`}
                  className={cn(ROW, FOCUS, 'items-start py-1.5', active ? SELECTED : REST)}
                >
                  <span className="mt-[3px]"><StatusGlyph kind={kind} /></span>
                  <span className="min-w-0">
                    <span className="block truncate">{workflow.name}</span>
                    {note && <span className="block truncate text-xs text-[#8e8e8e]">{note}</span>}
                  </span>
                </NavLink>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function CreateGroupRow({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const m = useMutation({
    mutationFn: () => createGroup(name.trim()),
    onSuccess: ({ group }) => {
      qc.invalidateQueries({ queryKey: ['groups'] })
      onDone()
      navigate(`/groups/${group.id}`)
    },
  })
  return (
    <div className="px-1">
      <input
        autoFocus
        value={name}
        disabled={m.isPending}
        placeholder="Group name"
        aria-label="New group name"
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && name.trim()) m.mutate()
          if (e.key === 'Escape') onDone()
        }}
        onBlur={() => !name.trim() && onDone()}
        className="w-full rounded-[10px] border border-[var(--border-default)] bg-[#2c2c2c] px-2.5 py-1.5 text-sm text-[#e8e6e3] outline-none placeholder:text-[#8e8e8e] focus-visible:ring-2 focus-visible:ring-brand/55"
      />
      {m.isError && <p className="mt-1 px-1 text-xs text-status-error">✕ {(m.error as Error).message}</p>}
    </div>
  )
}

export function Sidebar({ rail }: { rail: boolean }) {
  const { groups, publishCount, entitlements, loading } = useShellData()
  const { identity } = useAuth()
  const toggleNav = useShellStore((s) => s.toggleNav)
  const setSearchOpen = useShellStore((s) => s.setSearchOpen)
  const setSettingsOpen = useShellStore((s) => s.setSettingsOpen)
  const offline = useConnectionStore((s) => s.cloud === 'offline')
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
  const { pathname } = useLocation()
  const chatsQ = useQuery({ queryKey: ['chats'], queryFn: chatList, retry: false })
  const chats = chatsQ.data?.chats ?? []

  const compile = entitlements?.meters?.compile_credits
  const ai = entitlements?.meters?.ai_usage_credits ?? entitlements?.meters?.human_edit_tokens
  const aiPct = ai && !ai.unlimited && ai.limit ? Math.min(100, Math.round(((ai.used ?? 0) / ai.limit) * 100)) : null

  return (
    <aside
      aria-label="Navigation"
      className="flex h-full min-h-0 w-full flex-col bg-[var(--bg-sidebar)]"
    >
      <div className={cn('flex h-14 shrink-0 items-center gap-2.5 px-3', rail && 'justify-center px-0')}>
        <img src={logoUrl} alt="" className="size-7 shrink-0 rounded-[8px]" draggable={false} />
        {!rail && (
          <div className="min-w-0 leading-tight">
            <p className="truncate text-sm font-semibold text-[#e8e6e3]">Conxa</p>
            <p className="truncate text-xs text-[#8e8e8e]">Build Studio</p>
          </div>
        )}
        {!rail && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="ml-auto text-[#8e8e8e]"
            aria-label="Fold navigation (Ctrl \)"
            title="Fold navigation (Ctrl \)"
            onClick={toggleNav}
          >
            <PanelLeftClose className="size-4" />
          </Button>
        )}
      </div>

      {rail && (
        <Button type="button" variant="ghost" size="icon-sm" className="mx-auto mb-1 text-[#8e8e8e]" aria-label="Unfold navigation (Ctrl \)" title="Unfold navigation (Ctrl \)" onClick={toggleNav}>
          <PanelLeftOpen className="size-4" />
        </Button>
      )}

      <nav className="space-y-0.5 px-2" aria-label="Primary">
        <NavItem to="/chat" icon={MessageSquare} label="Chat" rail={rail} />
        <button
          type="button"
          title={rail ? 'New workflow (Ctrl N)' : undefined}
          onClick={() => navigate('/new')}
          className={cn(ROW, FOCUS, REST, 'w-full', rail && 'justify-center px-0')}
        >
          <Plus className="size-4 shrink-0" />
          {!rail && (<><span>New workflow</span><Kbd>Ctrl N</Kbd></>)}
        </button>
        <NavItem
          to="/publish"
          icon={UploadCloud}
          label="Publish"
          rail={rail}
          trailing={
            offline ? (
              <span className="ml-auto text-[0.6875rem] text-[#8e8e8e]">waiting to connect</span>
            ) : publishCount > 0 ? (
              <span className="ml-auto rounded-[6px] bg-white/[0.08] px-1.5 text-[0.6875rem] font-medium text-[#a3a3a3]" aria-label={`${publishCount} approved`}>
                {publishCount}
              </span>
            ) : undefined
          }
        />
        <button
          type="button"
          title={rail ? 'Search (Ctrl K)' : undefined}
          onClick={() => setSearchOpen(true)}
          className={cn(ROW, FOCUS, REST, 'w-full', rail && 'justify-center px-0')}
        >
          <Search className="size-4 shrink-0" />
          {!rail && (<><span>Search</span><Kbd>Ctrl K</Kbd></>)}
        </button>
      </nav>

      <div className="mt-3 min-h-0 flex-1 space-y-4 overflow-y-auto px-2 pb-3">
        {!rail && (
          <section aria-label="Chats">
            <h2 className="px-2.5 pb-1 text-[0.6875rem] font-medium text-[#8e8e8e]">Chats</h2>
            {chats.length === 0 && <p className="px-2.5 text-xs text-[#8e8e8e]">General conversations appear here.</p>}
            <ul className="space-y-0.5">
              {chats.map((c) => (
                <li key={c.id}>
                  <NavLink to={`/chat/${c.id}`} className={cn(ROW, FOCUS, pathname === `/chat/${c.id}` ? SELECTED : REST)}>
                    <MessageSquare className="size-3.5 shrink-0" />
                    <span className="truncate">{c.title}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section aria-label="Groups">
          {!rail && (
            <div className="flex items-center px-2.5 pb-1">
              <h2 className="text-[0.6875rem] font-medium text-[#8e8e8e]">Groups</h2>
              <button
                type="button"
                aria-label="Create a group"
                title="Create a group"
                onClick={() => setCreating(true)}
                className={cn('ml-auto rounded-[6px] p-1 text-[#8e8e8e] outline-none hover:bg-white/[0.04] hover:text-[#e8e6e3]', FOCUS)}
              >
                <Plus className="size-3.5" />
              </button>
            </div>
          )}
          {!rail && (
            <p className="px-2.5 pb-1.5 text-xs text-[#8e8e8e]">
              Groups hold workflows that share sign-ins — like everything you do in one billing app.
            </p>
          )}
          {creating && !rail && <CreateGroupRow onDone={() => setCreating(false)} />}
          <div className="space-y-0.5">
            {loading && !groups.length && (
              <div className="space-y-1.5 px-2.5" aria-hidden>
                {[0, 1, 2].map((i) => <div key={i} className="h-6 animate-pulse rounded-[6px] bg-white/[0.05]" />)}
              </div>
            )}
            {groups.map((g) => <GroupBlock key={g.group.id} g={g} rail={rail} />)}
          </div>
        </section>

        <nav aria-label="More" className="space-y-0.5">
          {!rail && <h2 className="px-2.5 pb-1 text-[0.6875rem] font-medium text-[#8e8e8e]">More</h2>}
          <NavItem to="/human-edit" icon={PencilLine} label="Human Edit" rail={rail} />
          <NavItem to="/build-installer" icon={PackageCheck} label="Build Installer" rail={rail} />
          <NavItem to="/workflows" icon={Hammer} label="All workflows" rail={rail} />
        </nav>
      </div>

      <div className={cn('shrink-0 border-t border-[var(--border-subtle)] p-2', rail && 'flex flex-col items-center gap-1')}>
        <button
          type="button"
          title={rail ? 'Settings' : undefined}
          onClick={() => setSettingsOpen(true)}
          className={cn(ROW, FOCUS, REST, 'w-full', rail && 'justify-center px-0')}
        >
          <Settings className="size-4 shrink-0" />
          {!rail && <span className="truncate">Settings</span>}
        </button>
        {!rail && (
          <div className="px-2.5 pt-1 text-xs text-[#8e8e8e]">
            <p className="truncate font-medium text-[#a3a3a3]">{identity?.org_name || identity?.email || 'Workspace'}</p>
            <ConnectionLine />
            {(compile || aiPct !== null) && (
              <p className="truncate">
                {compile && compile.remaining != null ? `${compile.remaining} compile credits` : null}
                {compile && compile.remaining != null && aiPct !== null ? ' · ' : null}
                {aiPct !== null ? `AI credits ${aiPct}% used` : null}
              </p>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
