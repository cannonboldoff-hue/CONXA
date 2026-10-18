// Plain node:test for the conversation thread reducer (renderer/src/lib/thread.ts).
//   node --experimental-strip-types --test test/thread.test.mjs

import assert from "node:assert/strict";
import test from "node:test";
import {
  applyDelta,
  clockTime,
  earlierLabel,
  openCards,
  prependPage,
  upsertItem,
  upsertMany,
  visibleItems,
} from "../renderer/src/lib/thread.ts";

const msg = (id, text, kind = "user_message") => ({ id, ts: "2026-10-18T10:00:00Z", kind, text });
const card = (card_id, revision, state, extra = {}) => ({
  id: `it_${card_id}_${revision}`, ts: `2026-10-18T10:0${revision}:00Z`, kind: "card", card_id, revision, state, card_type: "question", ...extra,
});

test("the same item arriving twice changes nothing", () => {
  const a = upsertItem([], msg("1", "hi"));
  assert.equal(upsertItem(a, msg("1", "hi")).length, 1);
});

test("a revised card replaces the old one in place and keeps its first timestamp", () => {
  let items = upsertMany([], [msg("1", "before"), card("c1", 1, "open"), msg("2", "after")]);
  items = upsertItem(items, card("c1", 2, "answered", { receipt: "Kept" }));
  assert.deepEqual(items.map((i) => i.card_id ?? i.text), ["before", "c1", "after"]);
  assert.equal(items[1].state, "answered");
  assert.equal(items[1].receipt, "Kept");
  assert.equal(items[1].ts, "2026-10-18T10:01:00Z"); // position and time come from the first revision
});

test("a stale revision that arrives late loses", () => {
  let items = upsertItem([], card("c1", 3, "applied"));
  items = upsertItem(items, card("c1", 2, "applying"));
  assert.equal(items[0].state, "applied");
});

test("older pages are prepended without duplicating what is on screen", () => {
  const onScreen = [msg("3", "c"), card("c9", 1, "open")];
  const older = [msg("1", "a"), msg("2", "b"), msg("3", "c"), card("c9", 1, "open")];
  const merged = prependPage(onScreen, older);
  assert.deepEqual(merged.map((i) => i.card_id ?? i.text), ["a", "b", "c", "c9"]);
});

test("open cards are the ones waiting on a person", () => {
  const items = [card("a", 1, "open"), card("b", 1, "answered"), msg("1", "x")];
  assert.deepEqual(openCards(items).map((i) => i.card_id), ["a"]);
});

test("summaries and import markers never render", () => {
  const items = [msg("1", "hi"), { id: "s", ts: "", kind: "summary", text: "..." }, { id: "m", ts: "", kind: "import_marker" }];
  assert.equal(visibleItems(items).length, 1);
});

test("the earlier row names what is folded inside it in plain words", () => {
  const ev = (id, text) => ({ id, ts: "", kind: "event", text });
  assert.equal(earlierLabel([ev("1", "Workflow created"), ev("2", "Signed in to Billing Portal"), ev("3", "Recording saved")]),
    "Earlier · workflow created, signed in to billing portal, recording saved");
  assert.equal(earlierLabel([msg("1", "a"), msg("2", "b")]), "Earlier · 2 items");
});

test("streamed deltas accumulate per turn and a new turn starts fresh", () => {
  let s = applyDelta(null, "t1", "Hel");
  s = applyDelta(s, "t1", "lo");
  assert.deepEqual(s, { turnId: "t1", text: "Hello" });
  assert.deepEqual(applyDelta(s, "t2", "Hi"), { turnId: "t2", text: "Hi" });
  assert.equal(applyDelta(s, "t1", ""), s);
});

test("clock time tolerates a bad date", () => {
  assert.equal(clockTime("not a date"), "");
  assert.equal(clockTime(undefined), "");
  assert.match(clockTime("2026-10-18T15:06:00Z", "en-US"), /\d/);
});
