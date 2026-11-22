// CDP-trusted click in the recording page (no OS input). node wf17_click.mjs <cdp> <urlSub> <selector>
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, sub, sel] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const p = b.contexts().flatMap((x) => x.pages()).filter((q) => !/localhost:517\d/.test(q.url())).find((q) => q.url().includes(sub));
const r = await p.evaluate((s) => { const e = document.querySelector(s); const q = e.getBoundingClientRect(); return [q.x + q.width / 2, q.y + q.height / 2, innerWidth, innerHeight]; }, sel);
console.log(r);
await p.mouse.click(r[0], r[1]);
process.exit(0);
