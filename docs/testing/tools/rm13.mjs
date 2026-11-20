// node rm13.mjs <n> [n2...] : remove Human Edit steps by number (do highest first)
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const sleep = (ms) => new Promise((z) => setTimeout(z, ms));
for (const n of process.argv.slice(2)) {
  const r = await s.evaluate((n) => { const e = [...document.querySelectorAll("*")].find((e) => e.children.length === 0 && e.textContent.trim() === n && e.getBoundingClientRect().x > 250 && e.getBoundingClientRect().x < 330); e.scrollIntoView({ block: "center" }); const q = e.getBoundingClientRect(); return [q.x, q.y]; }, n);
  await sleep(400);
  const y = await s.evaluate((n) => { const e = [...document.querySelectorAll("*")].find((e) => e.children.length === 0 && e.textContent.trim() === n && e.getBoundingClientRect().x > 250 && e.getBoundingClientRect().x < 330); return e.getBoundingClientRect().y + 6; }, n);
  await s.mouse.move(380, y); await s.mouse.click(420, y); await sleep(600);
  await s.mouse.move(562, y); await s.mouse.click(562, y); await sleep(800);
  const dlg = await s.getByText(new RegExp("^Remove step " + n + "\?")).count();
  console.log("step", n, "dialog", dlg);
  if (dlg) { await s.getByRole("button", { name: /^Remove$/ }).click(); await sleep(700); console.log("TOAST", (await s.evaluate(() => document.body.innerText)).split(String.fromCharCode(10)).filter((l) => /come straight|Something went wrong/.test(l))); if (process.env.SHOT) await s.screenshot({ path: process.env.SHOT }); await sleep(800); }
}
await b.close();
