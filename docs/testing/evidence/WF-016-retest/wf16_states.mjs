import { Mcp, sleep } from "./wf16_mcp.mjs";
const m = new Mcp(); await m.init();
const NL="\n"; const rid=(t)=>(/run_id: (r_\w+)/.exec(t)||[])[1];
const st = async (id)=>{const t=m.text(await m.call("get_execution_status", id?{run_id:id}:{})); console.log("STATUS",id||"(all)",t.slice(0,700)); return t;};
const ex = async(a,l)=>{const r=await m.call("execute_skill",{watch:false,...a}); const t=m.text(r); console.log("RESULT",l,"isError="+!!r.isError,NL+"  "+t.slice(0,500).split(NL).join(NL+"  ")); return t;};
// failed: destructive decision step fails closed
const f = await ex({skill:"wf10-decision-r3-94560d1d",inputs:{}},"FAILED case");
await st(rid(f));
// cancelled
const p = ex({skill:"mutator-fixture-72c45380",inputs:{name:"Ada",email:"a@b.c",shapeshifter:"x"}},"CANCEL case"); await sleep(2500);
const a = JSON.parse(await st()); const s=a.active_runs.find(x=>/mutator/.test(x.skill));
if(s){console.log("cancel",s.run_id,m.text(await m.call("cancel_execution",{run_id:s.run_id})));}
const ct = await p; await st(rid(ct)||(s&&s.run_id));
// paused: popup question
const pa = await ex({skill:"wf16-decision-safe",inputs:{}},"PAUSED popup case"); const pid = rid(pa);
await st(pid); await st();
// resolve pause: stop
await ex({skill:"wf16-decision-safe",resume_from:3,review_results:{"3":{choice:"__stop__"}}},"answer stop"); await st();
// paused 2: agent span
const sp = await ex({skill:"wf13-marie-curie-rt-283e911a",inputs:{full_name:"Marie Curie",search_wikipedia:"Marie Curie"}},"PAUSED span case"); await st(rid(sp));
await ex({skill:"wf13-marie-curie-rt-283e911a",inputs:{full_name:"Marie Curie",search_wikipedia:"Marie Curie"},resume_from:6,span_actions:{"6":{kind:"done",answer:{choice:"person"}}}},"span answer"); await st();
await sleep(2000); await m.close();
