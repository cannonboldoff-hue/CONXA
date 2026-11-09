"use client"
import { useParams, useRouter } from "next/navigation"
import { useState } from "react"
import { AGENTS, TEMPLATES, usePractice, type Ticket } from "../../../_data/store"
import { Btn, Card, Field, H1, Loading, Select } from "../../../_ui"

export default function Ticket() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const { s, update } = usePractice()
  const [lookup, setLookup] = useState(false)
  const [tpl, setTpl] = useState("")
  const [reply, setReply] = useState("")
  if (!s) return <Loading />
  const t = s.tickets.find((x) => x.id === id)
  if (!t) return <p className="text-sm">This request does not exist.</p>
  const cust = s.customers.find((c) => c.id === t.customerId)
  const patch = (fn: (x: Ticket) => void) => update((st) => { const x = st.tickets.find((y) => y.id === id); if (x) fn(x) })
  const ready = t.priority && t.category && t.agent
  return (
    <>
      <H1>{t.subject}</H1>
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <Field label="Priority"><Select data-practice="ticket-priority" value={t.priority} onChange={(e) => patch((x) => { x.priority = e.target.value })}><option value="">Choose…</option><option>Low</option><option>Normal</option><option>Urgent</option></Select></Field>
          <Field label="Topic"><Select data-practice="ticket-category" value={t.category} onChange={(e) => patch((x) => { x.category = e.target.value })}><option value="">Choose…</option><option>Billing</option><option>Account</option><option>Other</option></Select></Field>
          <Field label="Handled by"><Select data-practice="ticket-agent" value={t.agent} onChange={(e) => patch((x) => { x.agent = e.target.value })}><option value="">Choose…</option>{AGENTS.map((a) => <option key={a}>{a}</option>)}</Select></Field>
        </Card>
        <Card>
          <Btn kind="plain" data-practice="lookup-customer" onClick={() => setLookup(!lookup)}>{lookup ? "Hide customer" : "Look up customer"}</Btn>
          {lookup && cust && <dl className="mt-3 text-sm" data-practice="customer-panel"><dt className="font-medium">{cust.name}</dt><dd>{cust.email}</dd><dd>Plan: {cust.plan}</dd><dd>Customer since {cust.since}</dd></dl>}
        </Card>
      </div>
      <Card className="mt-4">
        <Field label="Ready-made reply"><Select data-practice="reply-template" value={tpl} onChange={(e) => { setTpl(e.target.value); setReply(TEMPLATES[e.target.value] ?? "") }}><option value="">Choose…</option>{Object.keys(TEMPLATES).map((k) => <option key={k}>{k}</option>)}</Select></Field>
        <Field label="Your reply"><textarea data-practice="reply-text" value={reply} onChange={(e) => setReply(e.target.value)} rows={3} className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></Field>
        <div className="flex gap-2">
          <Btn data-practice="reply-send" disabled={!reply.trim()} onClick={() => { patch((x) => x.replies.push(reply.trim())); setReply(""); setTpl("") }}>Send reply</Btn>
          <Btn kind="plain" data-practice="ticket-close" disabled={!ready || t.replies.length === 0} onClick={() => { patch((x) => { x.status = "Closed" }); router.push("/practice/helpdesk/inbox") }}>Close request</Btn>
        </div>
        {t.replies.length > 0 && <p className="mt-3 text-sm text-green-700">Reply sent. You can close the request once priority, topic and handler are set.</p>}
      </Card>
    </>
  )
}
