"use strict";

// Functions Playwright serializes via page.evaluate()/locator.evaluate() and runs inside the
// browser page realm (Function.prototype.toString(), re-parsed in-page — NOT in this Node
// process). Each export below must stay fully self-contained: no closures over module-scope
// vars, no calls into other app modules. That constraint is also why this file is obfuscated
// with --string-array/--self-defending OFF in the build scripts (build-runtime-app.yml,
// build-app-local.ps1) while the rest of the app layer keeps full obfuscation: those two
// transforms rewrite a function body to reference a module-scope decoder/guard var, which does
// not exist once the source is re-parsed in the browser — see the scroll ReferenceError this
// file was extracted to fix.

// ── Page-side fingerprint extractor ─────────────────────────────────────────
// Returns the comparable attributes for one candidate element plus the stable-hash payload
// string. The payload mirrors compiler/stable_hash.py so the Node-side SHA256 (resolve_adapter.js)
// matches the compiled stable_hash when the recorded attributes match.
function extractDescriptor(el) {
  const DYNAMIC_TOKENS = new Set([
    "focus", "hover", "active", "focus-visible", "focus-within",
    "loading", "animating", "transitioning", "selected", "disabled",
    "expanded", "collapsed", "open", "closed", "checked", "pressed",
    "dragging", "dragged", "dropping",
  ]);
  const DYNAMIC_PREFIXES = ["is-", "has-", "js-", "animate-", "transition-", "state-"];
  const SKIP = new Set([
    "class", "style", "tabindex",
    "aria-expanded", "aria-selected", "aria-checked", "aria-disabled",
    "aria-pressed", "aria-current", "aria-busy",
    "data-state", "data-active", "data-focus", "data-open",
  ]);

  const tag = (el.tagName || "").toLowerCase();
  const attrsObj = {};
  for (const a of Array.from(el.attributes || [])) {
    const k = a.name.toLowerCase();
    if (SKIP.has(k)) continue;
    if (k === "class") {
      const stable = String(a.value || "").split(/\s+/).filter(c => {
        const lc = c.toLowerCase();
        if (!c || DYNAMIC_TOKENS.has(lc)) return false;
        return !DYNAMIC_PREFIXES.some(p => lc.startsWith(p));
      });
      if (stable.length) attrsObj["class"] = stable.sort().join(" ");
    } else {
      attrsObj[k] = String(a.value || "");
    }
  }
  const sortedAttrs = Object.keys(attrsObj).sort().map(k => `${k}=${attrsObj[k]}`).join("&");

  const ariaLabel = el.getAttribute("aria-label") || "";
  const nameAttr = el.getAttribute("name") || "";
  // The hash input is the recorder's, field for field (compiler/stable_hash.py over bridge.js's
  // target): tag, only `role` + stable `data-*` attributes (never id/name/type/href/class), and the
  // name as aria-label || name || visible text. Anything else here and a recorded hash can never match.
  const shownText = (typeof el.innerText === "string" ? el.innerText : (el.textContent || "")).replace(/\s+/g, " ").trim();
  const hashAttrs = [];
  for (const a of Array.from(el.attributes || [])) {
    const k = a.name.toLowerCase();
    if (SKIP.has(k) || k === "data-testid" || k === "data-test-id" || k === "data-test" || k === "data-cy") continue;
    if (k !== "role" && k.indexOf("data-") !== 0) continue;
    const v = String(a.value || "").trim();
    const longMixed = v.length >= 16 && !/\s/.test(v) && /[A-Za-z]/.test(v) && /\d/.test(v) && /[A-Z]/.test(v) && /[a-z]/.test(v) && /^[A-Za-z0-9_\-]+$/.test(v);
    if (!v || v.length > 64 || /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v) || /^[0-9a-f]{16,}$/i.test(v) || longMixed) continue;
    hashAttrs.push([k, v]);
  }
  const PREF = ["role", "data-key", "data-command", "data-action", "data-value"];
  hashAttrs.sort((x, y) => {
    const rx = PREF.indexOf(x[0]) === -1 ? 99 : PREF.indexOf(x[0]);
    const ry = PREF.indexOf(y[0]) === -1 ? 99 : PREF.indexOf(y[0]);
    return rx !== ry ? rx - ry : (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0);
  });
  const hashAttrStr = hashAttrs.slice(0, 12).sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0)).map(([k, v]) => `${k}=${v}`).join("&");
  const typeAttr = (el.getAttribute("type") || "").toLowerCase();
  // A button drawn as <input type=submit value="Transfer"> is named by its value (bridge.js value_label).
  const valueLabel = tag === "input" && (typeAttr === "submit" || typeAttr === "button" || typeAttr === "reset")
    ? (el.getAttribute("value") || "") : "";
  const axName = (ariaLabel || nameAttr || valueLabel || shownText.slice(0, 80)).trim();
  const hashPayload = `${tag}|${hashAttrStr}|${axName}`;

  // Implicit-role best effort. This must cover every tag the compiler can record a role for,
  // not just the interactive ones: resolver.js's roleAgrees() returns false whenever EITHER
  // side is empty, so a tag missing here scores 0 against a fingerprint that names its role —
  // which silently vetoes an otherwise correctly-matched candidate. A bare <img> hit exactly
  // that: the compiled fingerprint said role "img", this said "", and the one structural
  // signal that DID find the element was rejected below the confidence threshold.
  let role = el.getAttribute("role") || "";
  if (!role) {
    if (tag === "a" && el.hasAttribute("href")) role = "link";
    else if (tag === "button") role = "button";
    else if (tag === "input") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      role = ({ checkbox: "checkbox", radio: "radio", button: "button", submit: "button" })[t] || "textbox";
    }
    else if (tag === "img") role = "img";
    else if (tag === "textarea") role = "textbox";
    else if (tag === "select") role = el.hasAttribute("multiple") ? "listbox" : "combobox";
    else if (/^h[1-6]$/.test(tag)) role = "heading";
    else if (tag === "nav") role = "navigation";
    else if (tag === "main") role = "main";
  }

  const neighbors = [];
  const pushText = (n) => {
    const t = (n && n.textContent || "").trim();
    if (t && t.length < 60) neighbors.push(t);
  };
  pushText(el.parentElement);
  pushText(el.previousElementSibling);
  pushText(el.nextElementSibling);

  // `name` feeds resolver.js's fpName match — placeholder is included so a live candidate
  // for a label-less input (e.g. a search box) can still match a fingerprint the compiler
  // named from its placeholder (identity_bundle.py's aria_label||name||inner_text||placeholder).
  // `_hashPayload`/axName above deliberately excludes it — that mirrors stable_hash.py's
  // narrower ax_name (aria_label||name||inner_text) and must not drift from it.
  // alt/title mirror identity_bundle.py's _accessible_name — an <img>'s only real name is
  // its alt, so without them a live image candidate could never match a fingerprint named
  // from one.
  // The title of the section the element sits in: the last heading before it, its outermost shadow host standing in
  // for it. The same rule as the recorder (bridge.js sectionHeading); tells identical twins in different sections apart.
  let headingOf = el;
  for (let hops = 0; headingOf && hops < 10; hops++) {
    const rootNode = typeof headingOf.getRootNode === "function" ? headingOf.getRootNode() : null;
    if (!rootNode || !rootNode.host) break;
    headingOf = rootNode.host;
  }
  let sectionHeading = "";
  if (headingOf && typeof headingOf.compareDocumentPosition === "function" && typeof document.querySelectorAll === "function") {
    const heads = document.querySelectorAll("h1,h2,h3,h4,h5,h6,[role=heading]");
    for (let i = 0; i < heads.length && i < 1000; i++) {
      const h = heads[i];
      if (h === headingOf || h.contains(headingOf)) continue;
      if (h.compareDocumentPosition(headingOf) & Node.DOCUMENT_POSITION_FOLLOWING) sectionHeading = h.textContent || "";
      else break;
    }
  }
  sectionHeading = String(sectionHeading).replace(/\s+/g, " ").trim().slice(0, 80);

  const placeholder = el.getAttribute("placeholder") || "";
  const altAttr = el.getAttribute("alt") || "";
  const titleAttr = el.getAttribute("title") || "";
  // The caption tied to a form control, by the recorder's first two rules (label[for] / enclosing label,
  // then aria-labelledby). Empty for anything else.
  let labelText = "";
  try {
    const own = el.labels && el.labels.length ? Array.from(el.labels).map(l => l.textContent || "").join(" ") : "";
    let by = "";
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    if (!own && ids.length) by = ids.map(i => { const n = document.getElementById(i); return n ? (n.textContent || "") : ""; }).join(" ");
    labelText = (own || by).replace(/\s+/g, " ").trim().slice(0, 80);
  } catch (_) {}
  return {
    role,
    // Same order as the recorder's accessible name (identity_bundle.py::_accessible_name); the form-field
    // `name` attribute comes last, only for an element with nothing else to be called, and is also compared
    // as its own field (nameAttr).
    name: (ariaLabel || altAttr || titleAttr || placeholder || valueLabel || shownText || labelText || nameAttr).slice(0, 120),
    nameAttr,
    elId: el.getAttribute("id") || "",
    inputType: tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : "",
    label: labelText,
    text: shownText.slice(0, 120),
    testid: el.getAttribute("data-testid") || el.getAttribute("data-test-id") || "",
    anchorNeighbors: neighbors,
    sectionHeading,
    _hashPayload: hashPayload,
  };
}

