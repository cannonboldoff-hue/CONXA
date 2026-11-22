"use strict";
// browser_panel.js bookkeeping: per-tab close (a finished login must not take its sibling
// login views down), last-tab-out ending the run, and the address-bar navigate mapping.
// `electron` is stubbed — these are pure bookkeeping assertions, no real window needed.
const test = require("node:test");
const assert = require("node:assert");
const Module = require("module");
const fs = require("fs");
const os = require("os");
const path = require("path");

// AUTH-6: loginDone writes a real file — point CONXA_DIR at a throwaway temp dir so this
// test never touches a developer's actual ~/.conxa.
const _tmpConxaDir = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-execute-browser-panel-test-"));
process.env.CONXA_DIR = _tmpConxaDir;

const cleared = [];
const downloadHandlers = {}; // partition -> will-download listener
class FakeWebContents {
  constructor() {
    this.url = "";
    this.calls = [];
    this.handlers = {};
    this.emulations = []; // Emulation.setDeviceMetricsOverride params, in order
    this.zoom = 1; // setZoomFactor, the way a driven run is shrunk to fit the pane
    this.title = "";
    this.methods = []; // every CDP method sent, in order
    this.live = null; // what the page reports as [innerWidth, innerHeight, devicePixelRatio]; null = unreadable
    this.debugger = {
      isAttached: () => true,
      attach() {},
      sendCommand: (m, p) => {
        this.methods.push(m);
        if (m === "Emulation.setDeviceMetricsOverride") this.emulations.push(p);
        if (m === "Runtime.evaluate") return Promise.resolve({ result: { value: this.live } });
        return Promise.resolve({});
      },
    };
    this.navigationHistory = {
      canGoBack: () => true,
      canGoForward: () => false,
      goBack: () => this.calls.push("back"),
      goForward: () => this.calls.push("forward"),
    };
  }
  setWindowOpenHandler(fn) { this.openHandler = fn; }
  capturePage(rect, opts) {
    this.captured = { rect, opts };
    return Promise.resolve({ toJPEG: (q) => Buffer.from(`jpeg${q}`), toPNG: () => Buffer.from("png") });
  }
  executeJavaScript() { return Promise.resolve(); }
  focus() { this.focused = true; }
  on(ev, fn) { this.handlers[ev] = fn; }
  loadURL(u) { this.url = u; this.calls.push(`load:${u}`); return Promise.resolve(); }
  getURL() { return this.url; }
  getTitle() { return this.title; }
  setZoomFactor(z) { this.zoom = z; }
  isDestroyed() { return false; }
  isLoading() { return false; }
  reload() { this.calls.push("reload"); }
  stop() { this.calls.push("stop"); }
  close() { this.closed = true; }
}
class FakeView {
  constructor() { this.webContents = new FakeWebContents(); this.bounds = null; this.visible = true; }
  setBounds(b) { this.bounds = b; }
  setVisible(v) { this.visible = v; }
}

const realLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === "electron") {
    return {
      session: {
        fromPartition: (p) => ({
          clearStorageData: async () => { cleared.push(p); },
          on: (ev, fn) => { if (ev === "will-download") downloadHandlers[p] = fn; },
        }),
      },
      WebContentsView: FakeView,
    };
  }
  return realLoad.call(this, request, ...rest);
};
const panel = require("../browser_panel");
Module._load = realLoad;

const removed = [];
const added = [];
const win = { contentView: { addChildView: (v) => added.push(v), removeChildView: (v) => removed.push(v) } };
let last = null; // last onTabsChanged emit: { runId, tabs, meta }
panel.init(win, { onTabsChanged: (runId, tabs, meta) => { last = { runId, tabs, meta }; } });

test("newView returns the tabId and carries the label into the tab descriptor", () => {
  const { markerUrl, tabId } = panel.newView("run-a", { label: "GitHub" });
  assert.ok(markerUrl.includes(`conxa_tab=${tabId}`));
  assert.strictEqual(last.tabs.length, 1);
  assert.strictEqual(last.tabs[0].label, "GitHub");
  assert.strictEqual(last.tabs[0].url, "", "the internal marker URL must never reach the address bar");
});

test("the marker page never becomes a tab label", () => {
  panel.newView("run-marker");
  const wc = added[added.length - 1].webContents;
  wc.title = "about:blank?conxa_run=run-marker&conxa_tab=ab12"; // what the blank marker page calls itself
  wc.handlers["page-title-updated"]();
  assert.strictEqual(last.tabs[0].label, "New tab");
  wc.url = "https://app.test/home";
  wc.title = "Home";
  wc.handlers["page-title-updated"]();
  assert.strictEqual(last.tabs[0].label, "Home");
});

