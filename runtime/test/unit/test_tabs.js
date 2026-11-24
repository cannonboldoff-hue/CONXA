"use strict";

const test = require("node:test");
const assert = require("node:assert");

const { createTabRegistry, resolveStepPage, stepNamesTab, stepInheritsPage, closeExtraTabs } = require("../../app/tabs");

// Minimal fake Playwright Page/BrowserContext — enough surface for tabs.js, no real browser.
function fakePage(id, { url = "" } = {}) {
  let closed = false;
  let currentUrl = url;
  let waiters = [];
  const page = {
    id,
    waitForURLCalls: 0,
    isClosed: () => closed,
    close: () => {
      closed = true;
      waiters.forEach((w) => w.reject(new Error("closed")));
      waiters = [];
    },
    url: () => currentUrl,
    // Simulates a real navigation (e.g. a target=_blank popup landing on its destination a
    // beat after opening at about:blank) — resolves any waitForURL() call whose predicate the
    // new URL satisfies.
    navigate(newUrl) {
      currentUrl = newUrl;
      const satisfied = waiters.filter((w) => w.predicate(newUrl));
      waiters = waiters.filter((w) => !w.predicate(newUrl));
      satisfied.forEach((w) => w.resolve());
    },
    waitForURL(predicate, opts = {}) {
      page.waitForURLCalls++;
      page._lastWaitForURLTimeout = opts.timeout;
      return new Promise((resolve, reject) => {
        if (predicate(currentUrl)) return resolve();
        // Fake timeout capped well below any real value under test so a page that never
        // navigates doesn't slow the suite down, regardless of what timeout was requested.
        const timer = setTimeout(() => {
          waiters = waiters.filter((w) => w.resolve !== resolve);
          reject(new Error("timeout"));
        }, Math.min(Number(opts.timeout) || 0, 50));
        waiters.push({ predicate, resolve: () => { clearTimeout(timer); resolve(); }, reject });
      });
    },
    waitForLoadState(_state, opts = {}) {
      page._lastLoadStateTimeout = opts.timeout;
      return Promise.resolve();
    },
    bringToFront: async () => {},
  };
  return page;
}

function fakeContext({ waitForEventPage = null } = {}) {
  const listeners = [];
  return {
    listeners,
    newPageCalls: 0,
    on(evt, cb) { if (evt === "page") listeners.push(cb); },
    async newPage() {
      this.newPageCalls++;
      const page = fakePage(`user-created-${this.newPageCalls}`);
      // Real Playwright fires the context's "page" event for context.newPage() too, not just
      // for site-opened popups — mirrored here since that's exactly the collision tabs.js's
      // `bound` set exists to prevent.
      for (const cb of listeners) cb(page);
      return page;
    },
    async waitForEvent(_evt, opts = {}) {
      const predicate = typeof opts === "function" ? opts : opts.predicate;
      if (waitForEventPage && (!predicate || predicate(waitForEventPage))) return waitForEventPage;
      throw new Error("timed out waiting for page event");
    },
    fireNewPage(page) { for (const cb of listeners) cb(page); },
  };
}

function registryWith(context) {
  const initial = fakePage("tab_0");
  initial.context = () => context;
  return createTabRegistry(initial);
}

test("stepNamesTab: false for absent/tab_0, true for a real tab", () => {
  assert.strictEqual(stepNamesTab({}), false);
  assert.strictEqual(stepNamesTab({ tab: { id: "tab_0" } }), false);
  assert.strictEqual(stepNamesTab({ tab: { id: "tab_1" } }), true);
});

test("stepInheritsPage: true only for a tab marker with no tab block at all", () => {
  assert.strictEqual(stepInheritsPage({ type: "popup" }), true);
  assert.strictEqual(stepInheritsPage({ type: "tab_switch" }), true);
  assert.strictEqual(stepInheritsPage({ type: "tab_open" }), true);
  assert.strictEqual(stepInheritsPage({ type: "popup", tab: {} }), true);
  assert.strictEqual(stepInheritsPage({ type: "popup", tab: { id: "tab_2" } }), false);
  assert.strictEqual(stepInheritsPage({ type: "click" }), false);
  assert.strictEqual(stepInheritsPage({ type: "click", tab: { id: "tab_2" } }), false);
});

