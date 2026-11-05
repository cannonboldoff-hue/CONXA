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

function attach(page) {
  page.on("response", (resp) => {
    try {
      const status = resp.status();
      const req = resp.request();
      const nav = req.isNavigationRequest() && resp.frame() === page.mainFrame();
      if (!nav) {
        const rt = req.resourceType();
        if (rt !== "xhr" && rt !== "fetch") return;
      }
      if (interesting(status, nav)) note(page, { status, nav, at: Date.now() });
      else if (nav) note(page, null); // a good page load: whatever came before is stale
    } catch (_) { /* a response that cannot be read is not evidence */ }
  });
}

/** The last refusal this page saw at or after `sinceMs`, or null. */
function recent(page, sinceMs) {
  const entry = page ? seen.get(page) : null;
  return entry && entry.at >= sinceMs ? entry : null;
}

module.exports = { attach, note, recent };
