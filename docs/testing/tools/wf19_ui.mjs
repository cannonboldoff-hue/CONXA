// WF-19 raw-CDP helper for Conxa Execute's own window (no Playwright: its attach can disturb Execute's panel views).
// node wf19_ui.mjs <cdp> text [n] | eval <js> | shot <file> | click <text-regex> [css] | fill <placeholder-regex> <text> | targets | key <Enter>
const [port, op, a, b] = process.argv.slice(2);
const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
if (op === "targets") { for (const t of list) console.log(t.type, t.id.slice(0, 8), t.url.slice(0, 110)); process.exit(0); }
const t = list.find((x) => x.type === "page" && /localhost:5175/.test(x.url));
if (!t) { console.log("no Execute main page"); process.exit(2); }
const ws = new WebSocket(t.webSocketDebuggerUrl); let id = 0; const pend = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); } };
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (js) => { const r = await send("Runtime.evaluate", { expression: js, returnByValue: true, awaitPromise: true }); return r.result?.result?.value ?? r.result?.exceptionDetails?.text; };
if (op === "text") console.log(((await ev("document.body.innerText")) || "").replace(/\n{2,}/g, "\n").slice(0, Number(a || 4000)));
if (op === "eval") console.log(JSON.stringify(await ev(a)));
if (op === "shot") { const r = await send("Page.captureScreenshot", { format: "png" }); (await import("node:fs")).writeFileSync(a, Buffer.from(r.result.data, "base64")); console.log("shot", a); }
if (op === "click") {
  const js = `(()=>{const re=new RegExp(${JSON.stringify(a)},'i');const els=[...document.querySelectorAll(${JSON.stringify(b || "button,a,[role=button],[role=tab],[role=menuitem],[role=option],li,div")})].filter(e=>re.test((e.innerText||e.getAttribute('aria-label')||e.title||'').trim())&&e.offsetParent!==null);
  els.sort((x,y)=>(x.innerText||'').length-(y.innerText||'').length);const e=els[0];if(!e)return 'NOT FOUND';e.click();return 'clicked: '+(e.innerText||e.getAttribute('aria-label')||'').slice(0,60)})()`;
  console.log(await ev(js));
}
if (op === "fill") {
  const js = `(()=>{const re=new RegExp(${JSON.stringify(a)},'i');const e=[...document.querySelectorAll('textarea,input')].find(x=>re.test((x.placeholder||'')+(x.getAttribute('aria-label')||'')))||document.querySelector('textarea');if(!e)return 'NOT FOUND';
  const set=Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set;set.call(e,${JSON.stringify(b)});e.dispatchEvent(new Event('input',{bubbles:true}));e.focus();return 'filled '+(e.placeholder||'')})()`;
  console.log(await ev(js));
}
if (op === "key") { for (const type of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", { type, key: a, code: a, windowsVirtualKeyCode: a === "Enter" ? 13 : 0, text: a === "Enter" && type === "keyDown" ? "\r" : undefined }); console.log("key", a); }
process.exit(0);
