import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
await s.mouse.move(450, 700); await s.mouse.move(520, 962, { steps: 6 }); await new Promise((z) => setTimeout(z, 600));
console.log(JSON.stringify(await s.evaluate(() => [...document.querySelectorAll("svg")].filter(x=>{const r=x.getBoundingClientRect();return r.x>650&&r.x<740&&r.y>800}).map((x) => [x.getAttribute("class"), x.parentElement.tagName, x.parentElement.getAttribute("aria-label"), x.parentElement.className.slice(0,60), Math.round(x.getBoundingClientRect().x), Math.round(x.getBoundingClientRect().y)]))));
await s.screenshot({ path: path.join(here, `../evidence/WF-013/retest2/hv.png`) });
await b.close();
