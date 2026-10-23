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
  const innerText = (el.textContent || "").trim().slice(0, 80);
  const axName = (ariaLabel || nameAttr || innerText).trim();
  const hashPayload = `${tag}|${sortedAttrs}|${axName}`;

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
  const placeholder = el.getAttribute("placeholder") || "";
  const altAttr = el.getAttribute("alt") || "";
  const titleAttr = el.getAttribute("title") || "";
  return {
    role,
    name: (ariaLabel || nameAttr || altAttr || titleAttr || (el.textContent || "").trim() || placeholder).slice(0, 120),
    text: (el.textContent || "").trim().slice(0, 120),
    testid: el.getAttribute("data-testid") || el.getAttribute("data-test-id") || "",
    anchorNeighbors: neighbors,
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
// Returns null when no visible dialog is open, else the TOPMOST (last in DOM order) one as
// { title, text, controls: [{ tag, role, name, text, testid, id }] }.
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
  const dlg = dialogs[dialogs.length - 1];
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
  return { title: title.slice(0, 120), text: clean(dlg.innerText).slice(0, 300), controls, hasFields };
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
  for (var i = 0; i < els.length && markers.length < 25; i++) {
    var el = els[i];
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

module.exports = {
  extractDescriptor,
  rafStable,
  isDisabled,
  scrollBy,
  getScrollY,
  preStepSignature,
  settleSignature,
  environmentSignature,
  pageFingerprint,
  domInventory,
  inventoryEntryForElement,
  overlayProbe,
  dialogProbe,
  INVENTORY_SELECTOR,
  scrollVirtualContainerStep,
  scrollDominantScrollableElement,
  passwordBoxProbe,
  pauseSignProbe,
  storageKeyCounts,
  originStorage,
  accountNameProbe,
  authDefinitionMarkersProbe,
};
