"use strict";
// page_status.js — remembers the last response status a page saw that says "the app refused or could
// not answer", so a failing step can tell a real fault (403, 429, a 5xx, a 404 on the page itself)
// from a moved element. Read by run.js via fault_classify.js; nothing here ever acts on the page.
//
// Only statuses the page itself did not choose count:
// - the main-frame document: any status >= 400 (a successful reload clears it);
// - xhr / fetch: 401, 403, 429 and 5xx only. A 404 on a background request is routine (a missing
//   avatar, an optional resource) and must not end a run.

const seen = new WeakMap();

function note(page, entry) {
  if (page && typeof page === "object") seen.set(page, entry);
}

function interesting(status, nav) {
  if (nav) return status >= 400;
  return status === 401 || status === 403 || status === 429 || status >= 500;
}

// Completion gate (completion.js): requests that CHANGE something on the server (POST/PUT/PATCH/
// DELETE via xhr/fetch) and have not finished, and the last one that failed. A page that is still
// sending data has not finished its step, and one whose request was refused has failed it, whatever
// the screen looks like.
const mutations = new WeakMap(); // page -> { open: Map<request, startedAt>, failed: {at,key,why} | null, startedAt }
const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const MUTATION_REFUSED = new Set([401, 403, 409, 413, 422, 429]);

function mutState(page) {
  let m = mutations.get(page);
  if (!m) { m = { open: new Map(), failed: null, startedAt: 0 }; mutations.set(page, m); }
  return m;
}

function isMutation(req) {
  const rt = req.resourceType();
  return (rt === "xhr" || rt === "fetch") && typeof req.method === "function" && MUTATING.has(req.method());
}

// Same endpoint, query stripped: a later success on it means the app retried and got through.
function endpointKey(req) {
  try { const u = new URL(req.url()); return `${req.method()} ${u.origin}${u.pathname}`; } catch (_) { return req.url(); }
}

function attach(page) {
  page.on("response", (resp) => {
    try {
      const status = resp.status();
      const req = resp.request();
      if (isMutation(req)) {
        const m = mutState(page);
        if (MUTATION_REFUSED.has(status) || status >= 500) m.failed = { at: Date.now(), key: endpointKey(req), why: `status ${status}` };
        else if (m.failed && m.failed.key === endpointKey(req)) m.failed = null;
      }
      const nav = req.isNavigationRequest() && resp.frame() === page.mainFrame();
      if (!nav) {
        const rt = req.resourceType();
        if (rt !== "xhr" && rt !== "fetch") return;
      }
      if (interesting(status, nav)) note(page, { status, nav, at: Date.now() });
      else if (nav) note(page, null); // a good page load: whatever came before is stale
    } catch (_) { /* a response that cannot be read is not evidence */ }
  });
  page.on("request", (req) => {
    try {
      if (!isMutation(req)) return;
      const m = mutState(page);
      m.startedAt = Date.now();
      m.open.set(req, m.startedAt);
    } catch (_) { /* not evidence */ }
  });
  page.on("requestfinished", (req) => {
    try { mutState(page).open.delete(req); } catch (_) { /* not evidence */ }
  });
  page.on("requestfailed", (req) => {
    try {
      const m = mutState(page);
      if (!m.open.delete(req)) return;
      const why = (req.failure() && req.failure().errorText) || "request failed";
      if (!/ERR_ABORTED/i.test(why)) m.failed = { at: Date.now(), key: endpointKey(req), why };
    } catch (_) { /* not evidence */ }
  });
}

/** The last refusal this page saw at or after `sinceMs`, or null. */
function recent(page, sinceMs) {
  const entry = page ? seen.get(page) : null;
  return entry && entry.at >= sinceMs ? entry : null;
}

/** How many data-changing requests started at or after `sinceMs` are still open. */
function pendingMutations(page, sinceMs) {
  const m = page ? mutations.get(page) : null;
  if (!m) return 0;
  let n = 0;
  for (const at of m.open.values()) if (at >= sinceMs) n++;
  return n;
}

/** Did a data-changing request start at or after `sinceMs`, finished or not? A first sign the app took the work. */
function mutationStarted(page, sinceMs) {
  const m = page ? mutations.get(page) : null;
  return !!m && m.startedAt > 0 && m.startedAt >= sinceMs;
}

/** The last data-changing request this page lost or had refused at or after `sinceMs`, or null. */
function mutationFailure(page, sinceMs) {
  const m = page ? mutations.get(page) : null;
  return m && m.failed && m.failed.at >= sinceMs ? m.failed : null;
}

module.exports = { attach, note, recent, pendingMutations, mutationStarted, mutationFailure };
