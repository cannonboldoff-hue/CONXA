// Plain node:test for the five-stage model (renderer/src/lib/stages.ts).
//   node --experimental-strip-types --test test/stages.test.mjs

import assert from "node:assert/strict";
import test from "node:test";
import { groupByStage, stageOfItem, stageOfLifecycle } from "../renderer/src/lib/stages.ts";

const ev = (text) => ({ kind: "event", text });
const card = (card_type) => ({ kind: "card", card_type });
const say = (text) => ({ kind: "assistant_message", text });

test("lifecycle stages fold into the five the person sees", () => {
  for (const [stage, substate, want] of [
    ["describe", "needs_details", "record"], ["configure", "sign_in_needed", "record"], ["record", "recording", "record"],
    ["compile", "compiling", "compile"], ["clarify", "open", "compile"], ["edit", "open", "compile"],
    ["test", "running", "test"], ["approve", "ready", "approve"], ["approve", "approved", "publish"],
  ]) assert.equal(stageOfLifecycle({ stage, substate }), want, `${stage}/${substate}`);
});

test("cards name their stage; messages and plain events continue the one before", () => {
  assert.equal(stageOfItem(card("clarify_question")), "compile");
  assert.equal(stageOfItem(card("autofix")), "test");
  assert.equal(stageOfItem(say("hi")), null);
  assert.equal(stageOfItem(ev("Test started")), "test");
  assert.equal(stageOfItem(ev("Recording started")), null);
});

test("a thread splits into runs in time order, and a repeated stage starts a new run", () => {
  const items = [ev("Workflow created"), card("recording"), card("compile"), say("Compiled into 9 steps."), card("clarify_question"),
    ev("Test started"), card("test_run"), card("autofix"), card("recording"), card("test_run")];
  assert.deepEqual(groupByStage(items).map((g) => [g.stage, g.items.length]),
    [["record", 2], ["compile", 3], ["test", 3], ["record", 1], ["test", 1]]);
  assert.deepEqual(groupByStage([]), []);
});
