// WF-18 Studio driver that pins the route to my workflow first (Studio is shared with other actors).
// usage: node wf18_ui.mjs <cdp> <op> [args]   ops: text [n] | click <text|/re/> | eval <js> | shot <png>
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const WF = process.env.WF18_ID || "001b9c76-6ff5-42e5-97b9-619ddaba9f22";
const [port, op, ...a] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
if (!s.url().includes(WF)) { await s.evaluate((id) => { location.hash = "#/w/" + id; }, WF); await new Promise((z) => setTimeout(z, 2500)); console.error("(re-pinned route)"); }
const tail = async (n) => (await s.innerText("body")).replace(/\n{2,}/g, "\n").slice(-n);
if (op === "text") console.log(await tail(+a[0] || 1500));
else if (op === "click") { const m = /^\/(.*)\/$/.exec(a[0]); const loc = s.getByText(m ? new RegExp(m[1]) : a[0], { exact: !m }); const bt = s.getByRole("button", { name: m ? new RegExp(m[1]) : a[0] }); const l = (await bt.count()) ? bt.last() : loc.last(); await l.click({ timeout: 8000 }); console.log("clicked", a[0]); }
else if (op === "eval") console.log(JSON.stringify(await s.evaluate(a.join(" "))));
else if (op === "shot") { await s.screenshot({ path: a[0] }); console.log("shot", a[0]); }
await b.close();
