"use client"
import { useRouter } from "next/navigation"
import { useState } from "react"
import { usePractice } from "../../../_data/store"
import { Btn, Card, Field, H1, Input } from "../../../_ui"

export default function NewContact() {
  const { s, update } = usePractice()
  const router = useRouter()
  const [f, setF] = useState({ name: "", email: "", company: "" })
  if (!s) return null
  const ok = f.name.trim() && /.+@.+/.test(f.email)
  const save = () => {
    update((d) => { d.contacts.push({ id: `C${d.nextId++}`, name: f.name.trim(), email: f.email.trim(), company: f.company.trim() }) })
    router.push("/practice/crm/contacts")
  }
  return (
    <>
      <H1>New customer</H1>
      <Card className="max-w-lg">
        <Field label="Full name"><Input data-practice="contact-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Email"><Input data-practice="contact-email" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="Company"><Input data-practice="contact-company" value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} /></Field>
        <Btn data-practice="contact-save" disabled={!ok} onClick={save}>Save customer</Btn>
      </Card>
    </>
  )
}
