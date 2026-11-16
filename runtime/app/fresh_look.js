"use strict";
/**
 * fresh_look.js — "go and look" (Proof of Done stage 5). The one witness that asks the app afresh instead of reading what
 * the page already says: open the SAME page again in a separate tab and see whether an uploaded file's name is on it, and
 * more times than before the upload.
 *
 * What it does and does not do:
 *   - It only loads the page the step is already on (a GET of an address already open) and reads its text. It never clicks,
 *     types or answers a popup, and it never touches the step's own tab except to bring it back to the front.
 *   - The tab it opens is closed again, and removed from the run's tab registry's pending list, so the run can never
 *     mistake it for a tab the site opened (steps only ever run on the tab they were recorded on).
 *   - Where a tab cannot be opened (a browser borrowed from Conxa Execute has no way to make one) or the address is not
 *     an ordinary web page, the answer is "cant_tell", never a guess.
 *   - "yes" needs MORE occurrences of the name than before the upload, so a file that was already there proves nothing.
 *     "no" means it was not found within the time allowed (a list can be slow to catch up).
 */
const pageScripts = require("./page_scripts");
const { evalOn } = require("./page_eval");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Run `fn(tab)` on a separate tab that has loaded the page's current address, then close it again. "cant_tell" when
 * the address is not an ordinary web page or no tab can be opened; whatever `fn` returns otherwise. Shared by every
 * "go and look" witness so none of them can leave a tab behind or mistake it for one the site opened.
 */
async function withFreshTab(page, registry, fn) {
  let url = "";
  try { url = String(page.url()); } catch (_) { return { answer: "cant_tell" }; }
  if (!/^https?:\/\//i.test(url)) return { answer: "cant_tell" };

  let tab = null;
  try {
    tab = await page.context().newPage();
  } catch (_) {
    return { answer: "cant_tell" };
  }
  try {
    await tab.goto(url, { waitUntil: "domcontentloaded", timeout: 15000 });
    return await fn(tab);
  } catch (_) {
    return { answer: "cant_tell" };
  } finally {
    try { await tab.close(); } catch (_) { /* already gone */ }
    if (registry && Array.isArray(registry.pendingPages)) {
      const at = registry.pendingPages.indexOf(tab);
      if (at >= 0) registry.pendingPages.splice(at, 1);
    }
    try { await page.bringToFront(); } catch (_) { /* optional */ }
  }
}

/**
 * @param {{page: any, registry?: {pendingPages?: any[]}, fileName: string|null, countBefore: number|null,
 *   maxMs?: number, pollMs?: number}} o
 * @returns {Promise<{answer: "yes"|"no"|"cant_tell", evidence?: string}>}
 */
async function freshLook({ page, registry = null, fileName, countBefore, maxMs = 12000, pollMs = 500 }) {
  if (!fileName || typeof countBefore !== "number") return { answer: "cant_tell" };
  return withFreshTab(page, registry, async (tab) => {
    const deadline = Date.now() + maxMs;
    for (;;) {
      const n = await evalOn(tab, pageScripts.countPageText, fileName, 3000);
      if (typeof n === "number" && n > countBefore) return { answer: "yes" };
      if (Date.now() >= deadline) return { answer: "no" };
      await sleep(pollMs);
    }
  });
}

module.exports = { freshLook, withFreshTab };
