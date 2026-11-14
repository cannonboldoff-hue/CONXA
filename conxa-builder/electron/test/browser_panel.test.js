"use strict";
// browser_panel.js bookkeeping: run kinds, fixed-viewport letterboxing + emulation, popups adopted as
// tabs with their opener, self-closing popups, and which finished runs a new run drops.
// `electron` is stubbed — pure bookkeeping assertions, no real window needed.
const test = require("node:test");
const assert = require("node:assert");
const Module = require("module");

const cleared = [];
let targetSeq = 0;
class FakeWebContents {
  constructor() {
    this.targetId = `T${++targetSeq}`;
    this.url = "";
    this.handlers = {};
    this.openHandler = null;
    this.destroyed = false;
    this.commands = [];
    this.live = null; // what the page reports as [innerWidth, innerHeight, devicePixelRatio]; null = unreadable
    this.attached = false;
    this.zooms = [];
    this.debugger = {
      isAttached: () => this.attached,
      attach: () => { this.attached = true; },
      sendCommand: (method, params) => {
        this.commands.push({ method, params });
        if (method === "Runtime.evaluate") return Promise.resolve({ result: { value: this.live } });
        return Promise.resolve(method === "Target.getTargetInfo" ? { targetInfo: { targetId: this.targetId } } : {});
      },
    };
    this.navigationHistory = { canGoBack: () => false, canGoForward: () => false };
  }
  setZoomFactor(z) { this.zooms.push(z); }
  setWindowOpenHandler(fn) { this.openHandler = fn; }
  on(ev, fn) { this.handlers[ev] = fn; }
  once(ev, fn) { this.handlers[ev] = fn; }
  loadURL(u) { this.url = u; return Promise.resolve(); }
  focus() { this.focused = true; }
  reload() { this.reloaded = "soft"; }
  reloadIgnoringCache() { this.reloaded = "hard"; }
  getURL() { return this.url; }
  getTitle() { return ""; }
  isDestroyed() { return this.destroyed; }
  isLoading() { return false; }
  close() { this.destroy(); }
  destroy() { this.destroyed = true; if (this.handlers.destroyed) this.handlers.destroyed(); }
}
class FakeView {
  constructor(opts = {}) { this.webContents = opts.webContents || new FakeWebContents(); this.bounds = null; }
  setBounds(b) { this.bounds = b; }
}

const realLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === "electron") {
    return {
      session: { fromPartition: (p) => ({ clearStorageData: async () => { cleared.push(p); }, on: () => {} }) },
      WebContentsView: FakeView,
    };
  }
  return realLoad.call(this, request, ...rest);
};
const panel = require("../browser_panel");
Module._load = realLoad;

const added = [];
const win = { contentView: { addChildView: (v) => added.push(v), removeChildView: () => {} } };
let last = null;
const shortcuts = [];
panel.init(win, {
  onTabsChanged: (runId, tabs, meta) => { last = { runId, tabs, meta }; },
  onShortcut: (runId, action) => shortcuts.push({ runId, action }),
});

const lastMetrics = (wc) => wc.commands.filter((c) => c.method === "Emulation.setDeviceMetricsOverride").pop();

test("a test run is unchanged: no emulation, the view fills the panel's rect", () => {
  const { tabId } = panel.newView("t1");
  const view = added[added.length - 1];
  assert.strictEqual(last.meta.kind, "test");
  panel.setActiveBounds("t1", tabId, { x: 10, y: 20, width: 800, height: 600 });
  assert.deepStrictEqual(view.bounds, { x: 10, y: 20, width: 800, height: 600 });
  assert.strictEqual(view.webContents.commands.length, 0);
});

test("a fixed-viewport run emulates 1280x720 at DPR 1 from its first tab", () => {
  panel.newView("rec", { kind: "record", viewport: { width: 1280, height: 720 } });
  const wc = added[added.length - 1].webContents;
  assert.strictEqual(last.meta.kind, "record");
  assert.deepStrictEqual(lastMetrics(wc).params, { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false, scale: 1 });
  assert.deepStrictEqual(panel.scaleOf("rec"), { scale: 1, width: 1280, height: 720 });
});

