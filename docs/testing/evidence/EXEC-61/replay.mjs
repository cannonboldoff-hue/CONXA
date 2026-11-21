// EXEC-61 replay: telemetry sink + stdio MCP client against the source runtime (runtime/app/server.js).
// Usage: node replay.mjs <label> [KEY=VAL ...env overrides]
import { spawn } from "node:child_process";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const label = process.argv[2] || "run";
const extraEnv = Object.fromEntries(process.argv.slice(3).map((kv) => kv.split("=")));
const out = path.join(HERE, "out", label);
fs.mkdirSync(out, { recursive: true });
const telFile = path.join(out, "telemetry.jsonl");
const t0 = Date.now();

const sink = http.createServer((req, res) => {
  let b = ""; req.on("data", (d) => (b += d));
  req.on("end", () => {
    try { const env = JSON.parse(b); for (const e of env.evts || []) fs.appendFileSync(telFile, JSON.stringify(e) + "\n"); } catch {}
    res.writeHead(200); res.end("{}");
  });
}).listen(18761, "127.0.0.1");

const dataDir = path.join(out, "data");
fs.rmSync(dataDir, { recursive: true, force: true });
const p = spawn(process.execPath, ["C:/Users/kiran/Desktop/Conxa/runtime/app/server.js"], {
  env: { ...process.env, CONXA_ENV: "dev", CONXA_DIR: path.join(HERE, "conxa"), CONXA_DATA_DIR: dataDir,
    CONXA_SKIP_SELF_UPDATE: "1", CONXA_EXECUTION_DEADLINE_MS: "1800000", CONXA_MAX_RECOVERY_TIER: "2",
    PLAYWRIGHT_BROWSERS_PATH: "C:/Users/kiran/.conxa-build-studio-dev/sandbox/.conxa/chromium", ...extraEnv },
  stdio: ["pipe", "pipe", "pipe"] });
const errLog = fs.createWriteStream(path.join(out, "stderr.log"));
p.stderr.pipe(errLog);
let buf = ""; const pend = new Map(); let id = 0;
p.stdout.on("data", (d) => { buf += d; let i; while ((i = buf.indexOf("\n")) >= 0) { const l = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (!l) continue; let m; try { m = JSON.parse(l); } catch { continue; } if (m.id != null && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } } });
const rpc = (method, params) => new Promise((res) => { const k = ++id; pend.set(k, res); p.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: k, method, params }) + "\n"); });

await rpc("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "exec61-replay", version: "1" } });
p.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
const start = Date.now();
const r = await rpc("tools/call", { name: "execute_skill", arguments: { skill: "wf3-github-tour-40-3770ced3", workspace_id: "wrk_local", inputs: {}, watch: false, wait_for_auth: false } });
const wall = Date.now() - start;
fs.writeFileSync(path.join(out, "result.json"), JSON.stringify({ wall_ms: wall, env: extraEnv, result: r }, null, 1));
console.log("wall_ms", wall);
console.log(((r.result && r.result.content) || []).map((c) => c.text || "").join("\n").slice(0, 1500));
await new Promise((res) => setTimeout(res, 4000)); // last telemetry flush
p.stdin.end(); setTimeout(() => { try { p.kill(); } catch {} sink.close(); process.exit(0); }, 8000);
