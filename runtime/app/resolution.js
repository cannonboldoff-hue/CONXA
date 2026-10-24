"use strict";
// Target/frame resolution seam, extracted from run.js: frame-root walking,
// primary identity-bundle resolution, the pre-action GATE, the agent-override
// uniqueness gate, and the frame-scoped DOM inventory used by failure payloads.
const pageScripts = require("./page_scripts");
const { resolve: resolveSignals, scoreCandidate, DEFAULT_UNIQUE_MARGIN, DEFAULT_CONFIDENCE_THRESHOLD } = require("./resolver");
const { gatherCandidates, bundleFingerprint, _extractDescriptor, toLocator } = require("./resolve_adapter");
const { STALE_RE } = require("./recovery");
const { interpolate } = require("./interpolate");
const {
  envNumber,
  VIRTUAL_SCROLL_ENABLED,
  VIRTUAL_SCROLL_MAX_PASSES,
} = require("./run_config");
const { unique, asObject, asArray } = require("./step_utils");
const { evalOn, countOn, EVAL_TIMED_OUT } = require("./page_eval");

// Frame roots are driven solely by identity_bundle.frame_chain (durability-ranked signals per
// iframe level). Each frame signal selector is a CSS attribute selector (iframe[name=…] etc.),
// so it feeds frameLocator() directly.
//
// Async because it must actually VERIFY each iframe element exists (root.locator(selector).count())
// before descending into it with frameLocator() — frameLocator() itself is lazy and happily hands
// back a usable-looking FrameLocator for a selector that matches nothing, so relying on it alone
// can never distinguish "the frame is gone/churned to a new identity" from "the frame exists and
// has content" (confirmed: a synthetic test with a frame_chain selector matching no real iframe
// still produced a non-empty roots array before this check was added, since frameLocator()
// construction never fails on its own).
async function rootCandidates(page, step, inputs) {
  const frameChain = asArray(asObject(step && step.identity_bundle).frame_chain);
  if (!frameChain.length) return [page];

  let roots = [page];
  for (const frameSpec of frameChain) {
    const sigs = asArray(frameSpec.signals)
      .filter(s => s && s.selector)
      .sort((a, b) => (b.durability || 0) - (a.durability || 0));
    const next = [];
    for (const root of roots) {
      if (!root || typeof root.frameLocator !== "function") continue;
      for (const s of sigs) {
        const selector = interpolate(String(s.selector), inputs);
        let exists = false;
        try {
          const n = await countOn(root.locator(selector));
          exists = n !== EVAL_TIMED_OUT && n > 0;
        } catch (_) { exists = false; }
        if (!exists) continue;
        next.push(root.frameLocator(selector));
      }
    }
    roots = next;
    if (!roots.length) break;
  }

  // A non-empty frame_chain that resolved to zero roots means the containing iframe itself
  // could not be located (e.g. its identifying attribute churned to a new value on reattach).
  // This must NOT silently fall back to the top-level page — a same-selector element there
  // could be wrongly acted on in place of a target that actually lives in a frame we lost
  // track of. Return [] (distinct from the "no frame_chain at all" case above, which correctly
  // returns [page]) so callers can diagnose "frame not found" instead of a generic miss.
  return roots;
}

// True only when the step's own frame_chain exists but rootCandidates() came back empty — i.e.
// the frame lookup itself failed, not just "no frame_chain to resolve" (that returns [page]).
function isFrameNotFound(step, roots) {
  return asArray(asObject(step && step.identity_bundle).frame_chain).length > 0 && roots.length === 0;
}