test("a narrow panel letterboxes the view, centres it and shrinks the page by the same scale", () => {
  const tabId = last.tabs[0].id;
  const view = added[added.length - 1];
  panel.setActiveBounds("rec", tabId, { x: 100, y: 50, width: 640, height: 600 });
  // s = min(640/1280, 600/720, 1) = 0.5 -> 640x360, centred vertically in the 600px rect.
  assert.deepStrictEqual(view.bounds, { x: 100, y: 170, width: 640, height: 360 });
  assert.strictEqual(lastMetrics(view.webContents).params.scale, 0.5);
  assert.deepStrictEqual(panel.scaleOf("rec"), { scale: 0.5, width: 1280, height: 720 });
  // A panel larger than the page never enlarges it.
  panel.setActiveBounds("rec", tabId, { x: 0, y: 0, width: 2000, height: 1200 });
  assert.deepStrictEqual(panel.scaleOf("rec"), { scale: 1, width: 1280, height: 720 });
  assert.deepStrictEqual(view.bounds, { x: 360, y: 240, width: 1280, height: 720 });
});

test("a site-opened popup is adopted as a tab with its opener, at the run's size and scale", () => {
  const openerId = last.tabs[0].id;
  const opener = added[added.length - 1];
  panel.setActiveBounds("rec", openerId, { x: 0, y: 0, width: 960, height: 540 }); // s = 0.75
  const decision = opener.webContents.openHandler({ url: "https://example.com/pop" });
  assert.strictEqual(decision.action, "allow");
  const chromiumMade = new FakeWebContents();
  assert.strictEqual(decision.createWindow({ webContents: chromiumMade, webPreferences: {} }), chromiumMade);
  assert.strictEqual(last.tabs.length, 2);
  const pop = last.tabs[1];
  assert.strictEqual(pop.opener, openerId);
  assert.strictEqual(pop.active, true, "the popup comes to the front, as in a browser");
  assert.strictEqual(lastMetrics(chromiumMade).params.scale, 0.75);
  assert.strictEqual(chromiumMade.url, "", "a popup is not sent to a marker — Chromium navigates it itself");
  assert.deepStrictEqual(opener.bounds, { x: 0, y: 0, width: 0, height: 0 }, "the opener is parked");
});

test("a popup that closes itself drops its tab and hands the panel back to its opener", () => {
  const [openerTab, popTab] = last.tabs;
  const popWc = added[added.length - 1].webContents;
  popWc.destroy();
  assert.strictEqual(last.tabs.length, 1);
  assert.strictEqual(last.tabs[0].id, openerTab.id);
  assert.strictEqual(last.tabs[0].active, true);
  assert.notStrictEqual(popTab.id, openerTab.id);
});

test("a new run drops a finished test but never a finished record or sign-in run", async () => {
  await panel.runEnd("t1");
  await panel.runEnd("rec");
  panel.newView("t2");
  await new Promise((r) => setImmediate(r));
  assert.ok(cleared.includes("persist:conxa-test-cache")); // t1 held the shared test partition
  assert.ok(!cleared.includes("conxa-run-rec"));
  await panel.closeRun("rec");
  assert.ok(cleared.includes("conxa-run-rec"));
  assert.throws(() => panel.scaleOf("rec"));
});

test("a record run lists its tabs' devtools target ids, popups included; a test run has none", async () => {
  const { tabId } = panel.newView("rec2", { kind: "record", viewport: { width: 1280, height: 720 } });
  const wc = added[added.length - 1].webContents;
  const popWc = new FakeWebContents();
  wc.openHandler({ url: "" }).createWindow({ webContents: popWc, webPreferences: {} });
  const { targets } = await panel.targetsOf("rec2");
  assert.deepStrictEqual(targets.map((t) => t.targetId), [wc.targetId, popWc.targetId]);
  assert.strictEqual(targets[1].opener, tabId);
  panel.newView("t3");
  assert.deepStrictEqual((await panel.targetsOf("t3")).targets, []);
  await panel.closeRun("rec2");
});

