// node open13.mjs "<exact workflow name>" : click the sidebar workflow row (exact text), print header text
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
await s.getByText(process.argv[2], { exact: true }).first().click();
await new Promise((z) => setTimeout(z, 2500));
console.log(s.url()); console.log((await s.evaluate(() => document.body.innerText)).replace(/\n+/g, " | ").slice(0, 1500));
await b.close();
