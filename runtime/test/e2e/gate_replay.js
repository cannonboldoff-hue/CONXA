"use strict";
// CI execution gate: drive a packed host exe through a real, hermetic skill replay and
// assert it resolves + clicks an element on a local file:// page. Catches the class of bug
// where the packed runtime's Playwright selector engine is broken (e.g. pkg bytecode
// corruption): every locator returns 0 elements, so the click step fails — and so does the
// build. Unlike an MCP-initialize-only gate, this exercises the selector engine end-to-end.
//
// Usage: node gate_replay.js <path-to-host-exe> [app-layer-dir]
//   <path-to-host-exe>  packed conxa-runtime.exe to test.
//   [app-layer-dir]     conxa-app layer to load (e.g. dist-app/). Defaults to the current
//                       runtime/*.js source — so the host gate tests the host against HEAD.
//   Env: PLAYWRIGHT_BROWSERS_PATH (where chromium is installed) — required.
//
// Exit 0 = replay reached "Done."; non-zero = gate failed.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const { pathToFileURL } = require("url");
const fixtureSite = require("../fixture-site/server");

const exe = process.argv[2] ? path.resolve(process.argv[2]) : "";
if (!exe || !fs.existsSync(exe)) {
  console.error(`[gate] host exe not found: ${exe}`);
  process.exit(2);
}

const FIXTURE_DIR = path.join(__dirname, "..", "gate-skill");
const SKILL_PACK_SRC = path.join(FIXTURE_DIR, "skill-pack");
const FIXTURE_URL = pathToFileURL(path.join(FIXTURE_DIR, "fixture.html")).href;
const GATE_VERSION = "v0.0.0-gate";

// ── stage a throwaway CONXA_DIR / CONXA_DATA_DIR ──────────────────────────────
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-gate-"));
const conxaDir = path.join(tmp, ".conxa");
const dataDir = path.join(tmp, "data");
const sessionsDir = path.join(dataDir, "cache", "sessions");
fs.mkdirSync(sessionsDir, { recursive: true });
fs.mkdirSync(path.join(dataDir, "logs"), { recursive: true });

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, e.name), d = path.join(dst, e.name);
    if (e.isDirectory()) copyDir(s, d); else fs.copyFileSync(s, d);
  }
}

// Every updateable component is laid out as <componentDir>/<version>/ + a `current`
// directory junction pointing at it (see runtime/version_manager.js). Mirror that here
// so the gate exercises the exact resolution path bootstrap.js/skill_loader.js use in
// production, not a flat stand-in.
function activateVersion(componentDir, versionName, populate) {
  const versionDir = path.join(componentDir, versionName);
  fs.mkdirSync(versionDir, { recursive: true });
  populate(versionDir);
  if (!fs.existsSync(path.join(versionDir, "version.json"))) {
    fs.writeFileSync(path.join(versionDir, "version.json"), JSON.stringify({ version: versionName }));
  }
  const currentLink = path.join(componentDir, "current");
  try { fs.unlinkSync(currentLink); } catch (_) {}
  fs.symlinkSync(versionDir, currentLink, process.platform === "win32" ? "junction" : "dir");
}

// skill-packs/<workspace_id>/pack.json stays flat; each skill under it becomes its own
// versioned component: skill-packs/<workspace_id>/<group_id>/<slug>/<version>/ + current.
// The fixture's pack.json carries no skill_groups map, so skill_loader.js resolves
// every skill's group as the "_default" sentinel — mirror that here.
const skillPacksDir = path.join(conxaDir, "skill-packs");
for (const workspace_id of fs.readdirSync(SKILL_PACK_SRC)) {
  const workspaceSrc = path.join(SKILL_PACK_SRC, workspace_id);
  const workspaceDst = path.join(skillPacksDir, workspace_id);
  fs.mkdirSync(workspaceDst, { recursive: true });
  for (const e of fs.readdirSync(workspaceSrc, { withFileTypes: true })) {
    if (!e.isDirectory()) { fs.copyFileSync(path.join(workspaceSrc, e.name), path.join(workspaceDst, e.name)); continue; }
    const slug = e.name;
    activateVersion(path.join(workspaceDst, "_default", slug), GATE_VERSION, (versionDir) => copyDir(path.join(workspaceSrc, slug), versionDir));
  }
}

// Stage the app layer the host loads from disk (CONXA_DIR/conxa-app/current/server.js).
const appRoot = path.join(conxaDir, "conxa-app");
const appSrc = process.argv[3];
activateVersion(appRoot, GATE_VERSION, (versionDir) => {
  if (appSrc && fs.existsSync(appSrc)) {
    copyDir(appSrc, versionDir);
  } else {
    // Default: current runtime source as the app layer (host gate tests host vs HEAD).
    const RUNTIME_ROOT = path.join(__dirname, "..", "..");
    // File list comes from app-layer-files.json — the same single source of truth
    // build-runtime-app.yml and build-app-local.ps1 consume, so the gate always
    // stages exactly what ships. bootstrap/min_host_gate are exe-side extras the
    // gate stages alongside for a realistic flat layout.
    const appManifest = JSON.parse(
      fs.readFileSync(path.join(RUNTIME_ROOT, "app-layer-files.json"), "utf8")
    );
    const APP_FILES = [
      ...appManifest.files.map((f) => `app/${f.name}`),
      "host/bootstrap.js", "host/min_host_gate.js",
    ];
    for (const f of APP_FILES) fs.copyFileSync(path.join(RUNTIME_ROOT, f), path.join(versionDir, path.basename(f)));
  }
  if (!fs.existsSync(path.join(versionDir, "version.json"))) {
    fs.writeFileSync(path.join(versionDir, "version.json"),
      JSON.stringify({ app_version: "app-vGATE", min_host: "host-v0.0.0", files: {} }));
  }
});

