// Raw-CDP eval in any target whose url contains <sub>. node wf17_raw.mjs <port> <sub> <js>   (Playwright connect can hang while Studio probes a sign-in)
const [port, sub, ...js] = process.argv.slice(2);
const t = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((x) => x.type === "page" && x.url.includes(sub));
if (!t) { console.log("NO TARGET"); process.exit(3); }
const ws = new WebSocket(t.webSocketDebuggerUrl);
ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression: js.join(" "), returnByValue: true, awaitPromise: true } }));
ws.onmessage = (m) => { const r = JSON.parse(m.data); console.log(JSON.stringify(r.result?.result?.value ?? r).slice(0, 1500)); process.exit(0); };
setTimeout(() => process.exit(1), 10000);