test("unknown kinds and empty viewports are refused", () => {
  assert.throws(() => panel.newView("bad", { kind: "other" }));
  assert.throws(() => panel.newView("bad2", { kind: "record", viewport: { width: 0, height: 720 } }));
});

const key = (wc, key, mods = {}) => {
  let prevented = false;
  wc.handlers["before-input-event"]({ preventDefault: () => { prevented = true; } }, { type: "keyDown", key, control: false, shift: false, alt: false, meta: false, ...mods });
  return prevented;
};

test("the + button opens a person's tab at the front, marked as theirs and found by its marker", () => {
  panel.newView("b1", { kind: "record", viewport: { width: 1280, height: 720 } });
  panel.setActiveBounds("b1", last.tabs[0].id, { x: 0, y: 0, width: 1280, height: 720 });
  const { tabId } = panel.personTab("b1");
  const t = last.tabs.find((x) => x.id === tabId);
  assert.strictEqual(t.openedBy, "person");
  assert.strictEqual(t.active, true);
  assert.strictEqual(t.opener, null);
  assert.ok(added[added.length - 1].webContents.url.startsWith("about:blank?conxa_run=b1"), "the recorder still finds it");
  assert.strictEqual(last.tabs[0].openedBy, "initial");
});

test("browser keys inside the page: Ctrl+T, Ctrl+L, Ctrl+Tab, Ctrl+1, Ctrl+R / Ctrl+Shift+R, Ctrl+W", async () => {
  const wc = added[added.length - 1].webContents;
  const before = last.tabs.length;
  assert.ok(key(wc, "t", { control: true }));
  assert.strictEqual(last.tabs.length, before + 1);
  assert.deepStrictEqual(shortcuts.pop(), { runId: "b1", action: "focus-address" });
  assert.ok(key(wc, "l", { control: true }));
  assert.deepStrictEqual(shortcuts.pop(), { runId: "b1", action: "focus-address" });
  const newest = added[added.length - 1].webContents;
  assert.ok(key(newest, "Tab", { control: true }));
  assert.strictEqual(last.tabs.find((t) => t.active).id, last.tabs[0].id, "wraps to the first tab");
  assert.ok(key(newest, "1", { control: true }));
  assert.strictEqual(last.tabs.find((t) => t.active).id, last.tabs[0].id);
  assert.ok(key(wc, "r", { control: true }));
  assert.strictEqual(wc.reloaded, "soft");
  assert.ok(key(wc, "R", { control: true, shift: true }));
  assert.strictEqual(wc.reloaded, "hard");
  assert.ok(!key(wc, "a"), "ordinary typing reaches the page");
  const n = last.tabs.length;
  assert.ok(key(newest, "w", { control: true }));
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(last.tabs.length, n - 1);
});

test("closing the active tab hands the panel to its neighbour and focuses it", async () => {
  const [, second] = last.tabs;
  panel.selectTab("b1", second.id);
  await panel.closeTab("b1", second.id);
  const active = last.tabs.find((t) => t.active);
  assert.ok(active && active.id !== second.id);
  await panel.closeRun("b1");
});

test("the address bar takes a site, a host, localhost or a search, as Chrome does", () => {
  panel.newView("nav", {});
  const tabId = last.tabs[0].id;
  const wc = added[added.length - 1].webContents;
  const go = (url) => { panel.navigate("nav", tabId, { action: "load", url }); return wc.url; };
  assert.strictEqual(go("https://a.test/x"), "https://a.test/x");
  assert.strictEqual(go("example.com/path"), "https://example.com/path");
  assert.strictEqual(go("localhost:3000"), "http://localhost:3000");
  assert.strictEqual(go("192.168.1.5"), "http://192.168.1.5");
  assert.strictEqual(go("best pizza near me"), "https://www.google.com/search?q=best%20pizza%20near%20me");
  assert.strictEqual(go("invoices"), "https://www.google.com/search?q=invoices");
  assert.ok(wc.focused, "typing an address hands the keyboard to the page");
});

