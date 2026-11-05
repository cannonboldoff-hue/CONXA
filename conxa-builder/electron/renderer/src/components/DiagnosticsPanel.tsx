import { useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { ChevronRight, Copy } from 'lucide-react'
import type { StepEditorDTO, WorkflowResponse } from '@/types/workflow'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { SECTION_LABEL } from '@/lib/fieldStyles'
import { cn } from '@/lib/utils'

type Props = {
  workflow: WorkflowResponse
  currentStep: StepEditorDTO | null
  skillId?: string
  version?: number
}

function Row({ label, value, mono = false }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-white/8 py-[11px] text-sm">
      <span className="shrink-0 text-zinc-500">{label}</span>
      <span className={cn('min-w-0 truncate text-right text-zinc-200', mono && 'font-mono')}>{value}</span>
    </div>
  )
}

/** Full value on its own line, broken across lines — for long identity hashes a support
 * engineer needs to read/copy in full rather than see truncated. */
function HashRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="font-mono text-xs break-all text-zinc-200">{value}</span>
    </div>
  )
}

function statusClass(status: string | undefined) {
  if (status === 'failed') return 'text-status-error'
  if (status === 'stale' || status === 'review_needed') return 'text-status-warn'
  return undefined
}

/**
 * Support/Conxa-engineer tier — integrity hashes, compile telemetry, and policy versioning that
 * are correctly kept out of an approver's way but shouldn't be entirely invisible either. Fed
 * by wf.compile_health (workflow-level) and the currently selected step's fingerprint (per-step
 * stable_hash/compat_fingerprint) — both read-only projections, never editable. Rarely-needed
 * detail (skill id, compat fingerprint, frame/shadow depth) sits behind "Advanced". See
 * research-analysis/Human-Edit-vs-Skill-Package.md §5.1's three-tier IA.
 */
export function DiagnosticsPanel({ workflow, currentStep, skillId, version }: Props) {
  const health = workflow.compile_health
  const routerStats = health.llm_router_stats && typeof health.llm_router_stats === 'object' ? health.llm_router_stats : {}
  const fingerprint = currentStep?.fingerprint
  const hasFingerprint = !!fingerprint && !!(fingerprint.stable_hash || fingerprint.compat_fingerprint)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  return (
    <div className="space-y-6">
      <section>
        <p className={cn(SECTION_LABEL, 'pb-1.5')}>Compile report</p>
        <Row label="Status" value={<span className={statusClass(health.status)}>{health.status || '—'}</span>} />
        <Row label="Steps total" value={health.steps_total ?? '—'} />
        <Row
          label="Min. confidence"
          value={typeof health.min_confidence === 'number' ? `${Math.round(health.min_confidence * 100)}%` : '—'}
        />
        <Row label="Steps with warnings" value={health.steps_with_warnings ?? 0} />
        <Row label="Compiler policy version" value={health.compiler_policy_version || '—'} mono />
        <Row label="Required runtime" value={health.required_runtime || '—'} mono />
        <Row label="Structural fingerprint" value={health.structural_fingerprint_present ? 'present' : 'absent'} />
      </section>

      {health.model_ledger && Object.keys(health.model_ledger).length > 0 ? (
        <section>
          <p className={cn(SECTION_LABEL, 'pb-1.5')}>What the model changed or dropped</p>
          {Object.entries(health.model_ledger).map(([task, c]) => (
            <Row key={task} label={task} value={`changed ${c.changed}, dropped ${c.dropped}, added ${c.added}`} mono />
          ))}
        </section>
      ) : null}

      {Object.keys(routerStats).length > 0 ? (
        <section>
          <p className={cn(SECTION_LABEL, 'pb-2')}>LLM router stats</p>
          <pre className="overflow-x-auto rounded-[10px] border border-white/8 bg-black/30 px-3.5 py-3 font-mono text-sm whitespace-pre-wrap text-zinc-200">
            {JSON.stringify(routerStats, null, 2)}
          </pre>
        </section>
      ) : null}

      <section>
        <p className="flex items-center gap-2 pb-2">
          <span className={SECTION_LABEL}>Selected step's identity hashes</span>
          {currentStep ? (
            <span className="rounded-full bg-white/8 px-2 py-0.5 text-xs text-zinc-500 uppercase">
              Step #{currentStep.step_index + 1}
            </span>
          ) : null}
        </p>
        {!currentStep ? (
          <p className="text-sm text-zinc-500">Select a step to see its integrity hashes.</p>
        ) : !hasFingerprint ? (
          <p className="text-sm text-zinc-500">No identity_bundle on this step (e.g. a recording marker).</p>
        ) : (
          <HashRow label="Stable hash" value={fingerprint.stable_hash || '—'} />
        )}
      </section>

      {skillId || hasFingerprint ? (
        <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
          <CollapsibleTrigger asChild>
            <button type="button" className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-300">
              Advanced
              <ChevronRight className={cn('size-3.5 transition-transform', advancedOpen && 'rotate-90')} aria-hidden />
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">
            {skillId ? (
              <div className="flex items-center justify-between gap-3 border-b border-white/8 py-[11px] text-sm">
                <span className="shrink-0 text-zinc-500">Skill id</span>
                <span className="flex min-w-0 items-center gap-1">
                  <span className="min-w-0 truncate font-mono text-zinc-200">{skillId}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="size-6 shrink-0 text-zinc-500 hover:bg-white/10 hover:text-zinc-200"
                    aria-label="Copy skill id"
                    onClick={() =>
                      navigator.clipboard
                        .writeText(skillId)
                        .then(() => toast.success('Skill id copied'))
                        .catch(() => toast.error('Could not copy'))
                    }
                  >
                    <Copy className="size-3" />
                  </Button>
                </span>
              </div>
            ) : null}
            {skillId && version !== undefined && version > 0 ? <Row label="Version" value={`v${version}`} mono /> : null}
            {hasFingerprint ? (
              <>
                <div className="border-b border-white/8 py-[11px]">
                  <HashRow label="Compat fingerprint" value={fingerprint.compat_fingerprint || '—'} />
                </div>
                <Row label="Frame depth" value={fingerprint.frame_depth} />
                <Row label="Shadow depth" value={fingerprint.shadow_depth} />
              </>
            ) : null}
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </div>
  )
}