// RAF-stable check: bounding box must be unchanged across two animation frames.
function rafStable(el) {
  return new Promise(resolve => {
    const r1 = el.getBoundingClientRect();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const r2 = el.getBoundingClientRect();
      resolve(Math.abs(r1.x - r2.x) < 1 && Math.abs(r1.y - r2.y) < 1
        && Math.abs(r1.width - r2.width) < 1 && Math.abs(r1.height - r2.height) < 1);
    }));
  });
}

function isDisabled(el) {
  return el.disabled === true || el.getAttribute("aria-disabled") === "true";
}

function scrollBy([x, y]) {
  window.scrollBy(x, y);
}

// BUILD-30: advance a virtualized container's scroll position by roughly one screenful, or
// reset to the top once the bottom is reached (so a recorded row above the current position is
// still reachable on a later pass). Called via Locator.evaluate(), so `el` is the container
// element Playwright already resolved — self-contained, no closure over module scope (see the
// file-header note: these functions are re-serialized and run standalone in the page realm).
function scrollVirtualContainerStep(el) {
  if (!el) return { advanced: false };
  const before = el.scrollTop;
  const step = Math.max(el.clientHeight || 300, 100);
  const atBottom = before + el.clientHeight >= el.scrollHeight - 2;
  el.scrollTop = atBottom ? 0 : before + step;
  return { advanced: el.scrollTop !== before };
}

// BUILD-30: no compiled virtualized_container hint and no entity binding to key a selector
// off — find the page's (or frame's) own dominant scrollable element (largest
// scrollHeight - clientHeight among auto/scroll-overflow elements) and scroll it the same way.
// For a virtualized listbox or long dropdown with nothing else to go on. Runs via Page/Frame
// .evaluate() (no element argument), so it reads `document` directly.
function scrollDominantScrollableElement() {
  let best = null;
  let bestRange = 0;
  const all = document.querySelectorAll("*");
  for (const el of all) {
    const range = el.scrollHeight - el.clientHeight;
    if (range <= 40 || range <= bestRange) continue;
    const style = getComputedStyle(el);
    if (style.overflowY !== "auto" && style.overflowY !== "scroll") continue;
    best = el;
    bestRange = range;
  }
  if (!best) return { advanced: false };
  const before = best.scrollTop;
  const step = Math.max(best.clientHeight || 300, 100);
  const atBottom = before + best.clientHeight >= best.scrollHeight - 2;
  best.scrollTop = atBottom ? 0 : before + step;
  return { advanced: best.scrollTop !== before };
}

function getScrollY() {
  return window.scrollY;
}

// "Did the click reach the element?" asked of the page itself, not of Playwright. A click can be dispatched and still
// land on something else (a banner in front, a transparent layer) or on nothing, and a step that clicked nothing
// must not be reported as done. armClickWitness listens, on the window and before the page's own handlers, for the
// next trusted click; readClickWitness reports where it went. The page's reply is always one of:
//   received  the click reached the element (or something inside / around it)
//   elsewhere the click went to another element, which is described
//   none      no click arrived anywhere
//   unknown   the page cannot say (the element is gone, the page was replaced): never a failure
function armClickWitness(el) {
  try { if (window.__conxaClick && window.__conxaClick.off) window.__conxaClick.off(); } catch (_) { /* stale */ }
  const w = { el, seen: [] };
  // The whole path, not ev.target: at the window the target of a click inside a web component is retargeted to the
  // component's host, which would make a button inside it look like "something else".
  const onClick = (ev) => { if (ev.isTrusted) w.seen.push(typeof ev.composedPath === "function" && ev.composedPath().length ? ev.composedPath() : [ev.target]); };
  window.addEventListener("click", onClick, true);
  w.off = () => window.removeEventListener("click", onClick, true);
  window.__conxaClick = w;
  return true;
}

