"use strict";
// W4-c: a click is only "performed" when the page says it reached the element. A click that went to something else (a
// banner in front) or to nothing used to be reported as a finished step; now the step fails as an intercepted click,
// which is what hands it to the existing dismiss ladder.
const test = require("node:test");
const assert = require("node:assert");

const pageScripts = require("../../app/page_scripts");
const { clickWithWitness } = require("../../app/handlers");
const { classifyException, CLASS } = require("../../app/recovery");

// A stand-in for the page realm: just enough window and elements for the two page scripts.
function fakePage(dispatchTo) {
  const listeners = [];
  global.window = {
    addEventListener: (type, fn) => { if (type === "click") listeners.push(fn); },
    removeEventListener: (type, fn) => { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); },
  };
  const mk = (name, extra = {}) => ({ tagName: "BUTTON", isConnected: true, contains: () => false, getAttribute: () => name, innerText: name, ...extra });
  const target = mk("Buy");
  const banner = mk("Accept all", { tagName: "DIV" });
  const inner = mk("icon", { tagName: "SPAN" });
  target.contains = (t) => t === inner;
  target.nodeType = 1; banner.nodeType = 1; inner.nodeType = 1;
  const fire = (el) => listeners.slice().forEach((fn) => fn({ isTrusted: true, target: el, composedPath: () => [el, ...(el === inner ? [target] : [])] }));
  const clicks = { n: 0 };
  const locator = {
    first: () => locator,
    evaluate: async (fn, arg) => fn(target, arg),
    click: async () => {
      clicks.n++;
      if (dispatchTo === "target") fire(target);
      else if (dispatchTo === "inner") fire(inner);
      else if (dispatchTo === "banner") fire(banner);
      else if (dispatchTo === "navigates") target.isConnected = false; // the page was replaced by the click
    },
  };
  return { locator, target, clicks };
}

test("the page scripts say received / elsewhere / none / unknown", () => {
  for (const [dispatchTo, want] of [["target", "received"], ["inner", "received"], ["banner", "elsewhere"], ["nothing", "none"], ["navigates", "unknown"]]) {
    const fp = fakePage(dispatchTo);
    assert.strictEqual(pageScripts.armClickWitness(fp.target), true);
    fp.locator.click();
    const r = pageScripts.readClickWitness();
    assert.strictEqual(r.state, want, dispatchTo);
    if (want === "elsewhere") assert.match(r.where, /accept all/i);
    delete global.window;
  }
});

test("clickWithWitness: a click that landed (or navigated away) passes", async () => {
  for (const dispatchTo of ["target", "inner", "navigates"]) {
    const fp = fakePage(dispatchTo);
    await clickWithWitness(fp.locator);
    assert.strictEqual(fp.clicks.n, 1);
    delete global.window;
  }
});

test("clickWithWitness: a click that went to a banner fails as an intercepted click that may have acted, and is not retried", async () => {
  const fp = fakePage("banner");
  await assert.rejects(() => clickWithWitness(fp.locator), (e) => {
    assert.strictEqual(e.clickNotReceived, true);
    assert.strictEqual(e.mayHaveActed, true, "the banner got a click, so something may have happened");
    assert.strictEqual(classifyException(e), CLASS.INTERCEPTED, "the dismiss ladder is keyed on this class");
    return true;
  });
  delete global.window;
});

test("clickWithWitness: a click that reached nothing fails as intercepted, without claiming it acted", async () => {
  const fp = fakePage("nothing");
  await assert.rejects(() => clickWithWitness(fp.locator), (e) => {
    assert.strictEqual(e.clickNotReceived, true);
    assert.notStrictEqual(e.mayHaveActed, true);
    assert.strictEqual(classifyException(e), CLASS.INTERCEPTED);
    return true;
  });
  delete global.window;
});

test("clickWithWitness: a page that cannot be listened to is never blamed", async () => {
  const fp = fakePage("nothing");
  fp.locator.evaluate = async () => { throw new Error("frame detached"); };
  await clickWithWitness(fp.locator); // no verdict, no throw
  assert.strictEqual(fp.clicks.n, 1);
  delete global.window;
});

test("a click inside a web component counts as received even though the window sees the host", () => {
  const listeners = [];
  global.window = { addEventListener: (t, fn) => listeners.push(fn), removeEventListener: () => {} };
  const inner = { nodeType: 1, tagName: "BUTTON" };                       // the button, inside a shadow root
  const host = { nodeType: 1, tagName: "MY-WIDGET", isConnected: true, contains: () => false };  // what ev.target shows at the window
  const el = { ...inner, isConnected: true, contains: () => false };
  pageScripts.armClickWitness(el);
  listeners[0]({ isTrusted: true, target: host, composedPath: () => [el, host] });
  assert.strictEqual(pageScripts.readClickWitness().state, "received");
  delete global.window;
});