test("no tab / tab_0 always resolves to the initial page", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const a = await resolveStepPage(registry, {});
  const b = await resolveStepPage(registry, { tab: { id: "tab_0" } });
  assert.strictEqual(a.id, "tab_0");
  assert.strictEqual(b.id, "tab_0");
});

test("site-opened tab: drains an already-queued popup instead of waiting", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const popup = fakePage("popup-1");
  ctx.fireNewPage(popup); // fires before any step asks — this is the race tabs.js closes
  const page = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } });
  assert.strictEqual(page.id, "popup-1");
});

test("site-opened tab: waits for the page event if none is queued yet", async () => {
  const late = fakePage("late-popup");
  const ctx = fakeContext({ waitForEventPage: late });
  const registry = registryWith(ctx);
  const page = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } });
  assert.strictEqual(page.id, "late-popup");
});

test("returning to an already-bound tab reuses the same page object", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const popup = fakePage("popup-1");
  ctx.fireNewPage(popup);
  const first = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } });
  const second = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } });
  assert.strictEqual(first, second);
});

test("user-opened tab (Ctrl+T): the runtime creates it itself", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const page = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "user" } });
  assert.strictEqual(ctx.newPageCalls, 1);
  assert.strictEqual(page.id, "user-created-1");
});

test("a page created for a user-opened tab is never later handed out as a different site-opened tab", async () => {
  // The exact Create-a-Lead failure: step 11 opens tab_1 via Ctrl+T (opened_by: "user"), then a
  // click on tab_1 opens the real login popup (tab_2, opened_by: "site"). Before the `bound` set
  // existed, tab_1's own blank page — which also lands in pendingPages, since newPage() fires
  // the same context "page" event a real popup does — was handed back out as tab_2, so the
  // login form ran against the wrong page.
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const userPage = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "user" } });
  const realPopup = fakePage("popup-2");
  ctx.fireNewPage(realPopup);
  const sitePage = await resolveStepPage(registry, { tab: { id: "tab_2", opened_by: "site" } });
  assert.strictEqual(sitePage.id, "popup-2");
  assert.notStrictEqual(sitePage, userPage);
});

test("draining pendingPages skips pages already bound to another tab or already closed", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const alreadyBound = fakePage("already-bound");
  registry.bound.add(alreadyBound);
  ctx.fireNewPage(alreadyBound);
  const closedOne = fakePage("closed-one");
  closedOne.close();
  ctx.fireNewPage(closedOne);
  const real = fakePage("real-popup");
  ctx.fireNewPage(real);
  const page = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } });
  assert.strictEqual(page.id, "real-popup");
});

test("unresolvable site tab throws tabNotFound and never falls back to the current page", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  await assert.rejects(
    () => resolveStepPage(registry, { tab: { id: "tab_9", opened_by: "site" } }),
    (err) => err.tabNotFound === true
  );
});

test("a bound tab that has since closed fails loudly rather than silently rebinding to an unrelated page", async () => {
  // A step never executes on a tab other than the one it was recorded on (CLAUDE.md invariant).
  // Once tab_1 has been resolved and closes, a later step recorded on tab_1 must not grab
  // whatever page happens to open next — that page has nothing to do with tab_1.
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const popup = fakePage("popup-1");
  ctx.fireNewPage(popup);
  const first = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } });
  first.close();
  const unrelated = fakePage("popup-2");
  ctx.fireNewPage(unrelated);
  await assert.rejects(
    () => resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } }),
    (err) => err.tabNotFound === true
  );
});

