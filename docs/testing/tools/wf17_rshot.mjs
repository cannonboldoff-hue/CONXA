// raw-CDP screenshot of the Studio renderer: node wf17_rshot.mjs <port> <out.png>
import fs from "node:fs";
const [port, out] = process.argv.slice(2);
const t = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((x) => x.type === "page" && /localhost:517/.test(x.url));
const ws = new WebSocket(t.webSocketDebuggerUrl);
ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: "Page.captureScreenshot", params: { format: "png" } }));
ws.onmessage = (m) => { fs.writeFileSync(out, Buffer.from(JSON.parse(m.data).result.data, "base64")); console.log("shot"); process.exit(0); };
setTimeout(() => process.exit(1), 10000);
