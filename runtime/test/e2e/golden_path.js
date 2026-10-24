"use strict";
// Golden path (gap analysis G1): one compiled skill that exercises what a real recording has to
// survive — a templated page address (a value that "changes each run"), a cookie banner, a link,
// a download whose button lives inside an iframe, a Drive-shaped upload and an assertion — run
// twice against a hermetic http fixture with two different inputs and NO recompile. Spawns the
// real runtime MCP server (app/server.js, the same code the packed host loads).
//
// Usage: node golden_path.js     Exit 0 = both runs reached "Done."; non-zero = see printed reason.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const fixture = require("../fixture-site/server");

const RUNTIME_ROOT = path.join(__dirname, "..", "..");
const VERSION = "v0.0.0-golden";

const FRAME = { signals: [{ selector: 'iframe[data-testid="raw-frame"]', durability: 0.99 }] };
const sel = (s) => ({ selector: s, _explicit_selector: s });
const EXECUTION = [
  { type: "navigate", url: "{{base_url}}/repo/{{repository}}" },
  { type: "if_present", selector: "#cookie-banner", timeout_ms: 1500, steps: [{ type: "click", ...sel("#accept-cookies") }] },
  // A record that is provably not in the list: on_missing "skip" means nothing to do here, so the run moves on.
  { type: "click", intent: "open_the_other_file",
    identity_bundle: {
      signals: [{ engine: "testid", selector: '[data-testid="open-file"]', durability: 0.95 }],
      fingerprint: { role: "link", data_testid: "open-file" }, frame_chain: [],
    },
    entity_binding: { container_selector: 'li[data-row="file"]', identifier: "no-such-file", source: "literal", confirmed: true }, on_missing: "skip" },
  { type: "click", ...sel('[data-testid="open-file"]') },
  // Read a value off the page into a named output (the file page's heading minus its extension).
  { type: "extract", ...sel("h1"), output_name: "file_title", extract: { source: "text", pattern: "^(.*)\\.gitignore$" } },
  // Runs only when the caller supplies `note`; the target does not exist, so running it would fail.
  { type: "click", ...sel("#only-when-asked"), intent: "optional_extra", run_if: { input: "note", op: "not_empty" } },
  {
    type: "click",
    identity_bundle: {
      signals: [{ engine: "testid", selector: '[data-testid="download-btn"]', durability: 0.95 }],
      fingerprint: { role: "link", data_testid: "download-btn", inner_text: "Download raw file" },
      frame_chain: [FRAME],
    },
  },
  { type: "download_observed" },
  { type: "navigate", url: "{{base_url}}/drive" },
  { type: "upload", ...sel('[data-testid="drive-upload"]'), value: "{{downloaded_file}}" },
  { type: "assert", kind: "text", selector: "#status", text: "Uploaded: {{repository}}.gitignore" },
];

function stage(tmp) {
  const conxaDir = path.join(tmp, ".conxa");
  const slugDir = path.join(conxaDir, "skill-packs", "e2e", "_default", "golden-skill");
  const versionDir = path.join(slugDir, VERSION);
  fs.mkdirSync(versionDir, { recursive: true });
  const write = (rel, data) => fs.writeFileSync(path.join(versionDir, rel), JSON.stringify(data, null, 2));
  write("execution.json", EXECUTION);
  write("recovery.json", { steps: [] });
  write("inputs.json", { inputs: [
    { name: "base_url", type: "string", description: "Base address of the site" },
    { name: "repository", type: "string", description: "Which repository to take the file from" },
    { name: "note", type: "string", description: "Optional: only when set does the optional extra step run" },
  ] });
  write("manifest.json", {
    slug: "golden-skill", name: "Golden path", description: "Download a file from a repository page and upload it elsewhere.",
    version: "0.0.1", required_runtime: ">=1.0.0", company: "e2e", target_url: "",
    inputs_required: ["base_url", "repository"], structural_fingerprint: { landmarks: [], landmark_count: 0 }, checksum: {},
  });
  const link = path.join(slugDir, "current");
  fs.symlinkSync(versionDir, link, process.platform === "win32" ? "junction" : "dir");
  fs.writeFileSync(path.join(conxaDir, "skill-packs", "e2e", "pack.json"), JSON.stringify({
    workspace_id: "e2e", display_name: "Golden path", skill_pack_version: "0.0.1", required_runtime: ">=1.0.0",
    target_url: "", protected_url: "", skills: ["golden-skill"], groups: [{ id: "g", name: "Fixture", apps: [] }], tracking: { enabled: false },
  }, null, 2));
  return conxaDir;
}

