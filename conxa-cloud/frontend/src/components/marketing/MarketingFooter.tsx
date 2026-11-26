import Link from 'next/link'
import Image from 'next/image'

export function MarketingFooter() {
  return (
    <footer className="border-t border-white/6 bg-[#06080b] px-6 py-12">
      <div className="mx-auto max-w-6xl">
        <div className="flex flex-col gap-10 sm:flex-row sm:items-start sm:justify-between">
          {/* Brand */}
          <div className="flex items-center gap-3">
            <div className="relative h-14 w-14 overflow-hidden rounded-2xl border border-white/10 bg-white/5 p-2 shadow-[0_0_0_1px_rgba(255,255,255,0.02)]">
              <Image
                src="/conxa-icon.png"
                alt="CONXA"
                fill
                priority
                sizes="56px"
                className="object-contain"
              />
            </div>
            <div className="flex flex-col">
              <span className="text-xs uppercase tracking-[0.28em] text-[#6b7280]">Conxa</span>
              <span className="text-sm text-[#9ba3af]">Browser tasks your AI can do reliably</span>
            </div>
          </div>

          {/* Links */}
          <div className="grid grid-cols-2 gap-8 sm:grid-cols-3">
            <div className="flex flex-col gap-3">
              <p className="text-xs font-semibold uppercase tracking-widest text-[#6b7280]">Product</p>
              <Link href="/#film" className="text-sm text-[#9ba3af] hover:text-white">See it</Link>
              <Link href="/#work" className="text-sm text-[#9ba3af] hover:text-white">Use cases</Link>
              <Link href="/#data" className="text-sm text-[#9ba3af] hover:text-white">Security</Link>
              <Link href="/#faq" className="text-sm text-[#9ba3af] hover:text-white">FAQ</Link>
              <Link href="/docs" className="text-sm text-[#9ba3af] hover:text-white">Docs</Link>
              <Link href="/sign-up" className="text-sm text-[#9ba3af] hover:text-white">Get started</Link>
            </div>
            <div className="flex flex-col gap-3">
              <p className="text-xs font-semibold uppercase tracking-widest text-[#6b7280]">Company</p>
              <Link href="/docs/support" className="text-sm text-[#9ba3af] hover:text-white">Contact</Link>
              <Link href="/sign-in" className="text-sm text-[#9ba3af] hover:text-white">Sign in</Link>
            </div>
            <div className="flex flex-col gap-3">
              <p className="text-xs font-semibold uppercase tracking-widest text-[#6b7280]">Legal</p>
              <Link href="/docs/security" className="text-sm text-[#9ba3af] hover:text-white">Security</Link>
              <Link href="/docs/privacy" className="text-sm text-[#9ba3af] hover:text-white">Privacy</Link>
              <Link href="/docs/terms" className="text-sm text-[#9ba3af] hover:text-white">Terms</Link>
              <Link href="/docs/billing" className="text-sm text-[#9ba3af] hover:text-white">Billing</Link>
            </div>
          </div>
        </div>

        <div className="mt-10 flex flex-col items-start justify-between gap-4 border-t border-white/6 pt-8 sm:flex-row sm:items-center">
          <p className="text-xs text-[#6b7280]">© {new Date().getFullYear()} CONXA. All rights reserved.</p>

        </div>
      </div>
    </footer>
  )
}