test("a run with no pane rect yet is held at 1280 x height by an override at scale 1, and moves to zoom (override cleared) once placed", async () => {
  // Runs before any test reports a pane rect, so the module has no "last rect" to place the tab from.
  const vp = { width: 1280, height: 720, fit: "width" };
  const { tabId } = panel.newView("run-unplaced", { viewport: vp });
  const wc = added[added.length - 1].webContents;
  assert.deepStrictEqual(wc.emulations, [{ width: 1280, height: 720, deviceScaleFactor: 1, mobile: false, scale: 1 }]);
  assert.strictEqual(wc.zoom, 1);
  panel.setActiveBounds("run-unplaced", tabId, { x: 0, y: 0, width: 640, height: 600 }); // zoom 0.5, height 1200
  await settle();
  assert.strictEqual(wc.zoom, 0.5);
  assert.ok(wc.methods.includes("Emulation.clearDeviceMetricsOverride"), "the override is cleared once the pane places it");
  // a minimized window lays views out at 0x0: the page is held by an override at zoom 1, and comes back at restore
  wc.methods.length = 0; wc.emulations.length = 0;
  await panel.setMinimized(true);
  assert.strictEqual(wc.zoom, 1);
  assert.deepStrictEqual(wc.emulations, [{ width: 1280, height: 1200, deviceScaleFactor: 1, mobile: false, scale: 1 }]);
  wc.methods.length = 0; wc.emulations.length = 0;
  await panel.setMinimized(false);
  assert.ok(wc.methods.includes("Emulation.clearDeviceMetricsOverride"));
  assert.deepStrictEqual(wc.emulations, [], "no override while the run is on screen");
  assert.strictEqual(wc.zoom, 0.5);
});

test("a driven run is shrunk with the view's zoom, never an emulation scale; width 1280, height follows the pane (480-1440)", () => {
  const vp = { width: 1280, height: 720, fit: "width" };
  const { tabId } = panel.newView("run-vp", { viewport: vp });
  const first = added[added.length - 1];
  panel.setActiveBounds("run-vp", tabId, { x: 10, y: 20, width: 640, height: 600 });
  // 640/1280 = 0.5: the page lays out 1280 wide and 1200 tall, drawn in the whole pane
  assert.deepStrictEqual(first.bounds, { x: 10, y: 20, width: 640, height: 600 });
  assert.strictEqual(first.webContents.zoom, 0.5);
  const tab2 = panel.newTab("run-vp", { label: "second" });
  const second = added[added.length - 1];
  assert.strictEqual(second.webContents.zoom, 0.5, "a new tab is placed at once from the run's pane, not left at 0x0");
  assert.deepStrictEqual(second.bounds, { x: 10, y: 20, width: 640, height: 600 });
  // a short pane keeps the page 480 tall (shrunk further, centred); a tall one stops at 1440
  panel.setActiveBounds("run-vp", tab2.tabId, { x: 0, y: 0, width: 640, height: 200 });
  assert.deepStrictEqual(second.bounds, { x: 53, y: 0, width: 533, height: 200 });
  assert.ok(Math.abs(second.webContents.zoom - 200 / 480) < 1e-9);
  assert.strictEqual(first.webContents.zoom, second.webContents.zoom, "every tab of the run shares one zoom");
  panel.setActiveBounds("run-vp", tab2.tabId, { x: 0, y: 0, width: 1280, height: 3000 });
  assert.deepStrictEqual(second.bounds, { x: 0, y: 0, width: 1280, height: 1440 });
  assert.strictEqual(second.webContents.zoom, 1);
  for (const v of [first, second]) {
    assert.ok(v.webContents.emulations.every((e) => e.scale === 1), "an emulation scale is never sent: CDP input is not mapped through it");
  }
});

test("a viewport without fit (an older runtime) still letterboxes, with zoom, and is never enlarged", () => {
  const { tabId } = panel.newView("run-nofit", { viewport: { width: 1280, height: 720 } });
  const view = added[added.length - 1];
  panel.setActiveBounds("run-nofit", tabId, { x: 10, y: 20, width: 640, height: 600 });
  assert.deepStrictEqual(view.bounds, { x: 10, y: 20 + 120, width: 640, height: 360 });
  assert.strictEqual(view.webContents.zoom, 0.5);
  panel.setActiveBounds("run-nofit", tabId, { x: 0, y: 0, width: 2000, height: 1500 });
  assert.deepStrictEqual(view.bounds, { x: 360, y: 390, width: 1280, height: 720 });
  assert.strictEqual(view.webContents.zoom, 1);
});