test("adopting a parked test run makes it a record run: kept views, target ids, fixed viewport, never dropped", async () => {
  const { tabId } = panel.newView("adopt1");
  panel.runEnd && await panel.runEnd("adopt1");                        // the runtime let go of it: finished
  const wc = added[added.length - 1].webContents;
  assert.strictEqual(last.meta.finished, true);
  await assert.rejects(() => panel.targetsOf("adopt1").then((t) => { if (!t.targets.length) throw new Error("no targets"); }));  // a test tab has none
  const got = await panel.adoptRun("adopt1", { kind: "record", viewport: { width: 1280, height: 720 } });
  assert.strictEqual(got.tabId, tabId);
  assert.strictEqual(got.targetId, wc.targetId);
  assert.strictEqual(last.meta.kind, "record");
  assert.strictEqual(last.meta.finished, false);
  assert.deepStrictEqual(lastMetrics(wc).params.width, 1280);
  assert.deepStrictEqual((await panel.targetsOf("adopt1")).targets.map((t) => t.targetId), [wc.targetId]);
  panel.newView("adopt2");                                             // a newer run must not drop the adopted one
  await new Promise((r) => setTimeout(r, 10));
  assert.ok((await panel.targetsOf("adopt1")).targets.length === 1);
  await assert.rejects(() => panel.adoptRun("nope"), /no run nope/);
  await assert.rejects(() => panel.adoptRun("adopt1", { kind: "test" }), /cannot adopt/);
});

test("test runs share one persistent partition (warm cache), wiped between runs; others never use it", async () => {
  // earlier tests leave runs open, so use a fresh copy of the module
  const modPath = require.resolve("../browser_panel");
  delete require.cache[modPath];
  Module._load = function (request, ...rest) {
    if (request === "electron") return { session: { fromPartition: (p) => ({ clearStorageData: async () => { cleared.push(p); }, on: () => {} }) }, WebContentsView: FakeView };
    return realLoad.call(this, request, ...rest);
  };
  const panel = require("../browser_panel");
  Module._load = realLoad;
  panel.init(win, {});
  cleared.length = 0;
  panel.newView("s1");
  panel.newView("s2");                                   // overlaps live s1 -> own in-memory partition
  panel.newView("rec-s", { kind: "record", viewport: { width: 1280, height: 720 } });
  await panel.closeRun("s2");
  await panel.closeRun("rec-s");
  assert.deepStrictEqual(cleared, ["conxa-run-s2", "conxa-run-rec-s"]);
  cleared.length = 0;
  await panel.closeRun("s1");
  assert.deepStrictEqual(cleared, ["persist:conxa-test-cache"]);
  // a finished test is dropped and the shared jar wiped before the next test starts
  panel.newView("s3");
  await panel.runEnd("s3");
  cleared.length = 0;
  await panel.prepareTestRun();
  assert.deepStrictEqual(cleared, ["persist:conxa-test-cache", "persist:conxa-test-cache"]);
  panel.newView("s4");
  await panel.closeRun("s4");
});

const settle = () => new Promise((r) => setImmediate(r));
const has = (wc, method) => wc.commands.some((c) => c.method === method);

test("a test page that becomes ready at the wrong size is cleared and zoomed again, at the run's current scale", async () => {
  const { tabId } = panel.newView("reapply", { viewport: { width: 1280, height: 720 } });
  const view = added[added.length - 1];
  panel.setActiveBounds("reapply", tabId, { x: 0, y: 0, width: 640, height: 600 }); // s = 0.5
  const wc = view.webContents;
  assert.strictEqual(wc.zooms[wc.zooms.length - 1], 0.5, "a test run shrinks with the view's zoom: `scale` breaks CDP clicks, a device scale factor leaves the page clipped");
  for (const ev of ["dom-ready", "did-finish-load"]) {
    for (const live of [[1024, 576, 1], null]) { // drifted size, unreadable
      wc.live = live;
      wc.commands.length = 0;
      wc.zooms.length = 0;
      wc.handlers[ev]();
      await settle();
      assert.ok(has(wc, "Emulation.clearDeviceMetricsOverride"), "clears Playwright's override first");
      assert.ok(!has(wc, "Emulation.setDeviceMetricsOverride"), "a test run sets no metrics override");
      assert.deepStrictEqual(wc.zooms, [0.5]);
    }
  }
});

