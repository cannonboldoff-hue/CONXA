// node st13.mjs <n> [shotName] : select Human Edit step n (1-based number badge) and print the editor pane; optional screenshot
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const n = process.argv[2];
const ok = await s.evaluate((n) => { const el = [...document.querySelectorAll("div,span")].filter((e) => e.children.length === 0 && e.textContent.trim() === n && e.getBoundingClientRect().x > 340 && e.getBoundingClientRect().x < 420)[0]; if (!el) return false; el.scrollIntoView({ block: "center" }); el.click(); return true; }, n);
await new Promise((z) => setTimeout(z, 1200));
console.log("clicked", ok);
if (process.argv[3]) await s.screenshot({ path: path.join(here, `../evidence/WF-013/retest2/${process.argv[3]}.png`) });
await b.close();
