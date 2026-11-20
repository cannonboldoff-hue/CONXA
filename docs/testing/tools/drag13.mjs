// node drag13.mjs <x> <y> <toY>
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const [x, y, toY] = process.argv.slice(2).map(Number);
await s.mouse.move(x, y); await s.mouse.down();
await s.mouse.move(x, y + 8, { steps: 4 });
const dir = Math.sign(toY - y);
for (let yy = y + 8; Math.abs(yy - toY) > 10; yy += dir * 10) await s.mouse.move(x + 2, yy, { steps: 2 });
await s.mouse.move(x + 2, toY, { steps: 4 }); await new Promise((z) => setTimeout(z, 500));
await s.mouse.up(); await new Promise((z) => setTimeout(z, 700)); if (process.env.SHOT) await s.screenshot({ path: process.env.SHOT }); console.log("TOAST", (await s.evaluate(() => document.body.innerText)).split(String.fromCharCode(10)).filter((l) => /come straight|Something went wrong|can.t be undone can/.test(l)));
await new Promise((z) => setTimeout(z, 1300));
console.log(await s.evaluate(() => document.body.innerText.match(/\n\d+\n[^\n]+/g)?.slice(0, 12).join("|").replace(/\n/g, " ")));
await b.close();
