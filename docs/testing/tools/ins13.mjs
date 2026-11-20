import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const kind = process.argv[2] || "Agent span";
const snap=()=>s.evaluate(()=>document.body.innerText.split(String.fromCharCode(10)));
const A=new Set(await snap());
await s.getByText("Add step", { exact: true }).first().click();
await new Promise(z=>setTimeout(z,600));
await s.getByText(kind, { exact: true }).first().click();
const seen=new Set();
for(let i=0;i<10;i++){await new Promise(z=>setTimeout(z,300));for(const l of await snap())if(!A.has(l))seen.add(l);}
console.log([...seen].slice(0,15));
await b.close();
