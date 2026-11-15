"use strict";
// W1: a parent that never reads our stderr must not be able to freeze the runtime. On Windows a write to a full pipe
// blocks the whole thread (timers, the run deadline, the inspector), so the stderr copy goes through stderr_sink.js.
const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { spawn } = require("node:child_process");

const SINK = path.join(__dirname, "..", "..", "app", "stderr_sink.js");

function runChild(src, ms) {
  return new Promise((resolve) => {
    // stderr is piped and NEVER read: this is the soak harness's mistake, reproduced on purpose.
    const c = spawn(process.execPath, ["-e", src], { stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    c.stdout.on("data", (d) => { out += d; });
    const timer = setTimeout(() => { c.kill(); resolve({ out, hung: true }); }, ms);
    c.on("exit", () => { clearTimeout(timer); resolve({ out, hung: false }); });
  });
}

test("a parent that never reads stderr cannot freeze the process (sink drops instead of waiting)", async () => {
  const src = `
    const { createStderrSink } = require(${JSON.stringify(SINK)});
    const sink = createStderrSink();
    const line = "x".repeat(1000) + "\\n";
    let ticks = 0;
    const t = setInterval(() => {
      for (let i = 0; i < 20; i++) sink.write(line);   // ~20 KB per tick, ~2 MB over the run: far past any pipe size
      if (++ticks >= 100) { clearInterval(t); console.log(JSON.stringify({ done: true, ...sink.stats() })); process.exit(0); }
    }, 1);
  `;
  const r = await runChild(src, 15000);
  assert.strictEqual(r.hung, false, "the process must keep running its timers while stderr is unread");
  const stats = JSON.parse(r.out.trim().split("\n").pop());
  assert.ok(stats.done && stats.dropped > 0, "lines past the byte budget are dropped, not waited for");
});

test("without the sink the same program freezes (pins the failure this guards against)", async (t) => {
  if (process.platform !== "win32") return t.skip("blocking pipe writes are a Windows behaviour");
  const src = `
    const line = "x".repeat(1000) + "\\n";
    let ticks = 0;
    const t = setInterval(() => {
      for (let i = 0; i < 20; i++) process.stderr.write(line);
      if (++ticks >= 100) { clearInterval(t); console.log("done"); process.exit(0); }
    }, 1);
  `;
  const r = await runChild(src, 4000);
  assert.strictEqual(r.hung, true, "a direct stderr write to an unread pipe blocks the process on Windows");
});

test("lines under the budget are all delivered, in order, to a parent that does read", async () => {
  const src = `
    const { createStderrSink } = require(${JSON.stringify(SINK)});
    const sink = createStderrSink();
    for (let i = 0; i < 50; i++) sink.write("line " + i + "\\n");
    setTimeout(() => process.exit(0), 300);
  `;
  const got = await new Promise((resolve) => {
    const c = spawn(process.execPath, ["-e", src], { stdio: ["ignore", "pipe", "pipe"] });
    let err = "";
    c.stderr.on("data", (d) => { err += d; });
    c.on("exit", () => resolve(err));
  });
  const lines = got.trim().split("\n");
  assert.strictEqual(lines.length, 50);
  assert.deepStrictEqual(lines.slice(0, 3), ["line 0", "line 1", "line 2"]);
});

test("stray console.error and Node warnings cannot freeze the process either once stderr is taken over", async () => {
  const src = `
    const { createStderrSink, takeOverProcessStderr } = require(${JSON.stringify(SINK)});
    takeOverProcessStderr(createStderrSink());
    const line = "x".repeat(1000);
    let ticks = 0;
    const t = setInterval(() => {
      for (let i = 0; i < 20; i++) console.error(line);        // main-thread writes, no sink call in sight
      if (++ticks >= 100) { clearInterval(t); console.log("done"); process.exit(0); }
    }, 1);
  `;
  const r = await runChild(src, 15000);
  assert.strictEqual(r.hung, false, "console.error must not block the process");
  assert.match(r.out, /done/);
});
