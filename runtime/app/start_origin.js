"use strict";
/**
 * start_origin.js — an address check follows the site the run starts on (BUILD-101 (6)).
 *
 * A check that says where a step should land (`url_pattern`, and the "moved on and stayed" look `url_stays`) names the
 * site the recording was made on. When the person changes the workflow's start page to another site of the same app
 * (en.wikipedia.org to de.wikipedia.org) every landing is on the new site and the check, still naming the old one,
 * would fail a run that did exactly what it should. The compiler stamps `start_origin` on a check whose site is the
 * recording's start site (compiler/start_origin.py); at the start of a run the site part of such a check is swapped for
 * the site the run actually starts on. The path part is untouched, so the check still proves where it landed.
 *
 * Only a check that carries the stamp is touched: a check on another site, one with no stamp (an older compile) and a
 * run that starts where it was recorded are left as they are.
 */

// Python's re.escape (3.7+) escapes exactly these; compiler/navigation_effects.py::generalize_url_pattern wrote the
// pattern with it, so the prefix to swap must be spelled the same way.
const PY_ESCAPED = /[()[\]{}?*+\-|^$\\.&~# \t\n\r\v\f]/g;
const pyEscape = (s) => String(s).replace(PY_ESCAPED, "\\$&");

/** "https://host" of an absolute http(s) address, "" otherwise. */
function originOf(url) {
  try {
    const u = new URL(String(url || "").trim());
    return u.protocol === "http:" || u.protocol === "https:" ? `${u.protocol}//${u.host}` : "";
  } catch (_) { return ""; }
}

/** The site the run starts on: the address of its first navigate step, with the run's inputs put in. */
function actualStartOrigin(steps, inputs, interpolate) {
  const first = (Array.isArray(steps) ? steps : []).find((s) => s && s.type === "navigate" && s.url);
  return first ? originOf(interpolate(String(first.url), inputs || {})) : "";
}

function follow(node, recorded, actual) {
  if (Array.isArray(node)) {
    let changed = false;
    const out = node.map((v) => { const r = follow(v, recorded, actual); if (r !== v) changed = true; return r; });
    return changed ? out : node;
  }
  if (!node || typeof node !== "object") return node;
  const isCheck = node.start_origin && ((node.type === "url_pattern" && typeof node.target === "string")
    || (node.type === "url_stays" && typeof node.pattern === "string"));
  if (isCheck) {
    const field = node.type === "url_pattern" ? "target" : "pattern";
    const was = `^${pyEscape(node.start_origin)}`;
    if (node.start_origin !== actual && node[field].startsWith(was)) {
      return { ...node, [field]: `^${pyEscape(actual)}${node[field].slice(was.length)}` };
    }
    return node;
  }
  let changed = false;
  const out = {};
  for (const [k, v] of Object.entries(node)) {
    const r = follow(v, recorded, actual);
    if (r !== v) changed = true;
    out[k] = r;
  }
  return changed ? out : node;
}

/** `steps` with every stamped address check moved to the site this run starts on. Returns `steps` itself when nothing
 *  needs to move (no navigate step, a start site that cannot be read, or every check already names it). */
function followStartOrigin(steps, inputs, interpolate) {
  const actual = actualStartOrigin(steps, inputs, interpolate);
  return actual ? follow(steps, null, actual) : steps;
}

module.exports = { followStartOrigin, originOf, pyEscape };
