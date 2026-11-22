// WF-017 runtime driver. WF17_ROOT=<install root> node wf17_cases.mjs <case> ; talks to the Conxa runtime exe over stdio MCP (wf16_mcp.mjs).
import { Mcp, sleep } from "./wf16_mcp.mjs";
import fs from "node:fs";
const ROOT = process.env.WF16_ROOT = process.env.WF17_ROOT;
const t0 = Date.now(), T = () => ((Date.now() - t0) / 1000).toFixed(1) + "s", NL = "\n";
const say = (...a) => console.log(T(), ...a);
const m = new Mcp({ CONXA_AUTH_VALIDATION_TTL_MS: "0" }); await m.init();
const site = async (p) => (await (await fetch("http://127.0.0.1:8101" + p)).text()).replace(/<[^>]+>/g, " ").trim();
const ex = async (slug, extra = {}, label = "") => {
  const s = Date.now(); const r = await m.call("execute_skill", { skill: slug, watch: false, wait_for_auth: false, ...extra }, 180000); const t = m.text(r);
  say("RESULT", slug, label, ((Date.now() - s) / 1000).toFixed(1) + "s", "isError=" + !!r.isError, "meta=" + JSON.stringify(r._meta || r.structuredContent || {}).slice(0, 300), NL + "   " + t.slice(0, 900).split(NL).join(NL + "   "));
  return { r, t };
};
const C = process.argv[2];
try {
  if (C === "list") { say(m.text(await m.call("list_skills", {})).slice(0, 1500)); }
  if (C === "smoke") { await ex("wf17-two-apps", {}, "both signed in"); }
  if (C === "a-only") { await ex("wf17-a-only"); }
  if (C === "b-only") { await ex("wf17-b-only"); }
  if (C === "expire-b") { say("expire", await site("/expire?host=localhost")); await ex("wf17-two-apps", {}, "App B expired"); await ex("wf17-a-only", {}, "A only after B expired"); await ex("wf17-b-only", {}, "B only after B expired"); }
  if (C === "expire-a") { say("expire", await site("/expire?host=127.0.0.1")); await ex("wf17-two-apps", {}, "App A expired"); await ex("wf17-b-only", {}, "B only after A expired"); }
  if (C === "mid") { // expire while the slow page is loading
    const p = ex("wf17-slow", { wait_for_auth: true }, "expire mid-run"); await sleep(14000); say("expire", await site("/expire?host=127.0.0.1")); await p; }
  if (C === "visit-login") { await ex("wf17-visit-login"); }
  if (C === "signout") { await ex("wf17-signout"); }
  if (C === "status") { say(m.text(await m.call("get_execution_status", {})).slice(0, 800)); }
  const errs = m.err.join("").split(NL).filter((l) => /auth|session|sign|expire|login/i.test(l)).slice(-25); say("STDERR (auth-related, last 25)", NL + errs.join(NL));
} catch (e) { say("ERR", e.message); }
await m.close(); process.exit(0);