// PROD-3 entity binding — narrows already frame-scoped roots to the ONE container holding this
// run's actual record (e.g. the table row containing Invoice #12345), so a same-looking element
// on a different row can never be resolved or recovered onto. Requires an EXACT single match:
// zero or an unresolved identifier fails closed ([]), and more than one match is treated the same
// way as zero — an ambiguous binding is not a binding. Never falls back to the unscoped roots.
async function entityRoots(roots, step, inputs) {
  const eb = asObject(step && step.entity_binding);
  if (!eb.container_selector || !eb.identifier) return roots;
  const wanted = interpolate(String(eb.identifier), inputs).trim();
  if (!wanted) return [];
  const out = [];
  // `absent` is true only when the list of records is on the page and NONE of them is this run's
  // record: a provable "it isn't there", as opposed to an ambiguous match (more than one), a hung
  // count, or a page that hasn't rendered the list. Only that case may be skipped by on_missing.
  let listPresent = false;
  let anyMatch = false;
  for (const root of roots) {
    if (!root || typeof root.locator !== "function") continue;
    let count = 0;
    let rows = null;
    try {
      const all = root.locator(eb.container_selector);
      // (a locator that can't be counted unfiltered simply never proves the list is there)
      const total = typeof all.count === "function" ? await countOn(all) : EVAL_TIMED_OUT;
      if (total !== EVAL_TIMED_OUT && total > 0) listPresent = true;
      rows = all.filter({ hasText: wanted });
      const n = await countOn(rows);
      count = n === EVAL_TIMED_OUT ? 0 : n; // fail closed on a hung count — never guess a match
      if (n === EVAL_TIMED_OUT || n > 0) anyMatch = true;
    } catch (_) { count = 0; anyMatch = true; }
    if (count === 1) out.push(rows.first());
  }
  // Non-enumerable so the result still compares equal to a plain array of roots.
  Object.defineProperty(out, "absent", { value: out.length === 0 && listPresent && !anyMatch, enumerable: false });
  return out;
}

// True only when the step carries an entity binding but narrowing it against the live page
// found no single matching row — i.e. the bound record isn't on the page (removed, filtered out,
// or never existed here), not just "no binding to apply".
function isEntityNotFound(step, roots) {
  const eb = asObject(step && step.entity_binding);
  return !!(eb.container_selector && eb.identifier) && roots.length === 0;
}