test("an about:blank popup is settled only after it navigates to a real url", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const popup = fakePage("popup-1", { url: "about:blank" });
  ctx.fireNewPage(popup);
  const settlePromise = resolveStepPage(
    registry,
    { tab: { id: "tab_1", opened_by: "site" } },
    { loadTimeoutMs: 5000 }
  );
  // Let resolveStepPage's drain + _settle's waitForURL registration run before navigating —
  // otherwise this call could race ahead of the waiter being set up.
  await new Promise((r) => setImmediate(r));
  popup.navigate("https://search-engine-5nfe.vercel.app/login");
  const page = await settlePromise;
  assert.strictEqual(page.url(), "https://search-engine-5nfe.vercel.app/login");
});

test("_settle passes the caller's loadTimeoutMs through to the page's load waits", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const popup = fakePage("popup-1", { url: "https://example.com/already-loaded" });
  ctx.fireNewPage(popup);
  await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } }, { loadTimeoutMs: 12345 });
  assert.strictEqual(popup._lastLoadStateTimeout, 12345);
});

test("_settle defaults to the 60s settle timeout when no loadTimeoutMs is given", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const popup = fakePage("popup-1", { url: "https://example.com/already-loaded" });
  ctx.fireNewPage(popup);
  await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } });
  assert.strictEqual(popup._lastLoadStateTimeout, 60000);
});

// Regression: a user-opened (Ctrl+T) tab's page is deliberately blank until the compiler's own
// synthesized `navigate` step runs against it — nothing else ever navigates it on its own. The
// about:blank wait in _settle must not apply to it, or resolving the tab_open marker step AND
// the navigate step that immediately follows it (both carry the same tab.id) each burn the full
// settle timeout waiting for a navigation that was never going to happen by itself — the exact
// stall reported against the Create-a-Lead workflow's Vercel tab.
test("a user-opened tab never waits for its own navigation — that would double the stall for no reason", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const tabOpenStep = { type: "tab_open", tab: { id: "tab_1", opened_by: "user" } };
  const navigateStep = { type: "navigate", tab: { id: "tab_1", opened_by: "user" } };

  const page1 = await resolveStepPage(registry, tabOpenStep, { loadTimeoutMs: 5000 });
  const page2 = await resolveStepPage(registry, navigateStep, { loadTimeoutMs: 5000 });

  assert.strictEqual(page1, page2);
  assert.strictEqual(page1.url(), "");
  assert.strictEqual(page1.waitForURLCalls, 0, "waitForURL must never be called for a user-opened tab");
});

test("a site-opened tab still waits for its popup to leave about:blank", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const popup = fakePage("popup-1", { url: "about:blank" });
  ctx.fireNewPage(popup);
  await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } }, { loadTimeoutMs: 5000 });
  assert.strictEqual(popup.waitForURLCalls, 1);
});

// Regression: the actual reported bug. A skill's own step 0 navigate step lands on a fresh
// tab_0 that is still on about:blank (server.js deliberately skips its own pre-navigation when
// step 0 is a navigate). tab_0 carries no `tab` block at all, so this used to fall into the same
// "wait for an external navigation" branch as a site-opened popup — burning the full settle
// timeout waiting for a navigation that the navigate step itself was about to perform one line
// later. This is the ~70s stall reported against "Testing Download then Upload with context
// keeping"'s step_0_navigate_start -> step_1_click_start gap.
test("a navigate step on a fresh tab_0 never waits for an external navigation", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx); // tab_0 starts at url: "" (fakePage default)
  const page = await resolveStepPage(registry, { type: "navigate" }, { loadTimeoutMs: 60000 });
  assert.strictEqual(page.waitForURLCalls, 0, "waitForURL must never be called for tab_0");
});

// Sibling case: any non-navigate step 0 on a blank tab_0 (protectedUrl was empty, e.g. the
// group-no-required-apps path) must not wait either — nothing is ever going to navigate tab_0
// except the runtime's own next step, regardless of that step's type.
test("a non-navigate step on a fresh tab_0 never waits for an external navigation", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const page = await resolveStepPage(registry, { type: "click" }, { loadTimeoutMs: 60000 });
  assert.strictEqual(page.waitForURLCalls, 0, "waitForURL must never be called for tab_0");
});

