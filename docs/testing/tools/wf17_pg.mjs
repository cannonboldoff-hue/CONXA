// WF-17: drive a target-site page in Studio's sign-in pane. node wf17_pg.mjs <cdp> <urlSubstr> <op> [args]
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, sub, op, a, c] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const ps = b.contexts().flatMap((x) => x.pages()).filter((p) => !/localhost:517\d/.test(p.url()));
const p = ps.find((x) => x.url().includes(sub));
if (!p) { console.log("NO PAGE", ps.map((x) => x.url())); process.exit(3); }
if (op === "fill") await p.fill(a, c);
if (op === "click") await p.click(a);
if (op === "text") console.log((await p.innerText("body")).slice(0, 800));
if (op === "url") console.log(p.url());
if (op === "shot") await p.screenshot({ path: a });
if (op === "goto") await p.goto(a);
if (op === "cookies") console.log(JSON.stringify((await p.context().cookies()).map((k) => ({ n: k.name, d: k.domain }))));
if (op === "clear") { await p.context().clearCookies(); console.log("cleared"); }
if (op === "list") console.log(ps.map((x) => x.url()));
process.exit(0);
