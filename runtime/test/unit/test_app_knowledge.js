"use strict";

// App knowledge (docs/TRD.md 11.5): a popup the skill's own fork does not list, but that the app
// map knows, is followed under the author's standing policy with the same fail-closed rules as a
// fork -- or asked about better. It is never dismissed or answered on anyone's behalf.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-app-knowledge-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");

const appKnowledge = require("../../app/app_knowledge");
const { runPlan } = require("../../app/run");

const PROBE = {
  title: "Discard changes?",
  text: "You have unsaved changes.",
  controls: [{ name: "Discard" }, { name: "Keep editing" }],
};

function popup(policy, extra = {}) {
  return {
    id: "p1",
    class: "decision",
    signature: { title: "Discard changes?", button_labels: ["Discard", "Keep editing"], message_tokens: ["unsaved", "changes"], container_selector: "[role=dialog]" },
    options: [
      { label: "Discard", role: "consequential", chosen: 0 },
      { label: "Keep editing", role: "neutral", chosen: 3 },
    ],
    app_policy: policy,
    ...extra,
  };
}

const knowledgeOf = (p, host = "x.test") => ({ schema_version: 1, apps: { [host]: { popups: [p] } } });

// ─── load ──────────────────────────────────────────────────────────────────────────────────

function skillDirWith(contents) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-skill-"));
  if (contents !== undefined) fs.writeFileSync(path.join(dir, "app_knowledge.json"), contents);
  return dir;
}

test("load returns the knowledge, or null for anything unusable", () => {
  const good = knowledgeOf(popup(null));
  assert.deepStrictEqual(appKnowledge.load(skillDirWith(JSON.stringify(good))), good);
  assert.strictEqual(appKnowledge.load(skillDirWith()), null, "no file");
  assert.strictEqual(appKnowledge.load(skillDirWith("{not json")), null, "malformed");
  assert.strictEqual(appKnowledge.load(skillDirWith(JSON.stringify({ ...good, schema_version: 2 }))), null, "newer schema");
  assert.strictEqual(appKnowledge.load(skillDirWith(JSON.stringify({ schema_version: 1 }))), null, "no apps");
  assert.strictEqual(appKnowledge.load(skillDirWith("[" + " ".repeat(300 * 1024) + "]")), null, "oversized");
  assert.strictEqual(appKnowledge.load(null), null);
});

test("hostOf lowercases, drops www and rejects non-http URLs", () => {
  assert.strictEqual(appKnowledge.hostOf("https://WWW.Drive.Example.com/x?y=1"), "drive.example.com");
  assert.strictEqual(appKnowledge.hostOf("about:blank"), "");
  assert.strictEqual(appKnowledge.hostOf("not a url"), "");
});

// ─── lookup ────────────────────────────────────────────────────────────────────────────────

test("lookupPopup finds a known popup on its host only", () => {
  const k = knowledgeOf(popup(null));
  assert.strictEqual(appKnowledge.lookupPopup(k, "x.test", PROBE).id, "p1");
  assert.strictEqual(appKnowledge.lookupPopup(k, "other.test", PROBE), null);
  assert.strictEqual(appKnowledge.lookupPopup(k, "x.test", { ...PROBE, controls: [{ name: "Discard" }] }), null, "a missing button is no match");
  assert.strictEqual(appKnowledge.lookupPopup(k, "x.test", { ...PROBE, native: true }), null, "native dialogs are never mapped");
  assert.strictEqual(appKnowledge.lookupPopup(null, "x.test", PROBE), null);
  assert.strictEqual(appKnowledge.lookupPopup(k, "x.test", null), null);
});

test("an ambiguous match is no match", () => {
  const a = popup(null, { id: "a" });
  const b = popup(null, { id: "b" });
  const k = { schema_version: 1, apps: { "x.test": { popups: [a, b] } } };
  assert.strictEqual(appKnowledge.lookupPopup(k, "x.test", PROBE), null);
});

// ─── decideKnown ───────────────────────────────────────────────────────────────────────────

test("no policy: a person decides, with the button other workflows chose suggested", () => {
  const d = appKnowledge.decideKnown(popup(null), PROBE);
  assert.deepStrictEqual(d, { do: "pause", reason: "known_popup", suggested: "Keep editing" });
});

test("auto on a harmless button clicks it", () => {
  const d = appKnowledge.decideKnown(popup({ mode: "auto", option: "Keep editing", author_approved: true }), PROBE);
  assert.deepStrictEqual(d, { do: "click", label: "Keep editing", source: "app_policy" });
});

