"use strict";
// Post-action VERIFY seam, extracted from run.js: compiled assertion access,
// polling primitives, per-type assertion evaluation, and verifyStep.
const pageScripts = require("./page_scripts");
const { interpolate } = require("./interpolate");
const { PAGE_LOAD_TIMEOUT_MS } = require("./run_config");
const { asObject, asArray, isNonIdempotent } = require("./step_utils");
const { rootCandidates, resolveStep } = require("./resolution");
const { withDeadline, evalOn, countOn, EVAL_TIMED_OUT } = require("./page_eval");

// Phase 8: post-action VERIFY — check compiled post-condition assertions independently of the
// action's own success. Returns { pass, channel, evidence }. Absent assertions → pass (no-op).
// The compiler only ever writes assertions under validation.assertions (ValidationBlock —
// packages/conxa-core/conxa_core/models/skill_spec.py); nothing emits a top-level step.assertions.
function stepAssertions(step) {
  return asArray(asObject(step.validation).assertions).filter(a => a && typeof a === "object");
}

// Normalize for value_equals comparison: trim, collapse internal whitespace, lowercase.
// Tolerates recorded values that differ only in incidental whitespace/case.
function normText(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ").toLowerCase();
}

const STATE_CHANGED_SELECTOR =
  'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="menuitem"], [role="option"]';
// Tolerance on body-text length delta so timestamp/clock-driven page noise (e.g. a live "2s ago"
// widget) doesn't register as a state change on its own.
const STATE_CHANGED_TEXT_LEN_TOLERANCE = 20;

// Cheap, deterministic snapshot of page shape used only to answer "did anything happen" for the
// state_changed assertion. No LLM, no DOM diffing — three counters compared before vs. after.
// How many new tabs/windows this page has opened (Playwright's per-page "popup" event: window.open, a target=_blank
// link). Counted from the first baseline taken on the page, so a step whose whole effect is opening a tab ("Click
// Here" -> /windows/new) is a change even though its own page stays exactly as it was. Per page, never the whole
// context: a borrowed host browser also holds other runs' pages.
const _popupCounts = new WeakMap();
function popupsOpened(page) {
  if (!page || typeof page.on !== "function") return null;
  let box = _popupCounts.get(page);
  if (!box) {
    box = { n: 0 };
    _popupCounts.set(page, box);
    try { page.on("popup", () => { box.n += 1; }); } catch (_) { return null; }
  }
  return box.n;
}

// ── A date check in any format (BUILD-111 (6), owner decision 2026-11-21) ──────────────────────────────────────────────
// Words that are, as a whole, one complete date (day, month, four-digit year) match that date however the page writes
// it: "21 Nov 2026" = "11/21/2026" = "2026-11-21" = "Nov 21, 2026". Nothing else is loosened: any other words keep the
// exact match. A numeric date that reads two ways ("03/04/2026") matches only a numeric date with the same two numbers
// in the same order, on either side. Month names come from one closed English table; a date in another language does
// not parse and keeps the exact match. Deterministic: no locale, no clock.
const MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];
const MONTH_OF = new Map([...MONTH_NAMES.map((m, k) => [m, k + 1]), ...MONTH_NAMES.map((m, k) => [m.slice(0, 3), k + 1]), ["sept", 9]]);
const MON = [...MONTH_OF.keys()].sort((x, y) => y.length - x.length).join("|");
const ORD = "(?:st|nd|rd|th)?";
// Each way a date is written, with how its numbers read. A date never starts inside a word or number, and never runs into
// a longer number; a letter may follow ("2026-11-21T10:00").
const DATE_FORMS = [
  [`(\\d{4})([-/.])(\\d{1,2})\\2(\\d{1,2})`, (g) => ({ y: +g[1], m: +g[3], d: +g[4] })],
  [`(\\d{1,2})([-/.])(\\d{1,2})\\2(\\d{4})`, (g) => numericDate(+g[1], +g[3], +g[4])],
  [`(\\d{1,2})${ORD}[ -](${MON})\\.?,?[ -](\\d{4})`, (g) => ({ y: +g[3], m: MONTH_OF.get(g[2]), d: +g[1] })],
  [`(${MON})\\.? (\\d{1,2})${ORD},? (\\d{4})`, (g) => ({ y: +g[3], m: MONTH_OF.get(g[1]), d: +g[2] })],
].map(([src, read]) => ({ whole: new RegExp(`^${src}$`, "u"), any: new RegExp(`(?<![\\p{L}\\p{N}])${src}(?!\\p{N})`, "gu"), read }));

