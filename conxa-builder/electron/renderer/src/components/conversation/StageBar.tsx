import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { STAGES, STAGE_LABEL, stageOfLifecycle, type Stage } from '@/lib/stages'
import type { Lifecycle } from '@/api/conversationApi'
import { cn } from '@/lib/utils'

/** Heading between the Record / Compile / Test sections of a workflow's thread. */
export function StageDivider({ stage, number }: { stage: Stage; number: number }) {
  return (
    <div data-stage={stage} className="flex items-center gap-3 pt-3">
      <span className="text-[0.6875rem] font-medium text-[var(--text-meta)]">
        {number} · {STAGE_LABEL[stage]}
      </span>
      <span className="h-px flex-1 bg-[var(--border-subtle)]" aria-hidden />
    </div>
  )
}

/** Where a click on a stage goes: its section in the thread, or the end for stages with no section. */
function jumpTo(stage: Stage) {
  const sections = document.querySelectorAll<HTMLElement>(`[data-stage="${stage}"]`)
  const target = sections[sections.length - 1]
  if (target) {
    target.scrollIntoView({ block: 'start', behavior: 'smooth' })
    return
  }
  const log = document.querySelector<HTMLElement>('[role="log"]')
  log?.scrollTo({ top: log.scrollHeight, behavior: 'smooth' })
}

/** Five steps with the current one marked. Neutral tint, not clay: clay is for the one action to take next. */
export function StageBar({ lifecycle }: { lifecycle: Lifecycle | undefined }) {
  if (!lifecycle) return null
  const current = STAGES.indexOf(stageOfLifecycle(lifecycle))
  const stuck = lifecycle.glyph === 'fail' || lifecycle.glyph === 'warn'
  return (
    <nav aria-label="Progress" className="flex flex-wrap items-center gap-1.5 text-xs">
      {STAGES.map((s, i) => {
        const done = i < current
        const active = i === current
        return (
          <span key={s} className="flex items-center gap-1.5">
            {i > 0 && <span className="h-px w-4 bg-[var(--border-default)]" aria-hidden />}
            <button
              type="button"
              onClick={() => jumpTo(s)}
              aria-current={active ? 'step' : undefined}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 transition-colors hover:bg-white/[0.05]',
                active ? 'bg-[var(--bg-selected)] font-semibold text-[var(--text-primary)]' : 'text-[var(--text-secondary)]',
              )}
            >
              <StatusGlyph kind={done ? 'ok' : active ? (stuck ? lifecycle.glyph : 'run') : 'none'} />
              {STAGE_LABEL[s]}
            </button>
          </span>
        )
      })}
    </nav>
  )
}