test("auto on a consequential button needs the author's approval, and fails closed without it", () => {
  const approved = appKnowledge.decideKnown(popup({ mode: "auto", option: "Discard", author_approved: true }), PROBE);
  assert.strictEqual(approved.do, "click");
  for (const policy of [{ mode: "auto", option: "Discard" }, { mode: "auto", option: "Discard", author_approved: false }, { mode: "auto", option: "Discard", author_approved: "yes" }]) {
    assert.strictEqual(appKnowledge.decideKnown(popup(policy), PROBE).reason, "consequential_unapproved", JSON.stringify(policy));
  }
});

test("a policy naming a button that is not on screen pauses instead of guessing", () => {
  const d = appKnowledge.decideKnown(popup({ mode: "auto", option: "Save", author_approved: true }), PROBE);
  assert.strictEqual(d.reason, "app_policy_option_missing");
});

test("ask_human pauses, stop stops, anything else stops", () => {
  assert.strictEqual(appKnowledge.decideKnown(popup({ mode: "ask_human" }), PROBE).reason, "app_policy_ask");
  assert.deepStrictEqual(appKnowledge.decideKnown(popup({ mode: "stop", message: "No." }), PROBE), { do: "stop", message: "No." });
  assert.strictEqual(appKnowledge.decideKnown(popup({ mode: "weird" }), PROBE).do, "stop");
});

test("inside a repeated step a pause becomes a stop", () => {
  const d = appKnowledge.decideKnown(popup({ mode: "ask_human" }), PROBE, { loopDepth: 1 });
  assert.strictEqual(d.do, "stop");
  assert.match(d.message, /repeated step/);
});

// ─── in a run ──────────────────────────────────────────────────────────────────────────────

function mockPage(probe = PROBE) {
  const clicks = [];
  const page = {
    clicks,
    probe,
    waitForLoadState: async () => {},
    context: () => ({ on: () => {} }),
    url: () => "https://www.x.test/editor",
    evaluate: async () => page.probe,
    // clickDialogButtonByLabel: dialogs.last().getByRole("button", {name, exact}).first().click()
    locator: () => ({
      last: () => ({
        getByRole: (_role, opts) => ({ first: () => ({ click: async () => { clicks.push(opts.name); page.probe = null; } }) }),
      }),
    }),
  };
  return page;
}

// The fork lists a different popup, so PROBE is "unknown" to the fork itself.
const FORK = {
  type: "outcome_fork", timeout_ms: 1500, unknown_policy: "ask_human",
  states: [{
    id: "exists", kind: "dialog",
    signature: { title: "Upload options", button_labels: ["Replace", "Keep both"], message_tokens: [] },
    options: [{ id: "replace", label: "Replace", role: "dismiss", steps: [] }, { id: "keep", label: "Keep both", role: "neutral", steps: [] }],
    policy: { mode: "ask_human" },
  }],
};

async function run(page, knowledge, opts = {}) {
  const events = [];
  const tracker = { emit: (e, f) => events.push({ e, ...f }) };
  const result = await runPlan(page, [FORK], {}, 0, "ak-test", { tracker, appKnowledge: knowledge, ...opts });
  return { result, events };
}

const pausesWith = (check) => (err) => { assert.strictEqual(err.forkPause, true, `expected a fork pause, got: ${err.message}`); check(err); return true; };

test("a known popup with an approved harmless policy is followed without asking", async () => {
  const page = mockPage();
  const { events } = await run(page, knowledgeOf(popup({ mode: "auto", option: "Keep editing", author_approved: true })));
  assert.deepStrictEqual(page.clicks, ["Keep editing"]);
  const decided = events.find((e) => e.e === "fork_decided");
  assert.strictEqual(decided.src, "app_policy");
  assert.strictEqual(decided.opt, "Keep editing");
  assert.ok(events.some((e) => e.e === "fork_unknown"));
});

test("a consequential policy without approval pauses for a person and clicks nothing", async () => {
  const page = mockPage();
  await assert.rejects(
    () => run(page, knowledgeOf(popup({ mode: "auto", option: "Discard" }))),
    pausesWith((err) => {
      assert.strictEqual(err.info.reason, "consequential_unapproved");
      assert.strictEqual(err.info.knownFromMap, true);
    }),
  );
  assert.deepStrictEqual(page.clicks, []);
});

test("an approved consequential policy runs it", async () => {
  const page = mockPage();
  await run(page, knowledgeOf(popup({ mode: "auto", option: "Discard", author_approved: true })));
  assert.deepStrictEqual(page.clicks, ["Discard"]);
});

