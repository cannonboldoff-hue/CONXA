// node pt.mjs <cdp> "<exact text>" [nth] : real click on element with exact text
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
await s.getByText(process.argv[3], { exact: true }).nth(Number(process.argv[4] || 0)).click({ timeout: 8000 });
console.log("ok"); await b.close();
