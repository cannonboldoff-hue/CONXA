import { BAND, WRAP } from './ui'

const CHIPS = [
  'Undo an update',
  'See what ran and what it fixed',
  'Download a full activity record (Pro)',
  'Share outside your company (Starter and up)',
]

export function HomeShare() {
  return (
    <section id="share" aria-labelledby="share-title" className={BAND}>
      <div className={`${WRAP} flex flex-wrap items-center gap-x-14 gap-y-8 py-16`}>
        <div className="flex-[1_1_340px]">
          <h2
            id="share-title"
            className="m-0 text-[clamp(2rem,3.4vw,2.75rem)] font-semibold leading-[1.1] tracking-[-0.03em]"
          >
            Update it once. Every computer gets it.
          </h2>
          <ul className="m-0 mt-5 flex list-none flex-wrap gap-2.5 p-0 text-sm">
            {CHIPS.map((c) => (
              <li key={c} className="rounded-full bg-[#17202c] px-3.5 py-2">
                {c}
              </li>
            ))}
          </ul>
        </div>
    <div role="img" aria-label="Animation: version 1.3 is published and six computers get it one after another" style={{ flex: "999 1 560px", minWidth: "0", position: "relative", display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "center", gap: "24px", padding: "28px", borderRadius: "18px", background: "#06080b", border: "1px solid rgba(255,255,255,0.12)" }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "8px", padding: "18px 22px", borderRadius: "14px", border: "2px solid #22d3ee" }}>
        <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" strokeWidth="1.4"><path d="M12 2 21 7v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10"></path></svg>
        <strong>Version 1.3</strong>
      </div>
      <span style={{ width: "90px", height: "3px", background: "#22d3ee", transformOrigin: "left center", animation: "hx-beam 7s ease-out infinite" }}></span>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 74px)", gap: "14px" }}>
        <span style={{ position: "relative", height: "58px", display: "flex", alignItems: "center", justifyContent: "center", borderRadius: "10px", background: "#17202c" }}><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="1.4"><rect x="4" y="5" width="16" height="11" rx="1.5"></rect><path d="M2 19h20"></path></svg><span style={{ position: "absolute", inset: "0", borderRadius: "10px", border: "2px solid #4ade80", display: "flex", alignItems: "flex-start", justifyContent: "flex-end", padding: "3px", animation: "hx-lit1 7s infinite" }}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>
        <span style={{ position: "relative", height: "58px", display: "flex", alignItems: "center", justifyContent: "center", borderRadius: "10px", background: "#17202c" }}><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="1.4"><rect x="4" y="5" width="16" height="11" rx="1.5"></rect><path d="M2 19h20"></path></svg><span style={{ position: "absolute", inset: "0", borderRadius: "10px", border: "2px solid #4ade80", display: "flex", alignItems: "flex-start", justifyContent: "flex-end", padding: "3px", animation: "hx-lit2 7s infinite" }}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>
        <span style={{ position: "relative", height: "58px", display: "flex", alignItems: "center", justifyContent: "center", borderRadius: "10px", background: "#17202c" }}><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="1.4"><rect x="4" y="5" width="16" height="11" rx="1.5"></rect><path d="M2 19h20"></path></svg><span style={{ position: "absolute", inset: "0", borderRadius: "10px", border: "2px solid #4ade80", display: "flex", alignItems: "flex-start", justifyContent: "flex-end", padding: "3px", animation: "hx-lit3 7s infinite" }}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>
        <span style={{ position: "relative", height: "58px", display: "flex", alignItems: "center", justifyContent: "center", borderRadius: "10px", background: "#17202c" }}><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="1.4"><rect x="4" y="5" width="16" height="11" rx="1.5"></rect><path d="M2 19h20"></path></svg><span style={{ position: "absolute", inset: "0", borderRadius: "10px", border: "2px solid #4ade80", display: "flex", alignItems: "flex-start", justifyContent: "flex-end", padding: "3px", animation: "hx-lit4 7s infinite" }}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>
        <span style={{ position: "relative", height: "58px", display: "flex", alignItems: "center", justifyContent: "center", borderRadius: "10px", background: "#17202c" }}><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="1.4"><rect x="4" y="5" width="16" height="11" rx="1.5"></rect><path d="M2 19h20"></path></svg><span style={{ position: "absolute", inset: "0", borderRadius: "10px", border: "2px solid #4ade80", display: "flex", alignItems: "flex-start", justifyContent: "flex-end", padding: "3px", animation: "hx-lit5 7s infinite" }}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>
        <span style={{ position: "relative", height: "58px", display: "flex", alignItems: "center", justifyContent: "center", borderRadius: "10px", background: "#17202c" }}><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="1.4"><rect x="4" y="5" width="16" height="11" rx="1.5"></rect><path d="M2 19h20"></path></svg><span style={{ position: "absolute", inset: "0", borderRadius: "10px", border: "2px solid #4ade80", display: "flex", alignItems: "flex-start", justifyContent: "flex-end", padding: "3px", animation: "hx-lit6 7s infinite" }}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="2.4"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span></span>
      </div>
    </div>
      </div>
    </section>
  )
}
