import Link from 'next/link'
import { BOOKING_HREF, ghostBtn, primaryBtn, WRAP } from './ui'

export function HomeHero() {
  return (
    <section aria-labelledby="hero-title" className={`${WRAP} pt-28 pb-14 sm:pt-32`}>
      <div className="flex flex-col items-start gap-7">
        <h1
          id="hero-title"
          className="m-0 text-[clamp(2.05rem,5.4vw,4.75rem)] font-semibold leading-[1.02] tracking-[-0.04em]"
        >
          Record a task once.
          <br />
          Your AI repeats it.
        </h1>
        <div className="flex flex-wrap gap-2.5">
          <Link href={BOOKING_HREF} className={primaryBtn()}>
            Book a walkthrough
          </Link>
          <Link href="/sign-up" className={ghostBtn()}>
            Start free for 30 days
          </Link>
        </div>
      </div>

  <div role="img" aria-label="Animation: a person fills in a new bill once while Conxa records the five steps, then an AI assistant repeats the same steps for other bills; on the second run a moved Save button is found by its name" style={{ marginTop: "32px", position: "relative", display: "flex", flexWrap: "wrap", gap: "18px", padding: "20px", borderRadius: "20px", border: "1px solid rgba(255,255,255,0.14)", background: "radial-gradient(120% 100% at 50% 0%, #121a26 0%, #0b0f14 60%)" }}>

    <div style={{ flex: "3 1 340px", minWidth: "0", display: "flex", flexDirection: "column", gap: "10px" }}>
      <span style={{ fontWeight: "600", fontSize: "15px" }}>You, once</span>
      <div style={{ borderRadius: "12px", overflow: "hidden", background: "#1a1a1a", border: "1px solid #2f2f2f", fontFamily: "system-ui, sans-serif" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "10px 12px", fontSize: "12px", color: "#a3a3a3" }}>
          <span style={{ width: "9px", height: "9px", borderRadius: "50%", background: "#f87171", animation: "hx-pulse 1.2s ease-in-out infinite" }}></span>
          <span style={{ position: "relative", width: "74px", height: "16px", color: "#e8e6e3" }}>
            <span style={{ position: "absolute", left: "0", top: "0", opacity: "0", animation: "hx-recA 16s infinite" }}>Recording</span>
            <span style={{ position: "absolute", left: "0", top: "0", color: "#4ade80", animation: "hx-recB 16s infinite" }}>Recorded</span>
          </span>
          <span style={{ marginLeft: "auto" }}>accounts.example</span>
        </div>
        <div style={{ position: "relative", background: "#ffffff", color: "#111827", padding: "14px 16px", height: "296px", boxSizing: "border-box", display: "flex", flexDirection: "column", gap: "8px", fontSize: "13px" }}>
          <strong style={{ fontSize: "16px" }}>New bill</strong>
          <div style={{ color: "#4b5563" }}>Supplier</div>
          <div style={{ marginTop: "-6px", height: "34px", boxSizing: "border-box", border: "1px solid #cbd5e1", borderRadius: "6px", padding: "7px 10px" }}><span style={{ display: "inline-block", overflow: "hidden", whiteSpace: "nowrap", verticalAlign: "top", animation: "hx-type1 16s infinite" }}>Kadam Freight</span></div>
          <div style={{ color: "#4b5563" }}>Amount</div>
          <div style={{ marginTop: "-6px", height: "34px", boxSizing: "border-box", border: "1px solid #cbd5e1", borderRadius: "6px", padding: "7px 10px" }}><span style={{ display: "inline-block", overflow: "hidden", whiteSpace: "nowrap", verticalAlign: "top", animation: "hx-type2 16s infinite" }}>8,250.00</span></div>
          <div style={{ height: "34px", boxSizing: "border-box", border: "1px dashed #94a3b8", borderRadius: "6px", padding: "6px 10px" }}><span style={{ display: "inline-flex", alignItems: "center", gap: "6px", animation: "hx-in29 16s infinite" }}><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#2563eb" strokeWidth="1.5"><path d="M4 2h5l3 3v9H4zM9 2v3h3"></path></svg>freight-0926.pdf</span></div>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ position: "relative", display: "inline-block" }}>
              <span style={{ display: "inline-block", padding: "8px 16px", borderRadius: "6px", background: "#1f2937", color: "#ffffff", fontWeight: "600", animation: "hx-press 16s infinite" }}>Save bill</span>
              <span style={{ position: "absolute", left: "22px", top: "2px", width: "34px", height: "34px", borderRadius: "50%", border: "2px solid #22d3ee", opacity: "0", animation: "hx-ripple 16s infinite" }}></span>
            </span>
            <span style={{ color: "#15803d", fontWeight: "600", animation: "hx-in36 16s infinite" }}>Saved</span>
          </div>
          <svg width="22" height="22" viewBox="0 0 22 22" style={{ position: "absolute", left: "12%", top: "234px", filter: "drop-shadow(0 2px 3px rgba(0,0,0,.35))", animation: "hx-cursor 16s ease-in-out infinite" }}><path d="M3 2v16l4.4-4.2 2.9 6.6 2.8-1.2-2.9-6.5H16z" fill="#111827" stroke="#ffffff" strokeWidth="1.4"></path></svg>
        </div>
      </div>
    </div>

    <div style={{ flex: "1 1 180px", minWidth: "170px", display: "flex", flexDirection: "column", gap: "10px" }}>
      <span style={{ fontWeight: "600", fontSize: "15px" }}>The same 5 steps</span>
      <div style={{ flex: "1", display: "flex", flexDirection: "column", justifyContent: "space-between", gap: "6px", padding: "8px 0" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", borderRadius: "10px", background: "#17202c", animation: "hx-in05 16s infinite" }}><span style={{ position: "relative", width: "24px", height: "24px", flex: "0 0 24px", borderRadius: "50%", border: "1.5px solid #64748b", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px" }}>1<span style={{ position: "absolute", inset: "-1.5px", borderRadius: "50%", background: "#4ade80", display: "flex", alignItems: "center", justifyContent: "center", animation: "hx-tick1 16s infinite" }}><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#06080b" strokeWidth="2.6"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>Open</div>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", borderRadius: "10px", background: "#17202c", animation: "hx-in10 16s infinite" }}><span style={{ position: "relative", width: "24px", height: "24px", flex: "0 0 24px", borderRadius: "50%", border: "1.5px solid #64748b", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px" }}>2<span style={{ position: "absolute", inset: "-1.5px", borderRadius: "50%", background: "#4ade80", display: "flex", alignItems: "center", justifyContent: "center", animation: "hx-tick2 16s infinite" }}><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#06080b" strokeWidth="2.6"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>Fill</div>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", borderRadius: "10px", background: "#17202c", animation: "hx-in29 16s infinite" }}><span style={{ position: "relative", width: "24px", height: "24px", flex: "0 0 24px", borderRadius: "50%", border: "1.5px solid #64748b", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px" }}>3<span style={{ position: "absolute", inset: "-1.5px", borderRadius: "50%", background: "#4ade80", display: "flex", alignItems: "center", justifyContent: "center", animation: "hx-tick3 16s infinite" }}><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#06080b" strokeWidth="2.6"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>Attach</div>
        <div style={{ position: "relative", display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", borderRadius: "10px", background: "#17202c", animation: "hx-in36 16s infinite" }}><span style={{ position: "relative", width: "24px", height: "24px", flex: "0 0 24px", borderRadius: "50%", border: "1.5px solid #64748b", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px" }}>4<span style={{ position: "absolute", inset: "-1.5px", borderRadius: "50%", background: "#4ade80", display: "flex", alignItems: "center", justifyContent: "center", animation: "hx-tick4 16s infinite" }}><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#06080b" strokeWidth="2.6"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>Save<span style={{ position: "absolute", right: "8px", padding: "2px 8px", borderRadius: "999px", background: "#fbbf24", color: "#06080b", fontSize: "12px", fontWeight: "700", opacity: "0", animation: "hx-moved 16s infinite" }}>moved</span></div>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", borderRadius: "10px", background: "#17202c", animation: "hx-in39 16s infinite" }}><span style={{ position: "relative", width: "24px", height: "24px", flex: "0 0 24px", borderRadius: "50%", border: "1.5px solid #64748b", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px" }}>5<span style={{ position: "absolute", inset: "-1.5px", borderRadius: "50%", background: "#4ade80", display: "flex", alignItems: "center", justifyContent: "center", animation: "hx-tick5 16s infinite" }}><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#06080b" strokeWidth="2.6"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>Check</div>
      </div>
    </div>

    <div style={{ flex: "3 1 340px", minWidth: "0", display: "flex", flexDirection: "column", gap: "10px" }}>
      <span style={{ fontWeight: "600", fontSize: "15px" }}>Your AI, every time after</span>
      <div style={{ flex: "1", minHeight: "330px", boxSizing: "border-box", borderRadius: "12px", background: "#1a1a1a", border: "1px solid #2f2f2f", padding: "18px", display: "flex", flexDirection: "column", justifyContent: "flex-end", gap: "12px", fontFamily: "system-ui, sans-serif", fontSize: "14px", color: "#e8e6e3" }}>
        <div style={{ alignSelf: "flex-end", background: "#2c2c2c", borderRadius: "14px", padding: "10px 14px", animation: "hx-in44 16s infinite" }}>Enter the Acme bill</div>
        <div style={{ alignSelf: "flex-start", display: "flex", alignItems: "center", gap: "10px", padding: "12px 14px", borderRadius: "12px", background: "#1e2a20", animation: "hx-in60 16s infinite" }}><svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg>Done. It's in the list.</div>
        <div style={{ alignSelf: "flex-end", background: "#2c2c2c", borderRadius: "14px", padding: "10px 14px", animation: "hx-in65 16s infinite" }}>Now the Northline one</div>
        <div style={{ alignSelf: "flex-start", display: "flex", alignItems: "center", gap: "10px", padding: "12px 14px", borderRadius: "12px", background: "#1e2a20", animation: "hx-in81 16s infinite" }}><svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg>Done. Save had moved, found it by name.</div>
      </div>
    </div>
  </div>
    </section>
  )
}