test("a page that is already at the recorded size is left alone", async () => {
  const { tabId } = panel.newView("reapply-ok", { viewport: { width: 1280, height: 720 } });
  const wc = added[added.length - 1].webContents;
  panel.setActiveBounds("reapply-ok", tabId, { x: 0, y: 0, width: 640, height: 600 });
  wc.live = [1280, 720, 0.5]; // a zoomed test page reads the screen's DPR times the zoom
  wc.commands.length = 0;
  wc.handlers["dom-ready"]();
  await settle();
  assert.ok(!has(wc, "Emulation.clearDeviceMetricsOverride") && !has(wc, "Emulation.setDeviceMetricsOverride"));
});

test("ensure_geometry repairs every tab of the run, hidden ones too, and says whether it held", async () => {
  panel.newView("ensure", { viewport: { width: 1280, height: 720 } });
  const first = added[added.length - 1].webContents;
  panel.newTab("ensure", {});
  const second = added[added.length - 1].webContents;
  first.live = [1024, 576, 1];
  second.live = [1280, 720, 1];
  first.commands.length = 0; second.commands.length = 0;
  const r = await panel.ensureGeometry("ensure");
  assert.strictEqual(r.tabs, 2);
  assert.ok(has(first, "Emulation.clearDeviceMetricsOverride"), "the drifted tab was repaired");
  assert.ok(!has(second, "Emulation.clearDeviceMetricsOverride"), "the healthy tab was not touched");
  assert.strictEqual(r.ok, false, "the fake still reports the old size after the repair, so it did not hold");
  await assert.rejects(() => panel.ensureGeometry("no-such-run"), /no run/);
});

const FIT = { width: 1280, height: 720, fit: "width" };

test("a fit-to-width run fills the rect: the page stays 1280 wide and its height follows the pane", () => {
  const { tabId } = panel.newView("fit1", { kind: "record", viewport: FIT });
  const view = added[added.length - 1];
  const rect = { x: 30, y: 40, width: 920, height: 800 };
  panel.setActiveBounds("fit1", tabId, rect);
  assert.deepStrictEqual(view.bounds, rect, "no bars: the view is the whole rect");
  const m = lastMetrics(view.webContents).params;
  assert.strictEqual(m.width, 1280);
  assert.strictEqual(m.height, 1113); // 800 / (920 / 1280)
  assert.ok(Math.abs(m.scale - 0.71875) < 1e-9);
  assert.deepStrictEqual(panel.scaleOf("fit1"), { scale: m.scale, width: 1280, height: 1113 });
  // The pane is dragged wider: the same page, drawn larger, a little shorter. Above 1 is allowed.
  panel.setActiveBounds("fit1", tabId, { x: 0, y: 0, width: 1600, height: 900 });
  assert.strictEqual(lastMetrics(view.webContents).params.height, 720);
  assert.strictEqual(lastMetrics(view.webContents).params.scale, 1.25);
});

test("a fit-to-width run keeps its page between 480 and 1440 tall", () => {
  const { tabId } = panel.newView("fit2", { kind: "record", viewport: FIT });
  const view = added[added.length - 1];
  // Short and wide: height pinned to 480, the page shrinks to fit and is centred sideways.
  panel.setActiveBounds("fit2", tabId, { x: 0, y: 0, width: 1280, height: 240 });
  assert.strictEqual(lastMetrics(view.webContents).params.height, 480);
  assert.strictEqual(lastMetrics(view.webContents).params.scale, 0.5);
  assert.deepStrictEqual(view.bounds, { x: 320, y: 0, width: 640, height: 240 });
  // Tall and narrow: height capped at 1440, page top-anchored, a bar only at the bottom.
  panel.setActiveBounds("fit2", tabId, { x: 0, y: 0, width: 400, height: 1200 });
  assert.strictEqual(lastMetrics(view.webContents).params.height, 1440);
  assert.deepStrictEqual(view.bounds, { x: 0, y: 0, width: 400, height: Math.round(1440 * (400 / 1280)) });
});