test("a known popup with no policy pauses with the real buttons, their risk and a suggestion", async () => {
  const page = mockPage();
  await assert.rejects(
    () => run(page, knowledgeOf(popup(null))),
    pausesWith((err) => {
      assert.strictEqual(err.info.knownFromMap, true);
      assert.strictEqual(err.info.suggested, "Keep editing");
      assert.deepStrictEqual(err.info.choices, [
        { id: "Discard", label: "Discard", role: "consequential" },
        { id: "Keep editing", label: "Keep editing", role: "neutral" },
        { id: "__stop__", label: "Stop the run", role: "stop" },
      ]);
    }),
  );
  assert.deepStrictEqual(page.clicks, []);
});

test("a popup nobody knows still pauses as before, with no map claim", async () => {
  const page = mockPage();
  await assert.rejects(
    () => run(page, knowledgeOf(popup(null), "other.test")),
    pausesWith((err) => {
      assert.strictEqual(err.info.knownFromMap, false);
      assert.deepStrictEqual(err.info.choices.map((c) => c.role), ["unknown", "unknown", "stop"]);
    }),
  );
  await assert.rejects(() => run(mockPage(), null), pausesWith((err) => assert.strictEqual(err.info.knownFromMap, false)));
});

test("a dry run does not click, and says so", async () => {
  const page = mockPage();
  const { result } = await run(page, knowledgeOf(popup({ mode: "auto", option: "Keep editing", author_approved: true })), { dryRun: true });
  assert.deepStrictEqual(page.clicks, []);
  assert.ok(result.warnings.some((w) => /Dry run: did not click "Keep editing"/.test(w)));
});

test("a clean page is unaffected by the knowledge", async () => {
  const page = mockPage(null);
  const { events } = await run(page, knowledgeOf(popup({ mode: "auto", option: "Discard", author_approved: true })));
  assert.deepStrictEqual(page.clicks, []);
  assert.ok(events.some((e) => e.e === "fork_clean"));
});

// ─── telemetry: what leaves the machine ────────────────────────────────────────────────────

test("observation keeps only the popup's shape, scrubbed", () => {
  const obs = appKnowledge.observation("https://www.drive.example.com/drive/folders/1AbCdEf9876543210ZZ?x=1", {
    title: 'Replace "q3-report.pdf"?',
    labels: ["Replace", "Keep both", "Cancel", "q3-report.pdf (3 MB)"],
    known: true,
  });
  assert.deepStrictEqual(obs, {
    host: "drive.example.com", rt: "/drive/folders/:id", tt: ["replace"], bt: ["Replace", "Keep both", "Cancel"], cl: "decision", k: 1,
  });
  assert.ok(!JSON.stringify(obs).includes("q3"));
});

test("observation is null for a non-web page or something that is not a choice", () => {
  const labels = ["A", "B"];
  assert.strictEqual(appKnowledge.observation("about:blank", { title: "t", labels }), null);
  assert.strictEqual(appKnowledge.observation("https://x.test/", { title: "t", labels: ["Only"] }), null);
  assert.strictEqual(appKnowledge.observation("https://x.test/", { title: "t", labels: ["report.pdf", "q3.xlsx"] }), null);
});

test("an unrecognised popup reports app_obs, scrubbed, and says whether the map knew it", async () => {
  const page = mockPage();
  page.probe = { title: 'Discard changes to "budget.xlsx"?', text: "Unsaved edits in budget.xlsx", controls: [{ name: "Discard" }, { name: "Keep editing" }] };
  const events = [];
  await assert.rejects(
    () => runPlan(page, [FORK], {}, 0, "ak-test", { tracker: { emit: (e, f) => events.push({ e, ...f }) }, appKnowledge: null }),
    (err) => err.forkPause === true,
  );
  const obs = events.find((e) => e.e === "app_obs");
  assert.deepStrictEqual(obs, { e: "app_obs", si: 0, host: "x.test", rt: "/editor", tt: ["discard", "changes"], bt: ["Discard", "Keep editing"], cl: "decision", k: 0 });
  assert.ok(!JSON.stringify(events).includes("budget"), "no file name leaves the machine");
});

test("a recognised popup reports k=1; a recognised clean page reports nothing", async () => {
  const known = [];
  await run(mockPage(), knowledgeOf(popup({ mode: "auto", option: "Keep editing", author_approved: true }))).then(({ events }) => known.push(...events));
  assert.strictEqual(known.find((e) => e.e === "app_obs").k, 1);
  const { events } = await run(mockPage(null), null);
  assert.ok(!events.some((e) => e.e === "app_obs"));
});