// EXEC-38 — the inverse of entityRoots: given a for_each step's row spec, list every row
// matching the container selector (top-level page only — no iframe scoping in this first cut)
// and derive each row's own full text as its identifier. entityRoots later re-locates that exact
// row with `.filter({ hasText: identifier })`, so using the row's own full text as its own
// identifier is self-consistent by construction — it always matches itself, and if two rows
// happen to share identical text, entityRoots's existing "exactly one match" gate fails that
// iteration closed rather than guessing, which is the correct non-guessing behavior, not a bug
// to work around here.
//
// Snapshot semantics: call this ONCE, up front — the for_each executor never re-enumerates
// mid-loop. Re-enumerating over a list that mutates as you act on it (a row you just processed
// disappears from a queue view) is how a batch silently skips or double-processes rows.
async function enumerateRows(page, spec, cap) {
  const containerSelector = String((spec && spec.container_selector) || "").trim();
  if (!containerSelector) return [];
  let all = [];
  try {
    all = await page.locator(containerSelector).all();
  } catch (_) {
    return [];
  }
  const seen = new Set();
  const out = [];
  for (const row of all) {
    if (out.length >= cap) break;
    let text = "";
    try {
      const t = await evalOn(row, (el) => (el.innerText || el.textContent || "").trim());
      text = t === EVAL_TIMED_OUT ? "" : String(t || "").trim();
    } catch (_) {
      text = "";
    }
    if (!text || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out;
}

// EXEC-38 items-source sibling to enumerateRows: a for_each driven by a runtime input
// (`items: "<input_name>"`) instead of a DOM container. `raw` is the named input's current
// value — a comma-separated string (the only shape a plain `text` input can carry) or, for a
// caller that already has one, an array. Trim/drop-empty/de-dup/cap mirror enumerateRows
// exactly, so both sources behave identically to the loop executor and its telemetry.
function splitListInput(raw, cap) {
  let items;
  if (Array.isArray(raw)) {
    items = raw.map((v) => String(v));
  } else if (typeof raw === "string") {
    items = raw.split(",");
  } else {
    return [];
  }
  const seen = new Set();
  const out = [];
  for (const item of items) {
    if (out.length >= cap) break;
    const text = item.trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out;
}

// BUILD-30: a virtualized grid (AG Grid, react-window, TanStack Virtual, ...) only renders the
// rows currently in the scrolled viewport, so a step's target/row can be entirely absent from
// the DOM until scrolled into range — indistinguishable, before this, from genuine breakage.
// Called by resolveStep on either miss path (entity_not_found or a plain resolve_miss); the
// caller re-runs its own lookup afterward and only fails for real if that also comes up empty.
// `scrollState` is one plain object created fresh per withLocator PRIMARY attempt (see
// locators.js) — not module-level state — so nothing here leaks across steps or concurrent runs.
async function maybeScrollForVirtualization(step, frameRoots, scrollState) {
  if (!VIRTUAL_SCROLL_ENABLED || !scrollState || !frameRoots.length) return false;
  if (scrollState.passes >= VIRTUAL_SCROLL_MAX_PASSES) return false;

  const root = frameRoots[0];
  if (!root || typeof root.locator !== "function") return false;

  const hints = asObject(step && step.handler_hints);
  const eb = asObject(step && step.entity_binding);
  // Container resolution order: the compiled hint first, then the entity-binding row container
  // (rescues a skill compiled before this shipped), then (empty selector) the page's own
  // dominant scrollable element — but only for a compiled choice/dropdown-kind control, never
  // for an ordinary step with no virtualization evidence at all. Without this last guard, an
  // unrelated broken selector would also scroll some unrelated part of the page on every
  // retry — this is the regression guard that keeps an unrelated miss's behavior untouched.
  const selector = String(hints.virtualized_container || eb.container_selector || "");
  if (!selector && hints.control_kind !== "choice") return false;

  scrollState.passes += 1;
  // Deadline extension (locators.js) is gated on real compiled evidence, never on the
  // dominant-scrollable guess alone — an ordinary broken selector must not get a longer wait
  // just because some unrelated scrollable element exists on the page.
  if (selector) scrollState.attemptedScroll = true;

  const target = root.locator(selector || ":root").first();
  const script = selector ? pageScripts.scrollVirtualContainerStep : pageScripts.scrollDominantScrollableElement;
  let outcome = null;
  try {
    outcome = await evalOn(target, script, undefined, 500);
  } catch (_) {
    outcome = null;
  }
  if (outcome && outcome.advanced) {
    step._used_virtual_scroll = true;
  }
  return !!(outcome && outcome !== EVAL_TIMED_OUT && outcome.advanced);
}

async function locatorCandidates(page, step, inputs, selector) {
  const resolved = interpolate(selector || "", inputs);
  if (!resolved) return [];
  let roots = await rootCandidates(page, step, inputs);
  roots = await entityRoots(roots, step, inputs);
  // toLocator, not root.locator(): `resolved` may be a role=/text= string (compiled
  // `internal:` grammar, or its unprefixed display form) — those need tolerant
  // accessible-name matching, not Playwright's exact-match selector engine.
  return roots.map(root => toLocator(root, resolved));
}

// Sentinel selector marking "resolve the step's primary target via identity_bundle.signals".
const PRIMARY = Symbol("primary-target");

// Resolve the step's primary target through the pure resolver over the live DOM.
// Returns a single Playwright locator for the chosen element, or throws a classified error.
// `scrollState` (optional — see maybeScrollForVirtualization) lets a miss try one scroll pass
// before giving up, for a step whose target may be virtualized out of the DOM.
async function resolveStep(page, step, inputs, scrollState) {
  const bundle = asObject(step.identity_bundle);
  const signals = asArray(bundle.signals).filter(s => s && s.selector);
  if (!signals.length) {
    throw Object.assign(
      new Error("Step has no identity_bundle.signals — pack must be recompiled"),
      { recompileRequired: true },
    );
  }
  const frameRoots = await rootCandidates(page, step, inputs);
  if (isFrameNotFound(step, frameRoots)) {
    throw Object.assign(
      new Error("Containing frame could not be located (identity may have changed)"),
      { frameNotFound: true },
    );
  }
  let roots = await entityRoots(frameRoots, step, inputs);
  if (isEntityNotFound(step, roots)) {
    if (await maybeScrollForVirtualization(step, frameRoots, scrollState)) {
      roots = await entityRoots(frameRoots, step, inputs);
    }
    if (isEntityNotFound(step, roots)) {
      throw Object.assign(
        new Error("Bound record could not be uniquely located on the page — refusing to act on a different row"),
        { entityNotFound: true, entityAbsent: roots.absent === true },
      );
    }
  }
  const fp = bundleFingerprint(bundle, interpolate, inputs);
  let map = await gatherCandidates(roots, signals, interpolate, inputs);
  let result = resolveSignals(signals, fp, { queryAll: sel => map[sel] || [] }, {});
  if (!(result && result.node && result.node._loc) && !(result && result.ambiguous)) {
    if (await maybeScrollForVirtualization(step, frameRoots, scrollState)) {
      map = await gatherCandidates(roots, signals, interpolate, inputs);
      result = resolveSignals(signals, fp, { queryAll: sel => map[sel] || [] }, {});
    }
  }
  if (result && result.node && result.node._loc) {
    return result.node._loc;
  }
  if (result && result.ambiguous) {
    throw Object.assign(new Error("Ambiguous element resolution (no signal cleared uniqueness gate)"), { ambiguous: true });
  }
  throw Object.assign(new Error("Element not found (resolve miss)"), { resolveMiss: true });
}

const GATE_ENABLED = process.env.CONXA_GATE !== "0";
const GATE_BUDGET_MS = envNumber("CONXA_GATE_BUDGET_MS", 600);

function isFileInputStep(step) {
  const s = asObject(step);
  const kind = String(s.type || "").toLowerCase();
  if (kind === "upload" || kind === "upload_intent") return true;
  const fp = asObject(asObject(s.identity_bundle).fingerprint);
  return String(fp.input_type || "").toLowerCase() === "file";
}

// Phase 8: pre-action GATE — confirm the element is attached, visible, RAF-stable, and enabled
// before acting. Budget is confidence-adaptive (a high-confidence step gets a shorter wait).
// Best-effort: gate failures throw so the caller can try the next candidate / recovery.
async function gateLocator(loc, step) {
  if (!GATE_ENABLED) return;
  const conf = Number(asObject(step).confidence);
  const budget = Number.isFinite(conf) && conf >= 0.85
    ? Math.round(GATE_BUDGET_MS / 2)
    : GATE_BUDGET_MS;

  // Hidden <input type=file> is never visible (Drive keeps one at 0×0). Playwright's
  // setInputFiles is designed for that. Waiting for "visible" is a guaranteed miss.
  if (isFileInputStep(step)) {
    await loc.waitFor({ state: "attached", timeout: budget });
    return;
  }

  await loc.waitFor({ state: "visible", timeout: budget });

  // RAF-stable: bounding box must be unchanged across two animation frames. EXEC-29 Guard A:
  // routed through evalOn/withDeadline rather than a bare loc.evaluate() — this call sits on
  // the hot path of every gated action, so a renderer stuck behind a native dialog (or any
  // other reason the page's JS thread stops responding) must surface as "not stable yet"
  // within GATE_BUDGET_MS, not hang the gate — and by extension the whole run — indefinitely.
  try {
    const stable = await evalOn(loc, pageScripts.rafStable, undefined, budget);
    if (!stable || stable === EVAL_TIMED_OUT) {
      await loc.waitFor({ state: "visible", timeout: budget }); // settle once more
    }
  } catch (_) {
    // evaluate may fail on detach — let the action path surface the real error.
  }

  // Enabled: reject disabled / aria-disabled controls.
  try {
    const disabled = await evalOn(loc, pageScripts.isDisabled, undefined, budget);
    if (disabled === EVAL_TIMED_OUT) return; // couldn't tell — don't block the action on it
    if (disabled) throw new Error("Element is disabled");
  } catch (err) {
    const msg = String((err && err.message) || "");
    // A detach here means the element vanished between the RAF-stability check and
    // this one — the caller must see that, not proceed to act on a stale locator.
    if (err && (/disabled/i.test(msg) || STALE_RE.test(msg.toLowerCase()))) throw err;
  }
}

// The override gate deliberately reuses resolver.js's exact default margin/threshold
// values (now imported, not locally copied — a silent drift between the two would
// loosen the "never pick candidate[0]" invariant on one edge but not the other).
// The semantics differ (single ad-hoc selector vs resolve()'s signal walk), only
// the numbers are shared.
const OVERRIDE_UNIQUE_MARGIN = DEFAULT_UNIQUE_MARGIN;
const OVERRIDE_CONFIDENCE_THRESHOLD = DEFAULT_CONFIDENCE_THRESHOLD;

// Validates an agent-supplied recovery selector (`step._explicit_selector` + `_agent_override`)
// against the step's recorded fingerprint before it is allowed to act. Extends the "resolver
// never blindly picks candidate[0]" invariant (resolver.js) to the Tier 3/4 closing edge —
// without this, a multi-match override selector silently acted on whatever `.first()` returned.
async function validateOverrideSelector(page, step, inputs) {
  const selector = interpolate(step._explicit_selector || "", inputs);
  if (!selector) return { valid: false, reason: "missing-selector", candidates: [] };

  const roots = await rootCandidates(page, step, inputs);
  if (isFrameNotFound(step, roots)) {
    // Distinct from "no-match": the agent's selector was never even tried, because the
    // containing frame itself couldn't be located — telling the agent "no element matched"
    // here would be misleading (it would keep proposing element selectors forever, when the
    // real problem is the frame is gone/changed identity).
    return { valid: false, reason: "frame-not-found", candidates: [] };
  }

  const descriptors = [];
  for (const root of roots) {
    let all;
    try { all = await root.locator(selector).all(); } catch (_) { continue; }
    for (const item of all) {
      let d;
      try { d = await evalOn(item, _extractDescriptor); } catch (_) { continue; }
      if (!d || d === EVAL_TIMED_OUT) continue;
      d._loc = item;
      descriptors.push(d);
    }
  }

  if (!descriptors.length) return { valid: false, reason: "no-match", candidates: [] };
  if (descriptors.length === 1) return { valid: true, loc: descriptors[0]._loc };

  const fp = bundleFingerprint(asObject(step.identity_bundle), interpolate, inputs);
  const scored = descriptors
    .map(d => ({ d, s: scoreCandidate(d, fp) }))
    .sort((a, b) => b.s - a.s);
  const margin = scored[0].s - (scored[1] ? scored[1].s : 0);
  if (margin >= OVERRIDE_UNIQUE_MARGIN && scored[0].s >= OVERRIDE_CONFIDENCE_THRESHOLD) {
    return { valid: true, loc: scored[0].d._loc };
  }
  return {
    valid: false,
    reason: "ambiguous",
    candidates: descriptors.slice(0, 20).map(d => ({ role: d.role, name: d.name, text: d.text, testid: d.testid })),
  };
}

const FRAME_INVENTORY_CAP = 50;
const FRAME_INVENTORY_PER_ROOT_CAP = 25;

// Frame-scoped counterpart of pageScripts.domInventory() — for a step whose target lives inside
// an iframe, document.querySelectorAll (what domInventory runs) cannot see into it at all, so
// the Tier 3+ agent's "ground truth" inventory would silently omit everything in that frame.
// Gathers the same kind of interactive-element summary, scoped to the step's own resolved frame
// chain, using the same Locator-based pattern resolve_adapter.js already uses for candidate
// gathering (root.locator(...).all() + per-item .evaluate()) — FrameLocator has no direct
// raw-Frame conversion, so this is the correct mechanism, not a workaround.
// Returns null when the step has no frame_chain (nothing extra to gather) or the frame couldn't
// be located at all (a distinct "frame not found" condition surfaced separately — see
// isFrameNotFound/frameNotFound — not silently reported as an empty inventory).
async function frameScopedInventory(page, step, inputs) {
  const frameChain = asArray(asObject(step && step.identity_bundle).frame_chain);
  if (!frameChain.length) return null;

  const roots = await rootCandidates(page, step, inputs);
  if (!roots.length) return null;

  const seen = new Set();
  const out = [];
  for (const root of roots) {
    if (out.length >= FRAME_INVENTORY_CAP) break;
    let items;
    try { items = await root.locator(pageScripts.INVENTORY_SELECTOR).all(); } catch (_) { continue; }
    for (const item of items.slice(0, FRAME_INVENTORY_PER_ROOT_CAP)) {
      if (out.length >= FRAME_INVENTORY_CAP) break;
      let entry;
      try { entry = await evalOn(item, pageScripts.inventoryEntryForElement); } catch (_) { continue; }
      if (!entry || entry === EVAL_TIMED_OUT) continue;
      const key = `${entry.tag}|${entry.type || ""}|${entry.text || ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(entry);
    }
  }
  return out;
}

// Capture the interactive-element inventory at the exact moment of step failure, before the
// T1/T2 recovery cascade runs (~12 s). Transient elements like open dropdown menus auto-close
// during the cascade, leaving _buildFailureResponse with an empty DOM scan. Storing the snapshot
// on the error object lets _buildFailureResponse prefer it over a stale post-cascade query.
// Merges in the frame-scoped inventory (tagged in_frame: true) when the step targets an iframe.
async function captureEarlyDomSnapshot(page, step, inputs) {
  let top;
  try {
    top = await evalOn(page, pageScripts.domInventory);
  } catch (_) {
    return null;
  }
  if (top === EVAL_TIMED_OUT || !Array.isArray(top)) return null;
  let frameEntries = null;
  try { frameEntries = await frameScopedInventory(page, step, inputs); } catch (_) { frameEntries = null; }
  if (Array.isArray(frameEntries) && frameEntries.length) {
    return [...top, ...frameEntries.map(e => ({ ...e, in_frame: true }))];
  }
  return top;
}

module.exports = {
  PRIMARY,
  unique,
  asObject,
  asArray,
  rootCandidates,
  isFrameNotFound,
  entityRoots,
  isEntityNotFound,
  enumerateRows,
  splitListInput,
  maybeScrollForVirtualization,
  locatorCandidates,
  resolveStep,
  gateLocator,
  isFileInputStep,
  validateOverrideSelector,
  frameScopedInventory,
  captureEarlyDomSnapshot,
};
