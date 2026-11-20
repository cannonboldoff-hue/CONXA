// node span13.mjs "<goal>" <preset> : fill the open span form (goal + answer preset), Save step, print result messages
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:62498`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const [goal, preset] = process.argv.slice(2);
const sleep = (ms) => new Promise((z) => setTimeout(z, ms));
const g = s.getByText("What the agent should get done").locator("xpath=following::input[1] | following::textarea[1]").first();
await g.fill(goal);
if (preset) await s.locator("select").filter({ has: s.locator("option[value=confirm]") }).selectOption(preset);
await sleep(600);
await s.screenshot({ path: path.join(here, "../evidence/WF-013/retest2/span-filled.png") });
await s.getByRole("button", { name: /^Save step/ }).click(); await sleep(2500);
console.log((await s.evaluate(() => document.body.innerText)).match(/Step saved|Nothing changed|Save failed|Say what[^\n]*|[^\n]*can.t come straight[^\n]*/g));
await s.screenshot({ path: path.join(here, "../evidence/WF-013/retest2/span-saved.png") });
await b.close();