test("a zero rect hides the views and leaves every size as it was", () => {
  const { tabId } = panel.newView("run-zero", { viewport: { width: 1280, height: 720, fit: "width" } });
  const view = added[added.length - 1];
  panel.setActiveBounds("run-zero", tabId, { x: 0, y: 0, width: 640, height: 600 });
  assert.strictEqual(view.visible, true);
  const before = view.bounds;
  panel.setActiveBounds("run-zero", tabId, { x: 0, y: 0, width: 0, height: 0 });
  assert.strictEqual(view.visible, false);
  assert.deepStrictEqual(view.bounds, before, "hidden, never shrunk to 0x0");
  assert.strictEqual(view.webContents.zoom, 0.5);
});

test("a hidden view keeps its bounds (setVisible false) instead of being shrunk to 0x0", () => {
  const { tabId } = panel.newView("run-hide");
  const view = added[added.length - 1];
  const rect = { x: 5, y: 5, width: 300, height: 200 };
  panel.setActiveBounds("run-hide", tabId, rect);
  panel.hideAll();
  assert.strictEqual(view.visible, false);
  assert.deepStrictEqual(view.bounds, rect);
  panel.setActiveBounds("run-hide", tabId, rect);
  assert.strictEqual(view.visible, true);
});

test("a run without a viewport fills the panel rect as before", () => {
  const { tabId } = panel.newView("run-free");
  const view = added[added.length - 1];
  panel.setActiveBounds("run-free", tabId, { x: 1, y: 2, width: 300, height: 400 });
  assert.deepStrictEqual(view.bounds, { x: 1, y: 2, width: 300, height: 400 });
  assert.deepStrictEqual(view.webContents.emulations, []);
});

test("closeTab on a two-tab run closes only that view and promotes the survivor", async () => {
  const github = panel.newTab("run-a", { label: "Google Drive" }); // second tab; becomes active
  assert.strictEqual(last.tabs.length, 2);
  const before = removed.length;
  await panel.closeTab("run-a", github.tabId);
  assert.strictEqual(removed.length, before + 1, "exactly one view removed");
  assert.strictEqual(last.tabs.length, 1);
  assert.strictEqual(last.tabs[0].label, "GitHub", "the sibling login survives");
  assert.strictEqual(last.tabs[0].active, true, "the survivor is promoted to active");
  assert.deepStrictEqual(cleared, [], "the run's partition must not be wiped while a tab remains");
});

test("closeTab on the last tab runs the full run-end cleanup", async () => {
  const only = last.tabs[0].id;
  await panel.closeTab("run-a", only);
  assert.deepStrictEqual(cleared, ["conxa-run-run-a"]);
  assert.deepStrictEqual(last.tabs, [], "an empty emit tells the renderer to drop the run");
});

test("navigate maps each action onto the right webContents call and normalises typed URLs", () => {
  const { tabId } = panel.newView("run-b");
  const wc = added[added.length - 1].webContents;
  wc.calls.length = 0;
  assert.strictEqual(panel.navigate("run-b", tabId, { action: "load", url: "example.com/x" }), true);
  assert.strictEqual(panel.navigate("run-b", tabId, { action: "load", url: "http://a.test" }), true);
  assert.strictEqual(panel.navigate("run-b", tabId, { action: "load", url: "   " }), false, "blank input is a no-op");
  for (const action of ["back", "forward", "reload", "stop"]) {
    assert.strictEqual(panel.navigate("run-b", tabId, { action }), true);
  }
  assert.deepStrictEqual(wc.calls, [
    "load:https://example.com/x", "load:http://a.test", "back", "forward", "reload", "stop",
  ]);
  assert.strictEqual(panel.navigate("run-b", tabId, { action: "bogus" }), false);
  assert.strictEqual(panel.navigate("run-b", "nope", { action: "reload" }), false, "unknown tab is a no-op");
});

test("a login view asks the renderer to take focus; an ordinary run view does not", () => {
  panel.newView("run-plain", { label: "Run" });
  assert.strictEqual(last.meta, undefined, "a run must not steal the panel from whatever is on screen");
  panel.newView("run-login", { label: "GitHub", focus: true });
  assert.deepStrictEqual(last.meta, { focus: true }, "a login is blocking a run on the user — it comes forward");
});

