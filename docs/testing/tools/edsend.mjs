// In the open inline editor: set text, click its Send, wait, print tail. node edsend.mjs <cdp> "<text>" [sec]
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const ta = s.locator("textarea").first();
await ta.fill(process.argv[3]);
await s.getByRole("button", { name: /^Send$/ }).first().click();
const t0 = Date.now(); const max = +(process.argv[4] || 60) * 1000;
while (Date.now() - t0 < max) { await s.waitForTimeout(1000); const t = await s.evaluate(() => document.body.innerText); if (Date.now() - t0 > 4000 && !/replying|Working on a reply/i.test(t)) break; }
console.log(await s.evaluate(() => { const t = document.body.innerText; const c = t.slice(t.lastIndexOf("\nConversation\n")); return c.slice(-1800); }));
await b.close();
