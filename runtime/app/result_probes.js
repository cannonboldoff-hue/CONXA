"use strict";
/**
 * result_probes.js: the checks of a step's `validation.effect` that need time or a second look (Proof of Done).
 *
 *   url_stays          the step's own page moved to the address the recording landed on and stayed there.
 *   row_with_identity  the value the run entered appears on the page more often than before the action.
 *   value_equals       a field of an earlier step, read on a freshly opened copy of the page, still holds the value.
 *
 * Each answers "yes" | "no" | "cant_tell". They only read: a page address, the page's own text, or one field on a
 * separate tab that is closed again. Each answer must be one a step that did nothing could not have given, so every
 * baseline is taken from before the action, and anything that cannot be told apart from "nothing happened"
 * (the address already matched, a value too short to mean anything, a field that never loaded) is "cant_tell",
 * never "yes". `later_step` is not here: it can only be answered after the later step ran (run.js).
 */
const pageScripts = require("./page_scripts");
const { evalOn, EVAL_TIMED_OUT } = require("./page_eval");
const { interpolate } = require("./interpolate");
const { withFreshTab } = require("./fresh_look");
const { hasBasis } = require("./effect_probes");

const CANT = Object.freeze({ answer: "cant_tell" });
const MIN_VALUE_CHARS = 4; // a shorter value is on almost any page, so finding it proves nothing

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const norm = (s) => String(s ?? "").replace(/\s+/g, " ").trim().toLowerCase();
// A check a proven test run showed to be dead no longer counts (same rule as effect_probes.evaluateChecks).
const isDead = (c) => c.calibrated === false && hasBasis(c);

/** The text a check wants found, filled in from this run's inputs; null when it is unset, unresolved or too short. */
function wantedValue(template, inputs) {
  const v = String(interpolate(String(template || ""), inputs) ?? "");
  return v.length >= MIN_VALUE_CHARS && !v.includes("{{") ? v : null;
}

/** The first of a check's candidate values (`keys`, nearest typed first, else `key`) long enough to mean something. A
 *  form's nearest value can be short ("QA") while another field of the same entry identifies it ("e5@acme.io"). */
function wantedIdentity(probe, inputs) {
  const keys = Array.isArray(probe && probe.keys) && probe.keys.length ? probe.keys : [probe && probe.key];
  for (const k of keys) {
    const v = wantedValue(k, inputs);
    if (v) return v;
  }
  return null;
}

/** "Moved to the landed address and stayed": needs an address that did NOT match before the action. */
async function urlStays(check, nb, { maxMs = 3000, settleMs = 600, pollMs = 200 } = {}) {
  const page = nb && nb.page;
  const before = nb && typeof nb.urlBefore === "function" ? nb.urlBefore() : null;
  let re = null;
  try { re = new RegExp(String((check.probe && check.probe.pattern) || "")); } catch (_) { return CANT; }
  if (!page || !before || !check.probe.pattern) return CANT;
  if (re.test(before)) return CANT; // already on that address: staying there proves nothing
  const now = () => { try { return String(page.url()); } catch (_) { return ""; } };
  const deadline = Date.now() + maxMs;
  for (;;) {
    if (re.test(now())) {
      await sleep(settleMs);
      return re.test(now()) ? { answer: "yes" } : { answer: "no" }; // bounced back to a form or a sign-in
    }
    if (Date.now() >= deadline) return { answer: "no" };
    await sleep(pollMs);
  }
}

/** "The item is on the page one more time than before": `notebook.nameCountBefore` was counted before the action. */
async function rowWithIdentity(check, { notebook, inputs }, { maxMs = 4000, pollMs = 400 } = {}) {
  const want = wantedIdentity(check.probe, inputs);
  const before = notebook && notebook.nameCountBefore;
  if (!want || !notebook || !notebook.page || typeof before !== "number") return CANT;
  const delta = Math.max(1, Number(check.probe.delta) || 1);
  const deadline = Date.now() + maxMs;
  for (;;) {
    const n = await evalOn(notebook.page, pageScripts.countPageText, want, 3000);
    if (typeof n === "number" && n >= before + delta) return { answer: "yes" };
    if (Date.now() >= deadline) return { answer: "no" };
    await sleep(pollMs);
  }
}

/** "The field still holds the value after the page is opened again". Never reads a password field. */
async function valueEquals(check, { notebook, steps, inputs, registry, resolve }, { maxMs = 8000, pollMs = 500 } = {}) {
  const want = wantedValue(check.probe && check.probe.value, inputs);
  const fillStep = (steps || []).find((s) => s && s.source_step_key && s.source_step_key === (check.probe && check.probe.field_step));
  if (!want || !fillStep || !notebook || !notebook.page || typeof resolve !== "function") return CANT;
  const expected = norm(want);
  return withFreshTab(notebook.page, registry, async (tab) => {
    const deadline = Date.now() + maxMs;
    let read = false;
    for (;;) {
      try {
        const loc = await resolve(tab, fillStep, inputs);
        const type = await loc.getAttribute("type", { timeout: 1500 }).catch(() => null);
        if (String(type || "").toLowerCase() === "password") return CANT;
        const raw = await evalOn(loc, pageScripts.readFieldValue, undefined, 1500);
        if (raw === EVAL_TIMED_OUT || typeof raw !== "string") throw new Error("not a field"); // a rich-text box answers too
        const actual = norm(raw);
        read = true;
        if (actual === expected || actual.includes(expected)) return { answer: "yes" };
      } catch (_) { /* the page may not have loaded the field yet */ }
      if (Date.now() >= deadline) return read ? { answer: "no" } : CANT; // never read it at all: not a "no"
      await sleep(pollMs);
    }
  });
}

/**
 * Every look this effect asks for, as `{ [check id]: {answer} }`. Never throws: a look that fails is "cant_tell".
 * @param {object} effect  the step's validation.effect
 * @param {{notebook: object|null, steps?: object[], inputs?: object, registry?: object, resolve?: Function}} ctx
 */
async function runLooks(effect, ctx) {
  const out = {};
  if (!effect || !ctx || !ctx.notebook) return out;
  for (const c of effect.checks || []) {
    if (!c || !c.id || !c.probe || isDead(c)) continue;
    try {
      if (c.probe.type === "url_stays") out[c.id] = await urlStays(c, ctx.notebook);
      else if (c.probe.type === "row_with_identity") out[c.id] = await rowWithIdentity(c, ctx);
      else if (c.probe.type === "value_equals") out[c.id] = await valueEquals(c, ctx);
    } catch (_) {
      out[c.id] = CANT;
    }
  }
  return out;
}

module.exports = { runLooks, urlStays, rowWithIdentity, valueEquals, wantedValue, wantedIdentity, MIN_VALUE_CHARS };
