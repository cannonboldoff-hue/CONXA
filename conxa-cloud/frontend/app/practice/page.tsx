import Link from "next/link"
import { Card, H1 } from "./_ui"

const TILES = [
  { href: "/practice/crm/contacts/new", title: "Customers", body: "Add customers, open deals and send quotes." },
  { href: "/practice/helpdesk/inbox", title: "Help desk", body: "Answer and close support requests." },
  { href: "/practice/billing/orders", title: "Billing", body: "Review orders, export reports and send invoices." },
  { href: "/practice/people/leave/new", title: "People", body: "Request time off and approve it." },
]

export default function PracticeHome() {
  return (
    <>
      <H1>Welcome to Northwind</H1>
      <div className="grid gap-4 sm:grid-cols-2">
        {TILES.map((t) => (
          <Link key={t.href} href={t.href} data-practice={`tile-${t.title.toLowerCase().replace(/\s+/g, "-")}`}>
            <Card className="h-full hover:border-indigo-400"><h2 className="font-semibold">{t.title}</h2><p className="mt-1 text-sm text-slate-600">{t.body}</p></Card>
          </Link>
        ))}
      </div>
    </>
  )
}
