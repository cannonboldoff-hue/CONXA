// Plain node:test for the shell's panel sizing rules (renderer/src/lib/panels.ts).
//   node --experimental-strip-types --test test/panels.test.mjs

import assert from "node:assert/strict";
import test from "node:test";
import {
  INSPECTOR,
  NAV,
  WORKSPACE_MIN,
  clamp,
  parsePanels,
  resolveLayout,
} from "../renderer/src/lib/panels.ts";

const base = {
  windowWidth: 1440,
  navWidth: NAV.def,
  inspectorWidth: INSPECTOR.def,
  navCollapsed: false,
  inspectorAvailable: true,
  inspectorOpen: true,
};

test("wide window: everything open", () => {
  const l = resolveLayout(base);
  assert.equal(l.navMode, "full");
  assert.equal(l.inspectorMode, "panel");
});

test("inspector is hidden off workflow screens", () => {
  assert.equal(resolveLayout({ ...base, inspectorAvailable: false }).inspectorMode, "hidden");
});

test("a closed inspector shows its strip", () => {
  assert.equal(resolveLayout({ ...base, inspectorOpen: false }).inspectorMode, "strip");
});

test("order of giving way: inspector strip first, then nav rail, then sheet", () => {
  // 1440 - 264 - 320 = 856 >= 520: all open.
  // At 1000: 1000 - 264 - 320 = 416 < 520, strip fits (1000 - 264 - 44 = 692).
  const strip = resolveLayout({ ...base, windowWidth: 1000 });
  assert.equal(strip.inspectorMode, "strip");
  assert.equal(strip.navMode, "full");
  // Narrower, with a wide nav: strip no longer leaves 520 -> nav folds to the rail.
  const rail = resolveLayout({ ...base, windowWidth: 800, navWidth: NAV.max });
  assert.equal(rail.navMode, "rail");
  assert.equal(rail.inspectorMode, "strip");
  // The minimum window (960) with nav collapsed and a 400px inspector: sheet only if even the strip would squeeze the workspace.
  const sheet = resolveLayout({ ...base, windowWidth: 600, navCollapsed: true });
  assert.equal(sheet.inspectorMode, "sheet");
});

test("workspace never gets less than its minimum when the inspector is a panel", () => {
  for (let w = 960; w <= 2000; w += 40) {
    const l = resolveLayout(base.windowWidth === w ? base : { ...base, windowWidth: w });
    if (l.inspectorMode === "panel") {
      const nav = l.navMode === "rail" ? NAV.rail : l.navWidth;
      assert.ok(w - nav - l.inspectorWidth >= WORKSPACE_MIN, `width ${w}`);
    }
  }
});

test("clamp keeps widths in range", () => {
  assert.equal(clamp(100, 220, 320), 220);
  assert.equal(clamp(999, 220, 320), 320);
  assert.equal(clamp(260, 220, 320), 260);
});

test("parsePanels falls back on garbage and clamps stored widths", () => {
  assert.equal(parsePanels(null).navWidth, NAV.def);
  assert.equal(parsePanels("not json").inspectorWidth, INSPECTOR.def);
  const p = parsePanels(JSON.stringify({ navWidth: 9999, inspectorWidth: 1, navCollapsed: true }));
  assert.equal(p.navWidth, NAV.max);
  assert.equal(p.inspectorWidth, INSPECTOR.min);
  assert.equal(p.navCollapsed, true);
  assert.equal(p.inspectorOpen, true);
});

import { BROWSER, resolveBrowserLayout } from "../renderer/src/lib/panels.ts";

test("browser: a roomy window gives it 75 percent", () => {
  assert.equal(resolveBrowserLayout({ windowWidth: 1920 }).width, 1440);
  assert.equal(resolveBrowserLayout({ windowWidth: 2400 }).width, 1800);
});

test("browser: a smaller window keeps the conversation at its minimum beside the rail (the strip is not drawn while the browser is open)", () => {
  for (let w = 960; w <= 2400; w += 20) {
    const l = resolveBrowserLayout({ windowWidth: w });
    assert.ok(w - NAV.rail - l.width >= BROWSER.conversationMin, `width ${w}`);
    assert.ok(l.width <= w * BROWSER.fraction, `width ${w}`);
    assert.ok(l.width > 0, `width ${w}`);
  }
});
