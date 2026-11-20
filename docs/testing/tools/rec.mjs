// Trusted OS-level actions on the Studio recording pane. Usage: node rec.mjs <cdpPort> '<json array>'
// Steps: {click:"css"} {type:"text"} {key:"{TAB}"} {wait:ms} {js:"expr"} (js runs in the recording page, result printed)
// Pane calibration (Studio window at default spot): page x0=640,y0=150 screen px, scale k=1.131 (page px per screen px). Override with PX0 PY0 OSK.
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, json] = process.argv.slice(2);
const X0 = Number(process.env.PX0 || 640), Y0 = Number(process.env.PY0 || 150), K = Number(process.env.OSK || 1.131);
const ps = (...a) => execFileSync("powershell", ["-NoProfile", "-File", path.join(here, "osinput.ps1"), ...a]);
const sleep = (ms) => new Promise((z) => setTimeout(z, ms));
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const pages = b.contexts().flatMap((c) => c.pages());
const p = pages.filter((x) => !/localhost:517\d/.test(x.url())).pop();
const res = [];
ps("focus"); await sleep(400);
for (const s of JSON.parse(json)) {
  if (s.click) {
    const r = await p.evaluate((sel) => { const e = document.querySelector(sel); if (!e) return null; e.scrollIntoView({ block: "center" }); const q = e.getBoundingClientRect(); return [q.x + q.width / 2, q.y + q.height / 2]; }, s.click);
    if (!r) { res.push("NOEL " + s.click); continue; }
    await sleep(120);
    const r2 = await p.evaluate((sel) => { const q = document.querySelector(sel).getBoundingClientRect(); return [q.x + q.width / 2, q.y + q.height / 2]; }, s.click);
    ps("click", String(Math.round(X0 + r2[0] / K)), String(Math.round(Y0 + r2[1] / K))); res.push("click " + s.click);
  } else if (s.type !== undefined) { ps("type", s.type); res.push("type"); }
  else if (s.key) { ps("key", s.key); res.push("key " + s.key); }
  else if (s.wait) await sleep(s.wait);
  else if (s.js) res.push(JSON.stringify(await p.evaluate(s.js)).slice(0, 1500));
  await sleep(s.pause ?? 250);
}
console.log(JSON.stringify(res));
await b.close();