// "a/b/yyyy": a number over 12 says which is the day; two equal numbers read the same either way; otherwise it is
// ambiguous and keeps its two numbers in the order written.
function numericDate(a, b, y) {
  if (a > 12 && b > 12) return null;
  if (a > 12) return { y, m: b, d: a };
  if (b > 12 || a === b) return { y, m: a, d: b };
  return { y, a, b, ambiguous: true };
}

function realDate(v) {
  if (!v) return null;
  if (v.ambiguous) return v.a >= 1 && v.b >= 1 ? v : null;
  return v.m >= 1 && v.m <= 12 && v.d >= 1 && v.d <= new Date(Date.UTC(v.y, v.m, 0)).getUTCDate() ? v : null;
}

// The date `text` is, as a whole, or null (not a date, part of a sentence, no year, another language).
function parseDate(text) {
  const t = String(text ?? "").replace(/\s+/g, " ").trim().toLowerCase();
  if (t.length > 40) return null;
  for (const f of DATE_FORMS) {
    const g = f.whole.exec(t);
    if (g) return realDate(f.read(g));
  }
  return null;
}

function sameDate(want, seen) {
  if (want.ambiguous || seen.ambiguous) return !!(want.ambiguous && seen.ambiguous && want.y === seen.y && want.a === seen.a && want.b === seen.b);
  return want.y === seen.y && want.m === seen.m && want.d === seen.d;
}

// How many dates in these texts are `want` (from parseDate), in any way of writing it.
function countDates(parts, want) {
  let n = 0;
  for (const part of parts) {
    const hay = String(part ?? "").replace(/\s+/g, " ").toLowerCase();
    for (const f of DATE_FORMS) {
      for (const g of hay.matchAll(f.any)) {
        const seen = realDate(f.read(g));
        if (seen && sameDate(want, seen)) n++;
      }
    }
  }
  return n;
}

// How often `text` is visibly on the page, frames included. A step inside a frame shows its result there (a tab's
// panel in a demo frame, WF-2 Segment D): counting the top document alone read 0 before and 0 after, so the text
// could never be "new" and the step never proven. Before and after are counted the same way.
// `ending`: count only where the text ends a line (pageScripts.countTextEnding), see endsInBoundValue.
// `pageOnly`: the page's text alone, never what a box holds (pageScripts.countPageText).
// Words that are one whole date are counted as that date, however the page writes it (parseDate below); a date is one
// whole token on the page, so `ending` has nothing to add there.
async function countTextAcross(page, text, ending = false, pageOnly = false) {
  const frames = typeof page.frames === "function" ? page.frames() : [page];
  const date = parseDate(text);
  let total = null;
  for (const f of frames) {
    const n = await (date ? evalOn(f, pageScripts.screenTexts, pageOnly).then((parts) => (Array.isArray(parts) ? countDates(parts, date) : null))
      : ending ? evalOn(f, pageScripts.countTextEnding, pageOnly ? { text, pageOnly } : text)
      : pageOnly ? evalOn(f, pageScripts.countPageText, text) : evalOn(f, pageScripts.countTextOccurrences, text))
      .catch(() => null);
    if (typeof n === "number") total = (total || 0) + n;
  }
  return total;
}

// True when a check's words end in a record value this run acts on (`step._bound_values`, every bound row's identifier,
// set by run.js::runPlan). "Saved Carol" then must not be found inside "Saved Carol Ann": the look-alike record.
function endsInBoundValue(step, target) {
  const norm = (v) => String(v || "").replace(/\s+/g, " ").trim().toLowerCase();
  const t = norm(target);
  return asArray(step && step._bound_values).some((v) => {
    const w = norm(v);
    return !!w && t.endsWith(w) && (t.length === w.length || !/[\p{L}\p{N}]/u.test(t[t.length - w.length - 1]));
  });
}

