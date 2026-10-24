"use strict";
// Unit tests for the pure date-picker replay math (runtime/app/date_picker.js). No browser, no
// mocks — every function here is pure by construction (see the file's own header comment).

const test = require("node:test");
const assert = require("node:assert");

const {
  parseDateValue,
  parseDateInput,
  formatForDisplay,
  parseHeader,
  monthDelta,
  machineCellSelector,
  dayNumberSelector,
  monthLabel,
} = require("../../app/date_picker");
const { _dateValueMatches, HANDLERS } = require("../../app/handlers");

test("parseDateValue: plain ISO date", () => {
  assert.deepStrictEqual(parseDateValue("2026-09-15"), { year: 2026, month: 9, day: 15, hour: null, minute: null });
});

test("parseDateValue: ISO datetime", () => {
  assert.deepStrictEqual(parseDateValue("2026-09-15T14:30"), { year: 2026, month: 9, day: 15, hour: 14, minute: 30 });
});

test("parseDateValue: rejects garbage rather than guessing", () => {
  assert.strictEqual(parseDateValue("not a date"), null);
  assert.strictEqual(parseDateValue(""), null);
  assert.strictEqual(parseDateValue("09/15/2026"), null);
});

test("formatForDisplay: renders MM/DD/YYYY", () => {
  const parsed = parseDateValue("2026-01-05");
  assert.strictEqual(formatForDisplay(parsed, "MM/DD/YYYY"), "01/05/2026");
});

test("formatForDisplay: renders DD-MM-YYYY (different order/separator)", () => {
  const parsed = parseDateValue("2026-01-05");
  assert.strictEqual(formatForDisplay(parsed, "DD-MM-YYYY"), "05-01-2026");
});

test("formatForDisplay: two-digit year token", () => {
  const parsed = parseDateValue("2026-01-05");
  assert.strictEqual(formatForDisplay(parsed, "MM/DD/YY"), "01/05/26");
});

test("formatForDisplay: no format means no guess", () => {
  const parsed = parseDateValue("2026-01-05");
  assert.strictEqual(formatForDisplay(parsed, ""), null);
  assert.strictEqual(formatForDisplay(null, "MM/DD/YYYY"), null);
});

test("parseHeader: full month name", () => {
  assert.deepStrictEqual(parseHeader("September 2026"), { year: 2026, month: 9 });
});

test("parseHeader: abbreviated month name", () => {
  assert.deepStrictEqual(parseHeader("Sep 2026"), { year: 2026, month: 9 });
});

test("parseHeader: year-first ordering", () => {
  assert.deepStrictEqual(parseHeader("2026 September"), { year: 2026, month: 9 });
});

test("parseHeader: numeric MM/YYYY", () => {
  assert.deepStrictEqual(parseHeader("09/2026"), { year: 2026, month: 9 });
});

test("parseHeader: numeric YYYY-MM", () => {
  assert.deepStrictEqual(parseHeader("2026-09"), { year: 2026, month: 9 });
});

test("parseHeader: unparseable text returns null, never a guess", () => {
  assert.strictEqual(parseHeader("Loading..."), null);
  assert.strictEqual(parseHeader(""), null);
});

test("monthDelta: forward within the same year", () => {
  assert.strictEqual(monthDelta("August 2026", 2026, 9), 1);
});

test("monthDelta: backward within the same year", () => {
  assert.strictEqual(monthDelta("December 2026", 2026, 9), -3);
});

test("monthDelta: crosses a year boundary forward", () => {
  assert.strictEqual(monthDelta("November 2026", 2027, 1), 2);
});

test("monthDelta: crosses a year boundary backward", () => {
  assert.strictEqual(monthDelta("January 2027", 2026, 11), -2);
});

test("monthDelta: zero when already on the target month", () => {
  assert.strictEqual(monthDelta("September 2026", 2026, 9), 0);
});

test("monthDelta: unparseable header refuses to guess a click count", () => {
  assert.strictEqual(monthDelta("", 2026, 9), null);
});

