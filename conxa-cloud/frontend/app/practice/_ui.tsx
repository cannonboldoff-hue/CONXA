"use client"
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react"

export const H1 = ({ children }: { children: ReactNode }) => <h1 className="mb-4 text-2xl font-semibold text-slate-900">{children}</h1>
export const Card = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <div className={`rounded-lg border border-slate-200 bg-white p-5 shadow-sm ${className}`}>{children}</div>
)
export const Field = ({ label, children }: { label: string; children: ReactNode }) => (
  <label className="mb-3 block text-sm font-medium text-slate-700">{label}<div className="mt-1">{children}</div></label>
)
const box = "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
export const Input = (p: InputHTMLAttributes<HTMLInputElement>) => <input {...p} className={`${box} ${p.className ?? ""}`} />
export const Select = (p: SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} className={`${box} ${p.className ?? ""}`} />
export function Btn({ kind = "primary", ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { kind?: "primary" | "plain" }) {
  const c = kind === "primary" ? "bg-indigo-600 text-white hover:bg-indigo-700" : "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50"
  return <button type="button" {...p} className={`rounded-md px-4 py-2 text-sm font-medium disabled:opacity-50 ${c} ${p.className ?? ""}`} />
}
export const Th = ({ children }: { children?: ReactNode }) => <th className="border-b border-slate-200 px-3 py-2 text-left text-xs font-semibold uppercase text-slate-500">{children}</th>
export const Td = ({ children }: { children?: ReactNode }) => <td className="border-b border-slate-100 px-3 py-2 text-sm text-slate-800">{children}</td>
export const Loading = () => <p className="text-sm text-slate-500">Loading…</p>
