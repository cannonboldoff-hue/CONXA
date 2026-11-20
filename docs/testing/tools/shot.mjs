// Screenshot a CDP page. Usage: node shot.mjs <cdpPort> <outfile.png> [studio|last|<urlSubstring>]
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const [port, out, which = "studio"] = process.argv.slice(2);
const b = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const pages = b.contexts().flatMap((c) => c.pages());
const studio = pages.find((p) => /localhost:517\d|^app:|^file:/.test(p.url()));
const p = which === "studio" ? studio : which === "last" ? pages.filter((x) => x !== studio).pop() : pages.find((x) => x.url().includes(which));
await p.screenshot({ path: out });
console.log(JSON.stringify({ ok: true, url: p.url(), out }));
await b.close();
