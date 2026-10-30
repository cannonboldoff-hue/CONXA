"use strict";

// EXEC-13: the ai_review step type — a planned pause (author-placed reasoning checkpoint), not
// a page action and not a Tier 1-4 recovery candidate. Two things are under test here:
//   • review_pause.js — the pure/mockable answer-validation + MCP request-building helpers,
//     following the same scope as test_failure_response.js's coverage of buildFailureResponse.
//   • run.js's runPlan interception — a fresh arrival throws a distinct `reviewPause` signal
//     instead of reaching executeStep/recoverStep; a resume that already bound an answer under
//     `__ai_review_answer_<i>` consumes it and continues, following the same runPlan-fixture
//     pattern as test_download_naming.js.

const test   = require("node:test");
const assert = require("node:assert");
const fs     = require("fs");
const os     = require("os");
const path   = require("path");

const {
  REVIEW_RETRY_MAX,
  checkReviewRetry,
  clearReviewRetry,
  validateReviewAnswer,
  buildReviewRequest,
  readAttachments,
} = require("../../app/review_pause");
const { runPlan, collectOutputs, runIfDecision } = require("../../app/run");

// ── validateReviewAnswer ─────────────────────────────────────────────────────

test("validateReviewAnswer: no schema always passes", () => {
  assert.deepStrictEqual(validateReviewAnswer({ anything: "goes" }, null), { valid: true, errors: [] });
  assert.deepStrictEqual(validateReviewAnswer("not even an object", undefined), { valid: true, errors: [] });
});

test("validateReviewAnswer: non-object answer fails when a schema is present", () => {
  const result = validateReviewAnswer("yes", { type: "object", properties: {} });
  assert.strictEqual(result.valid, false);
  assert.match(result.errors[0], /must be a JSON object/);
});

test("validateReviewAnswer: missing required field fails", () => {
  const schema = { type: "object", required: ["visible"], properties: { visible: { type: "boolean" } } };
  const result = validateReviewAnswer({ why: "no banner" }, schema);
  assert.strictEqual(result.valid, false);
  assert.match(result.errors[0], /missing required field "visible"/);
});

test("validateReviewAnswer: wrong type on a declared property fails", () => {
  const schema = { properties: { visible: { type: "boolean" } } };
  const result = validateReviewAnswer({ visible: "yes" }, schema);
  assert.strictEqual(result.valid, false);
  assert.match(result.errors[0], /must be of type boolean/);
});

test("validateReviewAnswer: enum mismatch fails", () => {
  const schema = { properties: { status: { type: "string", enum: ["ok", "error"] } } };
  const result = validateReviewAnswer({ status: "maybe" }, schema);
  assert.strictEqual(result.valid, false);
  assert.match(result.errors[0], /must be one of/);
});

test("validateReviewAnswer: matching answer against the canonical yes/no shape passes", () => {
  const schema = {
    type: "object",
    required: ["visible"],
    properties: { visible: { type: "boolean" }, why: { type: "string" } },
  };
  const result = validateReviewAnswer({ visible: true, why: "banner is showing" }, schema);
  assert.deepStrictEqual(result, { valid: true, errors: [] });
});

// ── checkReviewRetry / clearReviewRetry ──────────────────────────────────────

test("checkReviewRetry: allows up to REVIEW_RETRY_MAX attempts, then refuses", () => {
  const slug = "retry-test";
  for (let i = 0; i < REVIEW_RETRY_MAX; i++) {
    assert.strictEqual(checkReviewRetry(slug, 3), true);
  }
  assert.strictEqual(checkReviewRetry(slug, 3), false, "budget should be exhausted past REVIEW_RETRY_MAX");
});

test("checkReviewRetry: clearReviewRetry resets the counter for that step only", () => {
  const slug = "retry-clear-test";
  checkReviewRetry(slug, 0);
  checkReviewRetry(slug, 1);
  clearReviewRetry(slug, 0);
  assert.strictEqual(checkReviewRetry(slug, 0), true, "cleared step should look like a fresh start");
  // Step 1's count is untouched by clearing step 0.
  for (let i = 1; i < REVIEW_RETRY_MAX; i++) checkReviewRetry(slug, 1);
  assert.strictEqual(checkReviewRetry(slug, 1), false);
});

