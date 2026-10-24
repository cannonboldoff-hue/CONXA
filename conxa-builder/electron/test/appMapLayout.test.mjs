// Plain node:test for the app-map viewer's layout rules (renderer/src/lib/appMapLayout.ts).
//   node --experimental-strip-types --test test/appMapLayout.test.mjs

import assert from "node:assert/strict";
import test from "node:test";
import { NODE_W, actionLabel, layoutAppGraph } from "../renderer/src/lib/appMapLayout.ts";

const screen = (id, route = "/" + id) => ({ id, route, title_tokens: [], seen: 1, sources: [] });
const action = (id, from, to, extra = {}) => ({
  id, from_screen: from, to_screen: to, kind: "click", role: "", name: null, intent: "", consequence: "",
  input_name: null, seen: 1, sources: [], ...extra,
});
const graph = (screens, actions, popups = []) => ({
  app_key: "x.test", workspace_id: "w", built_at: 0, screens, actions, popups, paths: [],
});

test("screens sit in columns by distance from the entry screen", () => {
  const g = graph([screen("a"), screen("b"), screen("c")], [action("1", "a", "b"), action("2", "b", "c")]);
  const l = layoutAppGraph(g);
  const x = Object.fromEntries(l.nodes.map((n) => [n.screen.id, n.x]));
  assert.ok(x.a < x.b && x.b < x.c);
  assert.equal(l.edges.length, 2);
  assert.ok(l.width >= 3 * NODE_W);
});

test("actions that stay on a screen or leave the app are listed on the screen, not drawn as arrows", () => {
  const g = graph([screen("a"), screen("b")], [action("1", "a", "a"), action("2", "a", null), action("3", "a", "elsewhere"), action("4", "a", "b")]);
  const l = layoutAppGraph(g);
  assert.deepEqual(l.stayingActions.get("a").map((a) => a.id), ["1", "2", "3"]);
  assert.deepEqual(l.edges.map((e) => e.action.id), ["4"]);
});

test("screens that only lead into each other still appear", () => {
  const g = graph([screen("a"), screen("b")], [action("1", "a", "b"), action("2", "b", "a")]);
  const l = layoutAppGraph(g);
  assert.equal(l.nodes.length, 2);
});

test("an empty map lays out without throwing", () => {
  const l = layoutAppGraph(graph([], []));
  assert.equal(l.nodes.length, 0);
  assert.ok(l.width >= 0 && l.height >= 0);
});

test("popups are indexed by the action they follow", () => {
  const popup = { id: "p", title: "Upload options", button_labels: [], popup_class: "decision", after_actions: ["1"], appeared: 1, absent: 0, options: [], field_seen: 0, sources: [] };
  const l = layoutAppGraph(graph([screen("a")], [action("1", "a", "a")], [popup]));
  assert.equal(l.popupsByAction.get("1").length, 1);
});

test("action labels use the control name when it is known", () => {
  assert.equal(actionLabel(action("1", "a", "b", { name: "Upload" })), "click “Upload”");
  assert.equal(actionLabel(action("1", "a", "b")), "click");
});
