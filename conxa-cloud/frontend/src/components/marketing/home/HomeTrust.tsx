import { H2, BAND, WRAP } from './ui'

export function HomeTrust() {
  return (
    <section id="trust" aria-labelledby="trust-title" className={BAND}>
      <div className={`${WRAP} py-16`}>
        <h2
          id="trust-title"
          className={`${H2} text-[clamp(2.25rem,4.6vw,4rem)] leading-[1.05] tracking-[-0.035em]`}
        >
          Screens change. It doesn&apos;t guess.
        </h2>
    <ol style={{ margin: "32px 0 0", padding: "0", listStyle: "none", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(340px, 100%), 1fr))", gap: "20px" }}>
      <li style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <div role="img" aria-label="Animation: a pop-up banner is cleared, then the button is pressed" style={{ position: "relative", height: "220px", overflow: "hidden", borderRadius: "14px", background: "#f8fafc", color: "#111827", fontFamily: "system-ui, sans-serif", fontSize: "13px" }}>
          <span style={{ position: "absolute", left: "20px", top: "22px", width: "55%", height: "10px", borderRadius: "5px", background: "#e2e8f0" }}></span>
          <span style={{ position: "absolute", left: "20px", top: "42px", width: "38%", height: "10px", borderRadius: "5px", background: "#e2e8f0" }}></span>
          <span style={{ position: "absolute", left: "20px", top: "74px", padding: "10px 18px", borderRadius: "8px", background: "#1f2937", color: "#fff", fontWeight: "600", animation: "hx-clickAt50 6s infinite" }}>Submit</span>
          <span style={{ position: "absolute", left: "128px", top: "80px", animation: "hx-pop60 6s infinite" }}><svg width="28" height="28" viewBox="0 0 16 16" fill="none" stroke="#16a34a" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span>
          <span style={{ position: "absolute", left: "0", right: "0", bottom: "0", height: "86px", boxSizing: "border-box", padding: "16px 20px", background: "#1f2937", color: "#fff", display: "flex", alignItems: "center", justifyContent: "space-between", opacity: "0", transform: "translateY(110px)", animation: "hx-bannerGo 6s ease-in-out infinite" }}>We use cookies<span style={{ padding: "7px 14px", borderRadius: "6px", background: "#fff", color: "#111827" }}>Close</span></span>
        </div>
        <h3 style={{ margin: "6px 0 0", fontSize: "20px" }}>1. Fixes small things itself</h3>
        <p style={{ margin: "0", color: "#9ba3af" }}>Waits, banners, scrolling. No AI.</p>
      </li>
      <li style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <div role="img" aria-label="Animation: the button moves, the AI picks it from what is on the page, and the pick is checked" style={{ position: "relative", height: "220px", overflow: "hidden", borderRadius: "14px", background: "#f8fafc", color: "#111827", fontFamily: "system-ui, sans-serif", fontSize: "13px" }}>
          <span style={{ position: "absolute", left: "8%", top: "22px", width: "40%", height: "10px", borderRadius: "5px", background: "#e2e8f0" }}></span>
          <span style={{ position: "absolute", left: "56%", top: "96px", padding: "10px 18px", borderRadius: "8px", background: "#e2e8f0" }}>Save draft</span>
          <span style={{ position: "absolute", left: "56%", top: "150px", padding: "10px 18px", borderRadius: "8px", background: "#e2e8f0" }}>Preview</span>
          <span style={{ position: "absolute", left: "8%", top: "150px", padding: "10px 18px", borderRadius: "8px", background: "#1f2937", color: "#fff", fontWeight: "600", whiteSpace: "nowrap", animation: "hx-jump 6s ease-in-out infinite" }}>Send order</span>
          <span style={{ position: "absolute", left: "8%", top: "150px", width: "112px", height: "38px", boxSizing: "border-box", border: "2.5px dashed #0891b2", borderRadius: "10px", margin: "-3px 0 0 -3px", animation: "hx-scan 6s ease-in-out infinite" }}></span>
          <span style={{ position: "absolute", left: "8%", top: "112px", padding: "4px 10px", borderRadius: "999px", background: "#0e7490", color: "#fff", fontWeight: "700", animation: "hx-aiTag 6s infinite" }}>AI picks it</span>
          <span style={{ position: "absolute", left: "calc(8% + 122px)", top: "154px", animation: "hx-pop72 6s infinite" }}><svg width="28" height="28" viewBox="0 0 16 16" fill="none" stroke="#16a34a" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span>
        </div>
        <h3 style={{ margin: "6px 0 0", fontSize: "20px" }}>2. Asks your AI, then checks</h3>
        <p style={{ margin: "0", color: "#9ba3af" }}>Not on scheduled or form runs.</p>
      </li>
      <li style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <div role="img" aria-label="Animation: two identical Delete buttons, so it stops and says why instead of choosing one" style={{ position: "relative", height: "220px", overflow: "hidden", borderRadius: "14px", background: "#f8fafc", color: "#111827", fontFamily: "system-ui, sans-serif", fontSize: "13px" }}>
          <div style={{ position: "absolute", left: "20px", right: "20px", top: "30px", display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", borderRadius: "8px", background: "#fff", border: "1px solid #e2e8f0" }}>Invoice 1042<span style={{ padding: "6px 12px", borderRadius: "6px", background: "#fee2e2", color: "#b91c1c", animation: "hx-glowRow 6s infinite" }}>Delete</span></div>
          <div style={{ position: "absolute", left: "20px", right: "20px", top: "88px", display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", borderRadius: "8px", background: "#fff", border: "1px solid #e2e8f0" }}>Invoice 1042<span style={{ padding: "6px 12px", borderRadius: "6px", background: "#fee2e2", color: "#b91c1c", animation: "hx-glowRow 6s infinite" }}>Delete</span></div>
          <span style={{ position: "absolute", left: "calc(50% - 20px)", top: "140px", fontSize: "44px", fontWeight: "700", color: "#0891b2", opacity: "0", animation: "hx-ask 6s infinite" }}>?</span>
          <div style={{ position: "absolute", left: "20px", right: "20px", top: "146px", display: "flex", alignItems: "center", gap: "10px", padding: "12px 14px", borderRadius: "10px", background: "#1f2937", color: "#fff", animation: "hx-stopIn 6s infinite" }}><svg width="28" height="28" viewBox="0 0 24 24"><path d="M8 2h8l6 6v8l-6 6H8l-6-6V8z" fill="#dc2626"></path><path d="M7 12h10" stroke="#fff" strokeWidth="2.6"></path></svg>Stopped. Two look the same.</div>
        </div>
        <h3 style={{ margin: "6px 0 0", fontSize: "20px" }}>3. Still unsure? Stops</h3>
        <p style={{ margin: "0", color: "#9ba3af" }}>And says why, in plain words.</p>
      </li>
    </ol>

    <blockquote style={{ margin: "44px 0 0", padding: "24px 28px", borderRadius: "16px", background: "#06080b", border: "1px solid rgba(255,255,255,0.14)", display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: "16px" }}>
      <p style={{ margin: "0", fontSize: "clamp(1.4rem, 2.6vw, 2rem)", letterSpacing: "-0.02em", fontWeight: "500" }}>"It tried for a few seconds, then stopped rather than guess."</p>
      <footer style={{ fontSize: "14px", color: "#9ba3af" }}>A real message from the app</footer>
    </blockquote>

    <div style={{ marginTop: "20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(340px, 100%), 1fr))", gap: "20px" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <div role="img" aria-label="Animation: a highlight moves down a customer list and stops on the one asked for before deleting" style={{ position: "relative", height: "170px", overflow: "hidden", borderRadius: "14px", background: "#17202c", fontSize: "14px" }}>
          <span style={{ position: "absolute", left: "12px", right: "12px", top: "116px", height: "36px", borderRadius: "8px", background: "rgba(34,211,238,0.22)", border: "1.5px solid #22d3ee", boxSizing: "border-box", animation: "hx-sweep 6s ease-in-out infinite" }}></span>
          <span style={{ position: "absolute", left: "26px", top: "16px" }}>Ada Lovelace</span>
          <span style={{ position: "absolute", left: "26px", top: "52px" }}>Alan Turing</span>
          <span style={{ position: "absolute", left: "26px", top: "88px" }}>Mary Jackson</span>
          <span style={{ position: "absolute", left: "26px", top: "124px", fontWeight: "600" }}>Grace Hopper</span>
          <span style={{ position: "absolute", right: "24px", top: "121px", padding: "3px 10px", borderRadius: "6px", background: "#f87171", color: "#06080b", fontWeight: "700", fontSize: "13px", animation: "hx-rowOk 6s infinite" }}>Delete</span>
        </div>
        <h3 style={{ margin: "0", fontSize: "18px" }}>Only the right row</h3>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <div role="img" aria-label="Animation: the saved bill appears in the list, so the result is confirmed" style={{ position: "relative", height: "170px", overflow: "hidden", borderRadius: "14px", background: "#17202c", fontSize: "14px" }}>
          <span style={{ position: "absolute", left: "24px", right: "24px", top: "20px", height: "30px", borderRadius: "6px", background: "#243041" }}></span>
          <span style={{ position: "absolute", left: "24px", right: "24px", top: "58px", height: "30px", borderRadius: "6px", background: "#243041" }}></span>
          <span style={{ position: "absolute", left: "24px", right: "24px", top: "96px", height: "34px", boxSizing: "border-box", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 12px", borderRadius: "6px", background: "#163226", border: "1.5px solid #4ade80", animation: "hx-newRow 6s ease-out infinite" }}>Bill 1042<svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="2.6" style={{ animation: "hx-pop60 6s infinite" }}><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span>
          <span style={{ position: "absolute", left: "24px", bottom: "12px", fontSize: "13px", color: "#bbf7d0" }}>Seen in the list. Only then "Done".</span>
        </div>
        <h3 style={{ margin: "0", fontSize: "18px" }}>Proof, not hope</h3>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <div role="img" aria-label="Animation: a banner asks the person to tick a box, they tick it and the run resumes" style={{ position: "relative", height: "170px", overflow: "hidden", borderRadius: "14px", background: "#f8fafc", color: "#111827", fontFamily: "system-ui, sans-serif", fontSize: "13px" }}>
          <div style={{ position: "absolute", left: "0", right: "0", top: "0", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px", padding: "12px 14px", background: "#1b1c33", color: "#fff", animation: "hx-slideDown 6s ease-out infinite" }}>Your turn: tick the box<span style={{ padding: "6px 12px", borderRadius: "6px", background: "#2f6fed", fontWeight: "600", whiteSpace: "nowrap", animation: "hx-pressResume 6s infinite" }}>Resume</span></div>
          <div style={{ position: "absolute", left: "24px", top: "86px", display: "flex", alignItems: "center", gap: "12px", padding: "14px 16px", borderRadius: "8px", border: "1px solid #cbd5e1", background: "#fff" }}><span style={{ position: "relative", width: "20px", height: "20px", border: "2px solid #64748b", borderRadius: "4px" }}><span style={{ position: "absolute", left: "-1px", top: "-3px", animation: "hx-tickBox 6s infinite" }}><svg width="22" height="22" viewBox="0 0 16 16" fill="none" stroke="#16a34a" strokeWidth="2.6"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>I am a person</div>
        </div>
        <h3 style={{ margin: "0", fontSize: "18px" }}>Hands you the browser</h3>
      </div>
    </div>
      </div>
    </section>
  )
}
