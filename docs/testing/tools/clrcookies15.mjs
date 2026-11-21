// Test tool: clear cookies+storage on the run page(s) mid-run. Usage: node clrcookies15.mjs <cdp> <urlRegex>
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, re] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
for (const c of b.contexts()) { for (const p of c.pages()) if (new RegExp(re).test(p.url())) { console.log("page", p.url()); await c.clearCookies(); await p.evaluate(() => { localStorage.clear(); sessionStorage.clear(); }).catch(() => {}); console.log("cleared"); } }
process.exit(0);
