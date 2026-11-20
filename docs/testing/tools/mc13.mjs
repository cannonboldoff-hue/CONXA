// node mc13.mjs <x> <y> [shot] : move, click at point (real mouse), optional screenshot
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const [x, y] = process.argv.slice(2, 4).map(Number);
await s.mouse.move(x - 100, y); await s.mouse.move(x, y, { steps: 5 }); await new Promise((z) => setTimeout(z, 300)); await s.mouse.click(x, y); await new Promise((z) => setTimeout(z, 1200));
if (process.argv[4]) await s.screenshot({ path: path.join(here, `../evidence/WF-013/retest2/${process.argv[4]}.png`) });
await b.close();
