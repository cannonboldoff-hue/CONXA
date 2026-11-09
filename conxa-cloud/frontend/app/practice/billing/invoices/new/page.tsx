"use client"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useState } from "react"
import { DISCOUNT_CODES, money, usePractice } from "../../../_data/store"
import { Btn, Card, Field, H1, Input, Loading } from "../../../_ui"

function Form() {
  const ids = (useSearchParams().get("orders") ?? "").split(",").filter(Boolean)
  const { s, update } = usePractice()
  const router = useRouter()
  const [code, setCode] = useState("")
  const [applied, setApplied] = useState("")
  if (!s) return <Loading />
  const orders = s.orders.filter((o) => ids.includes(o.id))
  const sub = orders.reduce((t, o) => t + o.amount, 0)
  const rate = DISCOUNT_CODES[applied] ?? 0
  const total = sub * (1 - rate)
  const submit = () => {
    update((d) => {
      d.invoices.push({ id: `INV-${d.nextId++}`, orderIds: ids, discountCode: applied, total, status: "Pending approval" })
      d.orders.forEach((o) => { if (ids.includes(o.id)) o.invoiced = true })
    })
    router.push("/practice/billing/approvals")
  }
  return (
    <>
      <H1>New invoice</H1>
      <Card className="max-w-lg">
        <ul className="mb-3 text-sm">{orders.map((o) => <li key={o.id}>{o.id} · {o.customer} · {money(o.amount)}</li>)}</ul>
        <div className="flex items-end gap-2"><Field label="Discount code"><Input data-practice="discount-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="Try SAVE10" /></Field>
          <Btn kind="plain" data-practice="discount-apply" className="mb-3" onClick={() => setApplied(code in DISCOUNT_CODES ? code : "")}>Apply</Btn></div>
        {code && applied === "" && <p className="mb-2 text-xs text-slate-500">Press Apply to check the code.</p>}
        <p className="mb-3 text-sm font-semibold" data-practice="invoice-total">Total {money(total)}{applied ? ` (${applied} applied)` : ""}</p>
        <Btn data-practice="invoice-submit" disabled={orders.length === 0} onClick={submit}>Send for approval</Btn>
      </Card>
    </>
  )
}

export default function NewInvoice() { return <Suspense fallback={<Loading />}><Form /></Suspense> }