// ── buildReviewRequest ───────────────────────────────────────────────────────

let tmpDirs = [];
function tmpSkillDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-review-"));
  tmpDirs.push(dir);
  return dir;
}
test.after?.(() => {
  for (const dir of tmpDirs) fs.rmSync(dir, { recursive: true, force: true });
});

function mockPage() {
  return {
    url: () => "https://x.example/checkout",
    screenshot: async () => Buffer.from("shot"),
  };
}

// evaluate() is what evalOn()/domInventory ride on — absent on the plain mockPage() above
// (evalOn's synchronous TypeError is swallowed by buildReviewRequest's own .catch(() => null),
// so those tests never see a DOM block), present here to exercise it directly.
function mockPageWithDom(domResult) {
  return {
    ...mockPage(),
    evaluate: async () => domResult,
  };
}

test("buildReviewRequest: includes the prompt and resume instructions, no schema/reference", async () => {
  const step = { type: "ai_review", prompt: "Is the confirmation banner visible?" };
  const resp = await buildReviewRequest(mockPage(), step, 4, { slug: "s1", skillDir: tmpSkillDir() });
  const header = resp.content[0].text;
  assert.match(header, /paused at step 5/);
  assert.match(header, /Is the confirmation banner visible\?/);
  assert.match(header, /resume_from: 4/);
  assert.match(header, /review_results: \{ "4":/);
  // Current screenshot always included; no reference image without a reference_screenshot_ref.
  assert.strictEqual(resp.content.filter(c => c.type === "image").length, 1);
});

test("buildReviewRequest: includes the output schema and a matching reference image", async () => {
  const skillDir = tmpSkillDir();
  fs.mkdirSync(path.join(skillDir, "visuals"));
  fs.writeFileSync(path.join(skillDir, "visuals", "Image_5.jpg"), Buffer.from("ref"));
  const step = {
    type: "ai_review",
    prompt: "Is the banner visible?",
    output_schema: { type: "object", required: ["visible"] },
    reference_screenshot_ref: "visuals/Image_5.jpg",
  };
  const resp = await buildReviewRequest(mockPage(), step, 4, { slug: "s1", skillDir });
  assert.match(resp.content[0].text, /must be a JSON object matching this schema/);
  const images = resp.content.filter(c => c.type === "image");
  assert.strictEqual(images.length, 2, "reference image + current screenshot");
});

test("buildReviewRequest: appends validation-error and drift notes on a re-ask", async () => {
  const step = { type: "ai_review", prompt: "Is the banner visible?" };
  const resp = await buildReviewRequest(mockPage(), step, 0, { slug: "s1", skillDir: tmpSkillDir() }, {
    validationErrors: ['missing required field "visible"'],
    pageDrifted: true,
  });
  const header = resp.content[0].text;
  assert.match(header, /did not match the required output/);
  assert.match(header, /missing required field "visible"/);
  assert.match(header, /the page changed while this was pending/);
});

test("buildReviewRequest: includes a live DOM inventory block when the page yields one", async () => {
  const step = { type: "ai_review", prompt: "Is the banner visible?" };
  const domRows = [{ tag: "button", text: "Confirm" }];
  const resp = await buildReviewRequest(mockPageWithDom(domRows), step, 0, { slug: "s1", skillDir: tmpSkillDir() });
  const domBlock = resp.content.find((c) => c.type === "text" && /live DOM/.test(c.text));
  assert.ok(domBlock, "expected a live-DOM text block");
  assert.match(domBlock.text, /Confirm/);
});

test("buildReviewRequest: omits the DOM block when the page eval fails or returns nothing", async () => {
  const step = { type: "ai_review", prompt: "Is the banner visible?" };
  // mockPage() has no evaluate() at all — evalOn's synchronous TypeError is swallowed.
  const resp = await buildReviewRequest(mockPage(), step, 0, { slug: "s1", skillDir: tmpSkillDir() });
  assert.strictEqual(resp.content.some((c) => c.type === "text" && /live DOM/.test(c.text)), false);
});

test("buildReviewRequest: _meta carries the step index, prompt, and output schema for a non-agent caller", async () => {
  const outputSchema = { type: "object", required: ["visible"] };
  const step = { type: "ai_review", prompt: "Is the banner visible?", output_schema: outputSchema };
  const resp = await buildReviewRequest(mockPage(), step, 2, { slug: "s1", skillDir: tmpSkillDir() });
  assert.deepStrictEqual(resp._meta["conxa/ai_review"], {
    step_index: 2,
    prompt: "Is the banner visible?",
    output_schema: outputSchema,
  });
});

// ── run.js's runPlan interception ────────────────────────────────────────────
// Minimal page/tab fixture, same shape test_download_naming.js uses: an ai_review step has no
// `tab` block, so resolveStepPage takes the tab_0 shortcut and never touches Playwright.
const idleContext = { on: () => {} };
const idlePage = { waitForLoadState: async () => {}, context: () => idleContext };

test("runPlan: a fresh ai_review step throws a reviewPause signal, not a normal failure", async () => {
  const steps = [{ type: "ai_review", prompt: "Is X visible?", output_name: "x_visible" }];
  await assert.rejects(
    () => runPlan(idlePage, steps, {}, 0, "review-fresh"),
    (err) => {
      assert.strictEqual(err.reviewPause, true);
      assert.strictEqual(err.stepIndex, 0);
      assert.strictEqual(err.step, steps[0]);
      assert.strictEqual(err.page, idlePage);
      return true;
    }
  );
});

test("runPlan: a resume with a bound answer consumes it and continues past the step", async () => {
  const steps = [
    { type: "ai_review", prompt: "Is X visible?", output_name: "x_visible" },
    { type: "ai_review", prompt: "Never reached", output_name: "unreached" },
  ];
  const inputs = { __ai_review_answer_0: { visible: true, why: "it's there" } };
  // Step 1 has no bound answer, so it re-throws its own pause — proves step 0 was actually
  // consumed and execution moved on, rather than looping on step 0 forever.
  await assert.rejects(
    () => runPlan(idlePage, steps, inputs, 0, "review-resume"),
    (err) => err.reviewPause === true && err.stepIndex === 1
  );
  assert.deepStrictEqual(inputs.x_visible, { visible: true, why: "it's there" });
  assert.strictEqual(inputs.__ai_review_answer_0, undefined, "consumed answer key is removed");
});

test("runPlan: default output_name is used when the step carries none", async () => {
  const steps = [{ type: "ai_review", prompt: "Is X visible?" }];
  const inputs = { __ai_review_answer_0: "yes" };
  // Only step, answer already bound — the loop consumes it and finishes normally (no throw).
  await runPlan(idlePage, steps, inputs, 0, "review-default-name");
  assert.strictEqual(inputs.ai_review_output_0, "yes");
  assert.strictEqual(inputs.__ai_review_answer_0, undefined);
});

// ── answers a later step can use, and values the reviewer is handed ───────────────────────────

test("runPlan: each plain field of an answer is bound as <output_name>_<field>, so run_if can read it", async () => {
  const steps = [{ type: "ai_review", prompt: "Pick one", output_name: "verdict" }];
  const inputs = { __ai_review_answer_0: { choice: "refund", amount: 12, urgent: true, nested: { a: 1 }, "bad-key": "x" } };
  await runPlan(idlePage, steps, inputs, 0, "review-flatten");
  assert.strictEqual(inputs.verdict_choice, "refund");
  assert.strictEqual(inputs.verdict_amount, 12);
  assert.strictEqual(inputs.verdict_urgent, true);
  assert.strictEqual(inputs.verdict_nested, undefined, "objects are not flattened");
  assert.deepStrictEqual(inputs.verdict, inputs.verdict, "the whole answer stays bound too");
  const gate = runIfDecision({ run_if: { input: "verdict_choice", op: "equals", value: "refund" } }, inputs);
  assert.strictEqual(gate.skip, false);
  const other = runIfDecision({ run_if: { input: "verdict_choice", op: "equals", value: "keep" } }, inputs);
  assert.strictEqual(other.skip, true);
});

test("collectOutputs: a named ai_review answer is reported like an extract", () => {
  const steps = [{ type: "ai_review", prompt: "p", output_name: "verdict" }, { type: "ai_review", prompt: "unnamed" }];
  assert.deepStrictEqual(collectOutputs(steps, { verdict: { a: 1 }, ai_review_output_1: "x" }), { verdict: { a: 1 } });
});

test("buildReviewRequest: {{inputs}} in the prompt are filled in", async () => {
  const step = { type: "ai_review", prompt: "Is {{customer}} a duplicate?" };
  const resp = await buildReviewRequest(mockPage(), step, 0, { slug: "s", skillDir: tmpSkillDir() }, { inputs: { customer: "Acme" } });
  assert.match(resp.content[0].text, /Is Acme a duplicate\?/);
  assert.strictEqual(resp._meta["conxa/ai_review"].prompt, "Is Acme a duplicate?");
});

test("readAttachments: only a text file inside this run's download folder is read", () => {
  const dir = tmpSkillDir();
  const outside = tmpSkillDir();
  const csv = path.join(dir, "report.csv");
  fs.writeFileSync(csv, "a,b\n1,2\n");
  const secret = path.join(outside, "secret.txt");
  fs.writeFileSync(secret, "do not read");
  const pdf = path.join(dir, "invoice.pdf");
  fs.writeFileSync(pdf, "%PDF");
  const inputs = { downloaded_files_dir: dir, downloaded_file: csv, other: secret, doc: pdf };
  const [ok, out, bin, none] = readAttachments({ attach: ["downloaded_file", "other", "doc", "missing"] }, inputs);
  assert.strictEqual(ok.text, "a,b\n1,2\n");
  assert.strictEqual(out.text, "");
  assert.match(out.note, /outside this run's download folder/);
  assert.strictEqual(bin.text, "");
  assert.match(bin.note, /text files only/);
  assert.match(none.note, /no file was available/);
});

test("readAttachments: a file with no download folder known is never read", () => {
  const dir = tmpSkillDir();
  const csv = path.join(dir, "report.csv");
  fs.writeFileSync(csv, "a");
  const [item] = readAttachments({ attach: ["f"] }, { f: csv });
  assert.strictEqual(item.text, "");
});

test("readAttachments: a large file is cut and says so", () => {
  const dir = tmpSkillDir();
  const big = path.join(dir, "big.txt");
  fs.writeFileSync(big, "x".repeat(70 * 1024));
  const [item] = readAttachments({ attach: ["f"] }, { downloaded_files_dir: dir, f: big });
  assert.strictEqual(item.text.length, 64 * 1024);
  assert.match(item.note, /only the first/);
});

test("buildReviewRequest: attached file text is framed as untrusted data and mirrored in _meta", async () => {
  const dir = tmpSkillDir();
  const csv = path.join(dir, "report.csv");
  fs.writeFileSync(csv, "total,42\n");
  const step = { type: "ai_review", prompt: "What is the total?", attach: ["downloaded_file"] };
  const resp = await buildReviewRequest(mockPage(), step, 0, { slug: "s", skillDir: tmpSkillDir() },
    { inputs: { downloaded_files_dir: dir, downloaded_file: csv } });
  const text = resp.content.map((c) => c.text || "").join("\n");
  assert.match(text, /BEGIN FILE DATA/);
  assert.match(text, /total,42/);
  assert.match(text, /untrusted data/);
  assert.strictEqual(resp._meta["conxa/ai_review"].attachments[0].text, "total,42\n");
});
