"use strict";
// Covers durable_context.js — the "make the agent actually reach for the
// registered MCP tools" layer. Registering a server (mcp_register.js) makes
// tools callable; this makes them found.
//
// Same HOME/APPDATA sandboxing as test_mcp_register.js/test_mcp_hosts.js —
// durable_context.js resolves paths through mcp_hosts.js's buildContext(),
// which reads the real machine unless USERPROFILE/APPDATA/LOCALAPPDATA are
// overridden. Every test here must sandbox those, or it writes into this
// machine's real ~/.claude/skills, ~/.cursor/rules, etc.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ENV_KEYS = ["USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR", "COPILOT_HOME", "QWEN_HOME"];
let savedEnv;
test.beforeEach(() => {
  savedEnv = {};
  for (const k of ENV_KEYS) savedEnv[k] = process.env[k];
});
test.afterEach(() => {
  for (const k of ENV_KEYS) {
    if (savedEnv[k] === undefined) delete process.env[k];
    else process.env[k] = savedEnv[k];
  }
});

function mkFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "durable-context-test-"));
  const home = path.join(root, "home");
  const appData = path.join(home, "AppData", "Roaming");
  const localAppData = path.join(home, "AppData", "Local");
  fs.mkdirSync(appData, { recursive: true });
  fs.mkdirSync(localAppData, { recursive: true });
  process.env.USERPROFILE = home;
  process.env.HOME = home;
  process.env.APPDATA = appData;
  process.env.LOCALAPPDATA = localAppData;
  delete process.env.CLAUDE_CONFIG_DIR;
  return { root, home, appData, localAppData };
}
function mkdir(...segments) {
  const p = path.join(...segments);
  fs.mkdirSync(p, { recursive: true });
  return p;
}

function freshDurableContext() {
  delete require.cache[require.resolve("../../app/durable_context")];
  return require("../../app/durable_context");
}

test("buildNote: lists skills, falls back to a placeholder when empty", () => {
  const { buildNote } = freshDurableContext();
  const withSkills = buildNote("acme", ["invoice-approval", "onboard-user"]);
  assert.ok(withSkills.includes("2 automated workflows for **acme**"));
  assert.ok(withSkills.includes("`invoice-approval`"));
  assert.ok(withSkills.includes("`onboard-user`"));

  const empty = buildNote("acme", []);
  assert.ok(empty.includes("0 automated workflows"));
  assert.ok(empty.includes("no workflows synced yet"));
});

test("dataset guidance: the agent owns the file, the runtime gets one record per call", () => {
  const { buildNote } = freshDurableContext();
  const note = buildNote("acme", ["onboard-user"]);
  assert.ok(note.includes("one record per call"));
  assert.ok(note.includes("Never pass the file to Conxa"));

  const { SERVER_INSTRUCTIONS, CORE_TOOL_DEFS } = require("../../app/tool_defs");
  assert.ok(SERVER_INSTRUCTIONS.includes("ONE record per call"));
  assert.ok(SERVER_INSTRUCTIONS.includes("get_skill_inputs"));
  assert.ok(SERVER_INSTRUCTIONS.includes("Never pass the file"));
  const exec = CORE_TOOL_DEFS.find((t) => t.name === "execute_skill");
  assert.ok(exec.inputSchema.properties.inputs.description.includes("ONE record"));
});

test("writeDedicatedFile: creates parents, writes content, no-op when unchanged", async () => {
  const dir = mkFixture();
  const { writeDedicatedFile } = freshDurableContext();
  const filePath = path.join(dir.root, "nested", "SKILL.md");

  const r1 = await writeDedicatedFile(filePath, "hello");
  assert.strictEqual(r1.status, "ok");
  assert.strictEqual(fs.readFileSync(filePath, "utf8"), "hello");

  // Second call with identical content must be a true no-op (not just "ok" —
  // verify it doesn't even attempt a write by checking mtime is unchanged).
  const before = fs.statSync(filePath).mtimeMs;
  await new Promise((r) => setTimeout(r, 20));
  const r2 = await writeDedicatedFile(filePath, "hello");
  assert.strictEqual(r2.status, "ok");
  assert.strictEqual(fs.statSync(filePath).mtimeMs, before);
});

test("upsertSharedBlock: fresh file gets one marker-delimited block", async () => {
  const dir = mkFixture();
  const { upsertSharedBlock, markerStart, markerEnd } = freshDurableContext();
  const filePath = path.join(dir.root, "AGENTS.md");

  const r = await upsertSharedBlock(filePath, "acme", "line one\nline two");
  assert.strictEqual(r.status, "ok");
  const text = fs.readFileSync(filePath, "utf8");
  assert.ok(text.includes(markerStart("acme")));
  assert.ok(text.includes(markerEnd("acme")));
  assert.ok(text.includes("line one"));
});

test("upsertSharedBlock: surrounding customer content survives untouched", async () => {
  const dir = mkFixture();
  const { upsertSharedBlock } = freshDurableContext();
  const filePath = path.join(dir.root, "AGENTS.md");
  fs.writeFileSync(filePath, "# My project instructions\n\nAlways use TypeScript.\n");

  await upsertSharedBlock(filePath, "acme", "conxa content v1");
  const text = fs.readFileSync(filePath, "utf8");
  assert.ok(text.includes("# My project instructions"));
  assert.ok(text.includes("Always use TypeScript."));
  assert.ok(text.includes("conxa content v1"));
});

