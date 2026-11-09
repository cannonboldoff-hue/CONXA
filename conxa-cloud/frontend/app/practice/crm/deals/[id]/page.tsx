"use client"
import { useParams } from "next/navigation"
import { useState } from "react"
import { PRODUCTS, money, usePractice, type Deal } from "../../../_data/store"
import { Btn, Card, Field, H1, Loading, Select, Td, Th } from "../../../_ui"

export default function Deal() {
  const { id } = useParams<{ id: string }>()
  const { s, update } = usePractice()
  const [product, setProduct] = useState(PRODUCTS[0].name)
  const [qty, setQty] = useState(1)
  const [confirm, setConfirm] = useState(false)
  if (!s) return <Loading />
  const deal = s.deals.find((d) => d.id === id)
  if (!deal) return <p className="text-sm">This deal does not exist. <a className="underline" href="/practice/crm/contacts">Back to customers</a></p>
  const contact = s.contacts.find((c) => c.id === deal.contactId)
  const total = deal.items.reduce((t, i) => t + i.qty * i.price, 0)
  const patch = (fn: (d: Deal) => void) => update((st) => { const d = st.deals.find((x) => x.id === id); if (d) fn(d) })
  return (
    <>
      <H1>Deal for {contact?.name ?? "customer"}</H1>
      <Card className="mb-4">
        <div className="flex items-end gap-3">
          <Field label="Product"><Select data-practice="deal-product" value={product} onChange={(e) => setProduct(e.target.value)}>{PRODUCTS.map((p) => <option key={p.name}>{p.name}</option>)}</Select></Field>
          <Field label="How many"><input data-practice="deal-qty" type="number" min={1} value={qty} onChange={(e) => setQty(Math.max(1, Number(e.target.value) || 1))} className="w-20 rounded-md border border-slate-300 px-3 py-2 text-sm" /></Field>
          <Btn data-practice="deal-add" disabled={deal.quoteSent} className="mb-3" onClick={() => patch((d) => d.items.push({ product, qty, price: PRODUCTS.find((p) => p.name === product)!.price }))}>Add to quote</Btn>
        </div>
        <table className="w-full"><thead><tr><Th>Item</Th><Th>How many</Th><Th>Price</Th></tr></thead>
          <tbody>{deal.items.map((i, n) => <tr key={n}><Td>{i.product}</Td><Td>{i.qty}</Td><Td>{money(i.price * i.qty)}</Td></tr>)}</tbody></table>
        <p className="mt-3 text-right text-sm font-semibold" data-practice="deal-total">Total {money(total)}</p>
      </Card>
      {!deal.quoteSent && !confirm && <Btn data-practice="send-quote" disabled={deal.items.length === 0} onClick={() => setConfirm(true)}>Send quote</Btn>}
      {confirm && !deal.quoteSent && (
        <Card className="max-w-md border-amber-300"><p className="mb-3 text-sm">Send this quote to {contact?.email}? The customer will see it right away.</p>
          <div className="flex gap-2"><Btn data-practice="send-quote-yes" onClick={() => { patch((d) => { d.quoteSent = true; d.stage = "Quoted" }); setConfirm(false) }}>Yes, send it</Btn><Btn kind="plain" data-practice="send-quote-no" onClick={() => setConfirm(false)}>Not yet</Btn></div></Card>)}
      {deal.quoteSent && (
        <div className="flex items-end gap-3"><p className="mb-3 text-sm text-green-700">Quote sent.</p>
          <Field label="Deal stage"><Select data-practice="deal-stage" value={deal.stage} onChange={(e) => patch((d) => { d.stage = e.target.value as Deal["stage"] })}><option>Quoted</option><option>Won</option></Select></Field></div>)}
      {deal.stage === "Won" && <p data-practice="deal-won" className="mt-2 text-sm font-semibold text-green-700">Deal won. Nice work.</p>}
    </>
  )
}