function readClickWitness() {
  const w = window.__conxaClick;
  if (!w) return { state: "unknown" };
  window.__conxaClick = null;
  try { w.off(); } catch (_) { /* gone */ }
  const el = w.el;
  if (!el || !el.isConnected) return { state: "unknown" };
  if (!w.seen.length) return { state: "none" };
  const hit = w.seen.some((path) => path.some((n) => n === el) || path.some((n) => el.contains && n && n.nodeType === 1 && el.contains(n)));
  if (hit) return { state: "received" };
  const t = w.seen[0][0];
  const words = ((t && (t.getAttribute && (t.getAttribute("aria-label") || t.getAttribute("id"))) || t && t.innerText || "") + "").trim().slice(0, 40);
  return { state: "elsewhere", where: ((t && t.tagName) || "element").toLowerCase() + (words ? ` "${words}"` : "") };
}

// Where to put the pointer to reveal a hidden element: the centres of its nearest visible ancestors, innermost first
// (at most three, kept inside the window). A menu item that only shows while its menu is hovered has a visible ancestor
// (the menu), and hovering it is what a person does. Empty when the element has no visible ancestor.
function revealHoverPoints(el) {
  const points = [];
  const vw = window.innerWidth, vh = window.innerHeight;
  let node = el.parentElement;
  while (node && points.length < 3) {
    const r = node.getBoundingClientRect();
    const cs = window.getComputedStyle(node);
    if (r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none") {
      const x = Math.min(Math.max(r.left + r.width / 2, 1), vw - 1);
      const y = Math.min(Math.max(r.top + r.height / 2, 1), vh - 1);
      if (!points.some((p) => Math.abs(p.x - x) < 2 && Math.abs(p.y - y) < 2)) points.push({ x, y });
    }
    node = node.parentElement;
  }
  return points;
}

// What a field currently holds, as a person would read it. A text box, text area or list holds a `value`; a
// rich-text box (Gmail's message body, any editor built on contenteditable) holds text and has no value at all, so
// asking it for one fails even when the text really is there. A list answers with what it shows (the chosen
// option's label, "Name (Z to A)") and its value ("za") together: a step may have recorded either, and callers
// match by "contains". Anything else is not a field: null.
function readFieldValue(el) {
  const tag = (el.tagName || "").toUpperCase();
  if (tag === "SELECT") {
    return Array.from(el.selectedOptions || []).map((o) => o.label + "\n" + o.value).join("\n");
  }
  if (tag === "INPUT" || tag === "TEXTAREA") return el.value;
  if (el.isContentEditable) return el.innerText;
  return null;
}

// Cheap, deterministic snapshot of page shape used only to answer "did anything happen" for the
// state_changed assertion. No LLM, no DOM diffing — three counters compared before vs. after.
function preStepSignature(sel) {
  return {
    textLen: (document.body && document.body.innerText || "").length,
    interactiveCount: document.querySelectorAll(sel).length,
  };
}

// EXEC-37: settle-detection signature, extending preStepSignature with a total node count (a
// coarser churn signal than interactiveCount alone) and a busy-indicator count. Self-contained —
// no reference to any outer module scope — because this runs serialized into the page realm
// (see this file's "in-page" obfuscation-profile note at the top: no module-scope decoder var
// survives re-parsing there). Zero-arg by design so settle.js never needs to thread a selector
// arg through evalOn.
function settleSignature() {
  var interactiveSel = 'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="menuitem"], [role="option"]';
  var busySel = '[aria-busy="true"], [role="progressbar"], .spinner, .loading, .loader, [class*="spinner" i]';
  function isVisible(el) {
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    var cs = window.getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }
  var busyCount = 0;
  var busyEls = document.querySelectorAll(busySel);
  for (var i = 0; i < busyEls.length; i++) {
    if (isVisible(busyEls[i])) busyCount++;
  }
  return {
    textLen: (document.body && document.body.innerText || "").length,
    interactiveCount: document.querySelectorAll(interactiveSel).length,
    nodeCount: document.querySelectorAll("*").length,
    busyCount: busyCount,
  };
}

// EXEC-36: replay-side counterpart to recorder/session.py::_write_environment_sync's capture
// script — same field shape, evaluated against the LIVE page instead of the recording page, so
// env_match.js can compare the two. Self-contained for the same "in-page" reason as
// settleSignature above.
function environmentSignature() {
  var d = new Intl.DateTimeFormat().resolvedOptions();
  return {
    locale: navigator.language || "",
    timezone: d.timeZone || "",
    utc_offset_minutes: -new Date().getTimezoneOffset(),
    viewport: { w: window.innerWidth, h: window.innerHeight },
    device_pixel_ratio: window.devicePixelRatio || 1,
    date_format_sample: new Date(2026, 0, 31).toLocaleDateString(),
    platform: navigator.platform || "",
  };
}

// Page fingerprint used by server.js for change detection.
function pageFingerprint() {
  return {
    interactiveCount: document.querySelectorAll(
      'button, a[href], input, select, textarea, [role="button"], [role="link"]'
    ).length,
    text: (document.body && document.body.innerText || "").slice(0, 5000),
  };
}

// Selector shared by domInventory() (top-level document) and inventoryEntryForElement() (a
// single element already matched inside a frame — see run.js's frame-scoped inventory helper).
// Duplicated as a literal string in both call sites below rather than a shared const: these
// functions are serialized via Function.prototype.toString() and re-parsed standalone in the
// browser page realm (see file header) — they cannot close over module-scope values.
const INVENTORY_SELECTOR =
  'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="menuitem"], [role="option"]';

// Interactive-element inventory — shared by run.js's early-failure snapshot and server.js's
// post-cascade "current inventory" for Tier 3+ recovery context. Top-level document only; for a
// step whose target lives inside an iframe, run.js additionally gathers a frame-scoped inventory
// via inventoryEntryForElement() below (document.querySelectorAll cannot see into iframes).
function domInventory() {
  const seen = new Set();
  return Array.from(document.querySelectorAll(
    'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="menuitem"], [role="option"]'
  )).map(el => {
    const text = (el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("placeholder") || "").trim().slice(0, 80);
    const tag  = el.tagName.toLowerCase();
    const type = el.getAttribute("type")        || "";
    const role = el.getAttribute("role")        || "";
    const id   = el.id                          || undefined;
    const dt   = el.getAttribute("data-testid") || el.getAttribute("data-test") || undefined;
    const key  = `${tag}|${type}|${text}`;
    if (!text && !type && !id && !dt) return null;
    if (seen.has(key)) return null;
    seen.add(key);
    return { tag, type: type || undefined, role: role || undefined, text: text || undefined, id, "data-testid": dt };
  }).filter(Boolean).slice(0, 50);
}