test("a folded (zero-size) rect leaves a fit-to-width run's geometry alone", () => {
  const { tabId } = panel.newView("fit3", { kind: "record", viewport: FIT });
  const wc = added[added.length - 1].webContents;
  panel.setActiveBounds("fit3", tabId, { x: 0, y: 0, width: 640, height: 600 });
  wc.commands.length = 0;
  panel.setActiveBounds("fit3", tabId, { x: 0, y: 0, width: 0, height: 0 });
  assert.strictEqual(wc.commands.length, 0);
  assert.strictEqual(panel.scaleOf("fit3").scale, 0.5);
});

test("a viewport without fit still letterboxes, and an unknown fit value is ignored", () => {
  const { tabId } = panel.newView("fit4", { kind: "record", viewport: { width: 1280, height: 720, fit: "stretch" } });
  const view = added[added.length - 1];
  panel.setActiveBounds("fit4", tabId, { x: 0, y: 0, width: 640, height: 600 });
  assert.deepStrictEqual(view.bounds, { x: 0, y: 120, width: 640, height: 360 });
});

test("adopting a parked run into a fit-to-width viewport lays its page out from the last rect", async () => {
  const { tabId } = panel.newView("fit5");
  const view = added[added.length - 1];
  panel.setActiveBounds("fit5", tabId, { x: 0, y: 0, width: 640, height: 600 }); // a test run: full rect, no emulation
  assert.deepStrictEqual(view.bounds, { x: 0, y: 0, width: 640, height: 600 });
  await panel.adoptRun("fit5", { kind: "record", viewport: FIT });
  assert.deepStrictEqual(view.bounds, { x: 0, y: 0, width: 640, height: 600 });
  assert.strictEqual(lastMetrics(view.webContents).params.height, 1200); // 600 / 0.5
  assert.strictEqual(lastMetrics(view.webContents).params.scale, 0.5);
});

test("ensure_geometry compares against the run's live height, not the base one", async () => {
  const { tabId } = panel.newView("fit6", { kind: "record", viewport: FIT });
  const wc = added[added.length - 1].webContents;
  panel.setActiveBounds("fit6", tabId, { x: 0, y: 0, width: 920, height: 800 });
  wc.live = [1280, 1113, 1];
  wc.commands.length = 0;
  const r = await panel.ensureGeometry("fit6");
  assert.strictEqual(r.ok, true);
  assert.ok(!has(wc, "Emulation.clearDeviceMetricsOverride"), "a page at its live height is left alone");
});

test("tour hint is allowed only in sign-in runs on the Conxa website host", () => {
  process.env.CONXA_WEB_ORIGIN = "https://www.conxa.in";
  const wc = (url) => ({ getURL: () => url });
  panel.setTourHint({ target: "", text: "Sign in here" });
  assert.strictEqual(panel._tourAllowed({ kind: "signin" }, wc("https://www.conxa.in/sign-in")), true);
  assert.strictEqual(panel._tourAllowed({ kind: "record" }, wc("https://www.conxa.in/practice")), false);
  assert.strictEqual(panel._tourAllowed({ kind: "test" }, wc("https://www.conxa.in/practice")), false);
  assert.strictEqual(panel._tourAllowed({ kind: "signin" }, wc("https://evil.example/sign-in")), false);
  panel.setTourHint(null);
  assert.strictEqual(panel._tourAllowed({ kind: "signin" }, wc("https://www.conxa.in/sign-in")), false);
});

// ── input shield: the person's mouse and keys stay off a page the runtime is driving ──────────────────────────
// The shield is the FIRST view a test run adds (before its tab). `shieldOf` finds it by its marker URL.
const shieldOf = (viewList) => viewList.filter((v) => String(v.webContents.url).startsWith("about:blank?conxa_shield")).pop();
const RECT = { x: 10, y: 20, width: 800, height: 600 };

