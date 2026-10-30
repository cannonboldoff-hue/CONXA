"use strict";
/**
 * call_workflow.js — loading and checking the workflows a `call_workflow` step runs.
 *
 * A call_workflow step runs ANOTHER installed workflow's steps in the middle of this one:
 *   { type: "call_workflow", call: { skill, version, inputs: {callee_input: "{{value}}"}, outputs: {callee_output: "caller_name"} } }
 *
 * Everything that can be decided before the browser opens is decided here, once, at run start
 * (server.js), so a bad call is a clear refusal and never a surprise halfway through:
 *   - the callee is installed, passes its integrity check, and is the exact version this workflow was built
 *     against (`call.version`, pinned when the caller was built) -- a callee updated on its own never
 *     silently changes what the caller does;
 *   - no cycle (A calls B calls A) and no more than MAX_CALL_DEPTH calls deep;
 *   - the callee holds no pause (ai_review, handover, a popup decision): a pause inside a call would
 *     resume at the wrong step, so the same rule as a handover inside a loop body applies;
 *   - the inputs handed over match what the callee declares, and the outputs asked for exist.
 *
 * Pure: skills are found through injected `deps`, so this is unit-testable without booting the server.
 */

const MAX_CALL_DEPTH = 3;
// Steps that stop the run and wait for an answer. They resume by top-level step index, which a step
// inside a called workflow does not have.
const PAUSE_STEP_TYPES = new Set(["ai_review", "handover", "outcome_fork"]);

function walk(steps, visit) {
  for (const s of Array.isArray(steps) ? steps : []) {
    if (!s || typeof s !== "object") continue;
    visit(s);
    walk(s.steps, visit);
    if (s.for_each) walk(s.for_each.steps, visit);
    if (s.branch) {
      walk(s.branch.steps, visit);
      for (const o of Array.isArray(s.branch.options) ? s.branch.options : []) walk(o && o.steps, visit);
    }
    for (const o of Array.isArray(s.options) ? s.options : []) walk(o && o.steps, visit);
  }
}

function callSteps(steps) {
  const out = [];
  walk(steps, (s) => { if (s.type === "call_workflow") out.push(s); });
  return out;
}

function firstPauseType(steps) {
  let found = null;
  walk(steps, (s) => { if (!found && PAUSE_STEP_TYPES.has(s.type)) found = s.type; });
  return found;
}

const PAUSE_NAMES = { ai_review: "an AI review", handover: "a hand-over to a person", outcome_fork: "a popup decision" };

/**
 * @param {object} root  { entry, steps } -- the workflow being run
 * @param {object} deps  { resolveSkill(slug, workspaceId), ensureIntegrity(entry), loadSteps(entry),
 *                         declaredInputs(entry), declaredOutputNames(steps) }
 * @returns {{callees: object, entries: object[], error: string|null}}
 */
function loadCallees(root, deps) {
  const callees = {};
  const entries = [];
  const fail = (message) => ({ callees: {}, entries: [], error: message });

  const visit = (owner, steps, stack) => {
    for (const s of callSteps(steps)) {
      const call = s.call && typeof s.call === "object" ? s.call : {};
      const slug = String(call.skill || "").trim();
      if (!slug) return `A "call another workflow" step in ${owner} does not say which workflow to call.`;
      if (stack.includes(slug)) return `Workflows call each other in a circle (${[...stack, slug].join(" -> ")}).`;
      if (stack.length > MAX_CALL_DEPTH) return `Workflows call each other more than ${MAX_CALL_DEPTH} levels deep (${[...stack, slug].join(" -> ")}).`;

      let info = callees[slug];
      if (!info) {
        let entry = deps.resolveSkill(slug, root.entry.workspace_id || null);
        if (!entry) return `${owner} calls the workflow "${slug}", which is not installed here.`;
        try { entry = deps.ensureIntegrity(entry); } catch (e) { return `The workflow "${slug}" failed its integrity check: ${e.message}`; }
        const installed = String((entry.manifest && entry.manifest.version) || "");
        if (!call.version) return `${owner} calls "${slug}" without a pinned version. Rebuild ${owner} so it records the version it was tested with.`;
        if (String(call.version) !== installed) {
          return `${owner} was built to call version ${call.version} of "${slug}", but version ${installed || "(unknown)"} is installed. Rebuild ${owner} and republish both together.`;
        }
        let calleeSteps;
        try { calleeSteps = deps.loadSteps(entry); } catch (e) { return `The workflow "${slug}" could not be read: ${e.message}`; }
        const pause = firstPauseType(calleeSteps);
        if (pause) return `The workflow "${slug}" contains ${PAUSE_NAMES[pause]}, which cannot run inside another workflow. Run it on its own instead.`;
        info = {
          slug: entry.slug, version: installed, steps: calleeSteps, entry,
          declaredInputs: deps.declaredInputs(entry), outputNames: new Set(deps.declaredOutputNames(calleeSteps)),
        };
        callees[slug] = info;
        entries.push(entry);
        const inner = visit(slug, calleeSteps, [...stack, slug]);
        if (inner) return inner;
      }

      const declared = new Set(info.declaredInputs.map((f) => f && f.name).filter(Boolean));
      const mapped = call.inputs && typeof call.inputs === "object" ? call.inputs : {};
      for (const name of Object.keys(mapped)) {
        if (!declared.has(name)) return `${owner} hands "${slug}" an input called "${name}", which "${slug}" does not take.`;
      }
      const required = ((info.entry.manifest && info.entry.manifest.inputs_required) || [])
        .filter((n) => !(n in mapped) && !info.declaredInputs.some((f) => f && f.name === n && f.default !== undefined && f.default !== null && f.default !== ""));
      if (required.length) return `${owner} does not give "${slug}" the input(s) it needs: ${required.join(", ")}.`;
      const asked = call.outputs && typeof call.outputs === "object" ? call.outputs : {};
      for (const name of Object.keys(asked)) {
        if (!info.outputNames.has(name)) return `${owner} asks "${slug}" for a result called "${name}", which "${slug}" does not produce.`;
      }
    }
    return null;
  };

  const owner = root.entry.slug;
  const error = visit(owner, root.steps, [owner]);
  return error ? fail(error) : { callees, entries, error: null };
}

module.exports = { MAX_CALL_DEPTH, PAUSE_STEP_TYPES, walk, callSteps, firstPauseType, loadCallees };