// The one wait that stays genuine (a real site-opened popup) must still be bounded — a popup
// that hasn't navigated off about:blank within NAV_SETTLE_CAP_MS isn't going to, so it must not
// be allowed to burn the full loadTimeoutMs budget.
test("a site-opened popup's navigation wait is capped below the full settle timeout", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const popup = fakePage("popup-1", { url: "about:blank" });
  ctx.fireNewPage(popup);
  await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } }, { loadTimeoutMs: 60000 });
  assert.strictEqual(popup.waitForURLCalls, 1);
  assert.ok(popup._lastWaitForURLTimeout <= 10000, `expected capped timeout, got ${popup._lastWaitForURLTimeout}`);
});

// EXEC-14: a second tab a multi-tab run opens must not outlive the run in the headless/cached-
// context path (watch: false) — nothing else closes it there. See server.js's _openedTabs +
// closeExtraTabs(_openedTabs, ...) call on every exit path.
// Regression (2026-08-23, mega-workflow A→B→A): returning to the initial page used to skip
// settlement entirely — resolveStepPage's tab_0 shortcut returned the page raw, so the return
// leg of a multi-tab run replayed with no load wait and no bringToFront, firing clicks at a
// hidden background tab while the visible one stayed front.
test("a step that switches back to the initial page settles and brings it to front", async () => {
  const ctx = fakeContext();
  const initial = fakePage("tab_0", { url: "https://a.test/" });
  initial.context = () => ctx;
  const registry = createTabRegistry(initial);
  let frontCalls = 0;
  initial.bringToFront = async () => { frontCalls++; };

  const popup = fakePage("popup-1", { url: "https://b.test/" });
  ctx.fireNewPage(popup);
  const b1 = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } }, { prevPage: initial });
  assert.strictEqual(b1.id, "popup-1");

  const back = await resolveStepPage(registry, {}, { prevPage: b1, watch: true, loadTimeoutMs: 4000 });
  assert.strictEqual(back.id, "tab_0");
  assert.strictEqual(frontCalls, 1, "returning to tab_0 must bringToFront under watch");
  assert.strictEqual(initial._lastLoadStateTimeout, 4000, "returning to tab_0 must run the load-state wait");
});

test("consecutive steps on the same page do not re-settle", async () => {
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const popup = fakePage("popup-1", { url: "https://b.test/" });
  ctx.fireNewPage(popup);
  const first = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } }, { watch: true });

  let frontCalls = 0;
  first.bringToFront = async () => { frontCalls++; };
  const second = await resolveStepPage(
    registry,
    { tab: { id: "tab_1", opened_by: "site" } },
    { prevPage: first, watch: true }
  );
  assert.strictEqual(second, first);
  assert.strictEqual(frontCalls, 0, "no page switch — no bringToFront");
});

// Regression (2026-09-02, mega-workflow step 28-35): a click with target="_blank" opens a tab
// Chromium immediately puts in FRONT. Every following step stays on the original tab, so the
// prevPage === page early return skipped settlement and bringToFront was never called — the
// run drove the original tab from the background for the remaining ~80 steps while the popup
// sat visible. The user saw "it never switched tabs"; the failure surfaced 6 steps later.
test("a page appearing mid-run reclaims the foreground even with no page switch", async () => {
  const ctx = fakeContext();
  const initial = fakePage("tab_0", { url: "https://a.test/" });
  initial.context = () => ctx;
  const registry = createTabRegistry(initial);

  // Step N runs on tab_0 and clears the flag createTabRegistry never set.
  const first = await resolveStepPage(registry, {}, { prevPage: initial, watch: true });
  assert.strictEqual(first.id, "tab_0");

  let frontCalls = 0;
  const phases = [];
  initial.bringToFront = async () => { frontCalls++; };

  // The click on step N opened a popup nothing in the skill ever references.
  ctx.fireNewPage(fakePage("orphan-popup", { url: "https://b.test/new" }));

  // Step N+1 is still on tab_0 — same page, but the foreground moved.
  const second = await resolveStepPage(registry, {}, {
    prevPage: initial, watch: true, loadTimeoutMs: 4000, onPhase: (p) => phases.push(p),
  });
  assert.strictEqual(second, initial);
  assert.strictEqual(frontCalls, 1, "a page that stole the foreground must be displaced");
  assert.ok(phases.includes("foreground_reclaimed"), `expected phase marker, got ${phases}`);

  // One appearance, one reclaim — the flag must not latch.
  const third = await resolveStepPage(registry, {}, { prevPage: initial, watch: true });
  assert.strictEqual(third, initial);
  assert.strictEqual(frontCalls, 1, "flag must clear after one reclaim");
});

