"use client"
import { useCallback, useEffect, useState } from "react"

// Practice company data. Everything is fake and lives only in this browser (localStorage); nothing is sent anywhere.
export type Contact = { id: string; name: string; email: string; company: string }
export type Deal = { id: string; contactId: string; stage: "Open" | "Quoted" | "Won"; items: { product: string; qty: number; price: number }[]; quoteSent: boolean }
export type Ticket = { id: string; subject: string; customerId: string; opened: string; status: "Open" | "Closed"; priority: string; category: string; agent: string; replies: string[] }
export type Customer = { id: string; name: string; email: string; plan: string; since: string }
export type Order = { id: string; customer: string; date: string; amount: number; status: "Paid" | "Unpaid"; invoiced: boolean }
export type Invoice = { id: string; orderIds: string[]; discountCode: string; total: number; status: "Draft" | "Pending approval" | "Approved" }
export type Leave = { id: string; person: string; from: string; to: string; type: string; days: number; status: "Pending" | "Approved" }
export type State = {
  nextId: number
  contacts: Contact[]; deals: Deal[]; tickets: Ticket[]; customers: Customer[]
  orders: Order[]; invoices: Invoice[]; leaves: Leave[]; balance: Record<string, number>
}

export const PRODUCTS = [
  { name: "Starter plan", price: 49 }, { name: "Team plan", price: 199 }, { name: "Onboarding session", price: 120 }, { name: "Priority support", price: 80 },
]
export const AGENTS = ["Maya Chen", "Omar Haddad", "Priya Nair"]
export const TEMPLATES: Record<string, string> = {
  "Thanks, we're on it": "Thanks for getting in touch. We are looking into this now and will update you today.",
  "Fixed, please check": "We have fixed this. Please check on your side and tell us if anything still looks wrong.",
}
export const DISCOUNT_CODES: Record<string, number> = { SAVE10: 0.1, WELCOME20: 0.2 }

export const SEED: State = {
  nextId: 100,
  contacts: [{ id: "C1", name: "Dana Whitfield", email: "dana@brightline.example", company: "Brightline Studio" }],
  deals: [],
  customers: [
    { id: "U1", name: "Lena Ortiz", email: "lena@harborcafe.example", plan: "Team plan", since: "2025-03-02" },
    { id: "U2", name: "Tom Becker", email: "tom@northpeak.example", plan: "Starter plan", since: "2025-08-19" },
    { id: "U3", name: "Aiko Tanaka", email: "aiko@sakurahome.example", plan: "Team plan", since: "2024-11-30" },
  ],
  tickets: [
    { id: "T1", subject: "Can't download my invoice", customerId: "U2", opened: "2026-10-02", status: "Open", priority: "", category: "", agent: "", replies: [] },
    { id: "T2", subject: "Change my plan", customerId: "U1", opened: "2026-10-05", status: "Open", priority: "", category: "", agent: "", replies: [] },
    { id: "T3", subject: "Login keeps failing", customerId: "U3", opened: "2026-09-28", status: "Open", priority: "", category: "", agent: "", replies: [] },
    { id: "T4", subject: "Thanks for the help", customerId: "U1", opened: "2026-09-20", status: "Closed", priority: "Low", category: "Other", agent: "Maya Chen", replies: [] },
  ],
  orders: [
    { id: "O-1001", customer: "Harbor Cafe", date: "2026-09-04", amount: 199, status: "Paid", invoiced: true },
    { id: "O-1002", customer: "North Peak", date: "2026-09-12", amount: 49, status: "Unpaid", invoiced: false },
    { id: "O-1003", customer: "Sakura Home", date: "2026-09-21", amount: 398, status: "Unpaid", invoiced: false },
    { id: "O-1004", customer: "Brightline Studio", date: "2026-10-03", amount: 120, status: "Unpaid", invoiced: false },
    { id: "O-1005", customer: "Harbor Cafe", date: "2026-10-09", amount: 80, status: "Paid", invoiced: true },
  ],
  invoices: [],
  leaves: [],
  balance: { Annual: 18, Sick: 8 },
}

const KEY = "practice:state:v1"
const read = (): State => {
  try { const raw = window.localStorage.getItem(KEY); if (raw) return JSON.parse(raw) as State } catch { /* fresh data */ }
  return structuredClone(SEED)
}

export function usePractice() {
  const [s, setS] = useState<State | null>(null)
  useEffect(() => setS(read()), [])
  const update = useCallback((fn: (d: State) => void) => {
    const next = structuredClone(read())
    fn(next)
    try { window.localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* nothing to persist */ }
    setS(next)
  }, [])
  const reset = useCallback(() => {
    try { window.localStorage.removeItem(KEY) } catch { /* ignore */ }
    setS(structuredClone(SEED))
  }, [])
  return { s, update, reset }
}

export const money = (n: number) => `$${n.toFixed(2)}`