// Per-element counterpart of domInventory(), for elements already matched inside a specific
// frame (run.js resolves the frame via rootCandidates()/frameLocator, then calls this via
// locator.evaluate() on each matched element — the same per-item pattern resolve_adapter.js
// already uses for candidate gathering). Field logic must stay identical to domInventory()'s
// per-element body above; returns null (skip) for elements with no identifying signal, same as
// domInventory() — dedup across elements is the caller's job since this only sees one at a time.
function inventoryEntryForElement(el) {
  const text = (el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("placeholder") || "").trim().slice(0, 80);
  const tag  = el.tagName.toLowerCase();
  const type = el.getAttribute("type")        || "";
  const role = el.getAttribute("role")        || "";
  const id   = el.id                          || undefined;
  const dt   = el.getAttribute("data-testid") || el.getAttribute("data-test") || undefined;
  if (!text && !type && !id && !dt) return null;
  return { tag, type: type || undefined, role: role || undefined, text: text || undefined, id, "data-testid": dt };
}

// Outcome forks — what dialog (if any) is open right now. Queries the dialog elements directly
// instead of hit-testing like overlayProbe: a fork asks "is a decision dialog showing?" and a
// full-screen backdrop can sit on top of the dialog itself under elementFromPoint. Like every
// function in this file it is re-parsed standalone in the page realm, so it closes over nothing.
// Returns null when no visible dialog is open, else the topmost modal (or, with none, the last
// dialog in DOM order) as { title, text, controls: [{ tag, role, name, text, testid, id }],
// hasFields, modal, live, progress }.
function dialogProbe() {
  const SEL = 'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]';
  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim();
  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = window.getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }
  const dialogs = Array.from(document.querySelectorAll(SEL)).filter(visible);
  if (!dialogs.length) return null;
  // A modal blocks the page; a non-modal panel (an upload tray, a toast built as a dialog) does not.
  // Prefer the topmost modal so a late-appended tray can't shadow the real question.
  const isModal = (el) => {
    if (el.getAttribute("aria-modal") === "true") return true;
    try { return el.tagName === "DIALOG" && el.matches(":modal"); } catch (_) { return false; }
  };
  const modals = dialogs.filter(isModal);
  const dlg = (modals.length ? modals : dialogs).slice(-1)[0];
  let title = "";
  const labelledBy = dlg.getAttribute("aria-labelledby");
  if (labelledBy) {
    const t = document.getElementById(labelledBy.split(/\s+/)[0]);
    if (t) title = clean(t.textContent);
  }
  if (!title) title = clean(dlg.getAttribute("aria-label"));
  if (!title) {
    const h = dlg.querySelector("h1,h2,h3,h4,[role=heading]");
    if (h) title = clean(h.textContent);
  }
  const controls = Array.from(dlg.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]'))
    .filter(visible)
    .slice(0, 8)
    .map((el) => {
      const text = clean(el.innerText || el.value);
      return {
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute("role") || "button",
        name: clean(el.getAttribute("aria-label")) || text,
        text: text.slice(0, 80),
        testid: el.getAttribute("data-testid") || "",
        id: el.id || "",
      };
    });
  const hasFields = !!dlg.querySelector(
    'input:not([type="button"]):not([type="submit"]):not([type="hidden"]):not([type="checkbox"]):not([type="radio"]),textarea,select,[contenteditable="true"]'
  );
  // Facts about what kind of surface this is. They never decide anything on their own; they let a
  // failure say "a progress panel was on screen" instead of "a popup", and keep the picture honest.
  const modal = isModal(dlg);
  // Does it stop the person (or the run) from reaching the rest of the page? Measured, never read from words:
  // true = a modal, a covering backdrop, or an inert page; false = every point outside it reaches ordinary page
  // content; null = could not tell (a frame, an unreadable page). Callers treat null like true.
  // `why` names the branch that decided, so a verdict can say which fact it rested on.
  const measured = (() => {
    if (modal) return { v: true, why: "modal" };
    try {
      if (document.querySelector("[inert]") && !dlg.closest("[inert]")) return { v: true, why: "inert_page" };
      const vw = window.innerWidth, vh = window.innerHeight;
      const r = dlg.getBoundingClientRect();
      const pts = [[8, 8], [vw - 8, 8], [8, vh - 8], [vw - 8, vh - 8], [vw / 2, 8], [vw / 2, vh - 8], [8, vh / 2], [vw - 8, vh / 2]]
        .filter(([x, y]) => x < r.left - 4 || x > r.right + 4 || y < r.top - 4 || y > r.bottom + 4);
      if (!pts.length) return { v: true, why: "covers_window" };
      let unknown = false;
      for (const [x, y] of pts) {
        const el = document.elementFromPoint(x, y);
        if (!el) { unknown = true; continue; }
        if (el === document.documentElement || el === document.body || dlg.contains(el)) continue;
        if (el.tagName === "IFRAME" || el.tagName === "FRAME") { unknown = true; continue; }
        const er = el.getBoundingClientRect();
        const coversWindow = er.width * er.height >= 0.9 * vw * vh && window.getComputedStyle(el).position === "fixed";
        if (!coversWindow) continue;
        // A fixed window-sized element over a point outside the dialog is a backdrop only when it holds little besides
        // the dialog (and, if the dialog is not inside it, little at all). One that holds far more text than that is the
        // page's own app shell, whether or not the dialog happens to be mounted inside it.
        const room = el.contains(dlg) ? (dlg.innerText || "").length : 0;
        if ((el.innerText || "").length <= room + 40) return { v: true, why: "backdrop" };
      }
      return unknown ? { v: null, why: "unreadable" } : { v: false, why: "page_reachable" };
    } catch (_) { return { v: null, why: "unreadable" }; }
  })();
  const blocks = measured.v;
  const blocks_reason = measured.why;
  const live =!!dlg.closest('[aria-live]:not([aria-live="off"]),[role="status"],[role="alert"],[role="log"]');
  const progress = !!dlg.querySelector('[role="progressbar"],progress,[aria-busy="true"]');
  return { title: title.slice(0, 120), text: clean(dlg.innerText).slice(0, 300), controls, hasFields, modal, blocks, blocks_reason, live, progress };
}

