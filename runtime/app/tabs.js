"use strict";

// Multi-tab step resolution. A compiled skill's steps each carry an optional `tab` block
// (SkillStep.tab, from the compiler — see conxa_compile/compiler/build.py::_build_tab_context)
// describing which browser tab that step was recorded on. This module resolves that back to a
// live Playwright Page at replay time, mirroring the frame_chain precedent in resolver.js:
// declarative per-step context, resolved fresh on every step, never carried as mutable run state
// outside this registry.
//
// A step with no `tab` (or `tab.id` === "tab_0"/absent) always means the initial page — the
// only page every skill compiled before multi-tab support ever had, so those skills replay
// through the exact same code path as before this module existed.

const { SETTLE_TIMEOUT_MS: DEFAULT_SETTLE_TIMEOUT_MS, NAV_SETTLE_CAP_MS } = require("./run_config");
const hostBrowser = require("./host_browser");

const DEFAULT_TAB_OPEN_TIMEOUT_MS = 30000;

// Page -> { run, id }: which run's tab a live page is. Outlives one runPlan call, so a resumed
// call can find the tabs its run already opened (see createTabRegistry).
const TAB_TAGS = new WeakMap();

function tabOpenTimeoutMs() {
  const value = Number(process.env.CONXA_TAB_OPEN_TIMEOUT_MS);
  return Number.isFinite(value) && value >= 0 ? value : DEFAULT_TAB_OPEN_TIMEOUT_MS;
}

/**
 * Create a tab registry bound to `initialPage`'s browser context. Must be called once at the
 * start of a run, before any step executes — it registers the context's "page" listener
 * immediately so a popup that fires before any step asks for it is still queued, not missed
 * (the race this replaces: waiting for a "page" event only once the step that needs it runs).
 */
function createTabRegistry(initialPage, opts = {}) {
  const context = initialPage.context();
  // A resumed run (a park: test guard, popup question, hand-over) starts again on the page it
  // parked on, which may be any tab. Rebuild the run's own tab map from the tags its pages
  // carry, so a resumed step finds the tab it was recorded on instead of binding the parked page
  // as tab_0 and opening a fresh blank tab for its own (EXEC-10). A fresh run starts a new map.
  const prior = opts.resume ? TAB_TAGS.get(initialPage) : null;
  const run = prior ? prior.run : {};
  const pages = new Map();
  if (prior && typeof context.pages === "function") {
    for (const p of context.pages()) {
      const tag = TAB_TAGS.get(p);
      let closed = true;
      try { closed = p.isClosed(); } catch (_) { closed = true; }
      if (tag && tag.run === run && !closed) pages.set(tag.id, p);
    }
  } else {
    pages.set("tab_0", initialPage);
  }
  for (const [id, p] of pages) TAB_TAGS.set(p, { run, id });
  // Every page the registry has ever bound to a tab id — a drained popup, a waitForEvent
  // result, or a page the registry created itself via context.newPage(). Pages land in
  // pendingPages the instant Playwright reports them (a newPage() call fires the same "page"
  // event as a real popup), so without this set a page already bound to one tab id could be
  // handed out again as a different tab — see the pendingPages drain below.
  const bound = new Set([initialPage, ...pages.values()]);
  const pendingPages = [];
  // Chromium hands the foreground to every newly created tab. The runtime's own page switches
  // already reclaim it (see _settle), but a page the runtime never asked for — a site-opened
  // popup, a window.open from a timer — moves the foreground with no step boundary to hang a
  // reclaim off. Left unreclaimed, every later step drives a BACKGROUND tab: invisible under
  // watch, and with rAF/timers throttled, which stalls Playwright's actionability "stable"
  // wait. This flag is that missing signal; _settleIfSwitched consumes and clears it.
  // hostOwned/runId: when the context is a view borrowed from Execute's panel (see
  // host_browser.js), context.newPage() doesn't work (Electron has no Target.createTarget —
  // confirmed by the Stage-0 spike), so a user-opened (Ctrl+T-style) tab has to ask Execute
  // for a new view instead — see resolveStepPage's opened_by === "user" branch below.
  // Site-opened popups need none of this: Execute's own window-open handler creates the
  // sibling view, and it arrives through the same context "page" event either way.
  const registry = {
    context, pages, pendingPages, bound, foregroundStale: false, run,
    hostOwned: Boolean(opts.hostOwned), runId: opts.runId,
  };
  context.on("page", (page) => {
    pendingPages.push(page);
    registry.foregroundStale = true;
  });
  return registry;
}

/**
 * True only when a step names a real (non-tab_0) tab. Steps compiled before multi-tab support,
 * and every step recorded on the initial tab, carry no `tab` at all.
 */
function stepNamesTab(step) {
  const tab = step && step.tab;
  return !!(tab && typeof tab === "object" && tab.id && tab.id !== "tab_0");
}