(async () => {
  const site = await fixture.start();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-golden-"));
  const dataDir = path.join(tmp, "data");
  fs.mkdirSync(path.join(dataDir, "cache", "sessions"), { recursive: true });
  fs.mkdirSync(path.join(dataDir, "logs"), { recursive: true });
  const conxaDir = stage(tmp);
  const msPlaywright = path.join(os.homedir(), "AppData", "Local", "ms-playwright");
  const child = spawn(process.execPath, [path.join(RUNTIME_ROOT, "app", "server.js")], {
    cwd: RUNTIME_ROOT,
    env: { ...process.env, CONXA_DIR: conxaDir, CONXA_DATA_DIR: dataDir, CONXA_SKIP_SELF_UPDATE: "1",
      PLAYWRIGHT_BROWSERS_PATH: fs.existsSync(msPlaywright) ? msPlaywright : (process.env.PLAYWRIGHT_BROWSERS_PATH || "") },
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", (d) => { stderr = (stderr + d.toString()).slice(-4000); });
  let buf = "";
  const pending = new Map();
  child.stdout.on("data", (d) => {
    buf += d.toString();
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
      if (!line) continue;
      try { const m = JSON.parse(line); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } } catch (_) {}
    }
  });
  let nextId = 1;
  const send = (method, params) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, resolve);
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error(`timeout: ${method}`)); } }, 90000);
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  });
  const done = async (code, msg) => {
    if (msg) console.error(`[golden] FAIL: ${msg}` + (stderr ? `\n[golden] runtime stderr tail:\n${stderr}` : ""));
    try { child.kill(); } catch (_) {}
    await site.close();
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {}
    process.exit(code);
  };

  try {
    await send("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "conxa-golden", version: "1.0.0" } });
    for (const repository of ["gitignore", "templates"]) {
      const resp = await send("tools/call", {
        name: "execute_skill",
        arguments: { skill: "golden-skill", workspace_id: "e2e", inputs: { base_url: site.url, repository }, watch: false },
      });
      const text = ((resp.result && resp.result.content) || []).filter((c) => c && c.type === "text").map((c) => c.text).join("\n");
      if (resp.error) return done(1, `[${repository}] ${JSON.stringify(resp.error)}`);
      if (!/^Done\./m.test(text)) return done(1, `[${repository}] did not reach 'Done.':\n${text}`);
      const out = resp.result && resp.result.structuredContent && resp.result.structuredContent.outputs;
      if (!out || out.file_title !== repository) return done(1, `[${repository}] expected outputs.file_title="${repository}", got ${JSON.stringify(out)}`);
      if (!/was skipped: its record isn't on the page/.test(text)) return done(1, `[${repository}] on_missing skip not reported:\n${text}`);
      if (!/Outputs:\n  file_title: /.test(text) || !/Skipped because an input was empty/.test(text)) return done(1, `[${repository}] outputs / skipped notes missing:\n${text}`);
      console.log(`[golden] PASS: repository="${repository}" — templated address, banner, extract -> outputs, run_if skip, iframe download, upload, assertion.`);
    }
    console.log("[golden] PASS: one compiled skill ran with two different inputs and no recompile.");
    // Replay up to a step and park there (segment re-recording): nothing at or after the step has run.
    const stop = await send("tools/call", {
      name: "execute_skill",
      arguments: { skill: "golden-skill", workspace_id: "e2e", inputs: { base_url: site.url, repository: "gitignore" }, watch: false, _stop_before_step: 6 },
    });
    const stopped = stop.result && stop.result._meta && stop.result._meta["conxa/stopped_at"];
    const stopText = ((stop.result && stop.result.content) || []).map((c) => c.text).join("\n");
    if (!stopped || stopped.step_index !== 6 || /^Done\./m.test(stopText)) return done(1, `_stop_before_step=5 did not park at step 6:\n${stopText}\n${JSON.stringify(stop.result && stop.result._meta)}`);
    console.log("[golden] PASS: _stop_before_step parks before the named step without running it.");
    return done(0);
  } catch (e) {
    return done(1, e.message);
  }
})();
