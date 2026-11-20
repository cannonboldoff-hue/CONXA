import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const sleep = (ms) => new Promise((z) => setTimeout(z, ms));
const boxes = s.locator("input[type=checkbox]").filter({ visible: true });
const n = await boxes.count(); console.log("boxes", n, await boxes.evaluateAll(e=>e.map(x=>x.checked)));
for (let i = 0; i < n; i++) { if (await boxes.nth(i).isChecked()) await boxes.nth(i).uncheck(); }
console.log(await boxes.evaluateAll(e=>e.map(x=>x.checked)));
await s.getByRole("button", { name: /^Save step/ }).click(); await sleep(2500);
console.log((await s.evaluate(() => document.body.innerText)).match(/Step saved|Nothing changed|Save failed|[^\n]*can.t come straight[^\n]*/g));
await b.close();
