"use strict";
/**
 * route_template.js — the STRUCTURE of a URL and none of its per-run values.
 *
 * The runtime's twin of conxa_compile/compiler/navigation_effects.py::route_template /
 * generalize_url_pattern, with the same rule: origin and literal path words are kept, any path segment
 * that carries a digit or is a long token (ids, uuids, hashes, slugs with ids) becomes `*`, and the query
 * string and fragment are dropped. Two requests to /upload/abc123 and /upload/zzz999 are the same
 * template, and a customer's file name, id or token never reaches the evidence notebook or telemetry.
 */

// Same as _DYNAMIC_SEGMENT_MIN_LEN in navigation_effects.py.
const DYNAMIC_SEGMENT_MIN_LEN = 16;

function isDynamicSegment(segment) {
  return /\d/.test(segment) || segment.length >= DYNAMIC_SEGMENT_MIN_LEN;
}

/** "https://host/orders/*" for "https://host/orders/12345?token=x#y". "" when `url` is not an absolute http(s) URL. */
function routeTemplate(url) {
  let u;
  try { u = new URL(String(url || "").trim()); } catch (_) { return ""; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return "";
  const segs = u.pathname.split("/").filter(Boolean).map((s) => {
    let seg = s;
    try { seg = decodeURIComponent(s); } catch (_) { /* keep it as sent */ }
    return isDynamicSegment(seg) ? "*" : seg;
  });
  return `${u.protocol}//${u.host}${segs.length ? `/${segs.join("/")}` : ""}`;
}

module.exports = { routeTemplate, isDynamicSegment, DYNAMIC_SEGMENT_MIN_LEN };
