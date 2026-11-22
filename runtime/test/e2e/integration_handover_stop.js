"use strict";

// A hand-over the person never finishes (WF-018): with parking off (Studio's test setting) the run ends at
// that step in plain words, is not parked for an agent to "fix", tells Studio it could not be judged
// (conxa/couldnt_run), and the browser is closed. Drives the real MCP path (server.js) on a data: page.
//
// Run: node test/e2e/integration_handover_stop.js

const os = require("os");
const path = require("path");
const fs = require("fs");
const assert = require("node:assert");
const { spawn } = require("child_process");

const RUNTIME_DIR = path.join(__dirname, "..", "..");

let failures = 0;
function check(cond, label) {
  if (cond) console.log(`ok - ${label}`);
  else { failures++; console.log(`not ok - ${label}`); }
}

function writeSkill(skillPacksDir, workspaceId, slug) {
  const dir = path.join(skillPacksDir, workspaceId, "_default", slug, "current");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({
    slug, name: slug, version: "0.0.1", required_runtime: ">=0.0.0",
    company: workspaceId, target_url: "", inputs_required: [], checksum: {},
  }));
  fs.writeFileSync(path.join(dir, "execution.json"), JSON.stringify([
    { type: "navigate", url: "data:text/html,<title>x</title><p>hello" },
    { type: "handover", message: "Please sign in the browser, then click Done." },
    { type: "navigate", url: "data:text/html,<p>never</p>" },
  ]));
  fs.writeFileSync(path.join(dir, "inputs.json"), JSON.stringify({ inputs: [] }));
  fs.writeFileSync(path.join(dir, "recovery.json"), JSON.stringify({ steps: [] }));
}

async function main() {
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-hostop-e2e-"));
  const CONXA_DIR = path.join(tmpRoot, "install");
  const CONXA_DATA_DIR = path.join(tmpRoot, "data");
  const workspaceId = "hostop-ws";
  const skillPacksDir = path.join(CONXA_DIR, "skill-packs");

  writeSkill(skillPacksDir, workspaceId, "handover-skill");

  fs.writeFileSync(path.join(skillPacksDir, workspaceId, "pack.json"), JSON.stringify({
    workspace_id: workspaceId, skill_pack_version: "0.0.1", required_runtime: ">=0.0.0",
    target_url: "", protected_url: "",
    skills: ["handover-skill"], tracking: { enabled: false },
    groups: [{ id: "g1", name: "G", apps: [] }],
  }));
  fs.mkdirSync(path.join(CONXA_DATA_DIR, "cache", "sessions"), { recursive: true });

  const env = Object.assign({}, process.env, {
    CONXA_DIR, CONXA_DATA_DIR, CONXA_SKIP_SELF_UPDATE: "1",
    CONXA_HANDOVER_INCALL_MS: "400", CONXA_HANDOVER_NO_PARK: "1",
  });
  const child = spawn(process.execPath, ["app/server.js"], { cwd: RUNTIME_DIR, env, stdio: ["pipe", "pipe", "pipe"] });
  let stderrBuf = "";
  child.stderr.on("data", (d) => { stderrBuf += d.toString(); });

  let id = 1;
  const pending = new Map();
  let buf = "";
  child.stdout.on("data", (chunk) => {
    buf += chunk.toString();
    let idx;
    while ((idx = buf.indexOf("\n")) !== -1) {
      const line = buf.slice(0, idx); buf = buf.slice(idx + 1);
      if (!line.trim()) continue;
      let msg;
      try { msg = JSON.parse(line); } catch (_) { continue; }
      if (msg.id != null && pending.has(msg.id)) {
        const { resolve } = pending.get(msg.id);
        pending.delete(msg.id);
        resolve(msg);
      }
    }
  });
  function send(method, params = {}) {
    const reqId = id++;
    const msg = JSON.stringify({ jsonrpc: "2.0", id: reqId, method, params });
    child.stdin.write(msg + "\n");
    return new Promise((resolve) => { pending.set(reqId, { resolve }); });
  }

  try {
    await send("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "test", version: "1" } });

    const resp = await send("tools/call", { name: "execute_skill", arguments: { skill: "handover-skill", workspace_id: workspaceId, watch: false } });
    const text = (resp?.result?.content || []).map((c) => c.text || "").join(" ") || JSON.stringify(resp);
    check(/Nobody pressed Done within/.test(text), `the run says plainly that nobody pressed Done (got: ${text.slice(0, 300)})`);
    check(/Nothing after it ran/.test(text), "it says nothing after the step ran");
    check(!/candidate_index|Interactive elements NOW|step_overrides|unexpected/i.test(text), "no recovery protocol and no vague wording");
    check(!!(resp?.result?._meta || {})["conxa/couldnt_run"], "Studio is told the run could not be judged");
    check(!(resp?.result?._meta || {})["conxa/handover"], "nothing is parked as a hand-over");
    const status = await send("tools/call", { name: "get_runtime_status", arguments: {} });
    check(!!status?.result, "the server is still alive after the stop");
  } finally {
    child.kill();
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }

  if (failures) console.log("--- server stderr tail ---\n" + stderrBuf.split("\n").slice(-40).join("\n"));
  console.log(`# fail ${failures}`);
  process.exit(failures ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