function cleanup() { try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {} }

// ── spawn + MCP stdio handshake ───────────────────────────────────────────────
const child = spawn(exe, [], {
  cwd: conxaDir,
  env: {
    ...process.env,
    CONXA_DIR: conxaDir,
    CONXA_DATA_DIR: dataDir,
    CONXA_SKIP_SELF_UPDATE: "1",
  },
  stdio: ["pipe", "pipe", "pipe"],
});

let stderrTail = [];
child.stderr.on("data", d => {
  stderrTail.push(d.toString());
  if (stderrTail.length > 40) stderrTail = stderrTail.slice(-40);
});

let buf = "";
const pending = new Map();
child.stdout.on("data", d => {
  buf += d.toString();
  let nl;
  while ((nl = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, nl).trim();
    buf = buf.slice(nl + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch (_) { continue; }
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  }
});

let nextId = 1;
function send(method, params) {
  const id = nextId++;
  const p = new Promise((resolve, reject) => {
    pending.set(id, resolve);
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error(`timeout: ${method}`)); } }, 210000);
  });
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  return p;
}

let site = null;
function fail(msg) {
  console.error(`[gate] FAIL: ${msg}`);
  if (stderrTail.length) console.error("[gate] runtime stderr tail:\n" + stderrTail.join(""));
  try { child.kill(); } catch (_) {}
  if (site) site.close();
  cleanup();
  process.exit(1);
}

(async () => {
  try {
    await send("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "conxa-gate", version: "1.0.0" },
    });
    const resp = await send("tools/call", {
      name: "execute_skill",
      arguments: {
        skill: "gate-skill",
        workspace_id: "gate",
        inputs: { fixture_url: FIXTURE_URL },
        watch: false,
      },
    });
    const texts = ((resp.result && resp.result.content) || [])
      .filter(c => c && c.type === "text").map(c => c.text).join("\n");
    if (resp.error) return fail(`tools/call error: ${JSON.stringify(resp.error)}`);
    if (!/^(Done\.|Done, please check\.)/m.test(texts)) return fail(`replay did not reach 'Done.':\n${texts}`);
    console.log("[gate] PASS: selector engine resolved the fixture element — replay reached 'Done.'");

    // EXEC-10 tab round trip: download on one host, upload in a site-opened tab on another host,
    // a user-opened tab, a popup nobody uses, and switches back. Every step names its tab, so a
    // runtime that drives the wrong tab cannot find the element it needs and the gate fails.
    // 127.0.0.1 and localhost are different hosts to the browser, so this is cross-site.
    site = await fixtureSite.start();
    const port = new URL(site.url).port;
    const tabsResp = await send("tools/call", {
      name: "execute_skill",
      arguments: {
        skill: "gate-tabs",
        workspace_id: "gate",
        inputs: { base_url: site.url, other_url: `http://localhost:${port}` },
        watch: false,
      },
    });
    const tabsText = ((tabsResp.result && tabsResp.result.content) || [])
      .filter(c => c && c.type === "text").map(c => c.text).join("\n");
    if (tabsResp.error) return fail(`tools/call error (gate-tabs): ${JSON.stringify(tabsResp.error)}`);
    if (!/^(Done\.|Done, please check\.)/m.test(tabsText)) return fail(`tab round trip did not reach 'Done.':\n${tabsText}`);
    console.log("[gate] PASS: tab round trip — download on one site, upload in a new tab on another, back to the first tab — reached 'Done.'");

    // Same run, parked on the second tab (as Build Studio's test does before a step that can't be undone)
    // and then resumed: the resumed call must find the run's own tabs again, not open a blank one, and
    // still have the file it downloaded before the pause for the upload.
    const tabsInputs = { base_url: site.url, other_url: `http://localhost:${port}` };
    const PARK_AT = 5; // the upload on tab_1 of the file tab_0 downloaded
    const parked = await send("tools/call", {
      name: "execute_skill",
      arguments: { skill: "gate-tabs", workspace_id: "gate", inputs: tabsInputs, watch: false, _stop_before_step: PARK_AT },
    });
    const stopped = parked.result && parked.result._meta && parked.result._meta["conxa/stopped_at"];
    if (!stopped || stopped.step_index !== PARK_AT) return fail(`gate-tabs did not park before step ${PARK_AT + 1}:\n${JSON.stringify(parked.result || parked.error).slice(0, 1500)}`);
    const resumed = await send("tools/call", {
      name: "execute_skill",
      arguments: { skill: "gate-tabs", workspace_id: "gate", inputs: tabsInputs, watch: false, resume_from: PARK_AT, _guard_release: true },
    });
    const resumedText = ((resumed.result && resumed.result.content) || [])
      .filter(c => c && c.type === "text").map(c => c.text).join("\n");
    if (resumed.error) return fail(`tools/call error (gate-tabs resume): ${JSON.stringify(resumed.error)}`);
    if (!/^(Done\.|Done, please check\.)/m.test(resumedText)) return fail(`resume on the second tab did not reach 'Done.':\n${resumedText}`);
    console.log("[gate] PASS: a run parked on its second tab resumed on its own tabs — reached 'Done.'");
    try { child.kill(); } catch (_) {}
    await site.close();
    cleanup();
    process.exit(0);
  } catch (e) {
    return fail(e.message);
  }
})();
