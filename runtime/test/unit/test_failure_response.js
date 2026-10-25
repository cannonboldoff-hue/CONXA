"use strict";
// Unit tests for failure_response.js — the agent recovery-payload assembly
// extracted from server.js. Mock page objects only; no browser, no network.
//
// Tier model: the agent round is ARMED from round one — ranked indexed digest
// (browser-use style) AND screenshots (CUA style) in the same payload. The old
// split (text-only first round, vision withheld for a separate later round) was
// a token-cost lever that starved the attempt most likely to succeed. Ceiling 3
// and 4 are now identical; ceiling 2 stays deterministic with no agent handoff.
// Module-level round state (recovery_stage.js) is keyed by
// `${workspace}:${slug}` — every test uses its own slug to isolate.
const test   = require("node:test");
const assert = require("node:assert");
const fs     = require("fs");
const os     = require("os");
const path   = require("path");

const {
  buildFailureResponse,
  stepRecoveryContext,
  digestTargetContext,
  executedStepsBreadcrumb,
  recordedContextBlock,
  anchorSentenceBlock,
  phaseHintBlock,
} = require("../../app/failure_response");
const pageScripts = require("../../app/page_scripts");

let tmpDirs = [];
function tmpSkillDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "fail-resp-"));
  tmpDirs.push(dir);
  return dir;
}

const BASE_DEPS = {
  agentRecoveryEnabled: false,
  maxRecoveryTier: 4,
  appendRecoveryEvent: () => {},
  stepAssertions: () => [],
  frameScopedInventory: async () => null,
};

// Mock page whose evaluate() dispatches on the injected page-script function —
// failure_response gathers three different things from the page realm:
// the interactive inventory, scrollY, and the park-style page fingerprint.
function mockPage({ inventory = [], interactiveCount = inventory.length } = {}) {
  return {
    url: () => "https://x.example/",
    viewportSize: () => ({ width: 1280, height: 800 }),
    evaluate: async (fn) => {
      if (fn === pageScripts.domInventory) return inventory;
      if (fn === pageScripts.getScrollY) return 42;
      return { interactiveCount, text: "body text" }; // pageScripts.pageFingerprint shape
    },
    screenshot: async () => Buffer.from("shot"),
  };
}

function entry(slug, extra = {}) {
  return { slug, workspace_id: `ws-${slug}`, skillDir: tmpSkillDir(), ...extra };
}

test.after?.(() => {
  for (const dir of tmpDirs) fs.rmSync(dir, { recursive: true, force: true });
});

test("stepRecoveryContext: anchors outrank stale fingerprint text", () => {
  const ctx = stepRecoveryContext({
    failedStep: {
      type: "click",
      _intent: "Submit the login form",
      anchors: [{ text: "  Sign in ", priority: 1 }, { text: "Login button", priority: 5 }],
      identity_bundle: { fingerprint: { role: "button", inner_text: "OLD TEXT", data_testid: "submit" } },
    },
  });
  assert.strictEqual(ctx.action, "click");
  assert.strictEqual(ctx.intent, "Submit the login form");
  assert.strictEqual(ctx.target.text, "Login button", "highest-priority anchor wins over inner_text");
  assert.deepStrictEqual(ctx.anchors, ["Login button", "Sign in"]);
});

test("stepRecoveryContext: strips empty target fields and reports frame depth", () => {
  const ctx = stepRecoveryContext({
    failedStep: {
      identity_bundle: { fingerprint: {}, frame_chain: [{}, {}] },
    },
  });
  assert.strictEqual(ctx.target, undefined, "no positive identity signals → no target block");
  assert.deepStrictEqual(ctx.frame, { depth: 2 });
});

test("digestTargetContext: exposes tag/text/testid/anchors for ranking", () => {
  const ctx = digestTargetContext({
    failedStep: {
      anchors: [{ text: "Buy now", priority: 3 }],
      identity_bundle: { fingerprint: { tag: "button", role: "button", inner_text: "Buy Now", data_testid: "buy-btn" } },
    },
  });
  assert.strictEqual(ctx.tag, "button");
  assert.strictEqual(ctx.data_testid, "buy-btn");
  assert.deepStrictEqual(ctx.anchors, ["Buy now"]);
});

