"use client"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { usePractice } from "../../_data/store"
import { Btn, H1, Loading, Td, Th } from "../../_ui"

export default function Contacts() {
  const { s, update } = usePractice()
  const router = useRouter()
  if (!s) return <Loading />
  const openDeal = (contactId: string) => {
    const id = `D${s.nextId}`
    update((d) => { d.nextId++; d.deals.push({ id, contactId, stage: "Open", items: [], quoteSent: false }) })
    router.push(`/practice/crm/deals/${id}`)
  }
  return (
    <>
      <div className="mb-4 flex items-center justify-between"><H1>Customers</H1><Link href="/practice/crm/contacts/new" data-practice="new-contact" className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white">New customer</Link></div>
      <table className="w-full border border-slate-200 bg-white"><thead><tr><Th>Name</Th><Th>Email</Th><Th>Company</Th><Th /></tr></thead>
        <tbody>{s.contacts.map((c) => (
          <tr key={c.id}><Td>{c.name}</Td><Td>{c.email}</Td><Td>{c.company}</Td>
            <Td><Btn kind="plain" data-practice={`open-deal-${c.id}`} onClick={() => openDeal(c.id)}>Open a deal</Btn></Td></tr>))}</tbody></table>
    </>
  )
}
