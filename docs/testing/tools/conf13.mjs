import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const all = () => b.contexts().flatMap((c) => c.pages());
const s = all().find((p) => /localhost:517\d/.test(p.url()));
const txt = () => s.evaluate(() => document.body.innerText);
const sleep = (ms) => new Promise((z) => setTimeout(z, ms));
const out = async () => { const p = all().find((x) => x.url().includes("wf13-delete")); return p ? await p.evaluate(() => document.querySelector("#out").innerText) : "(no page)"; };
const decision = process.argv[2] || "Yes, go ahead";
{ const back = s.getByText("Back to conversation"); if (await back.count()) { await back.first().click(); await sleep(2000); } }
await s.getByRole("button", { name: /^Test (it|again)$/ }).last().click(); await sleep(2500);
await s.getByRole("button", { name: /^Run test/ }).click();
const t0 = Date.now();
for (;;) {
  await sleep(2000); const t = await txt(); const tail = t.slice(t.lastIndexOf("Test inputs"));
  if (/Paused before “Click Save/.test(tail) && await s.getByRole("button", { name: /Run the last/ }).count()) { await s.getByRole("button", { name: /Run the last/ }).click(); console.log("saved-click allowed"); await sleep(3000); continue; }
  if (/A person needs to confirm/.test(tail)) {
    await s.screenshot({ path: path.join(here, "../evidence/WF-013/retest/13.5-card.png") });
    console.log("CONFIRM CARD:", tail.match(/The agent wants[^\n]*/)[0]);
    for (let i = 0; i < 4; i++) { console.log("page #out while waiting:", await out()); await sleep(2500); }
    console.log("fixture deleted-hits before decision:", process.env.HITS);
    await s.getByRole("button", { name: new RegExp(decision) }).click(); await sleep(8000);
    console.log("page #out after decision:", await out());
    break;
  }
  if (/Test passed|Test failed at|Not a full pass/.test(tail)) { console.log("ended without card:", tail.slice(0, 300).replace(/\n/g, " | ")); break; }
  if (Date.now() - t0 > 150000) { console.log("timeout"); break; }
}
await sleep(6000);
const t = await txt(); console.log(t.slice(t.lastIndexOf("Test inputs"), t.lastIndexOf("Test inputs") + 700).replace(/\n/g, " | "));
await s.screenshot({ path: path.join(here, "../evidence/WF-013/retest/13.5-after-" + decision.split(",")[0].replace(/\W/g, "") + ".png") });
await b.close();