test("machineCellSelector: builds a selector for a machine-readable attribute", () => {
  assert.strictEqual(machineCellSelector("data-date", "2026-09-15"), '[data-date="2026-09-15"]');
});

test("machineCellSelector: refuses to regenerate an aria-label sentence", () => {
  assert.strictEqual(machineCellSelector("", "2026-09-15"), null);
  assert.strictEqual(machineCellSelector("aria-label", "2026-09-15"), null);
});

test("monthLabel: 1-indexed month number to English label", () => {
  assert.strictEqual(monthLabel(1), "January");
  assert.strictEqual(monthLabel(12), "December");
  assert.strictEqual(monthLabel(9), "September");
});

test("monthLabel: out-of-range month refuses to guess", () => {
  assert.strictEqual(monthLabel(0), null);
  assert.strictEqual(monthLabel(13), null);
});

test("dayNumberSelector: excludes disabled/outside-month cells, matches exact day text", () => {
  const sel = dayNumberSelector(15);
  assert.match(sel, /:text-is\("15"\)$/);
  assert.match(sel, /disabled/);
  assert.match(sel, /outside/);
  // Never matches "15" as a substring of "150" or similar via a loose text selector.
  assert.doesNotMatch(sel, /text=15[^"]/);
});

// _dateValueMatches (handlers.js) is now the enforced post-condition for a native/anchored-field
// date_pick — there is no compile-time value_equals assertion behind it any more (date_picker.py
// dropped it: a literal recorded date failed VERIFY for every caller-supplied date other than the
// one recorded). A false result here now makes the handler throw instead of returning silently.
test("_dateValueMatches: matches the raw ISO readback", () => {
  const parsed = parseDateValue("2026-09-15");
  assert.strictEqual(_dateValueMatches("2026-09-15", parsed, ""), true);
});

test("_dateValueMatches: matches the field's own display-formatted readback", () => {
  const parsed = parseDateValue("2026-09-15");
  assert.strictEqual(_dateValueMatches("09/15/2026", parsed, "MM/DD/YYYY"), true);
});

test("_dateValueMatches: tolerates the formatted value as a substring (extra field chrome)", () => {
  const parsed = parseDateValue("2026-09-15");
  assert.strictEqual(_dateValueMatches("Selected: 09/15/2026", parsed, "MM/DD/YYYY"), true);
});

test("_dateValueMatches: rejects an empty readback", () => {
  const parsed = parseDateValue("2026-09-15");
  assert.strictEqual(_dateValueMatches("", parsed, "MM/DD/YYYY"), false);
});

test("_dateValueMatches: rejects a readback for the wrong date", () => {
  const parsed = parseDateValue("2026-09-15");
  assert.strictEqual(_dateValueMatches("09/16/2026", parsed, "MM/DD/YYYY"), false);
});

// Typed-date format replay: semantic date in -> the application's own recorded format out.
const YMD = { year: 2026, month: 9, day: 25, hour: null, minute: null };

test("formatForDisplay: unpadded and month-name tokens", () => {
  const parsed = parseDateValue("2026-01-05");
  assert.strictEqual(formatForDisplay(parsed, "D/M/YYYY"), "5/1/2026");
  assert.strictEqual(formatForDisplay(parsed, "MMM D, YYYY"), "Jan 5, 2026");
  assert.strictEqual(formatForDisplay(parsed, "D MMMM YYYY"), "5 January 2026");
});

test("parseDateInput: ISO and month-name forms need no format", () => {
  assert.deepStrictEqual(parseDateInput("2026-09-25", ""), YMD);
  assert.deepStrictEqual(parseDateInput("September 25, 2026", ""), YMD);
  assert.deepStrictEqual(parseDateInput("Sep 25 2026", ""), YMD);
  assert.deepStrictEqual(parseDateInput("25th September 2026", ""), YMD);
  assert.deepStrictEqual(parseDateInput("25-Sep-2026", ""), YMD);
});

test("parseDateInput: numeric is read through the app's own format, never guessed", () => {
  assert.deepStrictEqual(parseDateInput("25/09/2026", "DD/MM/YYYY"), YMD);
  assert.deepStrictEqual(parseDateInput("09/25/2026", "MM/DD/YYYY"), YMD);
  assert.strictEqual(parseDateInput("25/09/2026", ""), null);
  assert.strictEqual(parseDateInput("09/25/2026", "DD/MM/YYYY"), null); // month 25
  assert.strictEqual(parseDateInput("31/02/2026", "DD/MM/YYYY"), null); // not a real date
  assert.strictEqual(parseDateInput("not a date", "DD/MM/YYYY"), null);
});

test("semantic date -> each application's recorded format", () => {
  const cases = { "DD/MM/YYYY": "25/09/2026", "MM/DD/YYYY": "09/25/2026", "YYYY-MM-DD": "2026-09-25", "DD.MM.YYYY": "25.09.2026", "MMM D, YYYY": "Sep 25, 2026" };
  for (const [fmt, expected] of Object.entries(cases)) {
    assert.strictEqual(formatForDisplay(parseDateInput("September 25, 2026", fmt), fmt), expected, fmt);
  }
});

test("date_pick: an unreadable date on a format-aware step is a badInput error, not a garbage fill", async () => {
  const step = {
    action: "date_pick",
    value: "{{date}}",
    input_binding: "date",
    handler_hints: { date_picker: { kind: "text_field", display_format: "DD/MM/YYYY" } },
  };
  await assert.rejects(() => HANDLERS.date_pick(null, step, { date: "next tuesday" }), (err) => err.badInput === true);
});

// Relative dates: the recorded date was only ever an offset from the day it was recorded.
test("dateFromOffset: today, later and earlier, across month and year ends", () => {
  const { dateFromOffset } = require("../../app/date_picker");
  const now = new Date(2026, 11, 28, 15, 30); // 28 Dec 2026, mid-afternoon: the time of day never matters
  assert.strictEqual(dateFromOffset(0, now), "2026-12-28");
  assert.strictEqual(dateFromOffset(7, now), "2027-01-04");
  assert.strictEqual(dateFromOffset(-28, now), "2026-11-30");
  assert.strictEqual(dateFromOffset(1, new Date(2028, 1, 28)), "2028-02-29"); // leap day
});

test("date_pick: date_offset_days ignores the recorded value and works the date out now", async () => {
  const { dateFromOffset } = require("../../app/date_picker");
  const pageScripts = require("../../app/page_scripts");
  const filled = [];
  const item = {
    // the descriptor extractor gets an object; the gate's "stable" / "disabled" probes get plain answers
    evaluate: async (fn) => (fn === pageScripts.isDisabled ? false : fn === pageScripts.rafStable ? true
      : { testid: "due", role: "textbox", name: "Due", text: "" }),
    fill: async (v) => { filled.push(v); },
    press: async () => {},
    inputValue: async () => filled[filled.length - 1] || "",
    waitFor: async () => {},
    first() { return this; },
  };
  const page = {
    waitForLoadState: async () => {},
    waitForTimeout: async () => {},
    context: () => ({ on: () => {} }),
    url: () => "https://x.test",
    locator: () => ({ all: async () => [item], first: () => item }),
  };
  const step = {
    type: "date_pick", value: "", intent: "due",
    identity_bundle: {
      signals: [{ engine: "testid", selector: 'internal:testid=[data-testid="due"]', durability: 0.99 }],
      fingerprint: { data_testid: "due", role: "textbox" }, frame_chain: [],
    },
    handler_hints: { date_picker: { kind: "text_field", display_format: "YYYY-MM-DD", date_offset_days: 7, recorded_value: "2020-01-01" } },
  };
  await HANDLERS.date_pick(page, step, {}).catch((e) => console.log("ERR", e.message));
  assert.ok(filled.length && filled[0] === dateFromOffset(7), `filled ${JSON.stringify(filled)}`);
});
