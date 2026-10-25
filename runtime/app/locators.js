"use strict";
// Locator seam, extracted from run.js: the three control-flow modes behind
// withLocator (PRIMARY retry loop / agent-override validation / plain string),
// selector derivation helpers, and the small action helpers handlers use.
const { signalToLocator } = require("./resolve_adapter");
const { interpolate } = require("./interpolate");
const { countOn, EVAL_TIMED_OUT } = require("./page_eval");
const {
  ACTION_TIMEOUT_MS,
  SECONDARY_ACTION_TIMEOUT_MS,
  VIRTUAL_SCROLL_BUDGET_MS,
} = require("./run_config");
const { asObject, asArray, unique, isNonIdempotent } = require("./step_utils");
const {
  PRIMARY,
  resolveStep,
  gateLocator,
  isFileInputStep,
  validateOverrideSelector,
  locatorCandidates,
  rootCandidates,
} = require("./resolution");

// EXEC-24 — the dispatch seam. Everything withLocator does before it calls `fn` is
// resolve → gate → wait and cannot touch the page; `fn` IS the action. An error that comes
// out of `fn` therefore means the page MAY have been acted on, and the recovery cascade must
// not blindly re-dispatch on top of it (cascade.js). Deliberately conservative: a Playwright
// actionability timeout raised *inside* click() never actually clicked, so this over-reports —
// which is the safe direction. It says "may have acted", never "did act".
function markMayHaveActed(err) {
  if (err && typeof err === "object") {
    try { err.mayHaveActed = true; } catch (_) { /* frozen error — nothing to do */ }
  }
  return err;
}

async function actAndMark(fn, ...args) {
  try {
    return await fn(...args);
  } catch (err) {
    throw markMayHaveActed(err);
  }
}

