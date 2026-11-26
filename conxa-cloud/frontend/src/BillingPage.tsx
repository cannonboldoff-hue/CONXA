'use client'
import { errorMessage } from '@/lib/apiBase'
import { queryKeys } from '@/lib/queryKeys'
import {
  displayPlanName,
  formatMeterValue,
  formatPeriod,
  formatPrice,
  formatUnixDate,
  meterPercent,
  meterTone,
  normalizePlan,
} from '@/billing/billingData'

import { useEffect, useMemo, useState, type ComponentType } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Code2,
  CreditCard,
  Loader2,
  Mail,
  PackageCheck,
  PhoneCall,
  RefreshCw,
  Users,
  Wand2,
} from 'lucide-react'
import {
  buyAddon,
  createCashfreeSubscription,
  listAddons,
  listPlans,
  verifyAddon,
  verifyCashfreeSubscription,
  type CheckoutTier,
  type Plan,
} from '@/api/cashfreeApi'
import {
  fetchEntitlements,
  fetchSubscription,
  type EntitlementMeter,
  type EntitlementMeterKey,
} from '@/api/productApi'
import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

type UsageMeterConfig = {
  key: EntitlementMeterKey
  label: string
  description: string
  icon: ComponentType<{ className?: string }>
}

const SALES_EMAIL = 'founder@conxa.in'
const SALES_PHONE_DISPLAY = process.env.NEXT_PUBLIC_SALES_PHONE_DISPLAY ?? '+91 9970257247'
const SALES_PHONE_TEL =
  process.env.NEXT_PUBLIC_SALES_PHONE_TEL ?? SALES_PHONE_DISPLAY.replace(/[^\d+]/g, '')

const ENTERPRISE_PLAN: Plan = {
  tier: 'enterprise',
  name: 'Enterprise',
  amount: 0,
  currency: 'INR',
  period: 'custom',
  features: [
    'Custom seats and installer slots',
    'Custom compile credits',
    'Custom AI Usage Credits',
    'Dedicated onboarding and support',
    'Security review and procurement support',
  ],
}

const METER_CONFIGS: UsageMeterConfig[] = [
  {
    key: 'seats',
    label: 'Seats',
    description: 'Workspace members',
    icon: Users,
  },
  {
    key: 'skill_pack_slots',
    label: 'Skill Pack Slots',
    description: 'Published skill pack products',
    icon: PackageCheck,
  },
  {
    key: 'execute_seats',
    label: 'Execute Seats',
    description: 'People with Conxa Execute access',
    icon: Bot,
  },
  {
    key: 'compile_credits',
    label: 'Compile Credits',
    description: 'Fresh workflow compiles',
    icon: Code2,
  },
  {
    key: 'ai_usage_credits',
    label: 'AI Usage Credits',
    description: 'LLM-assisted recovery, edits, and Execute chat usage',
    icon: Wand2,
  },
]


