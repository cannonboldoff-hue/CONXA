"use strict";
// EXEC-62 measurement harness. Serves docs/testing/fixtures/recovery-fixture.html (no-cache) on
// 127.0.0.1:8099, stages the recompiled "Buy Now" pack in a throwaway CONXA_DIR, starts the runtime
// MCP server from source (runtime/app/server.js, as run_loop_e2e.js does), calls execute_skill once
// per (mode, ceiling), and saves the response text, recovery.log, runtime stderr and the page's
// own click log for each run.
// Usage: node drift_run.js <repo> <pack_dir> <evidence_dir>
const fs = require("fs"), os = require("os"), path = require("path"), http = require("http");
const { spawn } = require("child_process");

const [repo, packDir, evDir] = process.argv.slice(2).map(p => path.resolve(p));
const FIXTURE = path.join(repo, "docs", "testing", "fixtures", "recovery-fixture.html");
const RUNTIME = path.join(repo, "runtime");
const SLUG = "recovery-fixture-exec62", WS = "exec62";
// mode:ceiling[:nd]  — "nd" replays the step as NOT irreversible (as if the person answered "it can be undone").
const RUNS = (process.env.RUNS || "clean:2,t2:2,t2:4,d1:2,d3:2").split(",").map(r => { const [mode, tier, nd] = r.split(":"); return { mode, tier, nd: nd === "nd" }; });
// CONFIRMED=1 serves a scratch copy whose click also POSTs an order and shows a confirmation line, so the
// click's proof can pass and the identity result is not hidden behind a proof failure. Repo file untouched.
const CONFIRMED = process.env.CONFIRMED === "1";
const PATCH_FROM = 'b.addEventListener("click", () => { log(name); b.textContent += " ✓"; });';
const PATCH_TO = 'b.addEventListener("click", () => { log(name); b.textContent += " ✓"; fetch("/__order", {method: "POST"}); '
  + 'const m = document.createElement("p"); m.textContent = "Thank you, your order has been placed."; document.body.appendChild(m); });';
fs.mkdirSync(evDir, { recursive: true });

const pageHits = [];
const server = http.createServer((req, res) => {
  const u = new URL(req.url, "http://x");
  if (u.pathname === "/__log") { pageHits.push(decodeURIComponent(u.search.slice(1))); res.writeHead(204); return res.end(); }
  if (u.pathname === "/__order") { pageHits.push("POST /__order"); res.writeHead(200, { "Content-Type": "application/json" }); return res.end('{"ok":true}'); }
  if (u.pathname === "/recovery-fixture.html") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
    let html = fs.readFileSync(FIXTURE, "utf8");
    if (CONFIRMED) { if (!html.includes(PATCH_FROM)) throw new Error("fixture changed: patch anchor not found"); html = html.replace(PATCH_FROM, PATCH_TO); }
    return res.end(html);
  }
  res.writeHead(404); res.end();
});

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, e.name), d = path.join(dst, e.name);
    if (e.isDirectory()) copyDir(s, d); else fs.copyFileSync(s, d);
  }
}

function stage(mode, nd) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-exec62-"));
  const conxaDir = path.join(tmp, ".conxa"), dataDir = path.join(tmp, "data");
  fs.mkdirSync(path.join(dataDir, "cache", "sessions"), { recursive: true });
  fs.mkdirSync(path.join(dataDir, "logs"), { recursive: true });
  const ws = path.join(conxaDir, "skill-packs", WS);
  fs.mkdirSync(ws, { recursive: true });
  fs.writeFileSync(path.join(ws, "pack.json"), JSON.stringify({ workspace_id: WS, display_name: "EXEC-62", skill_pack_version: "0.0.1",
    required_runtime: ">=1.0.0", target_url: "", protected_url: "", skills: [SLUG],
    groups: [{ id: "g", name: "fixture", apps: [] }], tracking: { enabled: false } }, null, 2));
  const ver = path.join(ws, "_default", SLUG, "v0.0.1");
  copyDir(packDir, ver);
  // The recorded start page, with the drift variant selected by query (the fixture reads ?v=).
  const exec = JSON.parse(fs.readFileSync(path.join(ver, "execution.json"), "utf8"));
  for (const s of exec) if (s.type === "navigate") s.url = `http://127.0.0.1:8099/recovery-fixture.html${mode === "clean" ? "" : "?v=" + mode}`;
  if (nd) for (const s of exec) if (s.destructive === true) { s.destructive = false; if (s.identity_bundle) s.identity_bundle.destructive = false; }
  fs.writeFileSync(path.join(ver, "execution.json"), JSON.stringify(exec, null, 2));
  fs.renameSync(path.join(ver, "input.json"), path.join(ver, "inputs.json"));
  const sf = JSON.parse(fs.readFileSync(path.join(ver, "structural_fingerprint.json"), "utf8"));
  fs.writeFileSync(path.join(ver, "manifest.json"), JSON.stringify({ slug: SLUG, name: "Recovery fixture (EXEC-62)",
    description: "Recovery fixture", version: "0.0.1", required_runtime: ">=1.0.0", company: WS,
    target_url: "http://127.0.0.1:8099/recovery-fixture.html", inputs_required: [], structural_fingerprint: sf, checksum: {} }, null, 2));
  fs.symlinkSync(ver, path.join(ws, "_default", SLUG, "current"), "junction");
  return { tmp, conxaDir, dataDir };
}