// A spot where a resting pointer cannot open anything: a point that reaches the page itself (or inert text), outside any
// dialog and not on or inside anything a pointer could act on. Used to take the pointer off the page before the run stops
// on a popup (run.js::parkAndRecheck). Returns {x, y}, or null when no such spot is found (then the pointer stays put).
// Re-parsed standalone in the page realm like every function here, so it closes over nothing.
function neutralPoint() {
  const vw = window.innerWidth, vh = window.innerHeight;
  const ACTIVE = 'a,button,input,select,textarea,summary,label,[role],[tabindex],[onclick],[onmouseover],[onmouseenter],[data-tooltip],[title],[aria-haspopup]';
  const spots = [[2, 2], [vw - 3, 2], [2, vh - 3], [vw - 3, vh - 3], [vw / 2, 2], [vw / 2, vh - 3], [2, vh / 2], [vw - 3, vh / 2], [vw / 2, vh / 2]];
  for (const [x, y] of spots) {
    const el = document.elementFromPoint(x, y);
    if (!el) continue;
    if (el === document.documentElement || el === document.body) return { x: Math.round(x), y: Math.round(y) };
    if (el.tagName === "IFRAME" || el.tagName === "FRAME") continue;
    if (el.closest('[role="dialog"],[role="alertdialog"],dialog,[aria-modal="true"]') || el.closest(ACTIVE)) continue;
    return { x: Math.round(x), y: Math.round(y) };
  }
  return null;
}

// EXEC-49 — fault probe. What the app itself is saying is wrong right now: visible role=alert /
// aria-live=assertive regions, the message of a field marked aria-invalid, and whether the browser
// is offline. Deliberately NOT loose page text: a plain "Required" label is not evidence of a fault.
// Re-parsed standalone in the page realm like every function here, so it closes over nothing.
// Returns { offline, alerts: string[], invalid: string[] } (at most 4 of each, 200 chars each).
function faultProbe() {
  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim().slice(0, 200);
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none";
  };
  const alerts = [];
  for (const el of document.querySelectorAll('[role="alert"], [aria-live="assertive"]')) {
    if (alerts.length >= 4) break;
    if (!visible(el)) continue;
    const t = clean(el.innerText || el.textContent);
    if (t) alerts.push(t);
  }
  const invalid = [];
  for (const el of document.querySelectorAll('[aria-invalid="true"]')) {
    if (invalid.length >= 4) break;
    if (!visible(el)) continue;
    const ids = `${el.getAttribute("aria-errormessage") || ""} ${el.getAttribute("aria-describedby") || ""}`.split(/\s+/).filter(Boolean);
    const t = clean(ids.map((id) => document.getElementById(id)).filter(Boolean).map((n) => n.innerText || n.textContent).join(" "));
    if (t) invalid.push(t);
  }
  return { offline: navigator.onLine === false, alerts, invalid };
}

