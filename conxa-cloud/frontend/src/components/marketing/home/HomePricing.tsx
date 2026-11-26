'use client'

import Link from 'next/link'
import { formatPrice, normalizePlan } from '@/billing/billingData'
import { STATIC_PLANS } from '../sections/PricingTable'
import { BAND, BOOKING_HREF, H2, WRAP, ghostBtn, primaryBtn } from './ui'

/** One line per plan, in plain words. The price and name come from STATIC_PLANS so they cannot drift. */
const LINE: Record<string, string> = {
  free: '1 person · 25 builds a month',
  starter: '3 builders · share outside your company',
  pro: '10 builders · full dashboard',
  enterprise: 'Your brand · your own AI account',
}

export function HomePricing() {
  return (
    <section id="pricing" aria-labelledby="price-title" className={BAND}>
      <div className={`${WRAP} py-14`}>
        <h2 id="price-title" className={H2}>
          Run it as often as you like.
        </h2>
        <div className="mt-6 grid grid-cols-[repeat(auto-fit,minmax(min(250px,100%),1fr))] gap-3.5">
          {STATIC_PLANS.map((plan) => {
            const tier = normalizePlan(plan.tier)
            const free = tier === 'free'
            return (
              <div
                key={tier}
                className={`flex flex-col gap-3 rounded-[14px] border bg-[#06080b] p-[22px] ${
                  free ? 'border-cyan-400/60' : 'border-white/14'
                }`}
              >
                <h3 className="m-0 text-lg font-semibold">{plan.name}</h3>
                <p className="m-0">
                  <span className="text-[30px] font-semibold">{free ? '₹0' : formatPrice(plan)}</span>{' '}
                  <span className="text-[#9ba3af]">{free ? '30 days' : tier === 'enterprise' ? '' : 'a month'}</span>
                </p>
                <p className="m-0 text-[15px] text-[#c9ced6]">{LINE[tier]}</p>
                {free ? (
                  <Link href="/sign-up" className={`${primaryBtn('sm')} mt-auto`}>
                    Start free
                  </Link>
                ) : (
                  <Link href={BOOKING_HREF} className={`${ghostBtn('sm')} mt-auto`}>
                    Talk to us
                  </Link>
                )}
              </div>
            )
          })}
        </div>
        <p className="m-0 mt-3.5 text-sm text-[#9ba3af]">
          Prices in rupees. A build is when Conxa turns your recording into a task. Runs are never counted. When a
          trial ends, tasks you already installed keep working.
        </p>
      </div>
    </section>
  )
}
