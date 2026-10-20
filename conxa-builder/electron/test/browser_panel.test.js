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
    this.attached = false;
    this.debugger = {
      isAttached: () => this.attached,
      attach: () => { this.attached = true; },
      sendCommand: (method, params) => {
        this.commands.push({ method, params });
        return Promise.resolve(method === "Target.getTargetInfo" ? { targetInfo: { targetId: this.targetId } } : {});
      },
    };
    this.navigationHistory = { canGoBack: () => false, canGoForward: () => false };
  }
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
  assert.deepStrictEqual(panel.scaleOf("rec"), { scale: 1 });
});

test("a narrow panel letterboxes the view, centres it and shrinks the page by the same scale", () => {
  const tabId = last.tabs[0].id;
  const view = added[added.length - 1];
  panel.setActiveBounds("rec", tabId, { x: 100, y: 50, width: 640, height: 600 });
  // s = min(640/1280, 600/720, 1) = 0.5 -> 640x360, centred vertically in the 600px rect.
  assert.deepStrictEqual(view.bounds, { x: 100, y: 170, width: 640, height: 360 });
  assert.strictEqual(lastMetrics(view.webContents).params.scale, 0.5);
  assert.deepStrictEqual(panel.scaleOf("rec"), { scale: 0.5 });
  // A panel larger than the page never enlarges it.
  panel.setActiveBounds("rec", tabId, { x: 0, y: 0, width: 2000, height: 1200 });
  assert.deepStrictEqual(panel.scaleOf("rec"), { scale: 1 });
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
  assert.ok(cleared.includes("conxa-run-t1"));
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
