// WF-19: the runtime's own geometry test (page_geometry.js::inputLands + size) replayed by hand on a panel page: size, DPR, and where a CDP mouse move at (97,61) arrives.
const [port, rx] = process.argv.slice(2);
const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const t = list.find((x) => x.type === "page" && new RegExp(rx).test(x.url));
const ws = new WebSocket(t.webSocketDebuggerUrl); let id = 0; const pend = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); } };
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (e) => (await send("Runtime.evaluate", { expression: e, returnByValue: true })).result?.result?.value;
console.log("size", JSON.stringify(await ev("[innerWidth, innerHeight, devicePixelRatio, screen.width, screen.height]")));
await ev("window.__mm=null;addEventListener('mousemove',e=>{window.__mm=window.__mm||[e.clientX,e.clientY]},{once:true,capture:true})");
await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 97, y: 61 });
await new Promise((r) => setTimeout(r, 300));
console.log("mouse aimed (97,61) arrived at", JSON.stringify(await ev("window.__mm")));
process.exit(0);
