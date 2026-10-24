"use strict";

// Advisory knowledge about an app's decision popups, shipped with a skill as app_knowledge.json
// (built from the author's app map: conxa-builder/python/conxa_compile/app_graph/knowledge.py).
//
// What it is for: a popup the skill's own fork does not recognise ("unknown dialog") may still be
// one that OTHER workflows on this app met. This module recognises it, so the run can
//   - follow the author's standing policy for it, under the same fail-closed rules as a fork
//     (a consequential button never runs unless the author approved it), or
//   - ask the person a better question (its real buttons marked risky or not, a suggestion).
//
// What it is NOT: a way to answer a popup on anyone's behalf. Nothing here dismisses anything; a
// popup it does not know, or knows without a policy, still goes to a person. Pure: no Playwright,
// no network, no model. See docs/TRD.md 11.5.

const fs = require("node:fs");
const path = require("node:path");
const fork = require("./fork");
const scrub = require("./scrub");

const FILE_NAME = "app_knowledge.json";
const SCHEMA_VERSION = 1;
const MAX_BYTES = 256 * 1024;

function asArray(v) { return Array.isArray(v) ? v : []; }

/** The web app (host) a skill works in, from its manifest's start URL; "" when unknown. */
function appOfManifest(manifest) {
  return hostOf(manifest && manifest.target_url);
}

/** The knowledge for a skill directory, or null when there is none or it is unusable. A missing,
 * oversized, malformed or newer-schema file is simply "no extra knowledge" -- never an error. */
function load(skillDir) {
  if (!skillDir) return null;
  try {
    const file = path.join(skillDir, FILE_NAME);
    if (!fs.existsSync(file) || fs.statSync(file).size > MAX_BYTES) return null;
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!data || data.schema_version !== SCHEMA_VERSION || typeof data.apps !== "object" || !data.apps) return null;
    return data;
  } catch (_) {
    return null;
  }
}

/** Lowercase hostname without a leading "www.", or "" for anything that is not an http(s) URL. */
function hostOf(url) {
  try {
    const u = new URL(String(url || ""));
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    const h = u.hostname.toLowerCase();
    return h.startsWith("www.") ? h.slice(4) : h;
  } catch (_) {
    return "";
  }
}

/** The known popup showing in `probe` on `host`, or null. Uses the fork matcher, so it needs every
 * known button present and a compatible title, and an ambiguous match is no match. */
function lookupPopup(knowledge, host, probe) {
  if (!knowledge || !host || !probe || probe.native) return null;
  const app = knowledge.apps && knowledge.apps[host];
  const popups = asArray(app && app.popups);
  if (!popups.length) return null;
  const seen = fork.matchState(probe, popups.map((p) => ({ id: p.id, signature: p.signature, popup: p })));
  return seen.kind === "state" ? seen.state.popup : null;
}

function optionByLabel(popup, label) {
  const want = fork.norm(label);
  return asArray(popup && popup.options).find((o) => fork.norm(o && o.label) === want) || null;
}

/** The role the map knows for a button label ("consequential" | "dismiss" | "neutral"), or null. */
function roleOf(popup, label) {
  const o = optionByLabel(popup, label);
  return o ? String(o.role || "neutral") : null;
}

// The button other workflows chose most, if it is on screen right now.
function suggestedLabel(popup, probe) {
  const visible = new Set(fork.probeLabelsRaw(probe).map(fork.norm));
  const best = asArray(popup && popup.options)
    .filter((o) => o && o.chosen > 0 && visible.has(fork.norm(o.label)))
    .sort((a, b) => b.chosen - a.chosen)[0];
  return best ? best.label : null;
}

/** What to do about a popup the map knows. Returns
 *   { do: "click", label, source: "app_policy" }   press this visible button
 *   { do: "pause", reason, suggested }             a person decides (the default)
 *   { do: "stop", message }
 * The same fail-closed rules as fork.decide: a consequential button without the author's approval
 * is a pause, a button that is not on screen is a pause, and inside a loop a pause is a stop. */
function decideKnown(popup, probe, { loopDepth = 0 } = {}) {
  const title = (popup && popup.signature && popup.signature.title) || (probe && probe.title) || "a popup";
  const stopMessage = `Needs a decision: ${title}`;
  const suggested = suggestedLabel(popup, probe);
  const pause = (reason) => (
    loopDepth > 0
      ? { do: "stop", message: `${stopMessage} (a person can't be asked inside a repeated step)` }
      : { do: "pause", reason, suggested }
  );

  const policy = popup && popup.app_policy;
  if (!policy || typeof policy !== "object") return pause("known_popup");

  if (policy.mode === "auto") {
    const label = fork.probeLabelsRaw(probe).find((l) => fork.norm(l) === fork.norm(policy.option));
    if (!label) return pause("app_policy_option_missing");
    const option = optionByLabel(popup, label);
    const risky = !option || option.role === "consequential";
    if (risky && policy.author_approved !== true) return pause("consequential_unapproved");
    return { do: "click", label, source: "app_policy" };
  }
  if (policy.mode === "ask_human") return pause("app_policy_ask");
  if (policy.mode === "stop") return { do: "stop", message: policy.message || stopMessage };
  return { do: "stop", message: stopMessage };
}

/** The telemetry fields for a popup this run could not place (event `app_obs`), or null when the
 * page is not an http(s) page. Everything goes through scrub.js: only a few title words, the
 * button labels and the page's route leave the machine -- never the popup's message, a file name,
 * a number or anything typed. `labels` are the visible buttons; `known` is whether the app map
 * recognised the popup. The cloud counts these per popup shape (docs/TRD.md 11.5, phase 6). */
function observation(url, { title, labels, known }) {
  const host = hostOf(url);
  if (!host) return null;
  const btns = asArray(labels).map(scrub.scrubLabel).filter(Boolean).slice(0, 6);
  if (btns.length < 2) return null; // not a choice worth reporting
  return {
    host,
    rt: scrub.routeTemplate(url),
    tt: scrub.scrubTokens(title),
    bt: btns,
    cl: "decision",
    k: known ? 1 : 0,
  };
}

module.exports = { FILE_NAME, SCHEMA_VERSION, load, hostOf, appOfManifest, lookupPopup, roleOf, decideKnown, observation };
