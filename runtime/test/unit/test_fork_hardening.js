"use strict";

// A blocking popup that asks the person to CHOOSE (Replace / Keep both / Cancel) must never be
// dismissed on their behalf: not by the Tier 1 ladder's Escape or learned/known patterns, and not by
// an agent's nominated dismissal. Cookie banners and plain close-me popups keep the zero-token path.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

process.env.CONXA_DATA_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "conxa-fork-hardening-"));
process.env.CONXA_DIR = process.env.CONXA_DATA_DIR;
process.env.CONXA_GATE = "0";

const test = require("node:test");
const assert = require("node:assert");

const fork = require("../../app/fork");
const { layer1Ladder, runPlan } = require("../../app/run");

const DECISION = {
  title: "Upload options",
  text: "report.pdf already exists.",
  controls: [{ name: "Replace" }, { name: "Keep both" }, { name: "Cancel" }],
};
const COOKIES = { title: "We use cookies", text: "Cookie preferences", controls: [{ name: "Accept all" }, { name: "Reject all" }] };

function mockPage({ probe = null, present = {} } = {}) {
  const clicks = [];
  const escapes = [];
  return {
    _clicks: clicks,
    _escapes: escapes,
    url: () => "https://drive.test/folder",
    evaluate: async () => probe,
    locator: (sel) => ({
      count: async () => (present[sel] ? 1 : 0),
      first: () => ({ evaluate: async () => ({ label: "", inDialogChrome: false }), click: async () => { clicks.push(sel); } }),
    }),
    keyboard: { press: async () => { escapes.push(1); } },
    waitForTimeout: async () => {},
    waitForLoadState: async () => {},
    context: () => ({ on: () => {} }),
  };
}

function recoveryLog() {
  const file = path.join(process.env.CONXA_DIR, "logs", "recovery.log");
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8").trim().split("\n").map((l) => JSON.parse(l)) : [];
}

// ─── isDecisionDialog ──────────────────────────────────────────────────────────────────────

test("a popup offering several real choices is a decision", () => {
  assert.strictEqual(fork.isDecisionDialog(DECISION), true);
});

test("consent banners, close-only popups, forms and single-button popups are not decisions", () => {
  assert.strictEqual(fork.isDecisionDialog(COOKIES), false);
  assert.strictEqual(fork.isDecisionDialog({ title: "Promo", text: "", controls: [{ name: "Close" }, { name: "Not now" }] }), false);
  assert.strictEqual(fork.isDecisionDialog({ ...DECISION, hasFields: true }), false);
  assert.strictEqual(fork.isDecisionDialog({ title: "Saved", text: "", controls: [{ name: "Got it" }] }), false);
  assert.strictEqual(fork.isDecisionDialog({ ...DECISION, native: true }), false);
  assert.strictEqual(fork.isDecisionDialog(null), false);
});

// ─── Tier 1 ladder ─────────────────────────────────────────────────────────────────────────

test("an intercepted click behind a decision popup is NOT dismissed: no Escape, no clicks, flagged", async () => {
  const page = mockPage({ probe: DECISION, present: { "#onetrust-accept-btn-handler": true } });
  const err = new Error("#upload intercepts pointer events");
  const result = await layer1Ladder(page, { type: "wait", ms: 1 }, {}, "slug-d", 2, "#upload", err);
  assert.strictEqual(result, false);
  assert.strictEqual(page._escapes.length, 0, "Escape would have cancelled the popup");
  assert.deepStrictEqual(page._clicks, []);
  assert.strictEqual(err.unknownOverlay, true);
  assert.deepStrictEqual(err.decisionDialog.buttons, ["Replace", "Keep both", "Cancel"]);
  assert.ok(recoveryLog().some((e) => e.event === "tier1_dismiss_refused_decision_dialog" && e.slug === "slug-d"));
});

test("a cookie banner is still dismissed by the known-pattern ladder", async () => {
  const page = mockPage({ probe: COOKIES, present: { "#onetrust-accept-btn-handler": true } });
  const err = new Error("#buy intercepts pointer events");
  const result = await layer1Ladder(page, { type: "wait", ms: 1 }, {}, "slug-c", 2, "#buy", err);
  assert.strictEqual(result, "dismiss-overlay");
  assert.deepStrictEqual(page._clicks, ["#onetrust-accept-btn-handler"]);
});

test("with no dialog at all the ladder behaves exactly as before", async () => {
  const page = mockPage({ probe: null, present: { "#onetrust-accept-btn-handler": true } });
  const result = await layer1Ladder(page, { type: "wait", ms: 1 }, {}, "slug-n", 2, "#buy", new Error("#buy intercepts pointer events"));
  assert.strictEqual(result, "dismiss-overlay");
});

// ─── agent-nominated dismissal ─────────────────────────────────────────────────────────────

test("an agent's nominated dismissal of a decision popup is rejected and the step still runs", async () => {
  const page = mockPage({ probe: DECISION, present: { "button.cancel": true } });
  page.goto = async () => {};
  const step = { type: "navigate", url: "https://x.test/next", _dismiss_selector: "button.cancel" };
  await runPlan(page, [step], {}, 0, "slug-a", { tracker: { emit: () => {} } });
  assert.deepStrictEqual(page._clicks, [], "the agent must not be able to press Cancel on the person's behalf");
  assert.strictEqual(page._escapes.length, 0);
  assert.ok(recoveryLog().some((e) => e.event === "overlay_dismiss_rejected" && e.reason === "decision_dialog" && e.slug === "slug-a"));
});

test("an agent's escape nomination is rejected the same way", async () => {
  const page = mockPage({ probe: DECISION });
  page.goto = async () => {};
  const step = { type: "navigate", url: "https://x.test/next", _dismiss_escape: true };
  await runPlan(page, [step], {}, 0, "slug-e", { tracker: { emit: () => {} } });
  assert.strictEqual(page._escapes.length, 0);
});
