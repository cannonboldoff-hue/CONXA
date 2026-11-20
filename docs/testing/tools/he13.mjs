// node he13.mjs  : click Human Edit, screenshot to evidence/WF-013/retest2/<name>.png ; prints visible list
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
await s.getByRole("button", { name: /^Human Edit$/ }).first().click();
await new Promise((z) => setTimeout(z, 2500));
await s.screenshot({ path: path.join(here, `../evidence/WF-013/retest2/${process.argv[2] || "he"}.png`) });
console.log((await s.evaluate(() => document.body.innerText)).split("\n").slice(0, 80).join(" | "));
await b.close();