test("upsertSharedBlock: re-running replaces only our span, not the whole file", async () => {
  const dir = mkFixture();
  const { upsertSharedBlock } = freshDurableContext();
  const filePath = path.join(dir.root, "AGENTS.md");
  fs.writeFileSync(filePath, "# Before\n\ncustomer text\n\n# After\n");

  await upsertSharedBlock(filePath, "acme", "conxa v1");
  await upsertSharedBlock(filePath, "acme", "conxa v2");

  const text = fs.readFileSync(filePath, "utf8");
  assert.ok(text.includes("# Before"));
  assert.ok(text.includes("customer text"));
  assert.ok(text.includes("# After"));
  assert.ok(text.includes("conxa v2"));
  assert.ok(!text.includes("conxa v1"), "stale block content must not survive a re-run");
  // Exactly one marker pair — not one appended per run.
  assert.strictEqual((text.match(/conxa:acme >>>/g) || []).length, 1);
});

test("two companies' blocks in the same file don't collide", async () => {
  const dir = mkFixture();
  const { upsertSharedBlock } = freshDurableContext();
  const filePath = path.join(dir.root, "AGENTS.md");

  await upsertSharedBlock(filePath, "acme", "acme content");
  await upsertSharedBlock(filePath, "globex", "globex content");

  const text = fs.readFileSync(filePath, "utf8");
  assert.ok(text.includes("acme content"));
  assert.ok(text.includes("globex content"));

  await upsertSharedBlock(filePath, "acme", "acme content v2");
  const text2 = fs.readFileSync(filePath, "utf8");
  assert.ok(text2.includes("acme content v2"));
  assert.ok(text2.includes("globex content"), "updating acme's block must not disturb globex's");
});

test("updateDurableContext: only writes for hosts that are actually detected", async () => {
  const dir = mkFixture();
  mkdir(dir.home, ".claude"); // proxy dir, not the real detect signal
  fs.writeFileSync(path.join(dir.home, ".claude.json"), "{}"); // claude-code's real detect signal
  mkdir(dir.home, ".cursor");
  // windsurf, copilot-cli, factory, opencode, gemini-cli, qwen intentionally absent

  const { updateDurableContext } = freshDurableContext();
  const results = await updateDurableContext("acme", ["invoice-approval"]);

  const byTarget = Object.fromEntries(results.map((r) => [r.target, r]));
  assert.strictEqual(byTarget["claude-code"].status, "ok");
  assert.strictEqual(byTarget["cursor"].status, "ok");
  assert.strictEqual(byTarget["windsurf"].status, "skipped:not-detected");
  assert.strictEqual(byTarget["gemini-cli"].status, "skipped:not-detected");

  const skillMd = fs.readFileSync(path.join(dir.home, ".claude", "skills", "conxa-acme", "SKILL.md"), "utf8");
  assert.ok(skillMd.startsWith("---\nname: conxa-acme"));
  assert.ok(skillMd.includes("invoice-approval"));

  const cursorRule = fs.readFileSync(path.join(dir.home, ".cursor", "rules", "conxa-acme.mdc"), "utf8");
  assert.ok(cursorRule.startsWith("---\ndescription:"));
});

test("updateDurableContext: shared-file target (windsurf) uses the marker-block path", async () => {
  const dir = mkFixture();
  mkdir(dir.home, ".codeium", "windsurf");

  const { updateDurableContext, markerStart } = freshDurableContext();
  const results = await updateDurableContext("acme", ["onboard-user"]);

  const windsurf = results.find((r) => r.target === "windsurf");
  assert.strictEqual(windsurf.status, "ok");
  const text = fs.readFileSync(path.join(dir.home, ".codeium", "windsurf", "memories", "global_rules.md"), "utf8");
  assert.ok(text.includes(markerStart("acme")));
  assert.ok(text.includes("onboard-user"));
});

test("buildNote: names the web app each workflow works in, and still lists one with no app", () => {
  const dc = require("../../app/durable_context");
  const note = dc.buildNote("wrk_acme", ["upload_file", "legacy"], { upload_file: "drive.example.com" });
  assert.match(note, /\*\*upload_file\*\* \(works in `drive\.example\.com`\)/);
  assert.match(note, /- \*\*legacy\*\* — call/);
  assert.ok(!/legacy\*\* \(works in/.test(note));
  assert.strictEqual(dc.buildNote("wrk_acme", ["a"]), dc.buildNote("wrk_acme", ["a"], {}), "apps is optional");
});

test("appOfManifest reads the start URL's host", () => {
  const { appOfManifest } = require("../../app/app_knowledge");
  assert.strictEqual(appOfManifest({ target_url: "https://www.Drive.Example.com/drive/my-drive" }), "drive.example.com");
  assert.strictEqual(appOfManifest({}), "");
  assert.strictEqual(appOfManifest(null), "");
});
