import Link from 'next/link'
import { BOOKING_HREF, ghostBtn, primaryBtn, WRAP } from './ui'

const STEPS = ['c1', 'c2', 'c3', 'c4', 'c5'] as const

export function HomeCta() {
  return (
    <section id="book" aria-labelledby="book-title" className="border-t border-white/10">
      <div className={`${WRAP} flex flex-col items-start gap-6 py-16`}>
        <h2
          id="book-title"
          className="m-0 text-[clamp(2.5rem,5.4vw,4.5rem)] font-semibold leading-[1.03] tracking-[-0.035em]"
        >
          Bring one task you repeat.
        </h2>
        <div role="img" aria-label="Five steps lighting up one after another" className="grid w-full max-w-[640px] grid-cols-5 gap-2">
          {STEPS.map((s) => (
            <span
              key={s}
              className="h-2.5 rounded-[5px] bg-cyan-400"
              style={{ animation: `hx-${s} 5s infinite`, ...(s === 'c5' ? { backgroundColor: '#4ade80' } : null) }}
            />
          ))}
        </div>
        <div className="flex flex-wrap gap-3">
          <Link href={BOOKING_HREF} className={primaryBtn('lg')}>
            Book a walkthrough
          </Link>
          <Link href="/sign-up" className={ghostBtn('lg')}>
            Start free for 30 days
          </Link>
        </div>
      </div>
    </section>
  )
}
