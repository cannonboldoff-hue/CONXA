"use client"
import { useRouter } from "next/navigation"
import { useState } from "react"
import { usePractice } from "../../../_data/store"
import { Btn, Card, Field, H1, Input, Loading, Select } from "../../../_ui"

export default function NewLeave() {
  const { s, update } = usePractice()
  const router = useRouter()
  const [f, setF] = useState({ type: "Annual", from: "", to: "" })
  if (!s) return <Loading />
  const days = f.from && f.to ? Math.max(0, Math.round((Date.parse(f.to) - Date.parse(f.from)) / 86400000) + 1) : 0
  const enough = days > 0 && days <= (s.balance[f.type] ?? 0)
  return (
    <>
      <H1>Request time off</H1>
      <Card className="max-w-lg">
        <Field label="Type of leave"><Select data-practice="leave-type" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}><option>Annual</option><option>Sick</option></Select></Field>
        <Field label="First day"><Input data-practice="leave-from" type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></Field>
        <Field label="Last day"><Input data-practice="leave-to" type="date" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></Field>
        <p className="mb-3 text-sm text-slate-600">{days > 0 ? `${days} day${days === 1 ? "" : "s"} requested.` : "Pick the dates."} {days > 0 && !enough ? "That is more than your balance." : ""}</p>
        <div className="flex gap-2"><Btn data-practice="leave-submit" disabled={!enough} onClick={() => { update((d) => { d.leaves.push({ id: `L${d.nextId++}`, person: "You", from: f.from, to: f.to, type: f.type, days, status: "Pending" }) }); router.push("/practice/people/approvals") }}>Send request</Btn>
          <Btn kind="plain" data-practice="leave-balance" onClick={() => router.push("/practice/people/balance")}>Check my balance</Btn></div>
      </Card>
    </>
  )
}
