// Test tool: raw-CDP eval in the Studio renderer when Playwright connect hangs (native dialog open). Usage: node cdp_eval.mjs <port> <js>
const [port, ...js] = process.argv.slice(2);
const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const t = list.find((x) => /localhost:517\d/.test(x.url));
const ws = new WebSocket(t.webSocketDebuggerUrl);
ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression: js.join(" "), returnByValue: true, awaitPromise: true } }));
ws.onmessage = (m) => { console.log(JSON.stringify(JSON.parse(m.data).result?.result?.value ?? JSON.parse(m.data)).slice(0, 2500)); process.exit(0); };
setTimeout(() => process.exit(1), 8000);
