// WF-17 helper. node wf17_ui.mjs <cdp> fill <label|placeholder regex> <text> | click <role> <name regex> | text | shot <file>
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, op, a, b2] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const re = (x) => new RegExp(x, "i");
if (op === "fill") { const l = s.getByLabel(re(a)).or(s.getByPlaceholder(re(a))).first(); await l.fill(b2, { timeout: 8000 }); console.log("filled"); }
if (op === "click") { await s.getByRole(a, { name: re(b2) }).first().click({ timeout: 8000 }); console.log("clicked"); }
if (op === "text") console.log((await s.innerText("body")).replace(/\n{2,}/g, "\n").slice(0, Number(a || 3000)));
if (op === "shot") { await s.screenshot({ path: a }); console.log("shot"); }
process.exit(0);
