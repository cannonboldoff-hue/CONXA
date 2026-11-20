// Send a message in Studio's chat box and watch the reply. Usage: node chat.mjs <cdp> "<message>" [maxSec]
// Prints: ms until the "replying" indicator showed, change samples (streaming), final conversation tail.
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, msg, max = "90"] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const tail = () => s.evaluate(() => { const t = document.body.innerText; const c = t.slice(t.lastIndexOf("\nConversation\n")); return c.slice(c.lastIndexOf(window.__m || "\u0000")); });
await s.evaluate((m) => { window.__m = m; const t = [...document.querySelectorAll("textarea")].find(x=>/Ask about/.test(x.placeholder)); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(t, m); t.dispatchEvent(new Event("input", { bubbles: true })); }, msg);
await new Promise((z) => setTimeout(z, 300));
const t0 = Date.now();
await s.evaluate(() => document.querySelector("button[aria-label=Send]").click());
let thinkAt = null, samples = [], last = "";
while ((Date.now() - t0) / 1000 < +max) {
  await new Promise((z) => setTimeout(z, 300));
  const t = await tail();
  if (thinkAt === null && /replying|Thinking/i.test(t)) thinkAt = Date.now() - t0;
  if (t !== last) { samples.push([Date.now() - t0, t.length]); last = t; }
  const busy = /replying|Thinking/i.test(t) || /\nStop\s*$/.test(t);
  if (Date.now() - t0 > 3000 && !busy && samples.length && Date.now() - t0 - samples[samples.length - 1][0] > 2500) break;
}
console.log(JSON.stringify({ thinkAt, changes: samples.length, firstSamples: samples.slice(0, 15), totalMs: Date.now() - t0 }));
console.log("----TAIL----\n" + last.slice(-2500));
await b.close();
