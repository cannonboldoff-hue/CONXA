// Hover the last chat text matching arg and list buttons near it. node hover.mjs <cdp> "<text>" <out.png>
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, text, out] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const l = s.getByText(text, { exact: false }).last(); await l.scrollIntoViewIfNeeded(); await l.hover(); await s.waitForTimeout(600);
await s.screenshot({ path: out });
console.log(JSON.stringify(await s.evaluate(() => [...document.querySelectorAll("button")].map((x) => x.getAttribute("aria-label") || x.title || "").filter(Boolean).slice(-25))));
await b.close();