async function capturePreStepSignature(page, countTexts = [], absentTargets = []) {
  try {
    const popups = popupsOpened(page);
    const url = page.url();
    const sig = await evalOn(page, pageScripts.preStepSignature, STATE_CHANGED_SELECTOR);
    // How many times each text the step will look for is already on the page, so a later "it is there" can be told
    // apart from "it was already there" (contrast: a check only proves something if it could have said no).
    // `pageCounts`: the same, without what boxes hold. A form that saves a row moves the typed words from its boxes into
    // the page, so the count with boxes stays the same while the page's own text gains them (WF-11 twin).
    let counts;
    let pageCounts;
    if (countTexts.length) {
      counts = {};
      pageCounts = {};
      for (const t of countTexts) {
        const n = await countTextAcross(page, t);
        if (typeof n === "number") counts[t] = n;
        const p = await countTextAcross(page, t, false, true);
        if (typeof p === "number") pageCounts[t] = p;
      }
    }
    // Whether each box a "this is gone" check names is on the page before the action: gone only proves the
    // action when it was there to begin with (same locator count the check itself uses, across every frame).
    let present;
    if (absentTargets.length) {
      present = {};
      const frames = typeof page.frames === "function" ? page.frames() : [page];
      for (const t of absentTargets) present[t] = await anyRootHasMatch(frames, t);
    }
    // A timed-out probe is unknown, not "definitely empty" — every caller already treats a null
    // signature as "no evidence" (signatureChanged/evaluateAssertion's state_changed branch both
    // fail closed to "no movement detected" rather than false-reporting one). Fabricating
    // {textLen:0, interactiveCount:0} here used to almost always read as "changed" against any
    // real page, which is a false positive on the exact guard meant to catch a repeated action.
    if (!sig || sig === EVAL_TIMED_OUT) return null;
    const { textLen, interactiveCount } = sig;
    return { url, textLen, interactiveCount, ...(counts ? { counts, pageCounts } : {}), ...(present ? { present } : {}), ...(popups !== null ? { popups } : {}) };
  } catch (_) {
    return null;
  }
}

