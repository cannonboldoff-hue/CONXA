// node pw.mjs <cdp> <role> <name-regex>  real click
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
await s.getByRole(process.argv[3], { name: new RegExp(process.argv[4], "i") }).first().click({ timeout: 8000 });
console.log("clicked");
await b.close();
