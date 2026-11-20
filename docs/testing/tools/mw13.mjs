// node mw13.mjs <x> <y> <dy> [shot] : wheel scroll at point then optional screenshot
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const [x, y, dy] = process.argv.slice(2, 5).map(Number);
await s.mouse.move(x, y); await s.mouse.wheel(0, dy); await new Promise((z) => setTimeout(z, 700));
if (process.argv[5]) await s.screenshot({ path: path.join(here, `../evidence/WF-013/retest2/${process.argv[5]}.png`) });
await b.close();
