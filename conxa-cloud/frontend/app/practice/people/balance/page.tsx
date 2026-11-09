"use client"
import { usePractice } from "../../_data/store"
import { Card, H1, Loading } from "../../_ui"

export default function Balance() {
  const { s } = usePractice()
  if (!s) return <Loading />
  return (
    <>
      <H1>Leave balance</H1>
      <div className="grid max-w-md grid-cols-2 gap-4">{Object.entries(s.balance).map(([k, v]) => <Card key={k}><p className="text-sm text-slate-600">{k}</p><p className="text-2xl font-semibold" data-practice={`balance-${k.toLowerCase()}`}>{v} days</p></Card>)}</div>
    </>
  )
}
