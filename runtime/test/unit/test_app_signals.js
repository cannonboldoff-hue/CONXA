"use strict";
// Proof of Done stage 6: an app that is not in English is judged by ITS OWN words (learned from its recordings),
// beside the English defaults. Without them a German "wird hochgeladen" is just new text, and the run says done too early.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-signals-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";
process.env.CONXA_COMPLETION_START_GRACE_MS = "150";

const test = require("node:test");
const assert = require("node:assert");
const { EventEmitter } = require("node:events");

const pageScripts = require("../../app/page_scripts");
const knowledge = require("../../app/app_knowledge");
const { classifySample, waitForCompletion } = require("../../app/completion");
const { classifyCompletionFailure } = require("../../app/fault_classify");
const { valueFreeWords } = require("../../app/effect_probes");
const { runPlan } = require("../../app/run");

const CLEAN = { busyCount: 0, regions: [] };
const TRAY = { busyCount: 0, regions: ["Datei wird hochgeladen"] };
const DONE = { busyCount: 0, regions: ["1 Upload abgeschlossen"] };
const FAILED = { busyCount: 0, regions: ["Upload fehlgeschlagen: Speicher voll"] };

const KNOWLEDGE = {
  schema_version: 1,
  apps: { "drive.example.de": { signals: { progress: ["hochgeladen", "laeuft"], success: ["abgeschlossen"], failure: ["fehlgeschlagen"] } } },
};

test("wordsRegex matches whole words in any language, ignores short or non-letter words, and is null when nothing is usable", () => {
  const re = knowledge.wordsRegex(["hochgeladen", "wird", "Abgeschlossen", "x1y2z3", "gelöscht"]);
  assert.ok(re.test("Datei wird hochgeladen..."));
  assert.ok(re.test("1 Upload ABGESCHLOSSEN"));
  assert.ok(re.test("Eintrag gelöscht"), "an umlaut is a letter, not a word break");
  assert.ok(!re.test("hochgeladene"), "a longer word is a different word");
  assert.ok(!re.test("wird"), "'wird' is too short to be learned");
  assert.strictEqual(knowledge.wordsRegex(["abc", "12345", ""]), null);
  assert.strictEqual(knowledge.wordsRegex(undefined), null);
});

test("signalsFor reads the words of the host a page is on, and nothing for any other host or no knowledge", () => {
  const s = knowledge.signalsFor(KNOWLEDGE, "drive.example.de");
  assert.ok(s.progress.test("wird hochgeladen") && s.success.test("abgeschlossen") && s.failure.test("fehlgeschlagen"));
  const none = knowledge.signalsFor(KNOWLEDGE, "other.example.com");
  assert.deepStrictEqual(none, { progress: null, success: null, failure: null });
  assert.deepStrictEqual(knowledge.signalsFor(null, "drive.example.de"), { progress: null, success: null, failure: null });
});

test("valueFreeWords keeps a whole non-English word, not the pieces around its umlaut", () => {
  assert.deepStrictEqual(valueFreeWords("Eintrag gelöscht"), ["eintrag", "gelöscht"]);
});

test("without the app's words a German tray is only new text; with them it is progress and a German failure is a failure", () => {
  assert.strictEqual(classifySample(TRAY, CLEAN).busy, false, "the English defaults know nothing of 'hochgeladen'");
  const signals = knowledge.signalsFor(KNOWLEDGE, "drive.example.de");
  assert.strictEqual(classifySample(TRAY, CLEAN, signals).busy, true);
  assert.strictEqual(classifyCompletionFailure(FAILED.regions), null);
  assert.strictEqual(classifyCompletionFailure(FAILED.regions, signals.failure).kind, "action_failed");
  // the English defaults still work beside the app's words
  assert.strictEqual(classifyCompletionFailure(["Upload failed"], signals.failure).kind, "action_failed");
});

function livePage(samples, url = "https://drive.example.de/ordner") {
  const page = new EventEmitter();
  let calls = 0;
  const pressed = [];
  Object.assign(page, {
    pressed, url: () => url, title: async () => "Drive", mainFrame: () => "main", context: () => ({ on: () => {} }),
    waitForLoadState: async () => {}, waitForTimeout: async () => {}, screenshot: async () => Buffer.from(""),
    keyboard: { press: async (k) => { pressed.push(k); } },
    evaluate: async (script) => (script === pageScripts.completionProbe ? samples[Math.min(calls++, samples.length - 1)] : null),
  });
  return page;
}
const STEP = () => [{ type: "keyboard_shortcut", value: "Enter", intent: "Datei hochladen" }];
const tracker = () => ({ emit: () => {} });

test("through a run: a German upload is waited out when the app's words are known, and called done too early when they are not", async () => {
  const waited = async (opts, slug) => {
    const events = [];
    const result = await runPlan(livePage([CLEAN, TRAY, TRAY, TRAY, DONE]), STEP(), {}, 0, slug, { tracker: { emit: (c, d) => events.push([c, d]) }, ...opts });
    assert.strictEqual(result.proof.steps[0].verdict, "proven");
    return events.find(([c]) => c === "completion")[1].waited;
  };
  const withWords = await waited({ appKnowledge: KNOWLEDGE }, "slug-de-1");
  // no knowledge: the tray reads as plain new text, so quiet is declared after half a second, before the work ended
  const without = await waited({}, "slug-de-2");
  assert.ok(withWords > without + 300, `with the app's words it waited ${withWords} ms, without them ${without} ms`);
});

test("through a run: a German failure message fails the upload when its failure word is known", async () => {
  await assert.rejects(
    runPlan(livePage([CLEAN, TRAY, FAILED]), STEP(), {}, 0, "slug-de-3", { tracker: tracker(), appKnowledge: KNOWLEDGE }),
    (err) => {
      assert.strictEqual(err.fault, true);
      assert.match(err.message, /fehlgeschlagen/);
      return true;
    },
  );
});

test("the gate itself waits while the app's own progress words show, and stops when they clear", async () => {
  const signals = knowledge.signalsFor(KNOWLEDGE, "drive.example.de");
  const seq = [TRAY, TRAY, TRAY, DONE];
  let n = 0;
  const page = { evaluate: async (s) => (s === pageScripts.completionProbe ? seq[Math.min(n++, seq.length - 1)] : null), on: () => {} };
  const v = await waitForCompletion(page, { baseline: CLEAN, sinceMs: 0, mode: "full", budgetMs: 1500, pollMs: 10, quietMs: 30, startGraceMs: 120, signals });
  assert.strictEqual(v.status, "done");
  assert.ok(n >= 4, `stopped before the tray cleared (${n} samples)`);
});
