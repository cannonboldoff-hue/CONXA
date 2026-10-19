import type { Ref } from 'react'
import type { SignInSession } from '@/components/GroupAuthWizard'
import { cn } from '@/lib/utils'
import { Check, Loader2, Lock, ShieldAlert } from 'lucide-react'

/**
 * The group page's browser pane. Sign-in itself happens in Chromium, which Studio opens docked
 * over `regionRef`'s rect (see GroupAuthWizard's `getWindowRect`) — this component owns the status
 * strip and the region the window sits on, and says so plainly if the window isn't covering it.
 */
export function SignInPane({
  session,
  connectedApp,
  regionRef,
}: {
  session: SignInSession | null
  connectedApp: string | null
  regionRef: Ref<HTMLDivElement>
}) {
  const connected = !session && connectedApp !== null
  const phase = session?.phase

  const tone = connected ? 'ok' : phase === 'failed' ? 'error' : phase === 'unconfirmed' ? 'warn' : 'run'
  const title = connected
    ? `${connectedApp || 'App'} session saved.`
    : phase === 'launching'
      ? 'Opening the browser…'
      : phase === 'failed'
        ? `Couldn’t open ${session!.appName}.`
        : phase === 'unconfirmed'
          ? 'Couldn’t confirm you’re signed in yet.'
          : `Waiting for you to sign in to ${session?.appName ?? ''}`
  const detail = connected
    ? 'Stored on this computer only.'
    : phase === 'failed'
      ? 'Retry from the list, or skip and sign in later.'
      : phase === 'unconfirmed'
        ? 'Keep signing in, then click Done again — or Save anyway.'
        : null

  return (
    <section
      aria-label="Sign-in browser"
      className="flex min-h-[28rem] min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-[var(--border-subtle)] bg-[#252525] lg:min-h-0"
    >
      {/* One quiet status line — the docked browser below carries its own tab strip and address bar. */}
      <header
        role="status"
        className={cn(
          'flex shrink-0 items-center gap-2.5 px-4 py-2.5 text-[0.8125rem]',
          tone === 'ok' ? 'bg-emerald-500/[0.06]' : tone === 'error' ? 'bg-red-500/[0.06]' : tone === 'warn' ? 'bg-amber-500/[0.06]' : 'bg-[#93b4e6]/[0.06]',
        )}
      >
        <span
          aria-hidden
          className={cn(
            'shrink-0',
            tone === 'ok' && 'text-status-ok',
            tone === 'warn' && 'text-status-warn',
            tone === 'error' && 'text-status-error',
            tone === 'run' && 'text-[#93b4e6]',
          )}
        >
          {tone === 'ok' ? <Check className="size-3.5" /> : tone === 'run' ? <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" /> : <ShieldAlert className="size-3.5" />}
        </span>
        <p className="min-w-0 truncate text-[#e8e6e3]">
          {title}
          {detail && <span className="ml-1.5 text-[#a3a3a3]">{detail}</span>}
        </p>
        {session && (
          <span className="ml-auto hidden max-w-[45%] shrink-0 items-center gap-1.5 truncate font-mono text-xs text-[#8e8e8e] sm:flex">
            <Lock className="size-3 shrink-0" aria-hidden />
            <span className="truncate">{session.loginUrl.replace(/^https?:\/\//, '')}</span>
          </span>
        )}
      </header>
      <div ref={regionRef} className="relative min-h-0 flex-1 bg-[#1a1a1a]">
        <div className="absolute inset-0 flex animate-in flex-col items-center justify-center gap-1.5 px-6 text-center fade-in duration-300 motion-reduce:animate-none">
          <p className="text-sm text-[#a3a3a3]">{connected ? 'Browser closed.' : 'The sign-in browser opens here.'}</p>
        </div>
      </div>
    </section>
  )
}
