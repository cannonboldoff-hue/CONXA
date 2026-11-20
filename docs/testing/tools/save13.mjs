import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
await s.getByRole("button", { name: /^Save step/ }).click();
for (let i=0;i<8;i++){ await new Promise(z=>setTimeout(z,300)); const t=await s.evaluate(()=>document.body.innerText); const m=t.match(/(Step saved|Save failed|Nothing changed|[^\n]*(refus|cannot|can't)[^\n]*)/gi); console.log(i, m&&m.slice(-3)); }
await s.screenshot({path:"../evidence/WF-013/s17.png"});
await b.close();
