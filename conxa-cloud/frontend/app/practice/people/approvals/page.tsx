"use client"
import { useState } from "react"
import { usePractice } from "../../_data/store"
import { Btn, H1, Loading, Select, Td, Th } from "../../_ui"

export default function LeaveApprovals() {
  const { s, update } = usePractice()
  const [view, setView] = useState("Employee")
  if (!s) return <Loading />
  const approve = (id: string) => update((d) => {
    const l = d.leaves.find((x) => x.id === id)
    if (l && l.status === "Pending") { l.status = "Approved"; d.balance[l.type] = (d.balance[l.type] ?? 0) - l.days }
  })
  return (
    <>
      <div className="mb-4 flex items-center justify-between"><H1>Leave requests</H1>
        <Select data-practice="view-as" value={view} onChange={(e) => setView(e.target.value)} className="w-44"><option>Employee</option><option>Manager</option></Select></div>
      <table className="w-full border border-slate-200 bg-white"><thead><tr><Th>Who</Th><Th>Dates</Th><Th>Type</Th><Th>Status</Th><Th /></tr></thead>
        <tbody>{s.leaves.map((l) => <tr key={l.id}><Td>{l.person}</Td><Td>{l.from} to {l.to}</Td><Td>{l.type}</Td><Td>{l.status}</Td>
          <Td>{view === "Manager" && l.status === "Pending" ? <Btn data-practice={`approve-${l.id}`} onClick={() => approve(l.id)}>Approve</Btn> : null}</Td></tr>)}</tbody></table>
      {s.leaves.length === 0 && <p className="mt-4 text-sm text-slate-600">No requests yet.</p>}
    </>
  )
}