test("foreground reclaim does not depend on watch mode", async () => {
  const ctx = fakeContext();
  const initial = fakePage("tab_0", { url: "https://a.test/" });
  initial.context = () => ctx;
  const registry = createTabRegistry(initial);
  await resolveStepPage(registry, {}, { prevPage: initial });

  let frontCalls = 0;
  initial.bringToFront = async () => { frontCalls++; };
  ctx.fireNewPage(fakePage("orphan-popup", { url: "https://b.test/new" }));

  // watch:false — a backgrounded page still throttles rAF/timers, which stalls Playwright's
  // actionability "stable" wait. The invisible switch is only the watch-mode symptom.
  await resolveStepPage(registry, {}, { prevPage: initial });
  assert.strictEqual(frontCalls, 1, "headless runs must reclaim the foreground too");
});

test("closeExtraTabs: closes every tracked page not in the keep set", async () => {
  const a = fakePage("a");
  const b = fakePage("b");
  const c = fakePage("c");
  await closeExtraTabs(new Set([a, b, c]), b);
  assert.strictEqual(a.isClosed(), true);
  assert.strictEqual(b.isClosed(), false, "kept page must survive");
  assert.strictEqual(c.isClosed(), true);
});

test("closeExtraTabs: accepts a Set for keep, not just a single page", async () => {
  const a = fakePage("a");
  const b = fakePage("b");
  await closeExtraTabs(new Set([a, b]), new Set([a, b]));
  assert.strictEqual(a.isClosed(), false);
  assert.strictEqual(b.isClosed(), false);
});

test("closeExtraTabs: no keep argument closes everything tracked", async () => {
  const a = fakePage("a");
  const b = fakePage("b");
  await closeExtraTabs(new Set([a, b]));
  assert.strictEqual(a.isClosed(), true);
  assert.strictEqual(b.isClosed(), true);
});

test("closeExtraTabs: already-closed pages are skipped without error", async () => {
  const a = fakePage("a");
  a.close();
  await assert.doesNotReject(() => closeExtraTabs(new Set([a])));
});

test("closeExtraTabs: a page whose close() throws does not stop the rest from closing", async () => {
  const broken = fakePage("broken");
  broken.close = () => { throw new Error("already gone"); };
  const survivor = fakePage("survivor");
  await assert.doesNotReject(() => closeExtraTabs(new Set([broken, survivor])));
  assert.strictEqual(survivor.isClosed(), true);
});

test("closeExtraTabs: empty tracked set is a no-op", async () => {
  await assert.doesNotReject(() => closeExtraTabs(new Set()));
});

// Studio's test browser keeps a transparent "hands off" page over the run's page. It is the first new page the run's
// context sees, and a site-opened-tab step binds the first pending page, so it must never be handed out as a tab.
test("the test browser's hands-off page is never bound as a recorded tab", async () => {
  const { isShieldPage } = require("../../app/tabs");
  const ctx = fakeContext();
  const registry = registryWith(ctx);
  const shield = fakePage("shield", { url: "about:blank?conxa_shield=1" });
  const popup = fakePage("popup", { url: "https://app.test/new" });
  assert.strictEqual(isShieldPage(shield), true);
  assert.strictEqual(isShieldPage(popup), false);
  ctx.fireNewPage(shield); // created at run start, long before the popup
  ctx.fireNewPage(popup);
  const got = await resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } });
  assert.strictEqual(got.id, "popup");
});

