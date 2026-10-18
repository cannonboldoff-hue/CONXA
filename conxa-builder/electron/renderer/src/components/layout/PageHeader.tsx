import { type ReactNode, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { usePageHeaderContext } from '@/contexts/PageHeaderContext'

type PageHeaderProps = {
  title: string
  description?: ReactNode
  /** Left half of the strip — status/usage content that reads before the buttons. */
  leading?: ReactNode
  actions?: ReactNode
  className?: string
  /** Extra chrome rendered full-width under AppChrome's header row itself (not
   * in the page body) — e.g. a progress strip. Most pages never set this. */
  extra?: ReactNode
  /** Shows a back-chevron at the true left edge of AppChrome's header row,
   * before the title. Most pages never set this. */
  onBack?: () => void
  /** When set, the back control is a labelled button ("Back to conversation") instead of an icon. */
  backLabel?: string
  /** Renders `actions` inside AppChrome's header row (after the title) instead
   * of a separate strip in the page body. */
  inlineActions?: boolean
}

/** Registers this page's title/description into AppChrome's sticky top bar
 * so it's never repeated in the page body.
 * Only renders something here when `leading` or `actions` are supplied — a
 * two-part strip that stays put in the body: standing information on the left,
 * page-level buttons on the right. */
export function PageHeader({ title, description, leading, actions, className, extra, onBack, backLabel, inlineActions }: PageHeaderProps) {
  const { setHeader } = usePageHeaderContext()
  const headerActions = inlineActions ? actions : undefined

  useEffect(() => {
    setHeader({ title, description, extra, onBack, backLabel, actions: headerActions })
    return () => setHeader(null)
  }, [title, description, extra, onBack, backLabel, headerActions, setHeader])

  if (inlineActions || (!actions && !leading)) return null

  return (
    // A toolbar, not a second header: one row tall, tinted a shade darker than
    // the canvas so it reads as chrome continuing from the title bar above it.
    <div className={cn('flex min-h-12 items-center gap-4 border-b border-white/8 bg-white/[0.015] px-4 py-2 sm:px-6', className)}>
      {leading ? <div className="flex min-w-0 flex-1 items-center gap-2">{leading}</div> : null}
      {actions ? (
        // Without a leading slot this is the sole child and stays shrinkable, so
        // wide toolbars (Human Edit's) compress on narrow windows exactly as
        // they did before the slot existed. With one, the leading content is
        // what gives — the buttons keep their size.
        <div className={cn('ml-auto flex items-center gap-2', leading ? 'shrink-0' : 'min-w-0')}>{actions}</div>
      ) : null}
    </div>
  )
}
