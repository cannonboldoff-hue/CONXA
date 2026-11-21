// Hold network chaos on EVERY run page (new ones too) for N seconds. Usage: node net15b.mjs <cdp> <offline|slow> <seconds> [startDelayAfterPagesSec]
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, mode, secs] = process.argv.slice(2);
const cond = { offline: { offline: true, latency: 0, downloadThroughput: 0, uploadThroughput: 0 },
  slow: { offline: false, latency: 500, downloadThroughput: 40 * 1024, uploadThroughput: 20 * 1024 } }[mode];
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const seen = new Map(); const end = Date.now() + secs * 1000; let t0 = null;
while (Date.now() < end) {
  for (const p of b.contexts().flatMap((c) => c.pages())) {
    if (/localhost:517\d/.test(p.url()) || /conxa_shield/.test(p.url())) continue;
    if (!seen.has(p)) { const cs = await p.context().newCDPSession(p); await cs.send("Network.enable"); await cs.send("Network.emulateNetworkConditions", cond); seen.set(p, cs); console.log(new Date().toISOString(), "applied", mode, p.url()); }
  }
  await new Promise((r) => setTimeout(r, 400));
}
for (const cs of seen.values()) await cs.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }).catch(() => {});
console.log(new Date().toISOString(), "cleared");
process.exit(0);