test("executedStepsBreadcrumb: null for step 0 / non-array steps", () => {
  assert.strictEqual(executedStepsBreadcrumb(null, 2), null);
  assert.strictEqual(executedStepsBreadcrumb([{ type: "navigate" }], 0), null);
});

test("ceiling 2 (Build Studio), no run workspace configured: deterministic text-only failure, no screenshots", async () => {
  // BUILD-26 stage (a1): the ceiling branch now DOES capture a screenshot when deps.dataDir +
  // deps.runId are present (see the next test) — this test covers the case with neither set
  // (no test-run context, e.g. a unit-test caller), where _writeStudioEvidence must no-op before
  // ever touching page.screenshot().
  let screenshotCalls = 0;
  const page = { url: () => "https://x.example/", screenshot: async () => { screenshotCalls++; return Buffer.from("x"); } };
  const events = [];
  const resp = await buildFailureResponse(
    page,
    { message: "element not found", failedAt: 3 },
    entry("studio-ceiling"),
    null,
    null,
    { ...BASE_DEPS, agentRecoveryEnabled: false, maxRecoveryTier: 2, appendRecoveryEvent: (e) => events.push(e) }
  );
  assert.strictEqual(screenshotCalls, 0, "no dataDir/runId → evidence write no-ops before any screenshot");
  assert.strictEqual(resp.content.length, 1);
  assert.match(resp.content[0].text, /Execution failed at step 4/);
  assert.match(resp.content[0].text, /Recovery ceiling Tier 2/);
  assert.ok(!/step_overrides/.test(resp.content[0].text), "no agent handoff protocol at ceiling 2");
  assert.ok(events.some(e => e.event === "recovery_ceiling_reached"));
});

test("ceiling 2 with a run workspace configured: writes evidence.json + failure.jpg, response text unchanged", async () => {
  const dataDir = tmpSkillDir();
  const page = mockPage({ inventory: [{ tag: "button", text: "Submit" }] });
  const resp = await buildFailureResponse(
    page,
    { message: "element not found", failedAt: 3, failedStep: { type: "click", _phase: "act" } },
    entry("studio-evidence"),
    null,
    null,
    {
      ...BASE_DEPS, agentRecoveryEnabled: false, maxRecoveryTier: 2,
      dataDir, runId: "run_test1", runStartTs: new Date(0).toISOString(),
    }
  );
  // The reviewer-facing response is untouched by evidence capture — still the terse 4-line shape.
  assert.strictEqual(resp.content.length, 1);
  assert.match(resp.content[0].text, /Recovery ceiling Tier 2/);

  const evidenceDir = path.join(dataDir, "runs", "run_test1", "_evidence");
  const evidence = JSON.parse(fs.readFileSync(path.join(evidenceDir, "evidence.json"), "utf8"));
  assert.strictEqual(evidence.run_id, "run_test1");
  assert.strictEqual(evidence.slug, "studio-evidence");
  assert.strictEqual(evidence.phase, "act");
  assert.ok(Array.isArray(evidence.inventory) && evidence.inventory.length >= 1);
  assert.ok(fs.existsSync(path.join(evidenceDir, "failure.jpg")), "live failure screenshot written");
  assert.ok(!fs.existsSync(path.join(evidenceDir, "pre_step.jpg")), "no err.preShot on this failure → not written");
});

