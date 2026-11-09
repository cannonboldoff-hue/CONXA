"use client"
import { useRouter } from "next/navigation"
import { useState } from "react"
import { money, usePractice } from "../../_data/store"
import { Btn, H1, Loading, Select, Td, Th } from "../../_ui"

export default function Orders() {
  const { s } = usePractice()
  const router = useRouter()
  const [month, setMonth] = useState("all")
  const [status, setStatus] = useState("all")
  const [picked, setPicked] = useState<string[]>([])
  if (!s) return <Loading />
  const rows = s.orders.filter((o) => (month === "all" || o.date.startsWith(month)) && (status === "all" || o.status === status))
  const exportCsv = () => {
    const csv = ["Order,Customer,Date,Amount,Status", ...rows.map((o) => `${o.id},${o.customer},${o.date},${o.amount},${o.status}`)].join("\n")
    const a = document.createElement("a")
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }))
    a.download = `orders-${month}.csv`
    a.click()
  }
  return (
    <>
      <H1>Orders</H1>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select data-practice="orders-month" value={month} onChange={(e) => { setMonth(e.target.value); setPicked([]) }} className="w-40"><option value="all">All months</option><option value="2026-09">September 2026</option><option value="2026-10">October 2026</option></Select>
        <Select data-practice="orders-status" value={status} onChange={(e) => { setStatus(e.target.value); setPicked([]) }} className="w-36"><option value="all">Any status</option><option>Paid</option><option>Unpaid</option></Select>
        <Btn kind="plain" data-practice="orders-export" onClick={exportCsv}>Download report</Btn>
        <Btn data-practice="orders-invoice" disabled={picked.length === 0} className="ml-auto" onClick={() => router.push(`/practice/billing/invoices/new?orders=${picked.join(",")}`)}>Create invoice</Btn>
      </div>
      <table className="w-full border border-slate-200 bg-white"><thead><tr><Th /><Th>Order</Th><Th>Customer</Th><Th>Date</Th><Th>Amount</Th><Th>Status</Th></tr></thead>
        <tbody>{rows.map((o) => (
          <tr key={o.id}><Td>{o.status === "Unpaid" && !o.invoiced ? <input type="checkbox" aria-label={`Select ${o.id}`} data-practice={`pick-${o.id}`} checked={picked.includes(o.id)} onChange={(e) => setPicked(e.target.checked ? [...picked, o.id] : picked.filter((x) => x !== o.id))} /> : null}</Td>
            <Td>{o.id}</Td><Td>{o.customer}</Td><Td>{o.date}</Td><Td>{money(o.amount)}</Td><Td>{o.status}</Td></tr>))}</tbody></table>
    </>
  )
}
