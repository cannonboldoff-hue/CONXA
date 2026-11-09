'use client'

import { useQuery } from '@tanstack/react-query'
import { fetchTrackingDashboard, fetchTrackingDrift } from '@/api/workflowsApi'
import { queryKeys } from '@/lib/queryKeys'
import { FLAKY_SHARE, Heatmap } from '@/components/viz/Heatmap'
import { TierLadder } from '@/components/viz/TierLadder'
import { INK, STATUS_COLORS } from '@/components/viz/chartTheme'
import { DashboardError, DashboardPageBody, DashboardSkeleton, NoTelemetry, UpgradeRequired, isUpgradeRequiredError } from './DashboardStates'
import { SectionCard, Sentence } from './SectionCard'
import { fmtNumber, fmtPercent, fmtRelative } from './dashboardData'
import { healingSummary } from './narrative'
import { rangeLongLabel, useRange } from './useRange'

const TWO_COLUMN = 'grid gap-14 lg:grid-cols-2 xl:gap-16'

function rateColor(rate: number): string {
  if (rate < 80) return STATUS_COLORS.error
  if (rate < 95) return STATUS_COLORS.warn
  return INK.mid
}

export function HealingPage() {
  const [range] = useRange()
  const dashboard = useQuery({
    queryKey: queryKeys.trackingDashboard(range),
    queryFn: () => fetchTrackingDashboard(range),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
  const drift = useQuery({
    queryKey: queryKeys.trackingDrift(),
    queryFn: fetchTrackingDrift,
    staleTime: 30_000,
  })

  if (dashboard.isPending) return <DashboardSkeleton />
  if (isUpgradeRequiredError(dashboard.error)) return <DashboardPageBody><UpgradeRequired /></DashboardPageBody>
  if (dashboard.isError || !dashboard.data) return <DashboardError error={dashboard.error} onRetry={() => dashboard.refetch()} />

  const data = dashboard.data
  const cascade = data.recovery_cascade
  const label = rangeLongLabel(range)

  if (data.metrics.total_executions === 0) {
    return (
      <DashboardPageBody>
        <NoTelemetry />
      </DashboardPageBody>
    )
  }

  // Share of steps that entered recovery and had to reach a paid tier. Derived from step
  // counts, not tier hits — one step trying two free methods is still one step.
  const agentShare = cascade.entered_recovery
    ? Math.round((cascade.agent_assisted / cascade.entered_recovery) * 100)
    : 0
  const totalSteps = cascade.resolved_directly + cascade.entered_recovery
  const methodTotal = data.recovery_type_usage.reduce((sum, u) => sum + u.count, 0)
  const freeShare = methodTotal
    ? Math.round(
        (data.recovery_type_usage
          .filter((u) => u.type === 'Selector' || u.type === 'Text Anchor')
          .reduce((sum, u) => sum + u.count, 0) /
          methodTotal) *
          100,
      )
    : 0
  const flakyHours = data.reliability_heatmap.cells.filter((c) => c.failed > 0 && c.failed / Math.max(1, c.runs) > FLAKY_SHARE)

  return (
    <DashboardPageBody>
      <section aria-label="Summary" className="space-y-9">
        <div className="space-y-6">
          <p className="text-sm text-zinc-400">Is self-healing keeping up?</p>
          <p className="max-w-3xl text-2xl leading-snug tracking-[-0.02em] text-zinc-400 sm:text-3xl">
            <Sentence parts={healingSummary(cascade)} />
          </p>
        </div>
        <div className="grid grid-cols-2 border-y border-white/8 lg:grid-cols-4 lg:divide-x lg:divide-white/8">
          {[
            ['Steps that needed repair', fmtNumber(cascade.entered_recovery), `${totalSteps ? fmtPercent((cascade.entered_recovery / totalSteps) * 100) : '0%'} of ${fmtNumber(totalSteps)} steps run`],
            ['Repaired without a person', fmtNumber(cascade.healed), `${fmtPercent(cascade.heal_rate)} of repair attempts`],
            ['Repaired at zero AI cost', fmtNumber(cascade.zero_token_heals), 'Tier A — no model call made'],
            ['Needed an AI model', fmtNumber(cascade.agent_assisted), `Tier B — ${agentShare}% of repairs, the billable path`],
          ].map(([title, value, sub]) => (
            <div key={title} className="min-w-0 space-y-2 py-5 pr-6 lg:px-6 lg:first:pl-0">
              <p className="truncate text-sm text-zinc-400">{title}</p>
              <p className="text-3xl font-semibold leading-none tabular-nums text-zinc-100">{value}</p>
              <p className="truncate text-xs text-zinc-400">{sub}</p>
            </div>
          ))}
        </div>
      </section>

      <SectionCard
        question="Which repair method is doing the work?"
        answer={
          methodTotal
            ? `${freeShare >= 50 ? 'Mostly the free tier' : 'Mostly the AI tier'} — ${freeShare}% of repairs cost nothing.`
            : 'No repairs were needed in this period.'
        }
      >
        <TierLadder usage={data.recovery_type_usage} />
      </SectionCard>

      <SectionCard
        question="When does automation get flaky?"
        answer={
          flakyHours.length
            ? `${flakyHours.length} hour${flakyHours.length === 1 ? '' : 's'} of the week had more than 5% of runs fail.`
            : 'No hour of the week stands out — failures are rare and spread out.'
        }
        context={`By hour of the week, in UTC · ${label.toLowerCase()}.`}
      >
        {data.reliability_heatmap.cells.length ? (
          <Heatmap cells={data.reliability_heatmap.cells} maxRuns={data.reliability_heatmap.max_runs} />
        ) : (
          <p className="border-t border-white/8 pt-5 text-sm text-zinc-400">Not enough runs yet to show a weekly pattern.</p>
        )}
      </SectionCard>

      <div className={TWO_COLUMN}>
        <SectionCard
          question="Which steps keep drifting?"
          answer={
            drift.data?.queue.length
              ? `${drift.data.queue.length} step${drift.data.queue.length === 1 ? '' : 's'} need repair again and again. Republishing gives them a direct match.`
              : 'No step keeps needing repair.'
          }
        >
          {drift.isPending ? (
            <p className="border-t border-white/8 pt-5 text-sm text-zinc-400">Loading drift signals…</p>
          ) : drift.data?.queue.length ? (
            <ul className="border-t border-white/8">
              {drift.data.queue.slice(0, 6).map((row) => (
                <li key={`${row.workflow_id}:${row.workflow_ver}:${row.step_id}`} className="space-y-2 border-b border-white/8 py-4">
                  <p className="truncate text-sm text-zinc-100">
                    {row.workflow_id} · step {row.step_id === null ? '—' : row.step_id + 1}
                  </p>
                  <p className="truncate text-xs text-zinc-400">
                    v{row.workflow_ver} · usually repaired by {row.dominant_method} ({row.dominant_tier}) · {fmtRelative(row.last_seen)}
                  </p>
                  <div className="flex items-center gap-3">
                    <span className="h-1.5 flex-1 rounded-full" style={{ background: INK.track }}>
                      <span
                        className="block h-full rounded-full"
                        style={{ width: `${Math.min(100, row.occurrence_rate_pct)}%`, background: rateColor(100 - row.occurrence_rate_pct) }}
                      />
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-zinc-400">
                      repaired on {fmtPercent(row.occurrence_rate_pct)} of runs
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="border-t border-white/8 pt-5 text-sm text-zinc-400">
              Compiled selectors are still matching directly.
            </p>
          )}
        </SectionCard>

        <SectionCard
          question="Are post-step checks starting to fail?"
          answer={
            data.assertion_health_by_step.some((r) => r.previous_pass_rate != null && r.pass_rate < r.previous_pass_rate - 5)
              ? 'Some checks dropped — often the first sign a page changed.'
              : data.assertion_health_by_step.length
                ? 'Checks are holding steady.'
                : 'No post-step checks have reported yet.'
          }
        >
          {data.assertion_health_by_step.length ? (
            <div className="space-y-3">
              <ul className="border-t border-white/8">
                {data.assertion_health_by_step.slice(0, 6).map((row) => (
                  <li key={`${row.company}:${row.workflow}:${row.step_index}`} className="space-y-2.5 border-b border-white/8 py-4">
                    <div className="flex items-baseline justify-between gap-4">
                      <span className="truncate text-sm text-zinc-100">
                        {row.workflow} · {row.step_label}
                      </span>
                      <span className="shrink-0 text-sm tabular-nums text-zinc-100">
                        {fmtPercent(row.pass_rate)}
                        {row.previous_pass_rate != null ? (
                          <span className="text-zinc-400"> · was {fmtPercent(row.previous_pass_rate)}</span>
                        ) : null}
                      </span>
                    </div>
                    <div className="space-y-1">
                      {row.previous_pass_rate != null ? (
                        <span className="block h-1 rounded-full" style={{ background: INK.track }}>
                          <span className="block h-full rounded-full" style={{ width: `${row.previous_pass_rate}%`, background: INK.soft }} />
                        </span>
                      ) : null}
                      <span className="block h-1 rounded-full" style={{ background: INK.track }}>
                        <span
                          className="block h-full rounded-full"
                          style={{ width: `${Math.max(1, row.pass_rate)}%`, background: rateColor(row.pass_rate) }}
                        />
                      </span>
                    </div>
                    <p className="text-xs text-zinc-400">
                      {row.passed} of {row.total} checks passed
                      {row.advisory_failures > 0 ? ` · ${row.advisory_failures} advisory failures` : ''}
                    </p>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-zinc-400">Grey bar: last period. Coloured bar: this period.</p>
            </div>
          ) : null}
        </SectionCard>

        <SectionCard
          question="Are steps coming back unconfirmed?"
          answer={
            (data.proof_health_by_step ?? []).length
              ? 'Some steps ran but could not be confirmed. The page may have changed how it says "done".'
              : 'Every step that reports proof is being confirmed.'
          }
        >
          {(data.proof_health_by_step ?? []).length ? (
            <ul className="border-t border-white/8">
              {(data.proof_health_by_step ?? []).slice(0, 6).map((row) => (
                <li key={`${row.company}:${row.workflow}:${row.step_index}`} className="space-y-1.5 border-b border-white/8 py-4">
                  <div className="flex items-baseline justify-between gap-4">
                    <span className="truncate text-sm text-zinc-100">
                      {row.workflow} · {row.step_label}
                    </span>
                    <span className="shrink-0 text-sm tabular-nums text-zinc-100">
                      {fmtPercent(row.unproven_rate)} of runs
                      {row.previous_unproven_rate != null && row.previous_unproven_rate > 0 ? (
                        <span className="text-zinc-400"> · was {fmtPercent(row.previous_unproven_rate)}</span>
                      ) : null}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-400">
                    {row.unproven} could not be confirmed
                    {row.failed > 0 ? ` · ${row.failed} failed` : ''}
                    {row.waiting > 0 ? ` · ${row.waiting} still running when checked` : ''}
                    {` · ${row.proven} of ${row.total} confirmed`}
                  </p>
                </li>
              ))}
            </ul>
          ) : null}
        </SectionCard>
      </div>
    </DashboardPageBody>
  )
}
