// Print innerWidth/innerHeight of the run page(s). Usage: node wid.mjs <cdp>
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const out = [];
for (const p of b.contexts().flatMap((c) => c.pages())) { if (/localhost:517|conxa_shield/.test(p.url())) continue; out.push({ u: p.url().slice(0, 50), w: await p.evaluate(() => [innerWidth, innerHeight, document.visibilityState]).catch((e) => String(e).slice(0, 40)) }); }
console.log(JSON.stringify(out)); process.exit(0);