// The texts a step's text_present checks look for. Any of them may already be on the page before the action (the
// label of the control that was clicked, a file name already listed), and finding it afterwards then proves nothing.
function watchedTexts(step, inputs) {
  const out = [];
  for (const a of stepAssertions(step)) {
    if (String(a.type || "").toLowerCase() !== "text_present") continue;
    const t = interpolate(String(a.target), inputs);
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

// The boxes a step's required "gone" checks name, so the baseline can note whether each was there before the action.
function watchedAbsents(step, inputs) {
  const out = [];
  for (const a of stepAssertions(step)) {
    if (String(a.type || "").toLowerCase() !== "selector_absent" || a.required === false) continue;
    const t = interpolate(String(a.target || ""), inputs);
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

// EXEC-24 — "has the page moved since we last looked?" using the signature above. This is the
// zero-token stand-in for what browser-use/CUA get by re-perceiving before every action: when a
// step carries no post-condition, a moved page is the only local evidence that a prior recovery
// attempt may already have taken effect. Same tolerance as the state_changed assertion, so a
// live clock or a lazily-loaded image doesn't read as "the action worked".
// Missing either side → false: absent evidence must never be reported as movement.
function signatureChanged(before, after) {
  if (!before || !after) return false;
  if (before.url !== after.url) return true;
  if (before.interactiveCount !== after.interactiveCount) return true;
  return Math.abs(before.textLen - after.textLen) > STATE_CHANGED_TEXT_LEN_TOLERANCE;
}

// Web-first polling for assertions that don't already poll internally (selector_present rides
// Playwright's own waitFor). Positive checks retry the predicate until it holds or the timeout
// elapses instead of sampling once — a slow render or an optimistic-UI update that lands 400ms
// after the action no longer reads as a required-assertion failure.
const VERIFY_POLL_INTERVAL_MS = 250;
// Negative checks (selector_absent, text_absent) can be trivially true while the page is still
// mid-load (nothing has rendered yet). Requiring the absence to hold through a short stabilization
// window after the first "absent" reading avoids a false pass that a moment later would flip back.
const NEGATIVE_STABILIZE_MS = 500;

// withDeadline moved to page_eval.js (EXEC-29 Guard A) so resolution.js/failure_response.js can
// share it without a require cycle through this file's own `rootCandidates` dependency.
// Re-exported below for existing callers of `require("./assertions").withDeadline`.

async function pollPositive(checkFn, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  // A 0 ms budget means "look once", not "don't look": racing a real browser's answer against an
  // already-expired timer always loses, so the first look gets a short grace (still bounded).
  let attemptDeadline = timeoutMs > 0 ? deadline : deadline + ZERO_BUDGET_LOOK_MS;
  for (;;) {
    let result = false;
    try { result = await withDeadline(checkFn, attemptDeadline, false); } catch (_) { result = false; }
    if (result) return true;
    if (Date.now() >= deadline) return false;
    attemptDeadline = deadline;
    await new Promise(r => setTimeout(r, Math.min(VERIFY_POLL_INTERVAL_MS, Math.max(0, deadline - Date.now()))));
  }
}

const ZERO_BUDGET_LOOK_MS = 1000;

async function pollNegative(checkAbsentFn, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    let absentNow = false;
    try { absentNow = await withDeadline(checkAbsentFn, deadline, false); } catch (_) { absentNow = false; }
    if (absentNow) {
      await new Promise(r => setTimeout(r, NEGATIVE_STABILIZE_MS));
      let stillAbsent = false;
      try { stillAbsent = await withDeadline(checkAbsentFn, deadline, false); } catch (_) { stillAbsent = false; }
      if (stillAbsent) return true;
      // Reappeared during the stabilization window — keep polling if time remains.
    }
    if (Date.now() >= deadline) return false;
    await new Promise(r => setTimeout(r, Math.min(VERIFY_POLL_INTERVAL_MS, Math.max(0, deadline - Date.now()))));
  }
}

// Presence-style locator check across every candidate frame root — true as soon as ANY root has
// a match. Used for selector_present/text_present, where the target is expected to exist
// SOMEWHERE among the roots (usually just [page], or the step's resolved frame chain).
// "These words are on the page": contained in an element's text, any letter case. The same meaning as the before-count
// (page_scripts.countTextOccurrences) and as the question a person answers ("words you should see on the screen"). An
// exact whole-element match (`text="…"`) never found "Asha Rao" inside "Name:Asha Rao".
function textSelector(words) {
  return `internal:text=${JSON.stringify(String(words))}i`;
}

async function anyRootHasMatch(roots, target) {
  for (const root of roots) {
    try {
      const n = await countOn(root.locator(target));
      if (n !== EVAL_TIMED_OUT && n > 0) return true;
    } catch (_) { /* try next root */ }
  }
  return false;
}

// The full type list a compiled Assertion can carry (packages/conxa-core's model docstring) —
// url_changed means "differs from the pre-action URL"; url_pattern means "matches this RegExp".
const URL_ASSERTION_TYPES = new Set(["url_changed", "url_pattern"]);

// Full navigations may need the page-load budget; same-document hash checks do not.
function assertionTimeout(type, target, declaredMs, required = true) {
  const declared = Number(declaredMs) || 0;
  // An advisory check never decides the verdict, so it never earns the page-load wait: that floor once
  // let one failed click burn 60s per advisory URL check.
  if (!required || !URL_ASSERTION_TYPES.has(type)) return declared || 3000;
  if (type === "url_pattern") {
    const raw = String(target || "");
    if (raw.startsWith("#")) return declared || 3000;
  }
  return Math.max(declared, PAGE_LOAD_TIMEOUT_MS);
}

async function evaluateAssertion(roots, page, a, inputs, baseline, capMs = 0, step = null) {
  const type = String(a.type || "").toLowerCase();
  const target = interpolate(String(a.target || ""), inputs);
  const required = a.required !== false;
  // capMs: a re-check on a page that has already settled asks "did it land?", not "will it land?".
  const timeout = capMs > 0 ? Math.min(capMs, assertionTimeout(type, target, a.timeout_ms, required)) : assertionTimeout(type, target, a.timeout_ms, required);
  const startedAt = Date.now();
  let ok = true;
  let recognized = true;
  // Proof of Done: "yes" | "no" | "not_yet" | "cant_tell". A check that passed only because it could not
  // look (no baseline, an unreadable page) is "cant_tell", never "yes": the step still passes, as it
  // always has, but it is not counted as proof.
  let cantTell = false;
  let fresh = false;

  try {
    if (type === "url_changed") {
      // "The URL differs from before the action." The live pre-action URL wins; the compiled
      // target (the recorded before-URL) is only a fallback when no baseline was captured.
      const before = baseline ? baseline.url : target;
      ok = await pollPositive(() => page.url() !== before, timeout);
    } else if (type === "url_pattern") {
      // An empty pattern is a compile-time mistake, not "anything matches" — matching that used
      // to make a required url_pattern assertion a silent no-op.
      ok = !!target && await pollPositive(() => new RegExp(target).test(page.url()), timeout);
    } else if (type === "selector_present") {
      ok = await pollPositive(() => anyRootHasMatch(roots, target), timeout);
    } else if (type === "selector_absent") {
      // Absent must hold in EVERY root, not just one — otherwise a root where it never existed
      // would trivially satisfy "absent" while it's still very much present in another.
      ok = await pollNegative(async () => !(await anyRootHasMatch(roots, target)), timeout);
      // Witness rule: gone proves the action only if the box was there before it. Not seen before (or no baseline)
      // is "can't tell", never a new failure.
      if (ok) {
        if (baseline && baseline.present && baseline.present[target] === true) fresh = true;
        else cantTell = true;
      }
    } else if (type === "text_present") {
      // In an element's text, or in what a visible text box holds (page_scripts.countTextOccurrences): both are "on the
      // screen" to the person who typed these words as proof.
      // Words ending in the run's record are matched only where they end a line ("Saved Carol", "Saved Carol."), never
      // inside a longer look-alike ("Saved Carol Ann"): an element-text match is a substring, so it is not used then.
      const strict = endsInBoundValue(step, target);
      const count = (t) => countTextAcross(page, t, strict);
      ok = await pollPositive(async () => (!strict && await anyRootHasMatch(roots, textSelector(target)))
        || ((await count(target)) || 0) > 0, timeout);
      // A "yes" proves the action only if it would have been a "no" had the action done nothing (CLAUDE.md, witness
      // rule): the words were counted before the action, and there are more of them now. Anything else is "can't
      // tell", never a new failure: no count before (no baseline, an unreadable page), already there and no more of
      // them now (even on a new address: a heading both pages show), or found only where the count cannot see (text
      // sitting hidden, a frame the count did not read). WF-8 8.13: a heading already on the page "proved" a click.
      // Measured the same way both times (visible text in every frame), so hidden text never counts as new.
      const before = ok && baseline && baseline.counts ? baseline.counts[target] : undefined;
      if (ok && typeof before !== "number") cantTell = true;
      if (typeof before === "number" && ok) {
        // More on the page itself also counts: a saved form row takes the words out of the boxes it was typed in
        // (same total), and that row would not be there had the click done nothing.
        const pageBefore = baseline.pageCounts ? baseline.pageCounts[target] : undefined;
        const grew = async () => {
          const n = await count(target);
          if (typeof n === "number" && n > before) return true;
          if (typeof pageBefore !== "number") return false;
          const p = await countTextAcross(page, target, strict, true);
          return typeof p === "number" && p > pageBefore;
        };
        // Absent before: the check above already waited for the words, so one count says whether it saw them.
        const more = before === 0 ? await grew().catch(() => false) : await pollPositive(grew, Math.min(timeout, 1500));
        if (!more) cantTell = true;
        fresh = more;
      }
    } else if (type === "text_absent") {
      ok = await pollNegative(async () => !(await anyRootHasMatch(roots, textSelector(target))), timeout);
    } else if (type === "value_equals") {
      const expected = interpolate(String(a.expected ?? ""), inputs);
      const normExpected = normText(expected);
      // Normalized-exact match, else "field contains expected": tolerates masked/formatted fields (phone, currency)
      // whose raw value never equals the typed text.
      const matches = (actual) => {
        if (typeof actual !== "string") return false;
        const normActual = normText(actual);
        return normActual === normExpected || (!!normExpected && normActual.includes(normExpected));
      };
      const readField = async (loc) => {
        const v = await evalOn(loc, pageScripts.readFieldValue, undefined, VERIFY_POLL_INTERVAL_MS * 2);
        return v === EVAL_TIMED_OUT ? null : v; // a rich-text box answers with its text, not a `value`
      };
      ok = await pollPositive(async () => {
        for (const root of roots) {
          try {
            if (matches(await readField(root.locator(target).first()))) return true;
          } catch (_) { /* try next root */ }
        }
        // The stored selector may name something the page rewrites (generated ids change on every render):
        // ask the step's own identity for the field it typed into.
        if (step) {
          try {
            const loc = await resolveStep(page, step, inputs);
            if (matches(await readField(loc && loc.first ? loc.first() : loc))) return true;
          } catch (_) { /* the field is not there (yet) */ }
        }
        return false;
      }, timeout);
    } else if (type === "state_changed") {
      // No compile-time target — confirms the action produced SOME observable effect (URL,
      // interactive-element count, or a non-trivial body-text delta) rather than silently
      // no-opping. Only meaningful when a pre-action baseline was captured.
      if (!baseline) {
        ok = true; // no baseline captured (e.g. resumed mid-run) — don't fail on a technicality
        cantTell = true;
      } else {
        ok = await pollPositive(async () => {
          // A tab this page opened since the baseline is the action's effect, whatever the page itself shows.
          const popups = popupsOpened(page);
          if (typeof baseline.popups === "number" && typeof popups === "number" && popups > baseline.popups) return true;
          const after = await capturePreStepSignature(page);
          if (!after) cantTell = true;
          return !after
            ? true
            : after.url !== baseline.url ||
              after.interactiveCount !== baseline.interactiveCount ||
              Math.abs(after.textLen - baseline.textLen) > STATE_CHANGED_TEXT_LEN_TOLERANCE;
        }, timeout);
      }
    } else {
      // An unrecognized type is a compiler/runtime version mismatch, not "nothing to check" —
      // silently passing here meant a typo'd or newer assertion type never actually verified
      // anything while looking like it had.
      recognized = false;
      ok = false;
    }
  } catch (err) {
    ok = false;
  }

  // An unrecognized type could not be checked at all (it still fails a required assertion, as before).
  // W-E (owner decision 2026-11-21): a button's or link's label that newly appeared (source "control") appears whether
  // or not the action worked, and a look-alike "Edit" elsewhere satisfies it. The compiler's own (advisory) one is never
  // proof: when it holds it says "can't tell", so a step with nothing else to show for it is "couldn't confirm". One a
  // person ticked (required) is their own word for "it worked": it counts as a check that passed, no more.
  const capped = a.source === "control" && !required;
  const answer = !recognized ? "cant_tell" : !ok ? "no" : cantTell || capped ? "cant_tell" : "yes";
  // `fresh` says "this finds the result itself". A control's label never does.
  const findsTheResult = fresh && answer === "yes" && a.source !== "control";
  return { type, target, required, ok, recognized, answer, ...(findsTheResult ? { fresh: true } : {}), elapsed_ms: Date.now() - startedAt };
}

// What a set of results proves, in the same four answers. Any required "no" is a "no"; otherwise one
// real "yes" is a "yes"; otherwise nothing could be read, which is "cant_tell". Advisory results still
// count as proof when they hold, and never as failure.
function summarizeAnswers(results) {
  if (results.some((r) => r.required && r.answer === "no")) return "no";
  if (results.some((r) => r.answer === "yes")) return "yes";
  return "cant_tell";
}

async function verifyStep(page, step, inputs, baseline = null, dialogQueue = null, { capMs = 0 } = {}) {
  // A native alert/confirm/prompt blocks the page's renderer thread until the *next* step
  // (dialog_accept/dialog_dismiss, which carries no assertions of its own) resolves it — no
  // DOM/URL/value check on THIS step can possibly answer while that's true, and every one of
  // them would hang forever on the CDP round-trip (see pollPositive/pollNegative's own
  // deadline guard above, which is the fallback for the case a dialog opens mid-poll instead
  // of before verification starts). This isn't a workaround: the step's real post-condition
  // ("a dialog opened") already happened, so skipping is the correct verdict, not a skipped one.
  if (dialogQueue && dialogQueue.length) {
    return { pass: true, channel: "dialog_pending", answer: "not_yet", evidence: "a JS dialog is open", results: [] };
  }
  const assertions = stepAssertions(step);
  if (!assertions.length) return { pass: true, channel: "none", answer: "cant_tell", evidence: "no-assertions", results: [] };

  // A post-condition for a step whose action happened inside an iframe is almost always about
  // that same iframe (a confirmation message, a field's new value, ...) — resolve assertions
  // against the step's own frame chain, not blindly the top-level page. Unlike action resolution
  // (rootCandidates/resolveStep), a broken frame lookup here falls back to [page] rather than
  // failing outright: verification has no "wrong click" risk, only a "checked the wrong document"
  // risk, which naturally surfaces as a failed assertion rather than corrupting page state.
  const frameRoots = await rootCandidates(page, step, inputs);
  const roots = frameRoots.length ? frameRoots : [page];

  // Every assertion is evaluated — not just up to the first required failure — so a failed step
  // carries a full audit of what held and what didn't (advisory included). This is the dataset the
  // fleet dashboard needs to see an assertion decaying before it becomes a hard failure.
  // "The page changed" (state_changed) is checked last: when a required check on this step already saw a change that
  // would not be there had the action done nothing (`fresh`: new words, a box that was there and is gone), the page
  // did change, even when the change is under the text tolerance ("Name:person" after Submit, WF-13 F6).
  const isStateChanged = (a) => String((a && a.type) || "").toLowerCase() === "state_changed";
  const results = new Array(assertions.length);
  const order = assertions.map((_, k) => k).sort((x, y) => isStateChanged(assertions[x]) - isStateChanged(assertions[y]));
  for (const k of order) {
    const a = assertions[k];
    if (isStateChanged(a) && results.some((r) => r && r.required && r.fresh && r.answer === "yes")) {
      results[k] = { type: "state_changed", target: "", required: a.required !== false, ok: true, recognized: true,
        answer: "yes", proven_by: "fresh_check", elapsed_ms: 0 };
      continue;
    }
    results[k] = await evaluateAssertion(roots, page, a, inputs, baseline, capMs, step);
  }
  const failing = results.find((r) => !r.ok && r.required) || null;

  if (failing) {
    return { pass: false, channel: failing.type, answer: "no", evidence: failing.target, results };
  }
  return { pass: true, channel: "all", answer: summarizeAnswers(results), evidence: `${assertions.length} assertion(s)`, results };
}

// Whether any assertion on this step is required (enforced) — gates the extra cost of capturing
// a pre-action baseline and of re-verifying after a recovery remedy.
function hasRequiredAssertion(step) {
  return stepAssertions(step).some(a => a && a.required !== false);
}

// Whether a pre-action page signature must be captured for this step. Two reasons now:
// a state_changed assertion needs it as its baseline, and (EXEC-24) any non-idempotent step
// needs it as the evidence the recovery guard consults before re-dispatching. The `required`
// filter was dropped from the assertion arm deliberately — compiled assertions currently ship
// advisory-only, so requiring `required` here would leave every state_changed check baseline-less.
function needsStateChangedBaseline(step) {
  if (isNonIdempotent(step)) return true;
  return stepAssertions(step).some(a => {
    const t = a && String(a.type || "").toLowerCase();
    return t === "state_changed" || t === "url_changed";
  });
}

module.exports = {
  stepAssertions,
  normText,
  capturePreStepSignature,
  signatureChanged,
  VERIFY_POLL_INTERVAL_MS,
  pollPositive,
  pollNegative,
  evaluateAssertion,
  verifyStep,
  hasRequiredAssertion,
  needsStateChangedBaseline,
  watchedTexts,
  watchedAbsents,
  withDeadline,
  parseDate,
  countDates,
};
