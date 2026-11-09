"use client"
import Link from "next/link"
import type { ReactNode } from "react"
import { usePractice } from "./_data/store"

const NAV = [
  { href: "/practice/crm/contacts", label: "Customers" },
  { href: "/practice/helpdesk/inbox", label: "Help desk" },
  { href: "/practice/billing/orders", label: "Billing" },
  { href: "/practice/people/leave/new", label: "People" },
]

export function PracticeShell({ children }: { children: ReactNode }) {
  const { reset } = usePractice()
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900" style={{ colorScheme: "light" }}>
      <div className="bg-amber-100 px-4 py-1.5 text-center text-xs text-amber-900">
        Practice company. Nothing here is real, and you cannot break anything.
      </div>
      <header className="flex flex-wrap items-center gap-6 border-b border-slate-200 bg-white px-6 py-3">
        <Link href="/practice" className="text-base font-semibold text-indigo-700">Northwind Practice</Link>
        <nav className="flex flex-wrap gap-4 text-sm">
          {NAV.map((n) => <Link key={n.href} href={n.href} className="text-slate-700 hover:text-indigo-700">{n.label}</Link>)}
        </nav>
        <button type="button" onClick={reset} className="ml-auto text-xs text-slate-500 underline">Reset practice data</button>
      </header>
      <main className="mx-auto max-w-4xl px-6 py-8">{children}</main>
    </div>
  )
}
