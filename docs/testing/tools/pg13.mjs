import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const p = b.contexts().flatMap((c) => c.pages()).find((x) => x.url().includes(process.argv[2]));
console.log(p ? await p.evaluate(process.argv[3]) : "no page");
await b.close();
