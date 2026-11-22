// WF-19: raw-CDP eval/shot inside one of Execute's PANEL pages (the run's own views). node wf19_pg.mjs <cdp> <url-regex> eval <js> | shot <file> | type <text> (Input.insertText)
const [port, rx, op, a] = process.argv.slice(2);
const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const t = list.find((x) => x.type === "page" && new RegExp(rx).test(x.url));
if (!t) { console.log("no such panel page; have:", list.map((x) => x.url.slice(0, 80)).join(" | ")); process.exit(2); }
const ws = new WebSocket(t.webSocketDebuggerUrl); let id = 0; const pend = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); } };
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); setTimeout(() => res({ timeout: true }), 8000); });
if (op === "eval") { const r = await send("Runtime.evaluate", { expression: a, returnByValue: true, awaitPromise: true }); console.log(JSON.stringify(r.result?.result?.value ?? r)); }
if (op === "shot") { const r = await send("Page.captureScreenshot", { format: "png" }); if (r.result) { (await import("node:fs")).writeFileSync(a, Buffer.from(r.result.data, "base64")); console.log("shot"); } else console.log("shot failed/timeout"); }
process.exit(0);