async function withLocator(page, step, inputs, selector, timeout, fn) {
  // PRIMARY identity-bundle path: late-bind resolve → gate → act, RE-TRIED within the action
  // budget. A transient state (target still hydrating, a menu still opening/animating) re-resolves
  // a fresh locator on each attempt instead of dumping straight into recovery — restoring, for the
  // scored multi-signal path, the auto-wait that string selectors get via waitFor. (Fixes the
  // Tier-1 timing race where step N+1 fired before step N's menu had finished opening.)
  if (selector === PRIMARY && !step._explicit_selector) {
    let deadline = Date.now() + (timeout || ACTION_TIMEOUT_MS);
    // BUILD-30: one plain object per attempt (not module-level state), threaded into
    // resolveStep so a miss can try a bounded scroll pass before giving up — see
    // resolution.js::maybeScrollForVirtualization. `attemptedScroll` only becomes true when
    // that scroll was grounded in real compiled evidence (a virtualized_container hint or an
    // entity binding), which is what the one-time deadline extension below is gated on.
    const scrollState = { passes: 0, extended: false, attemptedScroll: false };
    let lastErr = null;
    for (;;) {
      try {
        const locator = await resolveStep(page, step, inputs, scrollState);   // one attempt; loop owns the wait
        await gateLocator(locator.first(), step);
        return await actAndMark(fn, locator);
      } catch (err) {
        lastErr = err;
        // NOTE (EXEC-24): this loop deliberately keeps retrying even when `err.mayHaveActed`
        // is set. It is the Playwright-shaped wait — same step, same signals, re-resolved until
        // actionable within one action budget — not the cascade's "try a different strategy"
        // ladder, and the mark over-reports (an actionability timeout inside click() never
        // clicked). Bailing here would remove the auto-wait this loop exists to provide.
        // Ambiguity / recompile-required / bad input cannot be fixed by waiting — surface
        // immediately rather than re-resolving until the action deadline.
        if (err && (err.ambiguous || err.recompileRequired || err.badInput)) throw err;
        // BUILD-30: extend the wait once a scroll pass grounded in real evidence has actually
        // run, and only for a miss where no locator was ever produced (mayHaveActed unset) —
        // nothing was acted on, so a longer wait cannot turn a clean failure into a repeated
        // action. A plain broken selector (no virtualization evidence) gets none of this;
        // its timing is exactly what it was before this feature existed.
        if (
          !scrollState.extended && scrollState.attemptedScroll &&
          err && (err.entityNotFound || err.resolveMiss) && !err.mayHaveActed
        ) {
          scrollState.extended = true;
          deadline = Date.now() + VIRTUAL_SCROLL_BUDGET_MS;
        }
        if (Date.now() >= deadline) throw err;
        await page.waitForTimeout(120);
      }
    }
  }

  let candidates;
  if (selector === PRIMARY && step._agent_override) {
    // Agent-supplied recovery selector (Tier 3/4 closing edge) — gate it the same way the
    // primary path gates every compiled signal, instead of falling straight into plain
    // string-mode's unguarded .first().
    const validation = await validateOverrideSelector(page, step, inputs);
    if (!validation.valid) {
      const message = validation.reason === "frame-not-found"
        ? "The containing frame/iframe could not be located — cannot validate an element selector inside it"
        : validation.reason === "no-match"
          ? "Agent recovery selector matched no element on the page"
          : "Agent recovery selector was ambiguous (no candidate cleared the uniqueness margin)";
      throw Object.assign(
        new Error(message),
        { overrideValidationFailed: true, overrideReason: validation.reason, overrideCandidates: validation.candidates },
      );
    }
    candidates = [validation.loc];
  } else {
    // Explicit recovery selector (PRIMARY + _explicit_selector, non-agent) or plain string mode.
    candidates = selector === PRIMARY
      ? await locatorCandidates(page, step, inputs, step._explicit_selector)
      : await locatorCandidates(page, step, inputs, selector);
  }
  if (!candidates.length) throw new Error("Missing selector");

  let lastErr = null;
  let sawAmbiguous = false;
  for (const locator of candidates) {
    try {
      if (timeout && selector !== PRIMARY) {
        await locator.first().waitFor({
          state: isFileInputStep(step) ? "attached" : "visible",
          timeout,
        });
      }
      // Never blindly act on candidate[0] of a many-match locator — this is string-mode
      // (a plain/explicit/compiled selector, not the PRIMARY identity-bundle path, which
      // already enforces resolver.js's own uniqueness margin). A root whose selector matches
      // more than one element is not resolved; try the next root instead of guessing which one.
      // 0 matches is NOT treated as ambiguous or as a reason to skip — Playwright's own click()/
      // fill()/etc. auto-wait for the element to appear, exactly as before this check existed;
      // only "more than one" is something this loop can usefully act on. Failing to even count
      // (a genuinely non-Locator-like object) is treated the same as "can't tell" — proceed and
      // let the action itself surface the real problem, rather than block on a safety net that
      // itself couldn't run.
      let count = -1;
      try { count = await countOn(locator); } catch (_) { /* can't tell — proceed */ }
      if (count !== EVAL_TIMED_OUT && count > 1) { sawAmbiguous = true; continue; }
      await gateLocator(locator.first(), step);
      return await actAndMark(fn, locator);
    } catch (err) {
      lastErr = err;
      // Unlike the PRIMARY loop above, these candidates are DIFFERENT elements. Once one of
      // them may have acted, trying the next would act a second time on a page the first one
      // already changed — exactly the EXEC-24 failure. Only for step types where a second
      // dispatch actually duplicates an effect; a fill/select still walks the whole list.
      if (err && err.mayHaveActed && isNonIdempotent(step)) throw err;
    }
  }

  if (sawAmbiguous && !lastErr) {
    throw Object.assign(new Error(`Selector matched more than one element: ${String(selector)}`), { ambiguous: true });
  }
  throw lastErr || Object.assign(new Error(`Locator not found: ${String(selector)}`), { resolveMiss: true });
}

async function withLocatorPair(page, step, inputs, srcSelector, dstSelector, timeout, fn) {
  const src = interpolate(srcSelector || "", inputs);
  const dst = interpolate(dstSelector || "", inputs);
  if (!src || !dst) throw new Error("Missing selector");

  let lastErr = null;
  let sawAmbiguous = false;
  for (const root of await rootCandidates(page, step, inputs)) {
    try {
      const srcLoc = root.locator(src);
      const dstLoc = root.locator(dst);
      if (timeout) {
        await srcLoc.first().waitFor({ state: "visible", timeout });
        await dstLoc.first().waitFor({ state: "visible", timeout });
      }
      // Same no-guessing rule as withLocator: a source or target that matches more than one
      // element is not resolved, and dragging candidate[0] of either risks acting on the wrong
      // element entirely. A count that can't be determined at all is treated the same as "not
      // ambiguous" — dragTo below still has its own actionability wait/timeout.
      let srcCount = -1, dstCount = -1;
      try { srcCount = await countOn(srcLoc); dstCount = await countOn(dstLoc); } catch (_) { /* can't tell — proceed */ }
      if ((srcCount !== EVAL_TIMED_OUT && srcCount > 1) || (dstCount !== EVAL_TIMED_OUT && dstCount > 1)) {
        sawAmbiguous = true; continue;
      }
      return await actAndMark(fn, srcLoc, dstLoc);
    } catch (err) {
      lastErr = err;
      // Same reasoning as withLocator's candidate loop: each root is a different document, so
      // retrying after a possible drag would drag twice (drag_drop is non-idempotent by nature).
      if (err && err.mayHaveActed) throw err;
    }
  }

  if (sawAmbiguous && !lastErr) {
    throw Object.assign(new Error(`Drag selector matched more than one element: ${src} -> ${dst}`), { ambiguous: true });
  }
  throw lastErr || new Error(`Locator pair not found: ${src} -> ${dst}`);
}