test("evidence.json names the failure kind, the inputs the step read, and per-signal match counts", async () => {
  const dataDir = tmpSkillDir();
  const step = { type: "click", target: { name: "{{gitignore_file}}, (File)" }, identity_bundle: { signals: [{ selector: "role=link[name=\"{{gitignore_file}}\"]" }] } };
  await buildFailureResponse(
    mockPage(),
    {
      message: "Ambiguous element resolution", failedAt: 1, failedStep: step, ambiguous: true,
      signalCounts: [{ selector: "role=link[name=\"{{gitignore_file}}\"]", matches: 3 }],
    },
    entry("studio-evidence-inputs"), null, null,
    { ...BASE_DEPS, maxRecoveryTier: 2, dataDir, runId: "run_inputs1", runStartTs: new Date(0).toISOString(),
      inputs: { gitignore_file: "gitignore", unused: "x" } },
  );
  const evidence = JSON.parse(fs.readFileSync(path.join(dataDir, "runs", "run_inputs1", "_evidence", "evidence.json"), "utf8"));
  assert.strictEqual(evidence.failure_kind, "ambiguous");
  assert.deepStrictEqual(evidence.input_refs, [{ id: "gitignore_file", empty: false }]);
  assert.ok(!JSON.stringify(evidence).includes("\"gitignore\""), "input values never reach the evidence file");
  assert.strictEqual(evidence.signal_counts[0].matches, 3);
});

test("failureKind: one class per find-failure flag, 'other' otherwise", () => {
  const { failureKind } = require("../../app/failure_response");
  assert.strictEqual(failureKind({ resolveMiss: true }), "not_found");
  assert.strictEqual(failureKind({ entityNotFound: true }), "entity_not_found");
  assert.strictEqual(failureKind({ frameNotFound: true }), "frame_not_found");
  assert.strictEqual(failureKind({ verifyFail: true }), "verify_fail");
  assert.strictEqual(failureKind({}), "other");
});

test("evidence write never breaks the failure response when the page.screenshot() call throws", async () => {
  const dataDir = tmpSkillDir();
  const page = {
    url: () => "https://x.example/",
    screenshot: async () => { throw new Error("boom"); },
    evaluate: async () => null,
    viewportSize: () => null,
  };
  const resp = await buildFailureResponse(
    page,
    { message: "element not found", failedAt: 0 },
    entry("studio-evidence-fail"),
    null,
    null,
    { ...BASE_DEPS, agentRecoveryEnabled: false, maxRecoveryTier: 2, dataDir, runId: "run_test2" }
  );
  assert.match(resp.content[0].text, /Recovery ceiling Tier 2/, "a failed capture still returns the normal response");
});

test("first agent round: indexed digest, candidate_index protocol, screenshots included", async () => {
  let screenshotCalls = 0;
  const page = mockPage({
    inventory: [
      { tag: "button", text: "Purchase order", "data-testid": "buy-btn" },
      { tag: "a", text: "Order history" },
    ],
  });
  page.screenshot = async () => { screenshotCalls++; return Buffer.from("shot"); };

  const events = [];
  const resp = await buildFailureResponse(
    page,
    { message: "strict mode violation", failedAt: 1 },
    entry("t3-first"),
    { emit() {} },
    null,
    { ...BASE_DEPS, agentRecoveryEnabled: true, maxRecoveryTier: 4, appendRecoveryEvent: (e) => events.push(e) }
  );

  assert.ok(screenshotCalls >= 1, "the first agent round is armed — it captures the live page");
  assert.ok(resp.content.some(c => c.type === "image"), "screenshots ride along on round one");

  const texts = resp.content.filter(c => c.type === "text").map(c => c.text).join("\n");
  assert.match(texts, /resume_from: 1/);
  assert.match(texts, /candidate_index/);
  assert.match(texts, /confidence/);
  assert.match(texts, /ranked against the recorded target/);
  assert.match(texts, /\[0\] button testid=buy-btn "Purchase order"/, "strongest match ranks first");
  assert.match(texts, /\[1\] a "Order history"/);
  assert.ok(events.some(e => e.event === "agent_recovery_requested" && e.tier === 4 && e.round === 1),
    "round one is already the armed tier");
});

