'use client'

import { useState } from 'react'
import { H2, WRAP, pill } from './ui'

const TABS = [
  { id: 'ops', label: 'Operations teams' },
  { id: 'vendor', label: 'Software companies' },
  { id: 'firm', label: 'IT service firms' },
  { id: 'try', label: 'Just trying it' },
] as const

export function HomeWork() {
  const [tab, setTab] = useState<(typeof TABS)[number]['id']>('ops')
  return (
    <section id="work" aria-labelledby="work-title" className={`${WRAP} py-16`}>
      <h2 id="work-title" className={H2}>
        What do you repeat?
      </h2>
      <div role="group" aria-label="Choose who you are" className="mt-5 flex flex-wrap gap-2.5">
        {TABS.map((t) => (
          <button key={t.id} type="button" aria-pressed={tab === t.id} onClick={() => setTab(t.id)} className={pill(tab === t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'ops' && (
        <>
  <div style={{ marginTop: "24px", padding: "28px", borderRadius: "18px", background: "#0b0f14", border: "1px solid rgba(255,255,255,0.1)" }}>
    <h3 style={{ margin: "0 0 24px", fontSize: "22px" }}>Set up a new starter, in four apps</h3>
    <div style={{ overflowX: "auto", paddingBottom: "4px" }}>
      <div style={{ position: "relative", minWidth: "720px", display: "flex", justifyContent: "space-between" }}>
        <span aria-hidden="true" style={{ position: "absolute", left: "60px", right: "60px", top: "51px", height: "2px", backgroundImage: "linear-gradient(90deg, #22d3ee 50%, transparent 50%)", backgroundSize: "12px 2px", animation: "hx-dash 1s linear infinite" }}></span>
        <span aria-hidden="true" style={{ position: "absolute", left: "60px", right: "60px", top: "34px", height: "34px", zIndex: "2" }}><span style={{ position: "absolute", top: "0", left: "0", width: "34px", height: "34px", borderRadius: "8px", background: "#22d3ee", display: "flex", alignItems: "center", justifyContent: "center", animation: "hx-travel 4s ease-in-out infinite" }}><svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="#06080b" strokeWidth="1.8"><circle cx="8" cy="5.5" r="2.5"></circle><path d="M3 14c.6-3 2.6-4 5-4s4.4 1 5 4"></path></svg></span></span>
        <div style={{ position: "relative", width: "130px", display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}><span style={{ width: "104px", height: "104px", borderRadius: "18px", background: "#17202c", display: "flex", alignItems: "center", justifyContent: "center" }}><svg aria-hidden="true" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.4"><circle cx="9" cy="8" r="3.2"></circle><path d="M3 19c.6-3.6 3-5.2 6-5.2s5.4 1.6 6 5.2"></path><circle cx="17" cy="9.5" r="2.4"></circle><path d="M16 13.8c2.7.1 4.4 1.6 5 4.4"></path></svg></span>HR system</div>
        <div style={{ position: "relative", width: "130px", display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}><span style={{ width: "104px", height: "104px", borderRadius: "18px", background: "#17202c", display: "flex", alignItems: "center", justifyContent: "center" }}><svg aria-hidden="true" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.4"><path d="M4 7h9M17 7h3M4 17h3M11 17h9"></path><circle cx="15" cy="7" r="2"></circle><circle cx="9" cy="17" r="2"></circle></svg></span>Admin console</div>
        <div style={{ position: "relative", width: "130px", display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}><span style={{ width: "104px", height: "104px", borderRadius: "18px", background: "#17202c", display: "flex", alignItems: "center", justifyContent: "center" }}><svg aria-hidden="true" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.4"><rect x="3" y="6" width="18" height="12" rx="2"></rect><circle cx="12" cy="12" r="2.6"></circle></svg></span>Payroll</div>
        <div style={{ position: "relative", width: "130px", display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}><span style={{ width: "104px", height: "104px", borderRadius: "18px", background: "#17202c", display: "flex", alignItems: "center", justifyContent: "center" }}><svg aria-hidden="true" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.4"><path d="M4 5h16v11H10l-5 4v-4H4z"></path><path d="M8 9h8M8 12.5h5"></path></svg></span>Team chat</div>
      </div>
    </div>
    <div style={{ marginTop: "22px", display: "flex", flexWrap: "wrap", gap: "10px", fontSize: "14px" }}><span style={{ padding: "8px 14px", borderRadius: "999px", background: "#17202c" }}>You give: name, email, start date</span><span style={{ padding: "8px 14px", borderRadius: "999px", background: "#163226", color: "#bbf7d0" }}>You get: accounts ready</span><span style={{ padding: "8px 14px", borderRadius: "999px", border: "1px solid rgba(255,255,255,0.18)" }}>Also: supplier invoices</span><span style={{ padding: "8px 14px", borderRadius: "999px", border: "1px solid rgba(255,255,255,0.18)" }}>Also: Monday's numbers</span></div>
  </div>
        </>
      )}
      {tab === 'vendor' && (
        <>
  <div style={{ marginTop: "24px", padding: "28px", borderRadius: "18px", background: "#0b0f14", border: "1px solid rgba(255,255,255,0.1)" }}>
    <h3 style={{ margin: "0 0 24px", fontSize: "22px" }}>Your customers' AI sets up their account</h3>
    <div style={{ overflowX: "auto", paddingBottom: "4px" }}>
      <div style={{ position: "relative", minWidth: "560px", display: "flex", justifyContent: "space-between" }}>
        <span aria-hidden="true" style={{ position: "absolute", left: "60px", right: "60px", top: "51px", height: "2px", backgroundImage: "linear-gradient(90deg, #22d3ee 50%, transparent 50%)", backgroundSize: "12px 2px", animation: "hx-dash 1s linear infinite" }}></span>
        <span aria-hidden="true" style={{ position: "absolute", left: "60px", right: "60px", top: "34px", height: "34px", zIndex: "2" }}><span style={{ position: "absolute", top: "0", left: "0", width: "34px", height: "34px", borderRadius: "8px", background: "#22d3ee", display: "flex", alignItems: "center", justifyContent: "center", animation: "hx-travel 3.4s ease-in-out infinite" }}><svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="#06080b" strokeWidth="1.8"><path d="M3 3h10v8H8l-3 3v-3H3z"></path></svg></span></span>
        <div style={{ position: "relative", width: "150px", display: "flex", flexDirection: "column", alignItems: "center", gap: "10px", textAlign: "center" }}><span style={{ width: "104px", height: "104px", borderRadius: "18px", background: "#17202c", display: "flex", alignItems: "center", justifyContent: "center" }}><svg aria-hidden="true" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.4"><path d="M4 5h16v11H9l-5 4z"></path></svg></span>"Set up our Pune team"</div>
        <div style={{ position: "relative", width: "150px", display: "flex", flexDirection: "column", alignItems: "center", gap: "10px", textAlign: "center" }}><span style={{ width: "104px", height: "104px", borderRadius: "18px", background: "#17202c", display: "flex", alignItems: "center", justifyContent: "center" }}><svg aria-hidden="true" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" strokeWidth="1.4"><rect x="4" y="3" width="16" height="14" rx="2"></rect><path d="M8 21h8M12 17v4M8 8l2.5 2.5L16 6.5"></path></svg></span>Conxa on their PC</div>
        <div style={{ position: "relative", width: "150px", display: "flex", flexDirection: "column", alignItems: "center", gap: "10px", textAlign: "center" }}><span style={{ width: "104px", height: "104px", borderRadius: "18px", background: "#17202c", display: "flex", alignItems: "center", justifyContent: "center" }}><svg aria-hidden="true" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.4"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M3 9h18M7 13h4M7 16h7"></path></svg></span>Your product</div>
      </div>
    </div>
    <div style={{ marginTop: "22px", display: "flex", flexWrap: "wrap", gap: "10px", fontSize: "14px" }}><span style={{ padding: "8px 14px", borderRadius: "999px", background: "#17202c" }}>Ship as an installer</span><span style={{ padding: "8px 14px", borderRadius: "999px", background: "#163226", color: "#bbf7d0" }}>Updates itself</span><span style={{ padding: "8px 14px", borderRadius: "999px", border: "1px solid rgba(255,255,255,0.18)" }}>No API project first</span></div>
  </div>
        </>
      )}
      {tab === 'firm' && (
        <>
  <div style={{ marginTop: "24px", padding: "28px", borderRadius: "18px", background: "#0b0f14", border: "1px solid rgba(255,255,255,0.1)" }}>
    <h3 style={{ margin: "0 0 24px", fontSize: "22px" }}>Move approved orders into the ERP</h3>
    <div style={{ overflowX: "auto", paddingBottom: "4px" }}>
      <div style={{ position: "relative", minWidth: "560px", display: "flex", justifyContent: "space-between" }}>
        <span aria-hidden="true" style={{ position: "absolute", left: "60px", right: "60px", top: "51px", height: "2px", backgroundImage: "linear-gradient(90deg, #22d3ee 50%, transparent 50%)", backgroundSize: "12px 2px", animation: "hx-dash 1s linear infinite" }}></span>
        <span aria-hidden="true" style={{ position: "absolute", left: "60px", right: "60px", top: "34px", height: "34px", zIndex: "2" }}><span style={{ position: "absolute", top: "0", left: "0", width: "34px", height: "34px", borderRadius: "8px", background: "#22d3ee", display: "flex", alignItems: "center", justifyContent: "center", animation: "hx-travel 3.4s ease-in-out infinite" }}><svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="#06080b" strokeWidth="1.8"><path d="M4 2h5l3 3v9H4z"></path></svg></span></span>
        <div style={{ position: "relative", width: "130px", display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}><span style={{ width: "104px", height: "104px", borderRadius: "18px", background: "#17202c", display: "flex", alignItems: "center", justifyContent: "center" }}><svg aria-hidden="true" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.4"><path d="M3 5h18l-7 8v6l-4-2v-4z"></path></svg></span>CRM</div>
        <div style={{ position: "relative", width: "130px", display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}><span style={{ width: "104px", height: "104px", borderRadius: "18px", background: "#17202c", display: "flex", alignItems: "center", justifyContent: "center" }}><svg aria-hidden="true" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.4"><path d="M3 8.5 12 4l9 4.5-9 4.5zM3 12.5l9 4.5 9-4.5M3 16.5 12 21l9-4.5"></path></svg></span>ERP</div>
      </div>
    </div>
    <div style={{ marginTop: "22px", display: "flex", flexWrap: "wrap", gap: "10px", fontSize: "14px" }}><span style={{ padding: "8px 14px", borderRadius: "999px", background: "#17202c" }}>You give: order number</span><span style={{ padding: "8px 14px", borderRadius: "999px", background: "#163226", color: "#bbf7d0" }}>You get: order in the ERP</span><span style={{ padding: "8px 14px", borderRadius: "999px", border: "1px solid rgba(255,255,255,0.18)" }}>Different client screens need their own recording</span></div>
  </div>
        </>
      )}
      {tab === 'try' && (
        <>
  <div style={{ marginTop: "24px", padding: "28px", borderRadius: "18px", background: "#0b0f14", border: "1px solid rgba(255,255,255,0.1)" }}>
    <h3 style={{ margin: "0 0 24px", fontSize: "22px" }}>Your first task, today</h3>
    <ol style={{ margin: "0", padding: "0", listStyle: "none", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(240px, 100%), 1fr))", gap: "16px" }}>
      <li style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "12px", padding: "22px", borderRadius: "14px", background: "#17202c", textAlign: "center" }}><svg aria-hidden="true" width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" strokeWidth="1.4"><path d="M12 3v11M7.5 10 12 14.5 16.5 10M4 17v3h16v-3"></path></svg><strong>1. Install it</strong></li>
      <li style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "12px", padding: "22px", borderRadius: "14px", background: "#17202c", textAlign: "center" }}><svg aria-hidden="true" width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="1.4"><circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="3.5" fill="#f87171" stroke="none" style={{ animation: "hx-pulse 1.2s ease-in-out infinite", transformOrigin: "12px 12px" }}></circle></svg><strong>2. Record the task</strong></li>
      <li style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "12px", padding: "22px", borderRadius: "14px", background: "#17202c", textAlign: "center" }}><svg aria-hidden="true" width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="#4ade80" strokeWidth="1.4"><path d="M8 5.5v13l11-6.5z"></path></svg><strong>3. Ask your AI to run it</strong></li>
    </ol>
  </div>
        </>
      )}
    </section>
  )
}
