import { Mcp } from "./wf16_mcp.mjs";
const m = new Mcp(); const i = await m.init(); console.log(JSON.stringify(i.result.serverInfo));
const t = await m.rpc("tools/list", {}); console.log(t.result.tools.map((x) => x.name).join(","));
console.log(m.text(await m.call("list_skills")).slice(0, 3000));
console.log(m.text(await m.call("get_skill_inputs", { skill: "wf4-d-i18n-wiki-r5-06fd6288" })).slice(0, 1500));
await m.close(); console.log("STDERR", m.err.join("").slice(0, 1500));
