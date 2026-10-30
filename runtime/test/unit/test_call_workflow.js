"use strict";

// call_workflow: run another installed workflow's steps in the middle of this one. Two layers under
// test -- the checks made before a run starts (call_workflow.js::loadCallees) and the step that runs the
// callee (run.js::runCallWorkflowStep, driven through runPlan).

const test = require("node:test");
const assert = require("node:assert");

const { loadCallees, callSteps, firstPauseType, MAX_CALL_DEPTH } = require("../../app/call_workflow");
const { runPlan, declaredOutputNames } = require("../../app/run");

// ── loadCallees ──────────────────────────────────────────────────────────────

function world(skills) {
  // skills: { slug: { version, steps, inputs: [{name, default?}], required: [names] } }
  const entry = (slug) => ({
    slug, workspace_id: "ws", skillDir: `/skills/${slug}`,
    manifest: { version: skills[slug].version, inputs_required: skills[slug].required || [] },
  });
  return {
    entry,
    deps: {
      resolveSkill: (slug) => (skills[slug] ? entry(slug) : null),
      ensureIntegrity: (e) => e,
      loadSteps: (e) => skills[e.slug].steps,
      declaredInputs: (e) => skills[e.slug].inputs || [],
      declaredOutputNames,
    },
  };
}

const call = (skill, extra = {}) => ({ type: "call_workflow", call: { skill, version: "1.0.0", ...extra } });

test("loadCallees: a pinned, installed callee loads, with its entry for the lock and policy", () => {
  const w = world({ b: { version: "1.0.0", steps: [{ type: "extract", output_name: "total" }], inputs: [{ name: "id" }], required: ["id"] } });
  const root = { entry: { slug: "a", workspace_id: "ws" }, steps: [call("b", { inputs: { id: "{{x}}" }, outputs: { total: "t" } })] };
  const r = loadCallees(root, w.deps);
  assert.strictEqual(r.error, null);
  assert.deepStrictEqual(Object.keys(r.callees), ["b"]);
  assert.strictEqual(r.entries[0].slug, "b");
});

test("loadCallees: refuses, in plain words, everything that could go wrong", () => {
  const ok = { version: "1.0.0", steps: [{ type: "click" }], inputs: [{ name: "id" }], required: [] };
  const cases = [
    ["not installed", {}, [call("zzz")], /not installed/],
    ["no workflow named", { b: ok }, [{ type: "call_workflow", call: {} }], /does not say which workflow/],
    ["not pinned", { b: ok }, [{ type: "call_workflow", call: { skill: "b" } }], /without a pinned version/],
    ["version moved on", { b: { ...ok, version: "2.0.0" } }, [call("b")], /built to call version 1.0.0 of "b", but version 2.0.0/],
    ["unknown input", { b: ok }, [call("b", { inputs: { nope: "1" } })], /does not take/],
    ["missing required input", { b: { ...ok, required: ["id"] } }, [call("b")], /does not give "b" the input\(s\) it needs: id/],
    ["unknown output", { b: ok }, [call("b", { outputs: { total: "t" } })], /does not produce/],
    ["pause inside", { b: { ...ok, steps: [{ type: "handover" }] } }, [call("b")], /hand-over to a person, which cannot run inside/],
    ["ai_review inside", { b: { ...ok, steps: [{ type: "for_each", steps: [{ type: "ai_review" }] }] } }, [call("b")], /AI review/],
  ];
  for (const [name, skills, steps, pattern] of cases) {
    const w = world(skills);
    const r = loadCallees({ entry: { slug: "a", workspace_id: "ws" }, steps }, w.deps);
    assert.match(r.error || "", pattern, name);
    assert.deepStrictEqual(r.callees, {}, name);
  }
});

test("loadCallees: a default satisfies a required input", () => {
  const w = world({ b: { version: "1.0.0", steps: [{ type: "click" }], inputs: [{ name: "id", default: "7" }], required: ["id"] } });
  assert.strictEqual(loadCallees({ entry: { slug: "a", workspace_id: "ws" }, steps: [call("b")] }, w.deps).error, null);
});

test("loadCallees: a circle, and a chain that goes too deep, are refused", () => {
  const circle = world({
    b: { version: "1.0.0", steps: [call("c")] },
    c: { version: "1.0.0", steps: [call("a")] },
    a: { version: "1.0.0", steps: [] },
  });
  assert.match(loadCallees({ entry: { slug: "a", workspace_id: "ws" }, steps: [call("b")] }, circle.deps).error, /in a circle \(a -> b -> c -> a\)/);
  const self = world({ a: { version: "1.0.0", steps: [] } });
  assert.match(loadCallees({ entry: { slug: "a", workspace_id: "ws" }, steps: [call("a")] }, self.deps).error, /in a circle/);

  const chain = {};
  const names = ["s1", "s2", "s3", "s4", "s5"];
  names.forEach((n, i) => { chain[n] = { version: "1.0.0", steps: i + 1 < names.length ? [call(names[i + 1])] : [] }; });
  const deep = world(chain);
  assert.match(loadCallees({ entry: { slug: "root", workspace_id: "ws" }, steps: [call("s1")] }, deep.deps).error, new RegExp(`more than ${MAX_CALL_DEPTH} levels`));
  const shallow = world({ s1: chain.s1, s2: chain.s2, s3: { version: "1.0.0", steps: [] } });
  assert.strictEqual(loadCallees({ entry: { slug: "root", workspace_id: "ws" }, steps: [call("s1")] }, shallow.deps).error, null);
});

