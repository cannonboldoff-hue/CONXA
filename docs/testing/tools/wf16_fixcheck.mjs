// WF-016 fix check (16.1 + 16.11). usage: WF16_ROOT=... node wf16_fixcheck.mjs <1|11>
// 1: run a skill, then get_execution_status(run_id) must say completed/failed, and `recent` must list it.
// 11: caller first points pack.json tracking at a workspace with a stored token and a bogus token;
//     the run answer and list_skills must carry the plain "key was not accepted" sentence.
import { Mcp } from "./wf16_mcp.mjs";
const W = "wf4-d-i18n-wiki-r5-06fd6288";
const m = new Mcp(); await m.init();
const say = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const run = async () => {
  const t = m.text(await m.call("execute_skill", { skill: W, inputs: { search_wikipedia: "Automation" }, watch: false }));
  say("RUN", t.slice(0, 900));
  return (/run_id: (r_\w+)/.exec(t) || [])[1];
};
if (process.argv[2] === "11") {
  const id = await run();
  say("STATUS", m.text(await m.call("get_execution_status", { run_id: id })).slice(0, 300));
  say("LIST", m.text(await m.call("list_skills")).replace(/"skills":\[.*\],"total"/, '"skills":[...],"total"'));
} else {
  const id = await run();
  say("STATUS one", m.text(await m.call("get_execution_status", { run_id: id })).slice(0, 600));
  say("STATUS all", m.text(await m.call("get_execution_status")).slice(0, 600));
}
await m.close();