test("second round for the same step is a fresh armed payload, not a re-roll", async () => {
  let screenshotCalls = 0;
  const page = mockPage({ inventory: [{ tag: "button", text: "Submit" }] });
  page.screenshot = async () => { screenshotCalls++; return Buffer.from("shot"); };

  const ent = entry("t4-second");
  const commonDeps = { ...BASE_DEPS, agentRecoveryEnabled: true, maxRecoveryTier: 4 };

  await buildFailureResponse(page, { message: "not found", failedAt: 2 }, ent, { emit() {} }, null, commonDeps);

  const events = [];
  const resp = await buildFailureResponse(
    page,
    { message: "still not found", failedAt: 2 },
    ent,
    { emit() {} },
    null,
    { ...commonDeps, appendRecoveryEvent: (e) => events.push(e) }
  );

  assert.ok(screenshotCalls >= 1, "vision round captures the failing page");
  const texts = resp.content.map(c => c.text || "").join("\n");
  assert.match(texts, /Self-healing recovery\. The deterministic cascade could not resolve/);
  assert.ok(resp.content.some(c => c.type === "image"), "screenshots ride along the vision round");
  assert.match(texts, /candidate_index/, "closing edge unchanged — index nomination or selector");
  assert.ok(events.some(e => e.event === "agent_recovery_requested" && e.tier === 4 && e.round === 2));
});

test("a rejected agent override is reported back so the next round iterates", async () => {
  const page = mockPage();
  const resp = await buildFailureResponse(
    page,
    {
      message: "ambiguous",
      failedAt: 5,
      overrideValidationFailed: true,
      overrideReason: "ambiguous",
      failedStep: { type: "click", _agent_override: true, identity_bundle: { fingerprint: {} } },
    },
    entry("override-t4"),
    { emit() {} },
    null,
    { ...BASE_DEPS, agentRecoveryEnabled: true, maxRecoveryTier: 4 }
  );
  const texts = resp.content.map(c => c.text || "").join("\n");
  assert.match(texts, /Self-healing recovery\. The deterministic cascade could not resolve/);
  assert.match(texts, /previous recovery pick/, "reports WHY the last pick was rejected");
});

test("ceiling 3 now behaves exactly like ceiling 4 — armed from round one", async () => {
  // Ceiling 3 used to mean "reason, but never spend image tokens" — a cost control that is gone.
  // Collapsing it leaves one payload shape above the Studio ceiling, so a pack can no longer be
  // armed for one round and starved for the next.
  const page = mockPage({ inventory: [{ tag: "button", text: "Go" }] });
  const events = [];
  const resp = await buildFailureResponse(
    page, { message: "not found", failedAt: 0 }, entry("ceiling3"), { emit() {} }, null,
    { ...BASE_DEPS, agentRecoveryEnabled: true, maxRecoveryTier: 3, appendRecoveryEvent: (e) => events.push(e) },
  );

  assert.ok(resp.content.some(c => c.type === "image"), "ceiling 3 gets the screenshots too");
  assert.ok(events.some(e => e.event === "agent_recovery_requested" && e.tier === 4 && e.round === 1));
});

test("ceiling 2 is untouched — deterministic failure, no agent handoff, no screenshots", async () => {
  const page = mockPage({ inventory: [{ tag: "button", text: "Go" }] });
  const resp = await buildFailureResponse(
    page, { message: "not found", failedAt: 0 }, entry("ceiling2"), { emit() {} }, null,
    { ...BASE_DEPS, agentRecoveryEnabled: false, maxRecoveryTier: 2 },
  );
  const texts = resp.content.map(c => c.text || "").join("\n");
  assert.ok(!resp.content.some(c => c.type === "image"));
  assert.ok(!/candidate_index/.test(texts), "no agent recovery protocol under the Studio ceiling");
  assert.match(texts, /deterministic cascade only/);
});

test("stagnation hard cap: identical page across repeat rounds of one tier stops escalation", async () => {
  const page = mockPage(); // fingerprint identical on every call
  const ent = entry("stagnant");
  const commonDeps = { ...BASE_DEPS, agentRecoveryEnabled: true, maxRecoveryTier: 4 };
  const events = [];

  // Round 1 → T3 sent. Round 2 → T4 sent (tier change adds information even on an identical page).
  await buildFailureResponse(page, { message: "fail", failedAt: 7 }, ent, { emit() {} }, null, commonDeps);
  await buildFailureResponse(page, { message: "fail", failedAt: 7 }, ent, { emit() {} }, null, commonDeps);
  const resp = await buildFailureResponse(
    page, { message: "fail", failedAt: 7 }, ent, { emit() {} }, null,
    { ...commonDeps, appendRecoveryEvent: (e) => events.push(e) }
  );
  const texts = resp.content.map(c => c.text || "").join("\n");
  assert.match(texts, /Recovery stopped/);
  assert.match(texts, /page has not changed/);
  assert.ok(!/candidate_index/.test(texts), "no further paid recovery protocol after the cap");
  assert.ok(events.some(e => e.event === "recovery_stagnant_stop"));
});

