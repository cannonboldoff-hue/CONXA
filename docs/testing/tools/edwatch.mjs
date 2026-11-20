// Send a message, sample Edit-link count + replying indicator during the reply. node edwatch.mjs <cdp> "<msg>"
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
await s.locator("textarea").last().fill(process.argv[3]);
await s.getByRole("button", { name: /^Send$/ }).last().click();
const t0 = Date.now(), rows = [];
const probe = () => s.evaluate(() => ({ edit: [...document.querySelectorAll("button")].filter((e) => /^\s*Edit\s*$/.test(e.innerText)).length, busy: /replying|Working on a reply/i.test(document.body.innerText) }));
while (Date.now() - t0 < 40000) { const p = await probe(); rows.push([Date.now() - t0, p.edit, p.busy]); if (Date.now() - t0 > 4000 && !p.busy) break; await s.waitForTimeout(400); }
const busyRows = rows.filter((r) => r[2]);
console.log(JSON.stringify({ samples: rows.length, busySamples: busyRows.length, editDuringBusy: busyRows.map((r) => r[1]), final: rows.at(-1) }));
await b.close();
