// WF-19 retest scenario runner: node wf19r_scn.mjs <cdp> <skill> <step,step,...> [outPrefix]
// steps: probe | hide | show | runs | skills | newchat | min | restore | size:WxH (physical px) | wait:ms | shot:name
// Starts a chat run, then runs the steps while the run is live, logging time + what the panel page reports. Panel probe = size, DPR, where a CDP mouse move aimed at (97,61) lands (the runtime's own geometry test).
import { execFileSync } from "node:child_process"; import fs from "node:fs";
const [port, skill, stepsArg, prefix = "scn"] = process.argv.slice(2);
const T0 = Date.now(); const log = (...a) => console.log(((Date.now() - T0) / 1000).toFixed(1).padStart(5), ...a);
const list = async () => (await (await fetch(`http://127.0.0.1:${port}/json`)).json());
const cdp = async (t) => { const ws = new WebSocket(t.webSocketDebuggerUrl); let id = 0; const pend = new Map();
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); } };
  await new Promise((r) => (ws.onopen = r));
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); setTimeout(() => res({ timeout: true }), 6000); });
  return { send, ev: async (e) => (await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true })).result?.result?.value, close: () => ws.close() }; };
const main = await cdp((await list()).find((x) => /localhost:5175/.test(x.url)));
const click = (re, sel = "button,a") => main.ev(`(()=>{const re=new RegExp(${JSON.stringify(re)},'i');const e=[...document.querySelectorAll(${JSON.stringify(sel)})].filter(e=>re.test((e.title||e.getAttribute('aria-label')||e.innerText||'').trim())&&e.offsetParent!==null).sort((x,y)=>(x.innerText||'').length-(y.innerText||'').length)[0];if(!e)return 'NOT FOUND';e.click();return 'ok'})()`);
const fill = (txt) => main.ev(`(()=>{const e=document.querySelector('textarea');const s=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;s.call(e,${JSON.stringify(txt)});e.dispatchEvent(new Event('input',{bubbles:true}));e.focus();return 1})()`);
const panels = async () => (await list()).filter((x) => x.type === "page" && !/localhost:5175|conxa_shield|conxa_run=/.test(x.url));
async function probe(tag) {
  const ps = await panels(); if (!ps.length) return log(tag, "NO panel page listed");
  for (const p of ps) { const c = await cdp(p);
    const size = await c.ev("[innerWidth,innerHeight,devicePixelRatio]");
    await c.ev("window.__mm=null;addEventListener('mousemove',e=>{window.__mm=window.__mm||[e.clientX,e.clientY]},{once:true,capture:true})");
    await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 97, y: 61 }); await new Promise((r) => setTimeout(r, 250));
    log(tag, p.url.slice(0, 50), "size/dpr", JSON.stringify(size), "mouse(97,61)->", JSON.stringify(await c.ev("window.__mm"))); c.close(); } }
const ps1 = (...a) => execFileSync("powershell", ["-NoProfile", "-File", new URL("./wf19r_win.ps1", import.meta.url).pathname.slice(1), ...a]).toString().trim();
await click("^New chat"); await new Promise((r) => setTimeout(r, 1800)); await fill(`Run ${skill}`);
await main.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" }); await main.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
for (let i = 0; i < 20; i++) { await new Promise((r) => setTimeout(r, 700)); if ((await click("^Run$")) === "ok") { log("Run clicked"); break; } }
for (let i = 0; i < 30 && !(await panels()).length; i++) await new Promise((r) => setTimeout(r, 300));
for (const s of stepsArg.split(",")) {
  const [k, v] = s.split(":");
  if (k === "probe") await probe("probe"); else if (k === "hide") log("hide", await click("^Hide browser panel$")); else if (k === "show") log("show", await click("Show browser"));
  else if (k === "runs") log("Runs page", await click("^Runs$")); else if (k === "skills") log("Skills page", await click("^Skills$")); else if (k === "newchat") log("New chat", await click("^New chat"));
  else if (k === "min") log("minimize", ps1("min")); else if (k === "restore") log("restore", ps1("restore"));
  else if (k === "size") { const [w, h] = v.split("x"); log("size", ps1("size", w, h)); }
  else if (k === "pshot") { const ps = await panels(); if (!ps.length) log("pshot NO panel"); else { const c = await cdp(ps[0]); const r = await c.send("Page.captureScreenshot", { format: "png" }); if (r.result) { fs.writeFileSync(`${prefix}-${v}.png`, Buffer.from(r.result.data, "base64")); log("panel shot", v, ps[0].url.slice(0, 40), "bytes", r.result.data.length, "vis", await c.ev("document.visibilityState")); } else log("panel shot TIMEOUT (page not answering)", ps[0].url.slice(0, 40)); c.close(); } }
  else if (k === "wait") await new Promise((r) => setTimeout(r, +v));
  else if (k === "shot") { const r = await main.send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(`${prefix}-${v}.png`, Buffer.from(r.result.data, "base64")); log("shot", v); }
  await new Promise((r) => setTimeout(r, 400));
}
for (let i = 0; i < 120 && (await panels()).length; i++) await new Promise((r) => setTimeout(r, 1000));
log("panels gone / run over"); await new Promise((r) => setTimeout(r, 2500)); await click("^Runs$"); await new Promise((r) => setTimeout(r, 1500));
const t = await main.ev("document.body.innerText"); log("RUNS PAGE TOP:", (t || "").split(String.fromCharCode(10)).slice(12, 30).join(" | ").slice(0, 400));
process.exit(0);