export function BillingPage() {
  const queryClient = useQueryClient()
  const [processingTier, setProcessingTier] = useState<string | null>(null)

  const plansQuery = useQuery({
    queryKey: queryKeys.billingPlans,
    queryFn: listPlans,
  })
  const subscriptionQuery = useQuery({
    queryKey: queryKeys.subscription,
    queryFn: fetchSubscription,
  })
  const entitlementsQuery = useQuery({
    queryKey: queryKeys.entitlements,
    queryFn: fetchEntitlements,
  })

  // After Cashfree redirects back from mandate authorization, verify the pending subscription.
  useEffect(() => {
    const pendingSubId = sessionStorage.getItem('cashfree_pending_sub')
    if (!pendingSubId) return
    sessionStorage.removeItem('cashfree_pending_sub')

    void verifyCashfreeSubscription(pendingSubId)
      .then(async () => {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: queryKeys.subscription }),
          queryClient.invalidateQueries({ queryKey: queryKeys.entitlements }),
        ])
        toast.success('Subscription activated')
      })
      .catch((err: unknown) => {
        toast.error(
          err instanceof Error
            ? err.message
            : 'Subscription verification failed. Please refresh to check your plan.',
        )
      })
  }, [queryClient])

  // After Cashfree redirects back from a one-time add-on payment, verify and credit the wallet.
  useEffect(() => {
    const pendingLinkId = sessionStorage.getItem('cashfree_pending_addon')
    if (!pendingLinkId) return
    sessionStorage.removeItem('cashfree_pending_addon')

    void verifyAddon(pendingLinkId)
      .then(async (res) => {
        await queryClient.invalidateQueries({ queryKey: queryKeys.entitlements })
        if (res.status === 'PAID') toast.success('Add-on credits added to your wallet')
        // Any other status (pending, etc.) is a normal 200 response, not an
        // error — the webhook or a later check covers it, no toast needed.
      })
      .catch((err: unknown) => {
        // The backend only ever throws here for a genuine verification failure
        // (Cashfree unreachable, etc.) — "not paid yet" is a 200 handled above,
        // never routed through this catch. This used to be silently swallowed
        // with an outdated comment claiming otherwise: a real failure here
        // meant a customer who paid saw nothing and had no idea their credits
        // hadn't arrived.
        toast.error(
          err instanceof Error
            ? err.message
            : "Couldn't verify your payment. If you were charged, contact support if credits don't appear shortly.",
        )
      })
  }, [queryClient])

  const plans = useMemo(() => {
    const remotePlans = plansQuery.data?.plans ?? []
    const hasEnterprise = remotePlans.some((plan) => normalizePlan(plan.tier) === 'enterprise')
    const visiblePlans = hasEnterprise ? remotePlans : [...remotePlans, ENTERPRISE_PLAN]
    return [...visiblePlans].sort((a, b) => {
      const order = ['free', 'starter', 'pro', 'enterprise']
      return order.indexOf(normalizePlan(a.tier)) - order.indexOf(normalizePlan(b.tier))
    })
  }, [plansQuery.data?.plans])

  const subscription = subscriptionQuery.data?.subscription
  const entitlements = entitlementsQuery.data
  const hasError = plansQuery.isError || subscriptionQuery.isError || entitlementsQuery.isError
  // A failed subscription/entitlements fetch used to fall through to "free" —
  // a paying customer whose fetch failed would see their own plan grid
  // highlight Free as current, with an Upgrade button on the tier they
  // already have. `null` here means "we don't know", not "they're on Free".
  const rawPlan = subscription?.plan ?? entitlements?.plan
  const currentPlan = rawPlan != null ? normalizePlan(rawPlan) : subscriptionQuery.isError || entitlementsQuery.isError ? null : normalizePlan('free')

  async function refreshBilling() {
    await Promise.all([
      plansQuery.refetch(),
      subscriptionQuery.refetch(),
      entitlementsQuery.refetch(),
    ])
    toast.success('Billing data refreshed')
  }

  async function subscribe(tier: string) {
    const normalizedTier = normalizePlan(tier)

    const checkoutTiers: readonly CheckoutTier[] = ['starter', 'pro']
    if (!checkoutTiers.includes(normalizedTier as CheckoutTier)) {
      toast.info('This plan does not require checkout.')
      return
    }

    setProcessingTier(normalizedTier)
    try {
      const order = await createCashfreeSubscription(normalizedTier as CheckoutTier)
      if (!order.auth_link) {
        setProcessingTier(null)
        toast.error('Cashfree checkout link not received. Please try again.')
        return
      }
      // Store subscription_id in sessionStorage so we can verify after Cashfree redirects back.
      sessionStorage.setItem('cashfree_pending_sub', order.subscription_id)
      window.location.href = order.auth_link
    } catch (error) {
      setProcessingTier(null)
      toast.error(error instanceof Error ? error.message : 'Subscription could not be started')
    }
  }

  async function buyAddonPack(tier: string) {
    setProcessingTier(tier)
    try {
      const order = await buyAddon(tier)
      if (order.granted) {
        await queryClient.invalidateQueries({ queryKey: queryKeys.entitlements })
        toast.success('Add-on credits added to your wallet')
        return
      }
      if (!order.payment_link) {
        toast.error('Cashfree payment link not received. Please try again.')
        return
      }
      // Store link_id in sessionStorage so the wallet is credited after Cashfree redirects back.
      sessionStorage.setItem('cashfree_pending_addon', order.link_id)
      window.location.href = order.payment_link
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Add-on purchase could not be started')
    } finally {
      setProcessingTier(null)
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-[#0b0d10]">
      <PageHeader
        title="Billing"
        description="Plans and payment controls."
        info="Plans, usage, and payment controls for this workspace."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="cursor-pointer border-white/8 bg-white/[0.03] text-zinc-200 hover:bg-white/[0.06] disabled:cursor-not-allowed"
              onClick={refreshBilling}
              disabled={plansQuery.isFetching || subscriptionQuery.isFetching || entitlementsQuery.isFetching}
            >
              <RefreshCw
                className={`mr-2 h-4 w-4 ${
                  plansQuery.isFetching || subscriptionQuery.isFetching || entitlementsQuery.isFetching
                    ? 'animate-spin'
                    : ''
                }`}
              />
              Refresh
            </Button>
          </div>
        }
      />

      <main className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 py-4 sm:px-6">
        {hasError ? (
          <BillingErrorBanner
            onRetry={refreshBilling}
            loading={
              plansQuery.isFetching || subscriptionQuery.isFetching || entitlementsQuery.isFetching
            }
          />
        ) : null}

        <section className="space-y-3">
          <div className="flex flex-col gap-1.5 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-sm font-semibold text-white">Workspace Usage</h2>
              <p className="text-xs text-zinc-500">
                Customer-visible meters for the current billing cycle.
              </p>
            </div>
            {/* removed workspace badge per request */}
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {METER_CONFIGS.map((config) => (
              <UsageMeterCard
                key={config.key}
                config={config}
                meter={entitlements?.meters?.[config.key]}
                loading={entitlementsQuery.isLoading}
              />
            ))}
          </div>
        </section>

        <CompileCreditAddonPanel
          wallet={entitlements?.wallet ?? { compile_credits: 0, human_edit_tokens: 0, ai_usage_credits: 0 }}
          loading={entitlementsQuery.isLoading}
          processingTier={processingTier}
          onBuy={(tier) => void buyAddonPack(tier)}
        />

        <section className="space-y-3">
          <div className="flex flex-col gap-1.5 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-sm font-semibold text-white">Subscription Plans</h2>
              <p className="text-xs text-zinc-500">
                Upgrade limits without changing local workflow creation.
              </p>
            </div>
          </div>

          {plansQuery.isLoading ? (
            <PlanSkeletonGrid />
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {plans.map((plan) => (
                <PlanCard
                  key={plan.tier}
                  plan={plan}
                  currentPlan={currentPlan}
                  processingTier={processingTier}
                  onSubscribe={subscribe}
                />
              ))}
            </div>
          )}
        </section>

        <PaymentOperationsPanel
          currentPlan={currentPlan}
          currentPeriodEnd={subscription?.current_period_end}
        />
      </main>
    </div>
  )
}

