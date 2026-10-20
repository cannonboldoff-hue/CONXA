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

test("browser: opening it hides navigation and takes 60 percent of the window, in the inspector's place", () => {
  const l = resolveBrowserLayout({ windowWidth: 1920 });
  assert.equal(l.mode, "panel");
  assert.equal(l.width, 1152);
  assert.equal(l.navMode, "hidden");
});

test("browser: at 1440 the room navigation gave up goes to the browser", () => {
  const l = resolveBrowserLayout({ windowWidth: 1440 });
  assert.equal(l.navMode, "hidden");
  assert.equal(l.width, 864);
  assert.ok(l.width > 1440 - NAV.def - INSPECTOR.strip - WORKSPACE_MIN, "bigger than with navigation open");
});

test("browser: navigation brought back while it is open comes back as the rail", () => {
  const l = resolveBrowserLayout({ windowWidth: 1200, navShown: true });
  assert.equal(l.navMode, "rail");
  assert.equal(l.width, 1200 - NAV.rail - INSPECTOR.strip - WORKSPACE_MIN);
});

test("browser: the workspace never drops below its minimum", () => {
  for (const navShown of [false, true]) {
    for (let w = 960; w <= 2200; w += 20) {
      const l = resolveBrowserLayout({ windowWidth: w, navShown });
      if (l.mode === "panel") {
        const nav = l.navMode === "rail" ? NAV.rail : 0;
        assert.ok(w - nav - INSPECTOR.strip - l.width >= WORKSPACE_MIN, `width ${w}`);
        assert.ok(l.width >= BROWSER.min, `width ${w}`);
      }
    }
  }
});

test("browser: the minimum window has no room, so it is hidden instead of squeezing the conversation", () => {
  const l = resolveBrowserLayout({ windowWidth: 960 });
  assert.equal(l.mode, "hidden");
});

test("browser: a dragged width is honoured within the 480 to 75 percent range", () => {
  assert.equal(resolveBrowserLayout({ windowWidth: 1920, preferredWidth: 300 }).width, 480);
  assert.equal(resolveBrowserLayout({ windowWidth: 1920, preferredWidth: 9999 }).width <= 1920 * 0.75, true);
  assert.equal(resolveBrowserLayout({ windowWidth: 1920, preferredWidth: 900 }).width, 900);
});