// EXEC-30 — live overlay probe. domInventory() truncates at 50 entries IN DOM ORDER, so a modal
// appended late to <body> can be cut out entirely even though it is the only thing blocking the
// step. This answers "is something sitting on top of the page right now" directly, independent of
// inventory order, and also catches the case where an overlay HIDES the target rather than
// intercepting a click (which never throws INTERCEPTED at all).
// ponytail: three hit-test points (center, top-center, bottom-center), not a full z-order sweep —
// upgrade to a denser grid if a real overlay is ever missed by all three.
function overlayProbe() {
  const DIALOG_SEL = 'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"], .modal';
  const vw = window.innerWidth, vh = window.innerHeight;
  const points = [[vw / 2, vh / 2], [vw / 2, vh * 0.15], [vw / 2, vh * 0.85]];

  function isOverlayish(el) {
    if (!el || el === document.body || el === document.documentElement) return false;
    if (el.matches && el.matches(DIALOG_SEL)) return true;
    const cs = window.getComputedStyle(el);
    return cs.position === "fixed" || cs.position === "sticky";
  }

  function outermostOverlay(startEl) {
    let found = null;
    let el = startEl;
    while (el && el !== document.body && el !== document.documentElement) {
      if (isOverlayish(el)) found = el; // keep walking — want the OUTERMOST match
      el = el.parentElement;
    }
    return found;
  }

  let container = null;
  for (const [x, y] of points) {
    const hit = document.elementFromPoint(x, y);
    const candidate = outermostOverlay(hit);
    if (candidate) { container = candidate; break; }
  }
  if (!container) return null;

  const rect = container.getBoundingClientRect();
  const coverage = (Math.max(0, rect.width) * Math.max(0, rect.height)) / (vw * vh);
  if (coverage < 0.05) return null; // a slim sticky header is not "the story"

  // BUILD-26 stage (f): a duplicate of extractDescriptor's field shape, not a call to it —
  // page.evaluate() re-parses overlayProbe's source standalone in the page realm (see this
  // file's header comment), so it cannot close over or call the sibling extractDescriptor
  // function above. Same six fields resolver.js::scoreCandidate reads (role/name/text/testid/
  // anchorNeighbors/_hashPayload), without extractDescriptor's dynamic-class-token filtering —
  // unnecessary here since only role/name/text/testid ever become a selector, filtered again
  // server-side by selector_filters.py::selector_passes_filters before anything is proposed.
  function controlDescriptor(el) {
    const tag = el.tagName.toLowerCase();
    const ariaLabel = el.getAttribute("aria-label") || "";
    const text = (el.innerText || el.value || el.getAttribute("placeholder") || "").trim().slice(0, 80);
    const name = (ariaLabel || text || el.getAttribute("placeholder") || "").slice(0, 120);
    const testid = el.getAttribute("data-testid") || el.getAttribute("data-test") || el.getAttribute("data-test-id") || "";
    const role = el.getAttribute("role") || (tag === "button" ? "button" : tag === "a" && el.hasAttribute("href") ? "link" : "");
    if (!name && !text && !testid && !el.id) return null;
    const neighbors = [];
    const pushText = (n) => {
      const t = (n && n.textContent || "").trim();
      if (t && t.length < 60) neighbors.push(t);
    };
    pushText(el.parentElement);
    return {
      tag, role: role || undefined, name, text: text || undefined,
      testid: testid || undefined, id: el.id || undefined,
      anchorNeighbors: neighbors,
      _hashPayload: `${tag}|id=${el.id || ""}|${name}`,
    };
  }

  const controls = Array.from(container.querySelectorAll(
    'button, a[href], input, [role="button"], [role="link"]'
  )).slice(0, 15).map(controlDescriptor).filter(Boolean);

  // Container selector ladder (id > role > bounded css path) — the same priority order
  // recorder/bridge.js::buildDialogSignal uses for an observed dialog, duplicated rather than
  // shared for the same standalone-reparse reason as controlDescriptor above.
  function containerSignal(el) {
    if (el.id) return "#" + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id.replace(/[^a-zA-Z0-9_-]/g, c => "\\" + c));
    const role = el.getAttribute("role") || (el.getAttribute("aria-modal") === "true" ? "dialog" : "");
    if (role) return `[role="${role}"]`;
    const parts = [];
    let cur = el, depth = 0;
    while (cur && cur.nodeType === 1 && depth < 6) {
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(n => n.tagName === cur.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(cur) + 1})`;
      }
      parts.unshift(part);
      cur = parent;
      depth++;
    }
    return parts.join(" > ") || null;
  }

  return {
    container: {
      tag: container.tagName.toLowerCase(),
      id: container.id || undefined,
      class: container.className && typeof container.className === "string" ? container.className.slice(0, 120) : undefined,
      role: container.getAttribute("role") || undefined,
      text: (container.innerText || "").trim().slice(0, 120),
      signal: containerSignal(container),
    },
    controls,
  };
}

// ── Login-completion signals (login_signals.js's page-side counterparts) ────────────────────────
// Self-contained for the same "in-page" reason as settleSignature/environmentSignature above: no
// closure over module scope, evaluated standalone once re-parsed in the page realm.

// Password-box lookout: does any frame currently show a password field? Same-origin iframes only
// — a cross-origin iframe throws on .contentDocument access by construction and is skipped, the
// same limitation every other page_scripts.js DOM probe already accepts for embedded content.
function passwordBoxProbe() {
  function hasPasswordField(root) {
    try { return !!root.querySelector('input[type="password"]'); } catch (_) { return false; }
  }
  if (hasPasswordField(document)) return true;
  var frames = document.querySelectorAll("iframe");
  for (var i = 0; i < frames.length; i++) {
    try {
      if (frames[i].contentDocument && hasPasswordField(frames[i].contentDocument)) return true;
    } catch (_) {}
  }
  return false;
}

// Pause-sign lookout: an OTP/texted-code entry screen has an unambiguous DOM shape — either the
// standard autocomplete="one-time-code" marker, or a run of single-character numeric/text inputs.
// Deliberately does not attempt to recognise "choose an account" or "approve on your phone"
// screens, which have no comparably reliable structural signature without reading text (see
// login_signals.js::looksPaused's ponytail note).
function pauseSignProbe() {
  var hasOneTimeCodeAutocomplete = !!document.querySelector('input[autocomplete="one-time-code"]');
  var otpLike = document.querySelectorAll(
    'input[maxlength="1"][inputmode="numeric"], input[maxlength="1"][type="tel"], input[maxlength="1"][type="text"]'
  );
  return { otpLikeInputCount: otpLike.length, hasOneTimeCodeAutocomplete: hasOneTimeCodeAutocomplete };
}

// Tickets lookout, storage half — cookie names come from Playwright's own context.cookies() in
// Node; this supplies the other half (localStorage/sessionStorage key COUNTS only, never values or
// even key names, per the "secrets stay on the machine" rule).
function storageKeyCounts() {
  function countKeys(storage) {
    try { return storage.length; } catch (_) { return 0; }
  }
  return { localStorageKeys: countKeys(window.localStorage), sessionStorageKeys: countKeys(window.sessionStorage) };
}

// One page's own origin + localStorage, in Playwright storageState's `origins[]` entry shape —
// browser.js::_captureState reads a host-owned (Conxa Execute) context page by page with this.
function originStorage() {
  const localStorage = [];
  try {
    for (let i = 0; i < window.localStorage.length; i++) {
      const name = window.localStorage.key(i);
      localStorage.push({ name, value: window.localStorage.getItem(name) });
    }
  } catch (_) {}
  return { origin: location.origin, localStorage };
}

// AUTH-8 — "Signed in as ___". Unlike every probe above, this one is DELIBERATELY text-reading:
// the whole point is to surface the actual account name as a positive confirmation, best-effort
// and never blocking (see browser.js's call site — a miss is silent, never a warning). Looks for
// a short (2-60 char) label on or near a marker that plausibly identifies an account/profile
// control, never a value with no such marker nearby (avoids grabbing arbitrary page headings).
function accountNameProbe() {
  var MARKER_SEL =
    '[aria-label*="account" i], [aria-label*="profile" i], [aria-label*="user menu" i], ' +
    '[data-testid*="account" i], [data-testid*="profile" i], [data-testid*="avatar" i], ' +
    'header [role="button"], nav [role="button"], [role="banner"] [role="button"]';
  function shortLabel(s) {
    var t = (s || "").trim().replace(/\s+/g, " ");
    return t.length >= 2 && t.length <= 60 ? t : "";
  }
  var candidates = document.querySelectorAll(MARKER_SEL);
  for (var i = 0; i < candidates.length; i++) {
    var el = candidates[i];
    var direct = shortLabel(el.getAttribute("aria-label")) || shortLabel(el.textContent);
    if (direct) return direct;
    var img = el.querySelector && el.querySelector("img[alt]");
    if (img) {
      var alt = shortLabel(img.getAttribute("alt"));
      if (alt) return alt;
    }
  }
  return "";
}

// Learned auth definitions (P0: Application Authentication Recording) — the interactive-marker
// half of an auth_learning observation (see conxa_compile/auth_learning.py's PROBE_SCRIPT and
// its module docstring). Role+name pairs only, capped and deduped, same shape both Studio (at
// learn time) and the runtime (at evaluate time) gather — kept in sync by hand, not by sharing
// code across languages, since this one has to be re-parsed standalone in the page realm.
function authDefinitionMarkersProbe() {
  var markers = [];
  var seen = {};
  var els = document.querySelectorAll('button, a, [role="button"], [role="menuitem"]');
  for (var i = 0; i < els.length && markers.length < 100; i++) {
    var el = els[i];
    if (el.closest('footer, [role="contentinfo"]')) continue; // AUTH-22: keep in sync with PROBE_SCRIPT
    var name = (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 60);
    if (!name) continue;
    var role = el.getAttribute("role") || el.tagName.toLowerCase();
    var key = role + "|" + name;
    if (seen[key]) continue;
    seen[key] = true;
    markers.push({ role: role, name: name });
  }
  return markers;
}

// Completion gate (completion.js) — what the page is showing right now that says "still working" or
// "it failed": visible busy indicators, and the text of status regions (role=status/alert/log, a
// polite/assertive live region, or a dialog such as an upload tray). A modal dialog is a question for the
// person and is left to dialogProbe, unless it is really a progress window (a progress bar, or text that says
// it is still working). Re-parsed standalone
// in the page realm like every function here, so it closes over nothing.
// Returns { busyCount, regions: string[] } (at most 8 texts of 300 chars each).
function completionProbe() {
  var busySel = '[aria-busy="true"], [role="progressbar"], progress, .spinner, .loading, .loader, [class*="spinner" i]';
  var regionSel = '[role="status"], [role="alert"], [role="log"], [aria-live]:not([aria-live="off"]), ' +
    '[role="dialog"]:not([aria-modal="true"]), [role="alertdialog"]:not([aria-modal="true"])';
  function visible(el) {
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    var cs = window.getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }
  var busyCount = 0;
  var bars = [];
  var busyWhat = "";
  var busy = document.querySelectorAll(busySel);
  for (var i = 0; i < busy.length; i++) {
    if (!visible(busy[i])) continue;
    busyCount++;
    var b = busy[i];
    // What each indicator reads right now, so the gate can tell a bar that moves from one that only stays on screen.
    bars.push(String(b.getAttribute("aria-valuenow") || b.getAttribute("data-progress") || b.value || ""));
    if (!busyWhat) {
      var host = b.closest('[role="dialog"],[role="alertdialog"],[role="status"],[aria-live]');
      busyWhat = (b.tagName.toLowerCase() + (b.getAttribute("role") ? "[" + b.getAttribute("role") + "]" : "") +
        (host ? " in " + String(host.getAttribute("aria-label") || host.getAttribute("role") || host.tagName).slice(0, 60) : "")).slice(0, 120);
    }
  }
  var regions = [];
  var els = document.querySelectorAll(regionSel);
  for (var j = 0; j < els.length && regions.length < 8; j++) {
    if (!visible(els[j])) continue;
    var t = String(els[j].innerText || els[j].textContent || "").replace(/\s+/g, " ").trim().slice(0, 300);
    if (t && regions.indexOf(t) === -1) regions.push(t);
  }
  // A MODAL dialog is a question for the person (dialogProbe owns it), unless it is really a progress
  // window: one that holds a progress bar or says it is still working. Those are progress, and leaving
  // them out hid a modal "Uploading..." window from the gate entirely.
  var progressWords = /\b(uploading|processing|saving|sending|submitting|importing|exporting|converting|preparing|syncing|loading|in progress|please wait)\b|(^|[^\d.])\d{1,2}\s?%/i;
  var modals = document.querySelectorAll('[role="dialog"][aria-modal="true"], [role="alertdialog"][aria-modal="true"], dialog[open]');
  for (var k = 0; k < modals.length && regions.length < 8; k++) {
    if (!visible(modals[k])) continue;
    var mt = String(modals[k].innerText || modals[k].textContent || "").replace(/\s+/g, " ").trim().slice(0, 300);
    if (!mt || regions.indexOf(mt) !== -1) continue;
    if (modals[k].querySelector('[role="progressbar"], progress, [aria-busy="true"]') || progressWords.test(mt)) regions.push(mt);
  }
  return { busyCount: busyCount, regions: regions, bars: bars.slice(0, 8).join("|"), busyWhat: busyWhat };
}

// How many times `needle` appears in the page's visible text (case-insensitive). Used to see whether an uploaded
// file's name is on the page, and more times than before the upload. Closes over nothing (re-parsed in the page).
// Electron draws no window.prompt(): it raises no dialog event and the call does nothing (spike 2026-11-16, an
// Electron view under CDP: alert and confirm arrive as dialogs, prompt never does). So in a host-owned browser (Build
// Studio's test pane, Conxa Execute) the run answers a recorded prompt itself, the way the recorder's dialog server
// does: the next prompt() returns the recorded answer (null = Cancel), once, and puts the page's own prompt back.
function armPrompt(answer) {
  var own = window.prompt;
  window.prompt = function () {
    window.prompt = own;
    window.__conxaPromptAnswered = (window.__conxaPromptAnswered || 0) + 1;
    return answer;
  };
  return true;
}

// Whether an armed prompt() was answered since the last look (and resets the count).
function takePromptAnswered() {
  var n = window.__conxaPromptAnswered || 0;
  window.__conxaPromptAnswered = 0;
  return n > 0;
}

// How often `needle` is on the screen: the page's visible text, and what visible text boxes hold (a person reads a
// filled date box as "11/15/2026 on the screen", WF-2 Segment E). Only boxes a person types or reads text in: never a
// password, a hidden field, or a file picker (its value is the file name an upload check looks for elsewhere).
function countTextOccurrences(needle) {
  // Inside the function: it runs in the page on its own, so nothing outside it exists there.
  var _TEXT_BOX_TYPES = { "": 1, text: 1, search: 1, email: 1, url: 1, tel: 1, number: 1, date: 1, "datetime-local": 1, month: 1, week: 1, time: 1, textarea: 1 };
  var n = String(needle || "").toLowerCase();
  if (!n) return 0;
  var parts = [String((document.body && document.body.innerText) || "")];
  var fields = typeof document.querySelectorAll === "function" ? document.querySelectorAll("input, textarea") : [];
  for (var i = 0; i < fields.length; i++) {
    var el = fields[i];
    var type = String(el.type || "").toLowerCase();
    if (!_TEXT_BOX_TYPES[type] || el.offsetParent === null) continue;
    if (typeof el.value === "string" && el.value) parts.push(el.value);
  }
  var count = 0;
  for (var p = 0; p < parts.length; p++) {
    var hay = parts[p].toLowerCase();
    for (var at = hay.indexOf(n); at !== -1; at = hay.indexOf(n, at + n.length)) count++;
  }
  return count;
}

// The rows (indexes) that show `wanted` as a whole value: the row's own text, one element's text, or one field's
// value, compared after collapsing whitespace and case. A row that says "Carol Ann" does not hold "Carol" (WF-7).
// Run over every row at once (locator.evaluateAll): a field's value is not text, so no text filter may come first.
function rowsHoldingWholeValue(rows, wanted) {
  var norm = function (v) { return String(v || "").replace(/\s+/g, " ").trim().toLowerCase(); };
  var want = norm(wanted);
  var out = [];
  if (!want) return out;
  for (var r = 0; r < rows.length && r < 2000; r++) {
    var row = rows[r];
    var hit = norm(row.innerText || row.textContent) === want;
    var els = !hit && typeof row.querySelectorAll === "function" ? row.querySelectorAll("*") : [];
    for (var i = 0; i < els.length && i < 500 && !hit; i++) {
      var el = els[i];
      var isField = /^(INPUT|TEXTAREA|SELECT)$/.test(String(el.tagName || ""));
      hit = norm(isField ? el.value : (el.innerText || el.textContent)) === want;
    }
    if (hit) out.push(r);
  }
  return out;
}

// How often `needle` ends a line of the page's visible text (or a visible box's value), with at most closing
// punctuation after it: "Saved Carol" in "Saved Carol." but not in "Saved Carol Ann". For a check whose words end in
// the record the run acts on, where a longer look-alike record must not count.
function countTextEnding(needle) {
  var n = String(needle || "").replace(/\s+/g, " ").trim().toLowerCase();
  if (!n) return 0;
  var parts = String((document.body && document.body.innerText) || "").split(/[\n\t]+/);
  var fields = typeof document.querySelectorAll === "function" ? document.querySelectorAll("input, textarea") : [];
  for (var i = 0; i < fields.length; i++) {
    if (fields[i].offsetParent !== null && typeof fields[i].value === "string") parts.push(fields[i].value);
  }
  var count = 0;
  for (var p = 0; p < parts.length; p++) {
    var hay = parts[p].replace(/\s+/g, " ").trim().toLowerCase();
    for (var at = hay.indexOf(n); at !== -1; at = hay.indexOf(n, at + n.length)) {
      if (/^[\s.!?,;:)\]"'”]*$/.test(hay.slice(at + n.length))) count++;
    }
  }
  return count;
}

// How often `needle` is in the page's visible text only, never in what a text box holds. For "one more than before": a
// form's box holds the value before Save and the new row holds it after, so counting boxes made a saved row look like
// no change (WF-3 A, demoqa Web Tables).
function countPageText(needle) {
  var n = String(needle || "").toLowerCase();
  if (!n) return 0;
  var hay = String((document.body && document.body.innerText) || "").toLowerCase();
  var count = 0;
  for (var at = hay.indexOf(n); at !== -1; at = hay.indexOf(n, at + n.length)) count++;
  return count;
}

// (agent_span) Runs inside the page. Numbers every visible interactive element and marks it so the runtime can find
// the very same element again. Passwords are never read; fields show at most a short current value.
function gatherCandidates(limit) {
  const SEL = 'a[href],button,input,select,textarea,[role="button"],[role="link"],[role="menuitem"],' +
    '[role="tab"],[role="option"],[role="checkbox"],[role="radio"],[onclick]';
  document.querySelectorAll("[data-conxa-span]").forEach((e) => e.removeAttribute("data-conxa-span"));
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    if (out.length >= limit) break;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (!(r.width > 0 && r.height > 0) || st.visibility === "hidden" || st.display === "none") continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    const role = el.getAttribute("role") ||
      (tag === "a" ? "link" : tag === "button" ? "button" : tag === "select" ? "select" : tag === "textarea" ? "textbox" : type || "text");
    const name = (el.getAttribute("aria-label") || el.innerText || el.value || el.getAttribute("placeholder") ||
      el.getAttribute("title") || el.getAttribute("name") || "").replace(/\s+/g, " ").trim().slice(0, 80);
    let host = "";
    if (tag === "a") { try { host = new URL(el.href, location.href).host; } catch (_) { host = ""; } }
    const field = tag === "input" || tag === "textarea" || tag === "select";
    const i = out.length;
    el.setAttribute("data-conxa-span", String(i));
    out.push({
      i, tag, role, type, name, host,
      in_dialog: !!el.closest('dialog[open],[role="dialog"],[role="alertdialog"],[aria-modal="true"]'),
      disabled: !!el.disabled || el.getAttribute("aria-disabled") === "true",
      value: field && type !== "password" && type !== "file" ? String(el.value || "").slice(0, 40) : "",
    });
  }
  return out;
}

// Runs inside the page. The visible text, for the read_text action.
function visibleText(limit) {
  return String((document.body && document.body.innerText) || "").replace(/\n{3,}/g, "\n\n").slice(0, limit);
}

// Runs inside the page. The label of whatever has keyboard focus, so Enter cannot press a button the click rule
// would have refused.
function focusedLabel() {
  const el = document.activeElement;
  if (!el || el === document.body) return "";
  return String(el.getAttribute("aria-label") || el.innerText || el.value || el.getAttribute("title") || "")
    .replace(/\s+/g, " ").trim().slice(0, 80);
}

// Page size as [width, height, devicePixelRatio].
function viewportSize() {
  return [window.innerWidth, window.innerHeight, window.devicePixelRatio];
}

// Where the page says the mouse arrived, as [x, y], or null. Resolves on the first mousemove.
function firstMouseMove() {
  return new Promise((resolve) => {
    const done = (v) => { removeEventListener("mousemove", on, true); resolve(v); };
    const on = (e) => done([e.clientX, e.clientY]);
    addEventListener("mousemove", on, true);
    setTimeout(() => done(null), 1500);
  });
}

// A dismiss candidate's label and whether it sits in dialog chrome. Called via Locator.evaluate(el, dialogSel).
function dismissLabelInfo(el, dialogSel) {
  return {
    label: (el.getAttribute("aria-label") || el.innerText || el.getAttribute("title") || "").trim().slice(0, 120),
    inDialogChrome: !!el.closest(dialogSel),
  };
}

function acceptsMultiple(el) {
  return el.multiple === true;
}

// The names of the files a file box holds now (null when it is not a file box).
function chosenFileNames(el) {
  return el && el.files ? Array.from(el.files).map((f) => f.name) : null;
}

function elementText(el) {
  return (el.innerText || el.textContent || "").trim();
}

function clipboardWrite(text) {
  return navigator.clipboard.writeText(text);
}

function clipboardRead() {
  return navigator.clipboard.readText();
}

function seedLocalStorage(entries) {
  for (const { name, value } of entries) {
    try { window.localStorage.setItem(name, value); } catch (_) {}
  }
}

module.exports = {
  rowsHoldingWholeValue,
  countTextEnding,
  countTextOccurrences,
  countPageText,
  armPrompt,
  takePromptAnswered,
  extractDescriptor,
  rafStable,
  isDisabled,
  scrollBy,
  getScrollY,
  readFieldValue,
  revealHoverPoints,
  armClickWitness,
  readClickWitness,
  preStepSignature,
  settleSignature,
  environmentSignature,
  pageFingerprint,
  domInventory,
  inventoryEntryForElement,
  overlayProbe,
  dialogProbe,
  neutralPoint,
  faultProbe,
  completionProbe,
  INVENTORY_SELECTOR,
  scrollVirtualContainerStep,
  scrollDominantScrollableElement,
  passwordBoxProbe,
  pauseSignProbe,
  storageKeyCounts,
  originStorage,
  accountNameProbe,
  authDefinitionMarkersProbe,
  gatherCandidates,
  visibleText,
  focusedLabel,
  viewportSize,
  firstMouseMove,
  dismissLabelInfo,
  acceptsMultiple,
  chosenFileNames,
  elementText,
  clipboardWrite,
  clipboardRead,
  seedLocalStorage,
};
