// Click helper: node ui.mjs <cdp> click "<regex over element text, innermost>" | list
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, cmd, arg] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
if (cmd === "click") console.log(await s.evaluate((re) => { const r = new RegExp(re); const els = [...document.querySelectorAll("button,[role=button],[role=menuitem],label,div,span,a")].filter((e) => r.test(e.innerText || "") && ![...e.children].some((c) => r.test(c.innerText || ""))); if (!els.length) return "NONE"; els[0].click(); return "clicked " + els.length + " " + els[0].tagName; }, arg));
await b.close();
