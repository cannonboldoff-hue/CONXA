// New conversation helper. node nc.mjs <cdp> state|click [png]
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const btn = s.getByRole("button", { name: /New conversation/ });
const n = await btn.count();
if (process.argv[3] === "click" && n) { await btn.last().click(); await s.waitForTimeout(1500); }
if (process.argv[4]) await s.screenshot({ path: process.argv[4] });
console.log(JSON.stringify({ buttonCount: n, divider: await s.evaluate(() => (document.body.innerText.match(/New conversation · [^\n]*/g) || []), ), after: await btn.count() }));
await b.close();
