// List chat messages with Edit link presence. node ed.mjs <cdp> [clickEditOn "text"]
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
console.log(await s.evaluate(() => {
  const els = [...document.querySelectorAll("button,a,[role=button]")].filter((e) => /^\s*Edit\s*$/.test(e.innerText) || /edit (my|your|this|last)/i.test(e.getAttribute("aria-label")||""));
  return JSON.stringify(els.map((e) => ({ t: e.innerText.trim(), al: e.getAttribute("aria-label"), ctx: (e.closest("li,div[class]")?.parentElement?.innerText||"").slice(0,80).replace(/\n/g," ") })));
}));
await b.close();
