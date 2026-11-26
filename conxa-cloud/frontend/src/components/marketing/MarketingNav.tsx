'use client'

import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import Link from 'next/link'
import { GlowButton } from './primitives/GlowButton'
import { BOOKING_HREF } from './home/ui'

const links = [
  { label: 'See it', href: '/#film' },
  { label: 'Use cases', href: '/#work' },
  { label: 'Trust', href: '/#trust' },
  { label: 'Compare', href: '/#compare' },
  { label: 'Security', href: '/#data' },
  { label: 'Pricing', href: '/#pricing' },
  { label: 'Docs', href: '/docs' },
]

/** Homepage section ids behind the anchor links — the scroll-spy tracks these.
 *  Docs is a separate route, so its link never highlights. */
const SECTION_IDS = links.flatMap((l) => (l.href.startsWith('/#') ? [l.href.slice(2)] : []))

export function MarketingNav() {
  const [scrolled, setScrolled] = useState(false)
  const [active, setActive] = useState<string | null>(null)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Scroll-spy: the link whose section currently crosses the reading band is
  // lit, so the single fixed bar doubles as a you-are-here indicator on the
  // long homepage. Near the hero nothing is highlighted.
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting)
        if (visible.length > 0) {
          setActive(visible[visible.length - 1].target.id)
        } else if (window.scrollY < window.innerHeight / 2) {
          setActive(null)
        }
      },
      { rootMargin: '-40% 0px -55% 0px' },
    )
    for (const id of SECTION_IDS) {
      const el = document.getElementById(id)
      if (el) observer.observe(el)
    }
    return () => observer.disconnect()
  }, [])

  return (
    <motion.header
      className="fixed top-0 z-50 w-full transition-all duration-500"
      animate={{
        backgroundColor: scrolled ? 'rgba(6,8,11,0.92)' : 'rgba(6,8,11,0)',
        backdropFilter: scrolled ? 'blur(16px)' : 'blur(0px)',
        borderBottomColor: scrolled ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0)',
      }}
      style={{ borderBottomWidth: 1, borderBottomStyle: 'solid' }}
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2.5 group">
          <img
            src="/conxa-icon.png"
            alt="CONXA"
            width={32}
            height={32}
            className="h-8 w-8 rounded-lg object-contain transition-transform duration-200 group-hover:scale-[1.02]"
          />
          <span className="text-base font-semibold tracking-tight text-white">CONXA</span>
        </Link>

        {/* Desktop links */}
        <nav className="hidden items-center gap-8 md:flex">
          {links.map((l) => {
            const id = l.href.startsWith('/#') ? l.href.slice(2) : null
            return (
              <Link
                key={l.label}
                href={l.href}
                aria-current={id && id === active ? 'location' : undefined}
                className={`text-sm transition-colors ${id === active ? 'text-white' : 'text-[#9ba3af] hover:text-white'}`}
              >
                {l.label}
              </Link>
            )
          })}
        </nav>

        {/* CTA */}
        <div className="hidden items-center gap-3 md:flex">
          <Link
            href="/sign-in"
            className="text-sm text-[#9ba3af] transition-colors hover:text-white"
          >
            Sign in
          </Link>
          <GlowButton href={BOOKING_HREF}>Book a walkthrough</GlowButton>
        </div>

        {/* Mobile hamburger */}
        <button
          className="flex md:hidden flex-col gap-1.5 p-1"
          onClick={() => setMobileOpen((v) => !v)}
          aria-label="Toggle menu"
        >
          <span className={`block h-0.5 w-5 bg-white transition-transform ${mobileOpen ? 'translate-y-2 rotate-45' : ''}`} />
          <span className={`block h-0.5 w-5 bg-white transition-opacity ${mobileOpen ? 'opacity-0' : ''}`} />
          <span className={`block h-0.5 w-5 bg-white transition-transform ${mobileOpen ? '-translate-y-2 -rotate-45' : ''}`} />
        </button>
      </div>

      {/* Mobile menu */}
      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-white/6 bg-[#06080b]/95 backdrop-blur-xl"
          >
            <div className="flex flex-col gap-1 px-6 py-4">
              {links.map((l) => {
                const id = l.href.startsWith('/#') ? l.href.slice(2) : null
                return (
                  <Link
                    key={l.label}
                    href={l.href}
                    aria-current={id && id === active ? 'location' : undefined}
                    className={`py-2 text-sm transition-colors hover:text-white ${id === active ? 'text-white' : 'text-[#9ba3af]'}`}
                    onClick={() => setMobileOpen(false)}
                  >
                    {l.label}
                  </Link>
                )
              })}
              <div className="mt-4 flex flex-col gap-2">
                <Link href="/sign-in" className="py-2 text-sm text-[#9ba3af] hover:text-white">
                  Sign in
                </Link>
                <GlowButton href={BOOKING_HREF}>Book a walkthrough</GlowButton>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.header>
  )
}