// tab_open/tab_switch/popup are the only step types whose handler in run.js is a genuine no-op
// (see NOOP marker handlers there) — every other step type resolves its own target unconditionally.
const TAB_MARKER_TYPES = new Set(["tab_open", "tab_switch", "popup"]);

// Studio's test browser lays a transparent "hands off" page over the run's page (conxa-builder/electron/browser_panel.js).
// It is Studio's own page, never a tab anything opened: it must not be bound to a recorded tab or read as evidence.
function isShieldPage(page) {
  try { return String(page.url()).startsWith("about:blank?conxa_shield"); } catch (_) { return false; }
}

/**
 * True when a step is a tab-boundary marker that carries no `tab` block at all — a recorder
 * mis-stamp (e.g. a popup event attributed to whichever page happened to be active when the
 * event drained, not the page that actually fired it) rather than a real instruction to go to
 * tab_0. The caller should keep executing on whatever page it was already on rather than
 * resolving this step through the normal (tab_0) path.
 */
function stepInheritsPage(step) {
  const tab = step && step.tab;
  const namesAnyTab = !!(tab && typeof tab === "object" && tab.id);
  return !!(step && TAB_MARKER_TYPES.has(step.type) && !namesAnyTab);
}

/**
 * Resolve the live Page a step should run on. Never falls back to the currently-active page on
 * a miss — same rule as rootCandidates()/isFrameNotFound() in run.js for frame_chain: a
 * same-looking element on the wrong tab is worse than a clean, diagnosable failure. Throws an
 * Error with `.tabNotFound = true` when the step's tab cannot be resolved.
 *
 * opts.prevPage (the page the previous step ran on) gates settlement: any step that MOVES
 * execution to a different page — including returning to the initial page via an explicit or
 * implicit tab_0, the case that used to replay with no load wait and no bringToFront — gets
 * the full _settle treatment. Same-page steps skip it; run.js's waitForPageLoad already covers
 * their post-navigation load wait. The one exception is registry.foregroundStale — a page the
 * runtime does not own appeared and took the foreground, so even a same-page step settles, to
 * bring its own page back to the front (see createTabRegistry).
 */
async function resolveStepPage(registry, step, opts = {}) {
  if (!stepNamesTab(step)) {
    const initial = registry.pages.get("tab_0");
    if (!initial) {
      // Only reachable on a resumed run whose first tab has since closed: never act on another tab instead.
      throw Object.assign(new Error("Tab not found: step recorded on tab_0, which has since closed"), { tabNotFound: true });
    }
    return _settleIfSwitched(registry, initial, step, opts);
  }

  const tabId = step.tab.id;

  const existing = registry.pages.get(tabId);
  if (existing) {
    let closed = true;
    try { closed = existing.isClosed(); } catch (_) { closed = true; }
    if (!closed) return _settleIfSwitched(registry, existing, step, opts);
    // This tab WAS already resolved earlier in the run and is now closed — do not fall through
    // to the "never resolved yet" search below. That search binds whatever page opens/is
    // pending NEXT, with no check that it has anything to do with this tab; on a site with an
    // unrelated popup this silently rebinds a step to the wrong tab entirely, which is worse
    // than a clean, diagnosable failure (see the module header's invariant).
    const err = new Error(`Tab not found: step recorded on ${tabId} was open earlier in this run but has since closed`);
    err.tabNotFound = true;
    throw err;
  }

  let page = null;
  if (step.tab.opened_by === "user") {
    // Ctrl+T (or similar) — nothing on the recorded page ever opens this tab, so the runtime
    // must create it itself. The recorded first action on this tab is a navigate, which then
    // runs normally against the fresh page. This itself fires the context's "page" event, so it
    // lands in pendingPages too — bind it before that queue is ever drained, or a later
    // opened_by="site" step could hand this same blank page back out as a different tab.
    page = registry.hostOwned
      ? await hostBrowser.newTab({ context: registry.context, runId: registry.runId })
      : await registry.context.newPage();
    registry.bound.add(page);
  } else {
    // opened_by === "site" (a link/window.open) or unset (older compiles) — something on the
    // page is expected to open it. Drain the queue first (it may have fired before this step
    // ran), skipping any page already bound to a tab id or already closed — a bare shift() would
    // otherwise hand back a page this registry has already committed elsewhere (e.g. the blank
    // page created above for a user-opened tab).
    while (registry.pendingPages.length) {
      const candidate = registry.pendingPages.shift();
      if (registry.bound.has(candidate) || isShieldPage(candidate)) continue;
      let closed = false;
      try { closed = candidate.isClosed(); } catch (_) { closed = true; }
      if (closed) continue;
      page = candidate;
      break;
    }
    if (!page) {
      try {
        page = await registry.context.waitForEvent("page", {
          timeout: tabOpenTimeoutMs(),
          predicate: (p) => !registry.bound.has(p) && !isShieldPage(p),
        });
      } catch (_) {
        page = null;
      }
    }
    if (page) registry.bound.add(page);
  }

  if (!page) {
    const err = new Error(`Tab not found: step recorded on ${tabId} (opened_by=${step.tab.opened_by || "unknown"})`);
    err.tabNotFound = true;
    throw err;
  }

  registry.pages.set(tabId, page);
  TAB_TAGS.set(page, { run: registry.run, id: tabId });
  return _settleIfSwitched(registry, page, step, opts);
}