test("different steps escalate independently (a healed step resets nothing for others)", async () => {
  const page = mockPage({ inventory: [{ tag: "button", text: "Go" }] });
  const ent = entry("per-step");
  const commonDeps = { ...BASE_DEPS, agentRecoveryEnabled: true, maxRecoveryTier: 4 };

  await buildFailureResponse(page, { message: "fail", failedAt: 0 }, ent, { emit() {} }, null, commonDeps);
  const respOtherStep = await buildFailureResponse(
    page, { message: "fail", failedAt: 3 }, ent, { emit() {} }, null, commonDeps
  );
  const texts = respOtherStep.content.map(c => c.text || "").join("\n");
  assert.match(texts, /Self-healing recovery\. The deterministic cascade could not resolve/);
  assert.match(texts, /resume_from: 3/, "the fresh step's own index, not the one already escalating");
});

// ── Reference image: describe only what is actually attached ────────────────────────────────
// `visuals/` does not reach a customer machine today (the delta sync ships five JSON files), so
// the vision header used to describe a recording-time reference image that was never in the
// payload. Telling the model to compare against a missing attachment invites it to invent what
// the image showed — in the one tier that decides where to click.

async function visionRound(ent, failedAt) {
  const page = mockPage({ inventory: [{ tag: "button", text: "Submit" }] });
  const commonDeps = { ...BASE_DEPS, agentRecoveryEnabled: true, maxRecoveryTier: 4 };
  // First round is semantic; the second escalates to vision.
  await buildFailureResponse(page, { message: "fail", failedAt }, ent, { emit() {} }, null, commonDeps);
  return buildFailureResponse(page, { message: "still failing", failedAt }, ent, { emit() {} }, null, commonDeps);
}

test("vision round: no reference image on disk — header must not describe one", async () => {
  const resp = await visionRound(entry("ref-absent"), 2);
  const texts = resp.content.map(c => c.text || "").join("\n");

  assert.match(texts, /Self-healing recovery\. The deterministic cascade could not resolve/);
  assert.ok(!/recording-time reference image only shows/.test(texts),
    "must not describe a reference image that is not attached");
  assert.match(texts, /No recording-time reference image is available/,
    "says plainly that there is no reference image");
  assert.match(texts, /do not\s+assume how the target used to look/,
    "tells the model not to invent the missing before-picture");
  assert.ok(!resp.content.some(c => c.type === "image" && /reference/i.test(c.text || "")),
    "no reference image part in the payload");
});

test("vision round: reference image present — header describes it and it rides along", async () => {
  const ent = entry("ref-present");
  // failedAt 2 → step number 3 → visuals/Image_3.jpg
  fs.mkdirSync(path.join(ent.skillDir, "visuals"), { recursive: true });
  fs.writeFileSync(path.join(ent.skillDir, "visuals", "Image_3.jpg"), Buffer.from("jpegbytes"));

  const resp = await visionRound(ent, 2);
  const texts = resp.content.map(c => c.text || "").join("\n");

  assert.match(texts, /recording-time reference image only shows/,
    "describes the reference image when it is genuinely attached");
  assert.ok(!/No recording-time reference image is available/.test(texts));
  assert.ok(resp.content.some(c => c.type === "text" && /Reference image of the target from recording/.test(c.text || "")),
    "reference image is labelled in the payload");
  assert.ok(resp.content.filter(c => c.type === "image").length >= 2,
    "reference image rides along with the live screenshots");
});

