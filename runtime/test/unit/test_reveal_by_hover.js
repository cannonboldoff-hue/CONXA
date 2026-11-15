"use strict";
// W11-c: a target that is hidden until something is hovered is revealed by hovering its nearest visible ancestors, the
// way a person would, instead of timing out. (The recording only holds the hover when hover capture was on.)
const test = require("node:test");
const assert = require("node:assert");

const { gateLocator } = require("../../app/resolution");
const pageScripts = require("../../app/page_scripts");

function fakeLocator({ revealAfterMoves, points }) {
  const moves = [];
  let visible = false;
  const loc = {
    waitFor: async ({ state }) => {
      if (state === "attached") return;
      if (state === "visible" && !visible) throw new Error("Timeout exceeded");
    },
    evaluate: async (fn) => (fn === pageScripts.revealHoverPoints ? points : (fn === pageScripts.isDisabled ? false : true)),
    page: () => ({ mouse: { move: async (x, y) => { moves.push([x, y]); if (moves.length >= revealAfterMoves) visible = true; } } }),
  };
  return { loc, moves };
}

test("a hidden target is revealed by hovering its nearest visible ancestors, innermost first", async () => {
  const { loc, moves } = fakeLocator({ revealAfterMoves: 2, points: [{ x: 10, y: 10 }, { x: 50, y: 20 }, { x: 90, y: 30 }] });
  await gateLocator(loc, { confidence: 0.99 });
  assert.deepStrictEqual(moves, [[10, 10], [50, 20]], "stopped as soon as it showed");
});

test("a target that never shows still fails with the original error", async () => {
  const { loc, moves } = fakeLocator({ revealAfterMoves: 99, points: [{ x: 1, y: 1 }] });
  await assert.rejects(() => gateLocator(loc, { confidence: 0.99 }), /Timeout/);
  assert.strictEqual(moves.length, 1);
});

test("revealHoverPoints: visible ancestors only, innermost first, inside the window, no duplicates", () => {
  const mk = (rect, style = {}, parent = null) => ({ parentElement: parent, getBoundingClientRect: () => rect, __style: style });
  global.window = { innerWidth: 800, innerHeight: 600, getComputedStyle: (n) => ({ visibility: "visible", display: "block", ...n.__style }) };
  const root = mk({ left: 0, top: 0, width: 800, height: 600 });
  const menu = mk({ left: 100, top: 40, width: 80, height: 30 }, {}, root);
  const hiddenWrap = mk({ left: 100, top: 40, width: 80, height: 30 }, { display: "none" }, menu);
  const el = mk({ left: 0, top: 0, width: 0, height: 0 }, {}, hiddenWrap);
  const pts = pageScripts.revealHoverPoints(el);
  delete global.window;
  assert.deepStrictEqual(pts[0], { x: 140, y: 55 });
  assert.ok(pts.length >= 2 && pts.every((p) => p.x >= 1 && p.x <= 799));
});
