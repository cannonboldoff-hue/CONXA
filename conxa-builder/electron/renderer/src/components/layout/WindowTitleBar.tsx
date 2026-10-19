import { type ReactNode, useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Copy, Info, Minus, PanelLeft, PanelRight, RefreshCw, Square, X } from 'lucide-react'
import { usePageHeaderContext } from '@/contexts/PageHeaderContext'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useShellStore } from '@/store/shellStore'
import { useBrowserStore } from '@/store/browserStore'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const logoUrl = new URL('../../assets/conxa-icon.png', import.meta.url).href

function WindowButton({
  label,
  className,
  onClick,
  children,
}: {
  label: string
  className?: string
  onClick: () => void
  children: ReactNode
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      className={cn('app-region-no-drag h-8 w-10 rounded-none text-zinc-400 hover:bg-white/8 hover:text-white', className)}
      onClick={onClick}
    >
      {children}
    </Button>
  )
}

function ChromeIconButton({ label, disabled, onClick, children }: { label: string; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      disabled={disabled}
      className="app-region-no-drag text-zinc-400 hover:bg-white/8 hover:text-white disabled:opacity-40"
      onClick={onClick}
    >
      {children}
    </Button>
  )
}

/** The current page's title (registered by its <PageHeader>), with its description folded into an info icon. */
/** Page-supplied compact controls (e.g. the group page's usage meters), drawn left of Refresh. */
function TitleBarActions() {
  const { header } = usePageHeaderContext()
  if (!header?.titleBarActions) return null
  return <div className="app-region-no-drag mr-1 flex shrink-0 items-center gap-2">{header.titleBarActions}</div>
}

function PageTitle() {
  const { header } = usePageHeaderContext()
  const hasDescription = header?.description != null && header.description !== false && header.description !== ''
  return (
    <div className="flex min-w-0 flex-1 items-center gap-1.5 px-4 sm:px-6">
      {header ? (
        <>
          <h1 className="truncate text-sm font-semibold text-[var(--text-primary)]">{header.title}</h1>
          {hasDescription ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label="About this page"
                  className="app-region-no-drag shrink-0 rounded-full p-1 text-zinc-500 outline-none hover:text-zinc-200 focus-visible:text-zinc-200"
                >
                  <Info className="size-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom" align="start" className="max-w-[22rem]">{header.description}</TooltipContent>
            </Tooltip>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

export function WindowTitleBar() {
  const [isMaximized, setIsMaximized] = useState(false)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const navCollapsed = useShellStore((s) => s.navCollapsed)
  const navWidth = useShellStore((s) => s.navWidth)
  const toggleNav = useShellStore((s) => s.toggleNav)
  const browserRun = useBrowserStore((b) => b.run)
  const browserFolded = useBrowserStore((b) => b.folded)
  const setBrowserFolded = useBrowserStore((b) => b.setFolded)

  useEffect(() => {
    let mounted = true

    void window.conxa.windowControls.isMaximized().then((value) => {
      if (mounted) setIsMaximized(value)
    })

    const unsubscribe = window.conxa.windowControls.onMaximizeChange(setIsMaximized)
    return () => {
      mounted = false
      unsubscribe()
    }
  }, [])

  const handleToggleMaximize = () => {
    void window.conxa.windowControls.toggleMaximize().then(setIsMaximized)
  }

  return (
    <div className="app-region-drag flex h-10 shrink-0 select-none items-center bg-[var(--bg-base)] text-zinc-200">
      <div
        className="flex h-full shrink-0 items-center gap-1 bg-[var(--bg-sidebar)] px-3"
        style={{ width: navCollapsed ? undefined : navWidth }}
      >
        <img src={logoUrl} alt="" className="mr-1 size-5 shrink-0 rounded-[5px]" draggable={false} />
        <span className="mr-2 truncate text-sm font-semibold text-white">Conxa</span>
        <ChromeIconButton label="Toggle navigation (Ctrl \)" onClick={toggleNav}>
          <PanelLeft className="size-4" />
        </ChromeIconButton>
        <ChromeIconButton label="Back" onClick={() => navigate(-1)}>
          <ArrowLeft className="size-4" />
        </ChromeIconButton>
        <ChromeIconButton label="Forward" onClick={() => navigate(1)}>
          <ArrowRight className="size-4" />
        </ChromeIconButton>
      </div>

      <PageTitle />

      <TitleBarActions />

      <ChromeIconButton
        label="Refresh"
        onClick={async () => {
          setRefreshing(true)
          try {
            await qc.refetchQueries({ type: 'active' })
          } finally {
            setRefreshing(false)
          }
        }}
      >
        <RefreshCw className={cn('size-4', refreshing && 'animate-spin')} />
      </ChromeIconButton>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={!browserRun}
        title="Test browser"
        className="app-region-no-drag mr-2 gap-1.5 text-zinc-400 hover:bg-white/8 hover:text-white disabled:opacity-40"
        onClick={() => setBrowserFolded(!browserFolded)}
      >
        <PanelRight className="size-4" />
        Browser
      </Button>

      <div className="app-region-no-drag flex h-full items-center">
        <WindowButton label="Minimize" onClick={() => void window.conxa.windowControls.minimize()}>
          <Minus className="size-4" />
        </WindowButton>
        <WindowButton label={isMaximized ? 'Restore' : 'Maximize'} onClick={handleToggleMaximize}>
          {isMaximized ? <Copy className="size-3.5" /> : <Square className="size-3.5" />}
        </WindowButton>
        <WindowButton
          label="Close"
          className="hover:bg-red-500 hover:text-white"
          onClick={() => void window.conxa.windowControls.close()}
        >
          <X className="size-4" />
        </WindowButton>
      </div>
    </div>
  )
}