// Settle only when this step actually moves execution to a different page than the previous
// step ran on (opts.prevPage). A brand-new tab is always a switch (prevPage can't be it); a
// return to an already-open tab — most importantly back to tab_0, whose shortcut used to skip
// settlement entirely — now gets the load wait and, under watch mode, the bringToFront that
// makes the switch visible instead of firing clicks at a background tab.
// awaitNavigation: only a site-opened tab is ever navigated by something other than the
// runtime itself. tab_0 and a user-opened (Ctrl+T) tab are both navigated by the runtime's own
// very next step (the compiler always synthesizes/records a `navigate` for them) — waiting for
// an external navigation there burns the full settle timeout on a navigation that is never
// coming (this used to be the 60s+ hang before a workflow's own first `navigate` step, since a
// fresh tab_0 sits on about:blank with no `tab` block at all). A site-opened popup is expected
// to navigate itself a beat after creation, which is exactly the wait performed.
function _settleIfSwitched(registry, page, step, opts) {
  const prev = opts.prevPage;
  // Consume the flag whether or not it is used — it describes one foreground move, and the
  // very next _settle (this one, or the switch that made this an early return moot) reclaims it.
  const stale = registry.foregroundStale;
  registry.foregroundStale = false;
  if (page && prev === page && !stale) return Promise.resolve(page);
  return _settle(page, {
    ...opts,
    awaitNavigation: stepNamesTab(step) && step.tab.opened_by !== "user",
    // Something else took the foreground. Reclaim it regardless of watch mode: a backgrounded
    // page throttles rAF/timers in headless too, and the invisible switch is only the
    // watch-mode symptom of the same problem.
    forceFront: stale,
  });
}

async function _settle(page, opts) {
  const timeout = Number(opts.loadTimeoutMs) || DEFAULT_SETTLE_TIMEOUT_MS;
  // A target=_blank popup is created at about:blank and navigates to its real destination a
  // beat later — waitForLoadState("domcontentloaded") resolves instantly against that blank
  // page today, so the caller's first step on it can fire before anything has actually loaded.
  // Best-effort: a tab that genuinely stays blank still proceeds rather than hard-failing here.
  // Gated on opts.awaitNavigation (default false as of the _settleIfSwitched rewrite above) —
  // only a site-opened tab reaches here, and even a real popup navigates within a couple
  // seconds of opening, so this is capped to NAV_SETTLE_CAP_MS rather than the full settle
  // budget: a popup that hasn't navigated by then isn't going to.
  if (opts.awaitNavigation) {
    try {
      if (!page.url() || page.url() === "about:blank") {
        const navTimeout = Math.min(timeout, NAV_SETTLE_CAP_MS);
        if (opts.onPhase) opts.onPhase("settle_await_navigation_start");
        const _navT0 = Date.now();
        await page.waitForURL((u) => !!u && String(u) !== "about:blank", { timeout: navTimeout }).catch(() => {});
        if (opts.onPhase) opts.onPhase(`settle_await_navigation_done:${Date.now() - _navT0}ms`);
      }
    } catch (_) { /* page.url() itself can throw on a torn-down page — proceed to the load wait */ }
  }
  await page.waitForLoadState("domcontentloaded", { timeout }).catch(() => {});
  if (opts.watch || opts.forceFront) {
    if (opts.forceFront && opts.onPhase) opts.onPhase("foreground_reclaimed");
    await page.bringToFront().catch(() => {});
  }
  return page;
}

/**
 * Close every page in `openedTabs` other than anything in `keep` (a single Page or a Set).
 * EXEC-14: a multi-tab run can open pages beyond the one server.js already tracks as `page` —
 * in the headless/cached-context path (watch: false, the default), nothing else ever closes
 * them, so left untracked they leak for the life of the cached browser (browser.js's
 * per-company idle cache). Errors closing any one page (already closed, context torn down)
 * are swallowed — this is best-effort cleanup, not something that should fail an already
 * completed run.
 */
async function closeExtraTabs(openedTabs, keep) {
  const skip = keep instanceof Set ? keep : new Set(keep ? [keep] : []);
  for (const pg of openedTabs) {
    if (skip.has(pg)) continue;
    try {
      if (!pg.isClosed()) await pg.close();
    } catch (_) { /* best-effort */ }
  }
}

module.exports = { createTabRegistry, resolveStepPage, stepNamesTab, stepInheritsPage, tabOpenTimeoutMs, closeExtraTabs, isShieldPage };
