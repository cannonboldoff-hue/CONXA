// node run13.mjs <inputValue|-> [noai]  : Test it -> fill input -> Run test -> allow irreversible -> wait for result. Prints markers.
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
import { execSync } from "node:child_process";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [val, mode = "ai"] = process.argv.slice(2);
const usage = () => execSync("bash " + path.join(here, "usage13.sh")).toString().trim();
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const txt = () => s.evaluate(() => document.body.innerText);
console.log("before", usage());
const back = s.getByText("Back to conversation"); if (await back.count()) { await back.first().click(); await new Promise((z) => setTimeout(z, 2000)); }
if (!(await s.getByRole("button", { name: /^Run test/ }).count())) await s.getByRole("button", { name: /^Test (it|again)$/ }).last().click({ timeout: 10000 });
await new Promise((z) => setTimeout(z, 2500));
if (val !== "-") {
  const inps = s.locator("input[type=text],input:not([type])").filter({ visible: true });
  console.log("inputs", await inps.count(), await inps.evaluateAll((e) => e.map((x) => x.value)));
  if (await inps.count()) await inps.nth(0).fill(val);
}
if (mode === "noai") { const c = s.getByLabel(/Keep the AI helper off/); if (await c.count() && !(await c.isChecked())) await c.check(); }
await s.getByRole("button", { name: /^Run test/ }).click();
console.log("started", new Date().toISOString());
const base = ((await txt()).match(/Test passed|Test failed at|passed up to here|couldn.t finish/g) || []).length;
const t0 = Date.now(); let last = "";
while (Date.now() - t0 < 200000) {
  await new Promise((z) => setTimeout(z, 2000));
  const t = await txt();
  const m = t.match(/Paused before “[^”]*”|Test passed[^\n]*|Test failed at[^\n]*|passed up to here[^\n]*|Test failed[^\n]*|couldn.t finish[^\n]*|Stopped[^\n]*/g);
  const fin = ((t.match(/Test passed|Test failed at|passed up to here|couldn.t finish/g) || []).length) > base || !/Test running/.test(t) && /Paused before/.test(t) === false && Date.now()-t0>6000 && ((t.match(/Test passed|Test failed at|passed up to here|couldn.t finish/g) || []).length) > 0 && !/Test running/.test(t);
  const key = m ? m[m.length - 1] : "";
  if (!/^Paused before/.test(key) && !fin) { continue; }
  if (key !== last) { console.log(Math.round((Date.now() - t0) / 1000) + "s", key); last = key; }
  if (/^Paused before/.test(key) && (await s.getByRole("button", { name: /Run the last/ }).count())) { console.log("usage@pause", usage()); await s.getByRole("button", { name: /Run the last/ }).click(); await new Promise((z) => setTimeout(z, 3000)); last = "resumed"; continue; }
  if (/^(Test passed|Test failed|passed up to here|couldn)/.test(key)) break;
}
console.log("after", usage());
const t = await txt(); console.log(t.slice(t.lastIndexOf("Test inputs")).slice(0, 1800));
await s.screenshot({ path: path.join(here, "../evidence/WF-013/retest2/run-" + (process.env.TAG || "x") + ".png") });
await b.close();