function compiledSelectors(step, inputs) {
  return asArray(step.compiled_selectors)
    .filter(selector => typeof selector === "string" && selector.trim())
    .map(selector => interpolate(selector, inputs));
}

// step.selector is the only field the compiler emits (the display form of
// target.primary_selector — skill_package_builder_saved_skill.py). css_selector/target.css
// were an older pack shape nothing current writes.
function baseSelector(step, inputs) {
  return interpolate(step.selector || "", inputs);
}

function stepSelector(step, inputs) {
  const compiled = compiledSelectors(step, inputs);
  return compiled[0] || baseSelector(step, inputs);
}

function stepWithSelector(step, selector) {
  // Recovery injects an explicit selector — force string mode in withLocator/PRIMARY.
  return { ...step, _explicit_selector: selector };
}

function textSelector(value) {
  const text = typeof value === "string" ? value.trim() : "";
  return text ? `text=${JSON.stringify(text)}` : "";
}

// Step executor helpers

async function runLocatorStep(page, step, inputs, action, selector = PRIMARY) {
  await withLocator(page, step, inputs, selector, 0, async locator => action(locator.first(), locator));
}

// True when the step has a resolvable primary target (identity_bundle signals or an explicit
// recovery selector) — used by optional-target handlers (scroll/focus).
function hasTarget(step, inputs) {
  if (step._explicit_selector) return true;
  return asArray(asObject(step.identity_bundle).signals).some(s => s && s.selector);
}

// Deliberately no "retry against .last() when something intercepts the click" fallback — that
// used to silently click a DIFFERENT element than the one that was covered (never verified to be
// the same target), which is worse than a clean failure. An overlay covering the target is
// cascade.js's dismiss-overlay remedy's job, not something to route around here by guessing.
async function clickFirst(locator, options) {
  return await locator.first().click(options);
}

function checkboxValue(step, inputs) {
  return String(interpolate(step.value || "true", inputs)).toLowerCase() !== "false";
}

// Phase 7: hover each element in the precompiled hover_chain before acting (menu reveals, etc.).
// Hover signals use Playwright grammar, so resolve each via signalToLocator (not raw locator()).
async function walkHoverChain(page, step, inputs) {
  const chain = asArray(asObject(step.handler_hints).hover_chain)
    .filter(sig => sig && sig.selector)
    .sort((a, b) => (b.durability || 0) - (a.durability || 0));
  const roots = await rootCandidates(page, step, inputs);
  for (const sig of chain) {
    try {
      for (const root of roots) {
        const loc = signalToLocator(root, sig, interpolate, inputs);
        if (!loc) continue;
        await loc.first().hover({ timeout: SECONDARY_ACTION_TIMEOUT_MS });
        break;
      }
    } catch (err) {
      // Hover is best-effort — if the reveal element is gone the target may already be visible.
    }
  }
}

// The compiler resolves src/dst at build time and always emits them as src_selector/
// dst_selector directly (saved_skill.py) — the JSON-in-step.value shape this used to also parse
// as a fallback is never produced.
function parseDragSelectors(step, inputs) {
  return {
    srcSelector: interpolate(step.src_selector || "", inputs),
    dstSelector: interpolate(step.dst_selector || stepSelector(step, inputs), inputs),
  };
}

function parseKeyboardShortcut(value) {
  let keyStr = value;
  try {
    const parsed = JSON.parse(keyStr);
    const modifiers = parsed.modifiers || {};
    const parts = [];
    if (modifiers.ctrl) parts.push("Control");
    if (modifiers.meta) parts.push("Meta");
    if (modifiers.shift) parts.push("Shift");
    if (modifiers.alt) parts.push("Alt");
    if (parsed.key) parts.push(parsed.key.length === 1 ? parsed.key.toUpperCase() : parsed.key);
    if (parts.length) keyStr = parts.join("+");
  } catch (_) {}
  return keyStr;
}

module.exports = {
  PRIMARY,
  markMayHaveActed,
  withLocator,
  withLocatorPair,
  compiledSelectors,
  baseSelector,
  stepSelector,
  stepWithSelector,
  textSelector,
  runLocatorStep,
  hasTarget,
  clickFirst,
  checkboxValue,
  walkHoverChain,
  parseDragSelectors,
  parseKeyboardShortcut,
};
