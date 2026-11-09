"use client"
import { money, usePractice } from "../../_data/store"
import { H1, Loading, Td, Th } from "../../_ui"

export default function InvoiceApprovals() {
  const { s } = usePractice()
  if (!s) return <Loading />
  return (
    <>
      <H1>Invoices</H1>
      <table className="w-full border border-slate-200 bg-white"><thead><tr><Th>Invoice</Th><Th>Orders</Th><Th>Total</Th><Th>Status</Th></tr></thead>
        <tbody>{s.invoices.map((i) => <tr key={i.id}><Td>{i.id}</Td><Td>{i.orderIds.join(", ")}</Td><Td>{money(i.total)}</Td><Td>{i.status}</Td></tr>)}</tbody></table>
      {s.invoices.length === 0 && <p className="mt-4 text-sm text-slate-600">No invoices yet.</p>}
    </>
  )
}
