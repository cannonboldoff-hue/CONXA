// Network chaos on the browser-pane pages via CDP. Usage: node net15.mjs <cdp> <offline|slow|clear|close|info> [seconds]
// offline = page offline; slow = ~400ms latency, 60 KB/s; clear = normal; close = Target.closeTarget on the run page ("Chromium killed"); info = list pages
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, mode, secs] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const getPages = () => b.contexts().flatMap((c) => c.pages()).filter((p) => !/localhost:517\d/.test(p.url()));
let pages = getPages();
for (let i = 0; i < 90 && !pages.length && mode !== "clear"; i++) { await new Promise((r) => setTimeout(r, 1000)); pages = getPages(); }
console.log(JSON.stringify(pages.map((p) => p.url())));
const cond = { offline: { offline: true, latency: 0, downloadThroughput: 0, uploadThroughput: 0 },
  slow: { offline: false, latency: 400, downloadThroughput: 60 * 1024, uploadThroughput: 30 * 1024 },
  clear: { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 } }[mode];
for (const p of pages) {
  const cs = await p.context().newCDPSession(p);
  if (mode === "close") { await cs.send("Target.closeTarget", { targetId: (await cs.send("Target.getTargetInfo")).targetInfo.targetId }).catch((e) => console.log("close err", e.message)); console.log("closed", p.url()); continue; }
  if (cond) { await cs.send("Network.enable"); await cs.send("Network.emulateNetworkConditions", cond); }
}
if (cond && mode !== "clear") { // emulation lives only while this session is attached: hold, then reset
  const ss = []; for (const p of pages) ss.push(await p.context().newCDPSession(p));
  await new Promise((r) => setTimeout(r, (secs || 3600) * 1000));
  for (const c of ss) await c.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }).catch(() => {});
}
console.log("done", mode);
process.exit(0);
