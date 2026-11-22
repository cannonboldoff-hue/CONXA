// WF-18 MCP daemon: spawns the real runtime over stdio and runs commands appended (JSON lines) to <dir>/cmds.jsonl.
// usage: WF16_ROOT=<install root> node wf18_daemon.mjs <workdir>
// cmd line: {"id":"a","tool":"execute_skill","args":{...}}  |  {"id":"q","quit":true}
// result lines go to <workdir>/out.jsonl: {id,t0,t1,secs,text,structured,meta}; runtime stderr to <workdir>/stderr.log
import fs from "node:fs";
import { Mcp, sleep } from "./wf16_mcp.mjs";
const dir = process.argv[2]; fs.mkdirSync(dir, { recursive: true });
const cf = `${dir}/cmds.jsonl`; if (!fs.existsSync(cf)) fs.writeFileSync(cf, "");
const out = (o) => fs.appendFileSync(`${dir}/out.jsonl`, JSON.stringify(o) + "\n");
const m = new Mcp(JSON.parse(process.env.WF18_ENV || "{}"));
m.p.stderr.on("data", (d) => fs.appendFileSync(`${dir}/stderr.log`, d));
m.p.on("exit", (c) => out({ id: "_exit", code: c, t: new Date().toISOString() }));
await m.init(); out({ id: "_ready", pid: m.p.pid, t: new Date().toISOString() });
let off = 0, quit = false;
while (!quit) {
  const buf = fs.readFileSync(cf, "utf8"); const lines = buf.slice(off).split("\n"); off += buf.slice(off).lastIndexOf("\n") + 1;
  for (const l of lines.slice(0, -1).filter(Boolean)) {
    const c = JSON.parse(l);
    if (c.quit) { quit = true; break; }
    const t0 = Date.now();
    m.call(c.tool, c.args || {}, c.timeout || 900000).then((r) => out({ id: c.id, t0: new Date(t0).toISOString(), t1: new Date().toISOString(), secs: Math.round((Date.now() - t0) / 100) / 10,
      text: (r.content || []).filter((x) => x.type === "text").map((x) => x.text).join("\n"), images: (r.content || []).filter((x) => x.type === "image").length, structured: r.structuredContent, meta: r._meta, isError: r.isError }))
      .catch((e) => out({ id: c.id, error: String(e) }));
  }
  await sleep(500);
}
await m.close(); out({ id: "_closed" });
