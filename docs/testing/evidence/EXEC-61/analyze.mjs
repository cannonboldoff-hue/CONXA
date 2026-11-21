// Per-step time breakdown from a replay's telemetry.jsonl. Usage: node analyze.mjs <label>
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const HERE = path.dirname(fileURLToPath(import.meta.url));
const dir = path.join(HERE, "out", process.argv[2]);
const ev = fs.readFileSync(path.join(dir, "telemetry.jsonl"), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const by = new Map();
const row = (si) => { if (!by.has(si)) by.set(si, { si, ms: null, gate: null, mode: "", st: "", settle: null, tier: "", proof: "", fail: "", other: [] }); return by.get(si); };
for (const e of ev) {
  if (e.si == null) continue;
  const r = row(e.si);
  if (e.e === "step_ms") r.ms = e.ms;
  else if (e.e === "completion") { r.gate = e.waited; r.mode = e.mode; r.st = e.st + (e.still ? "/still" : "") + (e.bw ? `(${e.bw})` : ""); }
  else if (e.e === "settle_retry") r.settle = e.waited;
  else if (e.e === "tier_ok") r.tier = e.tier;
  else if (e.e === "proof") r.proof = e.v;
  else if (e.e === "step_fail") r.fail = e.fc;
  else if (!["verify_result", "app_obs"].includes(e.e)) r.other.push(e.e);
}
const rows = [...by.values()].sort((a, b) => a.si - b.si);
let tot = 0, gateTot = 0;
console.log("si | step_ms | gate_waited | mode | gate_status | rest_ms | settle | tier | proof | fail | other");
for (const r of rows) {
  tot += r.ms || 0; gateTot += r.gate || 0;
  console.log([r.si, r.ms, r.gate ?? "", r.mode, r.st, r.ms != null ? r.ms - (r.gate || 0) : "", r.settle ?? "", r.tier, r.proof, r.fail, [...new Set(r.other)].join(",")].join(" | "));
}
const n = rows.filter((r) => r.ms != null).length;
const end = ev.find((e) => /^wf_(done|fail|ok)/.test(e.e) || e.e === "wf_end");
console.log(`\nsteps=${n} sum_step_ms=${tot} mean=${Math.round(tot / Math.max(1, n))} gate_sum=${gateTot} (${Math.round(100 * gateTot / Math.max(1, tot))}%) end=${end ? JSON.stringify(end) : "?"}`);
