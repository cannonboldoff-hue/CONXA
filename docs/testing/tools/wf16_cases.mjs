// WF-016 case driver. usage: WF16_ROOT=... node wf16_cases.mjs <case>   (cases: 1 3 4 5 6 7 8 9 10 12)
// Talks to the Conxa runtime exe over stdio exactly like an MCP client (see wf16_mcp.mjs).
import { Mcp, sleep } from "./wf16_mcp.mjs";
import fs from "node:fs";
import { execSync } from "node:child_process";
const W = "wf4-d-i18n-wiki-r5-06fd6288", L = "wf1-a-wikipedia-control-r4-71600c18", MUT = "mutator-fixture-72c45380",
  SPAN = "wf13-marie-curie-rt-283e911a", DEC = "wf10-decision-r3-94560d1d", DECS = "wf16-decision-safe", DECL = "wf16-decision-loop",
  BR = "dismiss-df-2d7cebf8", NOTE = "wf11-note-r1-eafa1f0a";
const NL = String.fromCharCode(10), TAB = String.fromCharCode(9);
const t0 = Date.now(), T = () => ((Date.now() - t0) / 1000).toFixed(1) + "s";
const rid = (t) => (/run_id: (r_\w+)/.exec(t) || [])[1];
const say = (...a) => console.log(T(), ...a);
const m = new Mcp(); await m.init();
const ex = async (a, label = "") => {
  const s = Date.now(); const r = await m.call("execute_skill", { watch: false, ...a }); const t = m.text(r);
  say("RESULT", label, "after", ((Date.now() - s) / 1000).toFixed(1) + "s", "isError=" + !!r.isError, "outcome=" + (r.structuredContent && r.structuredContent.outcome),
    NL + "   " + t.slice(0, 700).split(NL).join(NL + "   "));
  return { r, t };
};
const status = async (id) => { const t = m.text(await m.call("get_execution_status", id ? { run_id: id } : {})); say("STATUS", id || "", t.slice(0, 600)); return t; };
const FXLOG = process.cwd() + "/../evidence/WF-016/fixtures-8097.log"; // request log of our own fixture server on :8097
const fxLines = (re) => fs.readFileSync(FXLOG, "utf8").split(NL).filter((l) => re.test(l));
const fxHits = (re, n0, key) => fxLines(re).slice(n0).map((l) => decodeURIComponent((new RegExp(key + "([^ ]*)").exec(l) || [])[1] || l)).join(" | ");
const C = process.argv[2];

