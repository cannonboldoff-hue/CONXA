// Minimal stdio MCP client for the Conxa runtime exe (WF-016). Import { Mcp } and use m.call(name,args).
import { spawn } from "node:child_process";
export const ROOT = process.env.WF16_ROOT; // dir with conxa/ and data/
export class Mcp {
  constructor(env = {}) {
    this.id = 0; this.pend = new Map(); this.buf = ""; this.err = []; this.notes = [];
    this.p = spawn(`${ROOT}/conxa/conxa-runtime.exe`, [], {
      env: { ...process.env, CONXA_ENV: "dev", CONXA_DIR: `${ROOT}/conxa`, CONXA_DATA_DIR: `${ROOT}/data`,
        CONXA_APP_DIR: `${ROOT}/conxa/conxa-app`, CONXA_SKIP_SELF_UPDATE: "1", ...env },
      stdio: ["pipe", "pipe", "pipe"] });
    this.p.stdout.on("data", (d) => { this.buf += d; let i; while ((i = this.buf.indexOf("\n")) >= 0) { const l = this.buf.slice(0, i).trim(); this.buf = this.buf.slice(i + 1); if (l) this.onLine(l); } });
    this.p.stderr.on("data", (d) => this.err.push(String(d)));
    this.exited = new Promise((r) => this.p.on("exit", r));
  }
  onLine(l) { let m; try { m = JSON.parse(l); } catch { return; }
    if (m.id != null && this.pend.has(m.id)) { this.pend.get(m.id)(m); this.pend.delete(m.id); } else this.notes.push(m); }
  rpc(method, params, timeoutMs = 600000) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      const t = setTimeout(() => { this.pend.delete(id); rej(new Error("client timeout " + method)); }, timeoutMs);
      this.pend.set(id, (m) => { clearTimeout(t); res(m); });
      this.p.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }
  async init() {
    const r = await this.rpc("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "wf16-tester", version: "1" } });
    this.p.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n"); return r;
  }
  async call(name, args = {}, timeoutMs) { const m = await this.rpc("tools/call", { name, arguments: args }, timeoutMs); return m.result || m; }
  text(r) { return (r.content || []).map((c) => c.text || "").join("\n"); }
  async close() { try { this.p.stdin.end(); } catch {} await Promise.race([this.exited, new Promise((r) => setTimeout(r, 15000))]); if (this.p.exitCode == null) this.p.kill(); }
}
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
