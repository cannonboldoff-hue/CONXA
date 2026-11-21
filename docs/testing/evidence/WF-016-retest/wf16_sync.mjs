import { Mcp, sleep } from "./wf16_mcp.mjs";
import fs from "node:fs";
const PK = process.env.WF16_ROOT + "/conxa/skill-packs/wrk_local/pack.json";
const base = JSON.parse(fs.readFileSync(PK, "utf8"));
const CASES = [["stub 401", "http://127.0.0.1:8096/s401"], ["stub 403", "http://127.0.0.1:8096/s403"], ["stub 304", "http://127.0.0.1:8096/s304"], ["stub 200 without skills list", "http://127.0.0.1:8096/x"], ["stub 401 then (new process) healthy 200 no list", "http://127.0.0.1:8096/x"]];
for (const [label, ep] of CASES) {
  const p = JSON.parse(JSON.stringify(base)); p.sync_endpoint = ep; p.sync_token = "bogus-sync-token"; delete p.last_synced;
  fs.writeFileSync(PK, JSON.stringify(p, null, 2));
  const m = new Mcp(); await m.init(); await sleep(6000);
  const o = JSON.parse(m.text(await m.call("list_skills")));
  const after = JSON.parse(fs.readFileSync(PK, "utf8"));
  console.log("SYNC", label, "-> list_skills total=" + o.total, "notice=" + (o.notice ? "YES: " + o.notice.slice(0, 60) : "none"), "pack.skills=" + (after.skills || []).length);
  await m.close();
}