test("callSteps finds a call nested in a loop or branch; firstPauseType finds a pause anywhere", () => {
  const steps = [{ type: "for_each", steps: [{ type: "if_present", branch: { steps: [call("b")] } }] }];
  assert.strictEqual(callSteps(steps).length, 1);
  assert.strictEqual(firstPauseType([{ type: "wait_for_one_of", options: [{ steps: [{ type: "outcome_fork" }] }] }]), "outcome_fork");
  assert.strictEqual(firstPauseType([{ type: "click" }]), null);
});

// ── running a call ───────────────────────────────────────────────────────────

const idleContext = { on: () => {}, grantPermissions: async () => {} };
function clipboardPage() {
  let clip = "";
  return {
    waitForLoadState: async () => {},
    context: () => idleContext,
    url: () => "https://x.test/p",
    evaluate: async (fn, arg) => {
      if (String(fn).includes("writeText")) { clip = arg; return undefined; }
      return clip;
    },
  };
}

function calleeInfo(steps, extra = {}) {
  return {
    slug: "b", version: "1.0.0", steps, declaredInputs: [{ name: "who" }], outputNames: new Set(["got"]),
    entry: { slug: "b", manifest: { inputs_required: ["who"] } }, ...extra,
  };
}

const greet = [
  { type: "clipboard_write", value: "Hi {{who}}" },
  { type: "clipboard_read", output_name: "got" },
];
const callB = (extra = {}) => ({ type: "call_workflow", call: { skill: "b", version: "1.0.0", inputs: { who: "{{name}}" }, outputs: { got: "greeting" }, ...extra } });

test("runPlan: a call hands over only what it is given, returns only what it is asked for, and restores the caller's values", async () => {
  const inputs = { name: "Ann", secret: "keep-me" };
  const seen = [];
  const spy = { type: "clipboard_write", value: "{{secret}}|{{who}}" };
  const result = await runPlan(clipboardPage(), [callB()], inputs, 0, "a", {
    callees: { b: calleeInfo([spy, ...greet]) },
    tracker: { emit: (code, data) => seen.push(code) },
  });
  assert.strictEqual(inputs.greeting, "Hi Ann", "the asked-for result comes back under the caller's name");
  assert.strictEqual(inputs.name, "Ann");
  assert.strictEqual(inputs.secret, "keep-me");
  assert.strictEqual("who" in inputs, false, "the callee's own input does not leak out");
  assert.strictEqual("got" in inputs, false, "an output not asked for under its own name does not leak out");
  assert.ok(seen.includes("call_start") && seen.includes("call_done"));
  assert.deepStrictEqual(result.warnings, []);
});

test("runPlan: the callee cannot see the caller's other inputs", async () => {
  // {{secret}} is empty inside the callee, so the written text is "|Ann"
  const inputs = { name: "Ann", secret: "keep-me" };
  const page = clipboardPage();
  await runPlan(page, [callB()], inputs, 0, "a", {
    callees: { b: calleeInfo([{ type: "clipboard_write", value: "{{secret}}|{{who}}" }, { type: "clipboard_read", output_name: "got" }]) },
  });
  assert.strictEqual(inputs.greeting, "|Ann");
});

test("runPlan: a failing callee step reports where it failed and offers no step to resume from", async () => {
  const inputs = { name: "Ann", secret: "keep-me" };
  const failing = [{ type: "clipboard_read", output_name: "got" }]; // empty clipboard -> a clear failure
  await assert.rejects(
    () => runPlan(clipboardPage(), [callB()], inputs, 0, "a", { callees: { b: calleeInfo(failing) } }),
    (err) => /In the called workflow "b"/.test(err.message) && err.failedAt === undefined && err.calledWorkflow === "b",
  );
  assert.deepStrictEqual(inputs, { name: "Ann", secret: "keep-me" }, "the caller's values are restored after a failure");
});

test("runPlan: a call to a workflow that was not loaded, or that calls back into the stack, fails clearly", async () => {
  await assert.rejects(() => runPlan(clipboardPage(), [callB()], { name: "x" }, 0, "a", { callees: {} }), /was not loaded for this run/);
  await assert.rejects(() => runPlan(clipboardPage(), [callB()], { name: "x" }, 0, "b", { callees: { b: calleeInfo(greet) } }), /cannot be called from here/);
});

test("runPlan: a call with an empty required input is refused before any callee step runs", async () => {
  let ran = false;
  const page = clipboardPage();
  const evaluate = page.evaluate;
  page.evaluate = async (...a) => { ran = true; return evaluate(...a); };
  await assert.rejects(() => runPlan(page, [callB({ inputs: { who: "{{name}}" } })], { name: "" }, 0, "a", { callees: { b: calleeInfo(greet) } }), /needs who/);
  assert.strictEqual(ran, false);
});

test("runPlan: the callee sees this run's download folder (and nothing else of the caller's)", async () => {
  const inputs = { name: "Ann", downloaded_files_dir: "/runs/1" };
  const calleeSteps = [
    { type: "clipboard_write", value: "{{downloaded_files_dir}}" },
    { type: "clipboard_read", output_name: "got" },
  ];
  await runPlan(clipboardPage(), [callB()], inputs, 0, "a", { callees: { b: calleeInfo(calleeSteps) } });
  assert.strictEqual(inputs.greeting, "/runs/1");
  assert.strictEqual(inputs.downloaded_files_dir, "/runs/1");
});
