'use client'

import { useState } from 'react'
import { H2, WRAP, pill } from './ui'

export function HomeData() {
  const [mode, setMode] = useState<'build' | 'run'>('build')
  const isBuild = mode === 'build'
  const isRun = !isBuild
  return (
    <section id="data" aria-labelledby="data-title" className={`${WRAP} py-16`}>
      <h2 id="data-title" className={H2}>
        What leaves your computer.
      </h2>
      <div role="group" aria-label="Choose a moment" className="mt-5 flex flex-wrap gap-2.5">
        <button type="button" aria-pressed={isBuild} onClick={() => setMode('build')} className={pill(isBuild)}>
          While you set it up
        </button>
        <button type="button" aria-pressed={isRun} onClick={() => setMode('run')} className={pill(isRun)}>
          While it runs
        </button>
      </div>

  <div style={{ marginTop: "28px", display: "flex", flexWrap: "wrap", alignItems: "stretch", gap: "12px" }}>
    <div style={{ flex: "1 1 250px", display: "flex", flexDirection: "column", alignItems: "center", gap: "14px", padding: "24px", borderRadius: "16px", border: "2px solid #22d3ee", background: "#0b0f14", textAlign: "center" }}>
      <svg aria-hidden="true" width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" strokeWidth="1.2"><rect x="4" y="5" width="16" height="11" rx="1.5"></rect><path d="M2 19h20"></path></svg>
      <h3 style={{ margin: "0", fontSize: "18px" }}>Your computer</h3>
      <span style={{ display: "inline-flex", alignItems: "center", gap: "8px", padding: "8px 14px", borderRadius: "999px", background: "#163226", color: "#bbf7d0", fontSize: "14px" }}><svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#4ade80" strokeWidth="1.6"><rect x="3" y="7" width="10" height="7" rx="1.5"></rect><path d="M5 7V5a3 3 0 0 1 6 0v2"></path></svg>Your saved logins stay here</span>
      {isBuild && (<><span style={{ fontSize: "14px", color: "#c9ced6" }}>The recording and your files</span></>)}
      {isRun && (<><span style={{ fontSize: "14px", color: "#c9ced6" }}>The browser and the pages it opens</span></>)}
    </div>
    <div aria-hidden="true" style={{ flex: "0 1 90px", minWidth: "70px", alignSelf: "center", position: "relative", height: "40px" }}>
      <span style={{ position: "absolute", left: "0", right: "0", top: "19px", borderTop: "2px dashed rgba(255,255,255,0.3)" }}></span>
      <span style={{ position: "absolute", top: "6px", left: "40%", width: "28px", height: "28px", borderRadius: "8px", background: "#22d3ee", display: "flex", alignItems: "center", justifyContent: "center", animation: "hx-packet 2.6s ease-in-out infinite" }}><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#06080b" strokeWidth="1.8"><path d="M2 4h8v8H2zM10 7l4-2v6l-4-2"></path></svg></span>
    </div>
    <div style={{ flex: "1 1 250px", display: "flex", flexDirection: "column", alignItems: "center", gap: "14px", padding: "24px", borderRadius: "16px", border: "1px solid rgba(255,255,255,0.18)", background: "#0b0f14", textAlign: "center" }}>
      <svg aria-hidden="true" width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.2"><path d="M7 18h10a4 4 0 0 0 .6-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z"></path></svg>
      <h3 style={{ margin: "0", fontSize: "18px" }}>Conxa Cloud</h3>
      {isBuild && (<><span style={{ display: "flex", flexDirection: "column", gap: "8px", fontSize: "14px" }}><span style={{ padding: "8px 12px", borderRadius: "8px", background: "#17202c" }}>A copy of the video, passwords blurred</span><span style={{ padding: "8px 12px", borderRadius: "8px", background: "#17202c" }}>Details of the pages you recorded</span><span style={{ padding: "8px 12px", borderRadius: "8px", background: "#17202c" }}>The finished task, when you publish</span></span></>)}
      {isRun && (<><span style={{ display: "flex", flexDirection: "column", gap: "8px", fontSize: "14px" }}><span style={{ padding: "8px 12px", borderRadius: "8px", background: "#17202c" }}>Whether each step worked, and how long it took</span><span style={{ padding: "8px 12px", borderRadius: "8px", background: "#17202c" }}>Computer name, user name and system version</span></span></>)}
    </div>
    <div aria-hidden="true" style={{ flex: "0 1 90px", minWidth: "70px", alignSelf: "center", position: "relative", height: "40px" }}>
      <span style={{ position: "absolute", left: "0", right: "0", top: "19px", borderTop: "2px dashed rgba(255,255,255,0.3)" }}></span>
      <span style={{ position: "absolute", top: "12px", left: "40%", width: "16px", height: "16px", borderRadius: "50%", background: "#5eead4", animation: "hx-packet 2.6s ease-in-out 1.3s infinite" }}></span>
    </div>
    <div style={{ flex: "1 1 250px", display: "flex", flexDirection: "column", alignItems: "center", gap: "14px", padding: "24px", borderRadius: "16px", border: "1px dashed rgba(255,255,255,0.32)", textAlign: "center" }}>
      <svg aria-hidden="true" width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#c9ced6" strokeWidth="1.2"><rect x="6" y="6" width="12" height="12" rx="2"></rect><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4M10 10h4v4h-4z"></path></svg>
      {isBuild && (<><span style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}><h3 style={{ margin: "0", fontSize: "18px" }}>AI services</h3><span style={{ padding: "8px 12px", borderRadius: "8px", background: "#17202c", fontSize: "14px" }}>AI studies the video, then that service deletes it</span><span style={{ padding: "8px 12px", borderRadius: "8px", background: "#17202c", fontSize: "14px" }}>AI reads the page details</span></span></>)}
      {isRun && (<><span style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}><h3 style={{ margin: "0", fontSize: "18px" }}>Your AI assistant</h3><span style={{ padding: "8px 12px", borderRadius: "8px", background: "#17202c", fontSize: "14px" }}>The results it read from the page</span><span style={{ padding: "8px 12px", borderRadius: "8px", background: "#17202c", fontSize: "14px" }}>A picture of the screen, only if it needs help</span></span></>)}
    </div>
  </div>
      <details className="mt-4 text-sm text-[#9ba3af]">
        <summary className="min-h-11 cursor-pointer leading-[44px] text-[#f4f5f7]">The fine print</summary>
        <p className="mt-1">
          Your saved logins are never uploaded or put into a task. Reports about each run never include screenshots
          or anything that was typed in. If an unexpected pop-up appears, the words in its title and the names of its
          buttons may be reported, with numbers, email addresses and quoted text taken out. If you chat with the
          built-in AI in the Conxa Execute app, the chat goes through Conxa to an AI company.
        </p>
      </details>
    </section>
  )
}