function UsageMeterCard({
  config,
  meter,
  loading,
}: {
  config: UsageMeterConfig
  meter?: EntitlementMeter
  loading?: boolean
}) {
  const Icon = config.icon
  const percent = meterPercent(meter)
  const tone = meterTone(meter)
  const progressClass =
    tone === 'danger'
      ? 'bg-red-500'
      : tone === 'warning'
        ? 'bg-amber-400'
        : 'bg-cyan-400/80'
  const statusLabel =
    tone === 'danger' ? 'Exhausted' : tone === 'warning' ? 'Near limit' : meter?.unlimited ? 'Unlimited' : 'Available'

  return (
    <Card className="gap-0 border-white/8 bg-white/[0.025] py-0 shadow-none">
      <CardContent className="flex min-h-[9.25rem] flex-col p-4">
        <div className="flex min-h-9 items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-medium text-white">{config.label}</p>
            <p className="mt-1 truncate text-[11px] leading-none text-zinc-500">
              {config.description}
            </p>
          </div>
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/8 bg-white/[0.03] text-zinc-400">
            <Icon className="h-3.5 w-3.5" />
          </div>
        </div>

        {loading ? (
          <div className="mt-auto space-y-3 pt-5">
            <div className="h-7 w-24 animate-pulse rounded bg-white/8" />
            <div className="h-1.5 w-full animate-pulse rounded-full bg-white/8" />
            <div className="flex items-center justify-between">
              <div className="h-3 w-16 animate-pulse rounded bg-white/8" />
              <div className="h-3 w-24 animate-pulse rounded bg-white/8" />
            </div>
          </div>
        ) : (
          <>
            <div className="mt-5 flex items-end justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-baseline gap-1.5 tabular-nums">
                  <span className="text-2xl font-semibold leading-none tracking-tight text-white">
                    {formatMeterValue(meter?.used, config.key)}
                  </span>
                  <span className="text-sm font-medium leading-none text-zinc-600">/</span>
                  <span className="truncate text-sm font-medium leading-none text-zinc-300">
                    {formatMeterValue(meter?.limit, config.key)}
                  </span>
                </div>
              </div>
              <Badge
                variant="outline"
                className={
                  tone === 'danger'
                    ? 'h-6 rounded-full border-red-500/40 bg-red-500/10 px-2 text-[11px] text-red-200'
                    : tone === 'warning'
                      ? 'h-6 rounded-full border-amber-500/40 bg-amber-500/10 px-2 text-[11px] text-amber-200'
                      : 'h-6 rounded-full border-cyan-400/30 bg-white/[0.03] px-2 text-[11px] text-cyan-300'
                }
              >
                {statusLabel}
              </Badge>
            </div>
            <div className="mt-auto pt-4">
              <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                <div
                  className={`h-1.5 rounded-full transition-all ${progressClass}`}
                  style={{ width: meter?.unlimited ? '100%' : `${percent}%` }}
                />
              </div>
              <div className="mt-3 flex items-center justify-between gap-3 text-[11px] leading-none text-zinc-500">
                <span className="shrink-0">{meter?.unlimited ? 'No cap' : `${percent}% used`}</span>
                <span className="truncate text-right">
                  {formatMeterValue(meter?.remaining, config.key)} remaining
                </span>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function formatTokens(tokens: number) {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(tokens)
}

type CreditWallet = {
  compile_credits: number
  human_edit_tokens: number
  ai_usage_credits: number
}

function CompileCreditAddonPanel({
  wallet,
  loading,
  processingTier,
  onBuy,
}: {
  wallet: CreditWallet
  loading?: boolean
  processingTier: string | null
  onBuy: (tier: string) => void
}) {
  const addonsQuery = useQuery({ queryKey: queryKeys.billingAddons, queryFn: listAddons })
  const addons = useMemo(() => addonsQuery.data?.addons ?? [], [addonsQuery.data])

  return (
    <Card className="gap-0 border-white/8 bg-white/[0.025] py-0 shadow-none">
      <CardHeader className="border-b border-white/8 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base text-white">Compile Credit Add-Ons</CardTitle>
            <p className="mt-0.5 text-xs text-zinc-500">
              One-time top-ups that never expire — used automatically after your monthly allowance runs out.
              {wallet.compile_credits > 0 || wallet.ai_usage_credits > 0 ? (
                <span className="ml-1 text-emerald-300/90">
                  Wallet balance: {wallet.compile_credits.toLocaleString()} compiles ·{' '}
                  {formatTokens(wallet.ai_usage_credits)} AI Usage Credits.
                </span>
              ) : null}
            </p>
          </div>
          <div className="rounded-md border border-white/8 bg-black/20 p-1.5 text-zinc-400">
            <Code2 className="h-3.5 w-3.5" />
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        {addonsQuery.isLoading || loading ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4 pt-4">
            {[0, 1, 2, 3].map((card) => (
              <div key={card} className="min-h-[13rem] animate-pulse rounded-lg border border-white/8 bg-black/20" />
            ))}
          </div>
        ) : addonsQuery.isError ? (
          <p className="pt-4 text-sm text-red-300">{errorMessage(addonsQuery.error)}</p>
        ) : (
          <div className="grid gap-3 pt-4 md:grid-cols-2 xl:grid-cols-4">
            {addons.map((addon) => {
              const buying = processingTier === addon.tier
              const busy = Boolean(processingTier)
              return (
                <div
                  key={addon.tier}
                  className={`flex min-h-[13rem] flex-col rounded-lg border p-4 ${
                    wallet.compile_credits > 0
                      ? 'border-emerald-500/30 bg-white/[0.03]'
                      : 'border-white/8 bg-white/[0.02]'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-sm font-semibold text-white">{addon.name}</p>
                    <Badge
                      variant="outline"
                      className="h-6 shrink-0 rounded-full border-white/10 bg-white/[0.03] px-2 text-[11px] text-zinc-400"
                    >
                      One-time
                    </Badge>
                  </div>
                  <div className="mt-1.5 flex items-baseline gap-1.5">
                    <span className="text-lg font-semibold tabular-nums text-white">
                      ₹{addon.amount.toLocaleString()}
                    </span>
                    <span className="text-xs text-zinc-600">pay once</span>
                  </div>
                  <div className="mt-3 flex flex-1 flex-col gap-1.5">
                    {(addon.features ?? []).map((feature) => (
                      <div key={feature} className="flex items-center gap-2 text-xs leading-5 text-zinc-300">
                        <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-cyan-300" />
                        {feature}
                      </div>
                    ))}
                  </div>
                  <div className="mt-auto flex gap-2 pt-3">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      className="h-8 flex-1 cursor-pointer border-cyan-400/30 bg-white/[0.04] text-cyan-200 hover:bg-white/[0.06]"
                      onClick={() => onBuy(addon.tier)}
                    >
                      {buying ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      {buying ? 'Redirecting…' : 'Buy once'}
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function PlanCard({
  plan,
  currentPlan,
  processingTier,
  onSubscribe,
}: {
  plan: Plan
  currentPlan: string | null
  processingTier: string | null
  onSubscribe: (tier: string) => void
}) {
  const [contactOpen, setContactOpen] = useState(false)
  const tier = normalizePlan(plan.tier)
  const isCurrent = tier === currentPlan
  const isRecommended = tier === 'starter'
  const isEnterprise = tier === 'enterprise'
  const isPaidCheckoutPlan = tier === 'starter' || tier === 'pro'
  const isProcessing = processingTier === tier
  const disabled =
    isCurrent ||
    tier === 'free' ||
    (!isPaidCheckoutPlan && !isEnterprise) ||
    Boolean(processingTier)

  let buttonLabel = isCurrent ? 'Current plan' : `Choose ${displayPlanName(tier)}`
  if (tier === 'free' && !isCurrent) buttonLabel = 'Free plan'
  if (isEnterprise) buttonLabel = 'Contact us'
  if (isProcessing) buttonLabel = 'Redirecting to Cashfree...'

  return (
    <>
      <Card
        className={`flex min-h-[22.75rem] flex-col gap-0 border py-0 shadow-none ${
          isEnterprise
            ? 'border-cyan-400/25 bg-white/[0.025]'
            : isRecommended
              ? 'border-cyan-400/25 bg-white/[0.025]'
              : isCurrent
                ? 'border-emerald-500/30 bg-white/[0.025]'
                : 'border-white/8 bg-white/[0.025]'
        }`}
      >
        <CardHeader className="p-4 pb-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <CardTitle className="text-base text-white">{plan.name || displayPlanName(tier)}</CardTitle>
              <p className="mt-0.5 text-xs text-zinc-500">{displayPlanName(tier)} workspace limits</p>
            </div>
            <div className="flex flex-col items-end gap-1.5">
              {isCurrent ? (
                <Badge className="border-emerald-500/30 bg-white/[0.03] text-emerald-300" variant="outline">
                  Current
                </Badge>
              ) : null}
              {isRecommended && !isCurrent ? (
                <Badge className="border-cyan-400/30 bg-white/[0.03] text-cyan-300" variant="outline">
                  Recommended
                </Badge>
              ) : null}
              {isEnterprise && !isCurrent ? (
                <Badge className="border-teal-300/30 bg-white/[0.03] text-teal-300" variant="outline">
                  Custom
                </Badge>
              ) : null}
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-1 flex-col p-4 pt-0">
          <div>
            <div className="flex items-baseline gap-2">
              <span className="text-xl font-semibold text-white">{formatPrice(plan)}</span>
              <span className="text-xs text-zinc-600">/ {formatPeriod(plan)}</span>
            </div>
          </div>

          <div className="mt-4 flex flex-1 flex-col gap-2">
            {(plan.features || []).slice(0, 7).map((feature) => (
              <div key={feature} className="flex gap-2 text-xs leading-5 text-zinc-300">
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cyan-300" />
                <span>{feature}</span>
              </div>
            ))}
          </div>

          <div className="mt-auto pt-4">
            {isEnterprise && !isCurrent ? (
              <Button
                className="h-8 w-full cursor-pointer border border-cyan-400/30 bg-white/[0.04] text-cyan-200 hover:bg-white/[0.06]"
                onClick={() => setContactOpen(true)}
              >
                <Mail className="mr-2 h-4 w-4" />
                {buttonLabel}
              </Button>
            ) : (
              <Button
                className={`h-8 w-full disabled:cursor-not-allowed ${
                  disabled
                    ? ''
                    : isRecommended
                      ? 'cursor-pointer border border-cyan-400/30 bg-white/[0.04] text-cyan-200 hover:bg-white/[0.06]'
                      : 'cursor-pointer border-white/8 bg-white/[0.03] text-zinc-200 hover:bg-white/[0.06]'
                }`}
                variant={isRecommended && !disabled ? 'default' : 'outline'}
                disabled={disabled}
                onClick={() => onSubscribe(tier)}
              >
                {isProcessing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                {buttonLabel}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
      {isEnterprise ? (
        <EnterpriseContactDialog open={contactOpen} onOpenChange={setContactOpen} />
      ) : null}
    </>
  )
}

function EnterpriseContactDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-white/10 bg-[#0d0f12] p-0 text-zinc-100 sm:max-w-md">
        <DialogHeader className="border-b border-white/8 px-4 py-3 text-left">
          <DialogTitle className="text-white">Contact Conxa</DialogTitle>
          <DialogDescription className="text-zinc-500">
            Use either channel for Enterprise pricing, custom limits, and procurement.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-2.5 px-4 py-4">
          <a
            className="flex items-center gap-3 rounded-lg border border-white/8 bg-black/20 p-3 text-sm text-zinc-200 transition-colors hover:border-cyan-400/30 hover:text-cyan-300"
            href={`mailto:${SALES_EMAIL}?subject=Conxa%20Enterprise%20plan&body=Hi%20Conxa%20team%2C%0A%0AI%20want%20to%20talk%20about%20an%20Enterprise%20plan.`}
          >
            <span className="rounded-md border border-white/8 bg-white/[0.03] p-1.5 text-cyan-300">
              <Mail className="h-3.5 w-3.5" />
            </span>
            <span className="min-w-0">
              <span className="block text-xs text-zinc-500">Email</span>
              <span className="block truncate font-medium">{SALES_EMAIL}</span>
            </span>
          </a>
          <a
            className="flex items-center gap-3 rounded-lg border border-white/8 bg-black/20 p-3 text-sm text-zinc-200 transition-colors hover:border-cyan-400/30 hover:text-cyan-300"
            href={`tel:${SALES_PHONE_TEL}`}
          >
            <span className="rounded-md border border-white/8 bg-white/[0.03] p-1.5 text-cyan-300">
              <PhoneCall className="h-3.5 w-3.5" />
            </span>
            <span className="min-w-0">
              <span className="block text-xs text-zinc-500">Phone</span>
              <span className="block truncate font-medium">{SALES_PHONE_DISPLAY}</span>
            </span>
          </a>
        </div>
        <div className="flex justify-end border-t border-white/8 bg-white/[0.03] px-4 py-3">
          <DialogClose asChild>
            <Button
              variant="outline"
              className="h-8 cursor-pointer border-white/8 bg-black/20 text-zinc-200 hover:bg-white/[0.06]"
            >
              Close
            </Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function PaymentOperationsPanel({
  currentPlan,
  currentPeriodEnd,
}: {
  currentPlan: string | null
  currentPeriodEnd?: number | null
}) {
  return (
    <Card className="gap-0 border-white/8 bg-white/[0.025] py-0 shadow-none">
      <CardHeader className="border-b border-white/8 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base text-white">Billing Operations</CardTitle>
            <p className="mt-0.5 text-xs text-zinc-500">Payment readiness and account timing.</p>
          </div>
          <div className="rounded-md border border-white/8 bg-black/20 p-1.5 text-zinc-400">
            <CreditCard className="h-3.5 w-3.5" />
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid items-start gap-3 p-4 lg:grid-cols-2">
        <div className="rounded-md border border-white/8 bg-black/20 p-2.5">
          <p className="text-xs font-medium uppercase tracking-normal text-zinc-500">Account Timing</p>
          <div className="mt-2 grid gap-2">
            <InfoRow label="Active plan" value={currentPlan === null ? 'Unknown — retry above' : displayPlanName(currentPlan)} />
            <InfoRow label="Usage reset" value={formatUnixDate(currentPeriodEnd)} />
          </div>
        </div>

        <div className="rounded-md border border-white/8 bg-black/20 p-2.5">
          <p className="text-sm font-medium text-cyan-300">Metering policy</p>
          <p className="mt-1.5 text-xs text-zinc-500">
            Workflow creation and recording stay unlimited. Only seats, installer slots, compile credits, Execute seats, and AI Usage Credits are customer-visible meters.
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-white/8 pb-2 last:border-b-0 last:pb-0">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="truncate text-right text-xs font-medium text-zinc-200">{value}</span>
    </div>
  )
}

function BillingErrorBanner({ onRetry, loading }: { onRetry: () => void; loading?: boolean }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3.5 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-300" />
        <div>
          <p className="font-medium text-red-100">Billing data could not be loaded.</p>
          <p className="mt-1 text-sm text-red-100/75">
            Retry after confirming the cloud backend is running and reachable.
          </p>
        </div>
      </div>
      <Button
        variant="outline"
        size="sm"
        className="w-fit cursor-pointer border-red-400/40 bg-transparent text-red-100 hover:bg-red-500/10 disabled:cursor-not-allowed"
        onClick={onRetry}
        disabled={loading}
      >
        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
        Retry
      </Button>
    </div>
  )
}

function PlanSkeletonGrid() {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {[0, 1, 2, 3].map((item) => (
        <Card key={item} className="min-h-[22.75rem] gap-0 border-white/8 bg-black/20 py-0 shadow-none">
          <CardContent className="p-4">
            <div className="h-4 w-20 animate-pulse rounded bg-white/8" />
            <div className="mt-4 h-7 w-28 animate-pulse rounded bg-white/8" />
            <div className="mt-5 space-y-2">
              <div className="h-3.5 w-full animate-pulse rounded bg-white/8" />
              <div className="h-3.5 w-5/6 animate-pulse rounded bg-white/8" />
              <div className="h-3.5 w-4/6 animate-pulse rounded bg-white/8" />
              <div className="h-3.5 w-3/4 animate-pulse rounded bg-white/8" />
            </div>
            <div className="mt-5 h-8 w-full animate-pulse rounded bg-white/8" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
