// Click the Edit link (real click), screenshot, report textarea. node edclick.mjs <cdp> <png>
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
await s.getByRole("button", { name: /^Edit$/ }).last().click();
await s.waitForTimeout(700);
await s.screenshot({ path: process.argv[3] });
console.log(JSON.stringify(await s.evaluate(() => ({ ta: [...document.querySelectorAll("textarea")].map((t) => t.value + "|" + t.placeholder), btns: [...document.querySelectorAll("button")].map((x) => x.innerText.trim() || x.getAttribute("aria-label")).filter(Boolean).slice(-10) }))));
await b.close();