// ── Recorded page structure ─────────────────────────────────────────────────────────────────
// Everything else in the payload describes the page as it is NOW, which answers "what is here?"
// but not "what changed" — and drift is a change. The recorded neighbourhood is the other half of
// that comparison, compiled at build time from signals the recorder already captured.

test("recordedContextBlock: renders the recorded neighbourhood and warns it may be stale", () => {
  const block = recordedContextBlock({
    failedStep: {
      _recorded_context: {
        parent: "div#toolbar[role=toolbar]",
        siblings: ["button#export:Export", "button#print:Print"],
        index_in_parent: 3,
        form_context: "form#report",
      },
    },
  });

  assert.match(block, /AT RECORDING TIME/);
  assert.match(block, /div#toolbar/);
  assert.match(block, /button#export:Export/);
  assert.match(block, /do not assume it still holds/,
    "the recorded structure is evidence of the past, never a claim about the present");
});

test("recordedContextBlock: null when the pack predates recorded context", () => {
  assert.strictEqual(recordedContextBlock({ failedStep: {} }), null);
  assert.strictEqual(recordedContextBlock({ failedStep: { _recorded_context: {} } }), null,
    "an empty object must not produce a meaningless block");
  assert.strictEqual(recordedContextBlock({}), null);
});

// anchorSentenceBlock: the prose description a vision LLM wrote at compile time from 5
// time-offset frames around the recorded action — a plain-English caption alongside
// recordedContextBlock's structural "where it sat", null-safe the same way.

test("anchorSentenceBlock: renders the recorded description", () => {
  const block = anchorSentenceBlock({
    failedStep: { _anchor_sentence: "The blue Sign in button below the password field" },
  });
  assert.match(block, /described when recorded/);
  assert.match(block, /The blue Sign in button below the password field/);
});

test("anchorSentenceBlock: null when the pack predates the anchor sentence", () => {
  assert.strictEqual(anchorSentenceBlock({ failedStep: {} }), null);
  assert.strictEqual(anchorSentenceBlock({ failedStep: { _anchor_sentence: "" } }), null);
  assert.strictEqual(anchorSentenceBlock({ failedStep: { _anchor_sentence: "   " } }), null,
    "whitespace-only must not produce a meaningless block");
  assert.strictEqual(anchorSentenceBlock({}), null);
});

// phaseHintBlock (BUILD-25 stage e): the compiler's second-opinion pass's label_phase
// finding, given to the agent tier as a workflow-position prior.

test("phaseHintBlock: renders the labeled phase", () => {
  const block = phaseHintBlock({ failedStep: { _phase: "login" } });
  assert.match(block, /workflow's "login" phase/);
});

test("phaseHintBlock: null when the step was never labeled", () => {
  assert.strictEqual(phaseHintBlock({ failedStep: {} }), null);
  assert.strictEqual(phaseHintBlock({ failedStep: { _phase: "" } }), null);
  assert.strictEqual(phaseHintBlock({ failedStep: { _phase: "   " } }), null,
    "whitespace-only must not produce a meaningless block");
  assert.strictEqual(phaseHintBlock({}), null);
});

test("the agent payload carries the recorded structure beside the live digest", async () => {
  const page = mockPage({ inventory: [{ tag: "button", text: "Export" }] });
  const resp = await buildFailureResponse(
    page,
    {
      message: "not found",
      failedAt: 0,
      failedStep: {
        type: "click",
        identity_bundle: { fingerprint: { role: "button", inner_text: "Export" } },
        _recorded_context: { parent: "div#toolbar[role=toolbar]", index_in_parent: 3 },
      },
    },
    entry("recorded-ctx"), { emit() {} }, null,
    { ...BASE_DEPS, agentRecoveryEnabled: true, maxRecoveryTier: 4 },
  );

  const texts = resp.content.map(c => c.text || "").join("\n");
  assert.match(texts, /AT RECORDING TIME/);
  assert.match(texts, /div#toolbar/);
  assert.match(texts, /Interactive elements NOW/, "sits alongside the live list, not instead of it");
});