test("a test run covers its page with a transparent shield the same size as the page, above the tab", () => {
  const { tabId } = panel.newView("sh1");
  const tabView = added[added.length - 1];
  panel.setActiveBounds("sh1", tabId, RECT);
  const shield = shieldOf(added);
  assert.ok(shield, "a shield was created");
  assert.deepStrictEqual(shield.bounds, RECT);
  assert.deepStrictEqual(tabView.bounds, RECT);
  assert.strictEqual(added[added.length - 1], shield, "re-added last = top-most");
  assert.strictEqual(shield.webContents.focused, true, "keyboard focus goes to the shield, not the page");
});

test("record and sign-in runs never get a shield: the person drives them", () => {
  const before = added.length;
  panel.newView("sh-rec", { kind: "record", viewport: { width: 1280, height: 720 } });
  assert.strictEqual(added.slice(before).filter((v) => String(v.webContents.url).startsWith("about:blank?conxa_shield")).length, 0);
});

test("a person's own tab in a test run is never covered", () => {
  const { tabId } = panel.newView("sh2");
  panel.setActiveBounds("sh2", tabId, RECT);
  const shield = shieldOf(added);
  const { tabId: mine } = panel.personTab("sh2");
  assert.deepStrictEqual(shield.bounds, { x: 0, y: 0, width: 0, height: 0 }, "hidden while the person's tab is in front");
  panel.selectTab("sh2", tabId);
  assert.deepStrictEqual(shield.bounds, RECT, "back over the run's own tab");
  assert.ok(mine);
});

test("the shield is lifted when the run ends, so the person can check the final page", async () => {
  const { tabId } = panel.newView("sh3");
  panel.setActiveBounds("sh3", tabId, RECT);
  const shield = shieldOf(added);
  await panel.runEnd("sh3");
  assert.strictEqual(shield.webContents.destroyed, true);
});

test("adopting a test run for the recorder lifts the shield", async () => {
  const { tabId } = panel.newView("sh4");
  panel.setActiveBounds("sh4", tabId, RECT);
  const shield = shieldOf(added);
  await panel.adoptRun("sh4", { kind: "record", viewport: { width: 1280, height: 720 } });
  assert.strictEqual(shield.webContents.destroyed, true);
});

test("hiding the panel parks the shield too, and closing the run removes it", async () => {
  const { tabId } = panel.newView("sh5");
  panel.setActiveBounds("sh5", tabId, RECT);
  const shield = shieldOf(added);
  panel.hideAll();
  assert.deepStrictEqual(shield.bounds, { x: 0, y: 0, width: 0, height: 0 });
  await panel.closeRun("sh5");
  assert.strictEqual(shield.webContents.destroyed, true);
});

test("a hand-over lifts the shield off the page and puts it back", () => {
  const { tabId } = panel.newView("sh6");
  panel.setActiveBounds("sh6", tabId, RECT);
  const shield = shieldOf(added);
  const tabView = added.filter((v) => !String(v.webContents.url).startsWith("about:blank?conxa_shield")).pop();
  panel.setInputOpen("sh6", true);
  assert.deepStrictEqual(shield.bounds, { x: 0, y: 0, width: 0, height: 0 }, "the person can click the page");
  assert.strictEqual(tabView.webContents.focused, true, "and type into it");
  panel.setInputOpen("sh6", false);
  assert.deepStrictEqual(shield.bounds, RECT, "back over the page when the person is done");
  assert.deepStrictEqual(panel.setInputOpen("no-such-run", true), { ok: false });
});

test("a shield put back while the panel is hidden does not reappear over something else", () => {
  const { tabId } = panel.newView("sh7");
  panel.setActiveBounds("sh7", tabId, RECT);
  const shield = shieldOf(added);
  panel.setInputOpen("sh7", true);
  panel.hideAll();
  panel.setInputOpen("sh7", false);
  assert.deepStrictEqual(shield.bounds, { x: 0, y: 0, width: 0, height: 0 }, "the panel is hidden; there is nothing to cover");
});