if (C === "1") {
  say("list_skills"); console.log(m.text(await m.call("list_skills")).slice(0, 600));
  say("get_skill_inputs", m.text(await m.call("get_skill_inputs", { skill: W })));
  const { t } = await ex({ skill: W, inputs: { search_wikipedia: "Automation" } }, "16.1");
  await status(rid(t)); await status();
} else if (C === "3") {
  const p1 = ex({ skill: L, inputs: { search_wikipedia: "Ada Lovelace" } }, "call-A"); await sleep(400);
  const p2 = ex({ skill: L, inputs: { search_wikipedia: "Ada Lovelace" } }, "call-B");
  await sleep(6000); await status(); await Promise.all([p1, p2]);
} else if (C === "4") {
  const long = ex({ skill: L, inputs: { search_wikipedia: "Ada Lovelace" } }, "LONG wiki"); await sleep(500);
  const short = ex({ skill: MUT, inputs: { name: "Ada", email: "a@b.c", shapeshifter: "x" } }, "SHORT mutator");
  await sleep(2500); const st = await status(); say("ACTIVE at +3s:", st);
  const s = JSON.parse(st).active_runs.find((x) => /mutator/.test(x.skill));
  if (s) { say("cancel", s.run_id); say("CANCEL RESP", m.text(await m.call("cancel_execution", { run_id: s.run_id }))); } else say("short run not active (finished before cancel)");
  await Promise.all([long, short]); await status();
} else if (C === "5" || C === "6") {
  // agent span (AI Review): pause, then answer. 5 = "person"; 6 = "banana" x3
  const inputs = { full_name: "Marie Curie", search_wikipedia: "Marie Curie" };
  const { r, t } = await ex({ skill: SPAN, inputs }, "start (expect pause at agent span)");
  console.log("PAUSE_FULL", JSON.stringify(r).slice(0, 3500));
  await status(rid(t)); await status();
  const answers = C === "5" ? ["person"] : ["banana", "banana", "banana"];
  for (const a of answers) {
    say("ANSWER", a);
    const x = await ex({ skill: SPAN, inputs, resume_from: 6, span_actions: { "6": { kind: "done", answer: { choice: a } } } }, "answer " + a);
    console.log("ANSWER_FULL", a, JSON.stringify(x.r).slice(0, 1200));
    if (!/paused at step/i.test(x.t)) break;
  }
  await status();
} else if (C === "7") {
  const cnt = () => { try { return execSync("powershell -NoProfile -File " + process.cwd() + "/wf16_tree.ps1 " + m.p.pid).toString().trim().split(NL).filter(Boolean); } catch (e) { return ["err"]; } };
  say("procs before", cnt().length);
  const inputs = { full_name: "Marie Curie", search_wikipedia: "Marie Curie" };
  await ex({ skill: SPAN, inputs }, "start (pause, never answered)");
  const paused = Date.now(); say("PAUSED; procs now", cnt().join(" | "));
  while (Date.now() - paused < 300000) { await sleep(20000); say("t+" + Math.round((Date.now() - paused) / 1000) + "s procs", cnt().length); }
  say("procs after", cnt().join(" | "));
  await ex({ skill: SPAN, inputs, resume_from: 6, span_actions: { "6": { kind: "done", answer: { choice: "person" } } } }, "late answer after expiry");
  await status();
} else if (C === "8") {
  const runOne = async (label, skill, extra, inputs = {}) => {
    const n0 = fxLines(/__log/).length; say("=====", label);
    const { r, t } = await ex({ skill, inputs, ...extra }, label); await sleep(1500);
    say("FIXTURE HITS during", label, ":", fxHits(/__log/, n0, "__log\\?") || "(none)");
    console.log("FULL", label, JSON.stringify(r).slice(0, 1800)); return { r, t };
  };
  const a = await runOne("8a normal run, unrecorded popup, reversible step", DECS, {});
  if (/paused|choice|popup/i.test(a.t)) {
    const n0 = fxLines(/__log/).length;
    await ex({ skill: DECS, resume_from: 3, review_results: { "3": { choice: "stop" } } }, "8a answer stop");
    say("FIXTURE HITS after stop:", fxHits(/__log/, n0, "__log\\?") || "(none)");
  }
  await runOne("8b dry_run", DECS, { dry_run: true });
  await runOne("8c loop", DECL, {}, { items: "a,b" });
  await runOne("8d destructive step", DEC, {});
  await runOne("8e scheduled", DECS, { _trigger: "scheduled" });
  await status();
} else if (C === "8e") {
  // scheduled run (nobody there to ask) and a correct stop answer on a normal run
  const n0 = fxLines(/__log/).length;
  await ex({ skill: DECS, inputs: {}, _trigger: "scheduled" }, "8e scheduled"); await sleep(1500);
  say("FIXTURE HITS scheduled:", fxHits(/__log/, n0, "__log\\?") || "(none)");
  const n1 = fxLines(/__log/).length;
  await ex({ skill: DECS, inputs: {} }, "8a again (normal)");
  await ex({ skill: DECS, inputs: {}, resume_from: 3, review_results: { "3": { choice: "__stop__" } } }, "answer __stop__"); await sleep(1500);
  say("FIXTURE HITS normal+stop:", fxHits(/__log/, n1, "__log\\?") || "(none)");
} else if (C === "9") {
  const { r } = await ex({ skill: BR, inputs: {} }, "9 branches skill on current app layer");
  console.log("FULL", JSON.stringify(r).slice(0, 4000));
} else if (C === "10") {
  say("get_skill_inputs", m.text(await m.call("get_skill_inputs", { skill: NOTE })));
  const cases = [["empty", ""], ["spaces", "   "], ["10k", "x".repeat(10000)], ["unicode", String.fromCodePoint(0x65e5, 0x672c, 0x8a9e, 32, 0x1f389, 32, 99, 97, 102, 0xe9)],
    ["injection", "<script>alert(1)</script>'; DROP TABLE users--"], ["newline-tab", "line1" + NL + "line2" + TAB + "end"],
    ["007", "007"], ["1e5", "1e5"], ["-0", "-0"], ["missing", undefined]];
  for (const [label, v] of cases) {
    const n0 = fxLines(/__log\?note/).length; say("=====", label);
    await ex({ skill: NOTE, inputs: v === undefined ? {} : { note: v } }, "10 " + label); await sleep(1200);
    say("FIXTURE", label, fxHits(/__log\?note/, n0, "note=") || "(no save reached the page)");
  }
} else if (C === "11") {
  // pack.json was edited by the caller: bogus sync_token + bogus tracking_token on a workspace whose cloud record has a real token
  await sleep(4000);
  say("list_skills", m.text(await m.call("list_skills")).slice(0, 500));
  say("get_runtime_status", m.text(await m.call("get_runtime_status")).slice(0, 1500));
  await ex({ skill: NOTE, inputs: { note: "token-test" } }, "11 execute with invalid token");
  await sleep(4000);
} else if (C === "12") {
  const n0 = fxLines(/__log\?note/).length, ids = [], t0r = Date.now();
  for (let i = 1; i <= 20; i++) {
    const r = await m.call("execute_skill", { skill: NOTE, inputs: { note: "q" + String(i).padStart(2, "0") }, watch: false });
    const id = rid(m.text(r)); ids.push([i, id, r.isError ? "ERR" : (r.structuredContent && r.structuredContent.outcome) || "?"]); say("run", i, id, ids[ids.length - 1][2]);
  }
  say("20 runs took", ((Date.now() - t0r) / 1000).toFixed(0) + "s");
  fs.writeFileSync(process.cwd() + "/../evidence/WF-016/16.12-runids.json", JSON.stringify(ids));
  await sleep(8000);
  say("fixture note saves seen:", fxLines(/__log\?note/).length - n0);
}
await sleep(3000); await m.close(); console.log("STDERR_TAIL", m.err.join("").split(NL).slice(-3).join(NL).slice(0, 600));
