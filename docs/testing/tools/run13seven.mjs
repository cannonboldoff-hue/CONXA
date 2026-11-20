import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const sleep = (ms) => new Promise((z) => setTimeout(z, ms));
const txt = () => s.evaluate(() => document.body.innerText);
const back = s.getByText("Back to conversation"); if (await back.count()) { await back.first().click(); await sleep(2000); }
await s.getByRole("button", { name: /^Test (it|again)$/ }).last().click(); await sleep(2500);
await s.getByRole("button", { name: /^Run test/ }).click();
const seen = new Set(); const t0 = Date.now();
for (;;) {
  await sleep(1500); const t = await txt(); const tail = t.slice(t.lastIndexOf("Test inputs"));
  for (const m of tail.match(/Step \d+ of \d+[^\n]*|Paused before[^\n]*|A person needs to confirm|Test passed[^\n]*|Test failed at[^\n]*|The AI agent gave up[^\n]*|Passed up to[^\n]*/g) || []) if (!seen.has(m)) { seen.add(m); console.log(Math.round((Date.now() - t0) / 1000) + "s", m); }
  if (/Paused before “Click Save/.test(tail) && await s.getByRole("button", { name: /Run the last/ }).count()) { await s.getByRole("button", { name: /Run the last/ }).click(); await sleep(2500); continue; }
  if (/A person needs to confirm/.test(tail) || /Test passed|Test failed at|Not a full pass/.test(tail) || Date.now() - t0 > 240000) break;
}
const t = await txt(); console.log(t.slice(t.lastIndexOf("Test inputs")).slice(0, 1800).replace(/\n+/g, " | "));
await s.screenshot({ path: path.join(here, "../evidence/WF-013/retest/13.3-run.png") });
await b.close();