function runOnce({ mode, tier, nd }) {
  const { tmp, conxaDir, dataDir } = stage(mode, nd);
  const ms = path.join(os.homedir(), "AppData", "Local", "ms-playwright");
  const child = spawn(process.execPath, [path.join(RUNTIME, "app", "server.js")], {
    cwd: RUNTIME, stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, CONXA_DIR: conxaDir, CONXA_DATA_DIR: dataDir, CONXA_SKIP_SELF_UPDATE: "1",
      CONXA_MAX_RECOVERY_TIER: tier, PLAYWRIGHT_BROWSERS_PATH: fs.existsSync(ms) ? ms : (process.env.PLAYWRIGHT_BROWSERS_PATH || "") },
  });
  let stderr = "", buf = "", nextId = 1;
  const pending = new Map();
  child.stderr.on("data", d => { stderr += d.toString(); });
  child.stdout.on("data", d => {
    buf += d.toString(); let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
      let msg; try { msg = JSON.parse(line); } catch (_) { continue; }
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    }
  });
  const send = (method, params) => new Promise((res, rej) => {
    const id = nextId++; pending.set(id, res);
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); rej(new Error("timeout " + method)); } }, 240000);
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  });
  return (async () => {
    const before = pageHits.length, t0 = Date.now();
    let text = "", images = 0, err = "";
    try {
      await send("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "exec62", version: "1" } });
      const r = await send("tools/call", { name: "execute_skill", arguments: { skill: SLUG, workspace_id: WS, inputs: {}, watch: false } });
      const content = (r.result && r.result.content) || [];
      text = content.filter(c => c.type === "text").map(c => c.text).join("\n");
      images = content.filter(c => c.type === "image").length;
      if (r.error) err = JSON.stringify(r.error);
    } catch (e) { err = e.message; }
    const ms_ = Date.now() - t0;
    await new Promise(r => setTimeout(r, 500)); // let the page's last /__log beacon land
    const tag = `${CONFIRMED ? "confirmed" : "asis"}-${mode}-tier${tier}${nd ? "-undoable" : ""}`;
    const recLog = path.join(conxaDir, "logs", "recovery.log");
    const rec = fs.existsSync(recLog) ? fs.readFileSync(recLog, "utf8") : "(no recovery.log)";
    const clicks = pageHits.slice(before);
    fs.writeFileSync(path.join(evDir, `${tag}.response.txt`), `${text}\n\n[images: ${images}] [error: ${err}] [ms: ${ms_}]\n`);
    fs.writeFileSync(path.join(evDir, `${tag}.recovery.log`), rec);
    fs.writeFileSync(path.join(evDir, `${tag}.stderr.log`), stderr);
    fs.writeFileSync(path.join(evDir, `${tag}.page-clicks.txt`), clicks.join("\n") + "\n");
    try { child.stdin.end(); } catch (_) {}
    await new Promise(r => setTimeout(r, 1500));
    try { child.kill(); } catch (_) {}
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {}
    console.log(`== ${tag}: ${ms_} ms, page clicks: [${clicks.join(", ")}], images: ${images}${err ? ", error: " + err : ""}`);
    console.log("   " + text.split("\n").slice(0, 3).join(" | ").slice(0, 300));
  })();
}

server.listen(8099, "127.0.0.1", async () => {
  for (const r of RUNS) await runOnce(r);
  process.exit(0); // keep-alive sockets from Chromium would hold server.close() open
});