test("a tab added to a run for a login (new_tab) can ask the renderer to take focus, like new_view", () => {
  panel.newView("run-tabfocus", { label: "first" });
  panel.newTab("run-tabfocus", { label: "Salesforce", focus: true });
  assert.deepStrictEqual(last.meta, { focus: true });
  panel.newTab("run-tabfocus", { label: "quiet" });
  assert.strictEqual(last.meta, undefined, "an ordinary tab (or a probe) must not steal focus");
});

test("a background probe tab (focus:false) never steals the shown tab; a plain new tab still does", () => {
  const { tabId: loginTab } = panel.newView("run-bg", { label: "Google", focus: true });
  const probe = panel.newTab("run-bg", { label: "verify", focus: false });
  assert.strictEqual(last.tabs.find((t) => t.id === loginTab).active, true, "the sign-in stays shown");
  assert.strictEqual(last.tabs.find((t) => t.id === probe.tabId).active, false);
  const plain = panel.newTab("run-bg", { label: "user tab" });
  assert.strictEqual(last.tabs.find((t) => t.id === plain.tabId).active, true);
});

test("AUTH-6: a tab created with loginKey is described as isLogin; one without is not", () => {
  const { tabId: loginTabId } = panel.newView("run-authkey", { label: "AppA", loginKey: "ws__app_a" });
  assert.strictEqual(last.tabs.find((t) => t.id === loginTabId).isLogin, true);
  const { tabId: probeTabId } = panel.newTab("run-authkey", { label: "verify", focus: false });
  assert.strictEqual(last.tabs.find((t) => t.id === probeTabId).isLogin, false, "a probe tab (no loginKey) is never shown a Done action");
});

test("EXEC-46: a run's download is saved silently (no Save As dialog) and handed to the runtime by URL", async () => {
  panel.newView("run-dl");
  const handler = downloadHandlers["conxa-run-run-dl"];
  assert.ok(handler, "every run partition gets a will-download handler");
  let savePath = null;
  let finish;
  const item = {
    getURL: () => "blob:https://github.com/abc",
    setSavePath: (p) => { savePath = p; },
    cancel: () => assert.fail("a live run's download must not be cancelled"),
    once: (ev, fn) => { if (ev === "done") finish = fn; },
  };
  handler({}, item);
  assert.ok(savePath, "a save path is set up front — that is what stops Electron opening its dialog");
  const dest = path.join(_tmpConxaDir, "runs", "r1", "Dotnet.gitignore");
  const saving = panel.saveDownload("run-dl", "blob:https://github.com/abc", dest, 2000);
  fs.writeFileSync(savePath, "## .NET");
  finish({}, "completed");
  assert.deepStrictEqual(await saving, { ok: true });
  assert.strictEqual(fs.readFileSync(dest, "utf8"), "## .NET");
  await assert.rejects(panel.saveDownload("run-dl", "blob:https://github.com/abc", dest, 200), /no download/,
    "a download is handed over once, never twice");
  await panel.runEnd("run-dl");
  assert.ok(!fs.existsSync(path.dirname(savePath)), "run end removes Execute's copy of the run's downloads");
});

test("AUTH-6: loginDone writes the file-drop signal for a real login tab, and no-ops otherwise", () => {
  const { tabId: loginTabId } = panel.newView("run-authdone", { label: "AppB", loginKey: "ws__app_b" });
  const result = panel.loginDone("run-authdone", loginTabId);
  assert.deepStrictEqual(result, { ok: true });
  const file = path.join(_tmpConxaDir, "login-done", "ws__app_b.cmd");
  assert.ok(fs.existsSync(file), "the signal file browser.js's _checkHumanOverride watches for must exist");

  const { tabId: probeTabId } = panel.newTab("run-authdone", { label: "verify" });
  assert.deepStrictEqual(panel.loginDone("run-authdone", probeTabId), { ok: false }, "never fires for a non-login tab");
  assert.deepStrictEqual(panel.loginDone("run-authdone", "nope"), { ok: false }, "an unknown tab id is a no-op, not a throw");
});

