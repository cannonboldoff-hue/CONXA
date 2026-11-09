"use client"
import Link from "next/link"
import { useState } from "react"
import { usePractice } from "../../_data/store"
import { H1, Loading, Select, Td, Th } from "../../_ui"

export default function Inbox() {
  const { s } = usePractice()
  const [sort, setSort] = useState("newest")
  if (!s) return <Loading />
  const rows = s.tickets.filter((t) => t.status === "Open").sort((a, b) => (sort === "oldest" ? a.opened.localeCompare(b.opened) : b.opened.localeCompare(a.opened)))
  return (
    <>
      <div className="mb-4 flex items-center justify-between"><H1>Open requests</H1>
        <Select data-practice="inbox-sort" value={sort} onChange={(e) => setSort(e.target.value)} className="w-44"><option value="newest">Newest first</option><option value="oldest">Oldest first</option></Select></div>
      <table className="w-full border border-slate-200 bg-white"><thead><tr><Th>Subject</Th><Th>Opened</Th><Th /></tr></thead>
        <tbody>{rows.map((t, i) => <tr key={t.id}><Td>{t.subject}</Td><Td>{t.opened}</Td><Td><Link data-practice={`open-ticket-${i}`} href={`/practice/helpdesk/tickets/${t.id}`} className="text-indigo-700 underline">Open</Link></Td></tr>)}</tbody></table>
      {rows.length === 0 && <p className="mt-4 text-sm text-slate-600">No open requests. Use “Reset practice data” to start again.</p>}
    </>
  )
}
