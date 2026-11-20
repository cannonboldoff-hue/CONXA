// node fill.mjs <cdp> '<json {name:value}>'  sets visible form fields by name (checkbox name=value pairs: {"agent_span_allowed:click":false})
import { createRequire } from "node:module"; import path from "node:path"; import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");
const b = await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
const s = b.contexts().flatMap((c) => c.pages()).find((p) => /localhost:517\d/.test(p.url()));
const o = JSON.parse(process.argv[3]);
for (const [k, v] of Object.entries(o)) {
  const [n, cv] = k.split(":");
  const loc = cv ? s.locator(`input[name="${n}"][value="${cv}"]`) : s.locator(`[name="${n}"]`).first();
  if (typeof v === "boolean") { if ((await loc.isChecked()) !== v) await loc.click(); }
  else if ((await loc.evaluate((e) => e.tagName)) === "SELECT") await loc.selectOption(v);
  else await loc.fill(String(v));
}
console.log("ok");
await b.close();