test("setVisibleChat hides other chats' views (still alive, same size); untagged runs stay visible", () => {
  const rect = { x: 10, y: 10, width: 300, height: 200 };
  panel.tagRun("run-chat-a", "chat-a");
  const a = panel.newView("run-chat-a");
  const viewA = added[added.length - 1];
  const n = panel.newView("run-no-chat");
  const viewNone = added[added.length - 1];
  panel.setActiveBounds("run-chat-a", a.tabId, rect);
  assert.deepStrictEqual(viewA.bounds, rect);
  assert.strictEqual(viewA.visible, true);
  panel.setVisibleChat("chat-b");
  assert.strictEqual(viewA.visible, false, "chat A's view is hidden when chat B is shown");
  assert.deepStrictEqual(viewA.bounds, rect, "hidden, never shrunk to 0x0");
  assert.strictEqual(viewA.webContents.closed, undefined, "hidden, not destroyed");
  panel.setActiveBounds("run-no-chat", n.tabId, rect);
  panel.setVisibleChat("chat-b");
  assert.strictEqual(viewNone.visible, true, "a run outside any chat is never hidden");
  panel.setActiveBounds("run-chat-a", a.tabId, rect); // back in chat A
  assert.strictEqual(viewA.visible, true);
  assert.strictEqual(viewNone.visible, false, "showing one run hides every other run's view");
  assert.deepStrictEqual(viewNone.bounds, rect);
});

const settle = () => new Promise((r) => setImmediate(r));

test("a page that becomes ready at the wrong size is cleared and put back on the run's current zoom", async () => {
  const { tabId } = panel.newView("run-reapply", { viewport: { width: 1280, height: 720 } });
  const view = added[added.length - 1];
  panel.setActiveBounds("run-reapply", tabId, { x: 0, y: 0, width: 640, height: 600 }); // s = 0.5
  const wc = view.webContents;
  for (const ev of ["dom-ready", "did-finish-load"]) {
    for (const live of [[1100, 720, 1], [1280, 500, 1], null]) { // drifted width, drifted height, unreadable
      wc.live = live;
      wc.zoom = 1; // whatever drifted it
      wc.methods.length = 0; wc.emulations.length = 0;
      wc.handlers[ev]();
      await settle();
      assert.ok(wc.methods.includes("Emulation.clearDeviceMetricsOverride"), "clears first: Chromium ignores a repeat of an override it holds");
      assert.deepStrictEqual(wc.emulations, [], "no override while the run is on screen: the zoom shrinks the page");
      assert.strictEqual(wc.zoom, 0.5);
    }
  }
});

test("a page at the recorded width and height is left alone, whatever its DPR (a zoomed page's DPR is screen x zoom)", async () => {
  const { tabId } = panel.newView("run-reapply-ok", { viewport: { width: 1280, height: 720 } });
  const wc = added[added.length - 1].webContents;
  panel.setActiveBounds("run-reapply-ok", tabId, { x: 0, y: 0, width: 640, height: 600 });
  for (const live of [[1280, 720, 1], [1280, 720, 0.625], [1281, 719, 0.5]]) {
    wc.live = live;
    wc.methods.length = 0;
    wc.handlers["dom-ready"]();
    await settle();
    assert.ok(!wc.methods.includes("Emulation.clearDeviceMetricsOverride") && !wc.methods.includes("Emulation.setDeviceMetricsOverride"));
  }
});

test("ensure_geometry repairs every tab of the run, hidden ones too, and says whether it held", async () => {
  panel.newView("run-ensure", { viewport: { width: 1280, height: 720 } });
  const first = added[added.length - 1].webContents;
  panel.newTab("run-ensure", {});
  const second = added[added.length - 1].webContents;
  first.live = [1024, 720, 1];
  second.live = [1280, 720, 1.25];
  first.methods.length = 0; second.methods.length = 0;
  const r = await panel.ensureGeometry("run-ensure");
  assert.strictEqual(r.tabs, 2);
  assert.ok(first.methods.includes("Emulation.clearDeviceMetricsOverride"), "the drifted tab was repaired");
  assert.ok(!second.methods.includes("Emulation.clearDeviceMetricsOverride"), "the healthy tab was not touched");
  assert.strictEqual(r.ok, false, "the fake still reports the old size after the repair, so it did not hold");
  second.live = [1280, 720, 1];
  assert.strictEqual((await panel.ensureGeometry("run-ensure")).tabs, 2);
  first.live = [1280, 720, 1];
  assert.strictEqual((await panel.ensureGeometry("run-ensure")).ok, true, "both tabs at the recorded size: held");
  await assert.rejects(() => panel.ensureGeometry("no-such-run"), /no run/);
});

// ── input shield: the person's mouse stays off a page the runtime is driving ─────────────────────────────────
const shieldOf = () => added.filter((v) => String(v.webContents.url).startsWith("about:blank?conxa_shield")).pop();
const RECT = { x: 10, y: 20, width: 800, height: 600 };
const HIDDEN = { x: 0, y: 0, width: 0, height: 0 };
const startRun = (id) => { const { tabId } = panel.newView(id); panel.setInputOpen(id, false); panel.setActiveBounds(id, tabId, RECT); return tabId; };

