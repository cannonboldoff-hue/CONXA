import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
await s.locator("textarea").last().fill(process.argv[3]);
await s.getByRole("button", { name: /^Send$/ }).last().click();
const t0 = Date.now(), rows = [];
while (Date.now() - t0 < 40000) { const p = await s.evaluate(() => { const bt = [...document.querySelectorAll("button")].find((x) => /New conversation/.test(x.innerText + (x.getAttribute("aria-label")||""))); return { present: !!bt, dis: bt ? bt.disabled : null, busy: /replying|Working on a reply/i.test(document.body.innerText) }; }); rows.push([Date.now()-t0, p.present, p.dis, p.busy]); if (Date.now()-t0 > 4000 && !p.busy) break; await s.waitForTimeout(400); }
console.log(JSON.stringify({ busy: rows.filter(r=>r[3]).map(r=>[r[1],r[2]]).slice(0,4), n: rows.filter(r=>r[3]).length, final: rows.at(-1) }));
await b.close();