test("with only the hands-off page pending, a site-opened tab is not found (never falls back to it)", async () => {
  const ctx = fakeContext({ waitForEventPage: fakePage("shield", { url: "about:blank?conxa_shield=1" }) });
  const registry = registryWith(ctx);
  ctx.fireNewPage(fakePage("shield2", { url: "about:blank?conxa_shield=1" }));
  await assert.rejects(resolveStepPage(registry, { tab: { id: "tab_1", opened_by: "site" } }), (e) => e.tabNotFound === true);
});

// EXEC-10, found by a real recorded 3-tab run: the Studio test paused before a step on the second tab, and the
// resumed call started on the parked page with a fresh registry. It bound that page as tab_0 and opened a blank
// tab for tab_1, so "Click Delete" ran on an empty page. A resume must find the run's own tabs again.
test("a resumed run finds the tabs it already opened, starting from whichever tab it parked on", async () => {
  const ctx = fakeContext({ waitForEventPage: null });
  const all = [];
  ctx.pages = () => all.filter((p) => !p.isClosed());
  const first = registryWith(ctx);
  const tab0 = first.pages.get("tab_0");
  all.push(tab0);
  const tab1 = await resolveStepPage(first, { tab: { id: "tab_1", opened_by: "user" } });
  all.push(tab1);
  const popup = fakePage("popup", { url: "https://app.test/new" });
  ctx.fireNewPage(popup); all.push(popup);
  await resolveStepPage(first, { tab: { id: "tab_2", opened_by: "site" } });

  // Park on tab_1, then resume: the new call's start page is tab_1, not the first tab.
  tab1.context = () => ctx;
  const resumed = createTabRegistry(tab1, { resume: true });
  assert.strictEqual(await resolveStepPage(resumed, { tab: { id: "tab_1", opened_by: "user" } }), tab1);
  assert.strictEqual(ctx.newPageCalls, 1, "no new blank tab is opened for a tab the run already has");
  assert.strictEqual(await resolveStepPage(resumed, { tab: { id: "tab_0" } }), tab0);
  assert.strictEqual(await resolveStepPage(resumed, { tab: { id: "tab_2", opened_by: "site" } }), popup);
});

test("a fresh run never adopts tabs left over from an earlier run in the same browser", async () => {
  const ctx = fakeContext();
  const all = [];
  ctx.pages = () => all;
  const first = registryWith(ctx);
  all.push(first.pages.get("tab_0"), await resolveStepPage(first, { tab: { id: "tab_1", opened_by: "user" } }));
  const next = fakePage("fresh");
  next.context = () => ctx;
  const fresh = createTabRegistry(next);
  assert.strictEqual(await resolveStepPage(fresh, { tab: { id: "tab_0" } }), next);
  const t1 = await resolveStepPage(fresh, { tab: { id: "tab_1", opened_by: "user" } });
  assert.notStrictEqual(t1, all[1], "the earlier run's tab_1 is not this run's tab_1");
});

test("a resumed step on a first tab that has since closed fails as tab-not-found, never runs on another tab", async () => {
  const ctx = fakeContext();
  const all = [];
  ctx.pages = () => all.filter((p) => !p.isClosed());
  const first = registryWith(ctx);
  const tab0 = first.pages.get("tab_0");
  const tab1 = await resolveStepPage(first, { tab: { id: "tab_1", opened_by: "user" } });
  all.push(tab0, tab1);
  tab0.close();
  tab1.context = () => ctx;
  const resumed = createTabRegistry(tab1, { resume: true });
  await assert.rejects(resolveStepPage(resumed, { tab: { id: "tab_0" } }), (e) => e.tabNotFound === true);
});