test("no shield exists until the runtime asks for one, so an older runtime never meets one it cannot lift", () => {
  const before = added.length;
  const { tabId } = panel.newView("sh0");
  panel.setActiveBounds("sh0", tabId, RECT);
  assert.strictEqual(added.slice(before).filter((v) => String(v.webContents.url).startsWith("about:blank?conxa_shield")).length, 0);
});

test("a run's page is covered by a transparent shield the same size, on top, that never takes the keyboard", () => {
  startRun("sh1");
  const shield = shieldOf();
  assert.ok(shield, "a shield was created");
  assert.deepStrictEqual(shield.bounds, RECT);
  assert.strictEqual(added[added.length - 1], shield, "re-added last = top-most");
  assert.notStrictEqual(shield.webContents.focused, true, "the person may be typing in the chat");
});

test("a sign-in tab, and a popup it opens, are never covered", () => {
  const tabId = startRun("sh2");
  const shield = shieldOf();
  const login = panel.newTab("sh2", { label: "Sign in", focus: true, loginKey: "k1" });
  panel.setActiveBounds("sh2", login.tabId, RECT);
  assert.deepStrictEqual(shield.bounds, HIDDEN, "the person signs in");
  const loginView = added.filter((v) => v.webContents.openHandler).find((v) => v.webContents.url.includes(login.tabId));
  loginView.webContents.openHandler({ url: "" }); // an OAuth leg opened by the sign-in tab is the person's too
  const popupId = last.tabs[last.tabs.length - 1].id;
  panel.setActiveBounds("sh2", popupId, RECT);
  assert.deepStrictEqual(shield.bounds, HIDDEN, "the popup is not covered");
  panel.setActiveBounds("sh2", tabId, RECT);
  assert.deepStrictEqual(shield.bounds, RECT, "back over the run's own page");
});

test("a hand-over lifts the shield off the page, and putting it back hands the keyboard to the app", () => {
  startRun("sh3");
  const shield = shieldOf();
  const tabView = added.filter((v) => !String(v.webContents.url).startsWith("about:blank?conxa_shield")).pop();
  let appFocused = 0;
  win.webContents = { isDestroyed: () => false, focus: () => { appFocused++; } };
  try {
    panel.setInputOpen("sh3", true);
    assert.deepStrictEqual(shield.bounds, HIDDEN, "the person can click the page");
    assert.strictEqual(tabView.webContents.focused, true, "and type into it");
    panel.setInputOpen("sh3", false);
    assert.deepStrictEqual(shield.bounds, RECT, "back over the page when the person is done");
    assert.strictEqual(appFocused, 1, "typing no longer reaches the page");
  } finally { delete win.webContents; }
  assert.deepStrictEqual(panel.setInputOpen("no-such-run", true), { ok: false });
});

test("a shield put back while the panel is hidden does not reappear over something else", () => {
  startRun("sh5");
  const shield = shieldOf();
  panel.setInputOpen("sh5", true);
  panel.hideAll();
  panel.setInputOpen("sh5", false);
  assert.deepStrictEqual(shield.bounds, HIDDEN, "the panel is hidden; there is nothing to cover");
});

test("hiding the panel parks the shield, and ending the run removes it", async () => {
  startRun("sh4");
  const shield = shieldOf();
  panel.hideAll();
  assert.deepStrictEqual(shield.bounds, HIDDEN);
  await panel.runEnd("sh4");
  assert.ok(removed.includes(shield));
  assert.strictEqual(shield.webContents.closed, true);
});

test("capture stills a tab with stayHidden/stayAwake, so it answers while the view is hidden, and refuses a missing run", async () => {
  const { tabId } = panel.newView("cap-run", {});
  const wc = added[added.length - 1].webContents;
  const got = await panel.capture("cap-run", tabId, { type: "jpeg", quality: 55 });
  assert.deepStrictEqual(wc.captured.opts, { stayHidden: true, stayAwake: true });
  assert.strictEqual(Buffer.from(got.data, "base64").toString(), "jpeg55");
  assert.strictEqual(Buffer.from((await panel.capture("cap-run", "gone", { type: "png" })).data, "base64").toString(), "png");
  await assert.rejects(() => panel.capture("no-such-run", tabId), /no tab to capture/);
});
